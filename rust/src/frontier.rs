//! Per-host queues plus a heap of ready hosts (spec/03-engine.md). Pure data structure, no I/O.
use crate::limits::Host;
use crate::model::Task;
use std::cmp::{Ordering, Reverse};
use std::collections::BinaryHeap;

/// A queued task: higher priority first, then FIFO.
pub struct Queued {
    priority: i64,
    seq: u64,
    pub task: Task,
    pub attempts: u32,
}

impl Ord for Queued {
    fn cmp(&self, o: &Self) -> Ordering {
        (self.priority, Reverse(self.seq)).cmp(&(o.priority, Reverse(o.seq)))
    }
}
impl PartialOrd for Queued {
    fn partial_cmp(&self, o: &Self) -> Option<Ordering> { Some(self.cmp(o)) }
}
impl PartialEq for Queued {
    fn eq(&self, o: &Self) -> bool { self.seq == o.seq }
}
impl Eq for Queued {}

pub enum Pop {
    Ready(usize, Task, u32), // host index, task, attempts so far
    Wait(f64),               // seconds until the next host is ready
    Empty,                   // nothing queued
}

fn micros(t: f64) -> u64 {
    (t * 1e6) as u64
}

#[derive(Default)]
pub struct Frontier {
    pub hosts: Vec<Host>,
    heap: BinaryHeap<Reverse<(u64, u64, usize)>>, // (ready_at µs, seq, host index)
    seq: u64,
}

impl Frontier {
    pub fn push(&mut self, h: usize, task: Task, attempts: u32) {
        self.seq += 1;
        self.hosts[h].queue.push(Queued { priority: task.priority, seq: self.seq, task, attempts });
        self.schedule(h);
    }

    pub fn release(&mut self, h: usize) {
        self.hosts[h].inflight -= 1;
        self.schedule(h);
    }

    fn schedule(&mut self, h: usize) {
        let host = &mut self.hosts[h];
        if !host.queue.is_empty() && host.inflight < host.cfg.max_concurrency && !host.in_heap {
            host.in_heap = true;
            self.seq += 1;
            self.heap.push(Reverse((micros(host.ready_at()), self.seq, h)));
        }
    }

    /// Next task whose host may start now, or how long to wait.
    pub fn pop(&mut self, now: f64) -> Pop {
        while let Some(&Reverse((at, _, h))) = self.heap.peek() {
            let ready = micros(self.hosts[h].ready_at());
            if ready != at { // stale entry: host was paused or blocked since it was queued
                self.heap.pop();
                self.seq += 1;
                self.heap.push(Reverse((ready, self.seq, h)));
                continue;
            }
            if at > micros(now) {
                return Pop::Wait((at - micros(now)) as f64 / 1e6);
            }
            self.heap.pop();
            let host = &mut self.hosts[h];
            host.in_heap = false;
            let q = host.queue.pop().expect("scheduled host has work");
            host.inflight += 1;
            if !host.blocked {
                host.next_at = now + 1.0 / host.rate;
            }
            self.schedule(h);
            return Pop::Ready(h, q.task, q.attempts);
        }
        Pop::Empty
    }
}

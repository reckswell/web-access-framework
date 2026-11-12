"""Per-host queues plus a heap of ready hosts (spec/03-engine.md). Pure data structure, no I/O."""
import itertools
from heapq import heappop, heappush, heapreplace


class Frontier:
    def __init__(self):
        self.heap, self.seq = [], itertools.count()  # heap of (ready_at, seq, host)

    def push(self, host, task, attempts=0):
        heappush(host.queue, (-task.priority, next(self.seq), task, attempts))
        self._schedule(host)

    def release(self, host):
        host.inflight -= 1
        self._schedule(host)

    def _schedule(self, host):
        if host.queue and host.inflight < host.cfg.max_concurrency and not host.in_heap:
            host.in_heap = True
            heappush(self.heap, (host.ready_at(), next(self.seq), host))

    def pop(self, now):
        """Return (host, task, attempts) if one may start now, else seconds to wait (None = nothing queued)."""
        while self.heap:
            at, _, host = self.heap[0]
            if host.ready_at() != at:  # stale entry: host was paused or blocked since it was queued
                heapreplace(self.heap, (host.ready_at(), next(self.seq), host))
                continue
            if at > now:
                return at - now
            heappop(self.heap)
            host.in_heap = False
            _, _, task, attempts = heappop(host.queue)
            host.inflight += 1
            if not host.blocked:
                host.next_at = now + 1 / host.rate
            self._schedule(host)
            return host, task, attempts
        return None

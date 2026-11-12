//! The engine: dispatch, requests, retries, robots.txt, events (spec/03-engine.md).
use crate::config::Config;
use crate::frontier::{Frontier, Pop};
use crate::goals::Goal;
use crate::limits::{backoff, classify, parse_retry_after, Host};
use crate::model::{Output, Response, Task};
use crate::robots::{Robots, DENY_ALL};
use crate::safety::Safety;
use crate::sinks::Sink;
use crate::urls::{host_key, normalize};
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs::File;
use std::io::{BufWriter, Write};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::sync::{Notify, Semaphore};

#[derive(Default)]
struct State {
    frontier: Frontier,
    index: HashMap<String, usize>, // host key -> index in frontier.hosts
    seen: HashSet<String>,
    outstanding: u64,
    requests: u64,
    finished: u64,
    outcomes: BTreeMap<&'static str, u64>,
    blocked_hosts: Vec<String>,
}

struct Inner {
    cfg: Config,
    goal: Arc<dyn Goal>,
    client: reqwest::Client,
    safety: Arc<Safety>,
    state: Mutex<State>,
    sinks: Mutex<Vec<Box<dyn Sink>>>,
    log: Mutex<Option<BufWriter<File>>>,
    wake: Notify,
    slots: Arc<Semaphore>,
    start: Instant,
}

pub struct Engine(Arc<Inner>);

/// A redirect hop refused by the safety check (`insecure` or `unsafe`).
#[derive(Debug)]
struct Refused(&'static str);
impl std::fmt::Display for Refused {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result { write!(f, "refused redirect: {}", self.0) }
}
impl std::error::Error for Refused {}

fn unix_now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
}

/// Raise the soft open-file limit where the OS has one (spec/09-platforms.md); None on Windows.
pub fn open_file_limit() -> Option<u64> {
    if cfg!(windows) { None } else { rlimit::increase_nofile_limit(10240).ok() }
}

impl Engine {
    pub fn new(cfg: Config, goal: Arc<dyn Goal>, sinks: Vec<Box<dyn Sink>>) -> Result<Self, String> {
        let _ = rustls::crypto::ring::default_provider().install_default(); // pure-Rust TLS, same on every OS
        let e = &cfg.engine;
        let headers = e.headers.iter()
            .filter_map(|(k, v)| Some((k.parse::<reqwest::header::HeaderName>().ok()?, v.parse().ok()?))).collect();
        let workers = cfg.workers(open_file_limit(), None);
        let safety = Arc::new(Safety::from_config(e)?);
        let (guard, max_redirects) = (safety.clone(), e.max_redirects);
        let redirects = reqwest::redirect::Policy::custom(move |hop| { // every hop passes the safety check
            if hop.previous().len() > max_redirects { return hop.error("too many redirects") }
            match guard.check(hop.url().as_str()) { Some(why) => hop.error(Refused(why)), None => hop.follow() }
        });
        let client = reqwest::Client::builder()
            .user_agent(&e.user_agent).default_headers(headers).cookie_store(true)
            .timeout(Duration::from_secs_f64(e.request_timeout))
            .redirect(redirects)
            .pool_max_idle_per_host(workers.min(64))
            .build().map_err(|err| err.to_string())?;
        let log = if e.log_path.is_empty() { None } else {
            if let Some(dir) = std::path::Path::new(&e.log_path).parent().filter(|d| !d.as_os_str().is_empty()) {
                std::fs::create_dir_all(dir).map_err(|err| err.to_string())?;
            }
            Some(BufWriter::new(File::create(&e.log_path).map_err(|err| format!("{}: {err}", e.log_path))?))
        };
        Ok(Self(Arc::new(Inner {
            goal, client, safety, state: Default::default(), sinks: Mutex::new(sinks), log: Mutex::new(log),
            wake: Notify::new(), slots: Arc::new(Semaphore::new(workers)), start: Instant::now(), cfg,
        })))
    }

    /// Queue a task (deduplicated by normalized URL).
    pub fn add(&self, task: Task) {
        self.0.add(task)
    }

    /// Run until every task is finished (or Ctrl-C). Returns (summary, interrupted).
    pub async fn run(&self) -> (Value, bool) {
        let me = &self.0;
        for task in me.goal.start() {
            me.add(task);
        }
        let stats = (me.cfg.engine.stats_interval > 0.0).then(|| tokio::spawn(me.clone().stats()));
        let interrupted = tokio::select! {
            _ = me.clone().dispatch() => false,
            _ = tokio::signal::ctrl_c() => true,
        };
        if let Some(s) = stats {
            s.abort();
        }
        for sink in me.sinks.lock().unwrap().iter_mut() {
            if let Err(e) = sink.close() {
                eprintln!("error closing sink: {e}");
            }
        }
        let summary = me.summary();
        me.emit("summary", summary.clone());
        if let Some(log) = me.log.lock().unwrap().as_mut() {
            let _ = log.flush();
        }
        eprintln!("summary: {summary}");
        (summary, interrupted)
    }
}

impl Inner {
    fn now(&self) -> f64 {
        self.start.elapsed().as_secs_f64()
    }

    fn add(&self, task: Task) {
        let key = normalize(&task.url);
        let mut s = self.state.lock().unwrap();
        if let Some(k) = key.as_ref().filter(|_| task.dedup && task.method == "GET") {
            if !s.seen.insert(k.clone()) {
                return;
            }
        }
        s.outstanding += 1;
        let refused = if key.is_none() { Some("invalid") } else { self.safety.check(&task.url) };
        let (Some(hk), None) = (host_key(&task.url), refused) else {
            drop(s);
            return self.finish(Response::bare(task, refused.unwrap_or("invalid"), 0));
        };
        let h = match s.index.get(&hk) {
            Some(&h) => h,
            None => {
                let host = Host::new(hk.clone(), self.cfg.host_config(&hk));
                s.frontier.hosts.push(host);
                let h = s.frontier.hosts.len() - 1;
                s.index.insert(hk, h);
                h
            }
        };
        s.frontier.push(h, task, 0);
        drop(s);
        self.wake.notify_one();
    }

    async fn dispatch(self: Arc<Self>) {
        loop {
            let permit = self.slots.clone().acquire_owned().await.expect("semaphore open");
            let (h, task, attempts) = loop {
                let pop = {
                    let mut s = self.state.lock().unwrap();
                    if s.outstanding == 0 {
                        return;
                    }
                    s.frontier.pop(self.now())
                };
                match pop {
                    Pop::Ready(h, t, a) => break (h, t, a),
                    Pop::Wait(secs) => { let _ = tokio::time::timeout(Duration::from_secs_f64(secs), self.wake.notified()).await; }
                    Pop::Empty => self.wake.notified().await,
                }
            };
            let me = self.clone();
            tokio::spawn(async move {
                me.handle(h, task, attempts).await;
                drop(permit);
            });
        }
    }

    async fn handle(self: Arc<Self>, h: usize, task: Task, mut attempts: u32) {
        let (blocked, key) = {
            let s = self.state.lock().unwrap();
            (s.frontier.hosts[h].blocked, s.frontier.hosts[h].key.clone())
        };
        if blocked {
            self.finish(Response::bare(task, "host_blocked", attempts));
        } else if !self.allowed(h, &task.url).await {
            self.finish(Response::bare(task, "robots_disallowed", attempts));
        } else {
            attempts += 1;
            let mut res = self.request(task, attempts).await;
            let kind = res.error.as_deref().map(|e| e.split(':').next().unwrap_or(e));
            let (outcome, signal, retry) = classify(res.status, kind, &res.body);
            let ra = res.headers.get("retry-after").and_then(|v| parse_retry_after(v, unix_now()));
            let (events, max_ra) = {
                let mut s = self.state.lock().unwrap();
                s.requests += 1;
                let now = self.now();
                let host = &mut s.frontier.hosts[h];
                let (events, max_ra) = (host.apply(signal, now, ra), host.cfg.max_retry_after);
                if events.iter().any(|(name, _)| *name == "host_blocked") {
                    s.blocked_hosts.push(key.clone());
                }
                (events, max_ra)
            };
            for (name, mut fields) in events {
                fields["host"] = key.clone().into();
                self.emit(name, fields);
            }
            let t = &res.task;
            let safe = matches!(t.method.as_str(), "GET" | "HEAD") || t.meta.get("retry_safe") == Some(&Value::Bool(true));
            if retry && safe && attempts < self.cfg.retry.max_attempts {
                let delay = backoff(attempts, &self.cfg.retry, ra, max_ra);
                let reason = res.status.map_or_else(|| kind.unwrap_or("").to_string(), |s| s.to_string());
                self.emit("retry", json!({"url": t.url, "host": key, "attempt": attempts,
                                          "delay": (delay * 1e3).round() / 1e3, "reason": reason}));
                let me = self.clone();
                tokio::spawn(async move {
                    tokio::time::sleep(Duration::from_secs_f64(delay)).await;
                    me.state.lock().unwrap().frontier.push(h, res.task, attempts);
                    me.wake.notify_one();
                });
            } else {
                res.outcome = outcome;
                self.finish(res);
            }
        }
        self.state.lock().unwrap().frontier.release(h);
        self.wake.notify_one();
    }

    async fn request(&self, task: Task, attempts: u32) -> Response {
        let (start, cap) = (Instant::now(), self.cfg.engine.max_body_bytes);
        let method = reqwest::Method::from_bytes(task.method.as_bytes()).unwrap_or(reqwest::Method::GET);
        let mut req = self.client.request(method, &task.url);
        for (k, v) in &task.headers {
            req = req.header(k.to_lowercase(), v);
        }
        if let Some(body) = &task.body {
            req = req.body(body.clone());
        }
        let result = async {
            let mut r = req.send().await?;
            let (status, url) = (r.status().as_u16(), r.url().to_string());
            let headers: HashMap<String, String> = r.headers().keys().map(|k| {
                let vals: Vec<_> = r.headers().get_all(k).iter().map(|v| String::from_utf8_lossy(v.as_bytes())).collect();
                (k.as_str().to_string(), vals.join(", "))
            }).collect();
            let mut body = Vec::new();
            while let Some(chunk) = r.chunk().await? {
                body.extend_from_slice(&chunk);
                if body.len() >= cap {
                    body.truncate(cap);
                    break;
                }
            }
            Ok::<_, reqwest::Error>((status, url, headers, body))
        }.await;
        let elapsed_ms = start.elapsed().as_millis() as u64;
        match result {
            Ok((status, url, headers, body)) =>
                Response { task, url, status: Some(status), outcome: "", headers, body, attempts, elapsed_ms, error: None },
            Err(e) => {
                let refused = std::iter::successors(std::error::Error::source(&e), |s| s.source())
                    .find_map(|s| s.downcast_ref::<Refused>()).map(|r| r.0);
                let error = if e.is_timeout() { "timeout".into() } else if let Some(why) = refused { why.into() }
                            else if e.is_redirect() { "redirects".into() }
                            else { format!("network: {}", if e.is_connect() { "connect" } else { "request" }) };
                Response { error: Some(error), elapsed_ms, ..Response::bare(task, "", attempts) }
            }
        }
    }

    async fn allowed(&self, h: usize, url: &str) -> bool {
        let e = &self.cfg.engine;
        if !e.respect_robots {
            return true;
        }
        let slot = self.state.lock().unwrap().frontier.hosts[h].robots.clone();
        let robots = {
            let mut slot = slot.lock().await; // one fetch per host; concurrent first tasks wait here
            if slot.as_ref().is_none_or(|(_, expires)| *expires < self.now()) {
                *slot = Some(self.fetch_robots(h, url).await);
            }
            slot.as_ref().unwrap().0.clone()
        };
        let Ok(u) = url::Url::parse(url) else { return true };
        let target = format!("{}{}", u.path(), u.query().map_or(String::new(), |q| format!("?{q}")));
        robots.allowed(&e.robots_agent, &target)
    }

    async fn fetch_robots(&self, h: usize, url: &str) -> (Arc<Robots>, f64) {
        let Ok(robots_url) = url::Url::parse(url).and_then(|u| u.join("/robots.txt")) else {
            return (Arc::new(Robots::default()), f64::INFINITY);
        };
        let (status, text) = match self.client.get(robots_url).send().await {
            Ok(r) => { let s = r.status().as_u16(); (s, if s < 300 { r.text().await.unwrap_or_default() } else { String::new() }) }
            Err(_) => (599, String::new()),
        };
        if status >= 500 {
            return (Arc::new(Robots::parse(DENY_ALL)), self.now() + self.cfg.engine.robots_error_ttl);
        }
        let robots = Robots::parse(&text);
        if let Some(delay) = robots.crawl_delay(&self.cfg.engine.robots_agent).filter(|d| *d > 0.0) {
            self.state.lock().unwrap().frontier.hosts[h].cap_rate(1.0 / delay);
        }
        (Arc::new(robots), f64::INFINITY)
    }

    fn finish(&self, res: Response) {
        self.emit("request", json!({"url": res.task.url, "host": host_key(&res.task.url), "status": res.status,
                                    "outcome": res.outcome, "attempts": res.attempts, "elapsed_ms": res.elapsed_ms,
                                    "error": res.error}));
        let outputs = catch_unwind(AssertUnwindSafe(|| {
            if res.outcome == "ok" { self.goal.parse(&res) } else { self.goal.failed(&res) }
        })).unwrap_or_else(|_| { eprintln!("goal panicked on {}", res.url); vec![] }); // a goal bug must not kill the run
        for out in outputs {
            match out {
                Output::Task(t) => self.add(t), // added before this task stops counting as outstanding
                Output::Record(r) => for sink in self.sinks.lock().unwrap().iter_mut() {
                    if let Err(e) = sink.write(&r) { eprintln!("sink error: {e}"); }
                },
            }
        }
        let mut s = self.state.lock().unwrap();
        (s.outstanding, s.finished) = (s.outstanding - 1, s.finished + 1);
        *s.outcomes.entry(res.outcome).or_default() += 1;
        if s.outstanding == 0 {
            self.wake.notify_one();
        }
    }

    fn emit(&self, event: &str, fields: Value) {
        let mut log = self.log.lock().unwrap();
        let Some(out) = log.as_mut() else { return };
        let mut line = Map::new();
        line.insert("ts".into(), json!((unix_now() * 1e3).round() / 1e3));
        line.insert("event".into(), event.into());
        line.extend(fields.as_object().cloned().unwrap_or_default());
        let _ = writeln!(out, "{}", Value::Object(line));
    }

    fn summary(&self) -> Value {
        let s = self.state.lock().unwrap();
        json!({"elapsed": (self.now() * 1e3).round() / 1e3, "finished": s.finished, "requests": s.requests,
               "outcomes": s.outcomes, "hosts": s.frontier.hosts.len(), "blocked_hosts": s.blocked_hosts})
    }

    async fn stats(self: Arc<Self>) {
        loop {
            tokio::time::sleep(Duration::from_secs_f64(self.cfg.engine.stats_interval)).await;
            let (now, s) = (self.now(), self.state.lock().unwrap());
            let (ok, failed) = (s.outcomes.get("ok").copied().unwrap_or(0), s.outcomes.get("failed").copied().unwrap_or(0));
            let hosts = &s.frontier.hosts;
            eprintln!("[{now:.1}s] done={} ok={ok} failed={failed} other={} rate={:.1}/s queued={} inflight={} hosts={} paused={} blocked={}",
                s.finished, s.finished - ok - failed, s.finished as f64 / now, hosts.iter().map(|h| h.queue.len()).sum::<usize>(),
                hosts.iter().map(|h| h.inflight).sum::<usize>(), hosts.len(), hosts.iter().filter(|h| h.paused_until > now).count(),
                s.blocked_hosts.len());
        }
    }
}

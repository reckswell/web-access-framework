//! Response classification, backoff and the per-host adaptive limiter (spec/04, spec/05).
use crate::config::{HostConfig, Retry};
use crate::frontier::Queued;
use crate::robots::Robots;
use serde_json::{json, Value};
use std::collections::BinaryHeap;
use std::sync::Arc;

const CHALLENGE_MARKERS: [&str; 6] =
    ["captcha", "cf-chl", "challenge-platform", "are you a robot", "verify you are human", "unusual traffic"];

pub fn is_challenge(body: &[u8]) -> bool {
    let head = String::from_utf8_lossy(&body[..body.len().min(65536)]).to_lowercase();
    CHALLENGE_MARKERS.iter().any(|m| head.contains(m))
}

/// One attempt -> (outcome, signal, retry). `error` is `timeout`, `network` or `redirects`.
pub fn classify(status: Option<u16>, error: Option<&str>, body: &[u8]) -> (&'static str, &'static str, bool) {
    if matches!(status, Some(403 | 429 | 503)) && is_challenge(body) {
        return ("blocked", "blocked", false);
    }
    let Some(s) = status else {
        return match error {
            Some("insecure") => ("insecure", "healthy", false), // redirect hops refused by the safety check
            Some("unsafe") => ("unsafe", "healthy", false),
            Some("redirects") => ("client_error", "healthy", false),
            _ => ("failed", "throttle", true),
        };
    };
    match s {
        200..=399 => ("ok", "healthy", false),
        401 => ("auth_required", "healthy", false),
        403 => ("blocked", "blocked", false),
        404 | 410 => ("not_found", "healthy", false),
        408 | 429 | 502 | 503 | 504 => ("failed", "throttle", true),
        501 | 505 => ("failed", "server_error", false),
        500..=599 => ("failed", "server_error", true),
        _ => ("client_error", "healthy", false),
    }
}

/// Retry-After header -> seconds to wait (>= 0), or None if unparseable. `now` is Unix seconds.
pub fn parse_retry_after(value: &str, now: f64) -> Option<f64> {
    let v = value.trim();
    if !v.is_empty() && v.bytes().all(|b| b.is_ascii_digit()) {
        return v.parse().ok();
    }
    let t = httpdate::parse_http_date(v).ok()?.duration_since(std::time::UNIX_EPOCH).ok()?.as_secs_f64();
    Some((t - now).max(0.0))
}

/// Equal-jitter exponential backoff, never shorter than Retry-After.
pub fn backoff(attempts: u32, retry: &Retry, retry_after: Option<f64>, max_retry_after: f64) -> f64 {
    let base = retry.max_delay.min(retry.base_delay * 2f64.powi(attempts as i32 - 1));
    let delay = base / 2.0 + fastrand::f64() * base / 2.0;
    retry_after.map_or(delay, |ra| delay.max(ra.min(max_retry_after)))
}

pub type Event = (&'static str, Value);
type RobotsSlot = Arc<tokio::sync::Mutex<Option<(Arc<Robots>, f64, Option<String>)>>>; // (rules, expires at, error)

/// Per-host politeness state plus the frontier's scheduling fields.
pub struct Host {
    pub key: String,
    pub cfg: HostConfig,
    pub rate: f64,
    pub successes: u32,
    pub failures: u32,
    pub blocks: u32,
    pub cooldown: f64,
    pub paused_until: f64,
    pub blocked: bool,
    pub queue: BinaryHeap<Queued>,
    pub inflight: usize,
    pub next_at: f64,
    pub in_heap: bool,
    pub robots: RobotsSlot,
}

impl Host {
    pub fn new(key: String, cfg: HostConfig) -> Self {
        Self { key, rate: cfg.initial_rate.max(cfg.min_rate).min(cfg.max_rate), successes: 0, failures: 0, blocks: 0,
               cooldown: cfg.breaker_cooldown, paused_until: 0.0, blocked: false, queue: BinaryHeap::new(),
               inflight: 0, next_at: 0.0, in_heap: false, robots: Default::default(), cfg }
    }

    pub fn ready_at(&self) -> f64 {
        if self.blocked { 0.0 } else { self.next_at.max(self.paused_until) }
    }

    /// robots.txt Crawl-delay.
    pub fn cap_rate(&mut self, max_rate: f64) {
        self.cfg.max_rate = self.cfg.max_rate.min(max_rate);
        self.rate = self.rate.min(self.cfg.max_rate);
    }

    /// Apply one signal; returns the events it caused.
    pub fn apply(&mut self, signal: &str, now: f64, retry_after: Option<f64>) -> Vec<Event> {
        let (c, mut events, old_rate) = (self.cfg.clone(), vec![], self.rate);
        match signal {
            "healthy" => {
                (self.failures, self.blocks, self.cooldown) = (0, 0, c.breaker_cooldown);
                self.successes += 1;
                if self.successes >= c.increase_after {
                    (self.rate, self.successes) = (c.max_rate.min(self.rate + c.additive_increase), 0);
                }
            }
            "blocked" => {
                (self.successes, self.blocks) = (0, self.blocks + 1);
                if self.blocks >= c.blocked_threshold && !self.blocked {
                    self.blocked = true;
                    events.push(("host_blocked", json!({})));
                }
            }
            _ => { // throttle or server_error
                if signal == "throttle" {
                    self.rate = c.min_rate.max(self.rate * c.decrease_factor);
                }
                (self.successes, self.failures) = (0, self.failures + 1);
                if self.failures >= c.breaker_threshold { // circuit breaker
                    events.push(self.pause(now + self.cooldown, "breaker", now));
                    (self.cooldown, self.failures) = ((self.cooldown * 2.0).min(c.breaker_max_cooldown), 0);
                }
            }
        }
        if let Some(ra) = retry_after {
            events.push(self.pause(now + ra.min(c.max_retry_after), "retry_after", now));
        }
        if self.rate != old_rate {
            events.push(("host_rate", json!({"rate": (self.rate * 1e4).round() / 1e4})));
        }
        events
    }

    fn pause(&mut self, until: f64, reason: &str, now: f64) -> Event {
        self.paused_until = self.paused_until.max(until);
        ("host_paused", json!({"seconds": ((self.paused_until - now) * 1e3).round() / 1e3, "reason": reason}))
    }
}

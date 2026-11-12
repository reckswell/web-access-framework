//! Configuration loading with spec defaults (spec/02-config.md).
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};

#[derive(Deserialize, Clone, Debug)]
#[serde(default)]
pub struct Resources {
    pub level: String,
    pub max_concurrency: usize,
    pub threads: usize,
    pub max_memory_mb: usize,
}

impl Default for Resources {
    fn default() -> Self {
        Self { level: "low".into(), max_concurrency: 0, threads: 0, max_memory_mb: 0 }
    }
}

impl Resources {
    /// Fill zero keys from the level preset.
    pub fn resolve(&self, cores: Option<usize>) -> Self {
        let c = cores.unwrap_or_else(|| std::thread::available_parallelism().map_or(1, |n| n.get()));
        let (conc, threads, mem) = match self.level.as_str() {
            "max" => ((128 * c).min(4096), c, 4096),
            "balanced" => ((32 * c).min(1024), (c / 2).max(1), 1024),
            _ => ((8 * c).min(128), 1, 256), // "low"
        };
        let or = |v: usize, d: usize| if v == 0 { d } else { v };
        Self { level: self.level.clone(), max_concurrency: or(self.max_concurrency, conc),
               threads: or(self.threads, threads), max_memory_mb: or(self.max_memory_mb, mem) }
    }
}

#[derive(Deserialize, Clone, Debug)]
#[serde(default)]
pub struct Engine {
    pub user_agent: String,
    pub robots_agent: String,
    pub respect_robots: bool,
    pub request_timeout: f64,
    pub max_body_bytes: usize,
    pub max_redirects: usize,
    pub log_path: String,
    pub stats_interval: f64,
    pub robots_error_ttl: f64,
    pub headers: BTreeMap<String, String>,
}

impl Default for Engine {
    fn default() -> Self {
        Self {
            user_agent: "web-access-framework/0.1 (+https://github.com/reckswell/web-access-framework)".into(),
            robots_agent: "web-access-framework".into(),
            respect_robots: true,
            request_timeout: 30.0,
            max_body_bytes: 10 * 1024 * 1024,
            max_redirects: 10,
            log_path: String::new(),
            stats_interval: 5.0,
            robots_error_ttl: 300.0,
            headers: BTreeMap::new(),
        }
    }
}

pub const DEFAULT_HEADERS: [(&str, &str); 2] = [
    ("accept", "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"),
    ("accept-language", "en-US,en;q=0.9"),
];

#[derive(Deserialize, Serialize, Clone, Debug, PartialEq)]
#[serde(default)]
pub struct HostConfig {
    pub initial_rate: f64,
    pub min_rate: f64,
    pub max_rate: f64,
    pub max_concurrency: usize,
    pub additive_increase: f64,
    pub increase_after: u32,
    pub decrease_factor: f64,
    pub max_retry_after: f64,
    pub breaker_threshold: u32,
    pub breaker_cooldown: f64,
    pub breaker_max_cooldown: f64,
    pub blocked_threshold: u32,
}

impl Default for HostConfig {
    fn default() -> Self {
        Self { initial_rate: 1.0, min_rate: 0.05, max_rate: 10.0, max_concurrency: 4, additive_increase: 0.25,
               increase_after: 10, decrease_factor: 0.5, max_retry_after: 600.0, breaker_threshold: 5,
               breaker_cooldown: 30.0, breaker_max_cooldown: 900.0, blocked_threshold: 3 }
    }
}

#[derive(Deserialize, Clone, Debug)]
#[serde(default)]
pub struct Retry {
    pub max_attempts: u32,
    pub base_delay: f64,
    pub max_delay: f64,
}

impl Default for Retry {
    fn default() -> Self {
        Self { max_attempts: 4, base_delay: 1.0, max_delay: 60.0 }
    }
}

#[derive(Deserialize, Clone, Debug, Default)]
pub struct SinkConfig {
    #[serde(rename = "type")]
    pub kind: String,
    #[serde(default)]
    pub path: String,
    pub fields: Option<Vec<String>>,
}

#[derive(Deserialize, Clone, Debug, Default)]
#[serde(default)]
pub struct Config {
    pub resources: Resources,
    pub engine: Engine,
    pub host: HostConfig,
    pub hosts: HashMap<String, toml::Table>, // per-host overrides, merged over `host` on use
    pub retry: Retry,
    pub goal: toml::Table,
    pub sinks: Vec<SinkConfig>,
}

impl Config {
    pub fn parse(text: &str) -> Result<Self, String> {
        let de = toml::Deserializer::parse(text).map_err(|e| e.to_string())?;
        let mut cfg: Config = serde_ignored::deserialize(de, |path| eprintln!("warning: unknown config key {path}"))
            .map_err(|e| e.to_string())?;
        let mut headers: BTreeMap<_, _> = DEFAULT_HEADERS.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        headers.extend(cfg.engine.headers.iter().map(|(k, v)| (k.to_lowercase(), v.clone())));
        cfg.engine.headers = headers;
        cfg.hosts = cfg.hosts.into_iter().map(|(k, v)| (k.to_lowercase(), v)).collect();
        Ok(cfg)
    }

    pub fn load(path: &str) -> Result<Self, String> {
        Self::parse(&std::fs::read_to_string(path).map_err(|e| format!("{path}: {e}"))?)
    }

    /// `[host]` with any `[hosts."name"]` overrides applied (name = host key without port).
    pub fn host_config(&self, host_key: &str) -> HostConfig {
        let name = match host_key.rsplit_once(':') {
            Some((name, port)) if port.chars().all(|c| c.is_ascii_digit()) => name,
            _ => host_key,
        };
        let Some(over) = self.hosts.get(name) else { return self.host.clone() };
        let mut table = toml::Table::try_from(&self.host).expect("host config serializes");
        table.extend(over.clone());
        table.try_into().unwrap_or_else(|e| {
            eprintln!("warning: bad [hosts.\"{name}\"] override: {e}");
            self.host.clone()
        })
    }

    /// Effective global in-flight limit (spec/02-config.md Resources).
    pub fn workers(&self, open_file_limit: Option<u64>, cores: Option<usize>) -> usize {
        let r = self.resources.resolve(cores);
        let per_request_mb = self.engine.max_body_bytes.min(1 << 20) as f64 / (1 << 20) as f64;
        let mut n = r.max_concurrency.min((r.max_memory_mb as f64 / per_request_mb) as usize);
        if let Some(files) = open_file_limit {
            n = n.min(files.saturating_sub(64) as usize);
        }
        n.max(1)
    }
}

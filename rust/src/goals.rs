//! Goal interface and the built-in `fetch` and `crawl` goals (spec/01-model.md, spec/06-goals.md).
use crate::config::Config;
use crate::model::{Output, Response, Task};
use crate::urls::{host_key, join, normalize};
use regex::Regex;
use scraper::{Html, Selector};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use std::collections::HashSet;
use std::sync::{Arc, Mutex};

/// Implement this to build on the engine. `parse` gets `ok` responses, `failed` everything else.
pub trait Goal: Send + Sync {
    fn start(&self) -> Vec<Task>;
    fn parse(&self, _res: &Response) -> Vec<Output> { vec![] }
    fn failed(&self, _res: &Response) -> Vec<Output> { vec![] }
}

/// One record per URL, success or failure.
pub struct FetchGoal {
    pub lines: Vec<String>,
}

impl Goal for FetchGoal {
    fn start(&self) -> Vec<Task> {
        self.lines.iter().map(|l| l.trim()).filter(|l| !l.is_empty() && !l.starts_with('#'))
            .map(|l| if l.starts_with('{') { serde_json::from_str(l).unwrap_or_else(|_| Task::new(l)) } else { Task::new(l) })
            .collect() // unparseable lines become tasks the engine reports as `invalid`
    }
    fn parse(&self, r: &Response) -> Vec<Output> {
        let rec = json!({"url": r.task.url, "final_url": r.url, "status": r.status, "outcome": r.outcome,
                         "attempts": r.attempts, "elapsed_ms": r.elapsed_ms, "bytes": r.body.len(), "error": r.error});
        vec![Output::Record(rec.as_object().cloned().unwrap_or_default())]
    }
    fn failed(&self, r: &Response) -> Vec<Output> { self.parse(r) }
}

#[derive(Deserialize)]
#[serde(default)]
struct CrawlConfig {
    seeds: Vec<String>,
    max_depth: u32,
    max_pages: usize,
    same_host: bool,
    follow: String,
    include: String,
    exclude: String,
    fields: toml::Table,
}

impl Default for CrawlConfig {
    fn default() -> Self {
        Self { seeds: vec![], max_depth: 2, max_pages: 1000, same_host: true, follow: "a[href]".into(),
               include: String::new(), exclude: String::new(), fields: toml::Table::new() }
    }
}

/// Follow links from seeds and extract fields with CSS selectors.
pub struct CrawlGoal {
    seeds: Vec<String>,
    max_depth: u32,
    max_pages: usize,
    hosts: Option<HashSet<String>>,
    follow: Option<Selector>,
    include: Option<Regex>,
    exclude: Option<Regex>,
    fields: Vec<(String, Selector, Option<String>)>, // name, css, attribute
    seen: Mutex<HashSet<String>>,
}

fn selector(css: &str) -> Result<Selector, String> {
    Selector::parse(css).map_err(|e| format!("bad CSS selector {css:?}: {e}"))
}

fn regex(src: &str) -> Result<Option<Regex>, String> {
    if src.is_empty() { Ok(None) } else { Regex::new(src).map(Some).map_err(|e| e.to_string()) }
}

impl CrawlGoal {
    fn new(c: CrawlConfig) -> Result<Self, String> {
        let fields = c.fields.iter().map(|(name, sel)| {
            let sel = sel.as_str().ok_or(format!("field {name} must be a string"))?;
            let (css, attr) = sel.split_once('@').map_or((sel, None), |(c, a)| (c, Some(a.to_string())));
            Ok((name.clone(), selector(css)?, attr))
        }).collect::<Result<_, String>>()?;
        Ok(Self {
            hosts: c.same_host.then(|| c.seeds.iter().filter_map(|s| host_key(s)).collect()),
            follow: if c.follow.is_empty() { None } else { Some(selector(&c.follow)?) },
            include: regex(&c.include)?, exclude: regex(&c.exclude)?, fields,
            seeds: c.seeds, max_depth: c.max_depth, max_pages: c.max_pages, seen: Mutex::new(HashSet::new()),
        })
    }

    fn want(&self, url: &str) -> bool {
        let Some(key) = normalize(url) else { return false };
        let mut seen = self.seen.lock().unwrap();
        let ok = !seen.contains(&key) && (self.max_pages == 0 || seen.len() < self.max_pages)
            && self.hosts.as_ref().is_none_or(|h| host_key(url).is_some_and(|k| h.contains(&k)))
            && self.include.as_ref().is_none_or(|r| r.is_match(url))
            && !self.exclude.as_ref().is_some_and(|r| r.is_match(url));
        if ok { seen.insert(key); }
        ok
    }
}

impl Goal for CrawlGoal {
    fn start(&self) -> Vec<Task> {
        self.seeds.iter().filter(|s| self.want(s)).map(Task::new).collect()
    }

    fn parse(&self, r: &Response) -> Vec<Output> {
        let mut rec = Map::new();
        rec.insert("url".into(), r.url.clone().into());
        if !r.headers.get("content-type").is_some_and(|c| c.contains("html")) {
            return vec![Output::Record(rec)];
        }
        let doc = Html::parse_document(&r.text());
        for (name, css, attr) in &self.fields {
            let value = doc.select(css).next().and_then(|el| match attr {
                Some(a) => el.value().attr(a).map(|v| match a.as_str() {
                    "href" | "src" => join(&r.url, v).unwrap_or_else(|| v.to_string()),
                    _ => v.to_string(),
                }),
                None => Some(el.text().collect::<String>().trim().to_string()),
            });
            rec.insert(name.clone(), value.map_or(Value::Null, Value::String));
        }
        let mut out = vec![Output::Record(rec)];
        if let (true, Some(follow)) = (r.task.depth < self.max_depth, &self.follow) {
            for href in doc.select(follow).filter_map(|a| a.value().attr("href")) {
                if let Some(task) = r.follow(href).filter(|t| self.want(&t.url)) {
                    out.push(Output::Task(task));
                }
            }
        }
        out
    }
}

pub fn make_goal(cfg: &Config, input: Option<&str>) -> Result<Arc<dyn Goal>, String> {
    let kind = cfg.goal.get("type").and_then(|v| v.as_str()).unwrap_or("fetch");
    match kind {
        "fetch" => {
            let mut lines: Vec<String> = cfg.goal.get("urls").and_then(|v| v.as_array())
                .map(|a| a.iter().filter_map(|u| u.as_str().map(String::from)).collect()).unwrap_or_default();
            if let Some(path) = input {
                lines.extend(std::fs::read_to_string(path).map_err(|e| format!("{path}: {e}"))?.lines().map(String::from));
            }
            Ok(Arc::new(FetchGoal { lines }))
        }
        "crawl" => Ok(Arc::new(CrawlGoal::new(cfg.goal.clone().try_into().map_err(|e| format!("[goal]: {e}"))?)?)),
        other => Err(format!("unknown goal type: {other}")),
    }
}

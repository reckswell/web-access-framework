//! Task, Response and goal Output (spec/01-model.md).
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::collections::{BTreeMap, HashMap};

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(default)]
pub struct Task {
    pub url: String,
    pub method: String,
    pub headers: BTreeMap<String, String>,
    pub body: Option<String>,
    pub priority: i64,
    pub depth: u32,
    pub meta: Map<String, Value>,
    pub dedup: bool,
}

impl Default for Task {
    fn default() -> Self {
        Self { url: String::new(), method: "GET".into(), headers: BTreeMap::new(), body: None, priority: 0,
               depth: 0, meta: Map::new(), dedup: true }
    }
}

impl Task {
    pub fn new(url: impl Into<String>) -> Self {
        Self { url: url.into(), ..Default::default() }
    }
}

#[derive(Clone, Debug)]
pub struct Response {
    pub task: Task,
    pub url: String,
    pub status: Option<u16>,
    pub outcome: &'static str,
    pub headers: HashMap<String, String>,
    pub body: Vec<u8>,
    pub attempts: u32,
    pub elapsed_ms: u64,
    pub error: Option<String>,
}

impl Response {
    /// A Response for a task that finished without a request.
    pub fn bare(task: Task, outcome: &'static str, attempts: u32) -> Self {
        Self { url: task.url.clone(), task, status: None, outcome, headers: HashMap::new(), body: vec![],
               attempts, elapsed_ms: 0, error: None }
    }

    /// Body decoded with the content-type charset, else UTF-8 (invalid bytes replaced).
    pub fn text(&self) -> String {
        let label = self.headers.get("content-type").and_then(|c| c.split("charset=").nth(1))
            .map(|c| c.split(';').next().unwrap_or("").trim()).unwrap_or("utf-8");
        let enc = encoding_rs::Encoding::for_label(label.as_bytes()).unwrap_or(encoding_rs::UTF_8);
        enc.decode(&self.body).0.into_owned()
    }

    pub fn json(&self) -> serde_json::Result<Value> {
        serde_json::from_slice(&self.body)
    }

    /// New Task for a link on this page, one level deeper.
    pub fn follow(&self, href: &str) -> Option<Task> {
        Some(Task { depth: self.task.depth + 1, ..Task::new(crate::urls::join(&self.url, href)?) })
    }
}

/// What goals return: output records and new tasks.
pub enum Output {
    Record(Map<String, Value>),
    Task(Task),
}

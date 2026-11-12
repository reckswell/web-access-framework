//! Custom goal: the top 30 Hacker News stories, using the site's official API instead of scraping HTML.
//! Run from rust/:  cargo run --release --example custom_goal
use serde_json::{Map, Value};
use std::sync::Arc;
use webaccess::{sinks::JsonlSink, Config, Engine, Goal, Output, Response, Task};

const API: &str = "https://hacker-news.firebaseio.com/v0";

struct TopStories;

impl Goal for TopStories {
    fn start(&self) -> Vec<Task> {
        vec![Task::new(format!("{API}/topstories.json"))]
    }

    fn parse(&self, r: &Response) -> Vec<Output> {
        match r.json() {
            // the list of story ids -> one task per story
            Ok(Value::Array(ids)) => ids.iter().take(30).filter_map(|id| r.follow(&format!("item/{id}.json"))).map(Output::Task).collect(),
            // one story -> one record
            Ok(Value::Object(item)) => {
                let rec: Map<String, Value> = ["title", "url", "score"].iter()
                    .map(|k| (k.to_string(), item.get(*k).cloned().unwrap_or(Value::Null))).collect();
                vec![Output::Record(rec)]
            }
            _ => vec![],
        }
    }
}

#[tokio::main]
async fn main() {
    let cfg = Config::parse("[host]\nmax_rate = 5.0").unwrap();
    let sink = JsonlSink::new(Some("out/hn.jsonl")).unwrap();
    Engine::new(cfg, Arc::new(TopStories), vec![Box::new(sink)]).unwrap().run().await;
}

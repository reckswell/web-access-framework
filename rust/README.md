# Web Access Framework — Rust

Rust implementation of the [spec](../spec/README.md). `tokio` multi-threaded runtime, `reqwest` with
pure-Rust TLS (`rustls` + `ring`, nothing to install), `scraper` for HTML. Builds on Linux, Windows and
macOS with a stable toolchain.

```bash
cargo build --release
./target/release/webaccess run ../examples/crawl.toml        # target\release\webaccess.exe on Windows
```

## Writing a goal

```rust
use std::sync::Arc;
use webaccess::{sinks::JsonlSink, Config, Engine, Goal, Output, Response, Task};

struct Titles;

impl Goal for Titles {
    fn start(&self) -> Vec<Task> {
        vec![Task::new("https://example.com/")]
    }
    fn parse(&self, r: &Response) -> Vec<Output> {        // called for each `ok` response
        let mut rec = serde_json::Map::new();
        rec.insert("url".into(), r.url.clone().into());
        let mut out = vec![Output::Record(rec)];
        out.extend(r.follow("/about").map(Output::Task));  // new tasks are queued and deduplicated
        out
    }
}

#[tokio::main]
async fn main() {
    let sink = Box::new(JsonlSink::new(Some("out/titles.jsonl")).unwrap());
    Engine::new(Config::load("config.toml").unwrap(), Arc::new(Titles), vec![sink]).unwrap().run().await;
}
```

A full example using an official API: [examples/custom_goal.rs](examples/custom_goal.rs)
(`cargo run --release --example custom_goal`).

## Layout

Module names match the Python implementation and the spec: `urls`, `robots`, `limits`, `config`,
`frontier`, `engine`, `goals`, `sinks`, `model`, plus `main.rs` for the CLI.

## Tests

```bash
cargo test --release                                                  # shared spec vectors
python ../conformance/run.py -- target/release/webaccess              # conformance suite
```

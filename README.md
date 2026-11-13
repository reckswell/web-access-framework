# Web Access Framework

A fast, polite, parallel engine for reaching websites, with **goals** (scrapers, watchers, agents)
built on top. The engine handles *how* to fetch: scheduling, per-site pacing, retries, robots.txt and
output. A goal only says *what* it wants.

Throughput comes from many sites in parallel, each paced at a rate it tolerates. Each site's pace adapts
on its own: it speeds up while responses are healthy and backs off on `429`/`503`/timeouts, honoring
`Retry-After`. That keeps errors and blocks rare without hiding what the client is.

| Folder | What it is |
| --- | --- |
| [`spec/`](spec/README.md) | Language-neutral specification: the source of truth for every implementation |
| [`python/`](python/README.md) | Python implementation (asyncio + aiohttp + selectolax) |
| [`rust/`](rust/README.md) | Rust implementation (tokio + reqwest + scraper) |
| [`conformance/`](conformance/README.md) | Mock website + test cases that every implementation must pass |
| [`bench/`](bench/README.md) | Throughput benchmark for either implementation, on a local server or live sites |
| [`examples/`](examples/) | Ready-to-run config files |

Both implementations read the same config files, take the same CLI arguments and produce the same
output, so you can switch between them freely. To port the framework to another language, follow the
[porting checklist](spec/README.md#porting-checklist).

## Quick start

Works the same on Ubuntu, other Linux distributions, Windows and macOS.

```bash
# Python 3.11+
pip install -e "python[fast]"          # [fast] adds uvloop on Linux/macOS; skipped on Windows
python -m webaccess run examples/fetch.toml

# Rust (stable toolchain)
cd rust && cargo build --release
./target/release/webaccess run ../examples/fetch.toml     # webaccess.exe on Windows
```

## Resource use

Lightweight by default, and scales up when you allow it:

```toml
[resources]
level = "low"            # "low" (default) | "balanced" | "max"
max_concurrency = 0      # global in-flight requests (0 = from level)
threads = 0              # CPU threads (0 = from level)
max_memory_mb = 0        # memory budget for in-flight responses (0 = from level)
```

See [spec/02-config.md](spec/02-config.md#resources) for the exact presets.

## Scope

In scope: high-throughput, well-behaved access; JavaScript rendering, sessions and logins, ad and
overlay handling, extraction models and more goal types are on the [roadmap](#roadmap).

Out of scope by design: hiding automation from sites (fingerprint spoofing, stealth plugins, CAPTCHA
solving, proxy rotation to evade bans or rate limits) and automating ticket queues or purchases. When a
site challenges or blocks the client, the engine stops contacting that host and reports it.

Safety defaults: only `https://` URLs are fetched (`https_only = true`, which also covers redirects), and
`blocked_domains` / `blocklist_files` (hosts-file format, so public malware and phishing lists work as-is)
keep the engine away from unsafe sites.

## Roadmap

1. **Core engine** (this release): scheduler, per-host adaptive limits, robots.txt, retries, HTTPS-only and
   blocklists, international domains and page encodings, `fetch` and `crawl` goals, JSONL/CSV output,
   event log, resource levels, conformance suite, CI on 3 OSes.
2. Browser tier: Playwright pool for JavaScript pages, ad/overlay blocking, screenshots.
3. Sessions: saved browser profiles, manual login, credential login from config, live-window handoff.
4. Extraction chain: structured data, readability, model plugins (text and vision).
5. More goals and sinks: multi-engine search via official APIs ([spec](spec/10-search.md)), browser agent
   (games), watch/notify, Parquet, SQLite, Postgres, S3.
6. Scale-out: shared queue across machines, benchmarks.

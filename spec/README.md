# Web Access Framework — Specification

This folder is the **source of truth** for every implementation (`python/`, `rust/`, and any future port).
Code follows the spec; when they disagree, the spec wins and the code is a bug.

The words MUST, SHOULD and MAY are used as in RFC 2119.

## What the framework is

A reusable engine that handles *how* to reach websites quickly and reliably, so a **goal** (scraper,
watcher, game agent, …) only describes *what* it wants. Throughput comes from many hosts in parallel;
each host is paced at a rate it tolerates, which is what keeps errors and blocks rare.

Out of scope by design: hiding automation from sites (fingerprint spoofing, stealth plugins, CAPTCHA
solving, proxy rotation to evade bans or rate limits) and automating ticket queues or purchases.
When a site challenges or blocks us, the engine stops contacting that host and reports it.

## Documents

| File | Contents |
| --- | --- |
| [01-model.md](01-model.md) | Data types: Task, Response, Outcome, Record, Goal, Sink |
| [02-config.md](02-config.md) | Configuration file format, every key and default |
| [03-engine.md](03-engine.md) | URL normalization, dedup, the frontier and the worker loop |
| [04-politeness.md](04-politeness.md) | Per-host adaptive rate limiting, circuit breaker, robots.txt |
| [05-errors.md](05-errors.md) | Response classification, retries and backoff |
| [06-goals.md](06-goals.md) | Built-in goals: `fetch` and `crawl` |
| [07-observability.md](07-observability.md) | Event log, progress line, run summary |
| [08-cli.md](08-cli.md) | Command-line contract every implementation exposes |
| [09-platforms.md](09-platforms.md) | Ubuntu, Windows, other Linux and macOS rules |
| [schemas/](schemas/) | JSON Schemas for tasks, fetch records and events |
| [vectors/](vectors/) | Shared test vectors every implementation MUST pass |

## Conformance

An implementation is conformant when it:

1. Passes every case in `spec/vectors/*.json` in its own unit tests.
2. Passes `python3 conformance/run.py -- <its run command>` (see [conformance/README.md](../conformance/README.md)),
   which drives the CLI in [08-cli.md](08-cli.md) against a local mock server.

## Porting checklist

Implement modules in this order; each has vectors or conformance cases you can test against immediately.

1. `urls` — normalize, host key, join (vectors: `url_normalize.json`).
2. `classify` — status/error → outcome, signal, retry (vectors: `classify.json`).
3. `limiter` — per-host AIMD state machine (vectors: `limiter.json`).
4. `robots` — parser and matcher (vectors: `robots.json`).
5. `config` — TOML loading with defaults ([02-config.md](02-config.md)).
6. `frontier` + `engine` — scheduling, retries, events.
7. `sinks`, `goals`, `cli` — then run the conformance suite.

Keep module and field names identical to the spec (adjusting only case conventions, e.g. `max_rate` /
`maxRate`) so readers can move between implementations without a map.

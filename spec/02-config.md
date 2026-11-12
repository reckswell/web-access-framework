# 02 — Configuration

Configuration is a single TOML file. Every key is optional; missing keys take the defaults below.
Unknown keys MUST be ignored with a warning (so newer configs still load in older ports).

```toml
[resources]                    # how much of the machine the engine may use (see "Resources" below)
level = "low"                  # "low" (default, lightweight) | "balanced" | "max"; presets for the keys below
max_concurrency = 0            # global in-flight requests; 0 = from level
threads = 0                    # CPU threads for the runtime and parsing; 0 = from level
max_memory_mb = 0              # soft memory budget for in-flight responses; 0 = from level

[engine]
user_agent = "web-access-framework/0.1 (+https://github.com/reckswell/web-access-framework)"
robots_agent = "web-access-framework"   # product token matched against robots.txt groups
respect_robots = true
request_timeout = 30.0         # seconds per attempt, connect + full body
max_body_bytes = 10485760      # bodies are truncated beyond this
max_redirects = 10
log_path = ""                  # JSONL event log; "" = no log file
stats_interval = 5.0           # seconds between progress lines on stderr; 0 = off
robots_error_ttl = 300.0       # seconds to treat a host as fully disallowed after robots.txt fails

[engine.headers]               # sent with every request (task headers override)
accept = "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"
accept-language = "en-US,en;q=0.9"

[host]                         # per-host politeness defaults (see 04-politeness.md)
initial_rate = 1.0             # requests/second when a host is first seen
min_rate = 0.05
max_rate = 10.0
max_concurrency = 4            # in-flight requests per host
additive_increase = 0.25       # rate += this after `increase_after` healthy responses
increase_after = 10
decrease_factor = 0.5          # rate *= this on a throttle signal
max_retry_after = 600.0        # Retry-After values are capped to this
breaker_threshold = 5          # consecutive failures that pause the host
breaker_cooldown = 30.0        # first pause length; doubles on each trip
breaker_max_cooldown = 900.0
blocked_threshold = 3          # consecutive blocked responses that stop the host for the run

[hosts."example.com"]          # overrides for one host (any [host] key)
max_rate = 2.0

[retry]
max_attempts = 4               # total attempts per task, including the first
base_delay = 1.0               # seconds; see 05-errors.md for the formula
max_delay = 60.0

[goal]                         # which built-in goal `run` uses; see 06-goals.md
type = "fetch"

[[sinks]]                      # zero or more; see below
type = "jsonl"
path = "out/records.jsonl"
```

## Rules

- **Host overrides** match the URL's host name (lower-cased, no port) exactly. Override keys replace the
  matching `[host]` keys; all other keys keep the `[host]` values.
- **Header names** are case-insensitive; implementations SHOULD lower-case them when merging.
- `initial_rate` is clamped to `[min_rate, max_rate]`. If robots.txt gives a `Crawl-delay` of `d` seconds,
  the host's `max_rate` becomes `min(max_rate, 1/d)` and its current rate is clamped to that.
- Relative sink and log paths resolve against the current working directory. Parent folders are created.

## Resources

`cores` = logical CPUs reported by the OS. A non-zero key overrides its preset.

| `level` | `max_concurrency` | `threads` | `max_memory_mb` |
| --- | --- | --- | --- |
| `low` (default) | `min(8 x cores, 128)` | `1` | `256` |
| `balanced` | `min(32 x cores, 1024)` | `max(1, cores / 2)` (integer division) | `1024` |
| `max` | `min(128 x cores, 4096)` | `cores` | `4096` |

The engine's effective in-flight limit is:

```
per_request_mb = min(max_body_bytes, 1 MiB) / 1 MiB
workers = max(1, min(max_concurrency, floor(max_memory_mb / per_request_mb), open_file_limit - 64))
```

`open_file_limit` is the process's soft limit on open files (sockets count). On Unix, implementations
SHOULD first try to raise the soft limit to `min(hard limit, 10240)`. Where no such limit exists
(Windows), the term is ignored.

`threads` bounds CPU threads: Rust sizes its async runtime with it. The Python base implementation runs
one event loop in one process, so `threads` only caps its helper thread pool; multi-process Python is a
later milestone. Queued tasks also use memory (roughly 0.5 KB each); `max_memory_mb` does not count them.

## Sinks

| `type` | Keys | Behavior |
| --- | --- | --- |
| `jsonl` | `path` | One JSON object per line, UTF-8, `\n` endings, keys in insertion order |
| `csv` | `path`, `fields` (optional list) | Header row = `fields`, or the first record's keys. Strings are written as-is, `null`/missing values as empty cells, everything else (numbers, booleans, nested values) as JSON. Keys not in the header are dropped. |
| `stdout` | — | Same as `jsonl` but to standard output |

If no sink is configured, `stdout` is used.

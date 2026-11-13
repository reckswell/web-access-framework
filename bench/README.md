# Benchmarks

`bench/run.py` measures how fast either port fetches, with the politeness defaults left on (robots.txt,
the per-host limiter, the identifying user agent).

## Local mode: the engine alone

The default. A local server (`rust/examples/bench_server.rs`) answers every request after a fixed delay,
so the network and the sites drop out of the numbers. Each of the N URLs uses its own loopback address
(`127.1.1.1`, `127.1.1.2`, ...), so to the engine they are N separate hosts, each fetched once: a
robots.txt request, then the page.

```
python bench/run.py --port rust --hosts 50000 --workers 0,4096,16384 --memory-mb 32768
python bench/run.py --port python --hosts 20000 --workers 0,4096 --memory-mb 32768
```

Run the Python port with the interpreter that has `webaccess` installed (`pip install -e python`).
No Rust toolchain? Download the `webaccess-<OS>` artifact from a CI run on GitHub (Actions → the run →
Artifacts) and unzip it into `rust/target/release/`. `run.py` builds with `cargo` only when it is installed.
Every run uses level `max`. Options:

| Option | Default | Meaning |
| --- | --- | --- |
| `--workers` | `0` | Comma-separated `max_concurrency` values to try; `0` = level `max`'s own (128 × cores, at most 4096) |
| `--memory-mb` | `0` | `max_memory_mb`; `0` = level `max`'s own (4096). Workers are capped at this many MB / 1 MB |
| `--threads` | `0` | Rust runtime threads; `0` = all cores |
| `--hosts` | `20000` | Number of hosts (local mode) |
| `--delay-ms`, `--page-kb` | `100`, `100` | Server delay per request and page size (local mode) |
| `--server-close` | `reset` | Local mode: `reset` ends each connection without leaving TIME_WAIT entries; `fin` closes normally (see `rust/examples/bench_server.rs`) |
| `--input FILE` | | Live mode: fetch these URLs from the internet instead |

Each run prints a row and appends it, with the machine's OS, cores and RAM, to `out/bench/results.jsonl`.
`sites/s` is pages finished per second; `CPU %` is the engine's share of all cores, `CPU ms/site` its CPU time per site and `kernel %` the part spent in the OS; `server CPU s` is
what the local server used on the same machine.

Linux and Windows route all of `127.0.0.0/8` to loopback. macOS routes only `127.0.0.1`, so local mode
needs Linux or Windows.

## Live mode

`--input FILE` fetches a URL list (one per line, e.g. the first N domains of the
[Tranco list](https://tranco-list.eu/) as `https://<domain>/`) at level `max`. The result depends on
your connection, DNS and router as much as on the engine. Home routers can struggle with thousands of
simultaneous connections, so start with `--workers 0` and raise it while watching the network.

## Limits on large worker counts

- **Linux and macOS:** each connection is an open file. The engine raises its soft limit as far as the
  hard limit allows; raise the hard limit (`ulimit -Hn`, `/etc/security/limits.conf`) for more.
- **Windows:** outgoing connections share about 16,000 local ports by default. Above that, raise the
  range as administrator: `netsh int ipv4 set dynamicport tcp start=10000 num=55535`.

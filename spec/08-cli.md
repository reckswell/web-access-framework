# 08 — CLI contract

Every implementation ships a command with this interface (the executable name may differ):

```
webaccess run <config.toml> [--input <file>] [--output <file>]
```

| Argument | Meaning |
| --- | --- |
| `<config.toml>` | Configuration ([02-config.md](02-config.md)) |
| `--input` | Extra tasks for the `fetch` goal ([06-goals.md](06-goals.md)) |
| `--output` | Shortcut: replaces the configured sinks with one `jsonl` sink at this path |

Exit status: `0` when the run completed (even if some tasks failed), `2` for a bad config or
arguments, `130` when interrupted.

| Implementation | Command |
| --- | --- |
| Python | `python -m webaccess run ...` (or the installed `webaccess` script) |
| Rust | `webaccess run ...` (binary built by `cargo build --release`) |

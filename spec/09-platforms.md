# 09 — Platform support

Every implementation MUST run on **Ubuntu** and **Windows 10/11** (both tested in CI), and SHOULD run
on other Linux distributions and **macOS** (also tested in CI). Platform differences stay inside
implementations; configs, inputs and outputs are identical everywhere.

## Rules

| Area | Rule |
| --- | --- |
| Paths | Use the language's path type (`pathlib.Path`, `std::path::PathBuf`). Configs may use `/` on every OS. Never build paths by string concatenation. |
| Text files | Read and write UTF-8 explicitly. Output lines end in `\n` on every OS (no CRLF), so files diff the same everywhere. Input files accept `\n` or `\r\n`. |
| TLS | Use a TLS stack that needs no system install (Rust: `rustls`; Python: the bundled `ssl`). Trust the bundled roots plus the OS certificate store, so custom CAs and TLS-inspecting company proxies work the same in both ports (Rust: platform verifier; Python: `certifi` + `load_default_certs()`). |
| Proxies | Use the `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` environment variables (and the OS proxy settings where the HTTP library reads them), resolved once per run, not per request. Never read `~/.netrc`: logins stored there must not reach crawled sites. |
| Signals | Ctrl-C handling uses the portable API (`KeyboardInterrupt` / `tokio::signal::ctrl_c`). No POSIX-only signals. |
| Event loop | Python MAY use `uvloop` when installed on Linux/macOS; it MUST fall back to the default loop (Windows has no `uvloop`). |
| Open-file limit | Raise it where the OS has one (see [02-config.md](02-config.md) Resources); skip silently elsewhere. macOS's default soft limit is 256, so this matters there. |
| Native dependencies | Only packages that ship prebuilt wheels / pure-Rust crates for Linux, Windows and macOS (x86-64 and ARM64). Nothing that needs a C toolchain on the user's machine. |
| Shell | Tooling and tests use the language's own runner (`python`, `cargo`), never bash-only scripts. |
| Browser tier (later) | Playwright supports all three OSes; profiles live under the per-user app-data folder (`%LOCALAPPDATA%`, `~/.local/share`, `~/Library/Application Support`). |

## CI matrix

`.github/workflows/ci.yml` runs unit tests, vectors and the conformance suite for both implementations
on `ubuntu-latest`, `windows-latest` and `macos-latest`.

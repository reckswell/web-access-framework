# Conformance suite

Checks that an implementation behaves as the [spec](../spec/README.md) says, by running its CLI against a
local mock website. Needs only Python 3.11+ (standard library), on any OS.

```bash
# Python implementation
python conformance/run.py -- python -m webaccess
# Rust implementation (after `cargo build --release` in rust/)
python conformance/run.py -- rust/target/release/webaccess
# One case only
python conformance/run.py -k pacing -- python -m webaccess
```

| File | Purpose |
| --- | --- |
| `server.py` | Mock site: `/ok`, `/status/<code>`, `/flaky`, `/ratelimit`, `/slow`, `/challenge`, `/redirect`, `/bigheader`, `/site/<n>`, robots.txt (503 to the first two fetches as `localhost`). Records every hit. Run it alone with `python conformance/server.py`. |
| `cases.json` | Each case: config overrides, input URLs and expectations (outcomes, record fields and key order, hit counts, minimum gaps between hits, event log). |
| `run.py` | Writes each case's config and input, runs the CLI, and checks the output. |

To add a case, append to `cases.json`; both implementations must then pass it.

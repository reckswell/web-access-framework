# Web Access Framework — Python

Python 3.11+ implementation of the [spec](../spec/README.md). One asyncio event loop, `aiohttp` for HTTP,
`selectolax` (lexbor) for HTML. Runs on Linux, Windows and macOS.

```bash
pip install -e ".[fast]"                 # from this folder; [fast] = uvloop on Linux/macOS
python -m webaccess run ../examples/crawl.toml
```

## Writing a goal

```python
import asyncio
from webaccess import Config, Engine, Goal, Task
from webaccess.sinks import JsonlSink

class Titles(Goal):
    def start(self):
        return [Task("https://example.com/")]

    def parse(self, r):                     # called for each `ok` response
        yield {"url": r.url, "length": len(r.body)}
        yield r.follow("/about")            # new tasks are queued and deduplicated

    def failed(self, r):                    # optional: every other outcome
        yield {"url": r.url, "outcome": r.outcome}

asyncio.run(Engine(Config.load("config.toml"), Titles(), [JsonlSink("out/titles.jsonl")]).run())
```

A full example using an official API: [examples/custom_goal.py](examples/custom_goal.py).

## Layout

| Module | Spec |
| --- | --- |
| `urls.py` | normalize, host key, join ([03](../spec/03-engine.md)) |
| `robots.py` | robots.txt ([04](../spec/04-politeness.md)) |
| `limits.py` | classify, Retry-After, backoff, per-host limiter ([04](../spec/04-politeness.md), [05](../spec/05-errors.md)) |
| `config.py` | config + resource levels ([02](../spec/02-config.md)) |
| `frontier.py` | per-host queues and ready-host heap ([03](../spec/03-engine.md)) |
| `engine.py` | dispatch, requests, retries, events ([03](../spec/03-engine.md), [07](../spec/07-observability.md)) |
| `goals.py`, `sinks.py`, `cli.py` | built-in goals, outputs, CLI ([06](../spec/06-goals.md), [08](../spec/08-cli.md)) |

## Tests

```bash
pip install -e ".[dev]"
python -m pytest                                        # shared spec vectors
python ../conformance/run.py -- python -m webaccess     # conformance suite
```

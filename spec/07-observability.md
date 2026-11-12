# 07 — Observability

## Event log

When `engine.log_path` is set, every event is one JSON line:
`{"ts": <unix seconds, float>, "event": <name>, ...fields}`. Schema:
[schemas/event.schema.json](schemas/event.schema.json).

| Event | Fields | When |
| --- | --- | --- |
| `request` | `url`, `host`, `status`, `outcome`, `attempts`, `elapsed_ms`, `error` | A task finished |
| `retry` | `url`, `host`, `attempt`, `delay`, `reason` | A task was scheduled for retry (`reason` = status or error) |
| `host_rate` | `host`, `rate` | A host's rate changed |
| `host_paused` | `host`, `seconds`, `reason` (`breaker` or `retry_after`) | A host was paused |
| `host_blocked` | `host` | A host hit `blocked_threshold` |
| `summary` | see below | The run ended |

## Progress line

Every `stats_interval` seconds, one line on stderr:

```
[12.0s] done=1520 ok=1490 failed=12 other=18 rate=126.7/s queued=480 inflight=64 hosts=37 paused=2 blocked=1
```

`rate` is tasks finished per second over the whole run.

## Summary

At the end of a run, emitted as the `summary` event and printed to stderr:

| Field | Meaning |
| --- | --- |
| `elapsed` | Run time in seconds |
| `finished` | Tasks finished |
| `requests` | HTTP requests made (attempts, excluding robots.txt) |
| `outcomes` | Object: outcome → count |
| `hosts` | Distinct hosts seen |
| `blocked_hosts` | List of hosts blocked for the run |

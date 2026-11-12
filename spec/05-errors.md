# 05 — Errors, retries and backoff

## Classification

`classify(status, error, body)` maps one attempt to `(outcome, signal, retry)`. Exactly one of
`status` / `error` is set. `error` is one of `timeout`, `network` (DNS, connect, reset, TLS, protocol),
`redirects` (more than `max_redirects`), or `insecure` / `unsafe` (a redirect hop refused by the safety
check in [03-engine.md](03-engine.md)). Vectors: [vectors/classify.json](vectors/classify.json).

Rows are checked top to bottom; the first match wins.

| Condition | Outcome | Signal | Retry |
| --- | --- | --- | --- |
| status in {403, 429, 503} and body is a **challenge** | `blocked` | `blocked` | no |
| error `timeout` or `network` | `failed` | `throttle` | yes |
| error `redirects` | `client_error` | `healthy` | no |
| error `insecure` | `insecure` | `healthy` | no |
| error `unsafe` | `unsafe` | `healthy` | no |
| 200–399 | `ok` | `healthy` | no |
| 401 | `auth_required` | `healthy` | no |
| 403 | `blocked` | `blocked` | no |
| 404, 410 | `not_found` | `healthy` | no |
| 408, 429 | `failed` | `throttle` | yes |
| 502, 503, 504 | `failed` | `throttle` | yes |
| 501, 505 | `failed` | `server_error` | no |
| other 500–599 | `failed` | `server_error` | yes |
| other 400–499 | `client_error` | `healthy` | no |

A body is a **challenge** when its first 64 KiB, lower-cased, contains any of:
`captcha`, `cf-chl`, `challenge-platform`, `are you a robot`, `verify you are human`, `unusual traffic`.

Challenges are never worked around. They count toward `blocked_threshold`, after which the host is
skipped for the rest of the run and reported, so a person can decide what to do.

## Retries

A task is retried when `retry` is true, `attempts < max_attempts`, and the method is `GET`/`HEAD` (or
`meta.retry_safe` is true). Otherwise it finishes with the outcome from the table.

Retries re-enter the frontier after a delay, so they also wait for host pacing and breaker pauses.

## Backoff

With `n` = attempts made so far (1 after the first failure):

```
base   = min(max_delay, base_delay * 2^(n-1))
delay  = base/2 + uniform(0, base/2)                   # "equal jitter"
if Retry-After is present: delay = max(delay, min(retry_after, max_retry_after))
```

`Retry-After` is either non-negative integer seconds or an HTTP-date (RFC 9110 §10.2.3); a date in
the past means 0. Unparseable values are ignored.

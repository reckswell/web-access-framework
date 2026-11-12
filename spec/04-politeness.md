# 04 — Politeness: per-host limiter and robots.txt

## Host limiter

Each host has its own state, created on first use from the merged `[host]` + `[hosts."name"]` config.

| State | Initial | Meaning |
| --- | --- | --- |
| `rate` | `clamp(initial_rate, min_rate, max_rate)` | Allowed request starts per second |
| `successes` | 0 | Healthy responses since the last rate change |
| `failures` | 0 | Consecutive failure signals (for the breaker) |
| `blocks` | 0 | Consecutive blocked signals |
| `cooldown` | `breaker_cooldown` | Length of the next breaker pause |
| `paused_until` | 0 | No requests start before this time |
| `blocked` | false | True = no more requests to this host this run |

Each classified response yields one **signal** ([05-errors.md](05-errors.md)). Apply it exactly as below.
Vectors: [vectors/limiter.json](vectors/limiter.json).

**`healthy`**
```
successes += 1; failures = 0; blocks = 0; cooldown = breaker_cooldown
if successes >= increase_after: rate = min(max_rate, rate + additive_increase); successes = 0
```

**`throttle`** (the host asked us to slow down, or is overloaded)
```
rate = max(min_rate, rate * decrease_factor); successes = 0; failures += 1
```

**`server_error`** (the host is erroring, but not because of load)
```
successes = 0; failures += 1
```

**`blocked`**
```
successes = 0; blocks += 1
if blocks >= blocked_threshold: blocked = true          -> emit host_blocked
```

After `throttle` or `server_error`, check the **circuit breaker**:
```
if failures >= breaker_threshold:
    paused_until = max(paused_until, now + cooldown)    -> emit host_paused
    cooldown = min(cooldown * 2, breaker_max_cooldown); failures = 0
```

**Retry-After.** When a response carries `Retry-After` (seconds, or an HTTP-date), after applying the
signal: `paused_until = max(paused_until, now + min(retry_after, max_retry_after))`.

Every change of `rate` emits `host_rate`. Rates SHOULD be rounded to 4 decimal places when compared in
tests.

## robots.txt

Fetched once per host, at the first task for that host, from `scheme://host[:port]/robots.txt`, with
the same user agent and timeout, following redirects. Concurrent first tasks for the same host MUST
share one fetch. The fetch does not count against the host's pacing.

| Fetch result | Treatment |
| --- | --- |
| 2xx | Parse the body |
| 4xx (incl. 404) | Allow everything |
| 5xx, timeout or network error | Disallow everything for `robots_error_ttl` seconds, then fetch again |

Tasks refused because robots.txt could not be fetched finish with `robots_disallowed` and
`error = "robots: <reason>"`, where the reason is the status (`robots: 503`) or the error text of
[05-errors.md](05-errors.md) (`robots: timeout`, `robots: network: ...`). This keeps unreachable hosts
apart from real `Disallow` rules in the output.

### Parsing (RFC 9309, plus `Crawl-delay`)

- Decode the body as UTF-8 (invalid bytes become U+FFFD) and drop a leading byte-order mark, which
  some sites send (e.g. Wikipedia). A BOM left in place would hide the first `User-agent` line.
- Split into lines; strip `#` comments and surrounding whitespace; skip blank lines.
- Each line is `key: value`; keys are case-insensitive. Lines without `:` are ignored.
- A `user-agent` line that follows a rule line (or is the first line) starts a new group; consecutive
  `user-agent` lines share one group.
- `allow` / `disallow` lines add rules to the current group. An empty `disallow` value adds nothing.
- `crawl-delay` sets the current group's delay (float seconds; invalid values ignored).
- Rules before any `user-agent` line are ignored.

### Matching

- **Group selection:** groups whose user-agent value equals `robots_agent` (case-insensitive) are
  used, rules merged. If none match, the `*` groups are used. If none exist, everything is allowed.
- **Target** = URL path, plus `?query` when a query exists.
- **Patterns:** `*` matches any sequence of characters; a trailing `$` anchors to the end. Otherwise a
  pattern matches as a prefix.
- The matching rule with the **longest pattern** (character count, as written) wins. On a tie,
  `allow` wins. No matching rule → allowed.
- `/robots.txt` itself is always allowed.

Vectors: [vectors/robots.json](vectors/robots.json).

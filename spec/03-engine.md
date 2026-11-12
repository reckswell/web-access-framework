# 03 — Engine

## URL normalization

`normalize(url)` returns the canonical string used for dedup and host keys. Vectors:
[vectors/url_normalize.json](vectors/url_normalize.json).

1. Parse the URL. If parsing fails, or the scheme is not `http`/`https`, or there is no host → `invalid`.
2. Lower-case the scheme and host.
3. Drop the port if it is the scheme default (80 for http, 443 for https).
4. An empty path becomes `/`.
5. Drop the fragment (`#...`) and any userinfo (`user:password@`).
6. Keep the query string byte-for-byte; drop a bare trailing `?`.

`host_key(url)` = normalized host, plus `:port` when the port is not the default. All per-host state is
keyed by it. `join(base, href)` resolves a relative reference per RFC 3986 §5.

## Dedup

The engine keeps a set of normalized URLs seen this run. A task with `dedup = true` and method `GET`
whose normalized URL is already in the set is dropped silently (no Response, no Record). Retries of a
task never count as duplicates.

## Frontier

The frontier holds pending tasks grouped by host.

- Each **host queue** is ordered by `priority` (high first), then by insertion order (FIFO).
- A host is **ready** when its queue is non-empty, its in-flight count is below its `max_concurrency`,
  and `now >= max(next_at, paused_until)`.
- `next_at` implements pacing: when a task is dispatched for a host, `next_at = now + 1 / rate`.
- Across hosts, the host that became ready earliest goes first. Priority does not cross hosts.

Reference implementation (both `python/` and `rust/` use it): a min-heap of `(ready_time, seq, host)`
holding every host that has queued work and spare concurrency. `next()` pops the top; if its stored
`ready_time` is stale (the host's `max(next_at, paused_until)` changed), it is pushed back with the new
time. If the top is in the future, the caller sleeps until then or until a new task / released slot
wakes it. When a request finishes, the host's in-flight count drops and the host is re-inserted if it
has work. This is `O(log hosts)` per dispatch.

## Worker loop

The engine runs `workers` workers (the effective in-flight limit from [02-config.md](02-config.md)
Resources). Each worker repeats:

1. `task = frontier.next()`; stop when the run is finished (below).
2. If robots.txt is respected and disallows the URL → finish with `robots_disallowed`.
3. If the host is blocked for the run → finish with `host_blocked`.
4. Send the request (`request_timeout`, `max_redirects`, `max_body_bytes`, merged headers).
5. Classify the result ([05-errors.md](05-errors.md)) and feed the signal to the host limiter
   ([04-politeness.md](04-politeness.md)).
6. If retryable and `attempts < max_attempts`: schedule the same task to re-enter the frontier after the
   backoff delay; emit `retry`. Otherwise finish the task with its outcome.
7. Release the host slot.

**Finishing a task** emits `request`, calls `goal.parse` (outcome `ok`) or `goal.failed` (others), adds
returned Tasks to the frontier and writes returned Records to every sink. Only then does the task stop
counting as outstanding, so the run cannot end between a parent finishing and its children being queued.

## Termination

The engine counts **outstanding** tasks: added and not yet finished (queued, in flight, or waiting
out a retry delay). The run ends when outstanding reaches zero. Then sinks are closed, the `summary`
event is emitted and the summary is printed to stderr.

On SIGINT / Ctrl-C the engine stops dispatching, waits up to `request_timeout` for in-flight requests,
closes sinks and prints the summary.

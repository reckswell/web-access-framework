# 01 — Data model

All types serialize to JSON with the field names shown. Durations are seconds (float) unless the name
ends in `_ms`.

## Task

A request the engine should make. Schema: [schemas/task.schema.json](schemas/task.schema.json).

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `url` | string | required | Absolute `http`/`https` URL |
| `method` | string | `"GET"` | HTTP method |
| `headers` | object<string,string> | `{}` | Extra request headers (override config headers) |
| `body` | string \| null | `null` | Request body (UTF-8) |
| `priority` | int | `0` | Higher runs first **within a host** |
| `depth` | int | `0` | Link distance from a seed (used by `crawl`) |
| `meta` | object | `{}` | Opaque data carried to the Response, for the goal's use |
| `dedup` | bool | `true` | Skip if the normalized URL was already seen this run |

Only `GET` and `HEAD` tasks are retried automatically. Other methods are retried only when
`meta.retry_safe` is `true`.

## Outcome

The final state of a task. Exactly one per task.

| Outcome | Meaning |
| --- | --- |
| `ok` | 2xx/3xx after redirects |
| `not_found` | 404 or 410 |
| `auth_required` | 401 |
| `client_error` | Any other 4xx not listed elsewhere |
| `blocked` | 403, or a challenge page (see [05-errors.md](05-errors.md)) |
| `host_blocked` | Not attempted: the host was blocked earlier in this run |
| `robots_disallowed` | Not attempted: robots.txt disallows the URL |
| `failed` | Retries exhausted on a retryable error |
| `invalid` | URL could not be parsed or has a non-http(s) scheme |

## Response

What a goal receives for each finished task, success or not.

| Field | Type | Meaning |
| --- | --- | --- |
| `task` | Task | The task as submitted |
| `url` | string | Final URL after redirects (equals `task.url` if none) |
| `status` | int \| null | HTTP status of the last attempt; `null` if no response |
| `outcome` | Outcome | See above |
| `headers` | object<string,string> | Response headers, names lower-cased; repeated headers joined with `, ` |
| `body` | bytes | Response body (empty when none); capped at `engine.max_body_bytes` |
| `attempts` | int | Requests made for this task (0 if never attempted) |
| `elapsed_ms` | int | Wall time of the last attempt |
| `error` | string \| null | Short error text for network failures, else `null` |

Implementations SHOULD give Response helpers: `text()` (decode body using the charset in
`content-type`, else UTF-8 with replacement), `json()`, and `follow(href, **task_fields)` which resolves
`href` against `url` and returns a Task with `depth = task.depth + 1`.

## Record

A JSON object a goal emits as output. The engine passes it to every sink unchanged.

## Goal

The interface a user implements. Three operations:

| Operation | Called | Returns |
| --- | --- | --- |
| `start()` | Once, before the run | Initial Tasks |
| `parse(response)` | For each Response with outcome `ok` | Records and/or new Tasks |
| `failed(response)` | For each Response with any other outcome | Records and/or new Tasks (default: none) |

Returned Tasks are added to the frontier; Records go to the sinks. `parse` and `failed` MUST NOT block
on network I/O (they run on the engine's workers).

## Sink

| Operation | Meaning |
| --- | --- |
| `write(record)` | Append one record. MAY buffer. |
| `close()` | Flush and release resources. Called once at the end of the run. |

Built-in sinks: `jsonl`, `csv`, `stdout` (see [02-config.md](02-config.md)).

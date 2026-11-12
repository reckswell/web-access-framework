# 06 — Built-in goals

Built-in goals let common jobs run from a config file with no code. Custom goals implement the
interface in [01-model.md](01-model.md).

## `fetch`

Fetches a list of URLs and emits one record per task, success or failure. Used by the conformance suite.

```toml
[goal]
type = "fetch"
urls = ["https://example.com/a", "https://example.com/b"]   # optional; also read from --input
```

Tasks come from `goal.urls` followed by the `--input` file (one URL per line; lines starting with `{`
are parsed as Task JSON; blank lines and lines starting with `#` are skipped). Lines that fail to parse
as JSON or URL produce a record with outcome `invalid`.

Record ([schemas/fetch-record.schema.json](schemas/fetch-record.schema.json)):

```json
{"url": "...", "final_url": "...", "status": 200, "outcome": "ok", "attempts": 1,
 "elapsed_ms": 42, "bytes": 5120, "error": null}
```

## `crawl`

Starts from seed URLs, follows links, and extracts fields with CSS selectors.

```toml
[goal]
type = "crawl"
seeds = ["https://example.com/"]
max_depth = 2              # seeds are depth 0
max_pages = 1000           # stop adding tasks after this many have been queued; 0 = no limit
same_host = true           # only follow links whose host_key is a seed's host_key
follow = "a[href]"         # CSS selector for links to follow ("" = none)
include = ""               # optional regex a followed URL must match
exclude = ""               # optional regex a followed URL must not match

[goal.fields]              # name = "css selector" -> text of the first match, trimmed
title = "title"            # name = "css selector@attr" -> that attribute of the first match
image = "img@src"          # attribute values named href/src are resolved to absolute URLs
```

For each `ok` response whose `content-type` contains `html`:

1. Emit a record: `{"url": <final url>, <field>: <value or null>, ...}` in field order.
2. If `depth < max_depth`, resolve each `follow` match's `href` against the final URL, keep it if it is
   http(s), passes `same_host` / `include` / `exclude`, and the page budget allows; add as a Task with
   `depth + 1`.

Non-HTML `ok` responses emit `{"url": ...}` only. Failures emit nothing.

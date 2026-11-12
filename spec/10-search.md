# 10 — Search goal and languages (planned, milestone 5)

**Status: specified, not yet implemented.** This defines the interface now so every port builds the
same thing later.

## Languages

The framework already reads pages in every language and script:

| Concern | Where |
| --- | --- |
| Internationalized domain names (`bücher.example`) | Normalized to punycode ([03-engine.md](03-engine.md)) |
| Non-UTF-8 pages (Shift_JIS, GBK, EUC-KR, Windows-1251, …) | `Response.text()` decoding order ([01-model.md](01-model.md)) |
| Preferred content language | `[engine.headers] accept-language`, per task via `headers` |
| Unicode in records | JSONL is UTF-8 with no escaping; CSV is UTF-8 |

## Search goal

Runs queries against **several search engines at once**, merges the results and optionally fetches
each result page. It uses each engine's **official API** or a self-hosted metasearch instance. It never
scrapes result pages: engines' terms forbid automated queries of their HTML results and block them,
while their APIs are stable, structured and permitted.

```toml
[goal]
type = "search"
queries = ["solar panel recycling", "太陽光パネル リサイクル"]
providers = ["brave", "google_cse", "mojeek", "searxng"]
lang = ""                  # ISO 639-1 code such as "ja"; "" = provider default
region = ""                # ISO 3166-1 alpha-2 code such as "JP"; "" = provider default
results_per_query = 20     # per provider
follow = false             # true: also fetch each result URL and emit page records (crawl goal fields apply)

[search.searxng]
base_url = "https://searx.example.org"     # your own instance; JSON output must be enabled

[search.brave]
api_key_env = "BRAVE_API_KEY"               # keys come from environment variables, never the config file
```

### Providers

A provider is two functions, so adding one is small in any language:

| Function | Does |
| --- | --- |
| `request(query, page, lang, region, settings) -> Task` | Builds the API request (URL, headers with the key) |
| `parse(response) -> [Result]` | Turns the API's JSON into results |

`Result` = `{"query", "provider", "rank", "url", "title", "snippet", "lang"}` (rank starts at 1).

Planned providers (check each API's current terms and availability when implementing, since search APIs
change, e.g. the Bing Search APIs were retired in 2025):

| Provider | API | Notes |
| --- | --- | --- |
| `brave` | Brave Search API | Independent index; language and country parameters |
| `google_cse` | Google Programmable Search (Custom Search JSON API) | `lr` / `gl` parameters |
| `mojeek` | Mojeek Search API | Independent index |
| `yandex` | Yandex Search API | Strong for Russian and Turkish |
| `naver` | Naver Search API | Korean |
| `searxng` | Self-hosted SearXNG (`format=json`) | Metasearch over many engines, as configured on your instance |

### Merging

Results from all providers are deduplicated by normalized URL ([03-engine.md](03-engine.md)) and ranked
with **reciprocal rank fusion**: `score(url) = Σ over providers 1 / (60 + rank)`. Records are emitted
in score order per query, with a `providers` list showing which engines returned each URL.

### Politeness

Provider API hosts go through the same per-host limiter as everything else. Set each provider's
`[hosts."api.example"] max_rate` to its documented quota.

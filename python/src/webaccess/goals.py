"""Goal interface and the built-in `fetch` and `crawl` goals (spec/01-model.md, spec/06-goals.md)."""
import json, re
from pathlib import Path
from selectolax.lexbor import LexborHTMLParser as HTMLParser
from .model import Task
from .urls import host_key, join, normalize


class Goal:
    """Subclass and override. parse/failed may return or yield Records (dicts) and Tasks."""

    def start(self):
        return []

    def parse(self, response):
        return []

    def failed(self, response):
        return []


class FetchGoal(Goal):
    """One record per URL, success or failure."""

    def __init__(self, urls=(), input_path=None):
        self.lines = list(urls)
        if input_path:
            self.lines += Path(input_path).read_text(encoding="utf-8").splitlines()

    def start(self):
        for line in map(str.strip, self.lines):
            if line and not line.startswith("#"):
                try:
                    yield Task.from_dict(json.loads(line)) if line.startswith("{") else Task(line)
                except (ValueError, TypeError):
                    yield Task(line)  # engine reports it as `invalid`

    def parse(self, r):
        yield {"url": r.task.url, "final_url": r.url, "status": r.status, "outcome": r.outcome,
               "attempts": r.attempts, "elapsed_ms": r.elapsed_ms, "bytes": len(r.body), "error": r.error}

    failed = parse


class CrawlGoal(Goal):
    """Follow links from seeds and extract fields with CSS selectors."""

    def __init__(self, seeds, max_depth=2, max_pages=1000, same_host=True, follow="a[href]",
                 include="", exclude="", fields=None, **_):
        self.seeds, self.max_depth, self.max_pages, self.follow = seeds, max_depth, max_pages, follow
        self.hosts = {host_key(s) for s in seeds} if same_host else None
        self.include, self.exclude = include and re.compile(include), exclude and re.compile(exclude)
        self.fields = {name: sel.partition("@")[::2] for name, sel in (fields or {}).items()}  # name -> (css, attr)
        self.seen = set()

    def _want(self, url):
        key = normalize(url)
        if (not key or key in self.seen or (self.max_pages and len(self.seen) >= self.max_pages)
                or (self.hosts is not None and host_key(url) not in self.hosts)
                or (self.include and not self.include.search(url)) or (self.exclude and self.exclude.search(url))):
            return False
        self.seen.add(key)
        return True

    def start(self):
        return [Task(s) for s in self.seeds if self._want(s)]

    def parse(self, r):
        if "html" not in r.headers.get("content-type", ""):
            yield {"url": r.url}
            return
        tree, record = HTMLParser(r.text()), {"url": r.url}
        for name, (css, attr) in self.fields.items():
            node = tree.css_first(css)
            value = None if node is None else node.attributes.get(attr) if attr else node.text().strip()
            record[name] = join(r.url, value) if value is not None and attr in ("href", "src") else value
        yield record
        if r.task.depth < self.max_depth and self.follow:
            for a in tree.css(self.follow):
                href = a.attributes.get("href")
                if href and self._want(url := join(r.url, href)):
                    yield r.follow(url)


def make_goal(cfg, input_path=None):
    g = dict(cfg.goal)
    kind = g.pop("type", "fetch")
    if kind == "fetch":
        return FetchGoal(g.get("urls", []), input_path)
    if kind == "crawl":
        return CrawlGoal(**g)
    raise ValueError(f"unknown goal type: {kind}")

"""Task and Response (spec/01-model.md)."""
import codecs, json, re
from dataclasses import dataclass, field, fields, replace
from .urls import join

CHARSET = re.compile(rb"charset\s*=\s*[\"']?\s*([\w.:-]+)", re.I)
META_CHARSET = re.compile(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([\w.:-]+)", re.I)


@dataclass
class Task:
    url: str
    method: str = "GET"
    headers: dict = field(default_factory=dict)
    body: str | None = None
    priority: int = 0
    depth: int = 0
    meta: dict = field(default_factory=dict)
    dedup: bool = True

    @classmethod
    def from_dict(cls, d):
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class Response:
    task: Task
    url: str
    status: int | None
    outcome: str
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    attempts: int = 0
    elapsed_ms: int = 0
    error: str | None = None

    def text(self):
        """Decode by BOM, then content-type charset, then <meta> charset, then UTF-8 (spec/01-model.md)."""
        b = self.body
        for bom, enc in ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
            if b.startswith(bom):
                return b[len(bom):].decode(enc, "replace")
        m = CHARSET.search(self.headers.get("content-type", "").encode()) or META_CHARSET.search(b[:1024])
        try:
            return b.decode(m.group(1).decode() if m else "utf-8", "replace")
        except LookupError:  # unknown label
            return b.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.body)

    def follow(self, href, **task_fields):
        """New Task for a link on this page, one level deeper."""
        return replace(Task(join(self.url, href)), depth=self.task.depth + 1, **task_fields)

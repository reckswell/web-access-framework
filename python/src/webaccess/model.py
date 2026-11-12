"""Task and Response (spec/01-model.md)."""
import json
from dataclasses import dataclass, field, fields, replace
from .urls import join


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
        ctype = self.headers.get("content-type", "")
        charset = ctype.split("charset=")[-1].split(";")[0].strip() if "charset=" in ctype else "utf-8"
        try:
            return self.body.decode(charset, "replace")
        except LookupError:
            return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.body)

    def follow(self, href, **task_fields):
        """New Task for a link on this page, one level deeper."""
        return replace(Task(join(self.url, href)), depth=self.task.depth + 1, **task_fields)

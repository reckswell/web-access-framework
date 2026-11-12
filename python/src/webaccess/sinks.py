"""Output sinks: jsonl, csv, stdout (spec/02-config.md)."""
import csv, json, sys
from pathlib import Path


def _open(path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p.open("w", encoding="utf-8", newline="")  # newline="" -> "\n" on every OS


class JsonlSink:
    def __init__(self, path=None):
        self.f = _open(path) if path else sys.stdout

    def write(self, record):
        self.f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def close(self):
        self.f.flush()
        if self.f is not sys.stdout:
            self.f.close()


class CsvSink:
    def __init__(self, path, fields=None):
        self.f, self.fields, self.w = _open(path), fields, None

    def write(self, record):
        if self.w is None:
            self.w = csv.DictWriter(self.f, self.fields or list(record), extrasaction="ignore", lineterminator="\n")
            self.w.writeheader()
        self.w.writerow({k: json.dumps(v) if isinstance(v, (dict, list, bool)) else v for k, v in record.items()})

    def close(self):
        self.f.close()


def make_sinks(specs):
    """Build sinks from [[sinks]] tables; stdout when none are configured."""
    build = {"jsonl": lambda s: JsonlSink(s["path"]), "stdout": lambda s: JsonlSink(),
             "csv": lambda s: CsvSink(s["path"], s.get("fields"))}
    return [build[s["type"]](s) for s in specs] or [JsonlSink()]

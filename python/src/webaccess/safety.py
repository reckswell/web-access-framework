"""HTTPS-only and blocked-domain checks (spec/03-engine.md Safety checks)."""
from pathlib import Path
from urllib.parse import urlsplit
from .urls import host_name


class Safety:
    def __init__(self, https_only=True, blocked_domains=(), blocklist_files=(), blocklist_text=""):
        self.https_only = https_only
        texts = [blocklist_text] + [Path(p).read_text(encoding="utf-8", errors="replace") for p in blocklist_files]
        last_tokens = (line.split("#", 1)[0].split()[-1:] for t in texts for line in t.splitlines())
        domains = list(blocked_domains) + [t[0] for t in last_tokens if t and "." in t[0]]
        self.blocked = {host_name(f"http://{d.strip()}/") or d.lower() for d in domains}  # same form as hosts

    @classmethod
    def from_config(cls, e):
        return cls(e.https_only, e.blocked_domains, e.blocklist_files)

    def check(self, url):
        """'insecure', 'unsafe' or None."""
        if self.https_only and urlsplit(url.strip()).scheme.lower() != "https":
            return "insecure"
        host = host_name(url) or ""
        while host:  # bad.example also matches www.bad.example
            if host in self.blocked:
                return "unsafe"
            host = host.partition(".")[2]
        return None

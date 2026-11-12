"""Configuration loading with spec defaults (spec/02-config.md)."""
import os, sys, tomllib
from dataclasses import dataclass, field, fields, replace

DEFAULT_HEADERS = {"accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                   "accept-language": "en-US,en;q=0.9"}
LEVELS = {  # level -> (concurrency per core, concurrency cap, threads(cores), memory MB)
    "low": (8, 128, lambda c: 1, 256),
    "balanced": (32, 1024, lambda c: max(1, c // 2), 1024),
    "max": (128, 4096, lambda c: c, 4096),
}


@dataclass(frozen=True)
class Resources:
    level: str = "low"
    max_concurrency: int = 0
    threads: int = 0
    max_memory_mb: int = 0

    def resolve(self, cores=None):
        """Fill zero keys from the level preset."""
        cores = cores or os.cpu_count() or 1
        per_core, cap, threads, mem = LEVELS.get(self.level, LEVELS["low"])
        return replace(self, max_concurrency=self.max_concurrency or min(per_core * cores, cap),
                       threads=self.threads or threads(cores), max_memory_mb=self.max_memory_mb or mem)


@dataclass(frozen=True)
class Engine:
    user_agent: str = "web-access-framework/0.1 (+https://github.com/reckswell/web-access-framework)"
    robots_agent: str = "web-access-framework"
    respect_robots: bool = True
    https_only: bool = True
    blocked_domains: list = field(default_factory=list)
    blocklist_files: list = field(default_factory=list)
    request_timeout: float = 30.0
    max_body_bytes: int = 10 * 1024 * 1024
    max_redirects: int = 10
    log_path: str = ""
    stats_interval: float = 5.0
    robots_error_ttl: float = 300.0
    headers: dict = field(default_factory=lambda: dict(DEFAULT_HEADERS))


@dataclass(frozen=True)
class HostConfig:
    initial_rate: float = 1.0
    min_rate: float = 0.05
    max_rate: float = 10.0
    max_concurrency: int = 4
    additive_increase: float = 0.25
    increase_after: int = 10
    decrease_factor: float = 0.5
    max_retry_after: float = 600.0
    breaker_threshold: int = 5
    breaker_cooldown: float = 30.0
    breaker_max_cooldown: float = 900.0
    blocked_threshold: int = 3


@dataclass(frozen=True)
class Retry:
    max_attempts: int = 4
    base_delay: float = 1.0
    max_delay: float = 60.0


def _build(cls, data, section, base=None):
    """Dataclass from a TOML table, warning about unknown keys."""
    known = {f.name for f in fields(cls)}
    for k in data.keys() - known:
        print(f"warning: unknown config key [{section}] {k}", file=sys.stderr)
    return replace(base or cls(), **{k: v for k, v in data.items() if k in known})


@dataclass
class Config:
    resources: Resources = field(default_factory=Resources)
    engine: Engine = field(default_factory=Engine)
    host: HostConfig = field(default_factory=HostConfig)
    hosts: dict = field(default_factory=dict)  # host name -> HostConfig with overrides applied
    retry: Retry = field(default_factory=Retry)
    goal: dict = field(default_factory=lambda: {"type": "fetch"})
    sinks: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d):
        engine = dict(d.get("engine", {}))
        headers = {**DEFAULT_HEADERS, **{k.lower(): str(v) for k, v in engine.pop("headers", {}).items()}}
        host = _build(HostConfig, d.get("host", {}), "host")
        return cls(resources=_build(Resources, d.get("resources", {}), "resources"),
                   engine=replace(_build(Engine, engine, "engine"), headers=headers), host=host,
                   hosts={k.lower(): _build(HostConfig, v, f"hosts.{k}", host) for k, v in d.get("hosts", {}).items()},
                   retry=_build(Retry, d.get("retry", {}), "retry"),
                   goal=d.get("goal", {"type": "fetch"}), sinks=d.get("sinks", []))

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return cls.from_dict(tomllib.load(f))

    def host_config(self, host_key):
        name, _, port = host_key.rpartition(":")
        return self.hosts.get(name if port.isdigit() else host_key, self.host)

    def workers(self, open_file_limit=None, cores=None):
        """Effective global in-flight limit (spec/02-config.md Resources)."""
        r = self.resources.resolve(cores)
        per_request_mb = min(self.engine.max_body_bytes, 1 << 20) / (1 << 20)
        limits = [r.max_concurrency, int(r.max_memory_mb / per_request_mb)]
        if open_file_limit:
            limits.append(open_file_limit - 64)
        return max(1, min(limits))

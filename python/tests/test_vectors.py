"""Shared spec vectors (spec/vectors/*.json): every implementation must pass these."""
import json
from pathlib import Path
import pytest
from webaccess.config import Config, HostConfig
from webaccess.limits import Host, classify, parse_retry_after
from webaccess.model import Response, Task
from webaccess.robots import Robots
from webaccess.safety import Safety
from webaccess.urls import host_key, join, normalize

VECTORS = Path(__file__).resolve().parents[2] / "spec" / "vectors"
load = lambda name: json.loads((VECTORS / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("v", load("url_normalize.json")["normalize"], ids=lambda v: v["in"] or "empty")
def test_normalize(v):
    assert normalize(v["in"]) == v["out"]
    assert host_key(v["in"]) == v["host"]


@pytest.mark.parametrize("v", load("url_normalize.json")["join"], ids=lambda v: v["href"])
def test_join(v):
    assert normalize(join(v["base"], v["href"])) == v["out"]


@pytest.mark.parametrize("v", load("classify.json")["cases"], ids=lambda v: f"{v['status']}-{v['error']}")
def test_classify(v):
    assert classify(v["status"], v["error"], v["body"].encode()) == (v["outcome"], v["signal"], v["retry"])


@pytest.mark.parametrize("v", load("classify.json")["retry_after"], ids=lambda v: v["value"])
def test_retry_after(v):
    assert parse_retry_after(v["value"], v["now"]) == v["seconds"]


def test_limiter():
    spec = load("limiter.json")
    host = Host("example.com", HostConfig(**spec["config"]))
    for i, step in enumerate(spec["steps"]):
        host.apply(step["signal"], step["now"], step["retry_after"])
        got = {k: getattr(host, k) for k in step["expect"]}
        got["rate"] = round(got["rate"], 4)
        assert got == step["expect"], f"step {i}: {step}"


@pytest.mark.parametrize("case", load("robots.json")["cases"], ids=lambda c: c["name"])
def test_robots(case):
    r = Robots(case["robots"])
    assert r.crawl_delay(case["agent"]) == case["crawl_delay"]
    for target, allowed in case["checks"]:
        assert r.allowed(case["agent"], target) == allowed, target


def test_resources_presets():
    cfg = Config.from_dict({"resources": {"level": "low"}})
    r = cfg.resources.resolve(cores=8)
    assert (r.max_concurrency, r.threads, r.max_memory_mb) == (64, 1, 256)
    assert cfg.workers(open_file_limit=100, cores=8) == 36  # capped by open files - 64
    big = Config.from_dict({"resources": {"level": "max", "max_memory_mb": 200}})
    assert big.workers() == 200  # 1 MiB per request with the default 10 MB body cap


def test_host_overrides():
    cfg = Config.from_dict({"host": {"max_rate": 5.0}, "hosts": {"Example.com": {"max_rate": 2.0}}})
    assert cfg.host_config("example.com:8080").max_rate == 2.0
    assert cfg.host_config("other.com").max_rate == 5.0


@pytest.mark.parametrize("case", load("safety.json")["cases"], ids=lambda c: str(c["checks"][0][0]))
def test_safety(case):
    s = Safety(case["https_only"], case["blocked_domains"], blocklist_text=case["blocklist"])
    for url, want in case["checks"]:
        assert s.check(url) == want, url


@pytest.mark.parametrize("v", load("text_decode.json")["cases"], ids=lambda v: v["name"])
def test_text_decode(v):
    r = Response(Task("https://x.example/"), "https://x.example/", 200, "ok",
                 {"content-type": v["content_type"]}, bytes.fromhex(v["body_hex"]))
    assert r.text() == v["text"]

//! Shared spec vectors (spec/vectors/*.json): every implementation must pass these.
use serde_json::Value;
use webaccess::config::{Config, HostConfig};
use webaccess::limits::{classify, parse_retry_after, Host};
use webaccess::robots::Robots;
use webaccess::urls::{host_key, join, normalize};

fn load(name: &str) -> Value {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../spec/vectors").join(name);
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn s(v: &Value) -> Option<String> {
    v.as_str().map(String::from)
}

#[test]
fn urls() {
    let v = load("url_normalize.json");
    for c in v["normalize"].as_array().unwrap() {
        let input = c["in"].as_str().unwrap();
        assert_eq!(normalize(input), s(&c["out"]), "normalize {input:?}");
        assert_eq!(host_key(input), s(&c["host"]), "host_key {input:?}");
    }
    for c in v["join"].as_array().unwrap() {
        let joined = join(c["base"].as_str().unwrap(), c["href"].as_str().unwrap()).unwrap();
        assert_eq!(normalize(&joined), s(&c["out"]), "join {c}");
    }
}

#[test]
fn classification() {
    let v = load("classify.json");
    for c in v["cases"].as_array().unwrap() {
        let got = classify(c["status"].as_u64().map(|n| n as u16), c["error"].as_str(), c["body"].as_str().unwrap().as_bytes());
        let want = (c["outcome"].as_str().unwrap(), c["signal"].as_str().unwrap(), c["retry"].as_bool().unwrap());
        assert_eq!(got, want, "{c}");
    }
    for c in v["retry_after"].as_array().unwrap() {
        assert_eq!(parse_retry_after(c["value"].as_str().unwrap(), c["now"].as_f64().unwrap()), c["seconds"].as_f64(), "{c}");
    }
}

#[test]
fn limiter() {
    let v = load("limiter.json");
    let cfg: HostConfig = serde_json::from_value(v["config"].clone()).unwrap();
    let mut host = Host::new("example.com".into(), cfg);
    for (i, step) in v["steps"].as_array().unwrap().iter().enumerate() {
        host.apply(step["signal"].as_str().unwrap(), step["now"].as_f64().unwrap(), step["retry_after"].as_f64());
        let e = &step["expect"];
        let got = ((host.rate * 1e4).round() / 1e4, host.successes, host.failures, host.blocks, host.cooldown, host.paused_until, host.blocked);
        let want = (e["rate"].as_f64().unwrap(), e["successes"].as_u64().unwrap() as u32, e["failures"].as_u64().unwrap() as u32,
                    e["blocks"].as_u64().unwrap() as u32, e["cooldown"].as_f64().unwrap(), e["paused_until"].as_f64().unwrap(),
                    e["blocked"].as_bool().unwrap());
        assert_eq!(got, want, "step {i}: {step}");
    }
}

#[test]
fn robots() {
    for c in load("robots.json")["cases"].as_array().unwrap() {
        let (r, agent) = (Robots::parse(c["robots"].as_str().unwrap()), c["agent"].as_str().unwrap());
        assert_eq!(r.crawl_delay(agent), c["crawl_delay"].as_f64(), "{}", c["name"]);
        for check in c["checks"].as_array().unwrap() {
            assert_eq!(r.allowed(agent, check[0].as_str().unwrap()), check[1].as_bool().unwrap(), "{}: {}", c["name"], check[0]);
        }
    }
}

#[test]
fn resources_presets() {
    let cfg = Config::parse("[resources]\nlevel = \"low\"").unwrap();
    let r = cfg.resources.resolve(Some(8));
    assert_eq!((r.max_concurrency, r.threads, r.max_memory_mb), (64, 1, 256));
    assert_eq!(cfg.workers(Some(100), Some(8)), 36); // capped by open files - 64
    let big = Config::parse("[resources]\nlevel = \"max\"\nmax_memory_mb = 200").unwrap();
    assert_eq!(big.workers(None, None), 200); // 1 MiB per request with the default 10 MB body cap
}

#[test]
fn host_overrides() {
    let cfg = Config::parse("[host]\nmax_rate = 5.0\n[hosts.\"Example.com\"]\nmax_rate = 2.0").unwrap();
    assert_eq!(cfg.host_config("example.com:8080").max_rate, 2.0);
    assert_eq!(cfg.host_config("other.com").max_rate, 5.0);
}

#[test]
fn safety() {
    for c in load("safety.json")["cases"].as_array().unwrap() {
        let domains: Vec<String> = c["blocked_domains"].as_array().unwrap().iter().map(|d| d.as_str().unwrap().into()).collect();
        let s = webaccess::safety::Safety::new(c["https_only"].as_bool().unwrap(), &domains, c["blocklist"].as_str().unwrap());
        for check in c["checks"].as_array().unwrap() {
            assert_eq!(s.check(check[0].as_str().unwrap()).map(String::from), self::s(&check[1]), "{}", check[0]);
        }
    }
}

#[test]
fn text_decode() {
    for c in load("text_decode.json")["cases"].as_array().unwrap() {
        let hex = c["body_hex"].as_str().unwrap();
        let body = (0..hex.len()).step_by(2).map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap()).collect();
        let mut r = webaccess::Response::bare(webaccess::Task::new("https://x.example/"), "ok", 1);
        r.headers.insert("content-type".into(), c["content_type"].as_str().unwrap().into());
        r.body = body;
        assert_eq!(r.text(), c["text"].as_str().unwrap(), "{}", c["name"]);
    }
}

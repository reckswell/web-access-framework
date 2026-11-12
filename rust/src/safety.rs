//! HTTPS-only and blocked-domain checks (spec/03-engine.md Safety checks).
use crate::config::Engine;
use crate::urls::host_name;
use std::collections::HashSet;

pub struct Safety {
    https_only: bool,
    blocked: HashSet<String>,
}

impl Safety {
    pub fn new(https_only: bool, domains: &[String], blocklist_text: &str) -> Self {
        let from_text = blocklist_text.lines()
            .filter_map(|l| l.split('#').next()?.split_whitespace().last()).filter(|d| d.contains('.'));
        let blocked = domains.iter().map(String::as_str).chain(from_text)
            .map(|d| host_name(&format!("http://{}/", d.trim())).unwrap_or_else(|| d.to_lowercase())).collect();
        Self { https_only, blocked }
    }

    pub fn from_config(e: &Engine) -> Result<Self, String> {
        let text = e.blocklist_files.iter()
            .map(|p| std::fs::read(p).map(|b| String::from_utf8_lossy(&b).into_owned()).map_err(|err| format!("{p}: {err}")))
            .collect::<Result<Vec<_>, _>>()?.join("\n");
        Ok(Self::new(e.https_only, &e.blocked_domains, &text))
    }

    /// `insecure`, `unsafe` or None.
    pub fn check(&self, url: &str) -> Option<&'static str> {
        let scheme = url.trim().split_once(':').map(|(s, _)| s.to_ascii_lowercase());
        if self.https_only && scheme.as_deref() != Some("https") {
            return Some("insecure");
        }
        let host = host_name(url)?;
        let mut h = host.as_str();
        loop { // bad.example also matches www.bad.example
            if self.blocked.contains(h) {
                return Some("unsafe");
            }
            h = h.split_once('.')?.1;
        }
    }
}

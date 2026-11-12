//! URL normalization, host keys and joining (spec/03-engine.md).
use url::Url;

fn parse(raw: &str) -> Option<Url> {
    let mut u = Url::parse(raw.trim()).ok()?;
    if !matches!(u.scheme(), "http" | "https") || u.host_str().is_none() {
        return None;
    }
    u.set_fragment(None);
    if u.query() == Some("") {
        u.set_query(None);
    }
    let _ = (u.set_username(""), u.set_password(None)); // drop userinfo
    Some(u)
}

pub fn normalize(raw: &str) -> Option<String> {
    parse(raw).map(String::from)
}

pub fn host_key(raw: &str) -> Option<String> {
    let u = parse(raw)?;
    let host = u.host_str()?;
    Some(u.port().map_or_else(|| host.to_string(), |p| format!("{host}:{p}"))) // port() is None when default
}

pub fn join(base: &str, href: &str) -> Option<String> {
    Url::parse(base).ok()?.join(href.trim()).ok().map(String::from)
}

"""URL normalization, host keys and joining (spec/03-engine.md)."""
from urllib.parse import urljoin, urlsplit, urlunsplit

DEFAULT_PORTS = {"http": 80, "https": 443}


def _parts(url):
    """(scheme, host_key, path, query) of a normalized URL, or None if invalid."""
    try:
        u = urlsplit(url.strip())
        port = u.port
    except (ValueError, AttributeError):
        return None
    scheme, host = u.scheme.lower(), u.hostname  # urllib lower-cases the host
    if scheme not in DEFAULT_PORTS or not host:
        return None
    host = f"[{host}]" if ":" in host else host  # IPv6 literal
    if port not in (None, DEFAULT_PORTS[scheme]):
        host = f"{host}:{port}"
    return scheme, host, u.path or "/", u.query


def normalize(url):
    p = _parts(url)
    return p and urlunsplit((*p, ""))


def host_key(url):
    p = _parts(url)
    return p and p[1]


def join(base, href):
    return urljoin(base, href.strip())

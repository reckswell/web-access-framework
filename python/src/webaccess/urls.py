"""URL normalization, host keys and joining (spec/03-engine.md)."""
from urllib.parse import urljoin, urlsplit, urlunsplit

DEFAULT_PORTS = {"http": 80, "https": 443}


def _parts(url):
    """(scheme, host, port or None, path, query) of a normalized URL, or None if invalid."""
    try:
        u = urlsplit(url.strip())
        port = u.port
    except (ValueError, AttributeError):
        return None
    scheme, host = u.scheme.lower(), u.hostname  # urllib lower-cases the host
    if scheme not in DEFAULT_PORTS or not host:
        return None
    if not host.isascii():  # internationalized domain -> punycode
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
    host = f"[{host}]" if ":" in host else host  # IPv6 literal
    return scheme, host, None if port == DEFAULT_PORTS[scheme] else port, u.path or "/", u.query


def _netloc(host, port):
    return host if port is None else f"{host}:{port}"


def normalize(url):
    p = _parts(url)
    return p and urlunsplit((p[0], _netloc(p[1], p[2]), p[3], p[4], ""))


def host_key(url):
    p = _parts(url)
    return p and _netloc(p[1], p[2])


def host_name(url):
    """Normalized host without the port."""
    p = _parts(url)
    return p and p[1]


def join(base, href):
    return urljoin(base, href.strip())

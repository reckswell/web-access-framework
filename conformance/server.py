"""Local mock website for the conformance suite. Standard library only, runs on any OS.

Every request is recorded so cases can assert on hit counts and timing. Requested as `localhost`, robots.txt
answers 503, so cases can test an unreachable robots.txt.
"""
import json, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs

ROBOTS = "User-agent: *\nDisallow: /private\n"
CHALLENGE = "<html><body><div class='g-recaptcha'>Please complete the CAPTCHA</div></body></html>"


class State:
    def __init__(self):
        self.lock, self.hits, self.counters = threading.Lock(), [], {}

    def hit(self, path):  # record a request; returns how many times this exact path+query was seen
        with self.lock:
            self.hits.append((time.monotonic(), path))
            self.counters[path] = self.counters.get(path, 0) + 1
            return self.counters[path]


def site_page(n, limit=6):
    links = "".join(f'<a href="/site/{c}">child {c}</a>' for c in (2 * n + 1, 2 * n + 2) if c <= limit)
    return (f"<html><head><title>Page {n}</title></head><body><span class='price'> {n} </span>"
            f"<noscript><b>JS off</b></noscript><img src='/img/{n}.png'>{links}<a href='/site/0#top'>top</a>"
            f"<a href='mailto:x@example.com'>mail</a></body></html>")


class Handler(BaseHTTPRequestHandler):
    state: State  # set by serve()
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep test output quiet
        pass

    def send(self, code, body="", ctype="text/plain", headers=()):
        data = body.encode()
        self.send_response(code)
        for k, v in [("Content-Type", ctype), ("Content-Length", str(len(data))), *headers]:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        p = url.path
        if p == "/__hits":  # test helper: timestamps of hits whose path starts with ?prefix=
            with self.state.lock:
                ts = [t for t, h in self.state.hits if h.startswith(q.get("prefix", ""))]
            return self.send(200, json.dumps(ts), "application/json")
        n = self.state.hit(self.path)
        if p == "/robots.txt":
            down = self.headers.get("Host", "").startswith("localhost")
            return self.send(503, "down") if down else self.send(200, ROBOTS)
        if p == "/ok":
            return self.send(200, "ok")
        if p.startswith("/status/"):
            return self.send(int(p.split("/")[2]), "status")
        if p == "/flaky":  # fails with 503 `fail` times per distinct URL, then succeeds
            return self.send(503 if n <= int(q.get("fail", 1)) else 200, "flaky")
        if p == "/ratelimit":  # first hit: 429 with Retry-After
            return self.send(429, "slow down", headers=[("Retry-After", q.get("wait", "1"))]) if n == 1 else self.send(200, "ok")
        if p == "/slow":
            time.sleep(int(q.get("ms", 1000)) / 1000)
            return self.send(200, "slow")
        if p == "/challenge":
            return self.send(403, CHALLENGE, "text/html")
        if p == "/redirect":
            return self.send(302, "", headers=[("Location", q.get("to", "/ok"))])
        if p.startswith("/site/"):
            return self.send(200, site_page(int(p.split("/")[2])), "text/html; charset=utf-8")
        if p == "/bigheader":  # some real sites send 10-20 KB Content-Security-Policy headers
            return self.send(200, "ok", headers=[("Content-Security-Policy", "default-src 'self' " + "x" * 20000)])
        if p.startswith("/img/"):
            return self.send(200, "img", "image/png")
        return self.send(404, "not found")


def serve():
    """Start on a free port in a background thread; returns (server, state, base_url)."""
    state = State()
    handler = type("H", (Handler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    server.handle_error = lambda request, address: None  # clients that time out close sockets early
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, state, f"http://127.0.0.1:{server.server_address[1]}"


if __name__ == "__main__":
    srv, _, base = serve()
    print(f"serving at {base} (Ctrl-C to stop)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()

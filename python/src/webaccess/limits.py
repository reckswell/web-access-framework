"""Response classification, backoff and the per-host adaptive limiter (spec/04, spec/05)."""
import random
from dataclasses import replace
from email.utils import parsedate_to_datetime

CHALLENGE_MARKERS = (b"captcha", b"cf-chl", b"challenge-platform", b"are you a robot", b"verify you are human",
                     b"unusual traffic")
STATUS = {  # status -> (outcome, signal, retry); anything not listed falls through to the range rules
    401: ("auth_required", "healthy", False), 403: ("blocked", "blocked", False),
    404: ("not_found", "healthy", False), 410: ("not_found", "healthy", False),
    408: ("failed", "throttle", True), 429: ("failed", "throttle", True),
    502: ("failed", "throttle", True), 503: ("failed", "throttle", True), 504: ("failed", "throttle", True),
    501: ("failed", "server_error", False), 505: ("failed", "server_error", False),
}


def is_challenge(body: bytes):
    head = body[:65536].lower()
    return any(m in head for m in CHALLENGE_MARKERS)


def classify(status, error, body=b""):
    """One attempt -> (outcome, signal, retry)."""
    if status in (403, 429, 503) and is_challenge(body):
        return "blocked", "blocked", False
    if error:
        return ("client_error", "healthy", False) if error == "redirects" else ("failed", "throttle", True)
    if 200 <= status < 400:
        return "ok", "healthy", False
    if status in STATUS:
        return STATUS[status]
    return ("failed", "server_error", True) if status >= 500 else ("client_error", "healthy", False)


def parse_retry_after(value, now):
    """Retry-After header -> seconds to wait (>= 0), or None if unparseable."""
    v = value.strip()
    if v.isdigit():
        return float(v)
    try:
        return max(0.0, parsedate_to_datetime(v).timestamp() - now)
    except (TypeError, ValueError, IndexError):
        return None


def backoff(attempts, retry, retry_after=None, max_retry_after=600.0):
    """Equal-jitter exponential backoff, never shorter than Retry-After."""
    base = min(retry.max_delay, retry.base_delay * 2 ** (attempts - 1))
    delay = base / 2 + random.uniform(0, base / 2)
    return delay if retry_after is None else max(delay, min(retry_after, max_retry_after))


class Host:
    """Per-host politeness state plus the frontier's scheduling fields."""

    def __init__(self, key, cfg):
        self.key, self.cfg = key, cfg
        self.rate = min(max(cfg.initial_rate, cfg.min_rate), cfg.max_rate)
        self.successes = self.failures = self.blocks = 0
        self.cooldown, self.paused_until, self.blocked = cfg.breaker_cooldown, 0.0, False
        self.queue, self.inflight, self.next_at, self.in_heap, self.robots = [], 0, 0.0, False, None

    def ready_at(self):
        return 0.0 if self.blocked else max(self.next_at, self.paused_until)

    def cap_rate(self, max_rate):  # robots.txt Crawl-delay
        self.cfg = replace(self.cfg, max_rate=min(self.cfg.max_rate, max_rate))
        self.rate = min(self.rate, self.cfg.max_rate)

    def apply(self, signal, now, retry_after=None):
        """Apply one signal; returns events as [(name, fields)]."""
        c, events, old_rate = self.cfg, [], self.rate
        if signal == "healthy":
            self.failures = self.blocks = 0
            self.cooldown = c.breaker_cooldown
            self.successes += 1
            if self.successes >= c.increase_after:
                self.rate, self.successes = min(c.max_rate, self.rate + c.additive_increase), 0
        elif signal == "blocked":
            self.successes, self.blocks = 0, self.blocks + 1
            if self.blocks >= c.blocked_threshold and not self.blocked:
                self.blocked = True
                events.append(("host_blocked", {}))
        else:  # throttle or server_error
            if signal == "throttle":
                self.rate = max(c.min_rate, self.rate * c.decrease_factor)
            self.successes, self.failures = 0, self.failures + 1
            if self.failures >= c.breaker_threshold:  # circuit breaker
                events.append(self._pause(now + self.cooldown, "breaker", now))
                self.cooldown, self.failures = min(self.cooldown * 2, c.breaker_max_cooldown), 0
        if retry_after is not None:
            events.append(self._pause(now + min(retry_after, c.max_retry_after), "retry_after", now))
        if self.rate != old_rate:
            events.append(("host_rate", {"rate": round(self.rate, 4)}))
        return events

    def _pause(self, until, reason, now):
        self.paused_until = max(self.paused_until, until)
        return "host_paused", {"seconds": round(self.paused_until - now, 3), "reason": reason}

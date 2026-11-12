"""The engine: dispatch, requests, retries, robots.txt, events (spec/03-engine.md)."""
import asyncio, json, ssl, sys, time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp, certifi

from .frontier import Frontier
from .limits import Host, backoff, classify, parse_retry_after
from .model import Response, Task
from .robots import DENY_ALL, Robots
from .safety import Safety
from .sinks import make_sinks
from .urls import host_key, normalize

INF = float("inf")


class Refused(Exception):
    """A redirect hop refused by the safety check; args[0] is `insecure` or `unsafe`."""


def open_file_limit():
    """Raise the soft open-file limit where the OS has one (spec/09-platforms.md); None on Windows."""
    try:
        import resource
    except ImportError:
        return None
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    want = min(10240, hard if hard != resource.RLIM_INFINITY else 10240)
    if soft < want:
        with suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    return resource.getrlimit(resource.RLIMIT_NOFILE)[0]


class Engine:
    def __init__(self, config, goal, sinks=None):
        self.cfg, self.goal = config, goal
        self.sinks = sinks if sinks is not None else make_sinks(config.sinks)
        self.frontier, self.hosts, self.seen = Frontier(), {}, set()
        self.safety = Safety.from_config(config.engine)
        self.outstanding = self.requests = self.finished = 0
        self.outcomes, self.blocked_hosts, self.wake = {}, [], asyncio.Event()
        log = config.engine.log_path
        if log:
            Path(log).parent.mkdir(parents=True, exist_ok=True)
        self.log = open(log, "w", encoding="utf-8", newline="") if log else None

    # ---- public -------------------------------------------------------------------------------
    def add(self, task):
        """Queue a task (deduplicated by normalized URL)."""
        key = normalize(task.url)
        if key and task.dedup and task.method == "GET":
            if key in self.seen:
                return
            self.seen.add(key)
        self.outstanding += 1
        if refused := ("invalid" if not key else self.safety.check(task.url)):
            return self._finish(Response(task, task.url, None, refused))
        hk = host_key(task.url)
        host = self.hosts.get(hk) or self.hosts.setdefault(hk, Host(hk, self.cfg.host_config(hk)))
        self.frontier.push(host, task)
        self.wake.set()

    async def run(self):
        """Run until every task is finished; returns the summary dict."""
        e, r = self.cfg.engine, self.cfg.resources.resolve()
        workers = self.cfg.workers(open_file_limit())
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(r.threads))  # used for DNS lookups
        self.slots, self.running, self.start = asyncio.Semaphore(workers), set(), time.monotonic()
        self.timeout = aiohttp.ClientTimeout(total=e.request_timeout)
        self.headers = {**e.headers, "user-agent": e.user_agent}
        async def guard(req, handler):  # runs on every hop, so redirects are checked too
            if refused := self.safety.check(str(req.url)):
                raise Refused(refused)
            return await handler(req)

        connector = aiohttp.TCPConnector(limit=workers, ttl_dns_cache=300,
                                         ssl=ssl.create_default_context(cafile=certifi.where()))
        stats = asyncio.create_task(self._stats()) if e.stats_interval > 0 else None
        try:
            async with aiohttp.ClientSession(connector=connector, timeout=self.timeout, trust_env=True,
                                         middlewares=(guard,)) as self.session:
                for task in self.goal.start():
                    self.add(task)
                await self._dispatch()
        finally:
            if stats:
                stats.cancel()
            for t in list(self.running):
                t.cancel()
            for s in self.sinks:
                s.close()
            summary = self._summary()
            self._emit("summary", **summary)
            print("summary: " + json.dumps(summary), file=sys.stderr)
            if self.log:
                self.log.close()
        return summary

    # ---- dispatch -----------------------------------------------------------------------------
    async def _dispatch(self):
        while self.outstanding:
            await self.slots.acquire()
            while not isinstance(got := self.frontier.pop(time.monotonic()), tuple):
                if not self.outstanding:
                    self.slots.release()
                    return
                self.wake.clear()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.wake.wait(), got)
            t = asyncio.create_task(self._handle(*got))
            self.running.add(t)
            t.add_done_callback(self.running.discard)

    async def _handle(self, host, task, attempts):
        try:
            if host.blocked:
                return self._finish(Response(task, task.url, None, "host_blocked", attempts=attempts))
            if not await self._allowed(host, task.url):
                return self._finish(Response(task, task.url, None, "robots_disallowed", attempts=attempts))
            attempts += 1
            self.requests += 1
            res = await self._request(task, attempts)
            outcome, signal, retry = classify(res.status, res.error and res.error.split(":")[0], res.body)
            ra = res.headers.get("retry-after")
            ra = ra and parse_retry_after(ra, time.time())
            for name, fields in host.apply(signal, time.monotonic(), ra):
                self._emit(name, host=host.key, **fields)
                if name == "host_blocked":
                    self.blocked_hosts.append(host.key)
            safe = task.method in ("GET", "HEAD") or task.meta.get("retry_safe")
            if retry and safe and attempts < self.cfg.retry.max_attempts:
                delay = backoff(attempts, self.cfg.retry, ra, host.cfg.max_retry_after)
                self._emit("retry", url=task.url, host=host.key, attempt=attempts, delay=round(delay, 3),
                           reason=str(res.status or res.error))
                asyncio.get_running_loop().call_later(delay, self._requeue, host, task, attempts)
                return
            res.outcome = outcome
            self._finish(res)
        finally:
            self.frontier.release(host)
            self.slots.release()
            self.wake.set()

    def _requeue(self, host, task, attempts):
        self.frontier.push(host, task, attempts)
        self.wake.set()

    async def _request(self, task, attempts):
        headers = {**self.headers, **{k.lower(): v for k, v in task.headers.items()}}
        cap, start, e = self.cfg.engine.max_body_bytes, time.monotonic(), self.cfg.engine
        status, rh, body, url, error = None, {}, b"", task.url, None
        try:
            async with self.session.request(task.method, task.url, headers=headers, data=task.body,
                                            max_redirects=e.max_redirects) as r:
                status, url = r.status, str(r.url)
                rh = {k.lower(): ", ".join(r.headers.getall(k)) for k in r.headers}
                buf = bytearray()
                async for chunk in r.content.iter_any():
                    buf += chunk
                    if len(buf) >= cap:
                        break
                body = bytes(buf[:cap])
        except Refused as exc:
            error = exc.args[0]
        except aiohttp.TooManyRedirects:
            error = "redirects"
        except asyncio.TimeoutError:
            error = "timeout"
        except (aiohttp.ClientError, OSError) as exc:
            error = f"network: {type(exc).__name__}"
        if error:
            status, rh, body = None, {}, b""
        return Response(task, url, status, "", rh, body, attempts, int((time.monotonic() - start) * 1000), error)

    # ---- robots.txt ---------------------------------------------------------------------------
    async def _allowed(self, host, url):
        e = self.cfg.engine
        if not e.respect_robots:
            return True
        t = host.robots
        if t is None or (t.done() and t.result()[1] < time.monotonic()):
            host.robots = t = asyncio.ensure_future(self._fetch_robots(host, url))
        robots = (await asyncio.shield(t))[0]
        u = urlsplit(url)
        return robots.allowed(e.robots_agent, (u.path or "/") + (f"?{u.query}" if u.query else ""))

    async def _fetch_robots(self, host, url):
        u = urlsplit(url)
        try:
            async with self.session.get(f"{u.scheme}://{u.netloc}/robots.txt", headers=self.headers,
                                        max_redirects=5) as r:
                text = await r.text(errors="replace") if r.status < 300 else ""
                status = r.status
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError, Refused):
            status = 599
        if status >= 500:
            return Robots(DENY_ALL), time.monotonic() + self.cfg.engine.robots_error_ttl
        robots = Robots(text)
        if (delay := robots.crawl_delay(self.cfg.engine.robots_agent)) and delay > 0:
            host.cap_rate(1 / delay)
        return robots, INF

    # ---- results ------------------------------------------------------------------------------
    def _finish(self, res):
        self._emit("request", url=res.task.url, host=host_key(res.task.url), status=res.status,
                   outcome=res.outcome, attempts=res.attempts, elapsed_ms=res.elapsed_ms, error=res.error)
        try:
            for out in (self.goal.parse(res) if res.outcome == "ok" else self.goal.failed(res)) or ():
                if isinstance(out, Task):
                    self.add(out)  # added before this task stops counting as outstanding
                else:
                    for s in self.sinks:
                        s.write(out)
        except Exception as exc:  # a goal bug must not kill the run
            print(f"goal error on {res.url}: {exc!r}", file=sys.stderr)
        self.outstanding -= 1
        self.finished += 1
        self.outcomes[res.outcome] = self.outcomes.get(res.outcome, 0) + 1
        if not self.outstanding:
            self.wake.set()

    def _emit(self, event, **fields):
        if self.log:
            self.log.write(json.dumps({"ts": round(time.time(), 3), "event": event, **fields}) + "\n")

    def _summary(self):
        return {"elapsed": round(time.monotonic() - self.start, 3), "finished": self.finished,
                "requests": self.requests, "outcomes": self.outcomes, "hosts": len(self.hosts),
                "blocked_hosts": self.blocked_hosts}

    async def _stats(self):
        while True:
            await asyncio.sleep(self.cfg.engine.stats_interval)
            now, o = time.monotonic(), self.outcomes
            el = now - self.start
            print(f"[{el:.1f}s] done={self.finished} ok={o.get('ok', 0)} failed={o.get('failed', 0)} "
                  f"other={self.finished - o.get('ok', 0) - o.get('failed', 0)} rate={self.finished / el:.1f}/s "
                  f"queued={sum(len(h.queue) for h in self.hosts.values())} inflight={len(self.running)} "
                  f"hosts={len(self.hosts)} paused={sum(h.paused_until > now for h in self.hosts.values())} "
                  f"blocked={len(self.blocked_hosts)}", file=sys.stderr)

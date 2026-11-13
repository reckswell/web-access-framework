"""Throughput benchmark for either port (see bench/README.md).

Local mode (default) fetches N hosts from bench_server, so the network and the sites drop out and only
the engine is measured. Every 127.x.y.z address reaches that server as a separate host, so the per-host
limiter stays at its defaults. Linux and Windows route all of 127/8 to loopback; macOS does not.
Live mode (--input FILE) fetches a URL list from the internet instead.

  python bench/run.py --port rust --hosts 50000 --workers 0,4096,16384 --memory-mb 32768
  python bench/run.py --port python --input top10k.txt --memory-mb 32768
"""
import argparse, json, os, platform, shutil, socket, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ".exe" if os.name == "nt" else ""
PROXY_VARS = {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}


def wait_measured(p):
    """Wait for a process; return (CPU seconds, peak memory MB)."""
    if os.name != "nt":
        _, status, ru = os.wait4(p.pid, 0)
        p.returncode = os.waitstatus_to_exitcode(status)
        return ru.ru_utime + ru.ru_stime, ru.ru_maxrss / (1 << 20 if sys.platform == "darwin" else 1 << 10)
    import ctypes
    from ctypes import wintypes
    p.wait()
    times = [wintypes.FILETIME() for _ in range(4)]  # creation, exit, kernel, user
    ctypes.windll.kernel32.GetProcessTimes(int(p._handle), *map(ctypes.byref, times))
    secs = lambda f: (f.dwHighDateTime << 32 | f.dwLowDateTime) / 1e7

    class Counters(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + \
                   [(n, ctypes.c_size_t) for n in ("peak_ws", "ws", "a", "b", "c", "d", "pagefile", "peak_pagefile")]
    m = Counters(cb=ctypes.sizeof(Counters))
    ctypes.windll.psapi.GetProcessMemoryInfo(int(p._handle), ctypes.byref(m), m.cb)
    return secs(times[2]) + secs(times[3]), m.peak_ws / (1 << 20)


def total_ram_gb():
    if os.name == "nt":
        import ctypes
        class Status(ctypes.Structure):  # MEMORYSTATUSEX
            _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in "abcdefg"]
        s = Status(len=ctypes.sizeof(Status))
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
        return s.a / (1 << 30)
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1 << 30)


def host_urls(n, port):
    """n distinct loopback hosts: 127.1.1.1, 127.1.1.2, ... (never .0 or .255)."""
    return [f"http://127.{1 + i // 64516}.{1 + i // 254 % 254}.{1 + i % 254}:{port}/" for i in range(n)]


def start_server(port, delay_ms, page_kb):
    server = subprocess.Popen([str(ROOT / f"rust/target/release/examples/bench_server{EXE}"),
                               str(port), str(delay_ms), str(page_kb)])
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return server
        except OSError:
            time.sleep(0.05)
    server.kill()
    sys.exit(f"bench_server did not start on port {port}")


def python_cmd(env):
    """Command for the Python port. On Windows a venv's python.exe is only a launcher that starts the real
    interpreter as a child, so measuring it would miss the engine: start the base interpreter the same way
    the launcher does instead."""
    if os.name == "nt" and sys.prefix != sys.base_prefix:
        env["__PYVENV_LAUNCHER__"] = sys.executable
        return [sys._base_executable, "-m", "webaccess"]
    return [sys.executable, "-m", "webaccess"]


def run_once(a, workers, out, input_file, env):
    """One engine run -> result dict."""
    resources = {"level": "max", "max_concurrency": workers, "max_memory_mb": a.memory_mb, "threads": a.threads}
    lines = ["[resources]"] + [f"{k} = {json.dumps(v)}" for k, v in resources.items() if v]
    lines += ["[engine]", "stats_interval = 0", f"https_only = {str(bool(a.input)).lower()}",
              "[goal]", 'type = "fetch"']
    cfg, records, log = out / "config.toml", out / "records.jsonl", out / "stderr.txt"
    cfg.write_text("\n".join(lines) + "\n")
    env = dict(env)
    cmd = [str(ROOT / f"rust/target/release/webaccess{EXE}")] if a.port == "rust" else python_cmd(env)
    server = None if a.input else start_server(a.server_port, a.delay_ms, a.page_kb)
    with open(log, "w") as err:
        start = time.monotonic()
        p = subprocess.Popen(cmd + ["run", str(cfg), "--input", str(input_file), "--output", str(records)],
                             stderr=err, env=env)
        cpu, peak = wait_measured(p)
        wall = time.monotonic() - start
    server_cpu = 0.0
    if server:
        server.terminate()
        server_cpu, _ = wait_measured(server)
    text = log.read_text(errors="replace")
    summary = json.loads(text.rsplit("summary: ", 1)[1].splitlines()[0]) if "summary: " in text else {}
    if p.returncode or not summary:
        sys.exit(f"engine failed (exit {p.returncode}):\n{text[-2000:]}")
    cores = os.cpu_count() or 1
    return {"port": a.port, "mode": "live" if a.input else "local", "workers": summary.get("workers"),
            "threads": a.threads or cores, "memory_budget_mb": a.memory_mb or 4096,
            "sites": summary["finished"], "ok": summary["outcomes"].get("ok", 0),
            "sites_per_s": round(summary["finished"] / summary["elapsed"], 1), "wall_s": round(wall, 2),
            "engine_cpu_s": round(cpu, 2), "cpu_used_pct": round(100 * cpu / wall / cores, 1),
            "peak_mb": round(peak, 1), "server_cpu_s": round(server_cpu, 2),
            "delay_ms": None if a.input else a.delay_ms, "page_kb": None if a.input else a.page_kb}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", choices=["rust", "python"], default="rust", help="which implementation to run")
    ap.add_argument("--hosts", type=int, default=20000, help="local mode: number of hosts (one page each)")
    ap.add_argument("--delay-ms", type=int, default=100, help="local mode: server delay per request")
    ap.add_argument("--page-kb", type=int, default=100, help="local mode: page size")
    ap.add_argument("--workers", default="0", help="comma-separated max_concurrency values; 0 = level max's own")
    ap.add_argument("--memory-mb", type=int, default=0, help="max_memory_mb; 0 = level max's own (4096)")
    ap.add_argument("--threads", type=int, default=0, help="0 = all cores")
    ap.add_argument("--input", help="live mode: file with one URL per line")
    ap.add_argument("--server-port", type=int, default=8800)
    ap.add_argument("--out", default=str(ROOT / "out/bench"))
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if (a.port == "rust" or not a.input) and shutil.which("cargo"):  # build what is needed; quick when up to date
        subprocess.run(["cargo", "build", "--release", "--bins", "--example", "bench_server"],
                       cwd=ROOT / "rust", check=True)
    needed = ([f"rust/target/release/webaccess{EXE}"] if a.port == "rust" else []) + \
             ([] if a.input else [f"rust/target/release/examples/bench_server{EXE}"])
    if missing := [n for n in needed if not (ROOT / n).exists()]:
        sys.exit(f"missing {', '.join(missing)}: install Rust, or unzip CI's webaccess-<OS> artifact into rust/target/release/")
    if a.input:
        input_file, env = Path(a.input), dict(os.environ)
    else:  # loopback only: no proxy
        input_file, env = out / "hosts.txt", {k: v for k, v in os.environ.items() if k.lower() not in PROXY_VARS}
        input_file.write_text("\n".join(host_urls(a.hosts, a.server_port)) + "\n")
    machine = {"os": platform.platform(), "cpu": platform.processor() or platform.machine(),
               "cores": os.cpu_count(), "ram_gb": round(total_ram_gb(), 1), "python": platform.python_version()}
    print(json.dumps(machine))
    print(f"{'port':6} {'workers':>7} {'sites/s':>8} {'ok':>7} {'wall s':>7} {'CPU %':>6} {'peak MB':>8} {'server CPU s':>12}")
    for w in (int(x) for x in a.workers.split(",")):
        r = run_once(a, w, out, input_file, env)
        print(f"{r['port']:6} {r['workers']:>7} {r['sites_per_s']:>8} {r['ok']:>7} {r['wall_s']:>7} "
              f"{r['cpu_used_pct']:>6} {r['peak_mb']:>8} {r['server_cpu_s']:>12}", flush=True)
        with open(out / "results.jsonl", "a") as f:
            f.write(json.dumps({**machine, **r}) + "\n")


if __name__ == "__main__":
    main()

"""Run the conformance cases against any implementation's CLI (spec/08-cli.md).

Usage:  python conformance/run.py [-k name-substring] -- <command that runs `webaccess`>
Example: python conformance/run.py -- python -m webaccess
         python conformance/run.py -- rust/target/release/webaccess
"""
import json, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
from server import serve

HERE = Path(__file__).parent


def toml(d, prefix=""):
    """Tiny TOML writer for nested dicts of scalars/lists (enough for test configs)."""
    val = lambda v: json.dumps(v) if not isinstance(v, bool) else str(v).lower()
    lines = [f'"{k}" = {val(v)}' for k, v in d.items() if not isinstance(v, dict)]
    for k, v in d.items():
        if isinstance(v, dict):
            name = f'{prefix}.{k}' if prefix else k
            lines += ["", f"[{name}]", toml(v, name)]
    return "\n".join(lines)


def merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def subst(obj, vars):
    if isinstance(obj, str):
        for k, v in vars.items():
            obj = obj.replace("{" + k + "}", v)
        return obj
    if isinstance(obj, list):
        return [subst(x, vars) for x in obj]
    if isinstance(obj, dict):
        return {k: subst(v, vars) for k, v in obj.items()}
    return obj


def check(case, records, base, tmp):
    """Return a list of failure messages for one case."""
    errs, exp = [], case["expect"]
    by_url = {r.get("url"): r for r in records}
    if "record_count" in exp and len(records) != exp["record_count"]:
        errs.append(f"expected {exp['record_count']} records, got {len(records)}")
    for want in exp.get("records", []):
        got = by_url.get(want["url"])
        if got is None:
            errs.append(f"no record for {want['url']}")
            continue
        for k, v in want.items():
            ok = (got.get(k) is not None) if v is True else got.get(k) == v
            if not ok:
                errs.append(f"{want['url']}: {k} expected {v!r}, got {got.get(k)!r}")
    counts = {}
    for r in records:
        counts[r.get("outcome")] = counts.get(r.get("outcome"), 0) + 1
    for k, v in exp.get("outcome_counts", {}).items():
        if counts.get(k, 0) != v:
            errs.append(f"outcome {k}: expected {v}, got {counts.get(k, 0)}")
    hits = lambda prefix: json.load(urllib.request.urlopen(f"{base}/__hits?prefix={prefix}"))
    for prefix, n in exp.get("hits", {}).items():
        if len(hits(prefix)) != n:
            errs.append(f"hits on {prefix}: expected {n}, got {len(hits(prefix))}")
    for prefix, gap in exp.get("min_gap", {}).items():
        ts = sorted(hits(prefix))
        small = [round(b - a, 3) for a, b in zip(ts, ts[1:]) if b - a < gap]
        if small:
            errs.append(f"gaps on {prefix} below {gap}s: {small}")
    if exp.get("events"):
        log = Path(tmp, "events.jsonl")
        events = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        if not any(e.get("event") == "summary" for e in events):
            errs.append("event log has no summary event")
        if any("ts" not in e or "event" not in e for e in events):
            errs.append("event without ts/event")
    return errs


def main():
    args = sys.argv[1:]
    if "--" not in args:
        sys.exit(__doc__)
    sep = args.index("--")
    opts, cmd = args[:sep], args[sep + 1:]
    only = opts[opts.index("-k") + 1] if "-k" in opts else ""
    spec = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    failed = 0
    for case in spec["cases"]:
        if only not in case["name"]:
            continue
        server, _, base = serve()  # fresh server per case: clean hit counters
        with tempfile.TemporaryDirectory() as tmp:
            vars = {"base": base, "BASE": base.replace("http://", "HTTP://"), "tmp": Path(tmp).as_posix()}
            cfg, out, inp = Path(tmp, "config.toml"), Path(tmp, "out.jsonl"), Path(tmp, "input.txt")
            cfg.write_text(toml(subst(merge(spec["base_config"], case["config"]), vars)), encoding="utf-8")
            inp.write_text("\n".join(subst(case["urls"], vars)) + "\n", encoding="utf-8")
            start = time.monotonic()
            proc = subprocess.run([*cmd, "run", str(cfg), "--input", str(inp), "--output", str(out)],
                                  capture_output=True, text=True, timeout=120)
            took = time.monotonic() - start
            records = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l] if out.exists() else []
            errs = [f"exit code {proc.returncode}: {proc.stderr[-500:]}"] if proc.returncode else []
            errs += check(subst(case, vars), records, base, tmp)
        server.shutdown()
        failed += bool(errs)
        print(f"{'PASS' if not errs else 'FAIL'}  {case['name']}  ({took:.2f}s)")
        for e in errs:
            print(f"      {e}")
    print(f"\n{'all passed' if not failed else f'{failed} case(s) failed'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

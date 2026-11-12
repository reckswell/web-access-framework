"""Command line: webaccess run <config.toml> [--input FILE] [--output FILE] (spec/08-cli.md)."""
import argparse, asyncio, sys
from .config import Config
from .engine import Engine
from .goals import make_goal
from .sinks import JsonlSink, make_sinks


def _loop_factory():
    """uvloop where available (Linux/macOS), otherwise the default loop (Windows)."""
    try:
        import uvloop
        return uvloop.new_event_loop
    except ImportError:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="webaccess")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run the goal in a config file")
    run.add_argument("config")
    run.add_argument("--input", help="extra tasks for the fetch goal (URL or Task JSON per line)")
    run.add_argument("--output", help="write records to this JSONL file instead of the configured sinks")
    args = ap.parse_args(argv)
    try:
        cfg = Config.load(args.config)
        goal = make_goal(cfg, args.input)
        sinks = [JsonlSink(args.output)] if args.output else make_sinks(cfg.sinks)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        with asyncio.Runner(loop_factory=_loop_factory()) as runner:
            runner.run(Engine(cfg, goal, sinks).run())
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())

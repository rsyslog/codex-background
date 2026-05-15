from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace

from codex_bg.config import load_config
from codex_bg.scheduler import Scheduler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codex-bg")
    parser.add_argument("--config", default="scheduler.toml")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "once", "status"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--config", default=argparse.SUPPRESS)
        subparser.add_argument("--debug", action="store_true", default=argparse.SUPPRESS)
        subparser.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    app = load_config(args.config)
    if args.debug:
        app = replace(app, debug=True)
    if args.dry_run:
        app = replace(app, dry_run=True)
    scheduler = Scheduler(app)
    if args.command == "run":
        scheduler.run_forever()
        return 0
    if args.command == "once":
        print(json.dumps(scheduler.once(), indent=2, sort_keys=True))
        return 0
    if args.command == "status":
        print(json.dumps(scheduler.status(), indent=2, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

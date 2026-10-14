# src/legaleta/cli.py
"""legaleta plan|validate|project: JSON in on stdin, JSON out on stdout."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import IO, Any

from legaleta import DISCLAIMER, __version__
from legaleta.api import RequestError, plan_json, project_json, validate_json
from legaleta.scheduler import SchedulingError

_COMMANDS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "plan": plan_json,
    "validate": validate_json,
    "project": project_json,
}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="legaleta",
        description="EU driving-time scheduler (Regulation (EC) 561/2006).\n" + DISCLAIMER,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Reads one JSON request on stdin and writes one JSON response on stdout. "
        "Exit codes: 0 ok, 2 invalid input, 1 no legal plan or internal error.",
    )
    p.add_argument("--version", action="version", version=f"legaleta {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    for name, doc in (
        ("plan", "insert breaks and rests into a route and return the legal ETA"),
        ("validate", "check a timeline against the rules"),
        ("project", "project a driver state forward under a constant activity"),
    ):
        sp = sub.add_parser(
            name,
            help=doc,
            description=f"{doc}.\n{DISCLAIMER}",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        sp.add_argument("--pretty", action="store_true", help="indent the JSON output")
    return p


def main(
    argv: list[str] | None = None,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> int:
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    args = _parser().parse_args(argv)
    try:
        request = json.load(stdin)
    except json.JSONDecodeError as e:
        print(f"invalid JSON on stdin: {e}", file=stderr)
        return 2
    if not isinstance(request, dict):
        print("the request must be a JSON object", file=stderr)
        return 2
    try:
        response = _COMMANDS[args.command](request)
    except RequestError as e:
        print(f"invalid request: {e}", file=stderr)
        return 2
    except SchedulingError as e:
        print(f"cannot plan: {e}", file=stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - the CLI boundary reports and exits 1
        print(f"internal error: {type(e).__name__}: {e}", file=stderr)
        return 1
    json.dump(response, stdout, indent=2 if args.pretty else None)
    stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

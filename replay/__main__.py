"""Command line: `python -m replay run PATH...`."""

from __future__ import annotations

import argparse
import json
import sys

from replay.compare import Result, run_inputs
from replay.report import render_json, render_text

POLICIES = ("fastest", "driver_friendly", "strict")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m replay", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="plan (if needed) and replay request or pair files")
    run.add_argument("paths", nargs="+", help="request/pair JSON files or directories")
    run.add_argument("--policy", default=",".join(POLICIES), help="comma list, default all three")
    run.add_argument("--rules", help="JSON object of rule overrides")
    run.add_argument("--json", action="store_true", help="machine-readable output")
    run.add_argument("--no-switches", action="store_true", help="skip interpretation re-runs")
    return p


def _summary(results: list[Result]) -> dict[str, int]:
    return {
        "replayed": sum(r.status != "SKIP" for r in results),
        "pass": sum(r.status == "PASS" for r in results),
        "fail": sum(r.status == "FAIL" for r in results),
        "skipped": sum(r.status == "SKIP" for r in results),
    }


def _print(results: list[Result], as_json: bool) -> None:
    summary = _summary(results)
    if as_json:
        rows = [
            {
                "name": r.name,
                "policy": r.policy,
                "verdict": r.status,
                "detail": r.detail,
                "report": render_json(r.report) if r.report else None,
            }
            for r in results
        ]
        print(json.dumps({"rows": rows, "summary": summary}, indent=2))
        return
    for r in results:
        print(f"{r.status:4}  {r.name}  {r.policy or '(given plan)'}  {r.detail}".rstrip())
        if r.status == "FAIL" and r.report:
            print(render_text(r.report))
    print(
        f"{summary['replayed']} replayed, {summary['pass']} pass, {summary['fail']} fail, "
        f"{summary['skipped']} skipped"
    )


def main(argv: list[str] | None = None) -> int:
    try:
        ns = _parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    policies = [p for p in ns.policy.split(",") if p]
    if not policies or any(p not in POLICIES for p in policies):
        print(f"unknown policy in {ns.policy!r}; known: {', '.join(POLICIES)}", file=sys.stderr)
        return 2
    try:
        rules = json.loads(ns.rules) if ns.rules else None
    except json.JSONDecodeError as e:
        print(f"--rules is not valid JSON: {e}", file=sys.stderr)
        return 2
    results = run_inputs(ns.paths, policies, rules, not ns.no_switches)
    if not results:
        print("no request or pair files found", file=sys.stderr)
        return 2
    _print(results, ns.json)
    return 1 if any(r.status == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())

"""The comparator: gets plans from legaleta's public JSON API and compares verdicts.

This and `__main__` are the only modules that may import legaleta, and only `legaleta.api`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from legaleta.api import plan_json, validate_json
from replay.fuzz import generate
from replay.report import Report, render_json
from replay.run import replay_plan
from replay.types import Finding, Switches

_BLOCK_FIELDS = (
    "kind",
    "start",
    "minutes",
    "subtype",
    "at",
    "counts_as_rest",
    "interruptions",
    "compensation_minutes",
    "in_vehicle",
)
_LEGALETA_ERRORS = {"RequestError", "SchedulingError"}


@dataclass
class Result:
    name: str
    policy: str | None
    status: Literal["PASS", "FAIL", "SKIP"]
    report: Report | None = None
    detail: str = ""
    response: dict[str, Any] | None = None
    request: dict[str, Any] | None = None


def plan_for(
    request: dict[str, Any], policy: str | None, rules: dict[str, Any] | None
) -> dict[str, Any]:
    req = dict(request)
    if policy:
        req["policy"] = {**(req.get("policy") or {}), "preset": policy}
    if rules:
        base = req.get("rules") or {"preset": "eu561"}
        req["rules"] = {**base, "overrides": {**(base.get("overrides") or {}), **rules}}
    return plan_json(req)


def differential(
    request: dict[str, Any], response: dict[str, Any], report: Report
) -> list[Finding]:
    """Compare pass/fail of `legaleta validate` on the plan's blocks with the replay's."""
    vreq: dict[str, Any] = {
        k: request[k] for k in ("start", "week_timezone", "driver", "rules") if k in request
    }
    vreq["blocks"] = [{k: b[k] for k in _BLOCK_FIELDS if k in b} for b in response["blocks"]]
    try:
        theirs = bool(validate_json(vreq)["violations"])
    except Exception as e:  # noqa: BLE001 - any failure here is itself a finding
        return [Finding("CLAIM_MISMATCH", "VALIDATE_FAILED", f"legaleta validate raised: {e}")]
    mine = any(f.cls == "VIOLATION" for f in report.findings)
    if theirs == mine:
        return []
    return [
        Finding(
            "CLAIM_MISMATCH",
            "VALIDATE_DISAGREES",
            f"legaleta validate says {'illegal' if theirs else 'legal'}, "
            f"the replay says {'illegal' if mine else 'legal'}",
        )
    ]


def load_inputs(paths: list[str]) -> Iterator[tuple[str, dict[str, Any], dict[str, Any] | None]]:
    """Yield (name, request, response or None). Files without a route or a pair are skipped."""
    files: list[Path] = []
    for p in map(Path, paths):
        files += sorted(p.glob("*.json")) if p.is_dir() else [p]
    for f in files:
        data = json.loads(f.read_text())
        if not isinstance(data, dict):
            continue
        if "request" in data and "response" in data:
            yield f.stem, data["request"], data["response"]
        elif "route" in data:
            yield f.stem, data, None


def run_requests(
    items: Iterable[tuple[str, dict[str, Any], dict[str, Any] | None]],
    policies: list[str],
    rules: dict[str, Any] | None,
    reclassify: bool,
) -> list[Result]:
    out: list[Result] = []
    for name, req, resp in items:
        for policy in [None] if resp is not None else policies:
            try:
                response = resp if resp is not None else plan_for(req, policy, rules)
            except Exception as e:
                if type(e).__name__ not in _LEGALETA_ERRORS:
                    raise
                out.append(Result(name, policy, "SKIP", None, f"{type(e).__name__}: {e}"))
                continue
            report = replay_plan(req, response, Switches(), reclassify)
            report.findings.extend(differential(req, response, report))
            out.append(Result(name, policy, report.verdict, report, "", response, req))
    return out


def run_inputs(
    paths: list[str], policies: list[str], rules: dict[str, Any] | None, reclassify: bool
) -> list[Result]:
    return run_requests(load_inputs(paths), policies, rules, reclassify)


def fuzz_run(
    seed: int,
    count: int,
    policies: list[str],
    rules: dict[str, Any] | None,
    reclassify: bool,
    corpus: Path,
) -> list[Result]:
    """Replay generated requests for seeds `seed` to `seed + count - 1`; save failures."""
    results: list[Result] = []
    for s in range(seed, seed + count):
        for r in run_requests([(f"seed{s}", generate(s), None)], policies, rules, reclassify):
            results.append(r)
            if r.status == "FAIL" and r.report and r.request and r.response:
                corpus.mkdir(parents=True, exist_ok=True)
                (corpus / f"seed{s}_{r.policy}.json").write_text(
                    json.dumps(
                        {
                            "seed": s,
                            "request": r.request,
                            "response": r.response,
                            "findings": render_json(r.report)["findings"],
                        },
                        indent=1,
                    )
                )
    return results

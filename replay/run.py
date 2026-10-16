"""Replay a plan: execute it as a log, judge the log, check the plan's claims."""

from __future__ import annotations

from collections import Counter
from dataclasses import fields, replace
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo

from replay.claims import check_claims
from replay.judge import judge_walk
from replay.log import build_log
from replay.regs import regs_from_effective
from replay.report import Report
from replay.start import prehistory
from replay.types import Finding, Switches

_NEUTRAL = ("INTERPRETATION", "NON_STANDARD_RULES")


def _week_tz(request: dict) -> tzinfo:  # type: ignore[type-arg]
    if request.get("week_timezone"):
        return ZoneInfo(request["week_timezone"])
    tz = datetime.fromisoformat(request["start"]).tzinfo
    assert tz is not None
    return tz


def _pipeline(request: dict, response: dict, sw: Switches) -> tuple[list[Finding], int]:  # type: ignore[type-arg]
    regs, f_regs = regs_from_effective((response.get("rules") or {}).get("effective") or {})
    log, f_log = build_log(request, response, sw)
    prehist, counters, f_start = prehistory(request, regs, sw)
    ferries = {
        i["id"]: (i["minutes"], bool(i.get("sleeper_cabin")))
        for i in request["route"]
        if i["kind"] == "ferry"
    }
    f_judge, dend, rend = judge_walk(log, prehist, counters, regs, sw, _week_tz(request), ferries)
    f_claims, checked = check_claims(request, response, log, counters, regs, sw, dend, rend)
    return [*f_regs, *f_log, *f_start, *f_judge, *f_claims], checked


def _key(f: Finding) -> tuple[str, str | None]:
    return f.code, f.article


def replay_plan(
    request: dict,  # type: ignore[type-arg]
    response: dict,  # type: ignore[type-arg]
    sw: Switches = Switches(),  # noqa: B008
    reclassify: bool = True,
) -> Report:
    findings, checked = _pipeline(request, response, sw)
    if reclassify and any(f.cls not in _NEUTRAL for f in findings):
        flipped = []
        for fld in fields(sw):
            alt = replace(sw, **{fld.name: not getattr(sw, fld.name)})
            counts = Counter(_key(f) for f in _pipeline(request, response, alt)[0])
            flipped.append((fld.name, counts))
        seen: Counter[tuple[str, str | None]] = Counter()
        reclassified = []
        for f in findings:
            nth = seen[_key(f)]
            seen[_key(f)] += 1
            # a finding that is gone (fewer of its kind) under the other reading is interpretation
            name = next((n for n, counts in flipped if counts[_key(f)] <= nth), None)
            if f.cls in _NEUTRAL or name is None:
                reclassified.append(f)
            else:
                reclassified.append(
                    replace(f, cls="INTERPRETATION", message=f"[switch {name}] {f.message}")
                )
        findings = reclassified
    return Report(findings, checked, datetime.fromisoformat(request["start"]))

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


_WARNING_CLASSES = ("MISSING_WARNING", "SPURIOUS_WARNING")


def replay_plan(
    request: dict,  # type: ignore[type-arg]
    response: dict,  # type: ignore[type-arg]
    sw: Switches = Switches(),  # noqa: B008
    reclassify: bool = True,
) -> Report:
    findings, checked = _pipeline(request, response, sw)
    if reclassify:
        findings = _with_interpretations(request, response, sw, findings)
    return Report(findings, checked, datetime.fromisoformat(request["start"]))


def _with_interpretations(
    request: dict,  # type: ignore[type-arg]
    response: dict,  # type: ignore[type-arg]
    sw: Switches,
    findings: list[Finding],
) -> list[Finding]:
    """Re-run under each switch flipped once.

    A finding that is gone under the other reading becomes INTERPRETATION (a missing or spurious
    warning never does: the assumption must be disclosed whatever its reading). A PASS that would
    fail under the other reading gets one INTERPRETATION note per switch and finding code, so a
    pass that rests on an assumption says so.
    """
    flips = []
    for fld in fields(sw):
        alt = replace(sw, **{fld.name: not getattr(sw, fld.name)})
        alt_findings = _pipeline(request, response, alt)[0]
        flips.append((fld.name, Counter(_key(f) for f in alt_findings), alt_findings))
    seen: Counter[tuple[str, str | None]] = Counter()
    out: list[Finding] = []
    for f in findings:
        nth = seen[_key(f)]
        seen[_key(f)] += 1
        name = next((n for n, counts, _ in flips if counts[_key(f)] <= nth), None)
        if f.cls in _NEUTRAL or f.cls in _WARNING_CLASSES or name is None:
            out.append(f)
        else:
            out.append(replace(f, cls="INTERPRETATION", message=f"[switch {name}] {f.message}"))
    base = Counter(_key(f) for f in findings)
    for name, counts, alt_findings in flips:
        noted: set[tuple[str, str | None]] = set()
        for f in alt_findings:
            key = _key(f)
            if (
                f.cls in ("VIOLATION", "CLAIM_MISMATCH")
                and counts[key] > base[key]
                and key not in noted
            ):
                noted.add(key)
                out.append(
                    Finding(
                        "INTERPRETATION",
                        "STRICTER_READING",
                        f"[switch {name} flipped] the plan would fail with {f.code}: {f.message}",
                        f.article,
                    )
                )
    return out

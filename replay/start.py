"""The driver's starting state, read independently from `docs/contract.md`.

Only what a retrospective judge can see in the current 24 h period is laid out as activities (the
rest ending at `last_daily_rest_end`, today's driving, where the break sits). Everything that is a
count over earlier days (driving earlier this week and last week, reduced rests used, extended days,
compensation) is a counter the judge seeds from `StartCounters`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from replay.regs import Regs
from replay.types import Activity, Finding, Kind, Label, Switches

PREHISTORY_REST = "prehistory_rest"
PREHISTORY = "prehistory"


@dataclass(frozen=True)
class StartCounters:
    daily_rest_end: int = 0
    weekly_rest_end: int = 0
    driving_this_week_prior: int = 0
    driving_last_week: int = 0
    extended_days_used_this_week: int = 0
    reduced_daily_rests_used: int = 0
    last_weekly_rest_reduced: bool = False
    compensation_owed: int = 0
    compensation_deadline: int | None = None
    split_break_first_taken: bool = False


def _raw_from_remaining(rem: dict[str, Any], regs: Regs) -> dict[str, int | bool | None]:
    """The contract's conversion table, read against the limits it names."""
    today = regs.daily_driving - rem["remaining_drive_today"]
    this_week = regs.weekly_driving - rem["remaining_drive_this_week"]
    deadline = rem.get("compensation_deadline_in")
    return {
        "driving_since_break": regs.break_after_driving - rem["remaining_drive_before_break"],
        "driving_today": today,
        "driving_this_week": this_week,
        "driving_last_week": max(
            0, regs.fortnight_driving - rem["remaining_drive_fortnight"] - this_week
        ),
        "daily_rest_end": -(regs.daily_window - rem["daily_rest_due_in"]),
        "weekly_rest_end": -(
            6 * regs.daily_window + 2 * regs.daily_window - rem["weekly_rest_due_in"]
        ),
        "compensation_deadline": deadline,
    }


def _act(kind: Kind, start: int, end: int, subtype: str = PREHISTORY) -> Activity:
    return Activity(kind, start, end, Label(None, "prehistory", subtype))


def _layout(
    dre: int, today: int, since_break: int, split_first: bool, regs: Regs
) -> list[Activity] | None:
    """Place today's driving after the daily rest; None if it cannot fit before minute 0."""
    early = today - since_break
    if dre > 0 or early < 0 or since_break < 0:
        return None
    cap, brk, part = regs.break_after_driving, regs.break_full, regs.break_split_first
    chunks = -(-early // cap) if early else 0
    need = early + brk * max(chunks - 1, 0) + (brk if early else 0) + since_break
    need += part if split_first else 0
    if need > -dre:
        return None
    out: list[Activity] = []
    t, left = dre, early
    for i in range(chunks):
        d = min(cap, left)
        out.append(_act("DRIVE", t, t + d))
        t, left = t + d, left - d
        if i < chunks - 1:
            out.append(_act("REST", t, t + brk))
            t += brk
    if early:
        out.append(_act("REST", t, t + brk))
        t += brk
    final = -(since_break + (part if split_first else 0))
    if final > t:
        out.append(_act("AVAIL", t, final))
    if since_break:
        out.append(_act("DRIVE", final, final + since_break))
    if split_first:
        out.append(_act("REST", -part, 0))
    return out


def prehistory(
    request: dict,  # type: ignore[type-arg]
    regs: Regs,
    sw: Switches,
) -> tuple[list[Activity], StartCounters, list[Finding]]:
    origin = datetime.fromisoformat(request["start"])

    def minute(s: str) -> int:
        return round((datetime.fromisoformat(s) - origin).total_seconds() / 60)

    driver = request.get("driver") or {}
    findings: list[Finding] = []
    if "remaining" in driver:
        rem = driver["remaining"]
        v = _raw_from_remaining(rem, regs)
        src: dict[str, Any] = rem
        deadline = v["compensation_deadline"]
    else:
        src = driver.get("raw") or {}
        v = {
            "driving_since_break": src.get("driving_since_break", 0),
            "driving_today": src.get("driving_today", 0),
            "driving_this_week": src.get("driving_this_week", 0),
            "driving_last_week": src.get("driving_last_week", 0),
            "daily_rest_end": minute(src["last_daily_rest_end"])
            if src.get("last_daily_rest_end")
            else 0,
            "weekly_rest_end": minute(src["last_weekly_rest_end"])
            if src.get("last_weekly_rest_end")
            else 0,
        }
        cd = src.get("compensation_deadline")
        deadline = minute(cd) if cd else None
    dre, wre = int(v["daily_rest_end"] or 0), int(v["weekly_rest_end"] or 0)
    today, since = int(v["driving_today"] or 0), int(v["driving_since_break"] or 0)
    split_first = bool(src.get("split_break_first_part_taken", False))
    counters = StartCounters(
        daily_rest_end=dre,
        weekly_rest_end=wre,
        driving_this_week_prior=max(0, int(v["driving_this_week"] or 0) - today),
        driving_last_week=int(v["driving_last_week"] or 0),
        extended_days_used_this_week=int(src.get("extended_days_used_this_week", 0)),
        reduced_daily_rests_used=int(src.get("reduced_daily_rests_used", 0)),
        last_weekly_rest_reduced=bool(src.get("last_weekly_rest_reduced", False)),
        compensation_owed=int(src.get("compensation_owed_minutes", 0)),
        compensation_deadline=None if deadline is None else int(deadline),
        split_break_first_taken=split_first,
    )
    if src.get("split_daily_rest_first_part_taken"):
        findings.append(
            Finding(
                "INTERPRETATION",
                "STATE_FLAG_IGNORED",
                "split_daily_rest_first_part_taken is not laid out in the prior log",
                article="Art 8(2)",
            )
        )
    laid = _layout(dre, today, since, split_first, regs)
    if laid is None:
        findings.append(
            Finding(
                "CLAIM_MISMATCH",
                "STATE_UNREPRESENTABLE",
                f"driving today ({today} min) does not fit between the last daily rest end "
                f"({dre}) and the start",
                article="Art 6(1)",
            )
        )
        return [], counters, findings
    length = (
        regs.weekly_rest_reduced if counters.last_weekly_rest_reduced else regs.weekly_rest_regular
    )
    rest_len = length if wre == dre else regs.daily_rest_regular
    return [_act("REST", dre - rest_len, dre, PREHISTORY_REST), *laid], counters, findings

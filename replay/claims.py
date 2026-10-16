"""Compare what the plan claims with what the replay measured."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from replay.judge_driving import DrivingEnd
from replay.judge_rest import RestEnd
from replay.log import ActivityLog
from replay.regs import Regs
from replay.start import StartCounters
from replay.types import Finding, FindingClass, Switches

_STANDING = ("ART12_NOT_MODELLED", "RETURN_HOME_NOT_CHECKED", "WTD_NOT_CHECKED")
_SUMMARY_KEYS = ("driving", "break", "daily_rest", "weekly_rest", "work", "wait", "ferry")


class _Claims:
    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self.checked = 0

    def eq(
        self, code: str, claimed: object, measured: object, what: str, minute: int | None = None
    ) -> None:
        self.checked += 1
        if claimed != measured:
            self.add(
                "CLAIM_MISMATCH",
                code,
                f"{what}: plan says {claimed}, replay measured {measured}",
                minute,
            )

    def add(self, cls: FindingClass, code: str, message: str, minute: int | None = None) -> None:
        self.findings.append(Finding(cls, code, message, None, minute))


def _raw_state(dend: DrivingEnd, rend: RestEnd) -> dict[str, Any]:
    return {
        "driving_since_break": dend.since_break,
        "driving_today": dend.today,
        "driving_this_week": dend.this_week,
        "driving_last_week": dend.last_week,
        "last_daily_rest_end": rend.daily_rest_end,
        "last_weekly_rest_end": rend.weekly_rest_end,
        "last_weekly_rest_reduced": rend.last_weekly_reduced,
        "reduced_daily_rests_used": rend.reduced_used,
        "extended_days_used_this_week": dend.extended_days,
        "compensation_owed_minutes": rend.owed,
        "split_break_first_part_taken": dend.split_first_taken,
    }


def _remaining_state(raw: dict[str, Any], end: int, regs: Regs) -> dict[str, Any]:
    return {
        "remaining_drive_before_break": regs.break_after_driving - raw["driving_since_break"],
        "remaining_drive_today": regs.daily_driving - raw["driving_today"],
        "remaining_drive_this_week": regs.weekly_driving - raw["driving_this_week"],
        "remaining_drive_fortnight": regs.fortnight_driving
        - raw["driving_this_week"]
        - raw["driving_last_week"],
        "daily_rest_due_in": raw["last_daily_rest_end"] + regs.daily_window - end,
        "weekly_rest_due_in": raw["last_weekly_rest_end"] + regs.weekly_rest_deadline - end,
        "reduced_daily_rests_used": raw["reduced_daily_rests_used"],
        "extended_days_used_this_week": raw["extended_days_used_this_week"],
        "last_weekly_rest_reduced": raw["last_weekly_rest_reduced"],
        "compensation_owed_minutes": raw["compensation_owed_minutes"],
        "split_break_first_part_taken": raw["split_break_first_part_taken"],
    }


def check_claims(
    request: dict,  # type: ignore[type-arg]
    response: dict,  # type: ignore[type-arg]
    log: ActivityLog,
    counters: StartCounters,
    regs: Regs,
    sw: Switches,
    dend: DrivingEnd,
    rend: RestEnd,
) -> tuple[list[Finding], int]:
    c = _Claims()
    acts = log.activities
    end = acts[-1].end if acts else 0

    def minute(s: str) -> int:
        return log.minute_of(datetime.fromisoformat(s))

    c.eq("ARRIVAL", minute(response["arrival"]), end, "arrival (minutes from start)", end)
    _stops(c, request, response, log, minute)
    _summary(c, response, log, end)
    _end_state(c, response, minute, end, regs, dend, rend)
    _warnings(c, request, response, log, regs, sw)
    return c.findings, c.checked


def _stops(c: _Claims, request: dict, response: dict, log: ActivityLog, minute) -> None:  # type: ignore[no-untyped-def, type-arg]
    route = {i["id"]: i for i in request["route"] if i["kind"] == "stop"}
    for st in response.get("stops", []):
        req = route.get(st["id"], {})
        at = [a for a in log.activities if a.label.at == st["id"]]
        arr, dep, late = minute(st["arrival"]), minute(st["departure"]), st["lateness_minutes"]
        if at:
            c.eq("STOP_ARRIVAL", arr, at[0].start, f"stop {st['id']} arrival", arr)
            c.eq("STOP_DEPARTURE", dep, at[-1].end, f"stop {st['id']} departure", dep)
        window = req.get("window") or {}
        latest = minute(window["latest"]) if window.get("latest") else None
        earliest = minute(window["earliest"]) if window.get("earliest") else arr
        if latest is None:
            c.eq("STOP_LATENESS", late, 0, f"stop {st['id']} lateness (no window)", arr)
        else:
            lo = max(0, max(arr, earliest) - latest)
            hi = max(lo, dep - req.get("service_minutes", 0) - latest)
            c.checked += 1
            if not lo <= late <= hi:
                c.add(
                    "CLAIM_MISMATCH",
                    "STOP_LATENESS",
                    f"stop {st['id']} lateness: plan says {late}, replay allows {lo} to {hi}",
                    arr,
                )
        c.eq("STOP_WINDOW_MET", st["window_met"], late == 0, f"stop {st['id']} window_met", arr)


def _summary(c: _Claims, response: dict, log: ActivityLog, end: int) -> None:  # type: ignore[type-arg]
    got = response.get("summary", {})
    mine: dict[str, int] = {}
    for a in log.activities:
        key = "driving" if a.label.block_kind == "drive" else a.label.block_kind
        mine[key] = mine.get(key, 0) + a.minutes
    for key in _SUMMARY_KEYS:
        c.eq(f"SUMMARY_{key.upper()}", got.get(key, 0), mine.get(key, 0), f"summary {key} minutes")
    c.eq("SUMMARY_TOTAL", got.get("total", 0), sum(mine.values()), "summary total minutes")


def _end_state(c, response, minute, end, regs, dend, rend) -> None:  # type: ignore[no-untyped-def]
    raw = _raw_state(dend, rend)
    state = response.get("driver_state_at_end", {})
    for k, v in (state.get("raw") or {}).items():
        if k not in raw:
            continue
        claimed = minute(v) if k.startswith("last_") and k.endswith("_end") and v else v
        c.eq(f"END_RAW_{k.upper()}", claimed, raw[k], f"end state {k}", end)
    mine = _remaining_state(raw, end, regs)
    for k, v in (state.get("remaining") or {}).items():
        if k in mine:
            c.eq(f"END_REMAINING_{k.upper()}", v, mine[k], f"end state {k}", end)


def _warnings(c, request, response, log, regs, sw) -> None:  # type: ignore[no-untyped-def]
    have = {str(w["code"]).split(":")[0] for w in response.get("warnings", [])}
    for code in _STANDING:
        c.checked += 1
        if code not in have:
            c.add("MISSING_WARNING", f"MISSING_{code}", f"warning {code} must appear on every plan")
    counted_wait = any(
        a.label.counts_as_rest and a.kind == "REST" and a.label.block_kind in ("wait", "break")
        for a in log.activities
    )
    on_road = any(
        a.label.block_kind == "weekly_rest"
        and a.label.at is None
        and a.minutes - sum(a.label.interruptions) >= regs.weekly_rest_regular
        for a in log.activities
    )
    conditional = {
        "WAIT_ASSUMED_REST": counted_wait,
        "REGULAR_WEEKLY_REST_ON_ROAD": on_road,
        "STATE_FROM_REMAINING": "remaining" in (request.get("driver") or {}),
    }
    for code, needed in conditional.items():
        c.checked += 1
        if needed and code not in have:
            c.add("MISSING_WARNING", f"MISSING_{code}", f"warning {code} is required here")
        elif not needed and code in have:
            c.add(
                "SPURIOUS_WARNING",
                f"SPURIOUS_{code}",
                f"warning {code} has no trigger in the replay",
            )

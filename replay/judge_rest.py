"""Retrospective recognition and check of rests (Art 8, Art 9) over a finished log.

A rest is recognised by what it is in the log (its length and its place in its 24 h period),
never by the label a block carries. Everything is a single pass over the merged rest intervals.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from replay.regs import Regs
from replay.start import PREHISTORY_REST, StartCounters
from replay.types import Activity, Finding, RestPeriod, Switches
from replay.weeks import Weeks


@dataclass
class _Debt:
    amount: int
    due: int | None


@dataclass
class _Merged:
    start: int
    end: int
    interruptions: tuple[int, ...]
    in_vehicle: bool
    prehistory: bool
    weekly_label: bool = False
    at: str | None = None

    @property
    def effective(self) -> int:
        return self.end - self.start - sum(self.interruptions)


def _merge(acts: list[Activity]) -> list[_Merged]:
    out: list[_Merged] = []
    for a in acts:
        if a.kind != "REST" or a.end == a.start or a.label.block_kind == "ferry":
            continue  # a ferry without a cabin counts as a break (Art 7) only, never as a rest
        pre = a.label.subtype == PREHISTORY_REST
        weekly = a.label.block_kind == "weekly_rest" or (pre and a.minutes >= 1440)
        if out and out[-1].end == a.start and out[-1].prehistory == pre:
            m = out[-1]
            m.end = a.end
            m.interruptions += a.label.interruptions
            m.in_vehicle = m.in_vehicle or a.label.in_vehicle
            m.prehistory = m.prehistory and pre
            m.weekly_label = m.weekly_label or weekly
            m.at = m.at or a.label.at
        else:
            out.append(
                _Merged(
                    a.start,
                    a.end,
                    a.label.interruptions,
                    a.label.in_vehicle,
                    pre,
                    weekly,
                    a.label.at,
                )
            )
    return out


def recognise_rests(
    acts: list[Activity], regs: Regs, sw: Switches, period_start: int | None = None
) -> list[RestPeriod]:
    """Classify the daily and weekly rests in `acts` by length and place in the 24 h period."""
    rests: list[RestPeriod] = []
    ps = period_start
    pending: _Merged | None = None

    def add(m: _Merged, kind) -> None:  # type: ignore[no-untyped-def]
        rests.append(
            RestPeriod(m.start, m.end, kind, m.interruptions, m.in_vehicle, m.prehistory, m.at)
        )

    for m in _merge(acts):
        e = m.effective
        if e >= regs.weekly_rest_reduced and m.weekly_label:
            add(m, "weekly_regular" if e >= regs.weekly_rest_regular else "weekly_reduced")
            ps, pending = m.end, None
        elif e >= regs.daily_rest_regular:
            inside = e if ps is None else min(m.end, ps + regs.daily_window) - max(m.start, ps)
            late = sw.window_reduced and ps is not None and inside < regs.daily_rest_regular
            add(m, "daily_reduced" if late else "daily_regular")
            ps, pending = m.end, None
        elif e >= regs.daily_rest_split_second:
            if pending is not None and (ps is None or m.start < ps + regs.daily_window):
                add(pending, "daily_split_1")
                add(m, "daily_split_2")
            else:
                add(m, "daily_reduced")
            ps, pending = m.end, None
        elif e >= regs.daily_rest_split_first:
            pending = m
    return rests


def _v(code: str, article: str, message: str, minute: int | None, excess: int = 0) -> Finding:
    return Finding("VIOLATION", code, message, article, minute, None, excess)


@dataclass(frozen=True)
class RestEnd:
    """Rest-side state at the end of the log."""

    daily_rest_end: int
    weekly_rest_end: int
    last_weekly_reduced: bool
    reduced_used: int
    owed: int
    owed_due: int | None


def check_rests(
    acts: list[Activity],
    rests: list[RestPeriod],
    regs: Regs,
    sw: Switches,
    counters: StartCounters,
    weeks: Weeks,
    ferries: Mapping[str, tuple[int, bool]] | None = None,
) -> list[Finding]:
    return walk_rests(acts, rests, regs, sw, counters, weeks, ferries)[0]


def walk_rests(  # noqa: C901
    acts: list[Activity],
    rests: list[RestPeriod],
    regs: Regs,
    sw: Switches,
    counters: StartCounters,
    weeks: Weeks,
    ferries: Mapping[str, tuple[int, bool]] | None = None,
) -> tuple[list[Finding], RestEnd]:
    out: list[Finding] = []
    log_end = acts[-1].end if acts else 0
    ps = counters.daily_rest_end
    last_daily_end = counters.daily_rest_end
    free_first = False
    if sw.overdue_start_restarts_period:
        while ps + regs.daily_window < regs.daily_rest_reduced:
            ps += regs.daily_window
            free_first = True
    wre = counters.weekly_rest_end
    prev_weekly_reduced = counters.last_weekly_rest_reduced
    reduced_used = counters.reduced_daily_rests_used
    debts: list[_Debt] = []
    if counters.compensation_owed > 0:
        debts.append(_Debt(counters.compensation_owed, counters.compensation_deadline))
    split_start: int | None = None

    for r in rests:
        e = r.end - r.start - sum(r.interruptions)
        if r.kind == "daily_split_1":
            split_start = r.start
            continue
        if r.prehistory:
            ps = last_daily_end = r.end
            continue
        cap = 0
        if r.kind.startswith("weekly"):
            deadline = wre + regs.weekly_rest_deadline
            if r.start > deadline:
                out.append(
                    _v(
                        "WEEKLY_REST_LATE",
                        "Art 8(6)",
                        "weekly rest started after six 24 h periods",
                        r.start,
                        r.start - deadline,
                    )
                )
            if r.kind == "weekly_reduced":
                if prev_weekly_reduced:
                    out.append(
                        _v(
                            "CONSECUTIVE_REDUCED_WEEKLY",
                            "Art 8(6)",
                            "two consecutive reduced weekly rests",
                            r.start,
                        )
                    )
                due = weeks.shift(weeks.floor(r.start), regs.compensation_deadline_weeks + 1)
                debts.append(_Debt(regs.weekly_rest_regular - e, due))
            else:
                cap = e - regs.weekly_rest_regular
                if r.in_vehicle:
                    out.append(
                        _v(
                            "WEEKLY_REST_IN_VEHICLE",
                            "Art 8(8)",
                            "regular weekly rest taken in the vehicle",
                            r.start,
                        )
                    )
            prev_weekly_reduced = r.kind == "weekly_reduced"
            wre, reduced_used, free_first = r.end, 0, False
        else:
            start = (
                split_start if (r.kind == "daily_split_2" and split_start is not None) else r.start
            )
            split_start = None
            if free_first:
                free_first = False
            else:
                latest = ps + regs.daily_window - regs.daily_rest_reduced
                if start > latest:
                    out.append(
                        _v(
                            "DAILY_REST_LATE",
                            "Art 8(2)",
                            "daily rest started too late for its 24 h period",
                            start,
                            start - latest,
                        )
                    )
            if r.kind == "daily_reduced":
                reduced_used += 1
                if reduced_used > regs.reduced_daily_rests_between_weekly:
                    out.append(
                        _v(
                            "REDUCED_DAILY_RESTS_EXCEEDED",
                            "Art 8(4)",
                            f"reduced daily rest {reduced_used} of at most "
                            f"{regs.reduced_daily_rests_between_weekly}",
                            r.start,
                            1,
                        )
                    )
            elif r.kind == "daily_regular":
                cap = e - regs.daily_rest_regular
        if r.interruptions:
            crossing, cabin = (ferries or {}).get(r.at or "", (0, False))
            ok = r.kind in ("daily_regular", "weekly_reduced") or (
                r.kind == "weekly_regular"
                and cabin
                and crossing >= regs.ferry_weekly_rest_min_crossing
            )
            if not ok:
                out.append(
                    _v(
                        "INTERRUPTION_NOT_ALLOWED",
                        "Art 9(1)",
                        f"a {r.kind} rest may not be interrupted",
                        r.start,
                    )
                )
            if len(r.interruptions) > regs.ferry_interruptions_max:
                out.append(
                    _v(
                        "INTERRUPTIONS_EXCEEDED",
                        "Art 9(1)",
                        "more than two interruptions",
                        r.start,
                        len(r.interruptions) - regs.ferry_interruptions_max,
                    )
                )
            if sum(r.interruptions) > regs.ferry_interruption_total:
                out.append(
                    _v(
                        "INTERRUPTIONS_EXCEEDED",
                        "Art 9(1)",
                        "interruptions total over one hour",
                        r.start,
                        sum(r.interruptions) - regs.ferry_interruption_total,
                    )
                )
        ps = last_daily_end = r.end
        for d in sorted(debts, key=lambda d: (d.due is None, d.due or 0)):
            if cap <= 0:
                break
            if d.due is not None and r.end > d.due:
                continue
            pay = d.amount if cap >= d.amount else (0 if sw.compensation_en_bloc else cap)
            d.amount -= pay
            cap -= pay
        debts = [d for d in debts if d.amount > 0]

    for d in debts:
        if d.due is not None and log_end >= d.due:
            out.append(
                _v(
                    "COMPENSATION_UNPAID",
                    "Art 8(6b)",
                    "compensation not taken by its deadline",
                    d.due,
                    d.amount,
                )
            )
    if log_end > ps + regs.daily_window:
        out.append(
            _v(
                "DAILY_REST_MISSING",
                "Art 8(2)",
                "no daily rest within a 24 h period",
                ps + regs.daily_window,
                log_end - (ps + regs.daily_window),
            )
        )
    if log_end > wre + regs.weekly_rest_deadline:
        out.append(
            _v(
                "WEEKLY_REST_MISSING",
                "Art 8(6)",
                "no weekly rest within six 24 h periods",
                wre + regs.weekly_rest_deadline,
                log_end - (wre + regs.weekly_rest_deadline),
            )
        )
    dues = [d.due for d in debts if d.due is not None]
    end = RestEnd(
        last_daily_end,
        wre,
        prev_weekly_reduced,
        reduced_used,
        sum(d.amount for d in debts),
        min(dues) if dues else None,
    )
    return out, end

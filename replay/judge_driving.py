"""Retrospective check of driving limits and breaks (Art 6, Art 7) over a finished log."""

from __future__ import annotations

from dataclasses import dataclass

from replay.regs import Regs
from replay.start import StartCounters
from replay.types import Activity, Finding, RestPeriod
from replay.weeks import Weeks

_DAILY_BOUNDARY = {
    "daily_regular",
    "daily_reduced",
    "daily_split_2",
    "weekly_regular",
    "weekly_reduced",
}


@dataclass(frozen=True)
class DrivingEnd:
    """Driving counters at the end of the log, as a look-back over it."""

    since_break: int
    split_first_taken: bool
    today: int
    extended_days: int
    this_week: int
    last_week: int


def walk_driving(
    acts: list[Activity],
    regs: Regs,
    rests: list[RestPeriod],
    counters: StartCounters,
    weeks: Weeks,
) -> tuple[list[Finding], DrivingEnd]:
    f1, since, pending = _check_breaks(acts, regs)
    f2, extended, today = _check_daily(acts, regs, rests, counters, weeks)
    f3, this_week, last_week = _check_weekly(acts, regs, counters, weeks)
    return [*f1, *f2, *f3], DrivingEnd(since, pending, today, extended, this_week, last_week)


def check_driving(
    acts: list[Activity],
    regs: Regs,
    rests: list[RestPeriod],
    counters: StartCounters,
    weeks: Weeks,
) -> list[Finding]:
    return walk_driving(acts, regs, rests, counters, weeks)[0]


def _check_breaks(acts: list[Activity], regs: Regs) -> tuple[list[Finding], int, bool]:
    """Art 7: within every 4.5 h of driving a 45 min break, or 15 min then 30 min."""
    out: list[Finding] = []
    driven = 0
    pending_first = False
    reported = False
    run = 0  # contiguous REST minutes not yet applied
    prev_end: int | None = None

    def flush() -> None:
        nonlocal driven, pending_first, run, reported
        if run >= regs.break_full or (run >= regs.break_split_second and pending_first):
            driven, pending_first, reported = 0, False, False
        elif run >= regs.break_split_first:
            pending_first = True
        run = 0

    for a in acts:
        contiguous = prev_end is not None and a.start == prev_end
        if a.kind == "REST":
            if not contiguous:
                flush()
            run += a.minutes - sum(a.label.interruptions)
        else:
            flush()
            if a.kind == "DRIVE":
                before = driven
                driven += a.minutes
                if driven > regs.break_after_driving and not reported:
                    reported = True
                    out.append(
                        Finding(
                            "VIOLATION",
                            "BREAK_MISSING",
                            f"{driven} min of driving without a qualifying break",
                            article="Art 7",
                            minute=a.start + max(0, regs.break_after_driving - before),
                            block_index=a.label.block_index,
                            excess_minutes=driven - regs.break_after_driving,
                        )
                    )
                elif reported:
                    out[-1] = _more(out[-1], driven - regs.break_after_driving)
        prev_end = a.end
    flush()
    return out, driven, pending_first


def _more(f: Finding, excess: int) -> Finding:
    return Finding(f.cls, f.code, f.message, f.article, f.minute, f.block_index, excess)


def _check_daily(
    acts: list[Activity],
    regs: Regs,
    rests: list[RestPeriod],
    counters: StartCounters,
    weeks: Weeks,
) -> tuple[list[Finding], int, int]:
    """Art 6(1): driving between daily rests, 10 h at most twice in a calendar week."""
    out: list[Finding] = []
    bounds = sorted(
        (r.end, r.kind.startswith("weekly")) for r in rests if r.kind in _DAILY_BOUNDARY
    )
    i, seg, seg_end, extended = 0, 0, 0, counters.extended_days_used_this_week
    ext_minute = 0  # the minute the day's driving first passed the 9 h cap
    ext_week = weeks.floor(0)
    seg_prior = (
        False  # the segment holds the start state's driving: its extension is already counted
    )

    def close(minute: int) -> None:
        nonlocal seg, extended, seg_prior, ext_week
        if seg > regs.daily_driving and not seg_prior and weeks.floor(ext_minute) != ext_week:
            ext_week, extended = weeks.floor(ext_minute), 0
        if seg > regs.daily_driving_extended:
            out.append(
                Finding(
                    "VIOLATION",
                    "DAILY_DRIVING_EXCEEDED",
                    f"{seg} min of driving between daily rests",
                    article="Art 6(1)",
                    minute=minute,
                    excess_minutes=seg - regs.daily_driving_extended,
                )
            )
        elif seg > regs.daily_driving and not seg_prior:
            extended += 1
            if extended > regs.extended_days_per_week:
                out.append(
                    Finding(
                        "VIOLATION",
                        "EXTENDED_DAY_LIMIT",
                        f"extended day {extended} of at most {regs.extended_days_per_week}",
                        article="Art 6(1)",
                        minute=minute,
                        excess_minutes=seg - regs.daily_driving,
                    )
                )
        seg, seg_prior = 0, False

    for a in acts:
        while i < len(bounds) and bounds[i][0] <= a.start:
            close(bounds[i][0])
            i += 1
        if a.kind == "DRIVE":
            seg_end = a.end
            if seg <= regs.daily_driving < seg + a.minutes:
                ext_minute = a.start + (regs.daily_driving - seg)
            seg_prior = seg_prior or (a.label.block_index is None and a.start < 0)
            seg += a.minutes
    while i < len(bounds):
        close(bounds[i][0])
        i += 1
    open_today = seg
    if seg:
        close(seg_end)
    if ext_week != weeks.floor(acts[-1].end if acts else 0):
        extended = 0
    return out, extended, open_today


def _check_weekly(
    acts: list[Activity], regs: Regs, counters: StartCounters, weeks: Weeks
) -> tuple[list[Finding], int, int]:
    """Art 6(2) and 6(3): calendar-week totals and any two consecutive weeks."""
    first = weeks.floor(0)
    totals = {
        first: counters.driving_this_week_prior,
        weeks.shift(first, -1): counters.driving_last_week,
    }
    crossed: dict[int, int] = {}  # week -> minute the 56 h limit was passed
    last_end: dict[int, int] = {}
    for a in acts:
        if a.kind != "DRIVE":
            continue
        if a.label.block_index is None and a.start < 0:
            # the start state's weekly total already counts today's driving, wherever it lies
            totals[first] += a.minutes
            last_end.setdefault(first, a.end)
            continue
        t, w = a.start, weeks.floor(a.start)
        while t < a.end:
            nxt = weeks.shift(w, 1)
            end = min(a.end, nxt)
            totals[w] = totals.get(w, 0) + (end - t)
            last_end[w] = end
            if totals[w] > regs.weekly_driving:
                crossed.setdefault(w, end)
            t, w = end, nxt
    out = [
        Finding(
            "VIOLATION",
            "WEEKLY_DRIVING_EXCEEDED",
            f"{totals[w]} min of driving in one calendar week",
            article="Art 6(2)",
            minute=crossed[w],
            excess_minutes=totals[w] - regs.weekly_driving,
        )
        for w in sorted(crossed)
    ]
    for w in sorted(last_end):
        both = totals[w] + totals.get(weeks.shift(w, -1), 0)
        if both > regs.fortnight_driving:
            out.append(
                Finding(
                    "VIOLATION",
                    "FORTNIGHT_DRIVING_EXCEEDED",
                    f"{both} min in two consecutive weeks",
                    article="Art 6(3)",
                    minute=last_end[w],
                    excess_minutes=both - regs.fortnight_driving,
                )
            )
    end = weeks.floor(acts[-1].end if acts else 0)
    return out, totals.get(end, 0), totals.get(weeks.shift(end, -1), 0)

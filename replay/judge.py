"""Judge a replayed log: the start-state prehistory plus the plan's activities."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import tzinfo

from replay.judge_driving import DrivingEnd, walk_driving
from replay.judge_rest import RestEnd, recognise_rests, walk_rests
from replay.log import ActivityLog
from replay.regs import Regs
from replay.start import StartCounters
from replay.types import Activity, Finding, Switches
from replay.weeks import Weeks


def judge_walk(
    log: ActivityLog,
    prehist: list[Activity],
    counters: StartCounters,
    regs: Regs,
    sw: Switches,
    week_tz: tzinfo,
    ferries: Mapping[str, tuple[int, bool]] | None = None,
) -> tuple[list[Finding], DrivingEnd, RestEnd]:
    acts = sorted([*prehist, *log.activities], key=lambda a: (a.start, a.end))
    weeks = Weeks(log.origin, week_tz)
    initial = None if prehist else counters.daily_rest_end
    rests = recognise_rests(acts, regs, sw, initial)
    f1, dend = walk_driving(acts, regs, rests, counters, weeks)
    f2, rend = walk_rests(acts, rests, regs, sw, counters, weeks, ferries)
    return [*f1, *f2], dend, rend


def judge(
    log: ActivityLog,
    prehist: list[Activity],
    counters: StartCounters,
    regs: Regs,
    sw: Switches,
    week_tz: tzinfo,
) -> list[Finding]:
    return judge_walk(log, prehist, counters, regs, sw, week_tz)[0]

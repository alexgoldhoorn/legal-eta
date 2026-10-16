"""Judge a replayed log: the start-state prehistory plus the plan's activities."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from replay.judge_driving import check_driving
from replay.judge_rest import check_rests, recognise_rests
from replay.log import ActivityLog
from replay.regs import Regs
from replay.start import StartCounters
from replay.types import Activity, Finding, Switches
from replay.weeks import Weeks


def judge(
    log: ActivityLog,
    prehist: list[Activity],
    counters: StartCounters,
    regs: Regs,
    sw: Switches,
    week_tz: ZoneInfo,
) -> list[Finding]:
    acts = sorted([*prehist, *log.activities], key=lambda a: (a.start, a.end))
    weeks = Weeks(log.origin, week_tz)
    initial = None if prehist else counters.daily_rest_end
    rests = recognise_rests(acts, regs, sw, initial)
    return [
        *check_driving(acts, regs, rests, counters, weeks),
        *check_rests(acts, rests, regs, sw, counters, weeks),
    ]

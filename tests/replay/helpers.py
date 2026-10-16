from datetime import datetime
from zoneinfo import ZoneInfo

from replay.start import StartCounters
from replay.types import Activity, Label, RestKind, RestPeriod
from replay.weeks import Weeks

TZ = ZoneInfo("Europe/Madrid")
MONDAY = datetime(2026, 10, 19, 0, 0, tzinfo=TZ)  # a Monday, before the October DST change
TUESDAY = datetime(2026, 10, 20, 0, 0, tzinfo=TZ)


def act(kind, start: int, end: int, block_kind: str = "test", **label) -> Activity:  # type: ignore[no-untyped-def]
    return Activity(kind, start, end, Label(None, block_kind, **label))


def rest(start: int, end: int, kind: RestKind, **kw) -> RestPeriod:  # type: ignore[no-untyped-def]
    return RestPeriod(start, end, kind, **kw)


def weeks(origin: datetime = MONDAY) -> Weeks:
    return Weeks(origin, TZ)


def counters(**kw) -> StartCounters:  # type: ignore[no-untyped-def]
    return StartCounters(**kw)


def day(t0: int, drive: int, rest_minutes: int = 660, kind: RestKind = "daily_regular"):  # type: ignore[no-untyped-def]
    """One working day: `drive` minutes in 270-minute stretches with 45-minute breaks, then a rest.

    Returns (activities, [rest period], end minute).
    """
    acts, t, left = [], t0, drive
    while left > 0:
        d = min(270, left)
        acts.append(act("DRIVE", t, t + d))
        t, left = t + d, left - d
        if left > 0:
            acts.append(act("REST", t, t + 45))
            t += 45
    acts.append(act("REST", t, t + rest_minutes))
    return acts, [rest(t, t + rest_minutes, kind)], t + rest_minutes

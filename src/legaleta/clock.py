"""Conversion between plan minutes (t, integer, t=0 at start) and wall-clock time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


def parse_hhmm(text: str) -> int:
    """'18:30' -> 1110 (minute of day)."""
    parts = text.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"expected HH:MM, got {text!r}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"expected HH:MM in 00:00..23:59, got {text!r}")
    return h * 60 + m


@dataclass(frozen=True)
class Clock:
    start: datetime
    week_tz: ZoneInfo

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.start.utcoffset() is None:
            raise ValueError("start must be timezone-aware")

    def at(self, t: int) -> datetime:
        # Absolute-time arithmetic: same-tzinfo datetime maths is wall-clock and ignores DST.
        utc = self.start.astimezone(UTC) + timedelta(minutes=t)
        return utc.astimezone(self.start.tzinfo)

    def to_t(self, dt: datetime) -> int:
        return round((dt.astimezone(UTC) - self.start.astimezone(UTC)).total_seconds() / 60)

    def local(self, t: int) -> datetime:
        return self.at(t).astimezone(self.week_tz)

    def minute_of_day(self, t: int) -> int:
        loc = self.local(t)
        return loc.hour * 60 + loc.minute

    def next_week_start(self, t: int) -> int:
        """First Monday 00:00 (week_tz) strictly after t."""
        loc = self.local(t)
        days_ahead = (7 - loc.weekday()) % 7
        candidate = (loc + timedelta(days=days_ahead)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        # replace() keeps tzinfo; re-normalise through the zone so DST folds resolve
        candidate = candidate.replace(tzinfo=None).replace(tzinfo=self.week_tz)
        if self.to_t(candidate) <= t:
            candidate = (
                (candidate + timedelta(days=7)).replace(tzinfo=None).replace(tzinfo=self.week_tz)
            )
        return self.to_t(candidate)

    def week_start_after(self, t: int, weeks: int) -> int:
        """Monday 00:00 that starts the week `weeks` weeks after the week containing t."""
        if weeks < 1:
            raise ValueError("weeks must be >= 1")
        mon = self.local(self.next_week_start(t))
        target = (
            (mon + timedelta(days=7 * (weeks - 1)))
            .replace(tzinfo=None)
            .replace(tzinfo=self.week_tz)
        )
        return self.to_t(target)

    def next_local_minute(self, t: int, minute_of_day: int) -> int:
        """First t' >= t whose local minute-of-day equals minute_of_day."""
        loc = self.local(t)
        day = loc.replace(hour=0, minute=0, second=0, microsecond=0)
        for d in range(0, 3):
            cand = (day + timedelta(days=d)).replace(tzinfo=None) + timedelta(minutes=minute_of_day)
            cand = cand.replace(tzinfo=self.week_tz)
            tc = self.to_t(cand)
            if tc >= t:
                return tc
        raise AssertionError("unreachable: a matching local minute exists within 3 days")

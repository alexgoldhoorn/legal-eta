"""Calendar weeks (Monday 00:00 to Sunday 24:00) in a local zone, over elapsed log minutes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo


class Weeks:
    def __init__(self, origin: datetime, tz: tzinfo) -> None:
        # UTC on purpose: subtracting two datetimes that share one ZoneInfo is wall-clock arithmetic
        self.origin = origin.astimezone(UTC)
        self.tz = tz

    def _minute(self, dt: datetime) -> int:
        return round((dt - self.origin).total_seconds() / 60)

    def local(self, minute: int) -> datetime:
        return (self.origin + timedelta(minutes=minute)).astimezone(self.tz)

    def floor(self, minute: int) -> int:
        """Minute of the Monday 00:00 (local) that starts the week containing `minute`."""
        d = self.local(minute).date()
        d -= timedelta(days=d.weekday())
        return self._minute(datetime(d.year, d.month, d.day, tzinfo=self.tz))

    def shift(self, week_start: int, n: int) -> int:
        """Minute of the Monday 00:00 (local) `n` weeks after the week starting at `week_start`."""
        d = self.local(week_start).date() + timedelta(weeks=n)
        return self._minute(datetime(d.year, d.month, d.day, tzinfo=self.tz))

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from legaleta.clock import Clock, parse_hhmm


def test_at_and_to_t_round_trip(monday_0600: datetime, madrid: ZoneInfo) -> None:
    c = Clock(monday_0600, madrid)
    assert c.at(0) == monday_0600
    assert c.to_t(c.at(1234)) == 1234
    assert c.minute_of_day(0) == 360


def test_next_week_start_from_monday_is_next_monday(
    monday_0600: datetime, madrid: ZoneInfo
) -> None:
    c = Clock(monday_0600, madrid)
    t = c.next_week_start(0)
    assert c.local(t).strftime("%a %H:%M") == "Mon 00:00"
    assert c.local(t).date().isoformat() == "2026-10-26"
    # exactly on a Monday 00:00 the *next* Monday is returned (strictly after)
    assert c.next_week_start(t) > t
    assert c.local(c.next_week_start(t)).date().isoformat() == "2026-11-02"


def test_next_week_start_across_dst(madrid: ZoneInfo) -> None:
    # Spain leaves DST on Sun 2026-10-25 03:00 -> 02:00; that week has 60 extra minutes.
    start = datetime(2026, 10, 19, 6, 0, tzinfo=madrid)
    c = Clock(start, madrid)
    t_mon = c.next_week_start(0)
    assert t_mon == (24 - 6) * 60 + 6 * 1440 + 60  # Mon 06:00 -> Mon 00:00 next week, +1 h for DST
    assert c.week_start_after(0, 3) == t_mon + 2 * 7 * 1440


def test_next_local_minute(monday_0600: datetime, madrid: ZoneInfo) -> None:
    c = Clock(monday_0600, madrid)
    assert c.next_local_minute(0, parse_hhmm("06:00")) == 0
    assert c.next_local_minute(1, parse_hhmm("06:00")) == 1440
    assert c.next_local_minute(0, parse_hhmm("18:30")) == 12 * 60 + 30


def test_parse_hhmm_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_hhmm("25:00")
    with pytest.raises(ValueError):
        parse_hhmm("6")


def test_naive_start_rejected(madrid: ZoneInfo) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Clock(datetime(2026, 10, 19, 6, 0), madrid)

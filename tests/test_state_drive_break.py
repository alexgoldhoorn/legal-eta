from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from legaleta.clock import Clock
from legaleta.rules import PRESETS
from legaleta.state import DriverState, apply, envelope, replay
from legaleta.timeline import Block

R = PRESETS["eu561"]


@pytest.fixture
def clock(monday_0600: datetime, madrid: ZoneInfo) -> Clock:
    return Clock(monday_0600, madrid)


def test_fresh_envelope() -> None:
    e = envelope(DriverState(), R, now=0)
    assert e.drive_before_break == 270
    assert e.drive_today == 600 and e.drive_today_unextended == 540
    assert e.drive_this_week == 3360 and e.drive_fortnight == 5400
    assert e.daily_window_ends_in == 1440 and e.weekly_rest_due_in == 8640
    assert e.extension_available is True
    assert e.allowed_daily_rests == ("regular", "reduced", "split_part_1")
    assert e.allowed_weekly_rests == ("regular", "reduced")
    assert e.compensation_due_in is None


def test_v1_plain_break_resets_counter(clock: Clock) -> None:
    # ref V1: drive 4h30, break 45, drive 4h30 -> 9 h driven, daily max reached
    s = replay(
        DriverState(),
        [Block("drive", 0, 270), Block("break", 270, 45), Block("drive", 315, 270)],
        R,
        clock,
    )
    assert s.driving_since_break == 270 and s.driving_today == 540
    e = envelope(s, R, 585)
    assert e.drive_before_break == 0 and e.drive_today_unextended == 0 and e.drive_today == 60


def test_v2_split_break_15_then_30(clock: Clock) -> None:
    s = replay(
        DriverState(),
        [
            Block("drive", 0, 120),
            Block("break", 120, 15),
            Block("drive", 135, 150),
            Block("break", 285, 30),
        ],
        R,
        clock,
    )
    assert s.driving_since_break == 0 and s.split_break_first_part_taken is False


def test_v3_lone_30_does_not_reset(clock: Clock) -> None:
    s = replay(DriverState(), [Block("drive", 0, 270), Block("break", 270, 30)], R, clock)
    assert s.driving_since_break == 270
    assert s.split_break_first_part_taken is True  # a lone 30 is recorded as a first part


def test_30_then_15_does_not_reset(clock: Clock) -> None:
    s = replay(
        DriverState(),
        [
            Block("drive", 0, 100),
            Block("break", 100, 30),
            Block("drive", 130, 100),
            Block("break", 230, 15),
        ],
        R,
        clock,
    )
    assert s.driving_since_break == 200 and s.split_break_first_part_taken is True


def test_wait_counts_only_when_flagged(clock: Clock) -> None:
    s = replay(
        DriverState(),
        [Block("drive", 0, 100), Block("wait", 100, 60, counts_as_rest=False)],
        R,
        clock,
    )
    assert s.driving_since_break == 100
    s = replay(
        DriverState(),
        [Block("drive", 0, 100), Block("wait", 100, 60, counts_as_rest=True)],
        R,
        clock,
    )
    assert s.driving_since_break == 0
    s = replay(DriverState(), [Block("drive", 0, 100), Block("ferry", 100, 60)], R, clock)
    assert s.driving_since_break == 0  # a ferry crossing is always free time
    s = replay(DriverState(), [Block("drive", 0, 100), Block("work", 100, 60)], R, clock)
    assert s.driving_since_break == 100


def test_extension_consumed_when_crossing_540(clock: Clock) -> None:
    s = replay(
        DriverState(),
        [Block("drive", 0, 270), Block("break", 270, 45), Block("drive", 315, 300)],
        R,
        clock,
    )
    assert s.driving_today == 570 and s.extended_days_used_this_week == 1
    e = envelope(s, R, 615)
    assert e.extension_available is True and e.drive_today == 30
    assert e.drive_today_unextended == -30
    s2 = DriverState(extended_days_used_this_week=2)
    assert envelope(s2, R, 0).extension_available is False
    assert envelope(s2, R, 0).drive_today == 540


def test_week_rollover_moves_counters(clock: Clock) -> None:
    # start Mon 06:00 CEST; DST ended Sun 2026-10-25 03:00, so Sun 22:00 CET is
    # t = 6*1440 + 16*60 + 60 = 9660. A 3 h drive from there crosses Monday 00:00 after 120 min.
    t_sun_2200 = 6 * 1440 + 16 * 60 + 60
    assert clock.local(t_sun_2200).strftime("%a %H:%M") == "Sun 22:00"
    s = DriverState(driving_this_week=3000, extended_days_used_this_week=2)
    s = apply(s, Block("drive", t_sun_2200, 180), R, clock)
    assert s.driving_last_week == 3000 + 120
    assert s.driving_this_week == 60
    assert s.extended_days_used_this_week == 0
    assert s.driving_today == 180


def test_week_rollover_on_non_drive_block(clock: Clock) -> None:
    t_sun_2200 = 6 * 1440 + 16 * 60 + 60  # CET after the DST change, see test above
    s = DriverState(driving_this_week=3000)
    s = apply(s, Block("work", t_sun_2200, 240), R, clock)
    assert (s.driving_last_week, s.driving_this_week) == (3000, 0)

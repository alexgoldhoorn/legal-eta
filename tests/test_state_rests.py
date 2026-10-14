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


def test_regular_daily_rest_resets_and_window_starts_at_end(clock: Clock) -> None:
    s = replay(
        DriverState(),
        [
            Block("drive", 0, 270),
            Block("break", 270, 30),
            Block("daily_rest", 300, 700, subtype="regular"),
        ],
        R,
        clock,
    )
    assert (
        s.driving_today == 0
        and s.driving_since_break == 0
        and s.split_break_first_part_taken is False
    )
    assert s.last_daily_rest_end == 1000
    assert s.reduced_daily_rests_used == 0
    assert envelope(s, R, 1000).daily_window_ends_in == 1440


def test_reduced_daily_rest_counts_and_v5_fourth_not_allowed(clock: Clock) -> None:
    s = DriverState()
    t = 0
    for minutes in (540, 600, 570):
        s = apply(s, Block("daily_rest", t, minutes, subtype="reduced"), R, clock)
        t += minutes
    assert s.reduced_daily_rests_used == 3
    assert "reduced" not in envelope(s, R, t).allowed_daily_rests
    s = apply(s, Block("daily_rest", t, 540, subtype="reduced"), R, clock)
    assert s.reduced_daily_rests_used == 4  # apply records what happened; the checker flags it


def test_split_daily_rest_3_then_9_is_regular(clock: Clock) -> None:
    s = replay(
        DriverState(),
        [Block("drive", 0, 100), Block("daily_rest", 100, 180, subtype="split_part_1")],
        R,
        clock,
    )
    assert (
        s.split_daily_rest_first_part_taken is True
        and s.driving_today == 100
        and s.driving_since_break == 0
    )
    assert envelope(s, R, 280).allowed_daily_rests == ("regular", "reduced", "split_part_2")
    s = replay(
        s,
        [Block("drive", 280, 60), Block("daily_rest", 340, 540, subtype="split_part_2")],
        R,
        clock,
    )
    assert (
        s.driving_today == 0
        and s.reduced_daily_rests_used == 0
        and s.split_daily_rest_first_part_taken is False
    )
    assert s.last_daily_rest_end == 880


def test_short_rest_is_only_a_break(clock: Clock) -> None:
    s = replay(
        DriverState(),
        [Block("drive", 0, 200), Block("daily_rest", 200, 400, subtype="reduced")],
        R,
        clock,
    )
    assert s.driving_today == 200 and s.driving_since_break == 0 and s.last_daily_rest_end == 0


def test_weekly_rest_regular_resets_everything(clock: Clock) -> None:
    s = DriverState(reduced_daily_rests_used=2, driving_today=300, last_weekly_rest_reduced=True)
    s = apply(s, Block("weekly_rest", 1000, 2700, subtype="regular"), R, clock)
    assert s.reduced_daily_rests_used == 0 and s.driving_today == 0
    assert s.last_weekly_rest_end == 3700 and s.last_daily_rest_end == 3700
    assert s.last_weekly_rest_reduced is False and s.compensation_owed_minutes == 0


def test_v7_reduced_weekly_rest_books_compensation(clock: Clock) -> None:
    # 30 h reduced weekly rest on Tue of week W (start Mon 2026-10-19):
    # 15 h owed by the end of Sun 2026-11-15
    t = 1440 + 600
    s = apply(DriverState(), Block("weekly_rest", t, 1800, subtype="reduced"), R, clock)
    assert s.last_weekly_rest_reduced is True
    assert s.compensation_owed_minutes == 900
    assert s.compensation_deadline is not None
    assert clock.local(s.compensation_deadline).strftime("%Y-%m-%d %H:%M") == "2026-11-16 00:00"
    assert "reduced" not in envelope(s, R, t + 1800).allowed_weekly_rests


def test_compensation_paid_en_bloc_on_a_9h_rest(clock: Clock) -> None:
    s = DriverState(compensation_owed_minutes=900, compensation_deadline=50000)
    s = apply(
        s, Block("daily_rest", 0, 1440, subtype="regular", compensation_minutes=900), R, clock
    )
    assert s.compensation_owed_minutes == 0 and s.compensation_deadline is None
    s = DriverState(compensation_owed_minutes=900, compensation_deadline=50000)
    s = apply(
        s, Block("daily_rest", 0, 1200, subtype="regular", compensation_minutes=480), R, clock
    )
    assert s.compensation_owed_minutes == 900  # partial is not en bloc: nothing paid


def test_v9_ferry_interrupted_rest_counts_rest_minutes(clock: Clock) -> None:
    b = Block("daily_rest", 0, 660 + 55, subtype="regular", at="ferry", interruptions=(25, 30))
    s = apply(DriverState(driving_today=400), b, R, clock)
    assert s.driving_today == 0 and s.last_daily_rest_end == 715 and s.reduced_daily_rests_used == 0


def test_weekly_rest_across_monday_counts_in_new_week(clock: Clock) -> None:
    t_sun_2200 = 6 * 1440 + 16 * 60 + 60  # Sun 22:00 CET (DST ended that morning)
    s = DriverState(driving_this_week=3000)
    s = apply(s, Block("weekly_rest", t_sun_2200, 2700, subtype="regular"), R, clock)
    assert (s.driving_last_week, s.driving_this_week) == (3000, 0)


def test_short_split_part_1_is_only_a_break(clock: Clock) -> None:
    s = replay(
        DriverState(),
        [Block("drive", 0, 200), Block("daily_rest", 200, 20, subtype="split_part_1")],
        R,
        clock,
    )
    assert s.split_daily_rest_first_part_taken is False and s.split_break_first_part_taken
    assert s.driving_today == 200 and s.last_daily_rest_end == 0


def test_second_reduced_weekly_rest_keeps_the_earlier_deadline(clock: Clock) -> None:
    s = DriverState(compensation_owed_minutes=600, compensation_deadline=100)
    s = apply(s, Block("weekly_rest", 5000, 1800, subtype="reduced"), R, clock)
    assert s.compensation_owed_minutes == 600 + 900
    assert s.compensation_deadline == 100


def test_weekly_block_below_reduced_minimum_acts_as_a_daily_rest_only(clock: Clock) -> None:
    s = apply(
        DriverState(driving_today=100),
        Block("weekly_rest", 0, 1000, subtype="reduced"),
        R,
        clock,
    )
    assert s.last_weekly_rest_end == 0 and s.compensation_owed_minutes == 0
    assert s.driving_today == 0 and s.last_daily_rest_end == 1000


def test_v9_interruptions_are_subtracted_before_classifying_the_rest(clock: Clock) -> None:
    # 700 min block, 55 min interrupted: 645 rest minutes is reduced (< 660), not regular
    b = Block("daily_rest", 0, 700, subtype="regular", at="ferry", interruptions=(25, 30))
    s = apply(DriverState(driving_today=400), b, R, clock)
    assert s.reduced_daily_rests_used == 1 and s.last_daily_rest_end == 700


def test_regular_rest_with_only_nine_hours_inside_the_window_is_reduced(clock: Clock) -> None:
    # Art 8(2): 9 h to under 11 h inside the 24 h window makes the rest a reduced one
    s = apply(DriverState(), Block("daily_rest", 900, 660, subtype="regular"), R, clock)
    assert s.reduced_daily_rests_used == 1 and s.last_daily_rest_end == 1560


def test_regular_rest_with_eleven_hours_inside_the_window_is_regular(clock: Clock) -> None:
    s = apply(DriverState(), Block("daily_rest", 700, 660, subtype="regular"), R, clock)
    assert s.reduced_daily_rests_used == 0 and s.last_daily_rest_end == 1360

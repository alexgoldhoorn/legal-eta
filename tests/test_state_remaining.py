from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from legaleta.clock import Clock
from legaleta.rules import PRESETS
from legaleta.state import DriverState, from_remaining, project, to_remaining

R = PRESETS["eu561"]

_BASE = {
    "remaining_drive_before_break": 270,
    "remaining_drive_today": 540,
    "remaining_drive_this_week": 3000,
    "remaining_drive_fortnight": 5000,
    "daily_rest_due_in": 600,
    "weekly_rest_due_in": 5000,
}


@pytest.fixture
def clock(monday_0600: datetime, madrid: ZoneInfo) -> Clock:
    return Clock(monday_0600, madrid)


def test_round_trip_fresh_and_used() -> None:
    for s in (
        DriverState(),
        DriverState(
            driving_since_break=100,
            driving_today=400,
            last_daily_rest_end=-300,
            driving_this_week=1000,
            driving_last_week=2000,
            last_weekly_rest_end=-5000,
            reduced_daily_rests_used=1,
            compensation_owed_minutes=600,
            compensation_deadline=9000,
        ),
    ):
        back, notes = from_remaining(to_remaining(s, R, now=0), R, now=0)
        assert back == s, notes


def test_round_trip_extended_day() -> None:
    s = DriverState(driving_today=570, extended_days_used_this_week=1)
    rem = to_remaining(s, R, now=0)
    assert rem["remaining_drive_today"] == -30
    back, notes = from_remaining(rem, R, now=0)
    assert back == s
    assert any("extended" in n for n in notes)


def test_extended_day_assumed_from_negative_remaining() -> None:
    rem = {
        "remaining_drive_before_break": 270,
        "remaining_drive_today": 30,
        "remaining_drive_this_week": 3000,
        "remaining_drive_fortnight": 5000,
        "daily_rest_due_in": 600,
        "weekly_rest_due_in": 5000,
        "extended_days_used_this_week": 1,
    }
    s, notes = from_remaining(rem, R, now=0)
    assert s.driving_today == 510  # assumed 9 h cap
    assert any("9 h cap" in n for n in notes)


def test_missing_key_rejected() -> None:
    with pytest.raises(ValueError, match="remaining_drive_today"):
        from_remaining({"remaining_drive_before_break": 1}, R)


def test_non_integer_and_non_boolean_rejected() -> None:
    with pytest.raises(ValueError, match="remaining_drive_today must be an integer"):
        from_remaining({**_BASE, "remaining_drive_today": "540"}, R)
    with pytest.raises(ValueError, match="remaining_drive_today must be an integer"):
        from_remaining({**_BASE, "remaining_drive_today": True}, R)
    with pytest.raises(ValueError, match="last_weekly_rest_reduced must be a boolean"):
        from_remaining({**_BASE, "last_weekly_rest_reduced": 1}, R)


def test_compensation_without_deadline_noted_and_deadline_read() -> None:
    s, notes = from_remaining({**_BASE, "compensation_owed_minutes": 300}, R, now=100)
    assert s.compensation_owed_minutes == 300 and s.compensation_deadline is None
    assert any("without a deadline" in n for n in notes)
    s, notes = from_remaining(
        {**_BASE, "compensation_owed_minutes": 300, "compensation_deadline_in": 4000}, R, now=100
    )
    assert s.compensation_deadline == 4100
    assert not any("without a deadline" in n for n in notes)


def test_to_remaining_reports_deadline_only_when_set() -> None:
    assert "compensation_deadline_in" not in to_remaining(DriverState(), R, now=0)
    s = DriverState(compensation_owed_minutes=100, compensation_deadline=500)
    assert to_remaining(s, R, now=200)["compensation_deadline_in"] == 300


def test_project_driving_reports_first_limit(clock: Clock) -> None:
    r = project(
        DriverState(driving_since_break=200), minutes=120, activity="driving", rules=R, clock=clock
    )
    assert r.first_limit == (70, "break")
    assert r.state.driving_since_break == 320
    assert r.envelope.drive_before_break == -50


def test_project_driving_within_limits_has_no_first_limit(clock: Clock) -> None:
    r = project(DriverState(), minutes=60, activity="driving", rules=R, clock=clock)
    assert r.first_limit is None


def test_project_resting_becomes_daily_rest(clock: Clock) -> None:
    r = project(
        DriverState(driving_today=500, driving_since_break=200),
        minutes=700,
        activity="resting",
        rules=R,
        clock=clock,
    )
    assert r.state.driving_today == 0 and r.first_limit is None


def test_project_long_rest_is_weekly_rest(clock: Clock) -> None:
    s = DriverState(driving_this_week=2000)
    regular = project(s, minutes=R.weekly_rest_regular, activity="resting", rules=R, clock=clock)
    assert regular.state.last_weekly_rest_end == R.weekly_rest_regular
    assert not regular.state.last_weekly_rest_reduced
    reduced = project(s, minutes=R.weekly_rest_reduced, activity="resting", rules=R, clock=clock)
    assert reduced.state.last_weekly_rest_reduced
    assert reduced.state.compensation_owed_minutes == R.weekly_rest_regular - R.weekly_rest_reduced


def test_project_short_rest_is_reduced_daily_rest(clock: Clock) -> None:
    r = project(
        DriverState(), minutes=R.daily_rest_reduced, activity="resting", rules=R, clock=clock
    )
    assert r.state.reduced_daily_rests_used == 1


def test_project_other_work_hits_daily_window(clock: Clock) -> None:
    r = project(
        DriverState(last_daily_rest_end=-1000),
        minutes=600,
        activity="other_work",
        rules=R,
        clock=clock,
    )
    assert r.first_limit == (440, "daily_rest_window")  # the window ends at -1000 + 1440


def test_project_rejects_unknown_activity(clock: Clock) -> None:
    with pytest.raises(ValueError):
        project(DriverState(), 10, "sleeping", R, clock)


def test_project_rejects_negative_minutes(clock: Clock) -> None:
    with pytest.raises(ValueError, match="minutes"):
        project(DriverState(), -1, "driving", R, clock)

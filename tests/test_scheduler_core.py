from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from legaleta.clock import Clock
from legaleta.policy import Policy
from legaleta.rules import PRESETS
from legaleta.scheduler import plan
from legaleta.state import DriverState
from legaleta.timeline import Drive, Ferry, Stop
from tests.conftest import assert_legal

R = PRESETS["eu561"]
CAREFUL = Policy(
    extended_driving="never",
    daily_rest_type="regular",
    weekly_rest_type="regular",
    split_daily_rest="never",
)
EAGER = Policy(extended_driving="eager", daily_rest_type="reduced_eager")


@pytest.fixture
def clock(monday_0600: datetime, madrid: ZoneInfo) -> Clock:
    return Clock(monday_0600, madrid)


def kinds(result) -> list[tuple[str, int]]:  # type: ignore[no-untyped-def]
    return [(b.kind, b.minutes) for b in result.blocks]


def test_short_drive_is_one_block(clock: Clock) -> None:
    res = plan([Drive(100)], DriverState(), R, CAREFUL, clock)
    assert kinds(res) == [("drive", 100)] and res.arrival == 100
    assert_legal(res, DriverState(), R, clock)


def test_break_inserted_after_270(clock: Clock) -> None:
    res = plan([Drive(400)], DriverState(), R, CAREFUL, clock)
    assert kinds(res) == [("drive", 270), ("break", 45), ("drive", 130)]
    assert res.blocks[1].article.startswith("Art 7") and res.blocks[1].at is None
    assert_legal(res, DriverState(), R, clock)


def test_split_break_completed_with_30(clock: Clock) -> None:
    s = DriverState(driving_since_break=270, split_break_first_part_taken=True)
    res = plan([Drive(60)], s, R, CAREFUL, clock)
    assert kinds(res) == [("break", 30), ("drive", 60)]
    assert_legal(res, s, R, clock)


def test_daily_rest_when_daily_driving_exhausted(clock: Clock) -> None:
    res = plan([Drive(600)], DriverState(), R, CAREFUL, clock)
    assert kinds(res) == [
        ("drive", 270),
        ("break", 45),
        ("drive", 270),
        ("daily_rest", 660),
        ("drive", 60),
    ]
    assert res.blocks[3].subtype == "regular"
    assert res.arrival == 1305
    assert_legal(res, DriverState(), R, clock)


def test_eager_extension_and_reduced_rest(clock: Clock) -> None:
    res = plan([Drive(600)], DriverState(), R, EAGER, clock)
    assert kinds(res) == [
        ("drive", 270),
        ("break", 45),
        ("drive", 270),
        ("break", 45),
        ("drive", 60),
    ]
    assert res.blocks[-1].subtype == "extended"
    assert res.arrival == 690
    res2 = plan([Drive(700)], DriverState(), R, EAGER, clock)
    rests = [b for b in res2.blocks if b.kind == "daily_rest"]
    assert len(rests) == 1 and rests[0].subtype == "reduced" and rests[0].minutes == 540
    assert_legal(res2, DriverState(), R, clock)


def test_window_wait_counts_as_break_and_warns(clock: Clock) -> None:
    route = [Drive(200), Stop("A", service_minutes=20, driver_works=True, earliest=300), Drive(200)]
    res = plan(route, DriverState(), R, CAREFUL, clock)
    assert kinds(res) == [("drive", 200), ("wait", 100), ("work", 20), ("drive", 200)]
    assert res.blocks[1].counts_as_rest is True and res.blocks[1].at == "A"
    assert "WAIT_ASSUMED_REST" in {w.code for w in res.warnings}
    assert (
        res.stops[0].arrival == 200
        and res.stops[0].departure == 320
        and res.stops[0].window_met is True
    )
    assert_legal(res, DriverState(), R, clock)


def test_idle_service_over_45_is_a_break_block(clock: Clock) -> None:
    route = [Drive(250), Stop("A", service_minutes=60, driver_works=False), Drive(250)]
    res = plan(route, DriverState(), R, CAREFUL, clock)
    assert kinds(res) == [("drive", 250), ("break", 60), ("drive", 250)]
    assert res.blocks[1].at == "A" and res.blocks[1].counts_as_rest is True
    assert_legal(res, DriverState(), R, clock)


def test_lateness_reported_never_blocks(clock: Clock) -> None:
    route = [Drive(300), Stop("A", service_minutes=10, latest=250)]
    res = plan(route, DriverState(), R, CAREFUL, clock)
    assert res.stops[0].lateness_minutes == 95  # 270 drive + 45 break + 30 drive = 345 arrival
    assert res.stops[0].window_met is False
    assert_legal(res, DriverState(), R, clock)


def test_ferry_counts_as_break(clock: Clock) -> None:
    route = [
        Drive(270),
        Ferry("f1", minutes=120, sleeper_cabin=False, boarding_minutes=15),
        Drive(100),
    ]
    res = plan(route, DriverState(), R, CAREFUL, clock)
    assert kinds(res) == [("drive", 270), ("work", 15), ("ferry", 120), ("drive", 100)]
    assert_legal(res, DriverState(), R, clock)


def test_rest_on_ferry_when_only_a_rest_unblocks(clock: Clock) -> None:
    s = DriverState(driving_today=540, driving_since_break=200)
    route = [Ferry("f1", minutes=540, sleeper_cabin=True, boarding_minutes=20), Drive(100)]
    res = plan(route, s, R, CAREFUL, clock)
    # a crossing of 9 h cannot host the 11 h regular rest without extending:
    # expect the interrupted form
    rest = [b for b in res.blocks if b.kind == "daily_rest"]
    assert (
        rest
        and rest[0].at == "f1"
        and rest[0].rest_minutes == 660
        and rest[0].interruptions == (20,)
    )
    assert_legal(res, s, R, clock)


def test_plan_very_long_drive_terminates_and_is_legal(clock: Clock) -> None:
    res = plan([Drive(4000)], DriverState(), R, EAGER, clock)
    assert any(b.kind == "weekly_rest" for b in res.blocks)
    assert_legal(res, DriverState(), R, clock)


def test_plan_opens_with_rest_when_window_already_passed(clock: Clock) -> None:
    s = DriverState(last_daily_rest_end=-1560, driving_today=300)
    res = plan([Drive(60)], s, R, CAREFUL, clock)
    assert res.blocks[0].kind == "daily_rest"
    assert_legal(res, s, R, clock)


def test_zero_length_items_emit_no_empty_blocks(clock: Clock) -> None:
    route = [
        Drive(0),
        Stop("A", service_minutes=0, earliest=0),
        Drive(10),
        Stop("B", service_minutes=0),
    ]
    res = plan(route, DriverState(), R, CAREFUL, clock)
    assert kinds(res) == [("drive", 10)]
    assert [s.id for s in res.stops] == ["A", "B"]
    assert_legal(res, DriverState(), R, clock)


def test_standing_warnings_present(clock: Clock) -> None:
    res = plan([Drive(10)], DriverState(), R, CAREFUL, clock)
    codes = {w.code for w in res.warnings}
    assert {"ART12_NOT_MODELLED", "RETURN_HOME_NOT_CHECKED", "WTD_NOT_CHECKED"} <= codes


def test_compensation_paid_at_next_weekly_rest(clock: Clock) -> None:
    s = DriverState(
        compensation_owed_minutes=900, compensation_deadline=30000, last_weekly_rest_end=-8000
    )
    res = plan([Drive(1200)], s, R, EAGER, clock)
    weekly = [b for b in res.blocks if b.kind == "weekly_rest"]
    assert weekly and weekly[0].compensation_minutes == 900
    assert res.end_state.compensation_owed_minutes == 0
    assert_legal(res, s, R, clock)


def test_impossible_state_raises(clock: Clock) -> None:
    from legaleta.scheduler import SchedulingError

    s = DriverState(driving_last_week=5400, driving_this_week=0)
    # fortnight exhausted by last week alone: only the week rollover unblocks;
    # the fallback weekly rest covers it
    res = plan([Drive(10)], s, R, EAGER, clock)
    assert res.blocks[0].kind == "weekly_rest"
    assert_legal(res, s, R, clock)
    with pytest.raises(SchedulingError):
        plan(
            [Drive(10)],
            DriverState(driving_last_week=5400, driving_this_week=5400),
            PRESETS["eu561"],
            EAGER,
            clock,
        )


# Legality at stops and ferries: time there is not bounded by the drive limit.


def test_service_after_drive_to_the_rest_cap_rests_first(clock: Clock) -> None:
    # window ends at 840; a regular rest must start by 180; 3 reduced rests are used up
    s = DriverState(last_daily_rest_end=-600, reduced_daily_rests_used=3)
    route = [Drive(180), Stop("A", service_minutes=60), Drive(60)]
    res = plan(route, s, R, CAREFUL, clock)
    assert kinds(res) == [("drive", 180), ("daily_rest", 660), ("work", 60), ("drive", 60)]
    assert res.blocks[1].at == "A" and res.blocks[1].subtype == "regular"
    assert res.stops[0].arrival == 180 and res.stops[0].rested_here is True
    assert_legal(res, s, R, clock)


def test_service_at_the_weekly_deadline_takes_the_weekly_rest_first(clock: Clock) -> None:
    s = DriverState(last_weekly_rest_end=180 - R.weekly_rest_deadline)
    route = [Drive(180), Stop("A", service_minutes=10), Drive(60)]
    res = plan(route, s, R, CAREFUL, clock)
    assert kinds(res)[:2] == [("drive", 180), ("weekly_rest", 2700)]
    assert res.blocks[1].start == 180 and res.blocks[1].at == "A"
    assert_legal(res, s, R, clock)


def test_long_window_wait_at_the_last_stop_becomes_a_rest(clock: Clock) -> None:
    s = DriverState(last_daily_rest_end=-600)  # window ends at 840, inside the wait
    route = [Drive(100), Stop("A", service_minutes=10, earliest=1000)]
    res = plan(route, s, R, CAREFUL, clock)
    assert kinds(res) == [("drive", 100), ("daily_rest", 900), ("work", 10)]
    assert res.stops[0].arrival == 100 and res.stops[0].departure == 1010
    assert_legal(res, s, R, clock)


def test_rest_at_the_port_when_the_crossing_would_lapse_the_window(clock: Clock) -> None:
    s = DriverState(last_daily_rest_end=-600)
    route = [Drive(100), Ferry("f1", minutes=600), Drive(60)]
    res = plan(route, s, R, CAREFUL, clock)
    assert kinds(res) == [("drive", 100), ("daily_rest", 660), ("ferry", 600), ("drive", 60)]
    assert_legal(res, s, R, clock)


def test_crossing_too_long_for_any_rest_raises(clock: Clock) -> None:
    from legaleta.scheduler import SchedulingError

    with pytest.raises(SchedulingError, match="ferry"):
        plan([Drive(60), Ferry("f1", minutes=1000), Drive(60)], DriverState(), R, CAREFUL, clock)


def test_rest_after_boarding_when_boarding_exceeds_the_art9_limit(clock: Clock) -> None:
    # Art 9(1)'s 60 min binds only a rest interrupted by boarding; one that starts after it is fine
    route = [Drive(60), Ferry("f1", 1000, sleeper_cabin=True, boarding_minutes=90), Drive(60)]
    res = plan(route, DriverState(), R, CAREFUL, clock)
    assert kinds(res) == [("drive", 60), ("work", 90), ("daily_rest", 1000), ("drive", 60)]
    assert res.blocks[2].at == "f1" and res.blocks[2].interruptions == ()
    assert_legal(res, DriverState(), R, clock)


# A daily rest with 9 h to under 11 h inside its 24 h period counts as reduced (Art 8(2)):
# the label says so, the minutes are unchanged, and a regular-rest policy gets a warning.


def test_relaxed_pass_after_a_crossing_labels_the_rest_reduced(clock: Clock) -> None:
    res = plan([Drive(60), Ferry("f1", 781), Drive(60)], DriverState(), R, CAREFUL, clock)
    rest = [b for b in res.blocks if b.kind == "daily_rest"]
    assert [(b.subtype, b.minutes) for b in rest] == [("reduced", 660)]
    assert "599 min" in rest[0].reason and "Art 8(2)" in rest[0].article
    assert res.end_state.reduced_daily_rests_used == 1
    assert "DAILY_REST_REDUCED_BY_WINDOW" in {w.code for w in res.warnings}
    assert_legal(res, DriverState(), R, clock)


def test_rest_after_service_under_when_needed_is_labelled_reduced(clock: Clock) -> None:
    s = DriverState(last_daily_rest_end=-600)  # window ends at 840
    when_needed = Policy(daily_rest_type="reduced_when_needed")
    res = plan([Drive(180), Stop("A", service_minutes=60), Drive(60)], s, R, when_needed, clock)
    rest = [b for b in res.blocks if b.kind == "daily_rest"]
    assert [(b.subtype, b.minutes, b.at) for b in rest] == [("reduced", 660, "A")]
    assert res.end_state.reduced_daily_rests_used == 1
    assert "DAILY_REST_REDUCED_BY_WINDOW" in {w.code for w in res.warnings}
    assert_legal(res, s, R, clock)


def test_opening_rest_late_in_its_period_is_labelled_reduced(clock: Clock) -> None:
    s = DriverState(last_daily_rest_end=-840)  # only 600 min of the period left
    res = plan([Drive(60)], s, R, CAREFUL, clock)
    assert [(b.kind, b.subtype, b.minutes) for b in res.blocks] == [
        ("daily_rest", "reduced", 660),
        ("drive", None, 60),
    ]
    assert res.end_state.reduced_daily_rests_used == 1
    assert "DAILY_REST_REDUCED_BY_WINDOW" in {w.code for w in res.warnings}
    assert_legal(res, s, R, clock)


def test_reduced_rest_under_eager_policy_does_not_warn(clock: Clock) -> None:
    res = plan([Drive(700)], DriverState(), R, EAGER, clock)
    assert "DAILY_REST_REDUCED_BY_WINDOW" not in {w.code for w in res.warnings}


def test_no_legal_daily_rest_takes_a_weekly_rest(clock: Clock) -> None:
    # I2: a 660 min rest at t=0 has only 600 min inside the period, so it counts as a fourth
    # reduced daily rest (Art 8(4)); a weekly rest is a daily rest too and resets the count
    s = DriverState(last_daily_rest_end=-840, reduced_daily_rests_used=3)
    res = plan([Drive(60)], s, R, CAREFUL, clock)
    assert [(b.kind, b.subtype, b.minutes) for b in res.blocks] == [
        ("weekly_rest", "regular", 2700),
        ("drive", None, 60),
    ]
    assert_legal(res, s, R, clock)


def test_state_admitting_no_legal_plan_raises(clock: Clock) -> None:
    from legaleta.scheduler import SchedulingError

    # 900 min of compensation due 100 min after the start: no rest of 9 h + 900 min can end
    # by then, whatever the plan does (Art 8(6b))
    s = DriverState(compensation_owed_minutes=900, compensation_deadline=100)
    with pytest.raises(SchedulingError, match="no legal plan") as exc:
        plan([Drive(60)], s, R, CAREFUL, clock)
    assert str(exc.value).startswith("the driver state admits no legal plan: ")
    assert "Art 8(6b)" in str(exc.value)


def test_split_part_2_on_board_when_only_it_fits_the_window(clock: Clock) -> None:
    # a 3 h first part is on record and no reduced rest is left; the period ends at t=640.
    # A regular rest on board (660) would have only 640 min inside it and count as reduced;
    # the 9 h second part fits whole in the 600 min crossing, without an interruption.
    s = DriverState(
        last_daily_rest_end=-800,
        split_daily_rest_first_part_taken=True,
        reduced_daily_rests_used=3,
    )
    route = [Ferry("c", minutes=600, sleeper_cabin=True), Drive(60)]
    res = plan(route, s, R, Policy(), clock)
    assert kinds(res) == [("daily_rest", 600), ("drive", 60)]
    rest = res.blocks[0]
    assert rest.subtype == "split_part_2" and rest.at == "c" and rest.interruptions == ()
    assert_legal(res, s, R, clock)


def test_no_interrupted_split_part_2_on_board(clock: Clock) -> None:
    # a crossing shorter than 9 h would need the rest to start at the port, interrupted by
    # boarding; Art 9(1) admits that only for a regular daily or reduced weekly rest
    s = DriverState(
        last_daily_rest_end=-800,
        split_daily_rest_first_part_taken=True,
        reduced_daily_rests_used=3,
    )
    route = [Ferry("c", minutes=500, sleeper_cabin=True, boarding_minutes=20), Drive(60)]
    res = plan(route, s, R, Policy(), clock)
    assert not any(b.subtype == "split_part_2" and b.interruptions for b in res.blocks)
    assert_legal(res, s, R, clock)


# ---------------------------------------------------------------- final review fixes


@pytest.mark.parametrize("ferry_minutes", [300, 500])
def test_no_interrupted_reduced_daily_rest_on_board(clock: Clock, ferry_minutes: int) -> None:
    # C1 probe: the default policy used to plan a 9 h rest starting at the port, interrupted by
    # boarding; Art 9(1) admits that only for a regular daily rest or a weekly rest
    route = [
        Drive(530),
        Ferry("F", ferry_minutes, sleeper_cabin=True, boarding_minutes=30),
        Drive(300),
    ]
    res = plan(route, DriverState(), R, EAGER, clock)
    for b in res.blocks:
        if b.interruptions:
            assert b.kind == "weekly_rest" or b.subtype == "regular", b
    assert not any(b.subtype == "reduced" and b.interruptions for b in res.blocks)
    assert_legal(res, DriverState(), R, clock)


def test_interrupted_regular_daily_rest_still_offered(clock: Clock) -> None:
    # a regular rest that starts at the port and continues on board stays a legal option
    route = [Drive(530), Ferry("F", 600, sleeper_cabin=True, boarding_minutes=30), Drive(300)]
    res = plan(route, DriverState(), R, CAREFUL, clock)
    rest = next(b for b in res.blocks if b.kind == "daily_rest")
    assert (rest.subtype, rest.at, rest.interruptions, rest.minutes) == ("regular", "F", (30,), 690)
    assert_legal(res, DriverState(), R, clock)


@pytest.mark.parametrize("ended", [-1700, -1441, -1440, -1200, -901])
@pytest.mark.parametrize("policy", [CAREFUL, EAGER])
def test_period_ending_too_soon_opens_with_a_rest(clock: Clock, ended: int, policy: Policy) -> None:
    # I1: 15 to 24 h after the last rest (and beyond) the plan opens with a daily rest
    s = DriverState(last_daily_rest_end=ended, driving_today=300)
    res = plan([Drive(300)], s, R, policy, clock)
    assert res.blocks[0].kind == "daily_rest" and res.blocks[0].start == 0
    assert "DAILY_REST_OVERDUE_AT_START" in {w.code for w in res.warnings}
    assert_legal(res, s, R, clock)


def test_no_overdue_warning_when_a_rest_still_fits(clock: Clock) -> None:
    res = plan([Drive(60)], DriverState(last_daily_rest_end=-900), R, CAREFUL, clock)
    assert "DAILY_REST_OVERDUE_AT_START" not in {w.code for w in res.warnings}


def test_compensation_paid_by_its_deadline(clock: Clock) -> None:
    # I4: 900 min owed, due at t=2000; the weekly rest is days away, so the debt rides on a
    # daily rest that must end by 2000 (Art 8(6b)), and driving is capped to make room for it
    s = DriverState(compensation_owed_minutes=900, compensation_deadline=2000)
    res = plan([Drive(1500)], s, R, EAGER, clock)
    payer = next(b for b in res.blocks if b.compensation_minutes)
    assert payer.compensation_minutes == 900 and payer.end <= 2000
    assert res.end_state.compensation_owed_minutes == 0
    assert any("compensation must be attached (Art 8(6b))" in b.reason for b in res.blocks)
    assert_legal(res, s, R, clock)


@pytest.mark.parametrize("deadline", [1600, 1700, 3000])
def test_compensation_deadline_sweep(clock: Clock, deadline: int) -> None:
    s = DriverState(compensation_owed_minutes=900, compensation_deadline=deadline)
    for policy in (CAREFUL, EAGER, Policy(extend_rest_to=6 * 60)):
        res = plan([Drive(600), Stop("X", 60), Drive(900)], s, R, policy, clock)
        assert res.end_state.compensation_owed_minutes == 0
        assert_legal(res, s, R, clock)


def test_lateness_is_measured_at_service_start(clock: Clock) -> None:
    # I6 probe: window 200-400, arrival at 120, and the rest must start now (the period ends at
    # 660): the rest covers the wait and the service starts at 660, 260 min after the window
    route = [Drive(120), Stop("X", 60, earliest=200, latest=400), Drive(100)]
    s = DriverState(last_daily_rest_end=-780)
    res = plan(route, s, R, Policy(), clock)
    x = res.stops[0]
    assert (x.arrival, x.departure, x.rested_here) == (120, 720, True)
    assert (x.lateness_minutes, x.window_met) == (260, False)
    assert_legal(res, s, R, clock)


def test_lookahead_serves_the_window_before_resting(clock: Clock) -> None:
    # with room to serve first (the rest may start up to 300), resting at X over the wait no
    # longer looks punctual: the plan serves X in its window, then rests
    route = [Drive(120), Stop("X", 60, earliest=200, latest=400), Drive(300)]
    s = DriverState(last_daily_rest_end=-600)
    res = plan(route, s, R, Policy(), clock)
    x = res.stops[0]
    assert (x.lateness_minutes, x.window_met, x.rested_here, x.departure) == (0, True, False, 260)
    assert_legal(res, s, R, clock)

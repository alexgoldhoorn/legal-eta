from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from legaleta.checker import Violation, validate
from legaleta.clock import Clock
from legaleta.rules import PRESETS
from legaleta.state import DriverState, replay
from legaleta.timeline import Block

R = PRESETS["eu561"]


@pytest.fixture
def clock(monday_0600: datetime, madrid: ZoneInfo) -> Clock:
    return Clock(monday_0600, madrid)


def arts(vs: list[Violation]) -> list[str]:
    return [v.article for v in vs]


def test_v1_valid_day(clock: Clock) -> None:
    blocks = [
        Block("drive", 0, 270),
        Block("break", 270, 45),
        Block("drive", 315, 270),
        Block("daily_rest", 585, 660, subtype="regular"),
    ]
    assert validate(blocks, DriverState(), R, clock) == []


def test_v3_break_too_short(clock: Clock) -> None:
    blocks = [Block("drive", 0, 270), Block("break", 270, 30), Block("drive", 300, 10)]
    vs = validate(blocks, DriverState(), R, clock)
    assert arts(vs) == ["Art 7"] and vs[0].block_index == 2 and vs[0].excess_minutes == 10


def test_v4_reduced_rest_window(clock: Clock) -> None:
    # The window anchor is 06:00 (last rest ended at t=0), so it ends at t=1440.
    # Rest A 900-1440 (540 min, all inside the window) is valid; rest B starting at 990
    # has only 1440 - 990 = 450 min inside, 90 short of the 540 minimum.
    a = [Block("work", 0, 900), Block("daily_rest", 900, 540, subtype="reduced")]
    assert validate(a, DriverState(), R, clock) == []
    b = [Block("work", 0, 990), Block("daily_rest", 990, 540, subtype="reduced")]
    vs = validate(b, DriverState(), R, clock)
    assert arts(vs) == ["Art 8(2)"] and vs[0].excess_minutes == 90


def test_v5_fourth_reduced_daily_rest(clock: Clock) -> None:
    blocks: list[Block] = []
    t = 0
    for m in (540, 600, 570, 540):
        blocks += [Block("work", t, 60), Block("daily_rest", t + 60, m, subtype="reduced")]
        t += 60 + m
    vs = validate(blocks, DriverState(), R, clock)
    assert arts(vs) == ["Art 8(4)"] and vs[0].block_index == 7


def test_v6_third_extension_in_a_week(clock: Clock) -> None:
    # three 10 h days in one Mon-Sun week (6 blocks a day)
    blocks: list[Block] = []
    t = 0
    for _ in range(3):
        blocks += [
            Block("drive", t, 270),
            Block("break", t + 270, 45),
            Block("drive", t + 315, 270),
            Block("break", t + 585, 45),
            Block("drive", t + 630, 60),
            Block("daily_rest", t + 690, 660, subtype="regular"),
        ]
        t += 690 + 660
    vs = validate(blocks, DriverState(), R, clock)
    assert arts(vs) == ["Art 6(1)"] and vs[0].block_index == 16


def test_claims_checked(clock: Clock) -> None:
    vs = validate([Block("daily_rest", 0, 600, subtype="regular")], DriverState(), R, clock)
    assert vs and "claims" in vs[0].message
    vs = validate([Block("weekly_rest", 0, 1400, subtype="reduced")], DriverState(), R, clock)
    assert vs and "claims" in vs[0].message
    vs = validate([Block("daily_rest", 0, 540, subtype="split_part_2")], DriverState(), R, clock)
    assert vs and "first part" in vs[0].message


def test_v7_compensation_rules(clock: Clock) -> None:
    s = DriverState(compensation_owed_minutes=900, compensation_deadline=3000)
    partial = [
        Block("daily_rest", 0, 1200, subtype="regular", compensation_minutes=480),
        Block("drive", 1200, 10),
    ]
    assert "Art 8(6b)" in arts(validate(partial, s, R, clock))
    short_base = [
        Block("break", 0, 45),
        Block("daily_rest", 45, 1380, subtype="regular", compensation_minutes=900),
    ]
    assert "Art 8(7)" in arts(validate(short_base, s, R, clock))  # base 1380 - 900 = 480 < 540
    missed = [
        Block("work", 0, 100),
        Block("daily_rest", 100, 660, subtype="regular"),
        Block("work", 760, 3000),
    ]
    assert "Art 8(6b)" in arts(validate(missed, s, R, clock))


def test_rest_straddling_window_end_qualifies_when_enough_inside(clock: Clock) -> None:
    # window ends at 1440; a 600 min rest from 900 has 540 inside and ends at 1500: valid
    blocks = [
        Block("work", 0, 900),
        Block("daily_rest", 900, 600, subtype="reduced"),
        Block("work", 1500, 1000),
    ]
    assert validate(blocks, DriverState(), R, clock) == []


def test_deadline_passed_before_start_is_not_reported(clock: Clock) -> None:
    # the window ended 120 min before the plan: the checker restarts it in 24 h blocks (GN 7)
    s = DriverState(last_daily_rest_end=-1560, driving_today=300)
    blocks = [Block("daily_rest", 0, 660, subtype="regular"), Block("drive", 660, 60)]
    assert validate(blocks, s, R, clock) == []


def test_v9_ferry_interruptions(clock: Clock) -> None:
    ok = [Block("daily_rest", 0, 715, subtype="regular", at="f", interruptions=(25, 30))]
    assert validate(ok, DriverState(), R, clock) == []
    bad = [Block("daily_rest", 0, 730, subtype="regular", at="f", interruptions=(25, 30, 15))]
    assert arts(validate(bad, DriverState(), R, clock)) == ["Art 9(1)"]
    bad2 = [Block("daily_rest", 0, 730, subtype="regular", at="f", interruptions=(40, 30))]
    assert arts(validate(bad2, DriverState(), R, clock)) == ["Art 9(1)"]


def test_weekly_deadline_and_in_vehicle(clock: Clock) -> None:
    late = [Block("work", 0, 8700)]
    vs = validate(late, DriverState(), R, clock)
    assert "Art 8(6)" in arts(vs)
    veh = [Block("weekly_rest", 0, 2700, subtype="regular", in_vehicle=True)]
    assert arts(validate(veh, DriverState(), R, clock)) == ["Art 8(8)"]
    reduced_twice = [Block("weekly_rest", 0, 1440, subtype="reduced")]
    state = DriverState(last_weekly_rest_reduced=True)
    assert arts(validate(reduced_twice, state, R, clock)) == ["Art 8(6)"]


def test_gap_rejected(clock: Clock) -> None:
    with pytest.raises(ValueError):
        validate([Block("drive", 10, 5)], DriverState(), R, clock)


def test_weekly_deadline_passed_before_start_is_not_reported(clock: Clock) -> None:
    # the last weekly rest ended 9000 min before the plan, so its deadline (8640 after it) lapsed
    # 360 min before t=0; the checker restarts it in 6 x 24 h periods (GN 7), now due at 8280
    s = DriverState(last_weekly_rest_end=-9000)
    assert validate([Block("work", 0, 100)], s, R, clock) == []


def test_daily_driving_beyond_ten_hours(clock: Clock) -> None:
    # 270 + 270 + 61 = 601 min: one minute over the 10 h cap, even with an extension available
    blocks = [
        Block("drive", 0, 270),
        Block("break", 270, 45),
        Block("drive", 315, 270),
        Block("break", 585, 45),
        Block("drive", 630, 61),
    ]
    vs = validate(blocks, DriverState(), R, clock)
    assert arts(vs) == ["Art 6(1)"] and vs[0].block_index == 4 and vs[0].excess_minutes == 1


def test_weekly_driving_beyond_56_hours(clock: Clock) -> None:
    # 55 h driven this week + 70 min = 56 h 10 min
    vs = validate([Block("drive", 0, 70)], DriverState(driving_this_week=3300), R, clock)
    assert arts(vs) == ["Art 6(2)"] and vs[0].excess_minutes == 10


def test_fortnight_driving_beyond_90_hours(clock: Clock) -> None:
    # 35 h last week + 55 h this week = 90 h exactly; 30 more minutes breach only Art 6(3)
    s = DriverState(driving_last_week=2100, driving_this_week=3300)
    vs = validate([Block("drive", 0, 30)], s, R, clock)
    assert arts(vs) == ["Art 6(3)"] and vs[0].excess_minutes == 30


def test_lapsed_weekly_deadlines_are_each_reported(clock: Clock) -> None:
    # work to 20000 misses the deadlines at 8640 and 17280 (GN 7 restarts them period by period);
    # the rest starting at 20000 lies in the third period (deadline 25920): no violation
    blocks = [Block("work", 0, 20000), Block("weekly_rest", 20000, 2700, subtype="regular")]
    vs = [v for v in validate(blocks, DriverState(), R, clock) if v.article == "Art 8(6)"]
    assert [(v.block_index, v.excess_minutes) for v in vs] == [
        (0, 20000 - 8640),
        (0, 20000 - 17280),
    ]


def test_weekly_rest_block_under_24h_is_not_a_weekly_rest(clock: Clock) -> None:
    # 1000 min starting before the deadline (8640) but ending after it: too short to be a
    # weekly rest, so no weekly rest started in time (deliberately stricter than the brief)
    blocks = [Block("work", 0, 8000), Block("weekly_rest", 8000, 1000)]
    vs = [v for v in validate(blocks, DriverState(), R, clock) if v.article == "Art 8(6)"]
    assert [(v.block_index, v.excess_minutes) for v in vs] == [(1, 9000 - 8640)]
    assert "no weekly rest started" in vs[0].message


def test_second_debt_lapsing_after_first_was_paid_is_reported(clock: Clock) -> None:
    # D1: reduced weekly rest 0-1440 owes 1260, due at the Monday starting the 4th week.
    # A regular weekly rest before that deadline repays it in full; a second reduced weekly
    # rest creates D2, and work past D2's deadline must be reported.
    d1 = clock.week_start_after(1440, R.compensation_deadline_weeks + 1)
    t_pay = d1 - 3960 - 100
    t_red = t_pay + 3960 + 100
    d2 = clock.week_start_after(t_red + 1440, R.compensation_deadline_weeks + 1)
    blocks = [
        Block("weekly_rest", 0, 1440, subtype="reduced"),
        Block("work", 1440, t_pay - 1440),
        Block("weekly_rest", t_pay, 2700 + 1260, subtype="regular", compensation_minutes=1260),
        Block("work", t_pay + 3960, 100),
        Block("weekly_rest", t_red, 1440, subtype="reduced"),
        Block("work", t_red + 1440, d2 - t_red - 1440 + 60),
    ]
    vs = validate(blocks, DriverState(), R, clock)
    comp = [v for v in vs if v.article in ("Art 8(6b)", "Art 8(7)")]
    assert [v.block_index for v in comp] == [5]


def test_late_compensation_does_not_pay(clock: Clock) -> None:
    # I4: the rest carrying the debt ends after the deadline: the debt is reported as still
    # owed at the deadline, and it stays owed
    s = DriverState(compensation_owed_minutes=900, compensation_deadline=10)
    blocks = [Block("daily_rest", 0, 1560, subtype="regular", compensation_minutes=900)]
    vs = validate(blocks, s, R, clock)
    assert arts(vs) == ["Art 8(6b)"] and vs[0].block_index == 0
    assert "900 min of weekly-rest compensation not taken before its deadline" in vs[0].message
    assert replay(s, blocks, R, clock).compensation_owed_minutes == 900
    # ending exactly at the deadline pays
    on_time = DriverState(compensation_owed_minutes=900, compensation_deadline=1560)
    assert validate(blocks, on_time, R, clock) == []
    assert replay(on_time, blocks, R, clock).compensation_owed_minutes == 0


def test_fourth_edge_rest_counts_as_reduced(clock: Clock) -> None:
    # each 660 min "regular" rest starts 540 min before its window closes, so Art 8(2) counts
    # it as reduced; Art 8(4) allows three between weekly rests, so the fourth is an infringement
    blocks: list[Block] = []
    t = 0
    for _ in range(4):
        blocks += [Block("work", t, 900), Block("daily_rest", t + 900, 660, subtype="regular")]
        t += 1560
    vs = validate(blocks, DriverState(), R, clock)
    assert arts(vs) == ["Art 8(4)"] and vs[0].block_index == 7


def test_v9_interrupted_reduced_or_split_daily_rest_is_an_infringement(clock: Clock) -> None:
    # Art 9(1) admits interruptions only on a regular daily rest or a weekly rest
    reduced = [Block("daily_rest", 0, 570, subtype="reduced", at="f", interruptions=(30,))]
    vs = validate(reduced, DriverState(), R, clock)
    assert arts(vs) == ["Art 9(1)"] and "reduced or split" in vs[0].message
    # labelled regular, but only 570 min fall inside the period: apply() counts it reduced
    late = [Block("daily_rest", 0, 690, subtype="regular", at="f", interruptions=(30,))]
    assert "Art 9(1)" in arts(validate(late, DriverState(last_daily_rest_end=-870), R, clock))
    split1 = [Block("daily_rest", 0, 200, subtype="split_part_1", interruptions=(20,))]
    assert arts(validate(split1, DriverState(), R, clock)) == ["Art 9(1)"]
    split2 = [Block("daily_rest", 0, 560, subtype="split_part_2", interruptions=(20,))]
    s = DriverState(split_daily_rest_first_part_taken=True)
    assert arts(validate(split2, s, R, clock)) == ["Art 9(1)"]
    # a regular daily rest and both weekly rests may be interrupted
    ok = [Block("daily_rest", 0, 690, subtype="regular", interruptions=(30,))]
    assert validate(ok, DriverState(), R, clock) == []
    weekly = [Block("weekly_rest", 0, 1470, subtype="reduced", interruptions=(30,))]
    assert validate(weekly, DriverState(), R, clock) == []


@pytest.mark.parametrize("ended", [-1440, -1200, -901])
def test_period_ending_too_soon_after_start_is_restarted(clock: Clock, ended: int) -> None:
    # I1: the 24 h period ends 0 to 539 min after the start, so no rest can fit in it; like a
    # period that lapsed before the start, its shortfall belongs to the driver state (GN 7)
    s = DriverState(last_daily_rest_end=ended, driving_today=300)
    blocks = [Block("daily_rest", 0, 660, subtype="regular"), Block("drive", 660, 60)]
    assert validate(blocks, s, R, clock) == []


def test_period_with_room_for_a_rest_is_not_restarted(clock: Clock) -> None:
    # 540 min left: a rest still fits, so driving through the end of the period is reported
    s = DriverState(last_daily_rest_end=-900)
    vs = validate([Block("work", 0, 600)], s, R, clock)
    assert arts(vs) == ["Art 8(2)"] and vs[0].excess_minutes == 60


def test_weekly_rules_follow_apply_not_labels(clock: Clock) -> None:
    # I3: no subtype, but apply() makes it a second consecutive reduced weekly rest
    s = DriverState(last_weekly_rest_reduced=True)
    assert arts(validate([Block("weekly_rest", 0, 1500)], s, R, clock)) == ["Art 8(6)"]
    # labelled reduced but 45 h long: a regular weekly rest, not a second reduced one
    long = [Block("weekly_rest", 0, 2700, subtype="reduced")]
    assert validate(long, s, R, clock) == []
    # Art 8(8): a regular weekly rest in the vehicle, with no subtype
    veh = [Block("weekly_rest", 0, 2700, in_vehicle=True)]
    assert arts(validate(veh, DriverState(), R, clock)) == ["Art 8(8)"]
    # a reduced weekly rest may be taken in the vehicle
    veh_red = [Block("weekly_rest", 0, 1500, in_vehicle=True)]
    assert validate(veh_red, DriverState(), R, clock) == []
    # Art 8(8) second clause: a compensation rest of more than 45 h, in the vehicle
    comp = [Block("daily_rest", 0, 2760, compensation_minutes=2100, in_vehicle=True)]
    owed = DriverState(compensation_owed_minutes=2100, compensation_deadline=5000)
    assert arts(validate(comp, owed, R, clock)) == ["Art 8(8)"]


def test_ferry_counts_as_break_only_when_flagged(clock: Clock) -> None:
    # I5: a hand-written ferry block defaults to counts_as_rest=False
    blocks = [Block("drive", 0, 270), Block("ferry", 270, 45), Block("drive", 315, 270)]
    assert arts(validate(blocks, DriverState(), R, clock)) == ["Art 7"]
    flagged = [
        Block("drive", 0, 270),
        Block("ferry", 270, 45, counts_as_rest=True),
        Block("drive", 315, 270),
    ]
    assert validate(flagged, DriverState(), R, clock) == []

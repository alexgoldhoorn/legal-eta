import time
from datetime import datetime

from replay.judge import judge
from replay.judge_rest import check_rests, recognise_rests
from replay.log import ActivityLog
from replay.regs import Regs
from replay.types import Switches
from tests.replay.helpers import MONDAY, TZ, act, counters, day, weeks

SW = Switches()


def _kinds(acts, sw=SW, ps=None):  # type: ignore[no-untyped-def]
    return [r.kind for r in recognise_rests(acts, Regs(), sw, ps)]


def _rest_findings(acts, c=None, sw=SW, origin=MONDAY):  # type: ignore[no-untyped-def]
    rests = recognise_rests(acts, Regs(), sw, (c or counters()).daily_rest_end)
    return check_rests(acts, rests, Regs(), sw, c or counters(), weeks(origin))


def _by_code(findings, code):  # type: ignore[no-untyped-def]
    return [f for f in findings if f.code == code]


def test_11h_rest_is_daily_regular() -> None:
    """Art 8(2): a daily rest of 11 h is regular."""
    assert _kinds([act("REST", 0, 660)]) == ["daily_regular"]


def test_9h_rest_is_reduced_and_fourth_reduced_violates_art8_4() -> None:
    """Art 8(4): at most three reduced daily rests between two weekly rests."""
    acts, t = [], 0
    for _ in range(4):
        a, _, t = day(t, 480, rest_minutes=540)
        acts += a
    f = _by_code(_rest_findings(acts), "REDUCED_DAILY_RESTS_EXCEEDED")
    assert [(x.article, x.excess_minutes) for x in f] == [("Art 8(4)", 1)]


def test_split_3h_plus_9h_recognised() -> None:
    """Art 8(2): a daily rest taken as 3 h then 9 h counts as one regular rest."""
    acts = [act("REST", 0, 180), act("DRIVE", 180, 480), act("REST", 480, 1020)]
    assert _kinds(acts) == ["daily_split_1", "daily_split_2"]


def test_label_is_not_trusted() -> None:
    """Art 8(2): a block labelled regular that lasts 10 h is a reduced rest."""
    acts = [act("REST", 0, 600, subtype="regular")]
    assert _kinds(acts) == ["daily_reduced"]


def test_weekly_regular_45h() -> None:
    """Art 4(h): a weekly rest of 45 h is regular, 24 h up to 45 h is reduced (Art 8(6))."""
    assert _kinds([act("REST", 0, 2700)]) == ["weekly_regular"]
    assert _kinds([act("REST", 0, 2699)]) == ["weekly_reduced"]
    assert _kinds([act("REST", 0, 1440)]) == ["weekly_reduced"]


def _late(acts, end):  # type: ignore[no-untyped-def]
    return [*acts, act("AVAIL", acts[-1].end, end)]


def test_reduced_weekly_rest_requires_compensation_in_3_weeks() -> None:
    """Art 8(6b): the reduction must be compensated before the end of the third following week."""
    deadline = weeks().shift(0, 4)  # Monday 00:00 four weeks on; DST makes it 60 min longer
    unpaid = _late([act("REST", 0, 1440)], deadline + 10)
    f = _by_code(_rest_findings(unpaid), "COMPENSATION_UNPAID")
    assert [(x.article, x.excess_minutes) for x in f] == [("Art 8(6b)", 1260)]
    paid = _late([act("REST", 0, 1440), act("REST", 3000, 3000 + 3960)], deadline + 10)
    assert _by_code(_rest_findings(paid), "COMPENSATION_UNPAID") == []
    late = _late([act("REST", 0, 1440), act("REST", 38000, 38000 + 3960)], deadline + 10)
    assert len(_by_code(_rest_findings(late), "COMPENSATION_UNPAID")) == 1


def test_two_consecutive_reduced_weekly_rests_violate() -> None:
    """Art 8(6): two consecutive reduced weekly rests are not allowed."""
    acts = [act("REST", 0, 1440), act("DRIVE", 1440, 1500), act("REST", 3000, 4440)]
    f = _by_code(_rest_findings(acts), "CONSECUTIVE_REDUCED_WEEKLY")
    assert [x.article for x in f] == ["Art 8(6)"]
    prior = _rest_findings([act("REST", 0, 1440)], counters(last_weekly_rest_reduced=True))
    assert len(_by_code(prior, "CONSECUTIVE_REDUCED_WEEKLY")) == 1


def test_weekly_rest_started_after_six_periods_violates() -> None:
    """Art 8(6): a weekly rest must start within six 24 h periods of the previous one."""
    acts = [act("AVAIL", 0, 8700), act("REST", 8700, 11400)]
    f = _by_code(_rest_findings(acts), "WEEKLY_REST_LATE")
    assert [(x.article, x.excess_minutes) for x in f] == [("Art 8(6)", 60)]


def test_weekly_rest_missing_at_end() -> None:
    """Art 8(6): driving on past the six-period limit with no weekly rest is a violation."""
    f = _by_code(_rest_findings([act("DRIVE", 8600, 8700)]), "WEEKLY_REST_MISSING")
    assert [x.excess_minutes for x in f] == [60]


def test_daily_rest_late_and_missing() -> None:
    """Art 8(2): a daily rest must be taken within each 24 h period."""
    late = [act("AVAIL", 0, 1000), act("REST", 1000, 1660)]
    assert len(_by_code(_rest_findings(late), "DAILY_REST_LATE")) == 1
    assert len(_by_code(_rest_findings([act("DRIVE", 0, 1500)]), "DAILY_REST_MISSING")) == 1


def test_overdue_start_is_restarted_under_the_switch() -> None:
    """Guidance Note 7: an overdue period at start is restarted, not reported."""
    c = counters(daily_rest_end=-1400)
    acts = [act("REST", 0, 660)]
    assert _rest_findings(acts, c) == []
    strict = _rest_findings(acts, c, Switches(overdue_start_restarts_period=False))
    assert _by_code(strict, "DAILY_REST_LATE")


def test_regular_rest_late_in_its_period_counts_reduced() -> None:
    """Art 8(2) second sentence: a 11 h rest with under 11 h inside its 24 h period is reduced."""
    acts = [act("AVAIL", 0, 840), act("REST", 840, 1500)]
    assert _kinds(acts, ps=0) == ["daily_reduced"]
    assert _kinds(acts, Switches(window_reduced=False), ps=0) == ["daily_regular"]


def test_art9_interruption_rules() -> None:
    """Art 9(1): a regular daily rest may be interrupted twice, one hour in total."""
    ok = [act("REST", 0, 700, interruptions=(20, 20))]
    assert _by_code(_rest_findings(ok), "INTERRUPTION_NOT_ALLOWED") == []
    three = [act("REST", 0, 700, interruptions=(10, 10, 10))]
    assert _by_code(_rest_findings(three), "INTERRUPTIONS_EXCEEDED")
    long = [act("REST", 0, 760, interruptions=(40, 30))]
    assert _by_code(_rest_findings(long), "INTERRUPTIONS_EXCEEDED")
    reduced = [act("REST", 0, 600, interruptions=(30,))]
    assert _by_code(_rest_findings(reduced), "INTERRUPTION_NOT_ALLOWED")
    weekly_regular = [act("REST", 0, 2800, interruptions=(30,))]
    assert _by_code(_rest_findings(weekly_regular), "INTERRUPTION_NOT_ALLOWED")
    weekly_reduced = [act("REST", 0, 1500, interruptions=(30,))]
    assert _by_code(_rest_findings(weekly_reduced), "INTERRUPTION_NOT_ALLOWED") == []


def test_regular_weekly_rest_in_vehicle_violates_art8_8() -> None:
    """Art 8(8): a regular weekly rest may not be taken in the vehicle."""
    f = _rest_findings([act("REST", 0, 2700, in_vehicle=True)])
    assert [x.article for x in _by_code(f, "WEEKLY_REST_IN_VEHICLE")] == ["Art 8(8)"]


def _judge(acts, c=None):  # type: ignore[no-untyped-def]
    return judge(ActivityLog(MONDAY, list(acts)), [], c or counters(), Regs(), SW, TZ)


def test_ferry_without_cabin_is_break_not_rest() -> None:
    """Art 7 + Art 8: a ferry crossing that is not a rest is not a break either (AVAIL)."""
    no_cabin = [act("DRIVE", 0, 270), act("AVAIL", 270, 570), act("DRIVE", 570, 600)]
    assert _by_code(_judge(no_cabin), "BREAK_MISSING")
    cabin = [act("DRIVE", 0, 270), act("REST", 270, 570), act("DRIVE", 570, 600)]
    assert _by_code(_judge(cabin), "BREAK_MISSING") == []


def test_week_boundary_across_dst() -> None:
    """Art 6(2): weeks are local calendar weeks; the 25 h day of the DST change is kept."""
    origin = datetime(2026, 10, 25, 0, 0, tzinfo=TZ)  # Sunday, the day the clocks go back
    w = weeks(origin)
    assert w.floor(1499) == -8640 and w.floor(1500) == 1500  # Monday 00:00 is 25 h away
    acts = [act("DRIVE", 1400, 1600)]
    log = ActivityLog(origin, acts)
    assert (
        _by_code(
            judge(log, [], counters(driving_this_week_prior=3260), Regs(), SW, TZ),
            "WEEKLY_DRIVING_EXCEEDED",
        )
        == []
    )
    got = judge(log, [], counters(driving_this_week_prior=3270), Regs(), SW, TZ)
    assert [f.excess_minutes for f in _by_code(got, "WEEKLY_DRIVING_EXCEEDED")] == [10]


def test_zero_minute_blocks_ignored_and_adjacent_rests_merge() -> None:
    """Art 8(2): two rests back to back form one rest; an empty rest is nothing."""
    assert _kinds([act("REST", 0, 0)]) == []
    assert _kinds([act("REST", 0, 300), act("REST", 300, 660)]) == ["daily_regular"]


def test_900_hour_route_is_linear() -> None:
    """A multi-week log judges in linear time."""
    acts, t = [], 0
    while t < 54_000:
        a, _, t = day(t, 480)
        acts += a
    start = time.perf_counter()
    _judge(acts)
    assert time.perf_counter() - start < 2.0


def test_ferry_break_does_not_join_a_daily_rest() -> None:
    """A crossing without a cabin is a break only: it must not merge into a 9 h rest."""
    acts = [act("REST", 0, 556, counts_as_rest=True), act("REST", 556, 586)]
    ferry = acts[0].__class__(
        acts[0].kind, 0, 556, acts[0].label.__class__(None, "ferry", counts_as_rest=True)
    )
    assert _kinds([ferry, acts[1]]) == []

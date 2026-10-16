"""Scenarios taken from the regulation's own wording (UK revised text, legislation.gov.uk).

Each docstring names the article and quotes the words the verdict rests on. No Commission guidance
note was reachable when these were written, so there is no scenario here that rests on one.
"""

from replay.judge import judge
from replay.log import ActivityLog
from replay.regs import Regs
from replay.types import Switches
from tests.replay.helpers import MONDAY, TUESDAY, TZ, act, counters, day

SW = Switches()


def _judge(acts, origin=MONDAY, c=None):  # type: ignore[no-untyped-def]
    return judge(ActivityLog(origin, list(acts)), [], c or counters(), Regs(), SW, TZ)


def _codes(findings) -> set[str]:  # type: ignore[no-untyped-def]
    return {f.code for f in findings if f.cls == "VIOLATION"}


def test_art6_1_two_ten_hour_days_in_a_week_are_legal_a_third_is_not() -> None:
    """Art 6(1): "may be extended to at most 10 hours not more than twice during the week"."""
    acts, t = [], 0
    for drive in (600, 600):
        a, _, t = day(t, drive)
        acts += a
    assert "EXTENDED_DAY_LIMIT" not in _codes(_judge(acts, TUESDAY))
    a, _, t = day(t, 600)
    assert "EXTENDED_DAY_LIMIT" in _codes(_judge(acts + a, TUESDAY))


def test_art7_after_4h30_a_45_minute_break_or_15_then_30() -> None:
    """Art 7: "a break of at least 15 minutes followed by a break of at least 30 minutes"."""
    legal = [act("DRIVE", 0, 270), act("REST", 270, 315), act("DRIVE", 315, 400)]
    assert "BREAK_MISSING" not in _codes(_judge(legal))
    only_30 = [act("DRIVE", 0, 270), act("REST", 270, 300), act("DRIVE", 300, 570)]
    assert "BREAK_MISSING" in _codes(_judge(only_30))


def test_art7_a_rest_before_any_driving_is_not_the_first_part_of_a_split_break() -> None:
    """Art 7: a break is taken "after a driving period"; the 15 minutes come before the 30."""
    acts = [
        act("REST", 0, 15),
        act("WORK", 15, 60),
        act("DRIVE", 60, 330),
        act("REST", 330, 360),
        act("DRIVE", 360, 630),
    ]
    assert "BREAK_MISSING" in _codes(_judge(acts))


def test_art8_2_portion_inside_the_24h_period_decides_reduced() -> None:
    """Art 8(2): "at least nine hours but less than 11 hours" inside the period is reduced."""
    from replay.judge_rest import recognise_rests

    late_start = [act("AVAIL", 0, 840), act("REST", 840, 1500)]  # 11 h rest, 10 h inside 24 h
    assert [r.kind for r in recognise_rests(late_start, Regs(), SW, 0)] == ["daily_reduced"]


def test_art8_4_at_most_three_reduced_daily_rests_between_weekly_rests() -> None:
    """Art 8(4): "at most three reduced daily rest periods between any two weekly rest periods"."""
    acts, t = [], 0
    for _ in range(3):
        a, _, t = day(t, 480, rest_minutes=540)
        acts += a
    assert "REDUCED_DAILY_RESTS_EXCEEDED" not in _codes(_judge(acts))
    a, _, t = day(t, 480, rest_minutes=540)
    assert "REDUCED_DAILY_RESTS_EXCEEDED" in _codes(_judge(acts + a))


def test_art8_6_weekly_rest_starts_within_six_24h_periods() -> None:
    """Art 8(6): "no later than at the end of six 24-hour periods from the end of the previous"."""
    ok = [act("AVAIL", 0, 8640), act("REST", 8640, 11340, "weekly_rest")]
    late = [act("AVAIL", 0, 8700), act("REST", 8700, 11400, "weekly_rest")]
    assert "WEEKLY_REST_LATE" not in _codes(_judge(ok))
    assert "WEEKLY_REST_LATE" in _codes(_judge(late))


def test_art8_6b_and_7_compensation_en_bloc_attached_to_a_rest_of_9_hours() -> None:
    """Art 8(6b): "compensated by an equivalent period of rest taken en bloc"; 8(7): "attached to
    another rest period of at least nine hours"."""
    c = counters(compensation_owed=300, compensation_deadline=3000)
    paid = [act("REST", 0, 540 + 300, "daily_rest"), act("AVAIL", 840, 3100)]
    assert "COMPENSATION_UNPAID" not in _codes(_judge(paid, c=c))
    two_parts = [
        act("REST", 0, 540 + 150, "daily_rest"),
        act("AVAIL", 690, 1500),
        act("REST", 1500, 1500 + 540 + 150, "daily_rest"),
        act("AVAIL", 2190, 3100),
    ]
    assert "COMPENSATION_UNPAID" in _codes(_judge(two_parts, c=c))


def test_art9_1_interruption_of_a_regular_daily_rest_at_most_twice_and_one_hour() -> None:
    """Art 9(1): "may be interrupted not more than twice by other activities not exceeding one
    hour in total"."""
    ok = [act("REST", 0, 720, interruptions=(30, 30))]
    bad = [act("REST", 0, 720, interruptions=(30, 20, 10, 5))]
    assert "INTERRUPTIONS_EXCEEDED" not in _codes(_judge(ok))
    assert "INTERRUPTIONS_EXCEEDED" in _codes(_judge(bad))

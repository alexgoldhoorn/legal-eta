from replay.judge_driving import check_driving
from replay.regs import Regs
from tests.replay.helpers import TUESDAY, act, counters, day, weeks


def _check(acts, rests=(), c=None, origin=None):  # type: ignore[no-untyped-def]
    w = weeks(origin) if origin else weeks()
    return check_driving(list(acts), Regs(), list(rests), c or counters(), w)


def _arts(findings) -> list[tuple[str | None, int]]:  # type: ignore[no-untyped-def]
    return [(f.article, f.excess_minutes) for f in findings]


def test_271_min_without_break_violates_art7() -> None:
    """Art 7: after 4.5 h of driving a break of 45 min is required."""
    assert _arts(_check([act("DRIVE", 0, 271)])) == [("Art 7", 1)]


def test_270_min_then_45_break_passes() -> None:
    """Art 7: 270 min of driving followed by a 45 min break is legal."""
    acts = [act("DRIVE", 0, 270), act("REST", 270, 315), act("DRIVE", 315, 400)]
    assert _check(acts) == []


def test_split_15_then_30_passes() -> None:
    """Art 7: a break may be taken as 15 min followed by 30 min."""
    acts = [
        act("DRIVE", 0, 100),
        act("REST", 100, 115),
        act("DRIVE", 115, 215),
        act("REST", 215, 245),
        act("DRIVE", 245, 345),
    ]
    assert _check(acts) == []


def test_split_30_then_15_violates() -> None:
    """Art 7: the order matters, 15 min must come first, then 30 min."""
    acts = [
        act("DRIVE", 0, 150),
        act("REST", 150, 180),
        act("DRIVE", 180, 280),
        act("REST", 280, 295),
        act("DRIVE", 295, 400),
    ]
    assert [a for a, _ in _arts(_check(acts))] == ["Art 7"]


def test_adjacent_rest_blocks_form_one_break() -> None:
    """Art 7: two rest blocks back to back (20 + 25 min) are one 45 min break."""
    acts = [
        act("DRIVE", 0, 270),
        act("REST", 270, 290),
        act("REST", 290, 315),
        act("DRIVE", 315, 500),
    ]
    assert _check(acts) == []


def test_work_between_rest_parts_does_not_join_them() -> None:
    """Art 7: other work between two rests keeps them separate breaks."""
    acts = [
        act("DRIVE", 0, 270),
        act("REST", 270, 290),
        act("WORK", 290, 300),
        act("REST", 300, 325),
        act("DRIVE", 325, 400),
    ]
    assert [a for a, _ in _arts(_check(acts))] == ["Art 7"]


def test_601_min_day_violates_art6_1() -> None:
    """Art 6(1): daily driving may not exceed 10 h even on an extended day."""
    acts, _, _ = day(0, 601, rest_minutes=0)
    assert ("Art 6(1)", 1) in _arts(_check(acts[:-1]))


def test_third_extended_day_violates() -> None:
    """Art 6(1): the 10 h extension is allowed at most twice between weekly rests."""
    acts, rests, t = [], [], 0
    for _ in range(3):
        a, r, t = day(t, 570)
        acts += a
        rests += r
    got = _arts(_check(acts, rests, origin=TUESDAY))
    assert got == [("Art 6(1)", 30)]


def _week(minutes_last_day: int):  # type: ignore[no-untyped-def]
    acts, rests, t = [], [], 0
    for i in range(7):
        a, r, t = day(t, 480 + (1 if i == 6 and minutes_last_day else 0))
        acts += a
        rests += r
    return acts, rests


def test_week_56h_boundary() -> None:
    """Art 6(2): 3360 min in a calendar week is legal, 3361 is not."""
    ok_acts, ok_rests = _week(0)
    bad_acts, bad_rests = _week(1)
    assert _check(ok_acts, ok_rests) == []
    assert _arts(_check(bad_acts, bad_rests)) == [("Art 6(2)", 1)]


def test_fortnight_90h() -> None:
    """Art 6(3): this week plus last week may not exceed 5400 min."""
    acts, rests = _week(0)
    assert _check(acts, rests, counters(driving_last_week=2040)) == []
    got = _arts(_check(acts, rests, counters(driving_last_week=2100)))
    assert got == [("Art 6(3)", 60)]


def test_prior_driving_counts_in_the_start_week() -> None:
    """Art 6(2): driving earlier this week (from the start state) counts."""
    got = _arts(_check([act("DRIVE", 0, 100)], c=counters(driving_this_week_prior=3300)))
    assert got == [("Art 6(2)", 40)]


def test_driving_split_at_week_boundary() -> None:
    """Art 6(2): a drive across Monday 00:00 counts in both weeks."""
    # origin Sunday 23:00 local: 60 minutes fall in the old week
    from datetime import datetime

    from tests.replay.helpers import TZ

    origin = datetime(2026, 10, 18, 23, 0, tzinfo=TZ)
    acts = [act("DRIVE", 0, 120)]
    assert _check(acts, c=counters(driving_this_week_prior=3300), origin=origin) == []
    got = _arts(_check(acts, c=counters(driving_this_week_prior=3350), origin=origin))
    assert got == [("Art 6(2)", 50)]


def test_boundary_rest_resets_daily_segment() -> None:
    """Art 6(1): driving is counted per 24 h period between daily rests."""
    a1, r1, t = day(0, 540)
    a2, r2, t = day(t, 540)
    assert _check(a1 + a2, r1 + r2) == []


def test_extended_days_count_per_calendar_week_not_per_weekly_rest() -> None:
    """Art 6(1): the 10 h extension is allowed twice during the (calendar) week."""
    # origin Saturday 2026-10-17 06:00 local: day 1 is Saturday, days 3 and 4 are Mon/Tue
    from datetime import datetime

    from tests.replay.helpers import TZ

    origin = datetime(2026, 10, 17, 6, 0, tzinfo=TZ)
    acts, rests, t = [], [], 0
    for drive in (570, 480, 480, 570, 570):  # Sat, Sun, Mon... see below
        a, r, t = day(t, drive)
        acts += a
        rests += r
    # extended days land on Sat (week 1) and on day 4 and 5 (Tue/Wed, week 2): two per week
    assert [f.code for f in _check(acts, rests, origin=origin) if f.article == "Art 6(1)"] == []

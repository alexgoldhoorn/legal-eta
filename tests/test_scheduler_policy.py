from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from legaleta.clock import Clock
from legaleta.policy import Policy, policy_from
from legaleta.rules import PRESETS
from legaleta.scheduler import plan
from legaleta.state import DriverState
from legaleta.timeline import Drive, Stop
from tests.conftest import assert_legal

R = PRESETS["eu561"]


@pytest.fixture
def clock(monday_0600: datetime, madrid: ZoneInfo) -> Clock:
    return Clock(monday_0600, madrid)


def codes(res) -> set[str]:  # type: ignore[no-untyped-def]
    return {w.code for w in res.warnings}


# --- break_timing
def test_earliest_convenient_breaks_at_first_stop_after_3h(clock: Clock) -> None:
    # Under "latest" the cheapest plan breaks at B (its 40 idle minutes nearly cover the break:
    # arrival 415). Breaking at A costs 40 more minutes (455) but after 200 min of driving
    # "earliest_convenient" prefers it.
    route = [
        Drive(200),
        Stop("A", service_minutes=20, driver_works=True),
        Drive(50),
        Stop("B", service_minutes=40, driver_works=False),
        Drive(100),
    ]
    latest = plan(route, DriverState(), R, Policy(break_timing="latest"), clock)
    early = plan(route, DriverState(), R, Policy(break_timing="earliest_convenient"), clock)
    assert [b.at for b in latest.blocks if b.kind == "break"] == ["B"] and latest.arrival == 415
    assert [b.at for b in early.blocks if b.kind == "break"][0] == "A" and early.arrival == 455
    assert_legal(early, DriverState(), R, clock)


def test_balanced_only_when_nearly_free(clock: Clock) -> None:
    # lookahead 0 so the due-soon rule does not offer a break at A by itself; balanced offers it
    # only when extending the stop costs at most 15 min (idle 35 -> yes, idle 5 -> no)
    p = Policy(break_timing="balanced", break_at_stop_lookahead_minutes=0)
    cheap = [
        Drive(200),
        Stop("A", service_minutes=35, driver_works=False),
        Drive(50),
        Stop("B", service_minutes=20, driver_works=True),
        Drive(100),
    ]
    dear = [
        Drive(200),
        Stop("A", service_minutes=5, driver_works=False),
        Drive(50),
        Stop("B", service_minutes=20, driver_works=True),
        Drive(100),
    ]
    assert [b.at for b in plan(cheap, DriverState(), R, p, clock).blocks if b.kind == "break"][
        0
    ] == "A"
    assert [b.at for b in plan(dear, DriverState(), R, p, clock).blocks if b.kind == "break"][
        0
    ] != "A"


# --- split_breaks
def test_split_never_uses_full_break_even_with_first_part(clock: Clock) -> None:
    s = DriverState(driving_since_break=270, split_break_first_part_taken=True)
    res = plan([Drive(30)], s, R, Policy(split_breaks="never"), clock)
    assert res.blocks[0].kind == "break" and res.blocks[0].minutes == 45
    assert_legal(res, s, R, clock)


def test_split_prefer_takes_15_at_working_stop(clock: Clock) -> None:
    route = [
        Drive(100),
        Stop("A", service_minutes=20, driver_works=True),
        Drive(170),
        Stop("B", service_minutes=40, driver_works=False),
        Drive(100),
    ]
    res = plan(route, DriverState(), R, Policy(split_breaks="prefer"), clock)
    brk = [(b.at, b.minutes) for b in res.blocks if b.kind in ("break",)]
    assert ("A", 15) in brk
    assert_legal(res, DriverState(), R, clock)


# --- break_at_working_stop
def test_break_before_or_after_work(clock: Clock) -> None:
    route = [Drive(240), Stop("A", service_minutes=30, driver_works=True), Drive(100)]
    after = plan(route, DriverState(), R, Policy(break_at_working_stop="after"), clock)
    before = plan(route, DriverState(), R, Policy(break_at_working_stop="before"), clock)
    assert [b.kind for b in after.blocks[1:3]] == ["work", "break"]
    assert [b.kind for b in before.blocks[1:3]] == ["break", "work"]


# --- rest_places
def test_prefer_tagged_rests_at_stop(clock: Clock) -> None:
    route = [
        Drive(480),
        Stop("C", service_minutes=20, driver_works=True, rest_allowed=True),
        Drive(200),
    ]
    anywhere = plan(
        route,
        DriverState(),
        R,
        Policy(rest_places="anywhere", daily_rest_type="regular", extended_driving="never"),
        clock,
    )
    tagged = plan(
        route,
        DriverState(),
        R,
        Policy(rest_places="prefer_tagged", daily_rest_type="regular", extended_driving="never"),
        clock,
    )
    assert tagged.stops[0].rested_here is True
    assert anywhere.arrival == tagged.arrival
    assert_legal(tagged, DriverState(), R, clock)


def test_tagged_only_falls_back_with_warning(clock: Clock) -> None:
    route = [Drive(700), Stop("C", service_minutes=20, rest_allowed=False)]
    res = plan(
        route,
        DriverState(),
        R,
        Policy(rest_places="tagged_only", daily_rest_type="regular", extended_driving="never"),
        clock,
    )
    assert "PREFERENCE_UNMET:rest_places" in codes(res)
    assert any(b.kind == "daily_rest" and b.at is None for b in res.blocks)
    assert_legal(res, DriverState(), R, clock)


# --- rest_start_window
# Route used by the window tests: D is reached at 11:45 (t=345) after 300 min of driving with one
# break; E is reached at 16:00 but its window opens at 19:00 (t=780); the 60 min after E need a
# daily rest first. Reduced rests are allowed: a regular rest starting at 19:15 would have only
# 645 min inside the 24 h period, a window-reduced rest that daily_rest_type "regular" forbids.
# A midday rest arrives at the end earlier than waiting at E, serving it, and resting on
# leaving (19:15, roadside, 540 min reduced). A rest at E itself would start at 16:00 (it covers
# the wait) and is outside an evening window.
WINDOW_ROUTE = [
    Drive(300),
    Stop("D", service_minutes=15, driver_works=True),
    Drive(240),
    Stop("E", service_minutes=15, driver_works=True, earliest=780),
    Drive(60),
]
WINDOW_BASE = {
    "daily_rest_type": "reduced_eager",
    "extended_driving": "never",
    "rest_place_lookahead_minutes": 300,
}


def test_rest_window_soft_moves_the_rest(clock: Clock) -> None:
    evening = policy_from(
        "fastest", {**WINDOW_BASE, "rest_start_window": {"earliest": "19:00", "latest": "22:00"}}
    )
    res = plan(WINDOW_ROUTE, DriverState(), R, evening, clock)
    rest = [b for b in res.blocks if b.kind == "daily_rest"][0]
    assert (
        rest.start == res.stops[1].departure
        and 19 * 60 <= clock.minute_of_day(rest.start) <= 22 * 60
    )
    assert_legal(res, DriverState(), R, clock)
    midday = policy_from(
        "fastest", {**WINDOW_BASE, "rest_start_window": {"earliest": "10:00", "latest": "12:00"}}
    )
    res2 = plan(WINDOW_ROUTE, DriverState(), R, midday, clock)
    rest2 = [b for b in res2.blocks if b.kind == "daily_rest"][0]
    # with 9 h rests the earliest midday rest (roadside at 10:30, after the first 4.5 h) beats
    # resting at D (11:45): both start inside the window, and only the first needs no break later
    assert 10 * 60 <= clock.minute_of_day(rest2.start) <= 12 * 60 and res2.arrival < res.arrival
    assert_legal(res2, DriverState(), R, clock)
    # the rest-type knob outranks a soft start window: under "regular" the rest is taken at E
    regular = policy_from(
        "fastest",
        {
            **WINDOW_BASE,
            "daily_rest_type": "regular",
            "rest_start_window": {"earliest": "19:00", "latest": "22:00"},
        },
    )
    res3 = plan(WINDOW_ROUTE, DriverState(), R, regular, clock)
    rest3 = [b for b in res3.blocks if b.kind == "daily_rest"][0]
    assert rest3.at == "E" and rest3.start == res3.stops[1].arrival and rest3.subtype == "regular"
    assert_legal(res3, DriverState(), R, clock)


def test_rest_window_hard_unmet_when_law_forces_earlier(clock: Clock) -> None:
    s = DriverState(
        last_daily_rest_end=-900
    )  # window ends at 540 -> a rest must start by 540 at the latest (reduced)
    p = policy_from(
        "fastest", {"rest_start_window": {"earliest": "20:00", "latest": "22:00", "hard": True}}
    )
    res = plan([Drive(700)], s, R, p, clock)
    assert "PREFERENCE_UNMET:rest_start_window" in codes(res)
    assert_legal(res, s, R, clock)


def test_rest_start_window_across_dst(madrid: ZoneInfo) -> None:
    # Sat 2026-10-24 06:00 start; the 9 h rest on leaving E starts 19:15 CEST and ends after DST
    # ended (03:00 -> 02:00): 04:15 CEST would be, so it ends at 03:15 CET on 2026-10-25. The window
    # must be read in local time on both sides of the change.
    start = datetime(2026, 10, 24, 6, 0, tzinfo=madrid)
    clock = Clock(start, madrid)
    evening = policy_from(
        "fastest", {**WINDOW_BASE, "rest_start_window": {"earliest": "19:00", "latest": "22:00"}}
    )
    res = plan(WINDOW_ROUTE, DriverState(), R, evening, clock)
    rest = [b for b in res.blocks if b.kind == "daily_rest"][0]
    assert (
        rest.start == res.stops[1].departure
        and 19 * 60 <= clock.minute_of_day(rest.start) <= 22 * 60
    )
    assert clock.local(rest.end).strftime("%Y-%m-%d %H:%M") == "2026-10-25 03:15"
    assert_legal(res, DriverState(), R, clock)


# --- extend_rest_to
def test_extend_rest_to_morning(clock: Clock) -> None:
    route = [Drive(540), Drive(60)]
    p = policy_from(
        "fastest",
        {"extend_rest_to": "06:00", "daily_rest_type": "regular", "extended_driving": "never"},
    )
    res = plan(route, DriverState(), R, p, clock)
    rest = [b for b in res.blocks if b.kind == "daily_rest"][0]
    assert clock.minute_of_day(rest.end) == 6 * 60
    assert_legal(res, DriverState(), R, clock)


def test_extend_rest_to_respects_max(clock: Clock) -> None:
    route = [Drive(540), Drive(60)]
    p = policy_from(
        "fastest",
        {
            "extend_rest_to": "12:00",
            "daily_rest_type": "regular",
            "extended_driving": "never",
            "extend_rest_max_minutes": 60,
        },
    )
    res = plan(route, DriverState(), R, p, clock)
    rest = [b for b in res.blocks if b.kind == "daily_rest"][0]
    assert rest.minutes == 660


# --- wait_counts_as_rest
def test_wait_not_counted_when_disabled(clock: Clock) -> None:
    route = [Drive(200), Stop("A", service_minutes=20, driver_works=True, earliest=300), Drive(200)]
    res = plan(route, DriverState(), R, Policy(wait_counts_as_rest=False), clock)
    # the 100 min wait no longer counts by itself; the scheduler turns it into an explicit break
    # instead
    assert not any(b.kind == "wait" and b.counts_as_rest for b in res.blocks)
    assert "WAIT_ASSUMED_REST" not in codes(res)
    assert any(b.kind == "break" and b.at == "A" and b.minutes == 100 for b in res.blocks)
    assert_legal(res, DriverState(), R, clock)


# --- weekly_rest_type
def test_weekly_regular_only(clock: Clock) -> None:
    s = DriverState(last_weekly_rest_end=-8000)
    res = plan([Drive(1200)], s, R, Policy(weekly_rest_type="regular"), clock)
    weekly = [b for b in res.blocks if b.kind == "weekly_rest"]
    assert weekly and weekly[0].subtype == "regular"
    assert_legal(res, s, R, clock)

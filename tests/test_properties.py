"""Hypothesis properties: legality, consistency, determinism, remaining-form round trip."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from legaleta.clock import Clock
from legaleta.policy import PRESETS as POLICIES
from legaleta.rules import PRESETS
from legaleta.scheduler import SchedulingError, plan
from legaleta.state import DriverState, from_remaining, replay, to_remaining
from legaleta.timeline import Drive, Ferry, RouteItem, Stop
from tests.conftest import assert_legal

R = PRESETS["eu561"]
MADRID = ZoneInfo("Europe/Madrid")
CLOCK = Clock(datetime(2026, 10, 19, 6, 0, tzinfo=MADRID), MADRID)

drives = st.builds(Drive, minutes=st.integers(5, 600))


@st.composite
def stops(draw: st.DrawFn) -> Stop:
    earliest = draw(st.one_of(st.none(), st.integers(0, 3000)))
    return Stop(
        id=draw(st.uuids().map(str)),
        service_minutes=draw(st.integers(0, 120)),
        driver_works=draw(st.booleans()),
        earliest=earliest,
        latest=None if earliest is None else earliest + 240,
        rest_allowed=draw(st.booleans()),
        counts_as_rest=draw(st.one_of(st.none(), st.booleans())),
    )


@st.composite
def ferries(draw: st.DrawFn) -> Ferry:
    cabin = draw(st.booleans())
    # without a bunk the crossing is only a break: longer than 780 min admits no legal plan
    return Ferry(
        id=draw(st.uuids().map(str)),
        minutes=draw(st.integers(30, 900 if cabin else 780)),
        sleeper_cabin=cabin,
        boarding_minutes=draw(st.integers(0, 90)),
    )


routes = st.lists(st.one_of(drives, stops(), ferries()), min_size=1, max_size=40)


@st.composite
def states(draw: st.DrawFn) -> DriverState:
    driving_today = draw(st.integers(0, 540))
    last_daily_rest_end = draw(st.integers(-900, 0))
    reduced_used = draw(st.integers(0, 3))
    # a regular rest at t=0 would count as reduced (Art 8(2)) with no reduced rest left
    assume(not (reduced_used == 3 and last_daily_rest_end < -780))
    return DriverState(
        driving_since_break=draw(st.integers(0, min(270, driving_today))),
        split_break_first_part_taken=draw(st.booleans()),
        driving_today=driving_today,
        last_daily_rest_end=last_daily_rest_end,
        split_daily_rest_first_part_taken=draw(st.booleans()),
        reduced_daily_rests_used=reduced_used,
        driving_this_week=draw(st.integers(0, 3000)),
        driving_last_week=draw(st.integers(0, 2400)),
        extended_days_used_this_week=draw(st.integers(0, 2)),
        # the weekly deadline must not have lapsed before t=0 plus a daily window
        last_weekly_rest_end=draw(st.integers(-8640 + 1440, 0)),
        last_weekly_rest_reduced=draw(st.booleans()),
    )


@settings(max_examples=150, deadline=None)
@given(route=routes, state=states(), preset=st.sampled_from(sorted(POLICIES)))
def test_every_plan_is_legal_and_consistent(
    route: list[RouteItem], state: DriverState, preset: str
) -> None:
    res = plan(route, state, R, POLICIES[preset], CLOCK)
    assert_legal(res, state, R, CLOCK)
    assert len(res.stops) == sum(isinstance(i, Stop) for i in route)
    assert replay(state, res.blocks, R, CLOCK) == res.end_state
    again = plan(route, state, R, POLICIES[preset], CLOCK)
    assert again.blocks == res.blocks
    assert again.end_state == res.end_state


@settings(max_examples=200, deadline=None)
@given(state=states())
def test_remaining_round_trip(state: DriverState) -> None:
    back, _ = from_remaining(to_remaining(state, R, now=0), R, now=0)
    assert back == state


def test_known_impossible_state_raises() -> None:
    state = DriverState(last_daily_rest_end=-840, reduced_daily_rests_used=3)
    with pytest.raises(SchedulingError, match="no legal plan"):
        plan([Drive(60)], state, R, POLICIES["fastest"], CLOCK)


@pytest.mark.parametrize("preset", ["fastest", "driver_friendly"])
def test_ferry_chain_counterexample(preset: str) -> None:
    """Shrunk by Hypothesis: three ferries in a row after a long drive; `strict` plans it.

    A lookahead horizon that dead-ended (no on-board split second part at ferry c) used to
    fail the whole plan; a dead-end horizon now only rules its candidate out.
    """
    state = DriverState(
        driving_since_break=98,
        driving_today=99,
        last_daily_rest_end=-606,
        split_daily_rest_first_part_taken=True,
        reduced_daily_rests_used=3,
        driving_last_week=31,
    )
    route: list[RouteItem] = [
        Drive(520),
        Ferry("a", 30, False, 0),
        Ferry("b", 80, False, 87),
        Ferry("c", 814, True, 90),
        Drive(94),
    ]
    res = plan(route, state, R, POLICIES[preset], CLOCK)
    assert_legal(res, state, R, CLOCK)


@pytest.mark.parametrize("preset", sorted(POLICIES))
def test_road_rest_relabelled_reduced_counterexample(preset: str) -> None:
    """Found by a 3000-example sweep after fix round 1; also hit by the property above.

    The regular-rest road choice at t=543 has only 540 min inside the 24 h period, so it
    counts as reduced with none left (Art 8(4)); it tied with the split second part (the wait
    at X absorbs the extra 2 h) and won, and plan() raised on its own output.
    """
    state = DriverState(
        last_daily_rest_end=-357,
        split_daily_rest_first_part_taken=True,
        reduced_daily_rests_used=3,
    )
    route: list[RouteItem] = [Drive(499), Stop("X", earliest=1204)]
    res = plan(route, state, R, POLICIES[preset], CLOCK)
    assert_legal(res, state, R, CLOCK)
    assert [b.subtype for b in res.blocks if b.kind == "daily_rest"] == ["split_part_2"]


@pytest.mark.parametrize("preset", sorted(POLICIES))
def test_ferry_port_rest_counterexample(preset: str) -> None:
    """Found by a 3000-example sweep in fix round 1 (the property above then hit the same class).

    At ferry b a rest on board would start after boarding with 539 min inside the 24 h period
    (540 needed), and a rest at the port followed by crossing as a break leaves too little of
    the next period for a rest; a rest at the port and a second one in the cabin is legal.
    """
    state = DriverState(last_daily_rest_end=-412, split_daily_rest_first_part_taken=True)
    route: list[RouteItem] = [
        Drive(73),
        Ferry("a", 236, sleeper_cabin=False, boarding_minutes=90),
        Ferry("b", 840, sleeper_cabin=True, boarding_minutes=90),
        Drive(5),
    ]
    res = plan(route, state, R, POLICIES[preset], CLOCK)
    assert_legal(res, state, R, CLOCK)


@pytest.mark.parametrize("preset", sorted(POLICIES))
def test_port_rest_before_a_last_long_crossing(preset: str) -> None:
    """Hit by the property: an 11 h rest on board would have 659 min inside the period.

    Legal: an 11 h rest at the port, boarding, then a second rest in the cabin.
    """
    state = DriverState(last_daily_rest_end=-691, reduced_daily_rests_used=3)
    route: list[RouteItem] = [Ferry("f", 811, sleeper_cabin=True, boarding_minutes=90), Drive(5)]
    res = plan(route, state, R, POLICIES[preset], CLOCK)
    assert_legal(res, state, R, CLOCK)
    assert [(b.kind, b.at) for b in res.blocks if b.kind == "daily_rest"] == [
        ("daily_rest", None),
        ("daily_rest", "f"),
    ]

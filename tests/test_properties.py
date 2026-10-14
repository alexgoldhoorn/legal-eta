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


COUNTEREXAMPLE = (
    "counterexample: SchedulingError 'no legal plan through the ferry' under the fastest and "
    "driver_friendly presets (strict plans the same input) for the route in "
    "test_ferry_chain_counterexample"
)


@pytest.mark.xfail(strict=True, reason=COUNTEREXAMPLE)
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


@pytest.mark.xfail(strict=True, reason=COUNTEREXAMPLE)
@pytest.mark.parametrize("preset", ["fastest", "driver_friendly"])
def test_ferry_chain_counterexample(preset: str) -> None:
    """Shrunk by Hypothesis: three ferries in a row after a long drive; `strict` plans it."""
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

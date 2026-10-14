from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from legaleta.clock import Clock
from legaleta.policy import PRESETS as POLICIES
from legaleta.policy import Policy
from legaleta.rules import PRESETS
from legaleta.scheduler import plan
from legaleta.state import DriverState
from legaleta.timeline import Drive, Stop
from tests.conftest import assert_legal

R = PRESETS["eu561"]
REDUCED_WN = Policy(extended_driving="never", daily_rest_type="reduced_when_needed")
EXTEND_WN = Policy(extended_driving="when_needed", daily_rest_type="regular")


@pytest.fixture
def clock(monday_0600: datetime, madrid: ZoneInfo) -> Clock:
    return Clock(monday_0600, madrid)


def test_break_taken_at_stop_when_due_soon(clock: Clock) -> None:
    route = [Drive(240), Stop("A", service_minutes=30, driver_works=True), Drive(100)]
    res = plan(route, DriverState(), R, POLICIES["fastest"], clock)
    brk = [b for b in res.blocks if b.kind == "break"]
    assert len(brk) == 1 and brk[0].at == "A" and brk[0].minutes == 45
    assert res.arrival == 240 + 30 + 45 + 100
    assert_legal(res, DriverState(), R, clock)


def test_no_break_at_stop_when_not_due(clock: Clock) -> None:
    route = [Drive(60), Stop("A", service_minutes=30, driver_works=True), Drive(100)]
    res = plan(route, DriverState(), R, POLICIES["fastest"], clock)
    assert not any(b.kind == "break" for b in res.blocks)


def test_reduced_rest_only_when_a_window_needs_it(clock: Clock) -> None:
    # 9 h of driving (t=585 at B), service 10, then the daily rest, then 60 min to C.
    # Regular rest: C at 595+660+60 = 1315. Reduced: 1195. C's window closes at 1195.
    windowed = [
        Drive(540),
        Stop("B", service_minutes=10, driver_works=True),
        Drive(60),
        Stop("C", service_minutes=10, driver_works=True, latest=595 + 540 + 60),
    ]
    res = plan(windowed, DriverState(), R, REDUCED_WN, clock)
    rest = [b for b in res.blocks if b.kind == "daily_rest"]
    assert rest and rest[0].subtype == "reduced"
    assert res.stops[1].lateness_minutes == 0
    assert_legal(res, DriverState(), R, clock)
    free = [
        Drive(540),
        Stop("B", service_minutes=10, driver_works=True),
        Drive(60),
        Stop("C", service_minutes=10, driver_works=True),
    ]
    res2 = plan(free, DriverState(), R, REDUCED_WN, clock)
    rest2 = [b for b in res2.blocks if b.kind == "daily_rest"]
    assert rest2 and rest2[0].subtype == "regular"


def test_extension_only_when_a_window_needs_it(clock: Clock) -> None:
    # at t=585 both the break and the 9 h cap bind; extend_day comes bundled with the
    # 45 min break: B at 690
    route = [Drive(600), Stop("B", service_minutes=10, driver_works=True, latest=700)]
    res = plan(route, DriverState(), R, EXTEND_WN, clock)
    assert not any(b.kind == "daily_rest" for b in res.blocks)
    assert res.stops[0].lateness_minutes == 0 and res.stops[0].arrival == 690
    assert_legal(res, DriverState(), R, clock)
    res2 = plan(
        [Drive(600), Stop("B", service_minutes=10, driver_works=True)],
        DriverState(),
        R,
        EXTEND_WN,
        clock,
    )
    assert any(b.kind == "daily_rest" for b in res2.blocks)


def test_lateness_is_minimised_not_avoided(clock: Clock) -> None:
    route = [Drive(300), Stop("A", service_minutes=10, latest=100), Drive(10)]
    res = plan(route, DriverState(), R, POLICIES["fastest"], clock)
    assert res.stops[0].lateness_minutes > 0
    assert_legal(res, DriverState(), R, clock)


def test_deterministic(clock: Clock) -> None:
    route = [
        Drive(500),
        Stop("A", service_minutes=40, driver_works=False, latest=700),
        Drive(300),
        Stop("B", service_minutes=30, driver_works=True),
        Drive(600),
        Stop("C", service_minutes=20),
    ]
    a = plan(route, DriverState(), R, POLICIES["driver_friendly"], clock)
    b = plan(route, DriverState(), R, POLICIES["driver_friendly"], clock)
    assert a.blocks == b.blocks and a.stops == b.stops


def test_lookahead_runs_in_milliseconds(clock: Clock) -> None:
    import time

    route: list = []  # type: ignore[type-arg]
    for i in range(50):
        route += [
            Drive(150),
            Stop(f"S{i}", service_minutes=30, driver_works=(i % 2 == 0), latest=400 * (i + 1)),
        ]
    t0 = time.perf_counter()
    res = plan(route, DriverState(), R, POLICIES["driver_friendly"], clock)
    assert time.perf_counter() - t0 < 2.0
    assert_legal(res, DriverState(), R, clock)


def test_extension_offered_when_the_break_binds_before_the_daily_cap(clock: Clock) -> None:
    # at t=555 the break binds with 30 min left before the 9 h cap; only an extension
    # (taken with that break) reaches B in its window: B at 690
    route = [
        Drive(240),
        Stop("A", service_minutes=45, driver_works=False),
        Drive(360),
        Stop("B", service_minutes=10, driver_works=True, latest=760),
    ]
    res = plan(route, DriverState(), R, POLICIES["driver_friendly"], clock)
    assert res.stops[1].lateness_minutes == 0
    assert not any(b.kind == "daily_rest" for b in res.blocks)
    assert_legal(res, DriverState(), R, clock)
    fast = plan(route, DriverState(), R, POLICIES["fastest"], clock)
    assert fast.stops[1].arrival == 690
    assert_legal(fast, DriverState(), R, clock)


# A horizon simulation that dead-ends marks its candidate infeasible; it never fails the plan.


def _decision_point(clock: Clock):  # type: ignore[no-untyped-def]
    from legaleta.scheduler import _Ctx, _Cursor, _road_choices

    ctx = _Ctx([Drive(600)], R, POLICIES["fastest"], clock)
    cur = _Cursor(t=270, state=DriverState(driving_since_break=270, driving_today=270), idx=0)
    cur.drive_left = 330
    return ctx, cur, _road_choices(ctx, cur)


def test_dead_end_horizon_excludes_the_candidate(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from legaleta import scheduler

    ctx, cur, choices = _decision_point(clock)
    names = [c.name for c in choices]
    assert "break_45" in names and len(names) > 1
    real_run = scheduler._run

    def run(ctx, sim, chooser, stop_budget=None):  # type: ignore[no-untyped-def]
        if sim.blocks[-1].kind == "break":
            raise scheduler.SchedulingError("dead end in the horizon")
        return real_run(ctx, sim, chooser, stop_budget)

    monkeypatch.setattr(scheduler, "_run", run)
    assert scheduler.lookahead_chooser(ctx, cur, choices).name != "break_45"


def test_every_horizon_dead_ends_falls_back_to_the_plain_rule(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from legaleta import scheduler

    ctx, cur, choices = _decision_point(clock)

    def run(ctx, sim, chooser, stop_budget=None):  # type: ignore[no-untyped-def]
        raise scheduler.SchedulingError("dead end in the horizon")

    monkeypatch.setattr(scheduler, "_run", run)
    expected = scheduler.plain_chooser(ctx, cur, choices)
    assert scheduler.lookahead_chooser(ctx, cur, choices) == expected

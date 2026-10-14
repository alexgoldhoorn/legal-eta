from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from legaleta.checker import validate
from legaleta.clock import Clock as _Clock
from legaleta.rules import RuleSet
from legaleta.scheduler import PlanResult
from legaleta.state import DriverState, replay
from legaleta.timeline import check_contiguous, summarize


@pytest.fixture
def madrid() -> ZoneInfo:
    return ZoneInfo("Europe/Madrid")


@pytest.fixture
def monday_0600(madrid: ZoneInfo) -> datetime:
    """Monday 2026-10-19 06:00 Europe/Madrid (CEST)."""
    return datetime(2026, 10, 19, 6, 0, tzinfo=madrid)


def assert_legal(result: PlanResult, state: DriverState, rules: RuleSet, clock: _Clock) -> None:
    """The scheduler/checker contract: every plan validates clean and is internally consistent."""
    check_contiguous(result.blocks, 0)
    violations = validate(result.blocks, state, rules, clock)
    assert violations == [], violations
    assert summarize(result.blocks)["total"] == result.arrival
    assert replay(state, result.blocks, rules, clock) == result.end_state
    for b in result.blocks:
        assert b.minutes > 0, b
        assert b.reason and b.article, b

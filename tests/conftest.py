from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture
def madrid() -> ZoneInfo:
    return ZoneInfo("Europe/Madrid")


@pytest.fixture
def monday_0600(madrid: ZoneInfo) -> datetime:
    """Monday 2026-10-19 06:00 Europe/Madrid (CEST)."""
    return datetime(2026, 10, 19, 6, 0, tzinfo=madrid)

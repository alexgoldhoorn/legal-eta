"""Shared types. A log minute is minutes since the request `start` (prehistory is negative)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["DRIVE", "WORK", "AVAIL", "REST"]
FindingClass = Literal[
    "VIOLATION",
    "CLAIM_MISMATCH",
    "MISSING_WARNING",
    "SPURIOUS_WARNING",
    "INTERPRETATION",
    "NON_STANDARD_RULES",
]
RestKind = Literal[
    "daily_regular",
    "daily_reduced",
    "daily_split_1",
    "daily_split_2",
    "weekly_regular",
    "weekly_reduced",
]


@dataclass(frozen=True)
class Label:
    block_index: int | None
    block_kind: str
    subtype: str | None = None
    at: str | None = None
    counts_as_rest: bool = False
    interruptions: tuple[int, ...] = ()  # minutes of each Art 9 interruption (no positions)
    in_vehicle: bool = False


@dataclass(frozen=True)
class Activity:
    kind: Kind
    start: int
    end: int
    label: Label

    @property
    def minutes(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class RestPeriod:
    start: int
    end: int
    kind: RestKind
    interruptions: tuple[int, ...] = ()


@dataclass(frozen=True)
class Finding:
    cls: FindingClass
    code: str
    message: str
    article: str | None = None
    minute: int | None = None
    block_index: int | None = None
    excess_minutes: int = 0


@dataclass(frozen=True)
class Switches:
    wait_counts_as_rest: bool = True
    overdue_start_restarts_period: bool = True
    window_reduced: bool = True

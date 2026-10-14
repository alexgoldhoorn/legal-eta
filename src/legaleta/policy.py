"""Scheduling preferences. Legality is never a preference."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from typing import Any

from legaleta.clock import parse_hhmm

# policy.rest_places: a stop is a tagged rest place when its `tags` contain this tag
REST_PLACE_TAG = "rest"

PREFERENCE_WEIGHTS: dict[str, float] = {
    "break_timing": 30.0,
    "split_breaks": 15.0,
    "rest_places": 60.0,
}

_ENUMS: dict[str, tuple[str, ...]] = {
    "break_timing": ("latest", "earliest_convenient", "balanced"),
    "split_breaks": ("never", "allow", "prefer"),
    "break_at_working_stop": ("before", "after"),
    "daily_rest_type": ("regular", "reduced_when_needed", "reduced_eager"),
    "split_daily_rest": ("never", "allow"),
    "extended_driving": ("never", "when_needed", "eager"),
    "rest_places": ("anywhere", "prefer_tagged", "tagged_only"),
    "weekly_rest_type": ("regular", "reduced_when_needed", "reduced_eager"),
}


@dataclass(frozen=True)
class RestWindow:
    earliest: int
    latest: int
    hard: bool = False
    weight: float = 1.0

    def contains(self, minute_of_day: int) -> bool:
        if self.earliest <= self.latest:
            return self.earliest <= minute_of_day <= self.latest
        return (
            minute_of_day >= self.earliest or minute_of_day <= self.latest
        )  # window across midnight


@dataclass(frozen=True)
class Policy:
    name: str = "fastest"
    overrides: Mapping[str, object] = field(default_factory=dict, compare=False)
    break_at_stop_lookahead_minutes: int = 60
    break_timing: str = "latest"
    earliest_convenient_after_minutes: int = 180
    split_breaks: str = "allow"
    break_at_working_stop: str = "after"
    daily_rest_type: str = "reduced_eager"  # spec deviation 5: fastest = defaults
    split_daily_rest: str = "allow"
    extended_driving: str = "eager"
    rest_places: str = "anywhere"
    rest_place_lookahead_minutes: int = 90
    rest_start_window: RestWindow | None = None
    extend_rest_to: int | None = None
    extend_rest_max_minutes: int = 360
    weekly_rest_type: str = "reduced_when_needed"
    wait_counts_as_rest: bool = True
    lateness_weight: float = 1.0
    arrival_weight: float = 0.1
    lookahead_stops: int = 3

    def __post_init__(self) -> None:
        for key, allowed in _ENUMS.items():
            if getattr(self, key) not in allowed:
                raise ValueError(f"{key} must be one of {allowed}")
        if self.lookahead_stops < 1:
            raise ValueError("lookahead_stops must be >= 1")
        for key in (
            "break_at_stop_lookahead_minutes",
            "earliest_convenient_after_minutes",
            "rest_place_lookahead_minutes",
            "extend_rest_max_minutes",
        ):
            if getattr(self, key) < 0:
                raise ValueError(f"{key} must be >= 0")
        if self.lateness_weight < 0 or self.arrival_weight < 0:
            raise ValueError("weights must be >= 0")


PRESETS: dict[str, Policy] = {
    "fastest": Policy(name="fastest"),
    "driver_friendly": Policy(
        name="driver_friendly",
        break_timing="balanced",
        rest_places="prefer_tagged",
        rest_start_window=RestWindow(19 * 60, 22 * 60, hard=False, weight=1.0),
        daily_rest_type="regular",
        extended_driving="when_needed",
    ),
    "strict": Policy(
        name="strict",
        split_breaks="never",
        daily_rest_type="regular",
        extended_driving="never",
        weekly_rest_type="regular",
        rest_places="tagged_only",
    ),
}


def _parse_window(value: object) -> RestWindow | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or "earliest" not in value or "latest" not in value:
        raise ValueError("rest_start_window must be null or {earliest, latest, hard?, weight?}")
    return RestWindow(
        earliest=parse_hhmm(str(value["earliest"])),
        latest=parse_hhmm(str(value["latest"])),
        hard=bool(value.get("hard", False)),
        weight=float(value.get("weight", 1.0)),
    )


def policy_from(preset: str = "fastest", overrides: Mapping[str, object] | None = None) -> Policy:
    if preset not in PRESETS:
        raise ValueError(f"unknown policy preset {preset!r}; known: {sorted(PRESETS)}")
    base = PRESETS[preset]
    ov = dict(overrides or {})
    known = {f.name for f in fields(base)} - {"name", "overrides"}
    parsed: dict[str, Any] = {}
    for key, value in ov.items():
        if key not in known:
            raise ValueError(f"unknown policy knob {key!r}")
        if key == "rest_start_window":
            parsed[key] = _parse_window(value)
        elif key == "extend_rest_to":
            parsed[key] = None if value is None else parse_hhmm(str(value))
        else:
            parsed[key] = value
    return replace(base, name=preset, overrides=ov, **parsed)

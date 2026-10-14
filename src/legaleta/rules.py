"""Numeric constants of Regulation (EC) 561/2006. Minutes unless stated."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace


@dataclass(frozen=True)
class RuleSet:
    name: str = "eu561"
    overrides: Mapping[str, object] = field(default_factory=dict, compare=False)
    # Art 7
    break_after_driving: int = 270
    break_full: int = 45
    break_split_first: int = 15
    break_split_second: int = 30
    # Art 6
    daily_driving: int = 540
    daily_driving_extended: int = 600
    extended_days_per_week: int = 2
    weekly_driving: int = 3360
    fortnight_driving: int = 5400
    # Art 4(g), 8(2), 8(4)
    daily_rest_regular: int = 660
    daily_rest_reduced: int = 540
    daily_rest_split_first: int = 180
    daily_rest_split_second: int = 540
    reduced_daily_rests_between_weekly: int = 3
    daily_window: int = 1440
    # Art 4(h), 8(6), 8(6b), 8(7), 8(8)
    weekly_rest_regular: int = 2700
    weekly_rest_reduced: int = 1440
    weekly_rest_deadline: int = 6 * 1440
    compensation_deadline_weeks: int = 3
    compensation_attach_min_rest: int = 540
    regular_weekly_rest_in_vehicle: bool = False
    # Art 9
    ferry_interruptions_max: int = 2
    ferry_interruption_total: int = 60
    ferry_weekly_rest_min_crossing: int = 480
    ferry_weekly_rest_needs_cabin: bool = True
    # Guidance
    min_break_portion: int = 15


PRESETS: dict[str, RuleSet] = {"eu561": RuleSet()}

_NOT_OVERRIDABLE = {"name", "overrides"}
# integer fields that count something and may be 0; every other integer field is minutes > 0
_COUNTS = {
    "extended_days_per_week",
    "reduced_daily_rests_between_weekly",
    "compensation_deadline_weeks",
    "ferry_interruptions_max",
}


def rules_from(preset: str = "eu561", overrides: Mapping[str, object] | None = None) -> RuleSet:
    """Build a RuleSet from a preset plus field overrides. Overrides are echoed in `.overrides`."""
    if preset not in PRESETS:
        raise ValueError(f"unknown rules preset {preset!r}; known: {sorted(PRESETS)}")
    base = PRESETS[preset]
    ov = dict(overrides or {})
    known = {f.name for f in fields(base)}
    for key, value in ov.items():
        if key in _NOT_OVERRIDABLE or key not in known:
            raise ValueError(f"unknown rule {key!r}")
        expected = type(getattr(base, key))
        if expected is bool and not isinstance(value, bool):
            raise ValueError(f"rule {key!r} must be a boolean")
        if expected is int and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError(f"rule {key!r} must be an integer number of minutes")
        if expected is int and key in _COUNTS and value < 0:  # type: ignore[operator]
            raise ValueError(f"rule {key!r} must be >= 0")
        if expected is int and key not in _COUNTS and value <= 0:  # type: ignore[operator]
            raise ValueError(f"rule {key!r} must be > 0 minutes")
    return replace(base, name=preset, overrides=ov, **ov)  # type: ignore[arg-type]

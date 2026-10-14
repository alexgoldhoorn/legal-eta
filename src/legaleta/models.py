# src/legaleta/models.py
"""JSON contract. The only module that imports Pydantic."""

from __future__ import annotations

from dataclasses import asdict
from datetime import tzinfo
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from legaleta import DISCLAIMER
from legaleta.clock import Clock
from legaleta.policy import Policy, policy_from
from legaleta.rules import RuleSet, rules_from
from legaleta.scheduler import PlanResult
from legaleta.state import DriverState, from_remaining, to_remaining
from legaleta.timeline import Block, Drive, Ferry, RouteItem, Stop


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DriveIn(_Strict):
    kind: Literal["drive"]
    minutes: int = Field(ge=0)


class WindowIn(_Strict):
    earliest: AwareDatetime | None = None
    latest: AwareDatetime | None = None

    @model_validator(mode="after")
    def _ordered(self) -> WindowIn:
        if self.earliest and self.latest and self.latest < self.earliest:
            raise ValueError("window latest is before earliest")
        return self


class StopIn(_Strict):
    kind: Literal["stop"]
    id: str = Field(min_length=1)
    service_minutes: int = Field(0, ge=0)
    driver_works: bool = True
    window: WindowIn | None = None
    rest_allowed: bool = True
    counts_as_rest: bool | None = None
    tags: list[str] = Field(default_factory=list)


class FerryIn(_Strict):
    kind: Literal["ferry"]
    id: str = Field(min_length=1)
    minutes: int = Field(gt=0)
    sleeper_cabin: bool = False
    boarding_minutes: int = Field(0, ge=0)


RouteItemIn = Annotated[DriveIn | StopIn | FerryIn, Field(discriminator="kind")]


class RawDriverIn(_Strict):
    driving_since_break: int = Field(0, ge=0)
    split_break_first_part_taken: bool = False
    driving_today: int = Field(0, ge=0)
    last_daily_rest_end: AwareDatetime | None = None
    split_daily_rest_first_part_taken: bool = False
    reduced_daily_rests_used: int = Field(0, ge=0)
    driving_this_week: int = Field(0, ge=0)
    driving_last_week: int = Field(0, ge=0)
    extended_days_used_this_week: int = Field(0, ge=0)
    last_weekly_rest_end: AwareDatetime | None = None
    last_weekly_rest_reduced: bool = False
    compensation_owed_minutes: int = Field(0, ge=0)
    compensation_deadline: AwareDatetime | None = None


class RemainingDriverIn(_Strict):
    remaining_drive_before_break: int
    remaining_drive_today: int
    remaining_drive_this_week: int
    remaining_drive_fortnight: int
    daily_rest_due_in: int
    weekly_rest_due_in: int
    split_break_first_part_taken: bool = False
    split_daily_rest_first_part_taken: bool = False
    reduced_daily_rests_used: int = Field(0, ge=0)
    extended_days_used_this_week: int = Field(0, ge=0)
    last_weekly_rest_reduced: bool = False
    compensation_owed_minutes: int = Field(0, ge=0)
    compensation_deadline_in: int | None = None


class DriverIn(_Strict):
    raw: RawDriverIn | None = None
    remaining: RemainingDriverIn | None = None


class RulesIn(_Strict):
    preset: str = "eu561"
    overrides: dict[str, Any] = Field(default_factory=dict)


class PolicyIn(_Strict):
    preset: str = "fastest"
    overrides: dict[str, Any] = Field(default_factory=dict)


class _Timed(_Strict):
    start: AwareDatetime
    week_timezone: str | None = None
    driver: DriverIn | None = None
    rules: RulesIn = Field(default_factory=RulesIn)


class PlanRequest(_Timed):
    crew: int = 1
    route: list[RouteItemIn] = Field(min_length=1)
    policy: PolicyIn = Field(default_factory=PolicyIn)

    @model_validator(mode="after")
    def _single_driver(self) -> PlanRequest:
        if self.crew != 1:
            raise ValueError(
                "crew: only single-driver operation is supported (multi-manning is not modelled)"
            )
        return self


class BlockIn(_Strict):
    kind: str
    start: AwareDatetime
    minutes: int = Field(ge=0)
    subtype: str | None = None
    at: str | None = None
    counts_as_rest: bool = False
    interruptions: list[int] = Field(default_factory=list)
    compensation_minutes: int = Field(0, ge=0)
    in_vehicle: bool = False

    @model_validator(mode="after")
    def _compensation_within_block(self) -> BlockIn:
        if self.compensation_minutes > self.minutes:
            raise ValueError("compensation_minutes exceeds the block's minutes")
        return self


class ValidateRequest(_Timed):
    blocks: list[BlockIn]


class ProjectRequest(_Timed):
    minutes: int | None = Field(None, ge=0)
    until: AwareDatetime | None = None
    activity: Literal["driving", "resting", "other_work"]

    @model_validator(mode="after")
    def _one_horizon(self) -> ProjectRequest:
        if (self.minutes is None) == (self.until is None):
            raise ValueError("give exactly one of minutes or until")
        return self


# ---------------------------------------------------------------- conversions


def make_clock(req: _Timed) -> Clock:
    tz: tzinfo
    if req.week_timezone is None:
        assert req.start.tzinfo is not None
        tz = req.start.tzinfo
    else:
        try:
            tz = ZoneInfo(req.week_timezone)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"week_timezone: unknown zone {req.week_timezone!r}") from e
    return Clock(req.start, tz)


def make_rules(req: _Timed) -> RuleSet:
    return rules_from(req.rules.preset, req.rules.overrides)


def make_policy(req: PlanRequest) -> Policy:
    _check_policy_types(req.policy.overrides)
    try:
        return policy_from(req.policy.preset, req.policy.overrides)
    except TypeError as e:  # a wrongly typed knob that slipped past the check
        raise ValueError(f"policy.overrides: {e}") from e


_STRUCTURED_KNOBS = ("rest_start_window", "extend_rest_to")


def _check_policy_types(overrides: dict[str, Any]) -> None:
    """Knobs are typed after the defaults; the two structured ones are parsed by policy_from."""
    base = Policy()
    for key, value in overrides.items():
        if key in _STRUCTURED_KNOBS or not hasattr(base, key) or key in ("name", "overrides"):
            continue  # unknown keys are rejected by policy_from with their name
        expected = type(getattr(base, key))
        if expected is float:
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        else:
            ok = isinstance(value, expected) and (expected is bool or not isinstance(value, bool))
        if not ok:
            want = {int: "an integer", float: "a number", bool: "a boolean", str: "a string"}[
                expected
            ]
            raise ValueError(f"policy knob {key!r} must be {want}")


def make_state(
    driver: DriverIn | None, rules: RuleSet, clock: Clock
) -> tuple[DriverState, list[str]]:
    """Raw is canonical; the remaining form is converted; fields present in both must agree."""
    if driver is None or (driver.raw is None and driver.remaining is None):
        return DriverState(), []
    notes: list[str] = []
    if driver.remaining is not None:
        rem = driver.remaining.model_dump(exclude_none=True)
        state, notes = from_remaining(rem, rules, now=0)
        if driver.raw is not None:
            raw_state = _raw_to_state(driver.raw, clock)
            for name in driver.raw.model_fields_set:
                if getattr(raw_state, name) != getattr(state, name):
                    raise ValueError(f"driver.raw.{name} disagrees with driver.remaining")
        return state, notes
    assert driver.raw is not None
    return _raw_to_state(driver.raw, clock), notes


def _raw_to_state(raw: RawDriverIn, clock: Clock) -> DriverState:
    d = raw.model_dump()
    for key in ("last_daily_rest_end", "last_weekly_rest_end", "compensation_deadline"):
        v = d[key]
        d[key] = clock.to_t(v) if v is not None else (None if key == "compensation_deadline" else 0)
    return DriverState(**d)


def make_route(req: PlanRequest, clock: Clock) -> list[RouteItem]:
    out: list[RouteItem] = []
    for item in req.route:
        if isinstance(item, DriveIn):
            out.append(Drive(item.minutes))
        elif isinstance(item, StopIn):
            w = item.window
            out.append(
                Stop(
                    item.id,
                    item.service_minutes,
                    item.driver_works,
                    clock.to_t(w.earliest) if w and w.earliest else None,
                    clock.to_t(w.latest) if w and w.latest else None,
                    item.rest_allowed,
                    item.counts_as_rest,
                    tuple(item.tags),
                )
            )
        else:
            out.append(Ferry(item.id, item.minutes, item.sleeper_cabin, item.boarding_minutes))
    return out


def make_blocks(req: ValidateRequest, clock: Clock) -> list[Block]:
    return [
        Block(
            b.kind,
            clock.to_t(b.start),
            b.minutes,
            b.subtype,
            b.at,
            "",
            "",
            b.counts_as_rest,
            tuple(b.interruptions),
            b.compensation_minutes,
            b.in_vehicle,
        )
        for b in req.blocks
    ]


def iso(clock: Clock, t: int) -> str:
    return clock.local(t).isoformat(timespec="seconds")


def state_out(state: DriverState, rules: RuleSet, clock: Clock, now: int) -> dict[str, Any]:
    raw: dict[str, Any] = asdict(state)
    for key in ("last_daily_rest_end", "last_weekly_rest_end", "compensation_deadline"):
        raw[key] = iso(clock, raw[key]) if raw[key] is not None else None
    return {"raw": raw, "remaining": to_remaining(state, rules, now)}


def plan_out(result: PlanResult, clock: Clock, notes: list[str]) -> dict[str, Any]:
    from legaleta.timeline import summarize

    blocks = [
        {
            "kind": b.kind,
            "start": iso(clock, b.start),
            "end": iso(clock, b.end),
            "minutes": b.minutes,
            "at": b.at,
            "subtype": b.subtype,
            "reason": b.reason,
            "article": b.article,
            "counts_as_rest": b.counts_as_rest,
            "interruptions": list(b.interruptions),
            "compensation_minutes": b.compensation_minutes,
        }
        for b in result.blocks
    ]
    warnings = [
        {"code": w.code, "message": w.message, "article": w.article} for w in result.warnings
    ]
    if notes:
        warnings.append(
            {"code": "STATE_FROM_REMAINING", "message": "; ".join(notes), "article": ""}
        )
    rules = result.rules
    effective = {k: v for k, v in rules.__dict__.items() if k not in ("name", "overrides")}
    return {
        "arrival": iso(clock, result.arrival),
        "blocks": blocks,
        "stops": [
            {
                "id": s.id,
                "arrival": iso(clock, s.arrival),
                "departure": iso(clock, s.departure),
                "lateness_minutes": s.lateness_minutes,
                "window_met": s.window_met,
                "rested_here": s.rested_here,
            }
            for s in result.stops
        ],
        "summary": summarize(result.blocks),
        "driver_state_at_end": state_out(result.end_state, rules, clock, result.arrival),
        "warnings": warnings,
        "violations": [],
        "rules": {"preset": rules.name, "overrides": dict(rules.overrides), "effective": effective},
        "policy": {"preset": result.policy.name, "overrides": dict(result.policy.overrides)},
        "disclaimer": DISCLAIMER,
    }

# src/legaleta/api.py
"""Dict in, dict out. Used by the CLI, the MCP server and the actor."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from legaleta import DISCLAIMER
from legaleta.checker import validate
from legaleta.models import (
    PlanRequest,
    ProjectRequest,
    ValidateRequest,
    iso,
    make_blocks,
    make_clock,
    make_policy,
    make_route,
    make_rules,
    make_state,
    plan_out,
    state_out,
)
from legaleta.scheduler import SchedulingError, plan
from legaleta.state import project, replay


class RequestError(ValueError):
    """Invalid request (the caller's fault). Exit code 2 / HTTP 422."""


def _fmt(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "request"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def plan_json(request: dict[str, Any]) -> dict[str, Any]:
    try:
        req = PlanRequest.model_validate(request)
        clock = make_clock(req)
        rules = make_rules(req)
        policy = make_policy(req)
        state, notes = make_state(req.driver, rules, clock)
        route = make_route(req, clock)
    except ValidationError as e:
        raise RequestError(_fmt(e)) from e
    except ValueError as e:
        raise RequestError(str(e)) from e
    result = plan(route, state, rules, policy, clock)
    violations = validate(result.blocks, state, rules, clock)
    if violations:
        raise SchedulingError(f"internal error: the plan did not validate: {violations[:3]}")
    return plan_out(result, clock, notes)


def validate_json(request: dict[str, Any]) -> dict[str, Any]:
    try:
        req = ValidateRequest.model_validate(request)
        clock = make_clock(req)
        rules = make_rules(req)
        state, _ = make_state(req.driver, rules, clock)
        blocks = make_blocks(req, clock)
        start = blocks[0].start if blocks else 0
        violations = validate(blocks, state, rules, clock, start=start)
    except ValidationError as e:
        raise RequestError(_fmt(e)) from e
    except ValueError as e:
        raise RequestError(str(e)) from e

    end_state = replay(state, blocks, rules, clock)
    end_t = blocks[-1].end if blocks else 0
    return {
        "violations": [
            {
                "article": v.article,
                "block_index": v.block_index,
                "message": v.message,
                "excess_minutes": v.excess_minutes,
            }
            for v in violations
        ],
        "driver_state_at_end": state_out(end_state, rules, clock, end_t),
        "disclaimer": DISCLAIMER,
    }


def project_json(request: dict[str, Any]) -> dict[str, Any]:
    try:
        req = ProjectRequest.model_validate(request)
        clock = make_clock(req)
        rules = make_rules(req)
        state, _ = make_state(req.driver, rules, clock)
        minutes = req.minutes if req.minutes is not None else clock.to_t(req.until)  # type: ignore[arg-type]
        if minutes < 0:
            raise ValueError("until is before start")
        res = project(state, minutes, req.activity, rules, clock)
    except ValidationError as e:
        raise RequestError(_fmt(e)) from e
    except ValueError as e:
        raise RequestError(str(e)) from e
    first = None
    if res.first_limit is not None:
        first = {
            "minutes_from_now": res.first_limit[0],
            "at": iso(clock, res.first_limit[0]),
            "limit": res.first_limit[1],
        }
    return {
        "state": state_out(res.state, rules, clock, minutes),
        "envelope": dict(res.envelope.__dict__),
        "first_limit": first,
        "disclaimer": DISCLAIMER,
    }


def plan_schema() -> dict[str, Any]:
    return PlanRequest.model_json_schema()

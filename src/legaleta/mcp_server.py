# src/legaleta/mcp_server.py
"""MCP server exposing plan/validate/project. Run with `legaleta-mcp` (stdio)."""

from __future__ import annotations

from typing import Any

from legaleta import DISCLAIMER
from legaleta.api import RequestError, plan_json, plan_schema, project_json, validate_json
from legaleta.scheduler import SchedulingError

try:  # mcp >= 2: FastMCP was renamed MCPServer
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef,attr-defined]

server = _Server(
    "legaleta",
    instructions=(
        "EU driving-time scheduler for single-driver trucks under Regulation (EC) 561/2006. "
        "Give plan_route a route of drives (minutes), stops (service time, windows, whether the "
        "driver works) and ferries; it returns the schedule with breaks and rests, the arrival "
        "time, lateness per stop and the article behind every block. "
        f"{DISCLAIMER}"
    ),
)


def _guard(fn: Any, request: dict[str, Any]) -> dict[str, Any]:
    try:
        return fn(request)  # type: ignore[no-any-return]
    except (RequestError, SchedulingError) as e:
        return {"error": str(e), "disclaimer": DISCLAIMER}


@server.tool()
def plan_route(request: dict[str, Any]) -> dict[str, Any]:
    """Insert the legally required breaks and rests into a route; return the legal arrival time.

    `request` follows the legaleta plan schema: start (ISO datetime with offset), optional
    week_timezone, route (list of {kind: drive|stop|ferry, ...}), optional driver state (raw or
    tachograph 'remaining' form), optional rules {preset, overrides} and policy
    {preset: fastest|driver_friendly|strict, overrides}.
    Returns blocks with reasons and articles, stops with lateness, warnings and the driver state
    at the end.
    Planning aid, not legal advice.
    """
    return _guard(plan_json, request)


@server.tool()
def validate_timeline(request: dict[str, Any]) -> dict[str, Any]:
    """Check a timeline of blocks (drive, break, daily_rest, weekly_rest, work, wait, ferry) against
    Regulation (EC) 561/2006 and list every violation with its article and block index.
    Planning aid, not legal advice.
    """
    return _guard(validate_json, request)


@server.tool()
def project_driver_state(request: dict[str, Any]) -> dict[str, Any]:
    """Project a driver state forward under one activity (driving, resting, other_work).

    Runs for `minutes` or `until` a datetime; returns the state, what is still allowed and the
    first limit hit.
    Planning aid, not legal advice.
    """
    return _guard(project_json, request)


@server.resource("legaleta://schema/plan")
def plan_request_schema() -> str:
    """JSON schema of the plan request."""
    import json

    return json.dumps(plan_schema(), indent=2)


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()

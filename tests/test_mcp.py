import asyncio
import json
from pathlib import Path
from typing import Any

from legaleta.mcp_server import server


def call(name: str, request: dict[str, Any]) -> dict[str, Any]:
    result = asyncio.run(server.call_tool(name, {"request": request}))
    assert result.structured_content is not None
    return dict(result.structured_content)


def test_tools_listed_with_disclaimer() -> None:
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {"plan_route", "validate_timeline", "project_driver_state"}
    for t in tools:
        assert "Planning aid, not legal advice." in (t.description or "")
        assert "request" in t.input_schema["properties"]


def test_plan_route_tool_runs() -> None:
    req = json.loads(Path("examples/plan_request.json").read_text())
    body = call("plan_route", req)
    assert body["violations"] == [] and body["disclaimer"]


def test_validate_and_project_tools_run() -> None:
    v = json.loads(Path("examples/validate_request.json").read_text())
    assert "violations" in call("validate_timeline", v)
    p = json.loads(Path("examples/project_request.json").read_text())
    assert "first_limit" in call("project_driver_state", p)


def test_plan_route_tool_reports_bad_request() -> None:
    body = call("plan_route", {"start": "2026-10-20T06:00:00+02:00", "route": []})
    assert "route" in body["error"] and body["disclaimer"]


def test_plan_route_tool_reports_impossible_state() -> None:
    req = {
        "start": "2026-10-20T06:00:00+02:00",
        "driver": {
            "raw": {
                "last_daily_rest_end": "2026-10-19T16:00:00+02:00",
                "reduced_daily_rests_used": 3,
            }
        },
        "route": [{"kind": "drive", "minutes": 60}],
    }
    body = call("plan_route", req)
    assert "admits no legal plan" in body["error"] and body["disclaimer"]


def test_schema_resource_and_main(monkeypatch: Any) -> None:
    from legaleta import mcp_server

    res = asyncio.run(server.read_resource("legaleta://schema/plan"))
    assert "route" in str(res)
    called: list[str] = []
    monkeypatch.setattr(server, "run", lambda transport: called.append(transport))
    mcp_server.main()
    assert called == ["stdio"]

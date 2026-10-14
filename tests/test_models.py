# tests/test_models.py
import json
from pathlib import Path

import pytest

from legaleta.api import RequestError, plan_json, plan_schema, project_json, validate_json

EXAMPLE = {
    "start": "2026-10-20T06:00:00+02:00",
    "week_timezone": "Europe/Madrid",
    "route": [
        {
            "kind": "stop",
            "id": "depot",
            "service_minutes": 30,
            "driver_works": True,
            "tags": ["depot"],
        },
        {"kind": "drive", "minutes": 310},
        {
            "kind": "stop",
            "id": "A",
            "service_minutes": 45,
            "driver_works": False,
            "window": {
                "earliest": "2026-10-20T11:00:00+02:00",
                "latest": "2026-10-20T13:00:00+02:00",
            },
            "rest_allowed": False,
        },
        {
            "kind": "ferry",
            "id": "f1",
            "minutes": 540,
            "sleeper_cabin": True,
            "boarding_minutes": 20,
        },
        {"kind": "drive", "minutes": 400},
        {"kind": "stop", "id": "B", "service_minutes": 60, "driver_works": True},
    ],
    "rules": {"preset": "eu561", "overrides": {"break_after_driving": 240}},
    "policy": {"preset": "driver_friendly", "overrides": {"split_breaks": "never"}},
}


def test_plan_round_trip_shape() -> None:
    out = plan_json(EXAMPLE)
    assert out["disclaimer"] == "Planning aid, not legal advice."
    assert out["violations"] == []
    assert out["arrival"].endswith("+02:00") or out["arrival"].endswith("+01:00")
    assert [s["id"] for s in out["stops"]] == ["depot", "A", "B"]
    assert set(out["summary"]) == {
        "driving",
        "break",
        "daily_rest",
        "weekly_rest",
        "work",
        "wait",
        "ferry",
        "total",
    }
    assert (
        out["rules"]["overrides"] == {"break_after_driving": 240}
        and out["rules"]["effective"]["break_after_driving"] == 240
    )
    assert out["policy"]["preset"] == "driver_friendly"
    assert {"raw", "remaining"} <= set(out["driver_state_at_end"])
    b0 = out["blocks"][0]
    assert {"kind", "start", "end", "minutes", "at", "subtype", "reason", "article"} <= set(b0)
    assert json.dumps(out)  # serialisable


def test_default_timezone_is_the_start_offset() -> None:
    req = {**EXAMPLE, "start": "2026-10-20T06:00:00+02:00"}
    req.pop("week_timezone")
    assert plan_json(req)["blocks"][0]["start"].endswith("+02:00")


def test_driver_remaining_form_and_agreement() -> None:
    rem = {
        "remaining_drive_before_break": 100,
        "remaining_drive_today": 200,
        "remaining_drive_this_week": 3000,
        "remaining_drive_fortnight": 5000,
        "daily_rest_due_in": 600,
        "weekly_rest_due_in": 5000,
    }
    out = plan_json({**EXAMPLE, "driver": {"remaining": rem}})
    assert "STATE_FROM_REMAINING" in {w["code"] for w in out["warnings"]}
    with pytest.raises(RequestError, match="driving_since_break disagrees"):
        plan_json({**EXAMPLE, "driver": {"remaining": rem, "raw": {"driving_since_break": 5}}})
    plan_json(
        {**EXAMPLE, "driver": {"remaining": rem, "raw": {"driving_since_break": 140}}}
    )  # agrees (EXAMPLE overrides break_after_driving to 240: 240 - 100 = 140)


def test_request_rejections() -> None:
    cases = [
        ({**EXAMPLE, "crew": 2}, "crew"),
        ({**EXAMPLE, "route": []}, "route"),
        ({**EXAMPLE, "route": [{"kind": "drive", "minutes": -5}]}, "minutes"),
        (
            {**EXAMPLE, "rules": {"preset": "eu561", "overrides": {"break_after_drivng": 1}}},
            "unknown rule",
        ),
        ({**EXAMPLE, "start": "2026-10-20T06:00:00"}, "start"),
        (
            {
                **EXAMPLE,
                "route": [
                    {
                        "kind": "stop",
                        "id": "X",
                        "window": {
                            "earliest": "2026-10-20T13:00:00+02:00",
                            "latest": "2026-10-20T11:00:00+02:00",
                        },
                    }
                ],
            },
            "latest",
        ),
        (
            {**EXAMPLE, "policy": {"preset": "fastest", "overrides": {"break_timing": "soon"}}},
            "break_timing",
        ),
        ({**EXAMPLE, "week_timezone": "Mars/Olympus"}, "week_timezone"),
    ]
    for req, needle in cases:
        with pytest.raises(RequestError, match=needle):
            plan_json(req)


def test_validate_json_reports_violation() -> None:
    req = {
        "start": "2026-10-19T06:00:00+02:00",
        "blocks": [{"kind": "drive", "start": "2026-10-19T06:00:00+02:00", "minutes": 300}],
    }
    out = validate_json(req)
    assert out["violations"][0]["article"] == "Art 7" and out["violations"][0]["block_index"] == 0
    assert out["violations"][0]["excess_minutes"] == 30


def test_project_json_until_and_minutes() -> None:
    base = {
        "start": "2026-10-19T06:00:00+02:00",
        "driver": {"raw": {"driving_since_break": 200}},
        "activity": "driving",
    }
    a = project_json({**base, "minutes": 120})
    b = project_json({**base, "until": "2026-10-19T08:00:00+02:00"})
    assert a == b
    assert a["first_limit"] == {
        "minutes_from_now": 70,
        "at": "2026-10-19T07:10:00+02:00",
        "limit": "break",
    }
    with pytest.raises(RequestError, match="minutes or until"):
        project_json(base)


def test_plan_schema_is_json_schema() -> None:
    s = plan_schema()
    assert s["type"] == "object" and "route" in s["properties"]


def test_example_file_matches_readme(tmp_path: Path) -> None:
    p = Path("examples/plan_request.json")
    assert p.exists(), "write examples/plan_request.json with the EXAMPLE request"
    assert json.loads(p.read_text())["route"][0]["id"] == "depot"


def test_more_rejections() -> None:
    bad_block = {
        "kind": "break",
        "start": "2026-10-19T06:00:00+02:00",
        "minutes": 45,
        "compensation_minutes": 60,
    }
    p = "2026-10-19T06:00:00+02:00"
    cases = [
        (lambda: validate_json({"start": p, "blocks": [bad_block]}), "compensation_minutes"),
        (
            lambda: plan_json(
                {**EXAMPLE, "policy": {"preset": "fastest", "overrides": {"lookahead_stops": "a"}}}
            ),
            "lookahead_stops",
        ),
        (
            lambda: plan_json(
                {**EXAMPLE, "policy": {"preset": "fastest", "overrides": {"arrival_weight": True}}}
            ),
            "arrival_weight",
        ),
        (
            lambda: plan_json(
                {
                    **EXAMPLE,
                    "policy": {"preset": "fastest", "overrides": {"wait_counts_as_rest": 1}},
                }
            ),
            "wait_counts_as_rest",
        ),
        (
            lambda: plan_json(
                {**EXAMPLE, "policy": {"preset": "fastest", "overrides": {"nope": 1}}}
            ),
            "nope",
        ),
        (lambda: plan_json({**EXAMPLE, "policy": {"preset": "zzz"}}), "preset"),
        (lambda: plan_json({**EXAMPLE, "extra": 1}), "extra"),
        (
            lambda: project_json(
                {
                    "start": p,
                    "activity": "driving",
                    "minutes": 5,
                    "until": "2026-10-19T08:00:00+02:00",
                }
            ),
            "minutes or until",
        ),
        (
            lambda: project_json(
                {"start": p, "activity": "driving", "until": "2026-10-19T05:00:00+02:00"}
            ),
            "before start",
        ),
        (
            lambda: plan_json({**EXAMPLE, "driver": {"remaining": {"remaining_drive_today": 1}}}),
            "remaining",
        ),
    ]
    for call, needle in cases:
        with pytest.raises(RequestError, match=needle):
            call()


def test_policy_structured_overrides_and_timezone() -> None:
    overrides = {
        "rest_start_window": {"earliest": "19:00", "latest": "22:00"},
        "extend_rest_to": "07:00",
        "arrival_weight": 1,
    }
    out = plan_json(
        {**EXAMPLE, "week_timezone": "UTC", "policy": {"preset": "fastest", "overrides": overrides}}
    )
    assert out["blocks"][0]["start"].endswith("+00:00")
    assert out["policy"]["overrides"]["extend_rest_to"] == "07:00"


def test_warnings_round_trip_whatever_the_scheduler_emits() -> None:
    out = plan_json(EXAMPLE)
    assert out["warnings"], "the standing warnings are always present"
    for w in out["warnings"]:
        assert set(w) == {"code", "message", "article"}


def test_driver_raw_dates_and_state_out() -> None:
    raw = {
        "driving_since_break": 60,
        "last_daily_rest_end": "2026-10-20T00:00:00+02:00",
        "last_weekly_rest_end": "2026-10-17T00:00:00+02:00",
        "compensation_owed_minutes": 120,
        "compensation_deadline": "2026-11-01T00:00:00+01:00",
    }
    out = plan_json({**EXAMPLE, "driver": {"raw": raw}})
    assert isinstance(out["driver_state_at_end"]["raw"]["last_daily_rest_end"], str) and json.dumps(
        out
    )


def test_validate_and_project_with_driver_forms() -> None:
    p = "2026-10-19T06:00:00+02:00"
    rem = {
        "remaining_drive_before_break": 100,
        "remaining_drive_today": 200,
        "remaining_drive_this_week": 3000,
        "remaining_drive_fortnight": 5000,
        "daily_rest_due_in": 600,
        "weekly_rest_due_in": 5000,
    }
    assert (
        validate_json({"start": p, "driver": {"remaining": rem}, "blocks": []})["violations"] == []
    )
    assert (
        project_json(
            {"start": p, "driver": {"remaining": rem}, "activity": "resting", "minutes": 700}
        )["first_limit"]
        is None
    )
    assert project_json({"start": p, "activity": "other_work", "minutes": 10})["envelope"]


@pytest.mark.parametrize(
    ("name", "fn"),
    [
        ("plan_request.json", plan_json),
        ("validate_request.json", validate_json),
        ("project_request.json", project_json),
    ],
)
def test_examples_run(name: str, fn) -> None:  # type: ignore[no-untyped-def]
    out = fn(json.loads((Path("examples") / name).read_text()))
    assert out["disclaimer"] == "Planning aid, not legal advice."


DRIVE = {"kind": "drive", "minutes": 60}
STOP = {"kind": "stop", "id": "A"}


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        ({"route": [STOP, DRIVE, STOP]}, "duplicate route item id 'A'"),
        ({"route": [{"kind": "drive", "minutes": 0}]}, "route is empty"),
        ({"rules": {"overrides": {"break_full": -5}}}, "'break_full' must be > 0"),
        ({"rules": {"overrides": {"daily_window": 0}}}, "'daily_window' must be > 0"),
        (
            {
                "policy": {
                    "overrides": {
                        "rest_start_window": {"earliest": "20:00", "latest": "22:00", "hard": "no"}
                    }
                }
            },
            "hard must be a boolean",
        ),
    ],
)
def test_construction_errors_are_request_errors(change: dict[str, object], needle: str) -> None:
    # I8: these used to escape as a bare ValueError (CLI exit 1, "internal error")
    req = {"start": "2026-10-20T06:00:00+02:00", "route": [DRIVE], **change}
    with pytest.raises(RequestError, match=needle):
        plan_json(req)

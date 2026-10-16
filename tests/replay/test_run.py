import copy
import json
from pathlib import Path

from legaleta.api import plan_json
from replay.report import render_json, render_text
from replay.run import replay_plan
from replay.types import Switches

ROOT = Path(__file__).resolve().parents[2]
START = "2026-10-19T06:00:00+02:00"


def _plan(route: list, **extra):  # type: ignore[no-untyped-def, type-arg]
    req = {"start": START, "route": route, **extra}
    return req, plan_json(req)


def _codes(report) -> list[str]:  # type: ignore[no-untyped-def]
    return [f.code for f in report.findings]


SIMPLE = [
    {"kind": "drive", "minutes": 310},
    {"kind": "stop", "id": "A", "service_minutes": 45, "driver_works": False},
    {"kind": "drive", "minutes": 400},
    {"kind": "stop", "id": "B", "service_minutes": 60},
]


def test_clean_plan_passes() -> None:
    req, resp = _plan(SIMPLE)
    report = replay_plan(req, resp)
    assert report.verdict == "PASS", render_text(report)
    assert report.claims_checked > 10


def test_example_request_passes() -> None:
    req = json.loads((ROOT / "examples" / "plan_request.json").read_text())
    report = replay_plan(req, plan_json(req))
    assert report.verdict == "PASS", render_text(report)


def _tamper(resp: dict, fn):  # type: ignore[no-untyped-def, type-arg]
    out = copy.deepcopy(resp)
    fn(out)
    return out


def test_wrong_arrival_flagged() -> None:
    req, resp = _plan(SIMPLE)
    bad = _tamper(resp, lambda r: r.update(arrival="2026-10-20T09:00:00+02:00"))
    assert "ARRIVAL" in _codes(replay_plan(req, bad))


def test_wrong_lateness_flagged() -> None:
    route = [
        {"kind": "drive", "minutes": 100},
        {
            "kind": "stop",
            "id": "A",
            "window": {"earliest": START, "latest": "2026-10-19T09:00:00+02:00"},
        },
    ]
    req, resp = _plan(route)
    bad = _tamper(resp, lambda r: r["stops"][0].update(lateness_minutes=30, window_met=False))
    assert "STOP_LATENESS" in _codes(replay_plan(req, bad))


def test_summary_total_mismatch_flagged() -> None:
    req, resp = _plan(SIMPLE)
    bad = _tamper(resp, lambda r: r["summary"].update(total=r["summary"]["total"] + 5))
    assert "SUMMARY_TOTAL" in _codes(replay_plan(req, bad))


def test_end_state_remaining_mismatch_flagged() -> None:
    req, resp = _plan(SIMPLE)
    bad = _tamper(
        resp, lambda r: r["driver_state_at_end"]["remaining"].update(remaining_drive_today=1)
    )
    assert "END_REMAINING_REMAINING_DRIVE_TODAY" in _codes(replay_plan(req, bad))


def test_missing_warning_flagged() -> None:
    req, resp = _plan(SIMPLE)
    bad = _tamper(
        resp,
        lambda r: r.update(warnings=[w for w in r["warnings"] if w["code"] != "WTD_NOT_CHECKED"]),
    )
    assert "MISSING_WTD_NOT_CHECKED" in _codes(replay_plan(req, bad))


def test_rest_violation_in_a_tampered_plan_is_a_violation() -> None:
    req, resp = _plan(SIMPLE)

    def drop_break(r: dict) -> None:  # type: ignore[type-arg]
        i = next(i for i, b in enumerate(r["blocks"]) if b["kind"] == "break")
        r["blocks"][i]["kind"] = "wait"  # a break that is not a rest

    bad = _tamper(resp, drop_break)
    report = replay_plan(req, bad, Switches(wait_counts_as_rest=False))
    assert report.verdict == "FAIL"


def test_negative_remaining_start() -> None:
    req, resp = _plan(
        SIMPLE,
        driver={
            "remaining": {
                "remaining_drive_before_break": 100,
                "remaining_drive_today": -30,
                "remaining_drive_this_week": 2000,
                "remaining_drive_fortnight": 4000,
                "daily_rest_due_in": 700,
                "weekly_rest_due_in": 6000,
            }
        },
    )
    report = replay_plan(req, resp)
    assert report.verdict == "PASS", render_text(report)


def test_interpretation_reclassified() -> None:
    """Legal only because idle time counts as rest: the strict reading is INTERPRETATION."""
    route = [
        {"kind": "drive", "minutes": 200},
        {
            "kind": "stop",
            "id": "A",
            "service_minutes": 30,
            "driver_works": False,
            "window": {
                "earliest": "2026-10-19T14:00:00+02:00",
                "latest": "2026-10-19T16:00:00+02:00",
            },
        },
        {"kind": "drive", "minutes": 700},
    ]
    req, resp = _plan(route)
    default = replay_plan(req, resp)
    assert default.verdict == "PASS", render_text(default)
    strict = replay_plan(req, resp, Switches(wait_counts_as_rest=False))
    classes = {f.cls for f in strict.findings}
    assert "INTERPRETATION" in classes and "VIOLATION" not in classes, render_text(strict)


def test_report_text_names_article_and_minutes() -> None:
    req, resp = _plan(SIMPLE)
    bad = _tamper(resp, lambda r: r["summary"].update(total=0))
    text = render_text(replay_plan(req, bad))
    assert text.startswith("FAIL") and "SUMMARY_TOTAL" in text
    assert render_json(replay_plan(req, bad))["verdict"] == "FAIL"

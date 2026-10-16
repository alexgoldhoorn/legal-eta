"""Findings of the final review: each of these let an illegal or undisclosed plan PASS."""

from dataclasses import fields

from legaleta.api import plan_json
from replay.judge import judge
from replay.judge_rest import check_rests, recognise_rests
from replay.log import ActivityLog
from replay.regs import Regs
from replay.run import replay_plan
from replay.start import prehistory
from replay.types import Switches
from tests.replay.helpers import MONDAY, TZ, act, counters, weeks

SW = Switches()
START = "2026-10-19T06:00:00+02:00"


def _rest(acts, c=None, ferries=None):  # type: ignore[no-untyped-def]
    c = c or counters()
    rests = recognise_rests(acts, Regs(), SW, c.daily_rest_end)
    return check_rests(acts, rests, Regs(), SW, c, weeks(), ferries)


def _codes(findings) -> list[str]:  # type: ignore[no-untyped-def]
    return [f.code for f in findings if f.cls == "VIOLATION"]


def test_c1_weekly_rest_must_also_start_inside_the_24h_period() -> None:
    """Art 8(2): the new rest must be taken within 24 h of the last one; a weekly rest too."""
    late = [act("WORK", 0, 1400), act("REST", 1400, 4100, "weekly_rest")]
    assert "DAILY_REST_LATE" in _codes(_rest(late))
    ok = [act("WORK", 0, 800), act("REST", 800, 3500, "weekly_rest")]
    assert "DAILY_REST_LATE" not in _codes(_rest(ok))


def test_c2_split_rest_must_have_nine_hours_inside_the_24h_period() -> None:
    """Art 8(2): the part of the rest inside the 24 h period must be at least nine hours."""
    acts = [
        act("WORK", 0, 300),
        act("REST", 300, 480),
        act("WORK", 480, 1400),
        act("REST", 1400, 1940),
    ]
    assert [r.kind for r in recognise_rests(acts, Regs(), SW, 0)] == [
        "daily_split_1",
        "daily_split_2",
    ]
    assert "DAILY_REST_LATE" in _codes(_rest(acts))
    fine = [
        act("WORK", 0, 300),
        act("REST", 300, 480),
        act("WORK", 480, 800),
        act("REST", 800, 1340),
    ]
    assert "DAILY_REST_LATE" not in _codes(_rest(fine))


def test_i1_a_ten_hour_day_counts_when_the_start_state_holds_only_some_of_it() -> None:
    """Art 6(1): the plan that pushes today past 9 h uses an extended day."""
    req = {
        "start": START,
        "driver": {
            "raw": {
                "driving_today": 300,
                "last_daily_rest_end": "2026-10-18T23:20:00+02:00",
                "extended_days_used_this_week": 2,
            }
        },
    }
    regs = Regs()
    pre, c, _ = prehistory(req, regs, SW)
    acts = [
        act("DRIVE", 0, 270),
        act("REST", 270, 315),
        act("DRIVE", 315, 345),
        act("REST", 345, 1005),
    ]
    got = judge(ActivityLog(MONDAY.replace(hour=6), acts), pre, c, regs, SW, TZ)
    assert "EXTENDED_DAY_LIMIT" in _codes(got)


def test_i2_a_missing_warning_is_never_excused_by_a_switch() -> None:
    """The assumption must be disclosed; flipping the assumption cannot excuse the omission."""
    route = [
        {"kind": "drive", "minutes": 310},
        {"kind": "stop", "id": "A", "service_minutes": 45, "driver_works": False},
        {"kind": "drive", "minutes": 400},
    ]
    req = {"start": START, "route": route}
    resp = plan_json(req)
    assert any(w["code"] == "WAIT_ASSUMED_REST" for w in resp["warnings"])
    resp["warnings"] = [w for w in resp["warnings"] if w["code"] != "WAIT_ASSUMED_REST"]
    report = replay_plan(req, resp)
    assert report.verdict == "FAIL"
    assert any(
        f.code == "MISSING_WAIT_ASSUMED_REST" and f.cls == "MISSING_WARNING"
        for f in report.findings
    )


def test_i3_compensation_is_en_bloc_only() -> None:
    """Art 8(6b): "taken en bloc": there is no partial-payment reading to switch to."""
    assert "compensation_en_bloc" not in {f.name for f in fields(Switches)}


def test_i4_an_interrupted_rest_needs_a_cabin() -> None:
    """Art 9(1): during the interrupted rest "the driver shall have access to a sleeper cabin"."""
    rest = [act("REST", 0, 720, "daily_rest", at="F", interruptions=(30,))]
    assert "INTERRUPTION_NO_CABIN" not in _codes(_rest(rest, ferries={"F": (600, True)}))
    assert "INTERRUPTION_NO_CABIN" in _codes(_rest(rest, ferries={"F": (600, False)}))
    assert "INTERRUPTION_NO_CABIN" in _codes(_rest(rest))


def test_i5_a_pass_that_rests_on_an_assumption_says_so() -> None:
    """A PASS that is legal only because waits count as rest carries an INTERPRETATION note."""
    route = [
        {"kind": "drive", "minutes": 310},
        {"kind": "stop", "id": "A", "service_minutes": 45, "driver_works": False},
        {"kind": "drive", "minutes": 400},
    ]
    req = {"start": START, "route": route}
    report = replay_plan(req, plan_json(req))
    assert report.verdict == "PASS"
    notes = [f for f in report.findings if f.cls == "INTERPRETATION"]
    assert any("wait_counts_as_rest" in f.message for f in notes), report.findings

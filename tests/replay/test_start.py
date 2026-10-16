from replay.regs import Regs
from replay.start import prehistory
from replay.types import Switches

REQ = {"start": "2026-10-19T06:00:00+02:00"}


def _pre(driver: dict | None):  # type: ignore[no-untyped-def, type-arg]
    req = dict(REQ) if driver is None else {**REQ, "driver": driver}
    return prehistory(req, Regs(), Switches())


def _drive(acts) -> int:  # type: ignore[no-untyped-def]
    return sum(a.minutes for a in acts if a.kind == "DRIVE")


def test_fresh_driver_has_no_driving() -> None:
    acts, counters, findings = _pre(None)
    assert _drive(acts) == 0 and findings == []
    assert counters.driving_this_week_prior == 0 and counters.weekly_rest_end == 0


def test_raw_driving_today_laid_out() -> None:
    raw = {"driving_today": 300, "last_daily_rest_end": "2026-10-18T23:20:00+02:00"}
    acts, _, findings = _pre({"raw": raw})
    assert findings == []
    assert _drive(acts) == 300
    rest_end = -400
    after = [a for a in acts if a.label.subtype != "prehistory_rest"]
    assert all(a.start >= rest_end for a in after)
    ordered = sorted(acts, key=lambda a: a.start)
    assert all(x.end <= y.start for x, y in zip(ordered, ordered[1:], strict=False))


def test_driving_since_break_ends_at_start() -> None:
    raw = {
        "driving_today": 300,
        "driving_since_break": 120,
        "last_daily_rest_end": "2026-10-18T23:20:00+02:00",
    }
    acts, _, _ = _pre({"raw": raw})
    last_drive = max((a for a in acts if a.kind == "DRIVE"), key=lambda a: a.end)
    assert (last_drive.start, last_drive.end) == (-120, 0)


def _remaining(**kw: int) -> dict:  # type: ignore[type-arg]
    base = {
        "remaining_drive_before_break": 270,
        "remaining_drive_today": 540,
        "remaining_drive_this_week": 3360,
        "remaining_drive_fortnight": 5400,
        "daily_rest_due_in": 1440,
        "weekly_rest_due_in": 8640,
    }
    return {"remaining": {**base, **kw}}


def test_remaining_form_reads_9h_cap() -> None:
    acts, counters, _ = _pre(_remaining(remaining_drive_today=120, daily_rest_due_in=840))
    assert _drive(acts) == 420
    assert counters.driving_this_week_prior == 0


def test_negative_remaining_today() -> None:
    acts, _, findings = _pre(_remaining(remaining_drive_today=-30, daily_rest_due_in=440))
    assert findings == [] and _drive(acts) == 570


def test_remaining_week_and_fortnight_become_counters() -> None:
    _, c, _ = _pre(
        _remaining(
            remaining_drive_today=540,
            remaining_drive_this_week=3000,
            remaining_drive_fortnight=4000,
        )
    )
    assert c.driving_this_week_prior == 360
    assert c.driving_last_week == 5400 - 4000 - 360


def test_unrepresentable_state_reports_finding() -> None:
    raw = {"driving_today": 900, "last_daily_rest_end": "2026-10-19T04:20:00+02:00"}
    acts, _, findings = _pre({"raw": raw})
    assert [f.code for f in findings] == ["STATE_UNREPRESENTABLE"]
    assert acts == []

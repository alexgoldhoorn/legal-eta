from replay.log import build_log
from replay.types import Switches

T0 = "2026-10-19T06:00:00+02:00"


def _block(kind: str, start_min: int, minutes: int, **kw: object) -> dict:  # type: ignore[type-arg]
    from datetime import datetime, timedelta

    s = datetime.fromisoformat(T0) + timedelta(minutes=start_min)
    e = s + timedelta(minutes=minutes)
    return {
        "kind": kind,
        "start": s.isoformat(),
        "end": e.isoformat(),
        "minutes": minutes,
        "at": None,
        "subtype": None,
        "counts_as_rest": False,
        "interruptions": [],
        **kw,
    }


def _resp(*blocks: dict) -> dict:  # type: ignore[type-arg]
    return {"blocks": list(blocks)}


REQ = {"start": T0}


def test_blocks_map_to_activities() -> None:
    log, findings = build_log(
        REQ,
        _resp(_block("drive", 0, 270), _block("break", 270, 45), _block("drive", 315, 60)),
        Switches(),
    )
    assert [a.kind for a in log.activities] == ["DRIVE", "REST", "DRIVE"]
    assert [a.start for a in log.activities] == [0, 270, 315]
    assert findings == []


def test_gap_is_a_finding() -> None:
    _, findings = build_log(
        REQ, _resp(_block("drive", 0, 100), _block("drive", 110, 50)), Switches()
    )
    assert [(f.code, f.excess_minutes) for f in findings] == [("BLOCK_GAP", 10)]


def test_overlap_is_a_finding() -> None:
    _, findings = build_log(
        REQ, _resp(_block("drive", 0, 100), _block("drive", 90, 50)), Switches()
    )
    assert [(f.code, f.excess_minutes) for f in findings] == [("BLOCK_OVERLAP", 10)]


def test_wait_respects_switch() -> None:
    resp = _resp(_block("wait", 0, 60, counts_as_rest=True))
    on, _ = build_log(REQ, resp, Switches())
    off, _ = build_log(REQ, resp, Switches(wait_counts_as_rest=False))
    assert on.activities[0].kind == "REST"
    assert off.activities[0].kind == "AVAIL"


def test_zero_minute_blocks_dropped() -> None:
    log, findings = build_log(
        REQ,
        _resp(_block("drive", 0, 10), _block("wait", 10, 0), _block("drive", 10, 5)),
        Switches(),
    )
    assert len(log.activities) == 2 and findings == []

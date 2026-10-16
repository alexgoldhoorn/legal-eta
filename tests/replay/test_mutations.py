"""Tamper with legal plans: the replay must notice. An oracle that never fails proves nothing."""

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

from legaleta.api import plan_json, validate_json
from replay.run import replay_plan

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [
    ROOT / "examples" / "plan_request.json",
    ROOT / "tests" / "fixtures" / "sim" / "benelux_V1.json",
    ROOT / "tests" / "fixtures" / "sim" / "iberia_V1.json",
    ROOT / "tests" / "fixtures" / "sim" / "mixed_V1.json",
    ROOT / "tests" / "fixtures" / "sim" / "eu_longhaul_V1.json",
]


def _retime(resp: dict, index: int, delta: int) -> None:  # type: ignore[type-arg]
    """Change block `index` by `delta` minutes and shift the rest, so blocks stay joined."""
    d = timedelta(minutes=delta)
    blocks = resp["blocks"]
    blocks[index]["minutes"] += delta
    blocks[index]["end"] = (datetime.fromisoformat(blocks[index]["end"]) + d).isoformat()
    for b in blocks[index + 1 :]:
        b["start"] = (datetime.fromisoformat(b["start"]) + d).isoformat()
        b["end"] = (datetime.fromisoformat(b["end"]) + d).isoformat()
    resp["arrival"] = (datetime.fromisoformat(resp["arrival"]) + d).isoformat()


def _find(resp, pred):  # type: ignore[no-untyped-def, type-arg]
    return next((i for i, b in enumerate(resp["blocks"]) if pred(resp["blocks"], i)), None)


def _mutants(resp: dict):  # type: ignore[no-untyped-def, type-arg]
    """(name, mutated response) for every rule mutation that applies to this plan."""
    out = []
    i = _find(resp, lambda bs, i: bs[i]["kind"] == "daily_rest")
    if i is not None:
        m = copy.deepcopy(resp)
        _retime(m, i, 500 - m["blocks"][i]["minutes"])  # a daily rest of 8 h 20
        out.append(("daily rest under 9 h", m))
        m = copy.deepcopy(resp)
        m["blocks"][i]["kind"] = "work"  # the rest never happened
        out.append(("daily rest dropped", m))
    i = _find(resp, lambda bs, i: bs[i]["kind"] == "weekly_rest")
    if i is not None:
        m = copy.deepcopy(resp)
        _retime(m, i, 1000 - m["blocks"][i]["minutes"])  # a weekly rest under 24 h
        out.append(("weekly rest under 24 h", m))
        m = copy.deepcopy(resp)
        m["blocks"][i]["kind"] = "work"  # the weekly rest never happened
        out.append(("weekly rest dropped", m))
    i = _find(resp, lambda bs, i: bs[i].get("subtype") == "split_part_2")
    if i is not None:
        m = copy.deepcopy(resp)
        _retime(m, i, 480 - m["blocks"][i]["minutes"])  # second part of a split rest under 9 h
        out.append(("split rest second part under 9 h", m))
    i = _find(resp, lambda bs, i: bs[i]["kind"] == "break" and bs[i]["minutes"] >= 45)
    if i is not None:
        m = copy.deepcopy(resp)
        m["blocks"][i]["kind"] = "work"  # the break became work
        out.append(("break dropped", m))
    i = _find(
        resp,
        lambda bs, i: (
            bs[i]["kind"] == "drive"
            and bs[i]["minutes"] == 270
            and i + 1 < len(bs)
            and bs[i + 1]["kind"] == "break"
        ),
    )
    if i is not None:
        m = copy.deepcopy(resp)
        _retime(m, i, 1)  # 271 minutes of driving before the break
        out.append(("drive 1 min past 4 h 30", m))
    return out


_REST_CODES = {
    "DAILY_REST_LATE",
    "DAILY_REST_MISSING",
    "DAILY_DRIVING_EXCEEDED",
    "EXTENDED_DAY_LIMIT",
    "BREAK_MISSING",
    "WEEKLY_REST_LATE",
    "WEEKLY_REST_MISSING",
    "WEEKLY_DRIVING_EXCEEDED",
    "FORTNIGHT_DRIVING_EXCEEDED",
}
EXPECTED = {  # the violation codes a mutation may legitimately cause
    "daily rest under 9 h": _REST_CODES,
    "daily rest dropped": _REST_CODES,
    "break dropped": {"BREAK_MISSING"},
    "drive 1 min past 4 h 30": {"BREAK_MISSING"},
    "weekly rest under 24 h": _REST_CODES,
    "weekly rest dropped": _REST_CODES,
    "split rest second part under 9 h": _REST_CODES,
}


def _legaleta_says_illegal(req: dict, resp: dict) -> bool:  # type: ignore[type-arg]
    keep = ("kind", "start", "minutes", "subtype", "at", "counts_as_rest", "interruptions")
    vreq = {k: req[k] for k in ("start", "driver", "rules") if k in req}
    vreq["blocks"] = [{k: b[k] for k in keep if k in b} for b in resp["blocks"]]
    return bool(validate_json(vreq)["violations"])


def test_rule_mutants_are_caught() -> None:
    caught, skipped, names = 0, 0, set()
    for path in SOURCES:
        req = json.loads(path.read_text())
        resp = plan_json(req)
        assert replay_plan(req, resp).verdict == "PASS", path.name
        for name, mutant in _mutants(resp):
            if not _legaleta_says_illegal(req, mutant):
                skipped += 1  # the mutation happens to leave a legal plan
                continue
            report = replay_plan(req, mutant)
            codes = {f.code for f in report.findings if f.cls == "VIOLATION"}
            assert codes & EXPECTED[name], (path.name, name, codes)
            caught += 1
            names.add(name)
    assert caught >= 8, (caught, skipped)
    assert skipped <= 0.3 * (caught + skipped), (caught, skipped)
    assert len(names) >= 5, names


def test_claim_mutants_are_caught() -> None:
    req = json.loads(SOURCES[1].read_text())
    resp = plan_json(req)
    cases = {
        "arrival": lambda r: r.update(arrival="2030-01-01T00:00:00+01:00"),
        "stop lateness": lambda r: r["stops"][0].update(
            lateness_minutes=r["stops"][0]["lateness_minutes"] + 999, window_met=False
        ),
        "summary": lambda r: r["summary"].update(driving=r["summary"]["driving"] + 1),
        "end state": lambda r: r["driver_state_at_end"]["raw"].update(driving_today=9999),
    }
    for name, tamper in cases.items():
        bad = copy.deepcopy(resp)
        tamper(bad)
        assert replay_plan(req, bad).verdict == "FAIL", name

# tests/test_sim_fixtures.py
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from legaleta.api import plan_json

FIX = Path(__file__).parent / "fixtures" / "sim"
SNAP = FIX / "expected.json"
FIXTURES = sorted(p for p in FIX.glob("*.json") if p.name != "expected.json")


def naive_minutes(req: dict) -> int:  # type: ignore[type-arg]
    """Drives + service + waiting for windows, no breaks or rests."""
    start = datetime.fromisoformat(req["start"])
    t = 0
    for item in req["route"]:
        if item["kind"] == "drive":
            t += item["minutes"]
        elif item["kind"] == "stop":
            w = item.get("window") or {}
            if w.get("earliest"):
                e = round((datetime.fromisoformat(w["earliest"]) - start).total_seconds() / 60)
                t = max(t, e)
            t += item.get("service_minutes", 0)
        else:
            t += item.get("boarding_minutes", 0) + item["minutes"]
    return t


@pytest.mark.parametrize("path", FIXTURES, ids=[p.stem for p in FIXTURES])
@pytest.mark.parametrize("preset", ["fastest", "driver_friendly", "strict"])
def test_fixture_plans_legally(path: Path, preset: str) -> None:
    req = json.loads(path.read_text())
    out = plan_json({**req, "policy": {"preset": preset}})
    assert out["violations"] == []


def test_snapshots() -> None:
    assert FIXTURES, "run scripts/export_sim_fixtures.py"
    got = {}
    rows = []
    for path in FIXTURES:
        req = json.loads(path.read_text())
        out = plan_json(req)
        start = datetime.fromisoformat(req["start"])
        legal = round((datetime.fromisoformat(out["arrival"]) - start).total_seconds() / 60)
        naive = naive_minutes(req)
        got[path.stem] = {
            "arrival": out["arrival"],
            "blocks": len(out["blocks"]),
            "legal_minutes": legal,
            "naive_minutes": naive,
        }
        rows.append((path.stem, naive, legal, round(100 * (legal - naive) / naive)))
    print("\nfixture naive_min legal_min gap%")
    for r in rows:
        print(f"{r[0]:<24} {r[1]:>8} {r[2]:>8} {r[3]:>5}")
    if os.environ.get("LEGALETA_UPDATE_SNAPSHOTS"):
        SNAP.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
    expected = json.loads(SNAP.read_text())
    assert got == expected, (
        "schedule changed: review the diff, then "
        "LEGALETA_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_sim_fixtures.py"
    )

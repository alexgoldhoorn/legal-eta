# scripts/export_sim_fixtures.py
"""Build tests/fixtures/sim/*.json from the freight-dispatch-simulator datasets.

Run: uv run python scripts/export_sim_fixtures.py
"""

from __future__ import annotations

import csv
import io
import json
import math
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

RAW = "https://raw.githubusercontent.com/alexgoldhoorn/freight-dispatch-simulator/main/data/{ds}/{f}.csv"
DATASETS = {
    "eu_longhaul": "Europe/Madrid",
    "benelux": "Europe/Brussels",
    "iberia": "Europe/Madrid",
    "mixed": "Europe/Madrid",
}
START = datetime(2026, 10, 19, 6, 0)
SERVICE_MIN = 30
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sim"


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def fetch(ds: str, name: str) -> list[dict[str, str]]:
    with urllib.request.urlopen(RAW.format(ds=ds, f=name), timeout=30) as r:  # noqa: S310
        return list(csv.DictReader(io.StringIO(r.read().decode())))


def minutes(km: float, speed: float) -> int:
    return max(1, round(km / speed * 60))


def build(ds: str, tz: str) -> dict[str, dict]:  # type: ignore[type-arg]
    freights = sorted(fetch(ds, "freights"), key=lambda f: float(f["pickup_time"]))
    vehicles = fetch(ds, "vehicles")
    start = START.replace(tzinfo=ZoneInfo(tz))
    # FCFS: each freight to the vehicle free earliest; per-vehicle state: free time, position, items
    state = {
        v["id"]: {
            "free": 0.0,
            "lat": float(v["start_lat"]),
            "lon": float(v["start_lon"]),
            "speed": float(v["speed_km_per_hour"]),
            "items": [],
            "n": 0,
        }
        for v in vehicles
    }
    for f in freights:
        vid = min(state, key=lambda k: (state[k]["free"], k))
        s = state[vid]
        plat, plon, dlat, dlon = (
            float(f["pickup_lat"]),
            float(f["pickup_lon"]),
            float(f["delivery_lat"]),
            float(f["delivery_lon"]),
        )
        to_pick = minutes(haversine(s["lat"], s["lon"], plat, plon), s["speed"])
        to_del = minutes(haversine(plat, plon, dlat, dlon), s["speed"])
        release = start + timedelta(seconds=float(f["pickup_time"]))
        due = start + timedelta(seconds=float(f["delivery_time"]))
        s["items"] += [
            {"kind": "drive", "minutes": to_pick},
            {
                "kind": "stop",
                "id": f"{f['id']}_pickup",
                "service_minutes": SERVICE_MIN,
                "driver_works": True,
                "window": {"earliest": release.isoformat()},
                "rest_allowed": False,
            },
            {"kind": "drive", "minutes": to_del},
            {
                "kind": "stop",
                "id": f"{f['id']}_delivery",
                "service_minutes": SERVICE_MIN,
                "driver_works": True,
                "window": {"latest": due.isoformat()},
                "rest_allowed": True,
            },
        ]
        s["free"] = (
            max(s["free"], float(f["pickup_time"])) + (to_pick + to_del + 2 * SERVICE_MIN) * 60
        )
        s["lat"], s["lon"], s["n"] = dlat, dlon, s["n"] + 1
    out = {}
    for v in vehicles:
        s = state[v["id"]]
        if s["n"] == 0:
            continue
        back = minutes(
            haversine(s["lat"], s["lon"], float(v["start_lat"]), float(v["start_lon"])), s["speed"]
        )
        items = s["items"] + [
            {"kind": "drive", "minutes": back},
            {"kind": "stop", "id": "base", "service_minutes": 0, "tags": ["depot"]},
        ]
        out[f"{ds}_{v['id']}"] = {
            "start": start.isoformat(),
            "week_timezone": tz,
            "route": items,
            "policy": {"preset": "fastest"},
        }
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for ds, tz in DATASETS.items():
        for name, req in build(ds, tz).items():
            (OUT / f"{name}.json").write_text(json.dumps(req, indent=1) + "\n")
            print("wrote", name, len(req["route"]), "items")


if __name__ == "__main__":
    main()

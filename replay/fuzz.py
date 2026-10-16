"""Seeded random plan requests. Pure: no legaleta import, deterministic in the seed."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

ZONES = ("Europe/Madrid", "Europe/Brussels")
FIRST_DAY = datetime(2026, 10, 19, 6, 0)  # a Monday; days 0 to 13 cover the DST change on Oct 25
START_DAYS = 14
ROUTE_ITEMS = (1, 30)
P_STOP, P_FERRY = 0.30, 0.10  # remaining probability is a drive
DRIVE_RANGES = ((5, 120), (60, 400), (300, 900))
SERVICE_CHOICES = (0, 0, 15, 30, 45, 60, 120, 600)
P_WINDOW, P_REST_TAG, P_NO_REST, P_IDLE_STOP = 0.30, 0.25, 0.10, 0.20
FERRY_MINUTES = (60, 900)
BOARDING_CHOICES = (0, 30, 60)
P_NO_DRIVER, P_RAW = 0.40, 0.50  # of the rest; remaining otherwise


def _iso(start: datetime, minutes: int) -> str:
    return (start + timedelta(minutes=minutes)).isoformat()


def _route(rng: random.Random, start: datetime) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for n in range(rng.randint(*ROUTE_ITEMS)):
        r = rng.random()
        if r < P_STOP:
            stop: dict[str, Any] = {"kind": "stop", "id": f"s{n}"}
            stop["service_minutes"] = rng.choice(SERVICE_CHOICES)
            if rng.random() < P_IDLE_STOP:
                stop["driver_works"] = False
            if rng.random() < P_WINDOW:
                earliest = rng.randint(0, 4000)
                stop["window"] = {
                    "earliest": _iso(start, earliest),
                    "latest": _iso(start, earliest + rng.randint(30, 600)),
                }
            if rng.random() < P_REST_TAG:
                stop["tags"] = ["rest"]
            if rng.random() < P_NO_REST:
                stop["rest_allowed"] = False
            items.append(stop)
        elif r < P_STOP + P_FERRY:
            items.append(
                {
                    "kind": "ferry",
                    "id": f"f{n}",
                    "minutes": rng.randint(*FERRY_MINUTES),
                    "sleeper_cabin": rng.random() < 0.5,
                    "boarding_minutes": rng.choice(BOARDING_CHOICES),
                }
            )
        else:
            items.append({"kind": "drive", "minutes": rng.randint(*rng.choice(DRIVE_RANGES))})
    if not any(i["kind"] == "drive" and i["minutes"] > 0 for i in items):
        items.append({"kind": "drive", "minutes": rng.randint(30, 300)})
    return items


def _raw_driver(rng: random.Random, start: datetime) -> dict[str, Any]:
    today = rng.randint(0, 600)
    since = rng.randint(0, min(today, 270))
    gap = min(1440, today + 135 + rng.randint(0, 300))
    raw: dict[str, Any] = {
        "driving_since_break": since,
        "driving_today": today,
        "last_daily_rest_end": _iso(start, -gap),
        "driving_this_week": rng.randint(today, 3360),
        "driving_last_week": rng.randint(0, 2400),
        "extended_days_used_this_week": rng.randint(0, 2),
        "reduced_daily_rests_used": rng.randint(0, 3),
        "last_weekly_rest_end": _iso(start, -rng.randint(gap, 8000)),
        "last_weekly_rest_reduced": rng.random() < 0.3,
    }
    if rng.random() < 0.3:
        raw["compensation_owed_minutes"] = rng.randint(60, 1000)
        raw["compensation_deadline"] = _iso(start, rng.randint(3000, 30000))
    return raw


def _remaining_driver(rng: random.Random) -> dict[str, Any]:
    return {
        "remaining_drive_before_break": rng.randint(0, 270),
        "remaining_drive_today": rng.randint(-60, 540),
        "remaining_drive_this_week": rng.randint(0, 3360),
        "remaining_drive_fortnight": rng.randint(0, 5400),
        "daily_rest_due_in": rng.randint(60, 1440),
        "weekly_rest_due_in": rng.randint(500, 8640),
    }


def generate(seed: int) -> dict[str, Any]:
    """A plan request for `seed`. Not every one is plannable; legaleta rejects some."""
    rng = random.Random(seed)
    zone = ZoneInfo(rng.choice(ZONES))
    local = FIRST_DAY + timedelta(days=rng.randrange(START_DAYS), minutes=15 * rng.randrange(96))
    start = local.replace(tzinfo=zone)
    req: dict[str, Any] = {"start": start.isoformat(), "route": _route(rng, start)}
    if rng.random() >= P_NO_DRIVER:
        if rng.random() < P_RAW:
            req["driver"] = {"raw": _raw_driver(rng, start)}
        else:
            req["driver"] = {"remaining": _remaining_driver(rng)}
    return req

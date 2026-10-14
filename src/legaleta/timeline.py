"""Blocks (what the driver does, minute by minute) and route items (what the caller asks for)."""

from __future__ import annotations

from dataclasses import dataclass

KINDS = ("drive", "break", "daily_rest", "weekly_rest", "work", "wait", "ferry")
REST_KINDS = ("daily_rest", "weekly_rest")
SUBTYPES = (None, "regular", "reduced", "split_part_1", "split_part_2", "extended")


@dataclass(frozen=True)
class Block:
    kind: str
    start: int
    minutes: int
    subtype: str | None = None
    at: str | None = None
    reason: str = ""
    article: str = ""
    counts_as_rest: bool = False  # wait/ferry: the time may count as break (Art 4(d))
    interruptions: tuple[int, ...] = ()  # Art 9: minutes of each interruption inside a rest
    compensation_minutes: int = 0  # Art 8(6b): part of this rest that repays a reduced weekly rest
    in_vehicle: bool = False  # Art 8(8): set by callers of validate(), never by the scheduler

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown block kind {self.kind!r}")
        if self.minutes < 0:
            raise ValueError("minutes must be >= 0")
        if self.subtype not in SUBTYPES:
            raise ValueError(f"unknown subtype {self.subtype!r}")
        if any(i <= 0 for i in self.interruptions):
            raise ValueError("interruptions must be positive minutes")
        if self.compensation_minutes < 0 or self.compensation_minutes > self.minutes:
            raise ValueError("compensation_minutes out of range")

    @property
    def end(self) -> int:
        return self.start + self.minutes

    @property
    def rest_minutes(self) -> int:
        return self.minutes - sum(self.interruptions)


@dataclass(frozen=True)
class Drive:
    minutes: int

    def __post_init__(self) -> None:
        if self.minutes < 0:
            raise ValueError("drive minutes must be >= 0")


@dataclass(frozen=True)
class Stop:
    id: str
    service_minutes: int = 0
    driver_works: bool = True
    earliest: int | None = None  # plan minutes
    latest: int | None = None
    rest_allowed: bool = True
    counts_as_rest: bool | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.service_minutes < 0:
            raise ValueError("service_minutes must be >= 0")
        if self.earliest is not None and self.latest is not None and self.latest < self.earliest:
            raise ValueError(f"stop {self.id!r}: window latest is before earliest")

    @property
    def idle_counts(self) -> bool:
        """Does idle time at this stop count as break/rest? (spec 4.4)"""
        if self.counts_as_rest is not None:
            return self.counts_as_rest
        return not self.driver_works


@dataclass(frozen=True)
class Ferry:
    id: str
    minutes: int
    sleeper_cabin: bool = False
    boarding_minutes: int = 0

    def __post_init__(self) -> None:
        if self.minutes <= 0:
            raise ValueError("ferry minutes must be > 0")
        if self.boarding_minutes < 0:
            raise ValueError("boarding_minutes must be >= 0")


RouteItem = Drive | Stop | Ferry


def merge_drives(route: list[RouteItem]) -> list[RouteItem]:
    """Merge consecutive drives, drop zero-minute drives; ids must be unique, route non-empty."""
    if not route:
        raise ValueError("route is empty")
    out: list[RouteItem] = []
    seen: set[str] = set()
    for item in route:
        if isinstance(item, Drive):
            if item.minutes == 0:
                continue
            last = out[-1] if out else None
            if isinstance(last, Drive):
                out[-1] = Drive(last.minutes + item.minutes)
            else:
                out.append(item)
        else:
            if item.id in seen:
                raise ValueError(f"duplicate route item id {item.id!r}")
            seen.add(item.id)
            out.append(item)
    if not out:
        raise ValueError("route is empty after dropping zero-minute drives")
    return out


def summarize(blocks: list[Block]) -> dict[str, int]:
    s = {
        "driving": 0,
        "break": 0,
        "daily_rest": 0,
        "weekly_rest": 0,
        "work": 0,
        "wait": 0,
        "ferry": 0,
    }
    for b in blocks:
        key = "driving" if b.kind == "drive" else b.kind
        s[key] += b.minutes
    s["total"] = sum(s.values())
    return s


def check_contiguous(blocks: list[Block], start: int = 0) -> None:
    t = start
    for i, b in enumerate(blocks):
        if b.start != t:
            what = "gap" if b.start > t else "overlap"
            raise ValueError(
                f"block {i} does not start at {t} ({what}); blocks start at {start}, contiguous"
            )
        t = b.end

"""Turn a plan's blocks into a flat activity log on an elapsed-minutes clock."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from replay.types import Activity, Finding, Kind, Label, Switches

_REST_BLOCKS = {"break", "daily_rest", "weekly_rest"}


@dataclass
class ActivityLog:
    origin: datetime
    activities: list[Activity] = field(default_factory=list)

    def minute_of(self, dt: datetime) -> int:
        return round((dt - self.origin).total_seconds() / 60)


def _kind(block: dict, sw: Switches) -> Kind:  # type: ignore[type-arg]
    bk = block["kind"]
    if bk == "drive":
        return "DRIVE"
    if bk == "work":
        return "WORK"
    counts = bool(block.get("counts_as_rest"))
    if bk == "break" and counts:  # idle service at a stop, counted as a break by assumption
        return "REST" if sw.wait_counts_as_rest else "AVAIL"
    if bk in _REST_BLOCKS:
        return "REST"
    if bk == "wait":
        return "REST" if counts and sw.wait_counts_as_rest else "AVAIL"
    return "REST" if counts else "AVAIL"  # ferry


def build_log(
    request: dict,  # type: ignore[type-arg]
    response: dict,  # type: ignore[type-arg]
    sw: Switches,
) -> tuple[ActivityLog, list[Finding]]:
    """Execute the plan's blocks as activities.

    Ferry blocks and waits count as REST only when the block says `counts_as_rest`. An Art 9
    interruption is given by plan_json as a list of minutes (one per interruption, no positions).
    Zero-minute blocks are dropped; gaps and overlaps between blocks are findings.
    """
    log = ActivityLog(origin=datetime.fromisoformat(request["start"]))
    findings: list[Finding] = []
    prev_end: int | None = None
    for i, b in enumerate(response["blocks"]):
        start = log.minute_of(datetime.fromisoformat(b["start"]))
        end = log.minute_of(datetime.fromisoformat(b["end"]))
        if end == start:
            continue
        if prev_end is not None and start != prev_end:
            gap = start - prev_end
            side = "after" if gap > 0 else "before"
            findings.append(
                Finding(
                    "CLAIM_MISMATCH",
                    "BLOCK_GAP" if gap > 0 else "BLOCK_OVERLAP",
                    f"block {i} starts {abs(gap)} min {side} the previous end",
                    minute=start,
                    block_index=i,
                    excess_minutes=abs(gap),
                )
            )
        label = Label(
            block_index=i,
            block_kind=b["kind"],
            subtype=b.get("subtype"),
            at=b.get("at"),
            counts_as_rest=bool(b.get("counts_as_rest")),
            interruptions=tuple(b.get("interruptions") or ()),
            in_vehicle=bool(b.get("in_vehicle")),
            compensation_minutes=int(b.get("compensation_minutes") or 0),
        )
        log.activities.append(Activity(_kind(b, sw), start, end, label))
        prev_end = end
    return log, findings

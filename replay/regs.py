"""Numbers of Regulation (EC) 561/2006 as amended by (EU) 2020/1054, in minutes.

Written from the regulation text; field names follow the public `rules.effective` keys of the plan
response so that overrides map by name. Nothing here is imported from legaleta.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any

from replay.types import Finding


@dataclass(frozen=True)
class Regs:
    # Art 7
    break_after_driving: int = 270
    break_full: int = 45
    break_split_first: int = 15
    break_split_second: int = 30
    # Art 6
    daily_driving: int = 540
    daily_driving_extended: int = 600
    extended_days_per_week: int = 2
    weekly_driving: int = 3360
    fortnight_driving: int = 5400
    # Art 4(g), 4(h), 4(i), 8
    daily_rest_regular: int = 660
    daily_rest_reduced: int = 540
    daily_rest_split_first: int = 180
    daily_rest_split_second: int = 540
    reduced_daily_rests_between_weekly: int = 3
    daily_window: int = 1440
    weekly_rest_regular: int = 2700
    weekly_rest_reduced: int = 1440
    weekly_rest_deadline: int = 6 * 1440
    compensation_deadline_weeks: int = 3
    # Art 9
    ferry_interruptions_max: int = 2
    ferry_interruption_total: int = 60
    ferry_weekly_rest_min_crossing: int = 480  # Art 9(1): regular weekly rest, 8 h or more, cabin


def regs_from_effective(effective: dict[str, Any]) -> tuple[Regs, list[Finding]]:
    """Apply the plan's echoed constants; report any that differ from the regulation."""
    base = Regs()
    known = {f.name for f in fields(base)}
    changed = {k: v for k, v in effective.items() if k in known and v != getattr(base, k)}
    if not changed:
        return base, []
    msg = "replay ran on non-standard rules: " + ", ".join(
        f"{k}={v} (regulation {getattr(base, k)})" for k, v in sorted(changed.items())
    )
    return replace(base, **changed), [Finding("NON_STANDARD_RULES", "NON_STANDARD_RULES", msg)]

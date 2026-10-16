"""Verdict and renderings of a replay."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from replay.types import Finding

_NOT_FAILURES = ("INTERPRETATION", "NON_STANDARD_RULES")


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    claims_checked: int = 0
    origin: datetime | None = None

    @property
    def verdict(self) -> Literal["PASS", "FAIL"]:
        return "FAIL" if any(f.cls not in _NOT_FAILURES for f in self.findings) else "PASS"


def _when(report: Report, minute: int | None) -> str:
    if minute is None or report.origin is None:
        return "-"
    return (report.origin + timedelta(minutes=minute)).strftime("%a %Y-%m-%d %H:%M")


def render_text(report: Report) -> str:
    lines = [
        f"{report.verdict}: {len(report.findings)} finding(s), "
        f"{report.claims_checked} claims checked"
    ]
    for f in report.findings:
        block = "" if f.block_index is None else f" block {f.block_index}"
        excess = f" (+{f.excess_minutes} min)" if f.excess_minutes else ""
        lines.append(
            f"  [{f.cls}] {f.code} {f.article or '-'} at {_when(report, f.minute)}{block}: "
            f"{f.message}{excess}"
        )
    return "\n".join(lines)


def render_json(report: Report) -> dict[str, Any]:
    return {
        "verdict": report.verdict,
        "claims_checked": report.claims_checked,
        "findings": [{**asdict(f), "time": _when(report, f.minute)} for f in report.findings],
    }

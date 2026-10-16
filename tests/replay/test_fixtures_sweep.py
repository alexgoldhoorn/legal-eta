"""Every stored fixture, in every policy, replays clean; known exceptions carry a reason."""

import json
from pathlib import Path

import pytest

from replay.compare import plan_for
from replay.report import render_text
from replay.run import replay_plan

ROOT = Path(__file__).resolve().parents[2]
FILES = sorted(
    [*(ROOT / "tests" / "fixtures" / "sim").glob("*.json"), *(ROOT / "examples").glob("*.json")]
)
POLICIES = ["fastest", "driver_friendly", "strict"]
# (file stem, policy) -> why the replay and legaleta disagree; see replay/FINDINGS.md
XFAIL: dict[tuple[str, str], str] = {}


def _requests() -> list[Path]:
    out = []
    for p in FILES:
        data = json.loads(p.read_text())
        if isinstance(data, dict) and "route" in data and "driver" not in data.get("blocks", {}):
            out.append(p)
    return out


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("path", _requests(), ids=lambda p: p.stem)
def test_fixture_replays_clean(path: Path, policy: str) -> None:
    if "start" not in json.loads(path.read_text()) or path.stem in (
        "validate_request",
        "project_request",
    ):
        pytest.skip("not a plan request")
    req = json.loads(path.read_text())
    report = replay_plan(req, plan_for(req, policy, None))
    if (path.stem, policy) in XFAIL:
        pytest.xfail(XFAIL[(path.stem, policy)])
    assert report.verdict == "PASS", render_text(report)

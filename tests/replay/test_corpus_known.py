"""The corpus holds known findings; pin that they are still exactly the documented ones."""

from pathlib import Path

from replay.compare import differential, load_inputs
from replay.run import replay_plan

CORPUS = Path(__file__).resolve().parents[2] / "replay" / "corpus"
DOCUMENTED = {  # FINDINGS.md #1
    "BREAK_MISSING",
    "VALIDATE_DISAGREES",
    "END_RAW_SPLIT_BREAK_FIRST_PART_TAKEN",
    "END_REMAINING_SPLIT_BREAK_FIRST_PART_TAKEN",
}


def test_corpus_fails_only_with_documented_findings() -> None:
    seen = 0
    for name, req, resp in load_inputs([str(CORPUS)]):
        assert resp is not None
        report = replay_plan(req, resp)
        report.findings.extend(differential(req, resp, report))
        codes = {f.code for f in report.findings if f.cls not in ("INTERPRETATION",)}
        assert codes and codes <= DOCUMENTED, (name, codes - DOCUMENTED)
        seen += 1
    assert seen >= 4

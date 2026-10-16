import copy
import json
from pathlib import Path

from replay.compare import differential, load_inputs, plan_for, run_inputs
from replay.run import replay_plan

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "plan_request.json"


def test_plan_then_replay_passes_on_example() -> None:
    results = run_inputs([str(EXAMPLE)], ["fastest", "driver_friendly", "strict"], None, True)
    assert [r.status for r in results] == ["PASS", "PASS", "PASS"]


def _pair(tmp_path: Path, tamper: bool) -> Path:
    req = json.loads(EXAMPLE.read_text())
    resp = plan_for(req, None, None)
    if tamper:
        resp = copy.deepcopy(resp)
        resp["summary"]["total"] += 7
    path = tmp_path / "pair.json"
    path.write_text(json.dumps({"request": req, "response": resp}))
    return path


def test_pair_input_is_not_replanned(tmp_path: Path) -> None:
    ok = run_inputs([str(_pair(tmp_path, False))], ["fastest", "strict"], None, True)
    assert [r.status for r in ok] == ["PASS"]  # one row: a pair has no policy
    bad = run_inputs([str(_pair(tmp_path, True))], ["fastest"], None, True)
    assert [r.status for r in bad] == ["FAIL"]


def test_unplannable_request_is_skipped(tmp_path: Path) -> None:
    req = json.loads(EXAMPLE.read_text())
    req["crew"] = 2  # rejected by legaleta: invalid request
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(req))
    assert [r.status for r in run_inputs([str(path)], ["fastest"], None, True)] == ["SKIP"]


def test_directory_input(tmp_path: Path) -> None:
    req = EXAMPLE.read_text()
    (tmp_path / "a.json").write_text(req)
    (tmp_path / "b.json").write_text(req)
    (tmp_path / "expected.json").write_text(json.dumps({"not": "a request"}))
    names = [n for n, _, _ in load_inputs([str(tmp_path)])]
    assert names == ["a", "b"]


def test_differential_agrees_on_clean_plan() -> None:
    req = json.loads(EXAMPLE.read_text())
    resp = plan_for(req, None, None)
    assert differential(req, resp, replay_plan(req, resp)) == []

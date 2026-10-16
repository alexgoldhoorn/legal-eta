import json
from pathlib import Path

import replay.compare as compare
from replay.compare import fuzz_run, load_inputs, plan_for
from replay.fuzz import generate
from replay.report import Report
from replay.types import Finding


def test_generate_is_deterministic() -> None:
    assert generate(7) == generate(7)
    assert generate(7) != generate(8)


def test_generated_requests_are_well_formed() -> None:
    planned = 0
    for seed in range(200):
        req = generate(seed)
        try:
            plan_for(req, None, None)
            planned += 1
        except Exception as e:
            assert type(e).__name__ in {"RequestError", "SchedulingError"}, (seed, repr(e))
    assert planned > 100  # the generator mostly produces plannable requests


def test_generator_covers_the_awkward_cases() -> None:
    reqs = [generate(s) for s in range(300)]
    kinds = {i["kind"] for r in reqs for i in r["route"]}
    assert kinds == {"drive", "stop", "ferry"}
    forms = {next(iter(r["driver"])) if r.get("driver") else None for r in reqs}
    assert forms == {None, "raw", "remaining"}
    assert any(r.get("driver", {}).get("raw", {}).get("compensation_owed_minutes") for r in reqs)
    assert any(
        r.get("driver", {}).get("remaining", {}).get("remaining_drive_today", 1) < 0 for r in reqs
    )


def test_fuzz_writes_failures_to_corpus(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    bad = Report([Finding("VIOLATION", "TEST", "forced")], 1)
    monkeypatch.setattr(compare, "replay_plan", lambda *a, **k: bad)
    results = fuzz_run(3, 4, ["fastest"], None, True, tmp_path)
    failed = [r for r in results if r.status == "FAIL"]
    assert failed
    files = sorted(tmp_path.glob("seed*_fastest.json"))
    assert files
    data = json.loads(files[0].read_text())
    assert data["seed"] in range(3, 7) and {"request", "response", "findings"} <= set(data)


def test_corpus_files_replay() -> None:
    corpus = Path(__file__).resolve().parents[2] / "replay" / "corpus"
    for name, req, resp in load_inputs([str(corpus)]):
        assert req and resp, name

import json
from pathlib import Path

from replay.__main__ import main

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = str(ROOT / "examples" / "plan_request.json")


def test_cli_exit_codes(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["run", EXAMPLES]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "replayed" in out

    pair = tmp_path / "pair.json"
    req = json.loads(Path(EXAMPLES).read_text())
    from replay.compare import plan_for

    resp = plan_for(req, None, None)
    resp["arrival"] = "2030-01-01T00:00:00+01:00"
    pair.write_text(json.dumps({"request": req, "response": resp}))
    assert main(["run", str(pair)]) == 1

    assert main(["run", EXAMPLES, "--policy", "bogus"]) == 2


def test_cli_json_output(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["run", EXAMPLES, "--policy", "strict", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["rows"][0]["verdict"] == "PASS"

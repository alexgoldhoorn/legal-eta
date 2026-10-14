# tests/test_cli.py
import io
import json
from pathlib import Path

from legaleta.cli import main


def run(argv: list[str], payload: object = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(
        argv,
        stdin=io.StringIO(json.dumps(payload) if payload is not None else ""),
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


def test_plan_ok() -> None:
    req = json.loads(Path("examples/plan_request.json").read_text())
    code, out, err = run(["plan"], req)
    assert code == 0 and err == ""
    assert json.loads(out)["disclaimer"] == "Planning aid, not legal advice."


def test_invalid_input_exit_2() -> None:
    code, out, err = run(["plan"], {"start": "2026-10-20T06:00:00+02:00", "route": []})
    assert code == 2 and out == "" and "route" in err


def test_bad_json_exit_2() -> None:
    out, err = io.StringIO(), io.StringIO()
    code = main(["plan"], stdin=io.StringIO("{not json"), stdout=out, stderr=err)
    assert code == 2 and "JSON" in err.getvalue()


def test_validate_and_project() -> None:
    v = json.loads(Path("examples/validate_request.json").read_text())
    code, out, _ = run(["validate"], v)
    assert code == 0 and "violations" in json.loads(out)
    p = json.loads(Path("examples/project_request.json").read_text())
    code, out, _ = run(["project", "--pretty"], p)
    assert code == 0 and "\n" in out


def test_help_has_disclaimer(capsys) -> None:  # type: ignore[no-untyped-def]
    import pytest

    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0
    assert "Planning aid, not legal advice." in capsys.readouterr().out


def test_impossible_state_exit_1_no_traceback() -> None:
    req = {
        "start": "2026-10-20T06:00:00+02:00",
        "driver": {
            "raw": {
                "last_daily_rest_end": "2026-10-19T16:00:00+02:00",
                "reduced_daily_rests_used": 3,
            }
        },
        "route": [{"kind": "drive", "minutes": 60}],
    }
    code, out, err = run(["plan"], req)
    assert code == 1 and out == ""
    assert "admits no legal plan" in err and "Traceback" not in err

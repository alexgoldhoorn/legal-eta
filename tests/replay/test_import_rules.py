import ast
from pathlib import Path

REPLAY = Path(__file__).resolve().parents[2] / "replay"
COMPARATOR_FILES = {"compare.py", "__main__.py"}


def _legaleta_imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] == "legaleta"]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "legaleta":
            found.append(node.module or "")
    return found


def test_core_imports_no_legaleta() -> None:
    for path in REPLAY.glob("*.py"):
        if path.name not in COMPARATOR_FILES:
            assert _legaleta_imports(path) == [], path.name


def test_comparator_uses_only_public_api() -> None:
    for name in COMPARATOR_FILES:
        path = REPLAY / name
        if path.exists():
            assert set(_legaleta_imports(path)) <= {"legaleta.api"}, name

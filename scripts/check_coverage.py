"""Fail unless rules.py, state.py and checker.py are 100 % covered.

Run after `pytest --cov=legaleta --cov-report=json`; reads ./coverage.json.
"""

import json
import sys
from pathlib import Path

REQUIRED = {"rules.py": 100.0, "state.py": 100.0, "checker.py": 100.0}


def main() -> int:
    data = json.loads(Path("coverage.json").read_text())
    seen: set[str] = set()
    failed = False
    for path, info in data["files"].items():
        name = Path(path).name
        if name in REQUIRED:
            seen.add(name)
            pct = info["summary"]["percent_covered"]
            print(f"{name}: {pct:.1f} %")
            if pct < REQUIRED[name]:
                failed = True
                print(f"  missing lines: {info['missing_lines']}")
    for name in sorted(set(REQUIRED) - seen):
        print(f"{name}: not in coverage.json")
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

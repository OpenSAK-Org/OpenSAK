#!/usr/bin/env python3
"""
scripts/update_macro_api_baseline.py — record the macro API as released in
tests/fixtures/macro_api_baseline.json (api_baseline.BASELINE_PATH).

The unit tests compare the API with this file and fail on a removal or
rename that wasn't deprecated first (#1185). Run this when a stable release
ships a new API version, after adding it to runtime.API_STABLE, and commit
the file. Additions don't need it: they are always allowed.

It refuses to write while the API breaks the current baseline (exit code 1),
so a breaking change can't be recorded by accident.

Usage:
    python scripts/update_macro_api_baseline.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

from opensak.macro.api_baseline import BASELINE_PATH, check, load_baseline, snapshot, to_json


def main() -> int:
    target = ROOT / BASELINE_PATH
    if target.exists():
        problems = check(load_baseline(ROOT))
        if problems:
            print("The macro API breaks the current baseline:")
            for problem in problems:
                print(f"  {problem}")
            return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_json(snapshot()), encoding="utf-8", newline="\n")
    print(f"Wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
scripts/generate_macro_api_docs.py — regenerate docs/macros/api.md and the
editor files in macros/types (GENERATED_FILES in opensak/macro/api_docs.py)
from the Lua API registry in src/opensak/macro/runtime.py.

Feature branches don't commit these files: the Macro API docs workflow
(.github/workflows/macro-api-docs.yml) runs this script after every merge to
beta and opens a PR with the result. Run it locally for up-to-date editor
files while writing macros, or with --check to see whether the committed
files are current (exit code 1 if not).

Usage:
    python scripts/generate_macro_api_docs.py [--check]
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
# The renderers read relative paths (e.g. macros/examples): same output
# wherever the script is started from.
os.chdir(ROOT)

from opensak.macro.api_docs import GENERATED_FILES


def main() -> int:
    check = "--check" in sys.argv[1:]
    stale = 0
    for path, render in GENERATED_FILES:
        target = ROOT / path
        text = render()
        if check:
            # Ignore CRLF from a Windows checkout (core.autocrlf).
            current = target.read_text(encoding="utf-8").replace("\r\n", "\n") if target.exists() else None
            if current != text:
                stale += 1
                print(f"Out of date: {path.as_posix()}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        print(f"Wrote {target}")
    if check and not stale:
        print("Macro API docs are up to date.")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())

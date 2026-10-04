#!/usr/bin/env python3
"""
scripts/generate_macro_api_docs.py — regenerate docs/macros/api.md from the
Lua API registry in src/opensak/macro/runtime.py.

Usage:
    python scripts/generate_macro_api_docs.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from opensak.macro.api_docs import DOC_PATH, render_api_markdown


def main() -> None:
    target = ROOT / DOC_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_api_markdown(), encoding="utf-8", newline="\n")
    print(f"Wrote {target}")


if __name__ == "__main__":
    main()

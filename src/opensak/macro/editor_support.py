"""
src/opensak/macro/editor_support.py — Lua Language Server support in the
user's macros folder (#1012).

The stub macros/types/opensak.lua (bundled by opensak.spec) is copied to
<macros>/types/opensak.lua on every start, so it always matches the
installed version. A .luarc.json pointing the language server at it is
written next to the macros once; a user's own .luarc.json is left alone.
Opening the macros folder in VS Code (extension "Lua" by sumneko), or any
other editor using LuaLS, then gives autocompletion and docs for opensak.*.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from opensak.config import get_macros_dir

STUB_NAME = "opensak.lua"
TYPES_DIR = "types"
LUARC_NAME = ".luarc.json"

LUARC = {
    "runtime.version": "Lua 5.4",
    "workspace.library": [TYPES_DIR],
    "diagnostics.globals": ["opensak"],
}


def bundled_stub() -> Path:
    """Where the shipped stub is: inside the PyInstaller bundle, or
    macros/types in a source checkout."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", "")) / "macros" / TYPES_DIR / STUB_NAME
    return Path(__file__).resolve().parents[3] / "macros" / TYPES_DIR / STUB_NAME


def install_editor_support(macros: Path | None = None) -> None:
    """Copy the stub into <macros>/types (only when it changed) and write
    <macros>/.luarc.json if there is none."""
    macros = macros or get_macros_dir()
    stub = bundled_stub().read_bytes()
    target = macros / TYPES_DIR / STUB_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or target.read_bytes() != stub:
        target.write_bytes(stub)
    luarc = macros / LUARC_NAME
    if not luarc.exists():
        luarc.write_text(json.dumps(LUARC, indent=2) + "\n", encoding="utf-8")

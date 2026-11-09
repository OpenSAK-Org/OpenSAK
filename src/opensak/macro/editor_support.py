"""
src/opensak/macro/editor_support.py — external editor support in the
user's macros folder (#1012).

The files in macros/types (bundled by opensak.spec) are copied to
<macros>/types on every start, so they always match the installed
version:

  * opensak.lua                — Lua Language Server stub (VS Code, ...)
  * notepad++/lua.xml          — Notepad++ auto-completion and calltips
  * ultraedit/opensak-lua.uew  — UltraEdit wordfile (highlighting and
                                 auto-complete)

A .luarc.json pointing the language server at the stub is written next to
the macros once; a user's own .luarc.json is left alone. Opening the
macros folder in VS Code (extension "Lua" by sumneko), or any other editor
using LuaLS, then gives autocompletion and docs for opensak.*. Notepad++
and UltraEdit read their files from their own folders, so the user copies
them there (see docs/macros/api.md, Editor support).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from opensak.config import get_macros_dir

STUB_NAME = "opensak.lua"
NPP_NAME = "notepad++/lua.xml"
ULTRAEDIT_NAME = "ultraedit/opensak-lua.uew"
# Relative to the types folder
EDITOR_FILES = (STUB_NAME, NPP_NAME, ULTRAEDIT_NAME)
TYPES_DIR = "types"
LUARC_NAME = ".luarc.json"

LUARC = {
    "runtime.version": "Lua 5.4",
    "workspace.library": [TYPES_DIR],
    "diagnostics.globals": ["opensak"],
}


def bundled_types_dir() -> Path:
    """Where the shipped editor files are: inside the PyInstaller bundle,
    or macros/types in a source checkout."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", "")) / "macros" / TYPES_DIR
    return Path(__file__).resolve().parents[3] / "macros" / TYPES_DIR


def bundled_stub() -> Path:
    """The shipped LuaLS stub."""
    return bundled_types_dir() / STUB_NAME


def install_editor_support(macros: Path | None = None) -> None:
    """Copy the editor files into <macros>/types (only those that changed)
    and write <macros>/.luarc.json if there is none.

    A file missing from the bundle is skipped: they are generated, and a
    source checkout only has a new one after the Macro API docs workflow
    committed it (or after running scripts/generate_macro_api_docs.py)."""
    macros = macros or get_macros_dir()
    for name in EDITOR_FILES:
        source = bundled_types_dir() / name
        if not source.is_file():
            continue
        data = source.read_bytes()
        target = macros / TYPES_DIR / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_bytes() != data:
            target.write_bytes(data)
    luarc = macros / LUARC_NAME
    if not luarc.exists():
        luarc.write_text(json.dumps(LUARC, indent=2) + "\n", encoding="utf-8")

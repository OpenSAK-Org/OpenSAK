"""
src/opensak/macro/metadata.py — the metadata header of a macro file and the
list of local macros the Macro manager shows.

A macro may start with a comment block naming it (concept §8, like GSAK's
MacHeader):

    -- OpenSAK Macro
    -- Name: Find easy traditionals
    -- Author: Jane Doe
    -- Version: 1.0
    -- Requires-API: 3
    -- Description: Filters for unfound traditional caches with low difficulty.

Every key is optional and the "OpenSAK Macro" line is not required. Keys
are matched case-insensitively in the comment lines at the top of the file
(up to the first line of code); other comment lines are ignored. Without a
Name the file name is used. Without a Description, the text after the dash
of a first line in the older "-- name.lua — what it does" style is used.

Requires-API is compared with the macro API version (opensak.api_version(),
runtime.API_VERSION) by its leading number, so "3" and "3.0" are the same.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Only the top of the file is read: the header is a few lines, and a
# folder may hold large generated macros.
_HEAD_BYTES = 16 * 1024

_KEYS = {
    "name": "name",
    "author": "author",
    "version": "version",
    "requires-api": "requires_api",
    "description": "description",
}

_KEY_LINE = re.compile(r"^([A-Za-z][A-Za-z-]*)\s*:\s*(.*?)\s*$")
_API_NUMBER = re.compile(r"^\s*(\d+)(?:\.\d+)*\s*$")
_LEGACY_SUMMARY = re.compile(r"^\S+\.lua\s+(?:—|–|-)\s+(.+?)\s*$")


@dataclass(frozen=True)
class MacroInfo:
    """What the header of one macro file says. *error* is set (and the
    other fields are mostly empty) when the file could not be read."""

    path: Path
    name: str
    description: str = ""
    author: str = ""
    version: str = ""
    requires_api: str = ""
    error: str = ""

    @property
    def requires_api_number(self) -> Optional[int]:
        """Requires-API as a number, None when missing or not a number."""
        m = _API_NUMBER.match(self.requires_api)
        return int(m.group(1)) if m else None

    def needs_newer_api(self, api_version: int) -> bool:
        """True if the macro asks for a newer API than *api_version*."""
        needed = self.requires_api_number
        return needed is not None and needed > api_version


def _header_lines(text: str) -> list[str]:
    """The text of the comment lines at the top of *text*, without "--".
    Blank lines are skipped, a block comment ("--[[") ends the header."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#!") and not lines:
            continue                    # shebang of a macro run from a shell
        if not line.startswith("--") or line.startswith("--[["):
            break
        lines.append(line[2:].strip())
    return lines


def parse_header(text: str, path: Path) -> MacroInfo:
    """The metadata in the header of macro source *text* from *path*."""
    fields: dict[str, str] = {}
    lines = _header_lines(text.lstrip("﻿"))
    for line in lines:
        m = _KEY_LINE.match(line)
        if m:
            field = _KEYS.get(m.group(1).lower())
            if field and field not in fields and m.group(2):
                fields[field] = m.group(2)
    if "description" not in fields and lines:
        m = _LEGACY_SUMMARY.match(lines[0])
        if m:
            fields["description"] = m.group(1)
    fields.setdefault("name", path.stem)
    return MacroInfo(path=path, **fields)


def read_metadata(path: Path) -> MacroInfo:
    """The metadata of the macro file *path*; an unreadable file gives a
    MacroInfo with *error* set."""
    path = Path(path)
    try:
        with path.open("rb") as f:
            head = f.read(_HEAD_BYTES)
        text = head.decode("utf-8", errors="strict" if len(head) < _HEAD_BYTES else "ignore")
    except (OSError, UnicodeDecodeError) as exc:
        return MacroInfo(path=path, name=path.stem, error=str(exc))
    return parse_header(text, path)


def discover_macros(root: Path) -> list[MacroInfo]:
    """The .lua files in *root* and its subfolders, sorted by relative
    path (case-insensitively). Hidden folders (".git", ".vscode", …) are
    left out, and so is <root>/types with the editor stub opensak.lua."""
    from opensak.macro.editor_support import TYPES_DIR

    root = Path(root)
    if not root.is_dir():
        return []
    found: list[Path] = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")
                   and not (d == TYPES_DIR and Path(folder) == root)]
        found += [Path(folder) / f for f in files if f.lower().endswith(".lua")]
    found.sort(key=lambda p: p.relative_to(root).as_posix().casefold())
    return [read_metadata(p) for p in found]

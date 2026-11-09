"""
src/opensak/macro/shortcuts.py — keyboard shortcuts the user gave macros in
the Macro manager.

Stored in opensak.json under "macros.shortcuts" as {macro: key sequence},
the key sequence in QKeySequence's portable text ("Ctrl+Alt+1"). A macro
in the macros folder is stored by its path relative to that folder (with
"/"), so its shortcut survives moving the macros folder; any other macro
by its absolute path. Entries whose file no longer exists are left out
when the shortcuts are read, so a renamed macro simply loses its shortcut.
"""

from __future__ import annotations

from pathlib import Path

STORE_KEY = "macros.shortcuts"


def _key(path: Path) -> str:
    from opensak.macro.permissions import macros_dir

    path = Path(path).resolve()
    try:
        return path.relative_to(macros_dir()).as_posix()
    except ValueError:
        return str(path)


def _path(key: str) -> Path:
    from opensak.macro.permissions import macros_dir

    path = Path(key)
    return path if path.is_absolute() else macros_dir() / path


def _stored() -> dict[str, str]:
    from opensak.settings_store import get_store

    raw = get_store().get(STORE_KEY)
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str) and v}


def macro_shortcuts() -> dict[Path, str]:
    """{macro file: key sequence} for the macros that still exist."""
    shortcuts = {_path(k): v for k, v in _stored().items()}
    return {p: v for p, v in shortcuts.items() if p.is_file()}


def macro_shortcut(path: Path) -> str:
    """The key sequence of macro *path*, "" if it has none."""
    return _stored().get(_key(path), "")


def set_macro_shortcut(path: Path, sequence: str) -> None:
    """Give macro *path* the key sequence *sequence*; "" removes it."""
    from opensak.settings_store import get_store

    stored = _stored()
    if sequence:
        stored[_key(path)] = sequence
    else:
        stored.pop(_key(path), None)
    if stored:
        get_store().set(STORE_KEY, stored)
    else:
        get_store().delete(STORE_KEY)

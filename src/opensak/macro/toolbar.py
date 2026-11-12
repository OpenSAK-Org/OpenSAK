"""
src/opensak/macro/toolbar.py — the macros the user put on the Macros
toolbar of the main window (#1271).

Stored in opensak.json under "macros.toolbar" as a list of macros, in the
order of their buttons. A macro is stored like its shortcut (see
opensak.macro.shortcuts): by its path relative to the macros folder (with
"/") when it is in that folder, so its button survives moving the macros
folder, any other macro by its absolute path.

Only the user puts a macro on the toolbar, here or in the Macro manager:
there is no macro API for it. Entries whose file no longer exists are left
out when the toolbar is read but kept in the store, so a macro on a folder
that is missing for a while gets its button back; a renamed macro simply
loses its button, as it loses its shortcut.
"""

from __future__ import annotations

from pathlib import Path

from opensak.macro.shortcuts import _key, _path

STORE_KEY = "macros.toolbar"


def _stored() -> list[str]:
    from opensak.settings_store import get_store

    raw = get_store().get(STORE_KEY)
    if not isinstance(raw, list):
        return []
    keys: list[str] = []
    for key in raw:
        if isinstance(key, str) and key and key not in keys:
            keys.append(key)
    return keys


def _save(keys: list[str]) -> None:
    from opensak.settings_store import get_store

    if keys:
        get_store().set(STORE_KEY, keys)
    else:
        get_store().delete(STORE_KEY)


def toolbar_macros() -> list[Path]:
    """The macros on the toolbar that still exist, in button order."""
    return [p for p in (_path(k) for k in _stored()) if p.is_file()]


def on_toolbar(path: Path) -> bool:
    """Whether macro *path* has a button on the toolbar."""
    return _key(path) in _stored()


def set_on_toolbar(path: Path, on: bool) -> None:
    """Put macro *path* on the toolbar, as its last button, or take it off."""
    keys = _stored()
    key = _key(path)
    if on and key not in keys:
        keys.append(key)
    elif not on and key in keys:
        keys.remove(key)
    else:
        return
    _save(keys)


def move_on_toolbar(path: Path, step: int) -> bool:
    """Move the button of macro *path* *step* places (-1 left, 1 right)
    among the buttons shown. Returns False if it cannot move that way."""
    keys = _stored()
    key = _key(path)
    shown = [i for i, k in enumerate(keys) if _path(k).is_file()]
    positions = [keys[i] for i in shown]
    if key not in positions:
        return False
    here = positions.index(key)
    there = here + step
    if not 0 <= there < len(positions):
        return False
    a, b = shown[here], shown[there]
    keys[a], keys[b] = keys[b], keys[a]
    _save(keys)
    return True

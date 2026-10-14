"""
src/opensak/macro/recent.py — the macro files the user opened or saved last.

Stored in opensak.json under "macros.recent_files" as a list of absolute
paths, newest first, at most MAX_RECENT entries. Files that no longer exist
are left out when the list is read, so a deleted or moved macro simply
disappears from the menu.
"""

from __future__ import annotations

from pathlib import Path

STORE_KEY = "macros.recent_files"
MAX_RECENT = 10


def _key(path: Path) -> str:
    """Comparison key: the same file must not be listed twice, also when
    spelled differently (case on Windows, ".." segments)."""
    import os

    return os.path.normcase(str(path.resolve()))


def _stored() -> list[str]:
    from opensak.settings_store import get_store

    raw = get_store().get(STORE_KEY)
    if not isinstance(raw, list):
        return []
    return [p for p in raw if isinstance(p, str) and p]


def recent_macros() -> list[Path]:
    """The existing macro files, newest first."""
    return [Path(p) for p in _stored() if Path(p).is_file()]


def add_recent_macro(path: Path) -> None:
    """Put *path* at the top of the list (moving it if already listed)."""
    from opensak.settings_store import get_store

    path = Path(path).resolve()
    key = _key(path)
    paths = [str(path)] + [p for p in _stored() if _key(Path(p)) != key]
    get_store().set(STORE_KEY, paths[:MAX_RECENT])


def remove_recent_macro(path: Path) -> None:
    from opensak.settings_store import get_store

    key = _key(Path(path))
    get_store().set(STORE_KEY, [p for p in _stored() if _key(Path(p)) != key])


def clear_recent_macros() -> None:
    from opensak.settings_store import get_store

    get_store().delete(STORE_KEY)

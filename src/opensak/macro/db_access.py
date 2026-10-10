"""
src/opensak/macro/db_access.py — which databases Lua macros may change.

Reading a database needs no approval. Before a macro's first write to a
database (opensak.update/insert/sql_write, set_corrected, clear_corrected)
the runtime asks the user, through OpenSAK's own dialog, never through the
macro:

  * Deny — the write fails; not asked again during this run;
  * Until OpenSAK closes — every macro may change this database until the
    application quits (kept in memory only);
  * Always — stored in opensak.json under "macros.db_write_always" as a
    list of resolved database paths, and listed in Settings → Folder
    permissions, where it can be removed again.

Databases are identified by their resolved file path, so renaming a
database in the list keeps its approval, while a different file under the
same name needs a new one.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Iterable

STORE_KEY = "macros.db_write_always"


class WriteApproval(Enum):
    """The user's answer when a macro wants to change a database."""

    SESSION = "session"   # until OpenSAK closes
    ALWAYS = "always"     # stored in Settings
    DENY = "deny"


# Databases approved "until OpenSAK closes" (keys from _key()).
_session: set[str] = set()


def _key(path: str | Path) -> str:
    """A comparable form of a database path: resolved, case-folded where
    the filesystem ignores case."""
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        resolved = Path(path)
    return os.path.normcase(str(resolved))


def always_approved() -> list[str]:
    """The database paths approved permanently, as stored."""
    from opensak.settings_store import get_store

    raw = get_store().get(STORE_KEY)
    if not isinstance(raw, list):
        return []
    return [str(p) for p in raw if isinstance(p, str) and p]


def save_always_approved(paths: Iterable[str]) -> None:
    """Store *paths* as the permanently approved databases (an empty list
    removes the setting). A database dropped from the list loses its
    approval at once, also the one until OpenSAK closes."""
    from opensak.settings_store import get_store

    store = get_store()
    unique: list[str] = []
    seen: set[str] = set()
    for p in paths:
        if _key(p) not in seen:
            seen.add(_key(p))
            unique.append(str(p))
    _session.difference_update({_key(p) for p in always_approved()} - seen)
    if unique:
        store.set(STORE_KEY, unique)
    elif store.get(STORE_KEY) is not None:
        store.delete(STORE_KEY)


def is_approved(path: str | Path) -> bool:
    """True if macros may change the database at *path* without asking."""
    key = _key(path)
    return key in _session or any(_key(p) == key for p in always_approved())


def approve(path: str | Path, answer: WriteApproval) -> None:
    """Record the user's *answer* for the database at *path*. DENY records
    nothing here — the runtime remembers it for the current run only."""
    if answer is WriteApproval.SESSION:
        _session.add(_key(path))
    elif answer is WriteApproval.ALWAYS:
        _session.add(_key(path))
        if not any(_key(p) == _key(path) for p in always_approved()):
            save_always_approved([*always_approved(), str(Path(path).resolve())])


def reset_session() -> None:
    """Forget the approvals given until OpenSAK closes (used by tests)."""
    _session.clear()

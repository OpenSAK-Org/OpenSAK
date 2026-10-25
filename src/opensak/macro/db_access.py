"""
src/opensak/macro/db_access.py — which Lua macros may change OpenSAK's
databases (#1034).

Reading a database needs no approval. Before a macro's first write
(opensak.update/insert/sql_write, set_corrected, clear_corrected) the
runtime asks the user, through OpenSAK's own dialog, never through the
macro, whether *this macro* may change the OpenSAK databases:

  * Deny — the write fails; not asked again during this run;
  * Until OpenSAK closes — the macro may change databases until the
    application quits (kept in memory only);
  * Always — stored in opensak.json under "macros.write_always" as a list
    of resolved macro file paths, and listed in Settings → Folder
    permissions, where it can be removed again.

Approval is keyed on the macro file's path: editing the macro keeps it,
moving or renaming the file needs a new one. It covers every database, but
a macro still only ever writes the active one. An unsaved macro (an editor
buffer without a file) can only be approved until OpenSAK closes; that
approval is shared by all unsaved macros. A macro started with
opensak.run_macro() needs its own approval.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

STORE_KEY = "macros.write_always"

# The key for macros without a file
_UNSAVED = "<unsaved>"


class WriteApproval(Enum):
    """The user's answer when a macro wants to change a database."""

    SESSION = "session"   # until OpenSAK closes
    ALWAYS = "always"     # stored in Settings
    DENY = "deny"


# Macros approved "until OpenSAK closes" (keys from _key()).
_session: set[str] = set()


def _key(macro: Optional[str | Path]) -> str:
    """A comparable form of a macro path: resolved, case-folded where the
    filesystem ignores case. None (an unsaved macro) has its own key."""
    if macro is None:
        return _UNSAVED
    try:
        resolved = Path(macro).expanduser().resolve()
    except (OSError, RuntimeError):
        resolved = Path(macro)
    return os.path.normcase(str(resolved))


def macro_key(macro: Optional[str | Path]) -> str:
    """The key of the macro at *macro*, as approvals store it. Also the
    owner of the tables a macro creates in the companion database."""
    return _key(macro)


def always_approved() -> list[str]:
    """The macro paths approved permanently, as stored."""
    from opensak.settings_store import get_store

    raw = get_store().get(STORE_KEY)
    if not isinstance(raw, list):
        return []
    return [str(p) for p in raw if isinstance(p, str) and p]


def save_always_approved(paths: Iterable[str]) -> None:
    """Store *paths* as the permanently approved macros (an empty list
    removes the setting). A macro dropped from the list loses its approval
    at once, also the one until OpenSAK closes."""
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


def is_approved(macro: Optional[str | Path]) -> bool:
    """True if the macro at *macro* (None: an unsaved one) may change
    databases without asking."""
    key = _key(macro)
    if key in _session:
        return True
    return macro is not None and any(_key(p) == key for p in always_approved())


def approve(macro: Optional[str | Path], answer: WriteApproval) -> None:
    """Record the user's *answer* for the macro at *macro*. DENY records
    nothing here — the runtime remembers it for the current run only. An
    unsaved macro (None) cannot be approved always; ALWAYS then counts as
    SESSION."""
    if answer not in (WriteApproval.SESSION, WriteApproval.ALWAYS):
        return
    _session.add(_key(macro))
    if answer is WriteApproval.ALWAYS and macro is not None:
        if not any(_key(p) == _key(macro) for p in always_approved()):
            save_always_approved([*always_approved(), str(Path(macro).resolve())])


def reset_session() -> None:
    """Forget the approvals given until OpenSAK closes (used by tests)."""
    _session.clear()

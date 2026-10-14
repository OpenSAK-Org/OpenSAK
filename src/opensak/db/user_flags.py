"""
src/opensak/db/user_flags.py — set or clear the user flag on many caches.

Qt-free, so the Waypoint → User flags menu (issue #293) uses it today and a
macro binding can reuse it later. Updates run in chunks so a large filter
never hits SQLite's bound-parameter limit.
"""

from __future__ import annotations

from typing import Callable, ContextManager, Iterable, Optional

from sqlalchemy.orm import Session

# GC codes per UPDATE statement.
CHUNK_SIZE = 500


def set_user_flag(
    gc_codes: Iterable[str],
    flagged: bool,
    session_factory: Optional[Callable[[], ContextManager[Session]]] = None,
) -> int:
    """Set (flagged=True) or clear (flagged=False) the user flag on *gc_codes*
    in the active database.

    Only caches whose flag actually changes are written, so the return value
    is the number of caches changed — 0 when every cache already had the
    requested state. Unknown GC codes are ignored.
    """
    from opensak.db.database import get_session
    from opensak.db.models import Cache

    factory = session_factory or get_session
    codes = list(dict.fromkeys(c for c in gc_codes if c))
    changed = 0
    with factory() as session:
        for start in range(0, len(codes), CHUNK_SIZE):
            chunk = codes[start:start + CHUNK_SIZE]
            changed += (
                session.query(Cache)
                .filter(Cache.gc_code.in_(chunk))
                .filter(Cache.user_flag != flagged)
                .update({Cache.user_flag: flagged}, synchronize_session=False)
            )
    return changed

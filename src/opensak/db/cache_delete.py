"""
src/opensak/db/cache_delete.py — delete caches together with their rows.

Qt-free, so the Waypoint → Delete menu, moving caches to another database
(transfer.py) and Lua macros (opensak.delete, #1035) share it. The schema's
foreign keys from the child tables to caches.id have no ON DELETE CASCADE,
so the logs, waypoints, attributes, trackables and user note are deleted
first. Deletes run in chunks so a large filter never hits SQLite's
bound-parameter limit (#1041).
"""

from __future__ import annotations

from typing import Iterable

from sqlalchemy.orm import Session

# GC codes per DELETE statement.
CHUNK_SIZE = 500


def delete_caches(session: Session, gc_codes: Iterable[str]) -> list[str]:
    """Delete the caches *gc_codes* and every row belonging to them, in
    *session*'s database. Returns the codes that were deleted, in the given
    order; unknown codes are ignored."""
    from opensak.db.models import Attribute, Cache, Log, Trackable, UserNote, Waypoint

    codes = list(dict.fromkeys(c for c in gc_codes if c))
    deleted: set[str] = set()
    for start in range(0, len(codes), CHUNK_SIZE):
        chunk = codes[start:start + CHUNK_SIZE]
        rows = session.query(Cache.id, Cache.gc_code).filter(Cache.gc_code.in_(chunk)).all()
        if not rows:
            continue
        cache_ids = [row[0] for row in rows]
        for model in (Log, Attribute, Trackable, Waypoint, UserNote):
            session.query(model).filter(
                model.cache_id.in_(cache_ids)
            ).delete(synchronize_session=False)
        session.query(Cache).filter(Cache.id.in_(cache_ids)).delete(synchronize_session=False)
        deleted.update(row[1] for row in rows)
    return [c for c in codes if c in deleted]

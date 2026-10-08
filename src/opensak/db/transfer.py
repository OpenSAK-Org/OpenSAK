"""
src/opensak/db/transfer.py — move or copy caches between two databases.

Qt-free, so both the Move/Copy Caches dialog (in a worker thread) and Lua
macros (opensak.move_caches / copy_caches) use it. Both databases get
private sessions (session_for): the app-wide engine is never swapped, so
the GUI keeps reading the active database while a transfer runs.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

# What to do when a cache already exists in the target database.
IF_EXISTS = ("replace", "skip", "newer")
# Caches per step — each step is loaded, written to the target and (when
# moving) deleted from the source before the next one, so memory stays
# bounded and SQLite's bound-parameter limit is never reached.
CHUNK_SIZE = 200


def _snapshot_cache(cache) -> dict:
    """Extract all cache data into a plain dict while the session is open."""
    snap: dict = {}

    # Scalar columns (skip id and relationships)
    for col in cache.__table__.columns:
        if col.name == "id":
            continue
        snap[col.name] = getattr(cache, col.name)

    # Child records
    snap["_logs"] = []
    for log in (cache.logs or []):
        d = {}
        for col in log.__table__.columns:
            if col.name in ("id", "cache_id"):
                continue
            d[col.name] = getattr(log, col.name)
        snap["_logs"].append(d)

    snap["_attributes"] = []
    for attr in (cache.attributes or []):
        d = {}
        for col in attr.__table__.columns:
            if col.name in ("id", "cache_id"):
                continue
            d[col.name] = getattr(attr, col.name)
        snap["_attributes"].append(d)

    snap["_trackables"] = []
    for tb in (cache.trackables or []):
        d = {}
        for col in tb.__table__.columns:
            if col.name in ("id", "cache_id"):
                continue
            d[col.name] = getattr(tb, col.name)
        snap["_trackables"].append(d)

    snap["_waypoints"] = []
    for wp in (cache.waypoints or []):
        d = {}
        for col in wp.__table__.columns:
            if col.name in ("id", "cache_id"):
                continue
            d[col.name] = getattr(wp, col.name)
        snap["_waypoints"].append(d)

    snap["_user_note"] = None
    if cache.user_note:
        d = {}
        for col in cache.user_note.__table__.columns:
            if col.name in ("id", "cache_id"):
                continue
            d[col.name] = getattr(cache.user_note, col.name)
        snap["_user_note"] = d

    return snap


def _insert_snapshot(session, snap: dict) -> None:
    """Insert a snapshot dict into the current session's database.

    If a cache with the same gc_code already exists in the target, it is
    replaced (all child records are deleted first).
    """
    from opensak.db.models import (
        Cache, Log, Attribute, Trackable, Waypoint, UserNote,
    )

    gc_code = snap["gc_code"]

    # Remove existing cache with same gc_code (if any)
    existing = session.query(Cache).filter_by(gc_code=gc_code).first()
    if existing:
        session.delete(existing)
        session.flush()

    # Build new Cache from scalar columns
    cache_data = {k: v for k, v in snap.items() if not k.startswith("_")}
    new_cache = Cache(**cache_data)
    session.add(new_cache)
    session.flush()  # assigns new_cache.id

    # Child records
    for log_data in snap["_logs"]:
        # Clear log_id to avoid unique constraint conflicts
        log_data_copy = dict(log_data)
        log_data_copy.pop("log_id", None)
        session.add(Log(cache_id=new_cache.id, **log_data_copy))

    for attr_data in snap["_attributes"]:
        session.add(Attribute(cache_id=new_cache.id, **attr_data))

    for tb_data in snap["_trackables"]:
        session.add(Trackable(cache_id=new_cache.id, **tb_data))

    for wp_data in snap["_waypoints"]:
        session.add(Waypoint(cache_id=new_cache.id, **wp_data))

    if snap["_user_note"]:
        session.add(UserNote(cache_id=new_cache.id, **snap["_user_note"]))


def _import_time(last_gpx_update: Optional[datetime], imported_at: Optional[datetime]):
    """When the cache data was last imported — what "newer" compares."""
    return last_gpx_update or imported_at


def _should_write(if_exists: str, source_time, target_time) -> bool:
    """For a cache that exists in the target already."""
    if if_exists == "replace":
        return True
    if if_exists == "skip":
        return False
    # "newer": the source copy was imported later than the target copy
    if target_time is None:
        return source_time is not None
    return source_time is not None and source_time > target_time


def transfer_caches(
    gc_codes: list[str],
    source_db_path: Path,
    target_db_path: Path,
    copy_only: bool = False,
    if_exists: str = "replace",
) -> int:
    """Copy (or move) *gc_codes* from the source to the target database.

    A cache that already exists in the target is replaced, kept
    (if_exists="skip") or replaced only if the source copy was imported
    later (if_exists="newer"). When moving, only the caches actually
    written to the target are deleted from the source — a skipped cache
    stays where it is. Returns the number of caches written.
    """
    from opensak.db.database import session_for
    from opensak.db.models import (
        Cache, Log, Attribute, Trackable, Waypoint, UserNote,
    )
    from sqlalchemy.orm import joinedload, selectinload

    if if_exists not in IF_EXISTS:
        raise ValueError(f"if_exists must be one of {', '.join(IF_EXISTS)}, got {if_exists!r}")

    written_total = 0
    codes = list(dict.fromkeys(gc_codes))
    for start in range(0, len(codes), CHUNK_SIZE):
        chunk = codes[start:start + CHUNK_SIZE]

        # ── 1. Load full caches from source DB ────────────────────────────
        with session_for(source_db_path) as session:
            caches = (
                session.query(Cache)
                .options(
                    selectinload(Cache.logs),
                    selectinload(Cache.attributes),
                    selectinload(Cache.waypoints),
                    selectinload(Cache.trackables),
                    joinedload(Cache.user_note),
                )
                .filter(Cache.gc_code.in_(chunk))
                .all()
            )
            # Snapshot all data while session is open
            snapshots = [_snapshot_cache(c) for c in caches]
        if not snapshots:
            continue

        # ── 2. Insert into target DB ──────────────────────────────────────
        written: list[str] = []
        with session_for(target_db_path) as session:
            existing = {}
            if if_exists != "replace":
                existing = {
                    code: _import_time(gpx, imported)
                    for code, gpx, imported in session.query(
                        Cache.gc_code, Cache.last_gpx_update, Cache.imported_at
                    ).filter(Cache.gc_code.in_(chunk))
                }
            for snap in snapshots:
                code = snap["gc_code"]
                if code in existing and not _should_write(
                    if_exists,
                    _import_time(snap.get("last_gpx_update"), snap.get("imported_at")),
                    existing[code],
                ):
                    continue
                _insert_snapshot(session, snap)
                written.append(code)

        # ── 3. Delete from source DB (move only) ──────────────────────────
        if not copy_only and written:
            with session_for(source_db_path) as session:
                cache_ids = [
                    row[0]
                    for row in session.query(Cache.id)
                    .filter(Cache.gc_code.in_(written))
                    .all()
                ]
                if cache_ids:
                    for model in (Log, Attribute, Trackable, Waypoint, UserNote):
                        session.query(model).filter(
                            model.cache_id.in_(cache_ids)
                        ).delete(synchronize_session=False)
                    session.query(Cache).filter(
                        Cache.id.in_(cache_ids)
                    ).delete(synchronize_session=False)

        written_total += len(written)
    return written_total

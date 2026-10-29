"""
src/opensak/importer/gsak_replace.py — overwrite import into a fresh file (#1231).

Overwriting an OpenSAK database with a GSAK import used to delete every
cache row by row and then import into the emptied file: ~50 s of deletes on
a 150k-cache database, all of it through the WAL, a file left full of free
pages, and an emptied database if the import then failed.

Instead the import is built into a new file next to the target, without
durability (journal in memory, no fsync — a failed build is simply deleted).
Everything in the target that isn't cache data is copied over — above all
``db_settings`` with the db_uuid its companion belongs to — and the new file
replaces the target with one rename. Until then the target is untouched.

Afterwards, as decided in #1231, every cache-linked row of the companion
(custom field values, macro tables keyed by GC code) belonged to the old
caches and is removed; the GSAK custom fields are imported after that.
Free macro tables and the custom field definitions stay.

The caller makes sure nothing edits the target meanwhile: the import dialog
is modal, the CLI / macro import runs on its own.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Callable, Optional

from opensak.importer.gsak_importer import (
    GsakImportResult,
    _import_custom_fields,
    _open_readonly,
    import_gsak_db,
)
from opensak.utils import flags

logger = logging.getLogger(__name__)

# The tables an import fills; everything else in the target is carried over.
CACHE_TABLES = frozenset(
    {"caches", "waypoints", "logs", "attributes", "trackables", "user_notes"}
)

# db_settings keys that describe the old caches, not the database: the
# distances are recalculated for the new ones (gsak_import_service).
_STALE_SETTINGS = ("dist_calc_lat", "dist_calc_lon", "dist_calc_method")

BUILD_SUFFIX = ".gsak-import"

# Swapping the file in: how often to retry while another connection (a
# timer on the GUI thread, a virus scanner) still holds the target open.
_SWAP_TRIES = 20
_SWAP_WAIT = 0.25


def build_path(target: Path) -> Path:
    """Where the new file for *target* is built (same folder, so the final
    rename never crosses drives)."""
    target = Path(target)
    return target.with_name(target.name + BUILD_SUFFIX)


def import_gsak_db_replacing(
    db_path: Path,
    target_path: Path,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    batch_size: int = 200,
    settings_db: Optional[Path] = None,
) -> GsakImportResult:
    """
    Replace all caches of the OpenSAK database *target_path* with those of
    the GSAK database *db_path* (the import dialog's "overwrite").

    Same result as emptying the target and importing into it, except that
    the target is left as it was when the import fails or raises. The
    result's ``cleared`` holds the number of caches the target had.
    """
    from opensak.db.database import session_for
    from opensak.db.models import Cache
    from sqlalchemy import func, select

    db_path, target_path = Path(db_path), Path(target_path)
    build = build_path(target_path)
    _remove_files(build)   # left over from an interrupted run

    # Opening it also brings an older target's schema up to date first.
    with session_for(target_path) as session:
        cleared = int(session.execute(select(func.count()).select_from(Cache)).scalar_one())

    try:
        with session_for(build) as session:
            result = import_gsak_db(
                db_path, session, progress_cb=progress_cb, batch_size=batch_size,
                scratch=True, custom_fields=False,
            )
        if result.created == 0 and result.skipped == 0 and result.errors:
            # The GSAK database couldn't be read at all: keep the target.
            _remove_files(build)
            return result
        copy_database_data(target_path, build)
        _swap(build, target_path)
    except BaseException:
        try:
            _remove_files(build)
        except OSError:
            logger.warning("GSAK overwrite: could not remove %s", build, exc_info=True)
        raise
    result.cleared = cleared

    if flags.companion_db:
        from opensak.db import companion
        removed = companion.clear_cache_rows(target_path)
        if removed:
            logger.info("GSAK overwrite: %d companion rows of the old caches removed", removed)
        _custom_fields_after_swap(db_path, target_path, result, settings_db)
    return result


def copy_database_data(source: Path, build: Path) -> None:
    """Copy every table of *source* that isn't cache data into *build*:
    rows (replacing what the new file has), and the table with its indexes
    if *build* doesn't have it yet. Only the columns both have are copied."""
    conn = sqlite3.connect(str(build), timeout=30)
    try:
        conn.execute("ATTACH DATABASE ? AS old", (str(source),))
        tables = conn.execute(
            "SELECT name, sql FROM old.sqlite_master "
            "WHERE type = 'table' AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        have = {name.lower() for (name,) in conn.execute(
            "SELECT name FROM main.sqlite_master WHERE type = 'table'")}
        with conn:
            for name, sql in tables:
                if name.lower() in CACHE_TABLES:
                    continue
                if name.lower() not in have:
                    conn.execute(sql)
                    for (index_sql,) in conn.execute(
                        "SELECT sql FROM old.sqlite_master "
                        "WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL", (name,)
                    ).fetchall():
                        conn.execute(index_sql)
                new_cols = {r[1].lower() for r in conn.execute(f"PRAGMA main.table_info({_q(name)})")}
                cols = ", ".join(
                    _q(r[1]) for r in conn.execute(f"PRAGMA old.table_info({_q(name)})")
                    if r[1].lower() in new_cols
                )
                conn.execute(f"DELETE FROM main.{_q(name)}")
                conn.execute(f"INSERT INTO main.{_q(name)} ({cols}) SELECT {cols} FROM old.{_q(name)}")
            if "db_settings" in {name.lower() for name, _sql in tables}:
                conn.execute(
                    f"DELETE FROM main.db_settings WHERE key IN ({', '.join('?' * len(_STALE_SETTINGS))})",
                    _STALE_SETTINGS,
                )
        conn.execute("DETACH DATABASE old")
    finally:
        conn.close()


def _swap(build: Path, target: Path) -> None:
    """Replace *target* by *build*. The target's -wal / -shm go first: a WAL
    left next to the new file would be replayed into it. Each try closes
    the active engine's idle connections again (release_connections keeps
    the engine, so the GUI just opens a new connection — to the new file)."""
    from opensak.db import db_settings
    from opensak.db.database import release_connections

    _remove_files(build, main=False)   # its own journal files, if any
    last: Optional[OSError] = None
    for _attempt in range(_SWAP_TRIES):
        release_connections(target)
        try:
            _remove_files(target, main=False)
            os.replace(build, target)
            db_settings.reload()
            return
        except OSError as exc:   # still open somewhere (Windows): try again
            last = exc
            time.sleep(_SWAP_WAIT)
    assert last is not None
    raise last


def _custom_fields_after_swap(db_path: Path, target: Path, result: GsakImportResult,
                              settings_db: Optional[Path]) -> None:
    try:
        conn = _open_readonly(db_path)
    except sqlite3.Error as exc:
        result.errors.append(f"Custom fields not imported: {exc}")
        return
    try:
        _import_custom_fields(conn, target, result.codes, settings_db, result)
    finally:
        conn.close()


def _remove_files(path: Path, main: bool = True) -> None:
    """Delete *path* (when *main*) and its -wal / -shm / -journal files."""
    names = [str(path) + suffix for suffix in ("-wal", "-shm", "-journal")]
    if main:
        names.insert(0, str(path))
    for name in names:
        if os.path.exists(name):
            os.remove(name)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'

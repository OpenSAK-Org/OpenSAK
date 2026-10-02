"""
src/opensak/db/db_settings.py — settings stored inside the database file (#659).

Some settings belong to a database rather than to the app: its home
location, the centre its distances were last calculated from, its column
layout, its sort order and last-used filter profile, … Until 1.21 these
lived in opensak.json, keyed on the database's file path or display name,
so they were lost whenever a database was copied, restored, moved or
renamed outside the app.

They now live in a small key/value table inside the database itself, so
they travel with the file:

    CREATE TABLE db_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)

Values are JSON. A key/value table is forward and backward compatible by
design: a database from an older version simply lacks newer keys (callers
fall back to their defaults), and an older version ignores keys it doesn't
know. Adding a setting never needs a schema migration.

The table is created on first use, not by create_all(), and older OpenSAK
builds ignore it.

Binding rule
------------
Settings are read from and written to the database only when the open
engine *is* the active database in the DatabaseManager. Otherwise (no
database open yet, an import that has temporarily switched the engine to
another database, tests with a stand-in manager) the legacy opensak.json key
the caller passes is used instead — exactly the behaviour before #659.

Migration
---------
The first time a database is bound, the legacy opensak.json values for it
are copied into its table, once (``ensure_seeded()``). The legacy keys are
left in place: opensak.json is shared between installed versions, and an
older build still reads them.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from opensak.settings_store import get_store

logger = logging.getLogger(__name__)

TABLE = "db_settings"
_DDL = f"CREATE TABLE IF NOT EXISTS {TABLE} (key TEXT PRIMARY KEY, value TEXT NOT NULL)"

UUID_KEY = "db_uuid"
_IMPORTED_KEY = "legacy_settings_imported"

# Settings that were keyed on the database's file path in opensak.json
# ("db.<path>.<key>"). The same names are used inside the database.
_LEGACY_PATH_KEYS = (
    "home_lat", "home_lon", "active_home_name",
    "dist_calc_lat", "dist_calc_lon", "dist_calc_method",
    "map_nearby_radius_km", "map_nearby_max_caches",
)
# "sort.<path>.<suffix>" → "sort.<suffix>"
_LEGACY_SORT_SUFFIXES = ("field", "ascending", "filter_profile")
# "columns.<name>.<suffix>" → "columns.<suffix>"
_LEGACY_COLUMN_SUFFIXES = ("visible", "widths")

_lock = threading.RLock()
_cache_engine: Any = None          # the engine _cache was loaded from
_cache: dict[str, Any] = {}


# ── Legacy opensak.json keys (single source of truth for their format) ───────

def legacy_db_key(path: Path | str, key: str) -> str:
    """The pre-#659 opensak.json key for a path-keyed setting."""
    safe = str(path).replace("/", "_").replace("\\", "_")
    return f"db.{safe}.{key}"


def legacy_sort_key(path: Path | str, suffix: str) -> str:
    """The pre-#659 opensak.json key for the sort order / filter profile."""
    return f"sort.{path}.{suffix}"


def legacy_columns_key(name: str, suffix: str) -> str:
    """The pre-#659 opensak.json key for the column layout (by db name)."""
    safe = name.replace(".", "_").replace(" ", "_")
    return f"columns.{safe}.{suffix}"


# ── Public API ────────────────────────────────────────────────────────────────

def get_value(key: str, legacy_key: Optional[str], default: Any = None) -> Any:
    """
    Value of *key* for the active database, or *default* if it isn't set.

    Falls back to *legacy_key* in opensak.json when no database is bound
    (see the module docstring); with ``legacy_key=None`` that fallback
    returns *default*.
    """
    engine = _bound_engine()
    if engine is not None:
        try:
            return _values(engine).get(key, default)
        except Exception:
            logger.warning("db_settings: could not read %r", key, exc_info=True)
    if legacy_key is None:
        return default
    return get_store().get(legacy_key, default)


def set_value(key: str, legacy_key: Optional[str], value: Any) -> None:
    """Store *value* for *key* in the active database (or the legacy key)."""
    engine = _bound_engine()
    if engine is not None:
        try:
            with _lock:
                values = _values(engine)
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"INSERT OR REPLACE INTO {TABLE} (key, value) VALUES (?, ?)",
                        (key, json.dumps(value)),
                    )
                values[key] = value
            return
        except Exception:
            logger.warning("db_settings: could not write %r", key, exc_info=True)
    if legacy_key is not None:
        get_store().set(legacy_key, value)


def ensure_seeded(path: Path, name: str) -> None:
    """
    Make sure the database at *path* has its settings table, a ``db_uuid``,
    and its legacy opensak.json settings imported (once). *name* is the
    database's display name, which the legacy column keys were based on.

    Works on any database file, open or not. Does nothing if the file
    doesn't exist (it is never created here).
    """
    path = Path(path)
    if not path.is_file():
        return
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        conn.execute(_DDL)
        present = {
            row[0] for row in conn.execute(
                f"SELECT key FROM {TABLE} WHERE key IN (?, ?)",
                (UUID_KEY, _IMPORTED_KEY),
            )
        }
        updates: dict[str, Any] = {}
        if UUID_KEY not in present:
            updates[UUID_KEY] = uuid.uuid4().hex
        if _IMPORTED_KEY not in present:
            updates.update(_legacy_values(path, name))
            updates[_IMPORTED_KEY] = True
        if updates:
            # OR IGNORE: never overwrite a value that is already there.
            conn.executemany(
                f"INSERT OR IGNORE INTO {TABLE} (key, value) VALUES (?, ?)",
                [(k, json.dumps(v)) for k, v in updates.items()],
            )
            conn.commit()
    finally:
        conn.close()


def assign_new_uuid(path: Path) -> str:
    """
    Give the database at *path* a new ``db_uuid`` and return it. Used for a
    copy, which is a database of its own from then on (a backup that is
    restored keeps its uuid instead — that's the same database).
    """
    new = uuid.uuid4().hex
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        conn.execute(_DDL)
        conn.execute(
            f"INSERT OR REPLACE INTO {TABLE} (key, value) VALUES (?, ?)",
            (UUID_KEY, json.dumps(new)),
        )
        conn.commit()
    finally:
        conn.close()
    _reset_cache()
    return new


def read_file(path: Path) -> dict[str, Any]:
    """All settings stored in the database file at *path* (open or not)."""
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (TABLE,),
        ).fetchone()
        if not exists:
            return {}
        return _decode_rows(conn.execute(f"SELECT key, value FROM {TABLE}"))
    finally:
        conn.close()


def peek_value(path: Path, key: str, default: Any = None) -> Any:
    """
    Value of path-keyed setting *key* for the database file at *path*, as
    get_value() would return it once that database is bound: its own table,
    or — while its legacy settings haven't been imported yet — the legacy
    opensak.json key that ensure_seeded() would import. Never seeds.

    For code that works on a database that may not be the active one, e.g.
    an import running on a temporarily switched engine.
    """
    values = read_file(path) if Path(path).is_file() else {}
    if key in values:
        return values[key]
    if not values.get(_IMPORTED_KEY):
        value = get_store().get(legacy_db_key(path, key))
        if value is not None and value != "":
            return value
    return default


def write_file(path: Path, values: dict[str, Any]) -> None:
    """
    Store *values* in the database file at *path* (open or not). Safe to call
    from a worker thread: the cached values of the bound database are
    dropped, so the next read sees the new ones.

    Existing keys are replaced. A later ensure_seeded() never overwrites
    them (it only inserts missing keys). Does nothing if the file doesn't
    exist (it is never created here).
    """
    if not Path(path).is_file():
        return
    with _lock:
        conn = sqlite3.connect(str(path), timeout=30)
        try:
            conn.execute(_DDL)
            conn.executemany(
                f"INSERT OR REPLACE INTO {TABLE} (key, value) VALUES (?, ?)",
                [(k, json.dumps(v)) for k, v in values.items()],
            )
            conn.commit()
        finally:
            conn.close()
        _reset_cache()


def _reset_cache() -> None:
    """Forget cached values; the next access reloads them (also for tests)."""
    global _cache_engine, _cache
    with _lock:
        _cache_engine = None
        _cache = {}


# ── Internals ─────────────────────────────────────────────────────────────────

def _bound_engine() -> Any:
    """
    The open engine if it belongs to the active database, else None.

    Compared on the path init_db() was given, which is the manager's path
    for the active database, so no filesystem access is needed here.
    """
    from opensak.db import database

    engine = database._engine
    if engine is None:
        return None
    # Everything in one try: whatever goes wrong while deciding (no manager,
    # an active entry without a usable path, …) must mean "not bound", so
    # the caller falls back to the legacy key instead of failing.
    try:
        from opensak.db.manager import get_db_manager
        active = get_db_manager().active
        if active is None:
            return None
        if Path(engine.url.database or "") != Path(active.path):
            return None
    except Exception:
        return None
    return engine


def _values(engine: Any) -> dict[str, Any]:
    """Cached settings of the bound database, loaded (and seeded) on first use."""
    global _cache_engine, _cache
    with _lock:
        if _cache_engine is not engine:
            from opensak.db.manager import get_db_manager
            active = get_db_manager().active
            if active is None:  # switched away since _bound_engine() checked
                raise RuntimeError("no active database")
            ensure_seeded(Path(active.path), active.name)
            with engine.connect() as conn:
                _cache = _decode_rows(
                    conn.exec_driver_sql(f"SELECT key, value FROM {TABLE}")
                )
            _cache_engine = engine
        return _cache


def _decode_rows(rows: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, raw in rows:
        try:
            values[key] = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("db_settings: ignoring unreadable value for %r", key)
    return values


def _legacy_values(path: Path, name: str) -> dict[str, Any]:
    """This database's settings as stored in opensak.json before #659."""
    store = get_store()
    found: dict[str, Any] = {}

    def _take(db_key: str, legacy_key: str) -> None:
        value = store.get(legacy_key)
        if value is not None and value != "":
            found[db_key] = value

    for key in _LEGACY_PATH_KEYS:
        _take(key, legacy_db_key(path, key))
    for suffix in _LEGACY_SORT_SUFFIXES:
        _take(f"sort.{suffix}", legacy_sort_key(path, suffix))
    for suffix in _LEGACY_COLUMN_SUFFIXES:
        _take(f"columns.{suffix}", legacy_columns_key(name, suffix))
    return found

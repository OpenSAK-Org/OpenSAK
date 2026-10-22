"""
src/opensak/db/companion.py — the companion database of a cache database.

Every cache database can have a companion: a second SQLite file next to it,
``Found.db`` → ``Found.companion.db3``, for data that doesn't belong in the
main schema — tables of Lua macros, shared by all macros, and later the
custom fields (#1028). Design: discussion #1034 and plans/companion-database.md.

The ``.db3`` suffix keeps it out of the database scan (db/discover.py looks
for ``.db``) and away from the macros' file functions, which refuse every
SQLite file (macro/permissions.py).

The file belongs to its database and follows it on rename, move, copy,
delete, backup and restore (db/manager.py, backup/). It is created only when
something first writes to it (create()); most databases never get one.

While the ``companion-db`` feature flag is on, every connection of the
engines made by db/database.py attaches it as COMPANION_SCHEMA, if it exists
and prepare() found it usable. The main database never depends on it: a
missing, damaged, newer or foreign companion is logged and simply not
attached.

It has its own schema version (``PRAGMA user_version``) and migrations,
separate from the main database's, and stores the main database's db_uuid
(db_settings.py). A companion copied next to another database by hand has a
different db_uuid and is not attached.

This module has no UI and doesn't depend on SQLAlchemy.
"""

from __future__ import annotations

import enum
import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

SUFFIX = ".companion.db3"
# The schema name under which the companion is attached. Reserved for macros
# in macro/sql.py (_RESERVED_SCHEMAS).
COMPANION_SCHEMA = "companion"
SCHEMA_VERSION = 1

META_TABLE = "opensak_meta"
REGISTRY_TABLE = "opensak_tables"
# Owner of the tables OpenSAK itself keeps in the companion.
OWNER_OPENSAK = "opensak"
KIND_CACHE = "cache"    # cache-linked: rows follow the cache by its GC code
KIND_FREE = "free"

MAIN_UUID_KEY = "main_db_uuid"

_SIDECARS = ("-wal", "-shm", "-journal")

_SCHEMA_V1 = (
    f"CREATE TABLE {META_TABLE} (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    f"""CREATE TABLE {REGISTRY_TABLE} (
        name TEXT PRIMARY KEY COLLATE NOCASE,
        owner TEXT NOT NULL,
        owner_name TEXT,
        kind TEXT NOT NULL CHECK (kind IN ('{KIND_CACHE}', '{KIND_FREE}')),
        code_column TEXT,
        description TEXT,
        created_at TEXT NOT NULL,
        created_by_version TEXT
    )""",
)

# Migrations from schema N-1 to N, run in order by _migrate(). Version 1 is
# the schema create() writes, so there are none yet.
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {}


class CompanionError(Exception):
    """The companion database can't be created or used."""


class Status(enum.Enum):
    """What prepare() found next to a database."""

    MISSING = "missing"     # no companion (yet)
    OK = "ok"               # usable: attached by the engines
    NEWER = "newer"         # made by a newer OpenSAK — left alone
    FOREIGN = "foreign"     # belongs to another database (db_uuid differs)
    BROKEN = "broken"       # not readable as a companion database


_lock = threading.Lock()
_status: dict[str, Status] = {}


# ── Paths ─────────────────────────────────────────────────────────────────────

def companion_path(db_path: Path) -> Path:
    """The companion file of the database at *db_path* (whether it exists or not)."""
    db_path = Path(db_path)
    return db_path.with_name(db_path.stem + SUFFIX)


def is_companion_file(path: Path) -> bool:
    """True if *path* is named like a companion database."""
    return Path(path).name.lower().endswith(SUFFIX)


def files(db_path: Path) -> list[Path]:
    """The companion of *db_path* and its sidecars that exist, companion first."""
    path = companion_path(db_path)
    return [p for p in (path, *(_with_suffix(path, s) for s in _SIDECARS)) if p.exists()]


def exists(db_path: Path) -> bool:
    """True if the database at *db_path* has a companion file."""
    return companion_path(db_path).is_file()


def _with_suffix(path: Path, suffix: str) -> Path:
    return Path(str(path) + suffix)


# ── Status ────────────────────────────────────────────────────────────────────

def _key(db_path: Path) -> str:
    try:
        resolved = Path(db_path).resolve()
    except OSError:
        resolved = Path(db_path).absolute()
    return os.path.normcase(str(resolved))


def status(db_path: Path) -> Status:
    """What the last prepare() or create() found for *db_path*."""
    return status_for_key(_key(db_path))


def status_for_key(key: str) -> Status:
    """status() for a key from status_key() — no path resolving, for hot paths."""
    with _lock:
        return _status.get(key, Status.MISSING)


def status_key(db_path: Path) -> str:
    """The key status_for_key() takes for *db_path*."""
    return _key(db_path)


def _set_status(db_path: Path, value: Status) -> Status:
    with _lock:
        _status[_key(db_path)] = value
    return value


def forget(db_path: Path) -> None:
    """Drop what is known about *db_path*'s companion (its file was moved,
    renamed or deleted); the next prepare() looks again."""
    with _lock:
        _status.pop(_key(db_path), None)


def reset() -> None:
    """Forget every status (tests)."""
    with _lock:
        _status.clear()


# ── Opening ───────────────────────────────────────────────────────────────────

def prepare(db_path: Path) -> Status:
    """
    Check the companion of *db_path* and bring its schema up to date, so the
    engines can attach it. Called by db/database.py whenever it opens a
    database. Never raises: a companion that can't be used is logged and
    left as it is, and the main database opens normally.
    """
    path = companion_path(db_path)
    if not path.is_file():
        return _set_status(db_path, Status.MISSING)
    try:
        conn = _connect(path)
    except sqlite3.Error as exc:
        logger.warning("companion: cannot open %s — not used: %s", path, exc)
        return _set_status(db_path, Status.BROKEN)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            logger.warning(
                "companion: %s has schema %s, newer than this OpenSAK (%s) — not used",
                path, version, SCHEMA_VERSION,
            )
            return _set_status(db_path, Status.NEWER)
        if not _has_table(conn, META_TABLE) or not _has_table(conn, REGISTRY_TABLE):
            logger.warning("companion: %s is not an OpenSAK companion database — not used", path)
            return _set_status(db_path, Status.BROKEN)
        if not _same_database(conn, db_path):
            logger.warning(
                "companion: %s belongs to another database (different db_uuid) — not used",
                path,
            )
            return _set_status(db_path, Status.FOREIGN)
        if version < SCHEMA_VERSION:
            conn.close()
            from opensak.backup.premigration import backup_before_migration
            backup_before_migration(path, SCHEMA_VERSION)
            conn = _connect(path)
            _migrate(conn, version)
    except sqlite3.Error as exc:
        logger.warning("companion: %s is not usable — not used: %s", path, exc)
        return _set_status(db_path, Status.BROKEN)
    finally:
        conn.close()
    return _set_status(db_path, Status.OK)


def create(db_path: Path) -> Path:
    """
    Create the companion of the database at *db_path* if it has none, and
    return its path. The file is written under a temporary name and renamed
    into place when complete, so a half-made companion never exists.

    Raises CompanionError if the database is missing, if a companion is
    there that can't be used (see Status), or if the file can't be written.
    """
    db_path = Path(db_path)
    path = companion_path(db_path)
    if not db_path.is_file():
        raise CompanionError(f"the database {db_path} is missing")
    if path.is_file():
        found = prepare(db_path)
        if found is not Status.OK:
            raise CompanionError(f"{path} can't be used ({found.value})")
        return path

    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        conn = sqlite3.connect(str(tmp), timeout=30)
        try:
            with conn:
                for ddl in _SCHEMA_V1:
                    conn.execute(ddl)
                _write_meta(conn, {
                    MAIN_UUID_KEY: _main_uuid(db_path),
                    "created_at": _now(),
                    "created_by_version": _version(),
                })
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        finally:
            conn.close()
        os.replace(tmp, path)
    except (OSError, sqlite3.Error) as exc:
        for p in (tmp, *(_with_suffix(tmp, s) for s in _SIDECARS)):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        raise CompanionError(f"cannot create {path}: {exc}") from exc
    logger.info("companion: created %s", path)
    _set_status(db_path, Status.OK)
    return path


def attach(dbapi_connection: Any, db_path: Path) -> bool:
    """
    ATTACH the companion of *db_path* to *dbapi_connection* as
    COMPANION_SCHEMA. Returns False (and logs) if that fails; the connection
    stays usable for the main database.
    """
    path = companion_path(db_path)
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"ATTACH DATABASE ? AS {COMPANION_SCHEMA}", (str(path),))
        cursor.execute(f"PRAGMA {COMPANION_SCHEMA}.journal_mode=WAL")
        return True
    except sqlite3.Error as exc:
        logger.warning("companion: cannot attach %s — not used: %s", path, exc)
        _set_status(db_path, Status.BROKEN)
        try:
            cursor.execute(f"DETACH DATABASE {COMPANION_SCHEMA}")
        except sqlite3.Error:
            pass
        return False
    finally:
        cursor.close()


def set_main_uuid(db_path: Path, value: Optional[str]) -> None:
    """Record *value* as the db_uuid of the main database in the companion of
    *db_path* (after a copy got a new id). Nothing if it has no companion."""
    path = companion_path(db_path)
    if not path.is_file():
        return
    conn = _connect(path)
    try:
        with conn:
            _write_meta(conn, {MAIN_UUID_KEY: value})
    finally:
        conn.close()


def read_meta(db_path: Path) -> dict[str, Any]:
    """The companion's opensak_meta values ({} without a companion)."""
    path = companion_path(db_path)
    if not path.is_file():
        return {}
    conn = _connect(path)
    try:
        return _read_meta(conn)
    finally:
        conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def _read_meta(conn: sqlite3.Connection) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, raw in conn.execute(f"SELECT key, value FROM {META_TABLE}"):
        try:
            values[key] = json.loads(raw)
        except ValueError:
            values[key] = raw
    return values


def _write_meta(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    conn.executemany(
        f"INSERT OR REPLACE INTO {META_TABLE} (key, value) VALUES (?, ?)",
        [(k, json.dumps(v)) for k, v in values.items()],
    )


def _main_uuid(db_path: Path) -> Optional[str]:
    from opensak.db.db_settings import UUID_KEY, read_file
    try:
        value = read_file(db_path).get(UUID_KEY)
    except sqlite3.Error:
        return None
    return value if isinstance(value, str) and value else None


def _same_database(conn: sqlite3.Connection, db_path: Path) -> bool:
    """
    False only if both files name a db_uuid and they differ. A companion
    made before its database had an id takes the id over.
    """
    recorded = _read_meta(conn).get(MAIN_UUID_KEY)
    actual = _main_uuid(db_path)
    if not recorded or not actual:
        if actual and not recorded:
            with conn:
                _write_meta(conn, {MAIN_UUID_KEY: actual})
        return True
    return bool(recorded == actual)


def _migrate(conn: sqlite3.Connection, from_version: int) -> None:
    for version in range(from_version + 1, SCHEMA_VERSION + 1):
        step = _MIGRATIONS.get(version)
        with conn:
            if step is not None:
                step(conn)
        conn.execute(f"PRAGMA user_version = {version}")
        logger.info("companion: migrated to schema %s", version)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _version() -> str:
    from opensak import __version__
    return __version__

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
attached. A damaged one is moved aside (``*.companion.corrupt-<time>.db3``)
and the user is told (take_notices()); the next write creates a new one.

It has its own schema version (``PRAGMA user_version``) and migrations,
separate from the main database's, and stores the main database's db_uuid
(db_settings.py). A companion copied next to another database by hand has a
different db_uuid and is not attached.

Cache-linked tables
-------------------
A table whose rows belong to caches is *cache-linked*: its rows follow the
cache by GC code. Every table with a ``gc_code`` column is one, unless the
registry (REGISTRY_TABLE) says ``kind = 'free'``; the registry can also name
another code column. OpenSAK keeps them in step with the caches:

* deleting caches deletes their rows (delete_rows_in(), delete_rows());
* copying or moving caches to another database copies their rows, creating
  a missing table there first (copy_rows());
* rows whose cache no longer exists ("orphans") are removed when the
  database is opened (prepare()).

Orphans happen because a transaction across attached files is not atomic in
WAL mode. SQLite commits the main database first, so a crash in between
leaves rows without a cache in the companion, never a cache that lost its
rows — provided writes go to the main database first and the companion
second, as everything here does.

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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

SUFFIX = ".companion.db3"
# A damaged companion is renamed to <stem>.companion.corrupt-<time>.db3.
CORRUPT_MARK = ".companion.corrupt-"
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
# The code column of a cache-linked table the registry says nothing about.
DEFAULT_CODE_COLUMN = "gc_code"

MAIN_UUID_KEY = "main_db_uuid"

_SIDECARS = ("-wal", "-shm", "-journal")
# Codes per statement, well below SQLite's bound-parameter limit.
_CHUNK = 500
# Schema name the main database is attached under for the orphan check.
_MAIN_ALIAS = "opensak_main"
# ... and the source companion for copy_rows().
_SOURCE_ALIAS = "opensak_source"

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


class _Damaged(Exception):
    """The companion is not usable as one (internal to prepare())."""


class Status(enum.Enum):
    """What prepare() found next to a database."""

    MISSING = "missing"     # no companion (yet)
    OK = "ok"               # usable: attached by the engines
    NEWER = "newer"         # made by a newer OpenSAK — left alone
    FOREIGN = "foreign"     # belongs to another database (db_uuid differs)
    BROKEN = "broken"       # not readable as a companion database


# Kinds of Notice.
NOTICE_CORRUPT = "corrupt"      # damaged; moved aside to Notice.path
NOTICE_FOREIGN = "foreign"      # belongs to another database; left alone
NOTICE_NEWER = "newer"          # from a newer OpenSAK; left alone


@dataclass(frozen=True)
class Notice:
    """Something the user should hear about a companion (take_notices())."""

    db_path: Path
    kind: str
    path: Path      # the companion, or where a damaged one was moved to


_lock = threading.Lock()
_status: dict[str, Status] = {}
# Databases whose companion was checked (quick_check, orphans) this session.
_checked: set[str] = set()
_notices: list[Notice] = []
_noticed: set[tuple[str, str]] = set()


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


# ── Status and notices ────────────────────────────────────────────────────────

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
    key = _key(db_path)
    with _lock:
        _status.pop(key, None)
        _checked.discard(key)


def reset() -> None:
    """Forget every status and notice (tests)."""
    with _lock:
        _status.clear()
        _checked.clear()
        _notices.clear()
        _noticed.clear()


def take_notices() -> list[Notice]:
    """Return and clear what the user should be told about companions. Each
    problem is reported once per session."""
    with _lock:
        notices = list(_notices)
        _notices.clear()
    return notices


def _notice(db_path: Path, kind: str, path: Path) -> None:
    key = (_key(db_path), kind)
    with _lock:
        if key in _noticed:
            return
        _noticed.add(key)
        _notices.append(Notice(Path(db_path), kind, path))


# ── Opening ───────────────────────────────────────────────────────────────────

def prepare(db_path: Path) -> Status:
    """
    Check the companion of *db_path* and bring its schema up to date, so the
    engines can attach it. Called by db/database.py whenever it opens a
    database. The first time in a session it also runs ``PRAGMA
    quick_check`` and removes orphan rows (see the module docstring).

    A damaged companion is moved aside and reported (take_notices()); one
    from a newer OpenSAK or of another database is left as it is and
    reported. Never raises: the main database opens normally either way.
    """
    path = companion_path(db_path)
    if not path.is_file():
        return _set_status(db_path, Status.MISSING)
    key = _key(db_path)
    with _lock:
        first = key not in _checked
    try:
        conn = _connect(path)
    except sqlite3.Error as exc:
        return _unusable(db_path, exc)
    try:
        found = _inspect(conn, db_path, first)
    except _Damaged as exc:
        conn.close()
        return _damaged(db_path, str(exc))
    except sqlite3.Error as exc:
        conn.close()
        return _unusable(db_path, exc)
    conn.close()
    if found is Status.OK and first:
        with _lock:
            _checked.add(key)
    return _set_status(db_path, found)


def _inspect(conn: sqlite3.Connection, db_path: Path, first: bool) -> Status:
    path = companion_path(db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        logger.warning(
            "companion: %s has schema %s, newer than this OpenSAK (%s) — not used",
            path, version, SCHEMA_VERSION,
        )
        _notice(db_path, NOTICE_NEWER, path)
        return Status.NEWER
    if not _has_table(conn, META_TABLE) or not _has_table(conn, REGISTRY_TABLE):
        raise _Damaged("it is not an OpenSAK companion database")
    if not _same_database(conn, db_path):
        logger.warning(
            "companion: %s belongs to another database (different db_uuid) — not used",
            path,
        )
        _notice(db_path, NOTICE_FOREIGN, path)
        return Status.FOREIGN
    if first:
        problems = _check_rows(conn, "quick_check")
        if problems:
            raise _Damaged("quick_check: " + "; ".join(problems[:5]))
    if version < SCHEMA_VERSION:
        from opensak.backup.premigration import backup_before_migration
        backup_before_migration(path, SCHEMA_VERSION)
        _migrate(conn, version)
    if first:
        removed = _remove_orphans(conn, db_path)
        if removed:
            logger.info("companion: removed %d orphan row(s) from %s", removed, path)
    return Status.OK


def _unusable(db_path: Path, exc: sqlite3.Error) -> Status:
    """An SQLite error while checking: a damaged file is moved aside; one
    that is only busy or unreadable right now (OperationalError: locked, no
    access) is left in place and not used this time."""
    if isinstance(exc, sqlite3.OperationalError) and "malformed" not in str(exc):
        logger.warning("companion: %s can't be used now — %s", companion_path(db_path), exc)
        return _set_status(db_path, Status.BROKEN)
    return _damaged(db_path, str(exc))


def _damaged(db_path: Path, reason: str) -> Status:
    """Move a damaged companion aside, report it, and return BROKEN. Once
    moved, the database has no companion: the next write creates a new one."""
    path = companion_path(db_path)
    logger.warning("companion: %s is damaged — %s", path, reason)
    target = path.with_name(
        path.name[: -len(SUFFIX)] + CORRUPT_MARK
        + datetime.now().strftime("%Y%m%d-%H%M%S") + ".db3"
    )
    try:
        for src in files(db_path):
            src.rename(Path(str(target) + src.name[len(path.name):]))
    except OSError as exc:
        # Most likely still open somewhere (Windows): left in place, not used.
        logger.warning("companion: could not move %s aside: %s", path, exc)
        _set_status(db_path, Status.BROKEN)
        return Status.BROKEN
    logger.warning("companion: moved the damaged %s to %s", path.name, target.name)
    _notice(db_path, NOTICE_CORRUPT, target)
    _set_status(db_path, Status.MISSING)
    return Status.BROKEN


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
    with _lock:
        _checked.add(_key(db_path))
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


def is_attached(conn: Any) -> bool:
    """True if the companion is attached to the sqlite3 connection *conn*."""
    return any(row[1] == COMPANION_SCHEMA for row in conn.execute("PRAGMA database_list"))


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


# ── Cache-linked tables ───────────────────────────────────────────────────────

def cache_linked_tables(conn: Any, schema: str = "main") -> list[tuple[str, str]]:
    """
    (table, code column) of the cache-linked tables in the companion open on
    *conn* as *schema* ("main" on its own connection, COMPANION_SCHEMA when
    attached). See the module docstring for the rule.
    """
    s = _q(schema)
    registry = {
        str(name).lower(): (kind, code_column)
        for name, kind, code_column in conn.execute(
            f"SELECT name, kind, code_column FROM {s}.{REGISTRY_TABLE}"
        )
    }
    linked: list[tuple[str, str]] = []
    for (name,) in conn.execute(
        f"SELECT name FROM {s}.sqlite_master WHERE type = 'table' ORDER BY name"
    ):
        if _is_internal(name):
            continue
        kind, code_column = registry.get(name.lower(), (None, None))
        if kind == KIND_FREE:
            continue
        wanted = (code_column or DEFAULT_CODE_COLUMN).lower()
        column = next(
            (row[1] for row in conn.execute(f"PRAGMA {s}.table_info({_q(name)})")
             if str(row[1]).lower() == wanted),
            None,
        )
        if column is None:
            if kind == KIND_CACHE:
                logger.warning(
                    "companion: table %s is registered as cache-linked but has no "
                    "column %s — left alone", name, wanted,
                )
            continue
        linked.append((name, column))
    return linked


def delete_rows_in(conn: Any, codes: Iterable[str], schema: str = COMPANION_SCHEMA) -> int:
    """
    Delete the rows of the caches *codes* from every cache-linked table of
    the companion attached to *conn* as *schema*, inside the caller's
    transaction. Does nothing (returns 0) if it isn't attached. Returns the
    number of rows deleted.
    """
    if schema != "main" and not any(row[1] == schema for row in conn.execute("PRAGMA database_list")):
        return 0
    return _delete_codes(conn, list(dict.fromkeys(c for c in codes if c)), schema)


def delete_rows(db_path: Path, codes: Iterable[str]) -> int:
    """
    Delete the rows of the caches *codes* from the companion of *db_path*,
    on its own connection, in its own transaction — for callers whose
    connection doesn't attach it. Call it after the caches' deletion is
    committed. Never raises; returns the number of rows deleted.
    """
    codes = list(dict.fromkeys(c for c in codes if c))
    if not codes or status(db_path) is not Status.OK or not exists(db_path):
        return 0
    try:
        conn = _connect(companion_path(db_path))
    except sqlite3.Error as exc:
        logger.warning("companion: cannot delete rows from %s: %s", companion_path(db_path), exc)
        return 0
    try:
        with conn:
            return _delete_codes(conn, codes, "main")
    except sqlite3.Error as exc:
        logger.warning("companion: cannot delete rows from %s: %s", companion_path(db_path), exc)
        return 0
    finally:
        conn.close()


def copy_rows(source_db: Path, target_db: Path, codes: Iterable[str]) -> int:
    """
    Copy the rows of the caches *codes* from the companion of *source_db* to
    the companion of *target_db*, replacing the target's rows for those
    codes (the caches themselves were just replaced there). The target's
    companion and missing tables (with their indexes and registry row) are
    created first; columns the target table lacks are left out.

    Call it after the caches are committed to the target. Never raises: the
    caches are copied already, so a problem is logged and the rows that
    could be copied stay. Returns the number of rows copied.
    """
    codes = list(dict.fromkeys(c for c in codes if c))
    if not codes or not exists(source_db) or prepare(source_db) is not Status.OK:
        return 0
    source = companion_path(source_db)
    try:
        src = _connect(source)
        try:
            tables = [
                (name, column) for name, column in cache_linked_tables(src)
                if _has_rows(src, name, column, codes)
            ]
        finally:
            src.close()
    except sqlite3.Error as exc:
        logger.warning("companion: cannot read %s to copy rows: %s", source, exc)
        return 0
    if not tables:
        return 0

    try:
        target = create(target_db)
    except CompanionError as exc:
        logger.warning("companion: rows of %d cache(s) not copied to %s: %s",
                       len(codes), target_db, exc)
        return 0
    copied = 0
    try:
        conn = _connect(target)
    except sqlite3.Error as exc:
        logger.warning("companion: cannot open %s to copy rows: %s", target, exc)
        return 0
    try:
        conn.execute(f"ATTACH DATABASE ? AS {_SOURCE_ALIAS}", (str(source),))
        # Custom fields (#1028): the target needs the fields before their
        # values, or columns it lacks would be left out below.
        from opensak.db import custom_fields
        try:
            with conn:
                custom_fields.copy_definitions(conn, _SOURCE_ALIAS)
            custom_fields.changed(target_db)
        except sqlite3.Error as exc:
            logger.warning("companion: custom field definitions not copied to %s: %s",
                           target, exc)
        for name, column in tables:
            try:
                with conn:
                    copied += _copy_table(conn, name, column, codes)
            except sqlite3.Error as exc:
                logger.warning("companion: rows of table %s not copied to %s: %s",
                               name, target, exc)
    except sqlite3.Error as exc:
        logger.warning("companion: cannot copy rows from %s to %s: %s", source, target, exc)
    finally:
        conn.close()
    return copied


def _copy_table(conn: sqlite3.Connection, name: str, column: str, codes: list[str]) -> int:
    src = _SOURCE_ALIAS
    if not _has_table(conn, name):
        for (sql,) in conn.execute(
            f"SELECT sql FROM {src}.sqlite_master WHERE tbl_name = ? AND sql IS NOT NULL "
            "AND type IN ('table', 'index') ORDER BY type = 'index'",
            (name,),
        ).fetchall():
            conn.execute(sql)
        conn.execute(
            f"INSERT OR IGNORE INTO {REGISTRY_TABLE} SELECT * FROM {src}.{REGISTRY_TABLE} "
            "WHERE name = ?", (name,),
        )
    target_columns = {str(row[1]).lower() for row in conn.execute(f"PRAGMA table_info({_q(name)})")}
    if column.lower() not in target_columns:
        logger.warning("companion: table %s in the target has no column %s — rows not copied",
                       name, column)
        return 0
    columns = ", ".join(
        _q(row[1]) for row in conn.execute(f"PRAGMA {src}.table_info({_q(name)})")
        if str(row[1]).lower() in target_columns
    )
    copied = 0
    for chunk in _chunks(codes):
        marks = ", ".join("?" * len(chunk))
        conn.execute(f"DELETE FROM main.{_q(name)} WHERE {_q(column)} IN ({marks})", chunk)
        cursor = conn.execute(
            f"INSERT INTO main.{_q(name)} ({columns}) SELECT {columns} FROM {src}.{_q(name)} "
            f"WHERE {_q(column)} IN ({marks})", chunk,
        )
        copied += max(cursor.rowcount, 0)
    return copied


def _delete_codes(conn: Any, codes: list[str], schema: str) -> int:
    if not codes:
        return 0
    s = _q(schema)
    deleted = 0
    for name, column in cache_linked_tables(conn, schema):
        for chunk in _chunks(codes):
            cursor = conn.execute(
                f"DELETE FROM {s}.{_q(name)} WHERE {_q(column)} IN ({', '.join('?' * len(chunk))})",
                chunk,
            )
            deleted += max(cursor.rowcount, 0)
    return deleted


def _has_rows(conn: sqlite3.Connection, name: str, column: str, codes: list[str]) -> bool:
    for chunk in _chunks(codes):
        if conn.execute(
            f"SELECT 1 FROM {_q(name)} WHERE {_q(column)} IN ({', '.join('?' * len(chunk))}) LIMIT 1",
            chunk,
        ).fetchone():
            return True
    return False


def _remove_orphans(conn: sqlite3.Connection, db_path: Path) -> int:
    """Delete the rows of cache-linked tables whose cache is not in the main
    database. Nothing for a main database without caches table (new)."""
    with _main_attached(conn, db_path) as attached:
        if not attached:
            return 0
        removed = 0
        with conn:
            for name, column in cache_linked_tables(conn):
                cursor = conn.execute(
                    f"DELETE FROM main.{_q(name)} WHERE {_q(column)} IS NOT NULL AND "
                    f"{_q(column)} NOT IN (SELECT gc_code FROM {_MAIN_ALIAS}.caches)"
                )
                removed += max(cursor.rowcount, 0)
        return removed


class _main_attached:
    """Attach the main database of *db_path* to *conn* (the companion's own
    connection) as _MAIN_ALIAS while the block runs; yields whether it has
    a caches table."""

    def __init__(self, conn: sqlite3.Connection, db_path: Path):
        self._conn = conn
        self._db_path = Path(db_path)
        self._attached = False

    def __enter__(self) -> bool:
        if not self._db_path.is_file():
            return False
        self._conn.execute(f"ATTACH DATABASE ? AS {_MAIN_ALIAS}", (str(self._db_path),))
        self._attached = True
        return self._conn.execute(
            f"SELECT 1 FROM {_MAIN_ALIAS}.sqlite_master WHERE type = 'table' AND name = 'caches'"
        ).fetchone() is not None

    def __exit__(self, *_exc: object) -> None:
        if self._attached:
            self._conn.execute(f"DETACH DATABASE {_MAIN_ALIAS}")


# ── Maintenance (#1048) ───────────────────────────────────────────────────────
# The companion side of the Database Manager's maintenance (#1048). Every
# function works on the companion file on its own connection; the caller
# runs them together with the same action on the main database. Compact is
# maintenance.vacuum_with_companion().

@dataclass(frozen=True)
class TableInfo:
    """One table of a companion, for Info."""

    name: str
    owner: Optional[str]            # None: not in the registry
    owner_name: Optional[str]
    kind: str                       # KIND_CACHE or KIND_FREE (as treated)
    rows: int


@dataclass(frozen=True)
class CompanionInfo:
    """Info (#1048) for a companion."""

    path: Path
    file_bytes: int
    wal_bytes: int
    free_bytes: int                 # what Compact would give back
    schema_version: int
    tables: tuple[TableInfo, ...]


@dataclass(frozen=True)
class CheckResult:
    """Check (#1048) for a companion. ok when nothing was found."""

    path: Path
    problems: tuple[str, ...] = ()          # integrity / quick_check messages
    foreign_key_problems: int = 0
    uuid_mismatch: bool = False
    orphans: dict[str, int] = field(default_factory=dict)   # table → orphan rows
    unregistered: tuple[str, ...] = ()      # tables without a registry row
    missing: tuple[str, ...] = ()           # registry rows without a table

    @property
    def ok(self) -> bool:
        # Unregistered tables are allowed (the gc_code convention); not a problem.
        return not (self.problems or self.foreign_key_problems or self.uuid_mismatch
                    or self.orphans or self.missing)


def info(db_path: Path) -> Optional[CompanionInfo]:
    """Info for the companion of *db_path*, None without one. Raises
    sqlite3.Error if it can't be read."""
    path = companion_path(db_path)
    if not path.is_file():
        return None
    conn = _connect(path)
    try:
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        free = conn.execute("PRAGMA freelist_count").fetchone()[0] * page_size
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        registry = {
            str(row[0]).lower(): row for row in conn.execute(
                f"SELECT name, owner, owner_name, kind FROM {REGISTRY_TABLE}"
            )
        }
        linked = {name for name, _column in cache_linked_tables(conn)}
        tables = []
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall():
            if _is_internal(name):
                continue
            row = registry.get(name.lower())
            rows = conn.execute(f"SELECT count(*) FROM {_q(name)}").fetchone()[0]
            tables.append(TableInfo(
                name=name,
                owner=row[1] if row else None,
                owner_name=row[2] if row else None,
                kind=KIND_CACHE if name in linked else KIND_FREE,
                rows=rows,
            ))
    finally:
        conn.close()
    return CompanionInfo(
        path=path,
        file_bytes=_size(path),
        wal_bytes=_size(_with_suffix(path, "-wal")),
        free_bytes=free,
        schema_version=version,
        tables=tuple(tables),
    )


def check(db_path: Path, thorough: bool = False) -> Optional[CheckResult]:
    """
    Check (#1048) the companion of *db_path*: ``quick_check`` (or
    ``integrity_check`` when *thorough*) and ``foreign_key_check``, plus the
    companion's own consistency — db_uuid, registry against the real tables,
    orphan rows. Changes nothing. None without a companion. Raises
    sqlite3.Error if it can't be read at all.
    """
    path = companion_path(db_path)
    if not path.is_file():
        return None
    conn = _connect(path)
    try:
        problems = _check_rows(conn, "integrity_check" if thorough else "quick_check")
        if problems:
            # Further checks would read a damaged file; report what we have.
            return CheckResult(path, problems=tuple(problems))
        fk = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        recorded = _read_meta(conn).get(MAIN_UUID_KEY)
        actual = _main_uuid(db_path)
        tables = {
            name.lower(): name for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ) if not _is_internal(name)
        }
        registered = {
            str(name).lower(): name
            for (name,) in conn.execute(f"SELECT name FROM {REGISTRY_TABLE}")
        }
        orphans: dict[str, int] = {}
        with _main_attached(conn, db_path) as attached:
            if attached:
                for name, column in cache_linked_tables(conn):
                    count = conn.execute(
                        f"SELECT count(*) FROM main.{_q(name)} WHERE {_q(column)} IS NOT NULL "
                        f"AND {_q(column)} NOT IN (SELECT gc_code FROM {_MAIN_ALIAS}.caches)"
                    ).fetchone()[0]
                    if count:
                        orphans[name] = count
    finally:
        conn.close()
    return CheckResult(
        path=path,
        foreign_key_problems=fk,
        uuid_mismatch=bool(recorded and actual and recorded != actual),
        orphans=orphans,
        unregistered=tuple(sorted(n for k, n in tables.items() if k not in registered)),
        missing=tuple(sorted(n for k, n in registered.items() if k not in tables)),
    )


def optimize(db_path: Path) -> bool:
    """Optimize (#1048): refresh the companion's query planner statistics.
    False if it has none or it failed (logged). Never raises."""
    return _pragma(db_path, _optimize_pragma())


def checkpoint(db_path: Path) -> bool:
    """Checkpoint (#1048): write the companion's WAL back and empty the -wal
    file. False if it has none or it failed (logged). Never raises."""
    return _pragma(db_path, "PRAGMA wal_checkpoint(TRUNCATE)")


def _optimize_pragma() -> str:
    # SQLite >= 3.46: 0x10002 also analyzes tables that never had statistics
    # (as #1048 proposes for the main database).
    return "PRAGMA optimize=0x10002" if sqlite3.sqlite_version_info >= (3, 46) else "PRAGMA optimize"


def _pragma(db_path: Path, statement: str) -> bool:
    path = companion_path(db_path)
    if not path.is_file():
        return False
    try:
        conn = _connect(path)
        try:
            conn.execute(statement).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("companion: %s on %s failed: %s", statement, path, exc)
        return False
    return True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def _q(name: str) -> str:
    """*name* as a quoted SQL identifier."""
    return '"' + str(name).replace('"', '""') + '"'


def _is_internal(name: str) -> bool:
    lower = str(name).lower()
    return lower.startswith("sqlite_") or lower.startswith("opensak_")


def _chunks(codes: list[str]) -> Iterable[list[str]]:
    for start in range(0, len(codes), _CHUNK):
        yield codes[start:start + _CHUNK]


def _check_rows(conn: sqlite3.Connection, pragma: str) -> list[str]:
    rows = [str(r[0]) for r in conn.execute(f"PRAGMA {pragma}").fetchall()]
    return [] if rows == ["ok"] else rows


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? COLLATE NOCASE", (name,)
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

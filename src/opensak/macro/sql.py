"""
src/opensak/macro/sql.py — read-only SQL access for Lua macros.

opensak.sql() runs arbitrary SELECTs against the active database (or another
one, with the `database` option). Read-only
is enforced by the connection, never by inspecting the SQL text:

  * the file is opened with `mode=ro`, so SQLite refuses every write at the
    file level — a `PRAGMA query_only = OFF` cannot undo that;
  * `PRAGMA query_only = ON` on top;
  * an authorizer allows only reading statements. It also denies ATTACH,
    which would otherwise let a macro read any database file on disk past
    the folder permissions, and every PRAGMA except the schema-info ones.

TEMP tables, views and indexes are the exception: the authorizer lets a
macro create, fill, change and drop them (db_name "temp"). SQLite keeps
them in memory or a temp file of the connection, never in the database
file, and drops them when the connection closes — at the end of the macro
run. `mode=ro` does not cover the temp database, but `query_only` does, so
the read-only connections turn it off again. A TEMP object may not take
the name of an object of the main database, which it would shadow for
OpenSAK's own unqualified statements on the same connection, and names
starting with _RESERVED_PREFIX belong to OpenSAK. TEMP triggers stay
refused: they could be put on main tables.

The macro's own database file (opensak.macro_db()) is attached to that
connection as MACRO_SCHEMA; there the authorizer allows everything that
stays inside the file (see _MACRO_ACTIONS). Macros themselves cannot ATTACH.

For the active database, opensak.sql() and friends read through the
WritableDatabase connection (reader()), so that the macro's TEMP tables are
the same for opensak.sql_write() and inside opensak.transaction().

A progress handler aborts statements that run longer than
QUERY_TIMEOUT_S, since the Lua instruction limit does not count time spent
inside SQLite.

opensak.sql_write() uses a separate WritableDatabase on the active
database only. Its authorizer allows INSERT, UPDATE and DELETE on the cache
tables (WRITABLE_TABLES) and nothing else that changes data: no schema
changes, no ATTACH, no PRAGMA that writes, no transaction control
(macros use opensak.transaction(), WritableDatabase.begin()/end()). An
UPDATE of a PROTECTED_COLUMNS column is refused by the same authorizer,
which SQLite consults per column. An INSERT cannot be checked per column;
instead temporary triggers record which caches a statement touched, and
cache_write.refresh_derived() then recalculates their derived columns
(overwriting whatever the INSERT put there) before the statement's
transaction is committed. Inside opensak.transaction() the recorded caches
are collected instead (_PENDING), also those of the ORM writes sharing the
connection, and recalculated once before the commit or by resync().

The schema's foreign keys to caches.id have no ON DELETE CASCADE (SQLite
cannot add one without rebuilding the tables), so a temporary BEFORE DELETE
trigger on caches deletes the cache's rows first, as the GUI delete does
(#1035); it also records the deleted codes for the host. Deleting a log,
waypoint or trackable records its cache like an insert does.

The REPLACE conflict resolution (INSERT OR REPLACE, REPLACE INTO, UPDATE OR
REPLACE, ON CONFLICT REPLACE) is refused before the statement runs: it
deletes the conflicting row, and with it, through that trigger, the cache's
logs, waypoints and user note, where a macro mostly means "update or
insert". INSERT ... ON CONFLICT (...) DO UPDATE is the upsert that keeps
them. The authorizer reports no conflict resolution, hence the keyword
check (_uses_replace); the schema has no ON CONFLICT REPLACE constraint.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

# Per statement (and per fetched chunk of opensak.sql_each()).
QUERY_TIMEOUT_S = 60.0
# opensak.sql() returns everything at once; larger results need sql_each().
MAX_ROWS = 100_000
# Rows fetched at once by sql_each().
FETCH_SIZE = 500
# SQLite VM instructions between two timeout checks.
_PROGRESS_STEPS = 10_000

# PRAGMAs that only read, whatever their argument (a table or index name).
_READ_PRAGMAS = frozenset({
    "table_info", "table_xinfo", "index_list", "index_info", "index_xinfo",
    "foreign_key_list", "database_list",
})
_ALLOWED_ACTIONS = frozenset({
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
})
# Actions allowed on the temp database.
_CREATE_TEMP_ACTIONS = frozenset({
    sqlite3.SQLITE_CREATE_TEMP_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_VIEW,
    sqlite3.SQLITE_CREATE_TEMP_INDEX,
})
_TEMP_ACTIONS = _CREATE_TEMP_ACTIONS | {
    sqlite3.SQLITE_DROP_TEMP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_VIEW,
    sqlite3.SQLITE_DROP_TEMP_INDEX,
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_REINDEX,         # also part of CREATE INDEX
}
# Names of OpenSAK's own TEMP tables and triggers, which macros cannot touch.
_RESERVED_PREFIX = "opensak_macro_"

# The macro's own database (opensak.macro_db()), attached to the active
# database's connection under this schema name. Only OpenSAK attaches it
# (macros cannot ATTACH), so the name always means that file, in which a
# macro may do anything that cannot reach past it. Its (non-TEMP) triggers
# and views can only use its own tables — SQLite's rule.
MACRO_SCHEMA = "macro"
_MACRO_ACTIONS = frozenset({
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_CREATE_TRIGGER,
    sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_VIEW,
    sqlite3.SQLITE_DROP_TRIGGER,
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_REINDEX,
    sqlite3.SQLITE_ANALYZE,
})
# PRAGMAs that act on the named database only (`PRAGMA macro.user_version = 2`);
# most others, e.g. foreign_keys, would change the whole connection.
_MACRO_PRAGMAS = frozenset({"user_version", "application_id", "integrity_check", "quick_check"})


class SqlError(Exception):
    """A query failed or tried to write."""


def _reserved(name: Any) -> bool:
    return isinstance(name, str) and name.lower().startswith(_RESERVED_PREFIX)


def _main_names(conn: sqlite3.Connection) -> frozenset[str]:
    """Names of the main database's objects, which TEMP objects may not take."""
    return frozenset(n.lower() for (n,) in conn.execute("SELECT name FROM main.sqlite_master"))


def _read_authorizer(main_names: frozenset[str]):
    """The authorizer of opensak.sql(): reading, and the temp database."""

    def authorize(action, arg1, arg2, db_name, _trigger) -> int:
        if _reserved(arg1) or _reserved(arg2):
            return sqlite3.SQLITE_DENY
        if action in _ALLOWED_ACTIONS:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and str(arg1).lower() in _READ_PRAGMAS:
            return sqlite3.SQLITE_OK
        if db_name == MACRO_SCHEMA and (
            action in _MACRO_ACTIONS
            or (action == sqlite3.SQLITE_PRAGMA and str(arg1).lower() in _MACRO_PRAGMAS)
        ):
            return sqlite3.SQLITE_OK
        if db_name == "temp" and action in _TEMP_ACTIONS:
            if action in _CREATE_TEMP_ACTIONS and str(arg1).lower() in main_names:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    return authorize


def _params(params: Any) -> Any:
    """Bind parameters: a list (for ?) or a dict (for :name), scalars only."""
    if params is None:
        return ()
    values = params.values() if isinstance(params, dict) else params
    for value in values:
        if value is not None and not isinstance(value, (bool, int, float, str)):
            raise SqlError(f"SQL parameters must be numbers, strings, booleans or nil, got {value!r}")
    return params


def connect_read_only(db_path: Path) -> sqlite3.Connection:
    """A connection that cannot write: `mode=ro` plus `query_only`. Also used
    for the ORM reads of opensak.cache{ database = ... } and friends."""
    uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30, check_same_thread=False)
    try:
        conn.execute("PRAGMA query_only = ON")
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def _read_only_macro_db(conn: Optional[sqlite3.Connection], exc: sqlite3.Error) -> Optional[SqlError]:
    """A write refused because the macro database (opensak.macro_db()) is
    read-only — the file or its folder — explained as such; None for any
    other error."""
    if conn is None or "readonly database" not in str(exc):
        return None
    try:
        files = {row[1]: row[2] for row in conn.execute("PRAGMA database_list")}
    except sqlite3.Error:
        return None
    path = files.get(MACRO_SCHEMA)
    if not path or (os.access(path, os.W_OK) and os.access(Path(path).parent, os.W_OK)):
        return None
    return SqlError(
        f"the macro database {path} is read-only (the file or its folder "
        "cannot be written): it can be read, but not changed"
    )


class ReadOnlyDatabase:
    """A read-only connection to one SQLite file, opened on first use.

    With *connection* (WritableDatabase.reader()), it reads through that
    connection instead, so a macro sees its own uncommitted changes inside
    opensak.transaction(). That connection is shared with the writes, so
    the authorizer and the timeout are only in place while one of our
    statements runs, and close() leaves it open.
    """

    def __init__(
        self, db_path: Path, timeout_s: float = QUERY_TIMEOUT_S,
        connection: Optional[sqlite3.Connection] = None,
    ):
        self._db_path = Path(db_path)
        self._timeout_s = timeout_s
        self._conn: Optional[sqlite3.Connection] = connection
        self._borrowed = connection
        self._authorizer: Any = None
        self._deadline = 0.0

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            try:
                conn = connect_read_only(self._db_path)
                # mode=ro still protects the file; this only lets the macro
                # write TEMP tables, each statement on its own (no implicit
                # BEGIN, which the authorizer refuses).
                conn.execute("PRAGMA query_only = OFF")
                conn.isolation_level = None
                self._authorizer = _read_authorizer(_main_names(conn))
            except sqlite3.Error as exc:
                raise SqlError(f"cannot open the database read-only: {exc}") from None
            conn.set_authorizer(self._authorizer)
            conn.set_progress_handler(self._check_deadline, _PROGRESS_STEPS)
            self._conn = conn
        elif self._authorizer is None:
            try:
                self._authorizer = _read_authorizer(_main_names(self._conn))
            except sqlite3.Error as exc:
                raise SqlError(f"SQL error: {exc}") from None
        return self._conn

    def _check_deadline(self) -> int:
        return 1 if time.monotonic() > self._deadline else 0

    def _start_clock(self) -> None:
        self._deadline = time.monotonic() + self._timeout_s
        if self._borrowed is not None:
            self._borrowed.set_progress_handler(self._check_deadline, _PROGRESS_STEPS)

    def _stop_clock(self) -> None:
        """Remove our timeout from a borrowed connection, so it never
        interrupts the writes sharing it."""
        if self._borrowed is not None:
            self._borrowed.set_progress_handler(None, 0)

    def _error(self, exc: sqlite3.Error) -> SqlError:
        if isinstance(exc, sqlite3.OperationalError) and "interrupted" in str(exc):
            return SqlError(f"SQL query aborted: took longer than {self._timeout_s:g} s")
        read_only = _read_only_macro_db(self._conn, exc)
        if read_only is not None:
            return read_only
        if "not authorized" in str(exc) or "readonly" in str(exc):
            return SqlError(
                "SQL in opensak.sql is read-only, except for TEMP tables not "
                f"named like a table of the database: {exc}"
            )
        return SqlError(f"SQL error: {exc}")

    def _execute(self, query: str, params: Any) -> sqlite3.Cursor:
        params = _params(params)
        conn = self._connection()
        self._start_clock()
        if self._borrowed is not None:
            # Consulted while the statement is prepared, i.e. in execute().
            conn.set_authorizer(self._authorizer)
        try:
            return conn.execute(query, params)
        except sqlite3.Error as exc:
            self._stop_clock()
            raise self._error(exc) from None
        except (ValueError, OverflowError) as exc:  # e.g. integer too large
            self._stop_clock()
            raise SqlError(f"SQL error: {exc}") from None
        finally:
            if self._borrowed is not None:
                conn.set_authorizer(None)

    @staticmethod
    def _names(cursor: sqlite3.Cursor) -> list[str]:
        return [d[0] for d in cursor.description or ()]

    def query(self, query: str, params: Any = None, max_rows: int = MAX_ROWS) -> list[dict]:
        """All result rows as dicts keyed by column name (NULL = missing)."""
        cursor = self._execute(query, params)
        names = self._names(cursor)
        try:
            rows = cursor.fetchmany(max_rows + 1)
        except sqlite3.Error as exc:
            raise self._error(exc) from None
        finally:
            cursor.close()
            self._stop_clock()
        if len(rows) > max_rows:
            raise SqlError(
                f"query returned more than {max_rows} rows — use opensak.sql_each() "
                "or add a LIMIT"
            )
        return [_row(names, r) for r in rows]

    def iterate(self, query: str, params: Any = None) -> Iterator[dict]:
        """Like query(), but fetches FETCH_SIZE rows at a time."""
        cursor = self._execute(query, params)
        names = self._names(cursor)
        try:
            while True:
                self._start_clock()
                try:
                    rows = cursor.fetchmany(FETCH_SIZE)
                except sqlite3.Error as exc:
                    raise self._error(exc) from None
                finally:
                    self._stop_clock()
                if not rows:
                    return
                for r in rows:
                    yield _row(names, r)
        finally:
            cursor.close()

    def tables(self) -> list[str]:
        """Tables and views, the macro's TEMP ones included, and those of
        the macro database as "macro.<name>" when it is attached."""
        macro = any(r["name"] == MACRO_SCHEMA for r in self.query("PRAGMA database_list"))
        rows = self.query(
            "SELECT name FROM (SELECT name, type FROM main.sqlite_master "
            "UNION ALL SELECT name, type FROM temp.sqlite_master"
            + (f" UNION ALL SELECT '{MACRO_SCHEMA}.' || name, type "
               f"FROM {MACRO_SCHEMA}.sqlite_master" if macro else "")
            + ") WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
            f"AND name NOT LIKE '{MACRO_SCHEMA}.sqlite_%' "
            "AND lower(substr(name, 1, :n)) <> :prefix ORDER BY name",
            {"n": len(_RESERVED_PREFIX), "prefix": _RESERVED_PREFIX},
        )
        return [r["name"] for r in rows]

    def columns(self, table: str) -> list[dict[str, Any]]:
        if table not in self.tables():
            raise SqlError(f"no table named {table!r}")
        schema, dot, name = table.partition(".")
        if dot and schema == MACRO_SCHEMA:
            pragma = f"{MACRO_SCHEMA}.table_info"
        else:
            pragma, name = "table_info", table
        quoted = name.replace('"', '""')
        cursor = self._execute(f'PRAGMA {pragma}("{quoted}")', None)
        try:
            # required: NOT NULL without a default — an INSERT must give it
            return [
                {"name": r[1], "type": r[2], "required": bool(r[3] and r[4] is None and not r[5])}
                for r in cursor.fetchall()
            ]
        finally:
            cursor.close()
            self._stop_clock()

    def close(self) -> None:
        if self._conn is not None and self._borrowed is None:
            self._conn.close()
        self._conn = None


def _row(names: list[str], values: tuple) -> dict:
    return {n: v for n, v in zip(names, values) if v is not None}


# ── Writing: opensak.sql_write() ─────────────────────────────────────────────

# Tables a macro may INSERT into, UPDATE and DELETE from.
WRITABLE_TABLES = frozenset({
    "caches", "user_notes", "waypoints", "logs", "attributes", "trackables",
})
# The columns an INSERT has to give (NOT NULL without a default; schema 26,
# #1033, gave every other NOT NULL column a database default). For the docs —
# opensak.columns() reads the same from the database itself.
INSERT_COLUMNS: dict[str, tuple[str, ...]] = {
    "caches": ("gc_code", "name", "cache_type", "latitude", "longitude"),
    "user_notes": ("cache_id",),
    "waypoints": ("cache_id",),
    "logs": ("cache_id", "log_type"),
    "attributes": ("cache_id", "attribute_id"),
    "trackables": ("cache_id",),
}
# Columns an UPDATE may never set: keys and identities, and the columns
# OpenSAK maintains itself (counts, log dates, distance, import metadata).
PROTECTED_COLUMNS: dict[str, frozenset[str]] = {
    "caches": frozenset({
        "id", "gc_code", "gc_cache_id", "guid",
        "distance", "bearing",
        "log_count", "found_log_count", "waypoint_count", "trackable_count",
        "last_log_date", "last_found_date", "last_four_logs",
        "imported_at", "last_gpx_update", "source_file",
        "location_source", "location_basis", "location_updated", "location_dataset",
    }),
    "user_notes": frozenset({"id", "cache_id"}),
    "waypoints": frozenset({"id", "cache_id", "parent_gc_code"}),
    "logs": frozenset({"id", "cache_id", "log_id"}),
    "attributes": frozenset({"id", "cache_id"}),
    "trackables": frozenset({"id", "cache_id"}),
}

# Comments, string literals and quoted names, which may contain anything.
_SQL_OPAQUE = re.compile(
    r"--[^\n]*|/\*.*?(?:\*/|\Z)|'(?:[^']|'')*'?|\"(?:[^\"]|\"\")*\"?|`(?:[^`]|``)*`?|\[[^\]]*\]?",
    re.DOTALL,
)
# The REPLACE conflict resolution; not the replace() function.
_SQL_REPLACE = re.compile(r"\b(?:OR\s+REPLACE|REPLACE\s+INTO|CONFLICT\s+REPLACE)\b", re.IGNORECASE)


def _uses_replace(query: str) -> bool:
    """True if *query* resolves a conflict with REPLACE."""
    return bool(_SQL_REPLACE.search(_SQL_OPAQUE.sub(" ", query)))


_TOUCHED = f"{_RESERVED_PREFIX}touched"
# Inside opensak.transaction(): the caches whose derived columns are still to
# be recalculated, at the commit or by resync(). The triggers record into
# _TOUCHED, from where _stash() moves the rows here; as TEMP tables both
# follow the transaction's savepoints, so undone writes leave nothing behind.
_PENDING = f"{_RESERVED_PREFIX}pending"
# GC codes of the caches the current statement deleted.
_REMOVED = f"{_RESERVED_PREFIX}removed"
# The tables holding a cache's rows, deleted along with it.
_CHILD_TABLES = ("logs", "waypoints", "attributes", "trackables", "user_notes")

# (table, what a change there means for refresh_derived(), cache id column)
_TRACKED = (
    ("caches", "cache", "id"),
    ("user_notes", "cache", "cache_id"),
    ("attributes", "cache", "cache_id"),
    ("logs", "logs", "cache_id"),
    ("waypoints", "waypoints", "cache_id"),
    ("trackables", "trackables", "cache_id"),
)


def _write_authorizer(main_names: frozenset[str]):
    """The authorizer of opensak.sql_write(): that of opensak.sql(), plus
    INSERT, UPDATE and DELETE on the cache tables."""
    read = _read_authorizer(main_names)

    def authorize(action, arg1, arg2, db_name, trigger) -> int:
        # Our own temporary triggers (the user cannot create triggers).
        if trigger and str(trigger).startswith(_RESERVED_PREFIX):
            return sqlite3.SQLITE_OK
        if read(action, arg1, arg2, db_name, trigger) == sqlite3.SQLITE_OK:
            return sqlite3.SQLITE_OK
        if db_name not in (None, "main"):
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE) and arg1 in WRITABLE_TABLES:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_UPDATE and arg1 in WRITABLE_TABLES:
            return sqlite3.SQLITE_DENY if arg2 in PROTECTED_COLUMNS[arg1] else sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    return authorize


@dataclass
class WriteResult:
    """What one opensak.sql_write() statement changed."""

    rows: int                       # rows inserted, updated or deleted (not by triggers)
    codes: list[str]                # GC codes of the caches touched
    added: bool                     # a cache row was inserted
    removed: list[str]              # GC codes of the caches deleted


class WritableDatabase:
    """A read-write connection to the active database for opensak.sql_write(),
    opened on first use. Every statement runs in its own transaction, or in
    a SAVEPOINT of the one opensak.transaction() opened with begin().

    Inside such a transaction every write and read of the active database
    goes through this connection (session(), reader()): a second
    connection could neither write while this one holds the write lock nor
    see the uncommitted changes. opensak.sql() reads through it outside a
    transaction as well, so the macro's TEMP tables are the same everywhere.
    """

    def __init__(self, db_path: Path, timeout_s: float = QUERY_TIMEOUT_S):
        self._db_path = Path(db_path)
        self._timeout_s = timeout_s
        self._conn: Optional[sqlite3.Connection] = None
        self._authorizer: Any = None
        self._deadline = 0.0
        # opensak.transaction(): SQLAlchemy's view of this connection and
        # its transaction, and the nesting depth (> 1: SAVEPOINTs).
        self._sa_engine: Any = None
        self._sa_conn: Any = None
        self._sa_trans: Any = None
        self._depth = 0
        self._reader: Optional[ReadOnlyDatabase] = None
        self._macro_db: Optional[Path] = None

    def attach_macro_db(self, path: Path) -> None:
        """ATTACH the macro's own database file (created if missing) as
        MACRO_SCHEMA; nothing if it is attached already."""
        if self._macro_db is not None:
            if self._macro_db != path:
                raise SqlError(f"another database is attached as {MACRO_SCHEMA!r}")
            return
        if self._depth:
            raise SqlError("cannot attach a database inside opensak.transaction()")
        try:
            # Our own statement: no authorizer is set outside the macro's.
            self._connection().execute(f"ATTACH DATABASE ? AS {MACRO_SCHEMA}", (str(path),))
        except sqlite3.Error as exc:
            raise SqlError(f"cannot open the macro database {path}: {exc}") from None
        self._macro_db = path

    def detach_macro_db(self) -> None:
        """DETACH the file attached as MACRO_SCHEMA; nothing if none is."""
        if self._macro_db is None:
            return
        if self._depth:
            raise SqlError("cannot detach a database inside opensak.transaction()")
        try:
            self._connection().execute(f"DETACH DATABASE {MACRO_SCHEMA}")
        except sqlite3.Error as exc:
            raise SqlError(f"cannot close the macro database {self._macro_db}: {exc}") from None
        self._macro_db = None

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            try:
                conn = sqlite3.connect(
                    self._db_path, timeout=30, isolation_level=None,
                    check_same_thread=False,
                    # No statement cache: a statement prepared without the
                    # authorizer must never be reused for macro SQL.
                    cached_statements=0,
                )
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA recursive_triggers = ON")
                self._authorizer = _write_authorizer(_main_names(conn))
                self._create_triggers(conn)
            except sqlite3.Error as exc:
                raise SqlError(f"cannot open the database for writing: {exc}") from None
            self._conn = conn
        return self._conn

    @staticmethod
    def _create_triggers(conn: sqlite3.Connection) -> None:
        """TEMP triggers (this connection only) that record the caches a
        statement touches, and delete a cache's rows along with it."""
        for name in (_TOUCHED, _PENDING):
            conn.execute(
                f"CREATE TEMP TABLE {name} "
                "(cache_id INTEGER NOT NULL, kind TEXT NOT NULL, PRIMARY KEY (cache_id, kind))"
            )
        conn.execute(f"CREATE TEMP TABLE {_REMOVED} (gc_code TEXT PRIMARY KEY)")
        for table, kind, id_col in _TRACKED:
            for event in ("INSERT", "UPDATE"):
                record = f"INSERT OR IGNORE INTO {_TOUCHED} VALUES (NEW.{id_col}, '{kind}');"
                if table == "caches" and event == "INSERT":
                    record += f" INSERT OR IGNORE INTO {_TOUCHED} VALUES (NEW.id, 'insert');"
                conn.execute(
                    f"CREATE TEMP TRIGGER {_RESERVED_PREFIX}{table}_{event.lower()} "
                    f"AFTER {event} ON main.{table} BEGIN {record} END"
                )
            if table == "caches":
                # Unqualified names in a TEMP trigger find main's tables:
                # TEMP objects cannot take their names (_read_authorizer).
                cascade = " ".join(
                    f"DELETE FROM {child} WHERE cache_id = OLD.id;" for child in _CHILD_TABLES
                )
                conn.execute(
                    f"CREATE TEMP TRIGGER {_RESERVED_PREFIX}caches_delete "
                    "BEFORE DELETE ON main.caches BEGIN "
                    f"INSERT OR IGNORE INTO {_REMOVED} VALUES (OLD.gc_code); {cascade} END"
                )
            else:
                conn.execute(
                    f"CREATE TEMP TRIGGER {_RESERVED_PREFIX}{table}_delete "
                    f"AFTER DELETE ON main.{table} BEGIN "
                    f"INSERT OR IGNORE INTO {_TOUCHED} VALUES (OLD.{id_col}, '{kind}'); END"
                )
        conn.execute(
            f"CREATE TEMP TRIGGER {_RESERVED_PREFIX}caches_coords "
            "AFTER UPDATE OF latitude, longitude ON main.caches "
            f"BEGIN INSERT OR IGNORE INTO {_TOUCHED} VALUES (NEW.id, 'coords'); END"
        )

    def _check_deadline(self) -> int:
        return 1 if time.monotonic() > self._deadline else 0

    def _error(self, exc: sqlite3.Error) -> SqlError:
        if isinstance(exc, sqlite3.OperationalError) and "interrupted" in str(exc):
            return SqlError(f"SQL statement aborted: took longer than {self._timeout_s:g} s")
        read_only = _read_only_macro_db(self._conn, exc)
        if read_only is not None:
            return read_only
        if "not authorized" in str(exc) or "prohibited" in str(exc):
            return SqlError(
                "opensak.sql_write may only INSERT into, UPDATE and DELETE from the cache tables "
                f"({', '.join(sorted(WRITABLE_TABLES))}), and not the keys or the "
                f"columns OpenSAK maintains itself (besides TEMP tables): {exc}"
            )
        return SqlError(f"SQL error: {exc}")

    # -- opensak.transaction() ------------------------------------------------

    @property
    def in_transaction(self) -> bool:
        return self._depth > 0

    def begin(self) -> None:
        """Open a transaction, or a SAVEPOINT inside the open one. Takes the
        write lock at once (BEGIN IMMEDIATE), so a transaction never fails
        halfway because another connection wrote in between."""
        conn = self._connection()
        try:
            if self._depth:
                conn.execute(f"SAVEPOINT opensak_tx{self._depth}")
            else:
                if self._sa_conn is None:
                    from sqlalchemy import create_engine
                    from sqlalchemy.pool import StaticPool

                    self._sa_engine = create_engine(
                        "sqlite://", creator=lambda: conn, poolclass=StaticPool
                    )
                    self._sa_conn = self._sa_engine.connect()
                # pysqlite emits no BEGIN itself (isolation_level=None); the
                # SQLAlchemy transaction only makes commit()/rollback() reach
                # the connection, and keeps session() from ending it.
                self._sa_trans = self._sa_conn.begin()
                try:
                    self._sa_conn.exec_driver_sql("BEGIN IMMEDIATE")
                except BaseException:
                    self._sa_trans.rollback()
                    self._sa_trans = None
                    raise
        except sqlite3.Error as exc:
            raise self._error(exc) from None
        except Exception as exc:              # SQLAlchemy wraps sqlite3 errors
            raise SqlError(f"cannot start a transaction: {exc}") from None
        self._depth += 1

    def end(self, commit: bool) -> None:
        """Commit (or roll back) what begin() opened last."""
        if not self._depth:
            raise SqlError("no transaction is open")
        self._depth -= 1
        conn = self._connection()
        try:
            if self._depth:
                name = f"opensak_tx{self._depth}"
                if not commit:
                    conn.execute(f"ROLLBACK TO {name}")
                conn.execute(f"RELEASE {name}")
                return
            trans, self._sa_trans = self._sa_trans, None
            self._reader = None
            if commit:
                try:
                    self._refresh_pending(conn)
                except BaseException:
                    trans.rollback()
                    raise
                trans.commit()
            else:
                trans.rollback()
                self._clear_pending(conn)
        except sqlite3.Error as exc:
            raise self._error(exc) from None
        except Exception as exc:
            raise SqlError(f"cannot end the transaction: {exc}") from None

    # The derived columns inside a transaction: recalculated once, before
    # the commit, for every cache changed since the last resync().

    @staticmethod
    def _stash(conn: sqlite3.Connection) -> None:
        """Move what the triggers recorded to the pending caches."""
        conn.execute(
            f"INSERT OR IGNORE INTO temp.{_PENDING} SELECT cache_id, kind FROM temp.{_TOUCHED}"
        )
        conn.execute(f"DELETE FROM temp.{_TOUCHED}")

    @staticmethod
    def _clear_pending(conn: sqlite3.Connection) -> None:
        conn.execute(f"DELETE FROM temp.{_TOUCHED}")
        conn.execute(f"DELETE FROM temp.{_PENDING}")

    def _refresh_pending(self, conn: sqlite3.Connection) -> int:
        """Recalculate the pending caches; returns how many needed it."""
        from opensak.macro.cache_write import refresh_derived

        self._stash(conn)
        kinds_by_id: dict[int, set[str]] = {}
        for cache_id, kind in conn.execute(f"SELECT cache_id, kind FROM temp.{_PENDING}"):
            if kind != "cache":                # a plain field: nothing derived
                kinds_by_id.setdefault(cache_id, set()).add(kind)
        refresh_derived(lambda sql, p: conn.execute(sql, p).fetchall(), kinds_by_id)
        # The refresh's own UPDATEs fired the triggers too.
        self._clear_pending(conn)
        return len(kinds_by_id)

    def resync(self) -> int:
        """opensak.resync(): recalculate the derived columns of the caches
        the open transaction changed so far. Returns how many caches were
        recalculated; 0 outside a transaction, where every write is
        recalculated before its commit."""
        if not self._depth:
            return 0
        conn = self._connection()
        try:
            conn.execute("SAVEPOINT opensak_resync")
            try:
                count = self._refresh_pending(conn)
            except BaseException:
                conn.execute("ROLLBACK TO opensak_resync")
                conn.execute("RELEASE opensak_resync")
                raise
            conn.execute("RELEASE opensak_resync")
        except sqlite3.Error as exc:
            raise self._error(exc) from None
        return count

    @contextmanager
    def session(self) -> Iterator[Any]:
        """An ORM session inside the open transaction, for opensak.update()
        and friends. Atomic like a statement of execute(): it runs in its
        own SAVEPOINT, rolled back on error."""
        from sqlalchemy.orm import Session

        if not self._depth:
            raise SqlError("no transaction is open")
        session = Session(
            bind=self._sa_conn, autoflush=False, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
            session.commit()                  # RELEASE SAVEPOINT
        except BaseException:
            session.rollback()                # ROLLBACK TO SAVEPOINT
            raise
        finally:
            session.close()

    def reader(self) -> ReadOnlyDatabase:
        """opensak.sql() and friends on the active database: they see the
        open transaction's changes and the macro's TEMP tables."""
        if self._reader is None:
            self._reader = ReadOnlyDatabase(
                self._db_path, self._timeout_s, connection=self._connection()
            )
        return self._reader

    # -- opensak.sql_write() ---------------------------------------------------

    def execute(self, query: str, params: Any = None) -> WriteResult:
        """Run one INSERT, UPDATE or DELETE statement and commit it together with
        the recalculated derived columns. Rolls everything back on error.
        Inside a transaction, "commit" means releasing the statement's
        SAVEPOINT; the transaction decides what is kept, and the derived
        columns are recalculated at its commit or by resync()."""
        from opensak.macro.cache_write import normalize_inserted, refresh_derived

        if _uses_replace(query):
            raise SqlError(
                "opensak.sql_write refuses REPLACE (INSERT OR REPLACE, REPLACE INTO, "
                "UPDATE OR REPLACE, ON CONFLICT REPLACE): it deletes the existing cache "
                "with its logs, waypoints, attributes, trackables and user note. To "
                "update or insert, use INSERT ... ON CONFLICT (gc_code) DO UPDATE SET ..."
            )
        params = _params(params)
        conn = self._connection()
        nested = self.in_transaction
        try:
            conn.execute("SAVEPOINT opensak_stmt" if nested else "BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise self._error(exc) from None
        try:
            # ORM writes of the same transaction fire the triggers as well:
            # keep what they recorded, but apart from this statement's caches.
            if nested:
                self._stash(conn)
            else:
                conn.execute(f"DELETE FROM temp.{_TOUCHED}")
            # Also left over from ORM deletes of the transaction, which
            # report their caches themselves.
            conn.execute(f"DELETE FROM temp.{_REMOVED}")
            self._deadline = time.monotonic() + self._timeout_s
            conn.set_progress_handler(self._check_deadline, _PROGRESS_STEPS)
            conn.set_authorizer(self._authorizer)
            try:
                cursor = conn.execute(query, params)
                cursor.fetchall()          # runs a RETURNING clause to the end
                cursor.close()
            finally:
                conn.set_authorizer(None)
            rows = conn.execute("SELECT changes()").fetchone()[0]

            kinds_by_id: dict[int, set[str]] = {}
            for cache_id, kind in conn.execute(f"SELECT cache_id, kind FROM temp.{_TOUCHED}"):
                kinds_by_id.setdefault(cache_id, set()).add(kind)
            run = lambda sql, p: conn.execute(sql, p).fetchall()  # noqa: E731
            inserted = {i for i, kinds in kinds_by_id.items() if "insert" in kinds}
            if nested:
                normalize_inserted(run, inserted)
            else:
                refresh_derived(run, kinds_by_id)
            codes = [
                code for (code,) in conn.execute(
                    "SELECT gc_code FROM caches WHERE id IN "
                    f"(SELECT cache_id FROM temp.{_TOUCHED}) ORDER BY gc_code"
                )
            ]
            added = bool(inserted)
            removed = [
                code for (code,) in conn.execute(
                    f"SELECT gc_code FROM temp.{_REMOVED} ORDER BY gc_code"
                )
            ]
            conn.execute(f"DELETE FROM temp.{_REMOVED}")
            if nested:
                self._stash(conn)
            else:
                conn.execute(f"DELETE FROM temp.{_TOUCHED}")
            conn.execute("RELEASE opensak_stmt" if nested else "COMMIT")
        except BaseException as exc:
            try:
                # ROLLBACK TO also restores the TEMP tables as they were.
                if nested:
                    conn.execute("ROLLBACK TO opensak_stmt")
                    conn.execute("RELEASE opensak_stmt")
                else:
                    conn.execute("ROLLBACK")
                    conn.execute(f"DELETE FROM temp.{_TOUCHED}")
            except sqlite3.Error:
                pass
            if isinstance(exc, sqlite3.Error):
                raise self._error(exc) from None
            if isinstance(exc, (ValueError, OverflowError)):
                raise SqlError(f"SQL error: {exc}") from None
            raise
        finally:
            # The connection also serves the ORM writes and reads of a
            # transaction, which this statement's deadline must not cut off.
            conn.set_progress_handler(None, 0)
        return WriteResult(rows, codes, added, removed)

    def close(self) -> None:
        """Close the connection; a transaction still open is rolled back."""
        self._depth = 0
        self._reader = None
        if self._sa_trans is not None:
            try:
                self._sa_trans.rollback()
            except Exception:
                pass
            self._sa_trans = None
        if self._sa_conn is not None:
            self._sa_conn.close()
            self._sa_engine.dispose()
            self._sa_conn = self._sa_engine = None
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._macro_db = None

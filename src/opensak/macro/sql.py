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

A progress handler aborts statements that run longer than
QUERY_TIMEOUT_S, since the Lua instruction limit does not count time spent
inside SQLite.

opensak.sql_write() uses a separate WritableDatabase on the active
database only. Its authorizer allows INSERT and UPDATE on the cache tables
(WRITABLE_TABLES) and nothing else that changes data: no DELETE, no schema
changes, no ATTACH, no PRAGMA that writes, no transaction control
(macros use opensak.transaction(), WritableDatabase.begin()/end()). An
UPDATE of a PROTECTED_COLUMNS column is refused by the same authorizer,
which SQLite consults per column. An INSERT cannot be checked per column;
instead temporary triggers record which caches a statement touched, and
cache_write.refresh_derived() then recalculates their derived columns
(overwriting whatever the INSERT put there) before the statement's
transaction is committed. With recursive_triggers on, a BEFORE DELETE
trigger also catches the rows INSERT OR REPLACE would delete.
"""

from __future__ import annotations

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
    "foreign_key_list",
})
_ALLOWED_ACTIONS = frozenset({
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
})


class SqlError(Exception):
    """A query failed or tried to write."""


def _authorizer(action, arg1, arg2, _db_name, _trigger) -> int:
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and str(arg1).lower() in _READ_PRAGMAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


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
        self._deadline = 0.0

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            try:
                conn = connect_read_only(self._db_path)
            except sqlite3.Error as exc:
                raise SqlError(f"cannot open the database read-only: {exc}") from None
            conn.set_authorizer(_authorizer)
            conn.set_progress_handler(self._check_deadline, _PROGRESS_STEPS)
            self._conn = conn
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
        if "not authorized" in str(exc) or "readonly" in str(exc):
            return SqlError(f"SQL in macros is read-only (SELECT only): {exc}")
        return SqlError(f"SQL error: {exc}")

    def _execute(self, query: str, params: Any) -> sqlite3.Cursor:
        params = _params(params)
        conn = self._connection()
        self._start_clock()
        if self._borrowed is not None:
            # Consulted while the statement is prepared, i.e. in execute().
            conn.set_authorizer(_authorizer)
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
        rows = self.query(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [r["name"] for r in rows]

    def columns(self, table: str) -> list[dict[str, str]]:
        if table not in self.tables():
            raise SqlError(f"no table named {table!r}")
        quoted = table.replace('"', '""')
        cursor = self._execute(f'PRAGMA table_info("{quoted}")', None)
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

# Tables a macro may INSERT into and UPDATE.
WRITABLE_TABLES = frozenset({
    "caches", "user_notes", "waypoints", "logs", "attributes", "trackables",
})
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

_TRIGGER_PREFIX = "opensak_macro_"
_TOUCHED = f"{_TRIGGER_PREFIX}touched"

# (table, what a change there means for refresh_derived(), cache id column)
_TRACKED = (
    ("caches", "cache", "id"),
    ("user_notes", "cache", "cache_id"),
    ("attributes", "cache", "cache_id"),
    ("logs", "logs", "cache_id"),
    ("waypoints", "waypoints", "cache_id"),
    ("trackables", "trackables", "cache_id"),
)


def _write_authorizer(action, arg1, arg2, db_name, trigger) -> int:
    # Our own temporary triggers (the user cannot create triggers).
    if trigger and str(trigger).startswith(_TRIGGER_PREFIX):
        return sqlite3.SQLITE_OK
    if _authorizer(action, arg1, arg2, db_name, trigger) == sqlite3.SQLITE_OK:
        return sqlite3.SQLITE_OK
    if db_name not in (None, "main"):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_INSERT and arg1 in WRITABLE_TABLES:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_UPDATE and arg1 in WRITABLE_TABLES:
        return sqlite3.SQLITE_DENY if arg2 in PROTECTED_COLUMNS[arg1] else sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


@dataclass
class WriteResult:
    """What one opensak.sql_write() statement changed."""

    rows: int                       # rows inserted or updated (not by triggers)
    codes: list[str]                # GC codes of the caches touched
    added: bool                     # a cache row was inserted


class WritableDatabase:
    """A read-write connection to the active database for opensak.sql_write(),
    opened on first use. Every statement runs in its own transaction, or in
    a SAVEPOINT of the one opensak.transaction() opened with begin().

    Inside such a transaction every write and read of the active database
    goes through this connection (session(), reader()): a second
    connection could neither write while this one holds the write lock nor
    see the uncommitted changes.
    """

    def __init__(self, db_path: Path, timeout_s: float = QUERY_TIMEOUT_S):
        self._db_path = Path(db_path)
        self._timeout_s = timeout_s
        self._conn: Optional[sqlite3.Connection] = None
        self._deadline = 0.0
        # opensak.transaction(): SQLAlchemy's view of this connection and
        # its transaction, and the nesting depth (> 1: SAVEPOINTs).
        self._sa_engine: Any = None
        self._sa_conn: Any = None
        self._sa_trans: Any = None
        self._depth = 0
        self._reader: Optional[ReadOnlyDatabase] = None

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
                self._create_triggers(conn)
            except sqlite3.Error as exc:
                raise SqlError(f"cannot open the database for writing: {exc}") from None
            self._conn = conn
        return self._conn

    @staticmethod
    def _create_triggers(conn: sqlite3.Connection) -> None:
        """TEMP triggers (this connection only) that record the caches a
        statement touches, and refuse deletes."""
        conn.execute(
            f"CREATE TEMP TABLE {_TOUCHED} "
            "(cache_id INTEGER NOT NULL, kind TEXT NOT NULL, PRIMARY KEY (cache_id, kind))"
        )
        for table, kind, id_col in _TRACKED:
            for event in ("INSERT", "UPDATE"):
                record = f"INSERT OR IGNORE INTO {_TOUCHED} VALUES (NEW.{id_col}, '{kind}');"
                if table == "caches" and event == "INSERT":
                    record += f" INSERT OR IGNORE INTO {_TOUCHED} VALUES (NEW.id, 'insert');"
                conn.execute(
                    f"CREATE TEMP TRIGGER {_TRIGGER_PREFIX}{table}_{event.lower()} "
                    f"AFTER {event} ON main.{table} BEGIN {record} END"
                )
            conn.execute(
                f"CREATE TEMP TRIGGER {_TRIGGER_PREFIX}{table}_delete "
                f"BEFORE DELETE ON main.{table} "
                "BEGIN SELECT RAISE(ABORT, 'macros may not delete rows'); END"
            )
        conn.execute(
            f"CREATE TEMP TRIGGER {_TRIGGER_PREFIX}caches_coords "
            "AFTER UPDATE OF latitude, longitude ON main.caches "
            f"BEGIN INSERT OR IGNORE INTO {_TOUCHED} VALUES (NEW.id, 'coords'); END"
        )

    def _check_deadline(self) -> int:
        return 1 if time.monotonic() > self._deadline else 0

    def _error(self, exc: sqlite3.Error) -> SqlError:
        if isinstance(exc, sqlite3.OperationalError) and "interrupted" in str(exc):
            return SqlError(f"SQL statement aborted: took longer than {self._timeout_s:g} s")
        if "not authorized" in str(exc) or "prohibited" in str(exc):
            return SqlError(
                "opensak.sql_write may only INSERT into and UPDATE the cache tables "
                f"({', '.join(sorted(WRITABLE_TABLES))}), and not the keys or the "
                f"columns OpenSAK maintains itself: {exc}"
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
                trans.commit()
            else:
                trans.rollback()
        except sqlite3.Error as exc:
            raise self._error(exc) from None
        except Exception as exc:
            raise SqlError(f"cannot end the transaction: {exc}") from None

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
        """opensak.sql() and friends inside the open transaction."""
        if not self._depth:
            raise SqlError("no transaction is open")
        if self._reader is None:
            self._reader = ReadOnlyDatabase(
                self._db_path, self._timeout_s, connection=self._connection()
            )
        return self._reader

    # -- opensak.sql_write() ---------------------------------------------------

    def execute(self, query: str, params: Any = None) -> WriteResult:
        """Run one INSERT or UPDATE statement and commit it together with
        the recalculated derived columns. Rolls everything back on error.
        Inside a transaction, "commit" means releasing the statement's
        SAVEPOINT; the transaction decides what is kept."""
        from opensak.macro.cache_write import refresh_derived

        params = _params(params)
        conn = self._connection()
        nested = self.in_transaction
        try:
            conn.execute("SAVEPOINT opensak_stmt" if nested else "BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise self._error(exc) from None
        try:
            # ORM writes of the same transaction fire the triggers as well.
            conn.execute(f"DELETE FROM temp.{_TOUCHED}")
            self._deadline = time.monotonic() + self._timeout_s
            conn.set_progress_handler(self._check_deadline, _PROGRESS_STEPS)
            conn.set_authorizer(_write_authorizer)
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
            refresh_derived(lambda sql, p: conn.execute(sql, p).fetchall(), kinds_by_id)
            codes = [
                code for (code,) in conn.execute(
                    "SELECT gc_code FROM caches WHERE id IN "
                    f"(SELECT cache_id FROM temp.{_TOUCHED}) ORDER BY gc_code"
                )
            ]
            added = any("insert" in kinds for kinds in kinds_by_id.values())
            conn.execute(f"DELETE FROM temp.{_TOUCHED}")
            conn.execute("RELEASE opensak_stmt" if nested else "COMMIT")
        except BaseException as exc:
            try:
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
        return WriteResult(rows, codes, added)

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

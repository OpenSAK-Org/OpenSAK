"""
src/opensak/macro/sql.py — read-only SQL access for Lua macros.

opensak.sql() runs arbitrary SELECTs against the active database. Read-only
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
"""

from __future__ import annotations

import sqlite3
import time
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


class ReadOnlyDatabase:
    """A read-only connection to one SQLite file, opened on first use."""

    def __init__(self, db_path: Path, timeout_s: float = QUERY_TIMEOUT_S):
        self._db_path = Path(db_path)
        self._timeout_s = timeout_s
        self._conn: Optional[sqlite3.Connection] = None
        self._deadline = 0.0

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            uri = f"{self._db_path.resolve().as_uri()}?mode=ro"
            try:
                conn = sqlite3.connect(uri, uri=True, timeout=30, check_same_thread=False)
                conn.execute("PRAGMA query_only = ON")
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

    def _error(self, exc: sqlite3.Error) -> SqlError:
        if isinstance(exc, sqlite3.OperationalError) and "interrupted" in str(exc):
            return SqlError(f"SQL query aborted: took longer than {self._timeout_s:g} s")
        if "not authorized" in str(exc) or "readonly" in str(exc):
            return SqlError(f"SQL in macros is read-only (SELECT only): {exc}")
        return SqlError(f"SQL error: {exc}")

    def _execute(self, query: str, params: Any) -> sqlite3.Cursor:
        params = _params(params)
        self._start_clock()
        try:
            return self._connection().execute(query, params)
        except sqlite3.Error as exc:
            raise self._error(exc) from None
        except (ValueError, OverflowError) as exc:  # e.g. integer too large
            raise SqlError(f"SQL error: {exc}") from None

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
            return [{"name": r[1], "type": r[2]} for r in cursor.fetchall()]
        finally:
            cursor.close()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _row(names: list[str], values: tuple) -> dict:
    return {n: v for n, v in zip(names, values) if v is not None}

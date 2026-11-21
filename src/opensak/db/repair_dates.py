"""
src/opensak/db/repair_dates.py — the date checks of the database repair
(#1051, phase 2), used by db/repair.py.

SQLite has no date type: SQLAlchemy stores a DateTime as text in the form
``YYYY-MM-DD HH:MM:SS.ffffff`` and reads it back with
``datetime.fromisoformat()`` (SQLAlchemy 2.0 and 2.1). A value it can't read
(not text, or text fromisoformat rejects) raises while a cache, its logs or
its waypoints are loaded. A value in another format that does load (a bare
date, a "T", an offset) still compares wrongly as text, and OpenSAK's
filters and sorting compare dates as text. A DATETIME column has NUMERIC
affinity, so "20240305" is stored as the integer 20240305.

Every DATETIME column of the main database is checked, found through
``PRAGMA table_info``, so new columns are covered without changes here.
Values in the stored format are recognised in SQL; only the others are read
into Python. Each of those is either

* readable: an ISO date or timestamp with or without time, "T" or offset,
  one-digit month or day, "/" or "." between the parts, a day-first
  "DD.MM.YYYY", or an 8-digit YYYYMMDD number. Fix: convert it to the
  stored format. A value with an offset is converted to UTC, as the GPX
  importer does (importer._parse_datetime(), #617), so a repair and a
  re-import store the same value; or
* unreadable: anything else (impossible dates such as 2024-02-30 or
  0000-00-00, empty text, other numbers, blobs). Fix: clear it, or for the
  two NOT NULL bookkeeping columns (``caches.imported_at``,
  ``user_notes.updated_at``) set it to now, like their database default.

1970-01-01 00:00:00 (the Unix epoch) is GSAK's "no date": GSAK's own
Waypoints.cDate holds it, and the GSAK import copies it (792 waypoints in a
real 150k-cache database). It is its own fixable check: clear it, or for a
NOT NULL column set it to now.

Every affected row is listed with its GC code, the field (for a waypoint
also its prefix, e.g. "waypoints.wp_date S1") and the value as stored.

Report only: dates before May 2000 (geocaching began on 3 May 2000) or
more than a day in the future, except the placed date of an event or a GPS
Adventures Maze exhibit (DATED_CACHE_TYPES), which is the day it takes
place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

from opensak.utils.constants import EVENT_LOG_CACHE_TYPES

# Cache types whose placed date is the day they take place, so it may lie
# in the future: the events, and the GPS Adventures Maze exhibits (logged
# "Found it", so they aren't in EVENT_LOG_CACHE_TYPES).
DATED_CACHE_TYPES: frozenset[str] = EVENT_LOG_CACHE_TYPES | {"GPS Adventures Maze"}

# Earlier dates are reported: the first geocache was placed on 3 May 2000.
EARLIEST = "2000-05-01"
# The Unix epoch: GSAK stores it as "no date" (e.g. Waypoints.cDate), and
# the GSAK import copies it. Its own fixable check, not "too early".
EPOCH = "1970-01-01 00:00:00.000000"
# Later than now plus this is reported (time zones, clocks a bit ahead).
FUTURE_MARGIN = timedelta(days=1)

_DATE_TYPES = ("DATETIME", "DATE", "TIMESTAMP")

_ISO_LOOSE = re.compile(
    r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"
    r"(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2})(?:[.,](\d{1,6}))?)?)?"
)
_DAY_FIRST = re.compile(
    r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?: (\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?"
)


@dataclass(frozen=True, order=True)
class Detail:
    """One affected row of a repair check: its cache, the field and the
    value as stored, so the user can find and fix it by hand."""

    gc_code: str
    field: str
    value: str

    @property
    def text(self) -> str:
        return f"{self.gc_code} {self.field} {self.value}"


def shown(value: object) -> str:
    """A stored value as the dialog shows it, with NULL and empty text
    visible."""
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return "x'" + value.hex() + "'"
    text = str(value)
    return text if text.strip() else repr(text)


@dataclass(frozen=True)
class DateColumn:
    table: str
    column: str
    not_null: bool

    @property
    def label(self) -> str:
        return self.column if self.table == "caches" else f"{self.table}.{self.column}"


@dataclass(frozen=True)
class BadDate:
    """A stored value that isn't in the stored format. *fixed* is the value
    in the stored format, or None when it can't be read."""

    column: DateColumn
    rowid: int
    value: object
    fixed: Optional[str]
    gc_code: Optional[str]
    place: Optional[str] = None     # a waypoint's prefix

    @property
    def detail(self) -> Detail:
        return Detail(self.gc_code or "?", _field(self.column, self.place), shown(self.value))


def to_stored(dt: datetime) -> str:
    """*dt* in the format SQLAlchemy stores a DateTime in. Formatted by hand:
    strftime("%Y") doesn't pad years before 1000 on every platform."""
    return (f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d} "
            f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}.{dt.microsecond:06d}")


def normalize(value: object) -> Optional[str]:
    """*value* in the stored format, or None if it can't be read as a date."""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value) if 19000101 <= value <= 99991231 else ""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    dt = _parse(text)
    if dt is None:
        return None
    if dt.tzinfo is not None:
        try:
            dt = dt.astimezone(timezone.utc)
        except OverflowError:       # e.g. 0001-01-01T00:00:00+01:00
            return None
    return to_stored(dt.replace(tzinfo=None))


def _parse(text: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    # Unmatched optional groups are None. Year, month and day always match;
    # "or 0" only satisfies the type (year 0 raises ValueError below).
    parts: Optional[tuple[Optional[str], ...]] = None
    m = _ISO_LOOSE.fullmatch(text)
    if m:
        parts = m.groups()
    else:
        m = _DAY_FIRST.fullmatch(text)
        if m:
            day, month, year, *clock = m.groups()
            parts = (year, month, day, *clock, None)
    if parts is None:
        return None
    year, month, day, hour, minute, second, fraction = parts
    try:
        return datetime(int(year or 0), int(month or 0), int(day or 0), int(hour or 0),
                        int(minute or 0),
                        int(second or 0), int((fraction or "0").ljust(6, "0")))
    except ValueError:
        return None


def date_columns(conn) -> list[DateColumn]:
    """Every date column of the main database open on *conn*."""
    columns = []
    tables = [name for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name")]
    for table in tables:
        for _cid, name, type_, notnull, _default, _pk in conn.execute(
                f"PRAGMA table_info({_q(table)})"):
            if (type_ or "").upper() in _DATE_TYPES:
                columns.append(DateColumn(table, name, bool(notnull)))
    return columns


def _stored_format(col: str) -> str:
    """SQL: *col* is text in the stored format and a real date and time."""
    return (
        f"(typeof({col}) = 'text' AND length({col}) = 26 AND {col} GLOB "
        "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9]"
        ".[0-9][0-9][0-9][0-9][0-9][0-9]' "
        f"AND date({col}) = substr({col}, 1, 10) "
        f"AND time(substr({col}, 12, 8)) = substr({col}, 12, 8) "
        f"AND substr({col}, 1, 4) <> '0000')"
    )


def _code_sql(conn, column: DateColumn) -> str:
    """SQL for the GC code of the row being checked (alias t), or NULL."""
    if column.table == "caches":
        return "t.gc_code"
    if "cache_id" in _names(conn, column.table):
        return "(SELECT c.gc_code FROM caches c WHERE c.id = t.cache_id)"
    return "NULL"


def _place_sql(conn, column: DateColumn) -> str:
    """SQL for where in the cache the row is: a waypoint's prefix, so the
    user finds the waypoint (in GSAK too). NULL for other tables."""
    if column.table == "waypoints" and "prefix" in _names(conn, column.table):
        return "t.prefix"
    return "NULL"


def _field(column: DateColumn, place: Optional[str]) -> str:
    return f"{column.label} {place}" if place else column.label


def _names(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({_q(table)})")}


def _rows(conn, column: DateColumn, where: str, params: Optional[dict] = None,
          extra: str = "") -> list:
    """(rowid, value, GC code, place) of the rows of *column* matching
    *where* (on alias t, column as {col})."""
    col = f"t.{_q(column.column)}"
    return conn.execute(
        f"SELECT t.rowid, {col}, {_code_sql(conn, column)}, {_place_sql(conn, column)} "
        f"FROM {_q(column.table)} t WHERE {where.format(col=col)} {extra}", params or {}
    ).fetchall()


def bad_dates(conn, columns: Optional[list[DateColumn]] = None) -> Iterator[BadDate]:
    """Every value of the date *columns* (all by default) that isn't NULL
    and isn't in the stored format."""
    for column in columns if columns is not None else date_columns(conn):
        where = "{col} IS NOT NULL AND NOT " + _stored_format("{col}")
        for rowid, value, code, place in _rows(conn, column, where):
            yield BadDate(column, rowid, value, normalize(value), code, place)


def placeholder_dates(conn) -> Iterator[BadDate]:
    """Every value that is EPOCH in the stored format. (One in another
    format comes from bad_dates(), with *fixed* == EPOCH.)"""
    for column in date_columns(conn):
        for rowid, value, code, place in _rows(conn, column, "{col} = :epoch",
                                               {"epoch": EPOCH}):
            yield BadDate(column, rowid, value, EPOCH, code, place)


def out_of_range(conn, limit: int) -> tuple[int, list[Detail]]:
    """(number of, the first *limit* of) the dates in the stored format
    before EARLIEST or after now + FUTURE_MARGIN, ordered by GC code. The
    placed date of a DATED_CACHE_TYPES cache may be in the future."""
    future = to_stored(datetime.now(timezone.utc).replace(tzinfo=None) + FUTURE_MARGIN)
    dated = ", ".join("'" + t.replace("'", "''") + "'" for t in sorted(DATED_CACHE_TYPES))
    total = 0
    details: list[Detail] = []
    params = {"future": future, "earliest": EARLIEST, "epoch": EPOCH}
    for column in date_columns(conn):
        too_late = "{col} > :future"
        if column.table == "caches" and column.column == "hidden_date":
            too_late += f" AND t.cache_type NOT IN ({dated})"
        where = (_stored_format("{col}") + " AND {col} <> :epoch "
                 "AND ({col} < :earliest OR (" + too_late + "))")
        count = conn.execute(
            f"SELECT count(*) FROM {_q(column.table)} t "
            f"WHERE {where.format(col='t.' + _q(column.column))}", params).fetchone()[0]
        if not count:
            continue
        total += count
        for _rowid, value, code, place in _rows(conn, column, where, params,
                                                f"ORDER BY 3 LIMIT {limit}"):
            details.append(Detail(code or "?", _field(column, place), shown(value)))
    return total, sorted(details)[:limit]


def now_stored() -> str:
    """Now as naive UTC in the stored format, like the columns' defaults."""
    return to_stored(datetime.now(timezone.utc).replace(tzinfo=None))


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'

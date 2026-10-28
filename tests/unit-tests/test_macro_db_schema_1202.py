"""
tests/unit-tests/test_macro_db_schema_1202.py — the database tables macros
may rely on (#1202, decision D3 in #1196) and the real schema.

macro/db_schema.py lists the tables and columns macros may read, with the
format of their values; api.md documents them as part of the API. These
tests keep that list and the database in step: every listed column exists
in a new database and in databases of older releases after all migrations,
stored as its format says, and every column of these tables is either
listed or marked internal. A migration that drops or renames a listed
column fails here, unless it keeps the old name as a compatibility view.
Removing a column from the list fails test_api_matches_the_committed_baseline.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from opensak.db.corrected_coords import set_corrected_coords
from opensak.db.database import init_db
from opensak.macro.api_baseline import check, load_baseline, snapshot
from opensak.macro.api_docs import render_api_markdown
from opensak.macro.db_schema import FORMATS, INTERNAL, TABLES
from opensak.utils.constants import CACHE_TYPES, CONTAINER_SIZES, LOG_TYPES
from tests.data import seed_example_macro_caches

ROOT = Path(__file__).resolve().parents[2]
UPGRADE_FIXTURES = ROOT / "tests" / "fixtures" / "upgrade"
OLD_DATABASES = sorted(UPGRADE_FIXTURES.glob("*/databases/*.db"))


def _affinity(declared: str) -> str:
    """SQLite's type affinity of a declared column type (sqlite.org/datatype3.html)."""
    t = declared.upper()
    if "INT" in t:
        return "INTEGER"
    if any(s in t for s in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if not t or "BLOB" in t:
        return "BLOB"
    if any(s in t for s in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    """Column → declared type of a table or view; {} if there is none."""
    return {r[1]: r[2] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def schema_problems(conn: sqlite3.Connection) -> list[str]:
    """What the database lacks of the documented tables and columns: a
    missing table or view, a missing column, or one stored differently
    than its format says."""
    problems = []
    for t in TABLES:
        actual = _columns(conn, t.name)
        if not actual:
            problems.append(f"table {t.name} is missing")
            continue
        for c in t.columns:
            if c.name not in actual:
                problems.append(f"column {t.name}.{c.name} is missing")
                continue
            want = FORMATS[c.format].affinity
            # A view's column has no declared type when it is an expression
            if actual[c.name] and _affinity(actual[c.name]) != want:
                problems.append(f"column {t.name}.{c.name} is {actual[c.name]}, "
                                f"not {want} as format {c.format!r} needs")
    return problems


def _open(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(path))


# ── The list itself ──────────────────────────────────────────────────────────


def test_every_column_has_a_known_format_and_is_listed_once():
    for t in TABLES:
        names = [c.name for c in t.columns]
        assert len(names) == len(set(names)), t.name
        assert all(c.format in FORMATS for c in t.columns), t.name
        assert not set(names) & INTERNAL.get(t.name, frozenset()), t.name
    assert set(INTERNAL) <= {t.name for t in TABLES}


def test_api_md_documents_every_table_column_and_format():
    md = render_api_markdown()
    section = md[md.index("## Database tables for macros"):md.index("## Changing caches")]
    for t in TABLES:
        assert f"### Table `{t.name}`" in section
        for c in t.columns:
            assert f"| `{c.name}` | {FORMATS[c.format].label} |" in section
    for f in FORMATS.values():
        assert f"| {f.label} | {f.description} |" in section
    for columns in INTERNAL.values():
        assert all(f"`{c}`" in section for c in columns)
    # The sentences saying the schema "may change between versions" are gone
    assert "may change between versions" not in md
    assert md.count("(#database-tables-for-macros)") >= 1


# ── The real schema ──────────────────────────────────────────────────────────


def test_a_new_database_has_every_documented_column(tmp_path):
    init_db(db_path=tmp_path / "new.db")
    with _open(tmp_path / "new.db") as conn:
        assert schema_problems(conn) == []


def test_every_column_is_documented_or_internal(tmp_path):
    """A new column of these tables needs a decision: macros may rely on it
    (db_schema.TABLES, documented in api.md) or not (INTERNAL)."""
    init_db(db_path=tmp_path / "new.db")
    with _open(tmp_path / "new.db") as conn:
        for t in TABLES:
            listed = {c.name for c in t.columns} | INTERNAL.get(t.name, frozenset())
            assert set(_columns(conn, t.name)) == listed, t.name


@pytest.mark.parametrize("old", OLD_DATABASES,
                         ids=[f"{p.parents[1].name}/{p.name}" for p in OLD_DATABASES])
def test_a_database_of_an_older_release_has_every_documented_column_after_migrating(old, tmp_path):
    db = tmp_path / old.name
    shutil.copy2(old, db)
    init_db(db_path=db)
    with _open(db) as conn:
        assert schema_problems(conn) == []


def test_removing_a_documented_column_breaks_the_api_baseline():
    current = snapshot()
    del current["columns"]["trackables.ref"]
    assert check(load_baseline(ROOT), current) == [
        "database column trackables.ref was removed or renamed, but it was not deprecated first"]


def test_old_releases_are_covered():
    assert {p.parents[1].name for p in OLD_DATABASES} >= {"v1.20.0", "v1.21.0"}


def test_a_dropped_or_renamed_column_is_reported_and_a_compatibility_view_accepted(tmp_path):
    init_db(db_path=tmp_path / "new.db")
    with _open(tmp_path / "new.db") as conn:
        conn.execute("ALTER TABLE trackables RENAME COLUMN ref TO reference")
        conn.execute("ALTER TABLE attributes DROP COLUMN name")
        assert schema_problems(conn) == ["column attributes.name is missing",
                                         "column trackables.ref is missing"]

        # The table renamed, the old name kept as a view with the old columns
        conn.execute("ALTER TABLE trackables RENAME TO trackables_v2")
        conn.execute("CREATE VIEW trackables AS SELECT id, cache_id, tracking_code, name, "
                     "reference AS ref FROM trackables_v2")
        assert schema_problems(conn) == ["column attributes.name is missing"]


def test_a_column_stored_differently_is_reported(tmp_path):
    init_db(db_path=tmp_path / "new.db")
    with _open(tmp_path / "new.db") as conn:
        conn.execute("ALTER TABLE logs RENAME TO logs_old")
        conn.execute("CREATE TABLE logs (id INTEGER, cache_id INTEGER, log_id TEXT, "
                     "log_type TEXT, log_date TEXT, finder TEXT, finder_id TEXT, text TEXT, "
                     "text_encoded BOOLEAN, latitude REAL, longitude REAL, logged_by_owner TEXT)")
        assert schema_problems(conn) == [
            "column logs.log_date is TEXT, not NUMERIC as format 'datetime' needs",
            "column logs.logged_by_owner is TEXT, not NUMERIC as format 'boolean' needs",
        ]


# ── Value formats ────────────────────────────────────────────────────────────

_DATETIME = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{6}")
_RATINGS = {1 + i / 2 for i in range(9)}


def _valid(format: str, column: str, value) -> bool:
    """Whether a stored *value* (not NULL) has the documented *format*."""
    if format in ("key", "integer"):
        return type(value) is int
    if format == "boolean":
        return value in (0, 1) and type(value) is int
    if format == "text":
        return isinstance(value, str)
    if format == "datetime":
        return isinstance(value, str) and _DATETIME.fullmatch(value) is not None
    if format == "degrees":
        limit = 90 if "lat" in column else 180
        return type(value) is float and -limit <= value <= limit
    if format == "rating":
        return value in _RATINGS
    if format in ("km", "metres"):
        return isinstance(value, (int, float))
    if format == "bearing":
        return isinstance(value, (int, float)) and 0 <= value <= 360
    if format == "cache_type":
        return value in CACHE_TYPES
    if format == "container":
        return value in CONTAINER_SIZES
    if format == "log_type":
        return value in LOG_TYPES
    raise AssertionError(f"no check for format {format!r}")


def test_imported_values_have_the_documented_formats(tmp_path):
    """The example macros' test database, imported as a user would: each
    documented column holds values of its format, and each format occurs."""
    db = tmp_path / "examples.db"
    init_db(db_path=db)
    seed_example_macro_caches()
    set_corrected_coords("GCEX003", 46.95205, 7.44093)
    with _open(db) as conn:
        conn.execute("UPDATE caches SET distance = 1.5, bearing = 270, elevation = 540.5 "
                     "WHERE gc_code = 'GCEX001'")
        seen: set[str] = set()
        for t in TABLES:
            for c in t.columns:
                for (value,) in conn.execute(
                        f"SELECT {c.name} FROM {t.name} WHERE {c.name} IS NOT NULL"):
                    assert _valid(c.format, c.name, value), (t.name, c.name, value)
                    seen.add(c.format)
    assert seen == set(FORMATS)

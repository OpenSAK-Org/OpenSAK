"""
tests/unit-tests/test_migrations.py — Startup migration gate (PRAGMA user_version).

The startup migration block is gated behind PRAGMA user_version so a database
already at the current schema skips the ~10 idempotent PRAGMA table_info probes
that would otherwise run on every launch. The migrations themselves are
unchanged; only the gate (skip-when-current, stamp-after-run) is exercised here.

Tests assert:
  * init_db stamps user_version to SCHEMA_VERSION
  * a current database short-circuits — no table_info probes
  * a stale database (user_version=0) re-runs the probes and is re-stamped
  * the schema migrations still produced their indexes (gate opened at least once)
"""

import pytest
from sqlalchemy import event, text

from opensak.db.database import (
    init_db,
    get_engine,
    _make_engine,
    _run_migrations,
    SCHEMA_VERSION,
)

# The original pre-migration schema: only the columns/tables the migrations read
# or rebuild. Running the migrations against it exercises every add-column /
# table-rebuild / data-normalisation branch.
_OLD_SCHEMA = [
    "CREATE TABLE caches (id INTEGER PRIMARY KEY AUTOINCREMENT, gc_code TEXT, cache_type TEXT, "
    "container TEXT, difficulty REAL, terrain REAL, hidden_date DATETIME, found_date DATETIME, "
    "found BOOLEAN, archived BOOLEAN, available BOOLEAN, latitude REAL, longitude REAL)",
    "CREATE TABLE waypoints (id INTEGER PRIMARY KEY AUTOINCREMENT, cache_id INTEGER, prefix TEXT, "
    "wp_type TEXT, name TEXT, description TEXT, comment TEXT, latitude REAL, longitude REAL)",
    "CREATE TABLE user_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, cache_id INTEGER)",
    "CREATE TABLE logs (id INTEGER PRIMARY KEY AUTOINCREMENT, cache_id INTEGER, log_date DATETIME)",
]


def _capture_statements(engine):
    """Return (list, detach) capturing every SQL statement run on *engine*."""
    seen: list[str] = []

    def _listener(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", _listener)
    return seen, lambda: event.remove(engine, "before_cursor_execute", _listener)


def test_user_version_stamped_after_init(tmp_path):
    init_db(db_path=tmp_path / "v.db")
    with get_engine().connect() as c:
        assert c.execute(text("PRAGMA user_version")).scalar() == SCHEMA_VERSION


def test_migrations_skipped_when_current(tmp_path):
    """A second migration pass on an up-to-date DB must not probe table_info."""
    init_db(db_path=tmp_path / "skip.db")
    engine = get_engine()

    seen, detach = _capture_statements(engine)
    try:
        _run_migrations(engine)
    finally:
        detach()

    assert not any("table_info" in s for s in seen), \
        f"gate did not short-circuit: {[s for s in seen if 'table_info' in s]}"
    # The only thing it should have read is the version gate.
    assert any("user_version" in s for s in seen)


def test_migrations_rerun_when_version_stale(tmp_path):
    """Resetting user_version=0 must re-open the gate and re-stamp afterwards."""
    init_db(db_path=tmp_path / "stale.db")
    engine = get_engine()

    with engine.connect() as c:
        c.execute(text("PRAGMA user_version = 0"))
        c.commit()

    seen, detach = _capture_statements(engine)
    try:
        _run_migrations(engine)
    finally:
        detach()

    assert any("table_info" in s for s in seen), "gate stayed shut on a stale DB"
    with engine.connect() as c:
        assert c.execute(text("PRAGMA user_version")).scalar() == SCHEMA_VERSION


def test_indexes_present_after_init(tmp_path):
    """Sanity: the gated migration block still created the filter/sort indexes."""
    init_db(db_path=tmp_path / "idx.db")
    with get_engine().connect() as c:
        names = {
            row[0] for row in c.execute(text(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='caches'"
            ))
        }
    for expected in ("ix_caches_cache_type", "ix_caches_lat_lon", "ix_caches_found"):
        assert expected in names


def test_old_schema_runs_every_migration(tmp_path):
    """A v0 database with the original schema must apply all migrations."""
    engine = _make_engine(tmp_path / "old.db")
    with engine.connect() as c:
        for ddl in _OLD_SCHEMA:
            c.execute(text(ddl))
        # Rows that trigger the data-normalisation migrations (5 and 7).
        c.execute(text(
            "INSERT INTO caches (gc_code, cache_type, container) "
            "VALUES ('GC1', 'gps adventures exhibit', 'Nano')"
        ))
        c.execute(text("INSERT INTO logs (cache_id, log_date) VALUES (1, '2024-01-01')"))
        c.execute(text("PRAGMA user_version = 0"))
        c.commit()

    _run_migrations(engine)

    with engine.connect() as c:
        cache_cols = {r[1] for r in c.execute(text("PRAGMA table_info(caches)"))}
        note_cols = {r[1] for r in c.execute(text("PRAGMA table_info(user_notes)"))}
        idx_names = {r[0] for r in c.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='waypoints'"
        ))}
        row = c.execute(text("SELECT cache_type, container FROM caches WHERE gc_code='GC1'")).first()
        version = c.execute(text("PRAGMA user_version")).scalar()

    for col in ("county", "log_count", "parent_gc_code", "owner_name", "last_log_date",
                "dnf_date", "favorite_points", "distance", "bearing"):
        assert col in cache_cols
    assert "is_corrected" in note_cols
    # The waypoints rebuild (migration 2) creates the named unique index
    # (matching the model's constraint name) plus the cache_id index.
    assert "uq_waypoint_cache_prefix_name" in idx_names
    assert "ix_waypoints_cache_id" in idx_names
    assert row == ("GPS Adventures Maze", "Micro")  # migration 5 + 7 normalisation
    assert version == SCHEMA_VERSION

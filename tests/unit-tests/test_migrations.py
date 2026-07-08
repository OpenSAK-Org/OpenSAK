"""tests/unit-tests/test_migrations.py — startup migration gate (PRAGMA user_version).

A DB already at SCHEMA_VERSION short-circuits the table_info probes; a stale one (user_version=0) re-runs them and is re-stamped.
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

# Pre-migration schema (only what the migrations read/rebuild) so running them
# exercises every add-column / table-rebuild / normalisation branch.
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
    # Return (list, detach) capturing every SQL statement run on *engine*.
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
    # A second migration pass on an up-to-date DB must not probe table_info.
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
    # Resetting user_version=0 must re-open the gate and re-stamp afterwards.
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
    # Sanity: the gated migration block still created the filter/sort indexes.
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
    # A v0 database with the original schema must apply all migrations.
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
        wpt_cols = {r[1] for r in c.execute(text("PRAGMA table_info(waypoints)"))}
        log_cols = {r[1] for r in c.execute(text("PRAGMA table_info(logs)"))}
        idx_names = {r[0] for r in c.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='waypoints'"
        ))}
        row = c.execute(text("SELECT cache_type, container FROM caches WHERE gc_code='GC1'")).first()
        version = c.execute(text("PRAGMA user_version")).scalar()

    for col in ("county", "log_count", "parent_gc_code", "owner_name", "last_log_date",
                "dnf_date", "favorite_points", "distance", "bearing", "waypoint_count",
                "locked", "gc_note", "url", "elevation", "color", "guid", "watch",
                "gc_cache_id", "find_count"):
        assert col in cache_cols
    assert "is_corrected" in note_cols
    # The waypoints rebuild (migration 2, later replaced by migration 21)
    # creates the named unique index (matching the model's constraint name)
    # plus the cache_id index.
    assert "uq_waypoint_cache_wp_code" in idx_names
    assert "uq_waypoint_cache_prefix_name" not in idx_names
    assert "ix_waypoints_cache_id" in idx_names
    assert row == ("GPS Adventures Maze", "Micro")  # migration 5 + 7 normalisation

    for col in ("wp_code", "url", "wp_date", "created_by_user", "wp_flag"):
        assert col in wpt_cols
    for col in ("latitude", "longitude", "logged_by_owner"):
        assert col in log_cols

    assert version == SCHEMA_VERSION


def test_waypoint_unique_constraint_wp_code_behaviour(tmp_path):
    # Issue #536: the (cache_id, wp_code) constraint replacing
    # (cache_id, prefix, name) — exercised directly on a fresh, current-
    # schema database (via init_db, so this doesn't depend on the migration
    # rebuild path, which test_old_schema_runs_every_migration already
    # covers end-to-end).
    from opensak.db.database import get_session
    from opensak.db.models import Cache

    init_db(db_path=tmp_path / "wp_constraint.db")
    with get_session() as s:
        s.add(Cache(gc_code="GC1TEST", name="Test Cache", cache_type="Traditional Cache",
                     latitude=55.5, longitude=11.1))
        s.commit()
        cache_id = s.query(Cache).filter_by(gc_code="GC1TEST").one().id

    engine = get_engine()
    with engine.connect() as c:
        # Same prefix+name, distinct wp_code — must both be storable (the
        # #536 scenario itself).
        c.execute(text(
            "INSERT INTO waypoints (cache_id, prefix, name, wp_code) "
            "VALUES (:cid, 'RP', 'Right turn', 'RP1TEST')"
        ), {"cid": cache_id})
        c.execute(text(
            "INSERT INTO waypoints (cache_id, prefix, name, wp_code) "
            "VALUES (:cid, 'RP', 'Right turn', 'RP1TEST-2')"
        ), {"cid": cache_id})
        c.commit()

        # Multiple NULL wp_codes (the GPX-import case) — also fine.
        c.execute(text(
            "INSERT INTO waypoints (cache_id, prefix, name) VALUES (:cid, 'PK', 'Parking')"
        ), {"cid": cache_id})
        c.execute(text(
            "INSERT INTO waypoints (cache_id, prefix, name) VALUES (:cid, 'PK', 'Parking')"
        ), {"cid": cache_id})
        c.commit()

        count = c.execute(text(
            "SELECT COUNT(*) FROM waypoints WHERE cache_id=:cid"
        ), {"cid": cache_id}).scalar()
        assert count == 4

        # A genuine duplicate wp_code on the same cache is still rejected.
        with pytest.raises(Exception):
            c.execute(text(
                "INSERT INTO waypoints (cache_id, prefix, name, wp_code) "
                "VALUES (:cid, 'RP', 'Another name', 'RP1TEST')"
            ), {"cid": cache_id})
            c.commit()


def test_leftover_favorite_point_column_removed(tmp_path):
    # Simulate a database created during the narrow v1.14.0 window where
    # `favorite_point` (singular, Boolean NOT NULL) briefly existed (#488,
    # #530): a fresh, current-schema database that also happens to still
    # carry the leftover column.
    init_db(db_path=tmp_path / "legacy_fav.db")
    engine = get_engine()

    _insert_sql = (
        "INSERT INTO caches (gc_code, name, cache_type, latitude, longitude, "
        "available, archived, premium_only, short_desc_html, long_desc_html, "
        "found, dnf, first_to_find, user_flag, watch, log_count, "
        "trackable_count, waypoint_count, locked, imported_at) "
        "VALUES ('{gc}', 'Test Cache', 'Traditional Cache', 1.0, 2.0, "
        "1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, '2026-01-01')"
    )

    # SQLite's ALTER TABLE ADD COLUMN requires a non-null default when the
    # column is NOT NULL, so this fixture adds one (DEFAULT 0) — unlike the
    # real leftover column, which has no default at all (see the reported
    # PRAGMA table_info output on issue #530: dflt_value is blank). That
    # difference doesn't matter here: the migration only cares whether the
    # column exists, not whether it currently has a default.
    with engine.connect() as c:
        c.execute(text(
            "ALTER TABLE caches ADD COLUMN favorite_point BOOLEAN NOT NULL DEFAULT 0"
        ))
        c.execute(text(_insert_sql.format(gc="GC1")))
        c.execute(text("PRAGMA user_version = 0"))
        c.commit()

    _run_migrations(engine)

    with engine.connect() as c:
        cache_cols = {r[1] for r in c.execute(text("PRAGMA table_info(caches)"))}
        row = c.execute(text("SELECT gc_code FROM caches WHERE gc_code='GC1'")).first()
        version = c.execute(text("PRAGMA user_version")).scalar()
        # The insert that failed above must now succeed with the leftover
        # column gone.
        c.execute(text(_insert_sql.format(gc="GC2")))
        c.commit()

    assert "favorite_point" not in cache_cols
    assert "favorite_points" in cache_cols  # the real, unrelated column stays
    assert row == ("GC1",)  # existing data survived the column drop
    assert version == SCHEMA_VERSION

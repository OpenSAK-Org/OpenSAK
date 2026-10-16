"""tests/unit-tests/test_server_defaults_1033.py — database defaults for NOT NULL columns (#1033).

Every NOT NULL column with a Python default also has one in the database, so a
plain SQL INSERT only has to list the real data. Migration 26 adds the defaults
to existing databases by rebuilding the affected tables.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import MetaData, text

from opensak.db import database
from opensak.db.database import (
    SCHEMA_VERSION,
    _COLUMN_DEFAULTS_26,
    _add_column_defaults,
    _make_engine,
    _run_migrations,
    make_session,
)
from opensak.db.models import Attribute, Base, Cache, Log, UserNote

_TABLES = ("caches", "user_notes", "logs", "attributes", "waypoints", "trackables")

# What an INSERT still has to list (NOT NULL, no default, not the primary key).
_REQUIRED = {
    "caches": {"gc_code", "name", "cache_type", "latitude", "longitude"},
    "user_notes": {"cache_id"},
    "logs": {"cache_id", "log_type"},
    "attributes": {"cache_id", "attribute_id"},
    "waypoints": {"cache_id"},
    "trackables": {"cache_id"},
}


def _fresh_db(path):
    # A new database as the app creates it today.
    engine = _make_engine(path)
    Base.metadata.create_all(engine)
    _run_migrations(engine)
    return engine


def _v25_db(path):
    # A schema-25 database: create_all() without the #1033 defaults, then
    # migrations 1-25, as a database opened before this change.
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    for table, defaults in _COLUMN_DEFAULTS_26.items():
        for col in defaults:
            metadata.tables[table].c[col].server_default = None
    engine = _make_engine(path)
    metadata.create_all(engine)
    with patch.object(database, "_COLUMN_DEFAULTS_26", {}):
        _run_migrations(engine)
    with engine.connect() as c:
        c.execute(text(
            "INSERT INTO caches (id, gc_code, name, cache_type, latitude, longitude, "
            "available, archived, premium_only, short_desc_html, long_desc_html, found, dnf, "
            "first_to_find, user_flag, watch, locked, log_count, trackable_count, "
            "found_log_count, waypoint_count, imported_at) VALUES "
            "(1, 'GC1', 'One', 'Traditional Cache', 55.0, 12.0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, "
            "1, 0, 1, 1, '2024-01-01 00:00:00.000000'), "
            "(2, 'GC2', 'Two', 'Multi-cache', 56.0, 13.0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1, "
            "0, 1, 0, 0, '2024-02-01 00:00:00.000000')"
        ))
        c.execute(text(
            "INSERT INTO user_notes (cache_id, note, is_corrected, updated_at) "
            "VALUES (1, 'n', 0, '2024-01-01 00:00:00.000000')"
        ))
        c.execute(text(
            "INSERT INTO logs (cache_id, log_id, log_type, text_encoded, logged_by_owner) "
            "VALUES (1, 'L1', 'Found it', 0, 0)"
        ))
        c.execute(text(
            "INSERT INTO attributes (cache_id, attribute_id, is_on) VALUES (1, 6, 1), (2, 6, 0)"
        ))
        c.execute(text(
            "INSERT INTO waypoints (cache_id, prefix, wp_code, created_by_user, wp_flag) "
            "VALUES (1, 'PK', 'PK1', 0, 0)"
        ))
        c.execute(text("INSERT INTO trackables (cache_id, ref) VALUES (2, 'TB1')"))
        c.execute(text("PRAGMA user_version = 25"))
        c.commit()
    return engine


def _table_info(conn, table):
    return conn.execute(text(f"PRAGMA table_info({table})")).fetchall()


def _indexes(conn):
    return set(conn.execute(text(
        "SELECT tbl_name, name, sql FROM sqlite_master WHERE type='index'"
    )).fetchall())


def _counts(conn):
    return {t: conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in _TABLES}


# ── The rule ─────────────────────────────────────────────────────────────────

def test_every_not_null_default_has_a_server_default():
    # Keeps later columns (e.g. #1028) from bringing the problem back.
    offenders = [
        f"{table.name}.{col.name}"
        for table in Base.metadata.sorted_tables
        for col in table.columns
        if col.default is not None and not col.nullable and col.server_default is None
    ]
    assert offenders == [], f"NOT NULL columns with default= but no server_default: {offenders}"


@pytest.mark.parametrize("table", _TABLES)
def test_fresh_database_required_columns(tmp_path, table):
    engine = _fresh_db(tmp_path / "fresh.db")
    with engine.connect() as c:
        required = {r[1] for r in _table_info(c, table) if r[3] and r[4] is None and not r[5]}
    engine.dispose()
    assert required == _REQUIRED[table]


# ── Migration 26 ─────────────────────────────────────────────────────────────

def test_migrated_schema_matches_fresh_schema(tmp_path):
    fresh = _fresh_db(tmp_path / "fresh.db")
    old = _v25_db(tmp_path / "old.db")
    with old.connect() as o:
        assert {r[1] for r in _table_info(o, "caches") if r[3] and r[4] is None} > _REQUIRED["caches"]
    _run_migrations(old)
    with fresh.connect() as f, old.connect() as o:
        for table in _TABLES:
            assert _table_info(o, table) == _table_info(f, table), table
        assert _indexes(o) == _indexes(f)
        assert o.execute(text("PRAGMA user_version")).scalar() == SCHEMA_VERSION
    fresh.dispose()
    old.dispose()


def test_migration_keeps_data_and_constraints(tmp_path):
    engine = _v25_db(tmp_path / "old.db")
    with engine.connect() as c:
        counts = _counts(c)
        rows = c.execute(text("SELECT * FROM caches ORDER BY id")).fetchall()
    _run_migrations(engine)

    with engine.connect() as c:
        assert _counts(c) == counts
        assert c.execute(text("SELECT * FROM caches ORDER BY id")).fetchall() == rows
        assert c.execute(text("PRAGMA foreign_key_check")).fetchall() == []
        assert c.execute(text("PRAGMA integrity_check")).scalar() == "ok"
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
        names = {r[1] for r in _indexes(c)}
        assert {"ix_caches_gc_code", "ix_caches_distance_missing", "ix_logs_cache_id",
                "uq_waypoint_cache_wp_code"} <= names
        # Unique constraints still hold.
        for stmt in (
            "INSERT INTO caches (gc_code, name, cache_type, latitude, longitude) "
            "VALUES ('GC1', 'x', 'y', 0, 0)",
            "INSERT INTO attributes (cache_id, attribute_id) VALUES (1, 6)",
            "INSERT INTO logs (cache_id, log_type, log_id) VALUES (2, 'Note', 'L1')",
            "INSERT INTO user_notes (cache_id) VALUES (1)",
        ):
            with pytest.raises(Exception, match="UNIQUE"):
                c.execute(text(stmt))
            c.rollback()
        # Foreign keys are enforced again.
        with pytest.raises(Exception, match="FOREIGN KEY"):
            c.execute(text("INSERT INTO logs (cache_id, log_type) VALUES (999, 'Note')"))
        c.rollback()
    engine.dispose()


def test_migration_rerun_changes_nothing(tmp_path):
    engine = _v25_db(tmp_path / "old.db")
    _run_migrations(engine)
    with engine.connect() as c:
        schema = c.execute(text("SELECT type, name, sql FROM sqlite_master ORDER BY name")).fetchall()
        assert _add_column_defaults(c, _COLUMN_DEFAULTS_26) == []
        c.execute(text("PRAGMA user_version = 25"))
        c.commit()
    _run_migrations(engine)
    with engine.connect() as c:
        assert c.execute(
            text("SELECT type, name, sql FROM sqlite_master ORDER BY name")
        ).fetchall() == schema
    engine.dispose()


def test_failed_rebuild_rolls_back_everything(tmp_path):
    engine = _v25_db(tmp_path / "old.db")
    with engine.connect() as c:
        # A column type the migration does not recognise.
        c.execute(text("CREATE TABLE odd (id INTEGER PRIMARY KEY, x DOUBLE PRECISION NOT NULL)"))
        c.commit()
        schema = c.execute(text("SELECT type, name, sql FROM sqlite_master ORDER BY name")).fetchall()
        with pytest.raises(RuntimeError, match="odd.x"):
            _add_column_defaults(c, {"caches": {"available": "1"}, "odd": {"x": "0"}})
        assert c.execute(
            text("SELECT type, name, sql FROM sqlite_master ORDER BY name")
        ).fetchall() == schema
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
    engine.dispose()


def test_quoted_column_names(tmp_path):
    engine = _make_engine(tmp_path / "q.db")
    with engine.connect() as c:
        c.execute(text(
            'CREATE TABLE t (id INTEGER PRIMARY KEY, "flag" BOOLEAN NOT NULL, '
            'flag2 VARCHAR(16), n NUMERIC(10, 2) NOT NULL UNIQUE)'
        ))
        c.commit()
        assert _add_column_defaults(c, {"t": {"flag": "1", "flag2": "'x'", "n": "0"}}) == ["t"]
        info = {r[1]: (r[3], r[4]) for r in _table_info(c, "t")}
        assert info == {"id": (0, None), "flag": (1, "1"), "flag2": (0, "'x'"), "n": (1, "0")}
        c.execute(text("INSERT INTO t (id) VALUES (1)"))
        assert c.execute(text("SELECT flag, flag2, n FROM t")).first() == (1, "x", 0)
    engine.dispose()


# ── A raw INSERT only needs the real data ────────────────────────────────────

def test_minimal_insert_reads_back_through_orm(tmp_path):
    engine = _v25_db(tmp_path / "old.db")
    _run_migrations(engine)
    with engine.connect() as c:
        c.execute(text(
            "INSERT INTO caches (gc_code, name, cache_type, latitude, longitude) "
            "VALUES ('GC9', 'New', 'Traditional Cache', 55.5, 12.5)"
        ))
        cache_id = c.execute(text("SELECT id FROM caches WHERE gc_code='GC9'")).scalar()
        c.execute(text("INSERT INTO user_notes (cache_id) VALUES (:i)"), {"i": cache_id})
        c.execute(text("INSERT INTO logs (cache_id, log_type) VALUES (:i, 'Note')"), {"i": cache_id})
        c.execute(text("INSERT INTO attributes (cache_id, attribute_id) VALUES (:i, 1)"), {"i": cache_id})
        c.commit()

    with make_session(engine) as session:
        cache = session.query(Cache).filter_by(gc_code="GC9").one()
        assert cache.available is True
        for flag in ("archived", "premium_only", "short_desc_html", "long_desc_html", "found",
                     "dnf", "first_to_find", "user_flag", "watch", "locked"):
            assert getattr(cache, flag) is False, flag
        for count in ("log_count", "trackable_count", "found_log_count", "waypoint_count"):
            assert getattr(cache, count) == 0, count
        assert isinstance(cache.imported_at, datetime)
        assert abs((datetime.now(timezone.utc).replace(tzinfo=None) - cache.imported_at).total_seconds()) < 60

        note = session.query(UserNote).filter_by(cache_id=cache.id).one()
        assert note.is_corrected is False
        assert isinstance(note.updated_at, datetime)

        log = session.query(Log).filter_by(cache_id=cache.id).one()
        assert (log.text_encoded, log.logged_by_owner) == (False, False)

        assert session.query(Attribute).filter_by(cache_id=cache.id).one().is_on is True
    engine.dispose()

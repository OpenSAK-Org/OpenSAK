# tests/unit-tests/test_db.py — database model, session and CRUD tests.

from types import SimpleNamespace

import pytest
from pathlib import Path
from datetime import datetime

from opensak.db import database
from opensak.db.database import (
    init_db,
    get_session,
    get_engine,
    make_session,
    dispose_engine,
    db_health_check,
    reload_caches_full,
)
from opensak.db.models import Cache, Waypoint, Log, Attribute, Trackable, UserNote


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_cache() -> Cache:
    # Return a Cache instance (not yet added to any session).
    return Cache(
        gc_code="GC12345",
        name="Sample Traditional Cache",
        cache_type="Traditional Cache",
        container="Regular",
        latitude=55.6761,
        longitude=12.5683,
        difficulty=2.0,
        terrain=2.5,
        placed_by="TestOwner",
        country="Denmark",
        state="Zealand",
        short_description="A test cache.",
        encoded_hints="Under a rock.",
        available=True,
        archived=False,
    )


# ── Config tests ──────────────────────────────────────────────────────────────

def test_config_paths():
    # Config paths should all be pathlib.Path instances.
    from opensak.config import get_app_data_dir, get_db_path, get_gpx_import_dir, get_log_path
    assert isinstance(get_app_data_dir(), Path)
    assert isinstance(get_db_path(), Path)
    assert isinstance(get_gpx_import_dir(), Path)
    assert isinstance(get_log_path(), Path)


def test_config_directories_created():
    # App data and import dirs should be created automatically.
    from opensak.config import get_app_data_dir, get_gpx_import_dir
    assert get_app_data_dir().exists()
    assert get_gpx_import_dir().exists()


# ── DB initialisation ─────────────────────────────────────────────────────────

def test_init_db_creates_file(tmp_db):
    assert tmp_db.exists(), "Database file was not created"


def test_init_db_is_idempotent(tmp_db):
    # Calling init_db a second time should not raise or corrupt the DB.
    init_db(db_path=tmp_db)  # second call — should be safe


# ── Cache CRUD ────────────────────────────────────────────────────────────────

def test_create_cache(tmp_db, sample_cache):
    with get_session() as s:
        s.add(sample_cache)

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert cache.name == "Sample Traditional Cache"
        assert cache.latitude == pytest.approx(55.6761)
        assert cache.longitude == pytest.approx(12.5683)
        assert cache.difficulty == pytest.approx(2.0)
        assert cache.terrain == pytest.approx(2.5)
        assert cache.available is True
        assert cache.archived is False
        assert cache.found is False


def test_cache_repr(tmp_db):
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert "GC12345" in repr(cache)


def test_gc_code_is_unique(tmp_db):
    # Inserting a duplicate gc_code should raise an integrity error.
    from sqlalchemy.exc import IntegrityError
    duplicate = Cache(
        gc_code="GC12345",
        name="Duplicate",
        cache_type="Traditional Cache",
        latitude=0.0,
        longitude=0.0,
    )
    with pytest.raises(IntegrityError):
        with get_session() as s:
            s.add(duplicate)


# ── Waypoint ──────────────────────────────────────────────────────────────────

def test_add_waypoint(tmp_db):
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        wp = Waypoint(
            prefix="PK",
            wp_type="Parking Area",
            name="Parking spot",
            latitude=55.6762,
            longitude=12.5680,
        )
        cache.waypoints.append(wp)

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert len(cache.waypoints) == 1
        assert cache.waypoints[0].prefix == "PK"
        assert cache.waypoints[0].wp_type == "Parking Area"


# ── Log ───────────────────────────────────────────────────────────────────────

def test_add_log(tmp_db):
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        log = Log(
            log_type="Found it",
            log_date=datetime(2024, 6, 15, 10, 30),
            finder="Tester",
            text="TFTC!",
        )
        cache.logs.append(log)

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert len(cache.logs) == 1
        assert cache.logs[0].log_type == "Found it"
        assert cache.logs[0].finder == "Tester"


# ── Attribute ─────────────────────────────────────────────────────────────────

def test_add_attribute(tmp_db):
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        attr = Attribute(attribute_id=1, name="Dogs", is_on=True)
        cache.attributes.append(attr)

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert len(cache.attributes) == 1
        assert cache.attributes[0].name == "Dogs"
        assert cache.attributes[0].is_on is True


# ── UserNote ──────────────────────────────────────────────────────────────────

def test_add_user_note(tmp_db):
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        cache.user_note = UserNote(
            note="Bring a pen.",
            corrected_lat=55.6763,
            corrected_lon=12.5685,
        )

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert cache.user_note is not None
        assert cache.user_note.note == "Bring a pen."
        assert cache.user_note.corrected_lat == pytest.approx(55.6763)


# ── Cascade delete ────────────────────────────────────────────────────────────

def test_cascade_delete(tmp_db):
    # Deleting a cache should also delete all its child records.
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        cache_id = cache.id
        s.delete(cache)

    with get_session() as s:
        assert s.query(Cache).filter_by(gc_code="GC12345").first() is None
        assert s.query(Waypoint).filter_by(cache_id=cache_id).first() is None
        assert s.query(Log).filter_by(cache_id=cache_id).first() is None
        assert s.query(Attribute).filter_by(cache_id=cache_id).first() is None
        assert s.query(UserNote).filter_by(cache_id=cache_id).first() is None


# ── Health check ──────────────────────────────────────────────────────────────

def test_health_check(tmp_db):
    stats = db_health_check()
    assert "caches" in stats
    assert "logs" in stats
    assert "waypoints" in stats
    assert all(isinstance(v, int) for v in stats.values())


# ── Engine lifecycle ──────────────────────────────────────────────────────────

class TestEngineGuards:
    def test_get_engine_raises_when_uninitialised(self, monkeypatch):
        monkeypatch.setattr(database, "_engine", None)
        with pytest.raises(RuntimeError):
            get_engine()

    def test_get_session_raises_when_uninitialised(self, monkeypatch):
        monkeypatch.setattr(database, "_SessionLocal", None)
        with pytest.raises(RuntimeError):
            with get_session():
                pass

    def test_make_session_raises_when_uninitialised(self, monkeypatch):
        monkeypatch.setattr(database, "_SessionLocal", None)
        with pytest.raises(RuntimeError):
            make_session()

    def test_get_session_rolls_back_on_error(self, tmp_db):
        with pytest.raises(ValueError):
            with get_session() as s:
                s.add(Cache(gc_code="GCROLL", name="x", cache_type="Traditional Cache"))
                raise ValueError("boom")
        with get_session() as s:
            assert s.query(Cache).filter_by(gc_code="GCROLL").first() is None


class TestDisposeEngine:
    def test_noop_when_no_engine(self, monkeypatch):
        monkeypatch.setattr(database, "_engine", None)
        dispose_engine()  # must not raise

    def test_disposes_active_engine(self, tmp_path):
        init_db(db_path=tmp_path / "d.db")
        assert database._engine is not None
        dispose_engine()
        assert database._engine is None

    def test_skips_when_path_mismatch(self, tmp_path):
        init_db(db_path=tmp_path / "keep.db")
        dispose_engine(tmp_path / "other.db")
        assert database._engine is not None

    def test_disposes_when_path_matches(self, tmp_path):
        target = tmp_path / "match.db"
        init_db(db_path=target)
        dispose_engine(target)
        assert database._engine is None


class TestModelReprs:
    def test_waypoint_repr(self):
        assert "Waypoint" in repr(Waypoint(prefix="PK", cache_id=1))

    def test_log_repr(self):
        assert "Log" in repr(Log(log_type="Found it", finder="Tester"))

    def test_attribute_repr(self):
        assert "Attribute" in repr(Attribute(name="Kids", is_on=True))
        assert "Attribute" in repr(Attribute(name="Dogs", is_on=False))

    def test_trackable_repr(self):
        assert "Trackable" in repr(Trackable())

    def test_usernote_repr(self):
        assert "UserNote" in repr(UserNote(cache_id=1))


class TestInitDbDefaultPath:
    def test_uses_manager_active_path(self, tmp_path, monkeypatch):
        path = tmp_path / "managed.db"
        monkeypatch.setattr(
            "opensak.db.manager.get_db_manager",
            lambda: SimpleNamespace(active_path=path),
        )
        init_db()
        assert "managed.db" in str(get_engine().url)

    def test_falls_back_to_config_when_no_active_path(self, tmp_path, monkeypatch):
        path = tmp_path / "configured.db"
        monkeypatch.setattr(
            "opensak.db.manager.get_db_manager",
            lambda: SimpleNamespace(active_path=None),
        )
        monkeypatch.setattr("opensak.config.get_db_path", lambda: path)
        init_db()
        assert "configured.db" in str(get_engine().url)

    def test_falls_back_to_config_when_manager_unavailable(self, tmp_path, monkeypatch):
        path = tmp_path / "fallback.db"
        def boom():
            raise RuntimeError("no manager")
        monkeypatch.setattr("opensak.db.manager.get_db_manager", boom)
        monkeypatch.setattr("opensak.config.get_db_path", lambda: path)
        init_db()
        assert "fallback.db" in str(get_engine().url)


# ── reload_caches_full ──────────────────────────────────────────────────────────

class TestReloadCachesFull:
    def test_passes_through_non_cache_objects(self, tmp_db):
        fakes = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
        assert reload_caches_full(fakes) == fakes  # no DB query, returned as-is

    def test_empty_input(self, tmp_db):
        assert reload_caches_full([]) == []

    def test_reloads_deferred_blob_and_noloaded_logs(self, tmp_path):
        from sqlalchemy.orm import defer, noload
        from sqlalchemy.orm.exc import DetachedInstanceError

        init_db(db_path=tmp_path / "reload.db")
        with get_session() as s:
            cache = Cache(
                gc_code="GCRELOAD", name="Reload me", cache_type="Traditional Cache",
                latitude=55.0, longitude=12.0, encoded_hints="Behind the sign.",
            )
            cache.logs.append(Log(log_type="Found it", finder="T", text="Nice one"))
            s.add(cache)

        # Load it the way the table does: deferred blob + noload'ed logs, detached.
        with get_session() as s:
            partial = (
                s.query(Cache)
                .options(defer(Cache.encoded_hints), noload(Cache.logs))
                .filter_by(gc_code="GCRELOAD")
                .one()
            )
        with pytest.raises(DetachedInstanceError):
            _ = partial.encoded_hints

        [full] = reload_caches_full([partial])
        assert full.encoded_hints == "Behind the sign."
        assert [lg.text for lg in full.logs] == ["Nice one"]

    def test_missing_row_falls_back_to_original(self, tmp_path):
        init_db(db_path=tmp_path / "missing.db")
        ghost = Cache(gc_code="GCGONE", name="Not saved")
        ghost.id = 424242  # an id with no matching row
        assert reload_caches_full([ghost]) == [ghost]


# ── Location provenance columns (issue #60 phase 3) ──────────────────────────

class TestLocationProvenanceColumns:
    def test_columns_default_to_null(self, tmp_path):
        # Fresh DB: all four provenance columns must be present and default NULL.
        import sqlite3 as _sql
        init_db(db_path=tmp_path / "prov.db")
        with get_session() as s:
            cache = Cache(
                gc_code="GCPROV1", name="Provenance test",
                cache_type="Traditional Cache", latitude=55.0, longitude=12.0,
            )
            s.add(cache)

        with get_session() as s:
            c = s.query(Cache).filter_by(gc_code="GCPROV1").one()
            assert c.location_source is None
            assert c.location_basis is None
            assert c.location_updated is None
            assert c.location_dataset is None

    def test_columns_are_writable(self, tmp_path):
        from datetime import datetime
        init_db(db_path=tmp_path / "prov2.db")
        with get_session() as s:
            cache = Cache(
                gc_code="GCPROV2", name="Written", cache_type="Traditional Cache",
                latitude=55.0, longitude=12.0,
                location_source="computed", location_basis="posted",
                location_updated=datetime(2025, 6, 1),
                location_dataset="2025-06-01",
            )
            s.add(cache)

        with get_session() as s:
            c = s.query(Cache).filter_by(gc_code="GCPROV2").one()
            assert c.location_source == "computed"
            assert c.location_basis == "posted"
            assert c.location_dataset == "2025-06-01"

    def test_migration_adds_columns_to_old_schema(self, tmp_path):
        # Create a v12 DB, strip the 4 provenance columns, rewind to v11,
        # then re-run init_db() — migration 12 must re-add all four.
        import sqlite3 as _sql
        from opensak.db.database import _migrated_paths

        db_file = tmp_path / "old_schema.db"
        _migrated_paths.discard(db_file)
        init_db(db_path=db_file)

        provenance = ("location_source", "location_basis", "location_updated", "location_dataset")
        with _sql.connect(db_file) as con:
            for col in provenance:
                con.execute(f"ALTER TABLE caches DROP COLUMN {col}")
            con.execute("PRAGMA user_version = 11")

        _migrated_paths.discard(db_file)
        init_db(db_path=db_file)

        cols = {
            row[1]
            for row in _sql.connect(db_file).execute("PRAGMA table_info(caches)").fetchall()
        }
        for col in provenance:
            assert col in cols, f"migration 12 did not restore column: {col}"

    def test_migration_13_adds_parent_gc_code_to_waypoints(self, tmp_path):
        # Create a v13 DB, drop parent_gc_code, rewind to v12, re-run init_db —
        # migration 13 must add the column and back-fill it from caches.
        import sqlite3 as _sql
        from opensak.db.database import _migrated_paths
        from opensak.db.models import Cache, Waypoint

        db_file = tmp_path / "m13.db"
        _migrated_paths.discard(db_file)
        init_db(db_path=db_file)

        from opensak.db.database import get_session
        with get_session() as s:
            cache = Cache(gc_code="GCTEST1", name="Test", cache_type="Traditional Cache",
                          latitude=55.0, longitude=12.0)
            s.add(cache)
            s.flush()
            s.add(Waypoint(cache_id=cache.id, parent_gc_code="GCTEST1",
                           prefix="PK", wp_type="Parking Area"))
            s.commit()

        with _sql.connect(db_file) as con:
            con.execute("ALTER TABLE waypoints DROP COLUMN parent_gc_code")
            con.execute("PRAGMA user_version = 12")

        _migrated_paths.discard(db_file)
        init_db(db_path=db_file)

        with _sql.connect(db_file) as con:
            wpt_cols = {row[1] for row in con.execute("PRAGMA table_info(waypoints)").fetchall()}
            assert "parent_gc_code" in wpt_cols, "migration 13 did not add waypoints.parent_gc_code"
            gc = con.execute("SELECT parent_gc_code FROM waypoints LIMIT 1").fetchone()[0]
            assert gc == "GCTEST1", f"back-fill failed: got {gc!r}"

    def test_migration_14_adds_waypoint_count_to_caches(self, tmp_path):
        # Create a v14 DB, drop waypoint_count, rewind to v13, re-run init_db —
        # migration 14 must add the column and back-fill it from waypoints.
        import sqlite3 as _sql
        from opensak.db.database import _migrated_paths
        from opensak.db.models import Cache, Waypoint

        db_file = tmp_path / "m14.db"
        _migrated_paths.discard(db_file)
        init_db(db_path=db_file)

        from opensak.db.database import get_session
        with get_session() as s:
            cache = Cache(gc_code="GCWPT01", name="Test", cache_type="Traditional Cache",
                          latitude=55.0, longitude=12.0)
            s.add(cache)
            s.flush()
            s.add(Waypoint(cache_id=cache.id, parent_gc_code="GCWPT01",
                           prefix="PK", wp_type="Parking Area"))
            s.commit()

        with _sql.connect(db_file) as con:
            con.execute("ALTER TABLE caches DROP COLUMN waypoint_count")
            con.execute("PRAGMA user_version = 13")

        _migrated_paths.discard(db_file)
        init_db(db_path=db_file)

        with _sql.connect(db_file) as con:
            cache_cols = {row[1] for row in con.execute("PRAGMA table_info(caches)").fetchall()}
            assert "waypoint_count" in cache_cols, "migration 14 did not add caches.waypoint_count"
            cnt = con.execute("SELECT waypoint_count FROM caches WHERE gc_code='GCWPT01'").fetchone()[0]
            assert cnt == 1, f"back-fill failed: got {cnt!r}"


class TestTrackablesTable:
    # Issue #491: the Trackable model (travel bugs / geocoins) has shipped
    # since v1.14.0 with no migration to create its table. Any existing
    # database crashes with "no such table: trackables" the moment
    # anything queries Cache.trackables (e.g. the already-shipped "Has
    # trackables" filter). Migration 16 creates the table on such databases.

    def test_fresh_db_has_trackables_table(self, tmp_path):
        import sqlite3 as _sql
        init_db(db_path=tmp_path / "fresh_trackables.db")
        with _sql.connect(tmp_path / "fresh_trackables.db") as con:
            tables = {
                row[0] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "trackables" in tables

    def test_trackables_are_queryable_and_writable(self, tmp_path):
        init_db(db_path=tmp_path / "trackables_rw.db")
        with get_session() as s:
            cache = Cache(
                gc_code="GCTB01", name="Trackable test",
                cache_type="Traditional Cache", latitude=55.0, longitude=12.0,
            )
            s.add(cache)
            s.flush()
            s.add(Trackable(cache_id=cache.id, ref="TB1234", name="Test Bug"))
            s.commit()

        with get_session() as s:
            c = s.query(Cache).filter_by(gc_code="GCTB01").one()
            assert len(c.trackables) == 1
            assert c.trackables[0].ref == "TB1234"
            assert c.trackables[0].name == "Test Bug"

    def test_migration_creates_table_on_old_schema(self, tmp_path):
        # Create a v15 DB (pre-trackables), drop the table entirely, rewind
        # to v15, then re-run init_db() — migration 16 must recreate it.
        import sqlite3 as _sql
        from opensak.db.database import _migrated_paths

        db_file = tmp_path / "old_schema_trackables.db"
        _migrated_paths.discard(db_file)
        init_db(db_path=db_file)

        with _sql.connect(db_file) as con:
            con.execute("DROP TABLE trackables")
            con.execute("PRAGMA user_version = 15")

        _migrated_paths.discard(db_file)
        init_db(db_path=db_file)

        with _sql.connect(db_file) as con:
            tables = {
                row[0] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "trackables" in tables, "migration 16 did not recreate trackables"

        # And it must actually be usable afterward, not just present.
        with get_session() as s:
            cache = Cache(
                gc_code="GCTB02", name="Post-migration test",
                cache_type="Traditional Cache", latitude=55.0, longitude=12.0,
            )
            s.add(cache)
            s.flush()
            s.add(Trackable(cache_id=cache.id, ref="TB5678", name="Post Bug"))
            s.commit()

        with get_session() as s:
            c = s.query(Cache).filter_by(gc_code="GCTB02").one()
            assert len(c.trackables) == 1
            assert c.trackables[0].ref == "TB5678"

    def test_migration_is_idempotent_when_table_already_exists(self, tmp_path):
        # Running init_db() again on an already-migrated DB must not error
        # (e.g. from a stray CREATE TABLE without IF NOT EXISTS semantics).
        from opensak.db.database import _migrated_paths

        db_file = tmp_path / "idempotent_trackables.db"
        init_db(db_path=db_file)
        _migrated_paths.discard(db_file)
        init_db(db_path=db_file)  # must not raise


class TestTrackableCountColumn:
    # Issue #489/#491: trackable_count caches the trackable count on the
    # caches row itself (mirroring log_count), so the new "Trackables"
    # table column can display it without loading the trackables
    # relationship for every row. Migration 17 adds and backfills it.

    def test_fresh_db_has_trackable_count_column(self, tmp_path):
        import sqlite3 as _sql
        init_db(db_path=tmp_path / "fresh_tc.db")
        with _sql.connect(tmp_path / "fresh_tc.db") as con:
            cols = [row[1] for row in con.execute("PRAGMA table_info(caches)").fetchall()]
            assert "trackable_count" in cols

    def test_migration_backfills_count_from_existing_trackables(self, tmp_path):
        # Simulate a v16 DB (has the trackables table, but not yet the
        # trackable_count column) with existing trackable rows, then
        # re-run init_db() — migration 17 must backfill the count.
        import sqlite3 as _sql
        from opensak.db.database import _migrated_paths

        db_file = tmp_path / "old_schema_tc.db"
        _migrated_paths.discard(db_file)
        init_db(db_path=db_file)

        with get_session() as s:
            cache = Cache(
                gc_code="GCTC01", name="Backfill test",
                cache_type="Traditional Cache", latitude=55.0, longitude=12.0,
            )
            s.add(cache)
            s.flush()
            s.add(Trackable(cache_id=cache.id, ref="TB0001", name="Bug One"))
            s.add(Trackable(cache_id=cache.id, ref="TB0002", name="Bug Two"))
            s.commit()

        with _sql.connect(db_file) as con:
            con.execute("ALTER TABLE caches DROP COLUMN trackable_count")
            con.execute("PRAGMA user_version = 16")

        _migrated_paths.discard(db_file)
        init_db(db_path=db_file)

        with get_session() as s:
            c = s.query(Cache).filter_by(gc_code="GCTC01").one()
            assert c.trackable_count == 2

    def test_migration_is_idempotent_when_column_already_exists(self, tmp_path):
        from opensak.db.database import _migrated_paths

        db_file = tmp_path / "idempotent_tc.db"
        init_db(db_path=db_file)
        _migrated_paths.discard(db_file)
        init_db(db_path=db_file)  # must not raise

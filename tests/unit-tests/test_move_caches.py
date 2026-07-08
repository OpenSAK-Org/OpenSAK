# tests/unit-tests/test_move_caches.py — tests for the move/copy caches logic.

import pytest
from pathlib import Path

from opensak.db.database import init_db, get_session, make_session
from opensak.db.models import Cache, Log, Attribute, Trackable, Waypoint, UserNote
from opensak.gui.dialogs.move_caches_dialog import (
    _snapshot_cache,
    _insert_snapshot,
    _MoveWorker,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def source_db(tmp_path):
    db_path = tmp_path / "source.db"
    init_db(db_path=db_path)
    return db_path


@pytest.fixture
def target_db(tmp_path):
    db_path = tmp_path / "target.db"
    init_db(db_path=db_path)
    return db_path


def _make_cache_with_children(session, gc_code="GC100"):
    """Insert a cache with child records and return it fully loaded."""
    from sqlalchemy.orm import joinedload
    cache = Cache(
        gc_code=gc_code,
        name="Test Cache",
        cache_type="Traditional Cache",
        latitude=55.0,
        longitude=12.0,
    )
    session.add(cache)
    session.flush()
    session.add(Log(cache_id=cache.id, log_type="Found it", finder="Tester"))
    session.add(Attribute(cache_id=cache.id, attribute_id=1, name="Dogs", is_on=True))
    session.add(Trackable(cache_id=cache.id, name="TB Test", ref="TB001"))
    session.add(Waypoint(cache_id=cache.id, prefix="PK", wp_type="Parking Area",
                         name="Parking", latitude=55.01, longitude=12.01))
    session.add(UserNote(cache_id=cache.id, note="My note", is_corrected=False))
    session.flush()
    # Re-query with eager loads
    return (
        session.query(Cache)
        .options(
            joinedload(Cache.logs),
            joinedload(Cache.attributes),
            joinedload(Cache.trackables),
            joinedload(Cache.waypoints),
            joinedload(Cache.user_note),
        )
        .filter_by(gc_code=gc_code)
        .first()
    )


# ── _snapshot_cache tests ─────────────────────────────────────────────────────

class TestSnapshotCache:
    def test_snapshot_captures_scalar_fields(self, source_db):
        init_db(db_path=source_db)
        with get_session() as session:
            cache = _make_cache_with_children(session, "GC200")
            snap = _snapshot_cache(cache)

        assert snap["gc_code"] == "GC200"
        assert snap["name"] == "Test Cache"
        assert snap["latitude"] == 55.0
        assert "id" not in snap  # id should be excluded

    def test_snapshot_captures_logs(self, source_db):
        init_db(db_path=source_db)
        with get_session() as session:
            cache = _make_cache_with_children(session)
            snap = _snapshot_cache(cache)

        assert len(snap["_logs"]) == 1
        assert snap["_logs"][0]["log_type"] == "Found it"
        assert "cache_id" not in snap["_logs"][0]

    def test_snapshot_captures_attributes(self, source_db):
        init_db(db_path=source_db)
        with get_session() as session:
            cache = _make_cache_with_children(session)
            snap = _snapshot_cache(cache)

        assert len(snap["_attributes"]) == 1
        assert snap["_attributes"][0]["name"] == "Dogs"

    def test_snapshot_captures_trackables(self, source_db):
        init_db(db_path=source_db)
        with get_session() as session:
            cache = _make_cache_with_children(session)
            snap = _snapshot_cache(cache)

        assert len(snap["_trackables"]) == 1
        assert snap["_trackables"][0]["ref"] == "TB001"

    def test_snapshot_captures_waypoints(self, source_db):
        init_db(db_path=source_db)
        with get_session() as session:
            cache = _make_cache_with_children(session)
            snap = _snapshot_cache(cache)

        assert len(snap["_waypoints"]) == 1
        assert snap["_waypoints"][0]["prefix"] == "PK"

    def test_snapshot_captures_user_note(self, source_db):
        init_db(db_path=source_db)
        with get_session() as session:
            cache = _make_cache_with_children(session)
            snap = _snapshot_cache(cache)

        assert snap["_user_note"] is not None
        assert snap["_user_note"]["note"] == "My note"

    def test_snapshot_no_user_note(self, source_db):
        init_db(db_path=source_db)
        with get_session() as session:
            cache = Cache(gc_code="GC999", name="Bare", cache_type="Traditional Cache",
                          latitude=55.0, longitude=12.0)
            session.add(cache)
            session.flush()
            from sqlalchemy.orm import joinedload
            cache = (session.query(Cache)
                     .options(joinedload(Cache.user_note))
                     .filter_by(gc_code="GC999").first())
            snap = _snapshot_cache(cache)

        assert snap["_user_note"] is None
        assert snap["_logs"] == []


# ── _insert_snapshot tests ────────────────────────────────────────────────────

class TestInsertSnapshot:
    def test_insert_creates_cache(self, target_db):
        init_db(db_path=target_db)
        snap = {
            "gc_code": "GC300",
            "name": "Inserted",
            "cache_type": "Mystery Cache",
            "latitude": 40.0,
            "longitude": -74.0,
            "_logs": [],
            "_attributes": [],
            "_trackables": [],
            "_waypoints": [],
            "_user_note": None,
        }
        with get_session() as session:
            _insert_snapshot(session, snap)

        with get_session() as session:
            cache = session.query(Cache).filter_by(gc_code="GC300").first()
            assert cache is not None
            assert cache.name == "Inserted"

    def test_insert_replaces_existing(self, target_db):
        init_db(db_path=target_db)
        # Seed existing cache
        with get_session() as session:
            session.add(Cache(gc_code="GC400", name="Old", cache_type="Traditional Cache",
                              latitude=1.0, longitude=1.0))
        # Insert snapshot with same gc_code
        snap = {
            "gc_code": "GC400",
            "name": "Replaced",
            "cache_type": "Multi-cache",
            "latitude": 2.0,
            "longitude": 2.0,
            "_logs": [],
            "_attributes": [],
            "_trackables": [],
            "_waypoints": [],
            "_user_note": None,
        }
        with get_session() as session:
            _insert_snapshot(session, snap)

        with get_session() as session:
            cache = session.query(Cache).filter_by(gc_code="GC400").first()
            assert cache.name == "Replaced"
            assert session.query(Cache).filter_by(gc_code="GC400").count() == 1

    def test_insert_with_children(self, target_db):
        init_db(db_path=target_db)
        snap = {
            "gc_code": "GC500",
            "name": "WithKids",
            "cache_type": "Traditional Cache",
            "latitude": 55.0,
            "longitude": 12.0,
            "_logs": [{"log_type": "Found it", "finder": "A"}],
            "_attributes": [{"attribute_id": 5, "name": "Night", "is_on": True}],
            "_trackables": [{"name": "Coin", "ref": "GC01"}],
            "_waypoints": [{"prefix": "FN", "wp_type": "Final Location", "name": "Final"}],
            "_user_note": {"note": "Hello", "is_corrected": False},
        }
        with get_session() as session:
            _insert_snapshot(session, snap)

        from sqlalchemy.orm import joinedload
        with get_session() as session:
            cache = (session.query(Cache)
                     .options(
                         joinedload(Cache.logs),
                         joinedload(Cache.attributes),
                         joinedload(Cache.trackables),
                         joinedload(Cache.waypoints),
                         joinedload(Cache.user_note),
                     )
                     .filter_by(gc_code="GC500").first())
            assert len(cache.logs) == 1
            assert len(cache.attributes) == 1
            assert len(cache.trackables) == 1
            assert len(cache.waypoints) == 1
            assert cache.user_note is not None
            assert cache.user_note.note == "Hello"


# ── _MoveWorker tests (synchronous via .run()) ───────────────────────────────

class TestMoveWorker:
    def test_move_transfers_and_deletes(self, source_db, target_db):
        # Seed source
        init_db(db_path=source_db)
        with get_session() as session:
            _make_cache_with_children(session, "GCMOVE1")

        worker = _MoveWorker(
            gc_codes=["GCMOVE1"],
            source_db_path=source_db,
            target_db_path=target_db,
            copy_only=False,
        )
        results = []
        worker.finished.connect(results.append)
        worker.run()  # run synchronously (not .start())

        assert results == [1]

        # Cache should exist in target
        init_db(db_path=target_db)
        with get_session() as session:
            assert session.query(Cache).filter_by(gc_code="GCMOVE1").count() == 1
            cache = session.query(Cache).filter_by(gc_code="GCMOVE1").first()
            assert cache.name == "Test Cache"

        # Cache should be gone from source
        init_db(db_path=source_db)
        with get_session() as session:
            assert session.query(Cache).filter_by(gc_code="GCMOVE1").count() == 0
            assert session.query(Log).count() == 0

    def test_copy_keeps_source(self, source_db, target_db):
        init_db(db_path=source_db)
        with get_session() as session:
            _make_cache_with_children(session, "GCCOPY1")

        worker = _MoveWorker(
            gc_codes=["GCCOPY1"],
            source_db_path=source_db,
            target_db_path=target_db,
            copy_only=True,
        )
        results = []
        worker.finished.connect(results.append)
        worker.run()

        assert results == [1]

        # Target has the cache
        init_db(db_path=target_db)
        with get_session() as session:
            assert session.query(Cache).filter_by(gc_code="GCCOPY1").count() == 1

        # Source still has the cache
        init_db(db_path=source_db)
        with get_session() as session:
            assert session.query(Cache).filter_by(gc_code="GCCOPY1").count() == 1

    def test_move_no_matching_caches(self, source_db, target_db):
        init_db(db_path=source_db)
        worker = _MoveWorker(
            gc_codes=["GCNOTHERE"],
            source_db_path=source_db,
            target_db_path=target_db,
        )
        results = []
        worker.finished.connect(results.append)
        worker.run()

        assert results == [0]

    def test_move_multiple_caches(self, source_db, target_db):
        init_db(db_path=source_db)
        with get_session() as session:
            _make_cache_with_children(session, "GCM1")
            _make_cache_with_children(session, "GCM2")

        worker = _MoveWorker(
            gc_codes=["GCM1", "GCM2"],
            source_db_path=source_db,
            target_db_path=target_db,
            copy_only=False,
        )
        results = []
        worker.finished.connect(results.append)
        worker.run()

        assert results == [2]

        init_db(db_path=target_db)
        with get_session() as session:
            assert session.query(Cache).count() == 2

        init_db(db_path=source_db)
        with get_session() as session:
            assert session.query(Cache).count() == 0

    def test_move_error_restores_source(self, source_db, tmp_path, monkeypatch):
        init_db(db_path=source_db)
        with get_session() as session:
            _make_cache_with_children(session, "GCERR")

        # Sabotage _insert_snapshot to force an error during step 2
        import opensak.gui.dialogs.move_caches_dialog as mod
        original = mod._insert_snapshot

        def _boom(session, snap):
            raise RuntimeError("Simulated insert failure")

        monkeypatch.setattr(mod, "_insert_snapshot", _boom)

        target = tmp_path / "target_err.db"

        worker = _MoveWorker(
            gc_codes=["GCERR"],
            source_db_path=source_db,
            target_db_path=target,
        )
        errors = []
        worker.error.connect(errors.append)
        finished = []
        worker.finished.connect(finished.append)
        worker.run()

        assert len(errors) == 1
        assert "Simulated" in errors[0]
        assert finished == []

        # Worker should have restored source DB regardless
        init_db(db_path=source_db)
        with get_session() as session:
            assert session.query(Cache).filter_by(gc_code="GCERR").count() == 1

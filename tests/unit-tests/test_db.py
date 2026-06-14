"""tests/unit-tests/test_db.py — database model, session and CRUD tests."""

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
)
from opensak.db.models import Cache, Waypoint, Log, Attribute, Trackable, UserNote


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_cache() -> Cache:
    """Return a Cache instance (not yet added to any session)."""
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
    """Config paths should all be pathlib.Path instances."""
    from opensak.config import get_app_data_dir, get_db_path, get_gpx_import_dir, get_log_path
    assert isinstance(get_app_data_dir(), Path)
    assert isinstance(get_db_path(), Path)
    assert isinstance(get_gpx_import_dir(), Path)
    assert isinstance(get_log_path(), Path)


def test_config_directories_created():
    """App data and import dirs should be created automatically."""
    from opensak.config import get_app_data_dir, get_gpx_import_dir
    assert get_app_data_dir().exists()
    assert get_gpx_import_dir().exists()


# ── DB initialisation ─────────────────────────────────────────────────────────

def test_init_db_creates_file(tmp_db):
    assert tmp_db.exists(), "Database file was not created"


def test_init_db_is_idempotent(tmp_db):
    """Calling init_db a second time should not raise or corrupt the DB."""
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
    """Inserting a duplicate gc_code should raise an integrity error."""
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
    """Deleting a cache should also delete all its child records."""
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

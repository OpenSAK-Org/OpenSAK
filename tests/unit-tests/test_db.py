"""tests/unit-tests/test_db.py — database model, session and CRUD tests."""

import pytest
from pathlib import Path
from datetime import datetime

from opensak.db.database import init_db, get_session, db_health_check
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

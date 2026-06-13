"""tests/unit-tests/test_importer.py — GPX/ZIP importer tests."""

import pytest
from pathlib import Path

from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.importer import import_gpx, import_zip

from tests.data import (
    SAMPLE_GPX, SAMPLE_WPTS_GPX, EMPTY_GPX,
    make_variant_gpx, make_gpx_with_inline_wpt, write_gpx, make_zip,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def gpx_file(tmp_path) -> Path:
    return write_gpx(tmp_path, "test.gpx", SAMPLE_GPX)


@pytest.fixture
def wpts_file(tmp_path) -> Path:
    return write_gpx(tmp_path, "test-wpts.gpx", SAMPLE_WPTS_GPX)


@pytest.fixture
def zip_file(tmp_path, gpx_file, wpts_file) -> Path:
    return make_zip(tmp_path, "test_pq.zip", {
        "test.gpx": gpx_file,
        "test-wpts.gpx": wpts_file,
    })


@pytest.fixture
def multi_gpx_zip(tmp_path) -> Path:
    return make_zip(tmp_path, "multi_pq.zip", {
        "first.gpx": SAMPLE_GPX,
        "second.gpx": make_variant_gpx(),
    })


# ── Basic import tests ────────────────────────────────────────────────────────

def test_import_gpx_returns_result(tmp_db, gpx_file):
    with get_session() as s:
        result = import_gpx(gpx_file, s)
    assert result.total == 2
    assert result.created == 2
    assert result.skipped == 0
    assert result.errors == []


def test_import_gpx_cache_fields(tmp_db, gpx_file):
    """Verify all scalar fields are correctly parsed and stored."""
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert cache.name == "Test Traditional"
        assert cache.cache_type == "Traditional Cache"
        assert cache.container == "Small"
        assert cache.latitude == pytest.approx(55.6761)
        assert cache.longitude == pytest.approx(12.5683)
        assert cache.difficulty == pytest.approx(2.0)
        assert cache.terrain == pytest.approx(3.0)
        assert cache.placed_by == "TestOwner"
        assert cache.country == "Denmark"
        assert cache.state == "Zealand"
        assert cache.county == "Copenhagen"
        assert cache.encoded_hints == "Under a rock."
        assert cache.available is True
        assert cache.archived is False
        assert cache.short_desc_html is False
        assert cache.long_desc_html is True


def test_import_gpx_logs(tmp_db, gpx_file):
    """Verify logs are imported with correct fields."""
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert len(cache.logs) == 2
        found_log = next(l for l in cache.logs if l.log_type == "Found it")
        assert found_log.finder == "Tester"
        assert found_log.log_id == "111"
        assert found_log.log_date is not None
        # SQLite stores datetimes without tz info — just verify the values
        assert found_log.log_date.year == 2026
        assert found_log.log_date.month == 1
        assert found_log.log_date.day == 15


def test_import_gpx_attributes(tmp_db, gpx_file):
    """Verify attributes are imported correctly."""
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert len(cache.attributes) == 2
        kids_attr = next(a for a in cache.attributes if a.attribute_id == 6)
        assert kids_attr.name == "Recommended for kids"
        assert kids_attr.is_on is True
        wheelchair = next(a for a in cache.attributes if a.attribute_id == 24)
        assert wheelchair.is_on is False


def test_import_gpx_second_cache(tmp_db, gpx_file):
    """Verify the second cache (Unknown) is also imported."""
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC99999").one()
        assert cache.cache_type == "Unknown Cache"
        assert cache.container == "Micro"
        assert cache.difficulty == pytest.approx(4.0)
        assert len(cache.logs) == 0
        assert len(cache.attributes) == 0


# ── Waypoints tests ───────────────────────────────────────────────────────────

def test_import_with_companion_wpts(tmp_db, gpx_file, wpts_file):
    """Verify companion -wpts.gpx waypoints are linked to the correct cache."""
    with get_session() as s:
        result = import_gpx(gpx_file, s, wpts_path=wpts_file)
    assert result.waypoints == 1

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert len(cache.waypoints) == 1
        wp = cache.waypoints[0]
        assert wp.wp_type == "Parking Area"
        assert wp.prefix == "PK"
        assert wp.latitude == pytest.approx(55.6762)
        assert wp.comment == "Park here and walk 200m south."


# ── ZIP import tests ──────────────────────────────────────────────────────────

def test_import_zip(tmp_db, zip_file):
    """Verify that a PQ zip file is imported correctly end-to-end."""
    with get_session() as s:
        for cache in s.query(Cache).all():
            s.delete(cache)

    with get_session() as s:
        result = import_zip(zip_file, s)

    assert result.total == 2
    assert result.waypoints == 1
    assert result.errors == []


def test_import_zip_invalid(tmp_db, tmp_path):
    """A non-zip file should return an error, not raise an exception."""
    bad = tmp_path / "bad.zip"
    bad.write_text("this is not a zip file")
    with get_session() as s:
        result = import_zip(bad, s)
    assert len(result.errors) > 0


def test_import_zip_multiple_files(tmp_db, multi_gpx_zip):
    """Verify that a zip with multiple GPX files imports all records."""
    with get_session() as s:
        for cache in s.query(Cache).all():
            s.delete(cache)

    result = import_zip(multi_gpx_zip)

    assert result.total == 4
    assert result.created == 4
    assert result.errors == []

    with get_session() as s:
        assert s.query(Cache).count() == 4
        assert s.query(Cache).filter_by(gc_code="GC12345").first() is not None
        assert s.query(Cache).filter_by(gc_code="GCABCDE").first() is not None


# ── Upsert / duplicate handling ───────────────────────────────────────────────

def test_reimport_updates_not_duplicates(tmp_db, gpx_file):
    """Importing the same file twice should update, not duplicate."""
    with get_session() as s:
        import_gpx(gpx_file, s)

    with get_session() as s:
        import_gpx(gpx_file, s)

    with get_session() as s:
        count = s.query(Cache).filter_by(gc_code="GC12345").count()
        assert count == 1, "Duplicate cache rows created on re-import!"

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        log_count = len(cache.logs)
        assert log_count == 2, f"Expected 2 logs after re-import, got {log_count}"


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_import_empty_gpx(tmp_db, tmp_path):
    """A GPX file with no <wpt> elements should import cleanly with 0 results."""
    f = write_gpx(tmp_path, "empty.gpx", EMPTY_GPX)
    with get_session() as s:
        result = import_gpx(f, s)
    assert result.total == 0
    assert result.errors == []


def test_import_corrupt_gpx(tmp_db, tmp_path):
    """A corrupt XML file should return an error gracefully."""
    bad = tmp_path / "corrupt.gpx"
    bad.write_text("<<<not xml at all>>>", encoding="utf-8")
    with get_session() as s:
        result = import_gpx(bad, s)
    assert len(result.errors) > 0


def test_import_zip_empty(tmp_db, tmp_path):
    """A zip with no GPX files should return an error."""
    z = make_zip(tmp_path, "empty.zip", {"readme.txt": "no gpx here"})
    result = import_zip(z)
    assert result.total == 0
    assert len(result.errors) > 0


def test_import_zip_multiple_with_companion_wpts(tmp_db, tmp_path):
    """Verify companion -wpts.gpx files are linked per GPX in a multi-file zip."""
    with get_session() as s:
        for cache in s.query(Cache).all():
            s.delete(cache)

    z = make_zip(tmp_path, "multi_wpts.zip", {
        "first.gpx": SAMPLE_GPX,
        "first-wpts.gpx": SAMPLE_WPTS_GPX,
        "second.gpx": make_variant_gpx(log1="555", log2="666"),
    })

    result = import_zip(z)

    assert result.total == 4
    assert result.waypoints >= 1
    assert result.errors == []

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert len(cache.waypoints) >= 1
        assert any(wp.prefix == "PK" for wp in cache.waypoints)


def test_import_gpx_inline_extra_waypoints(tmp_db, tmp_path):
    """Verify extra waypoints embedded in the main GPX are linked to their cache."""
    with get_session() as s:
        for cache in s.query(Cache).all():
            s.delete(cache)

    f = write_gpx(tmp_path, "inline_wpts.gpx", make_gpx_with_inline_wpt())
    result = import_gpx(f)

    assert result.total == 2
    assert result.waypoints == 1
    assert result.errors == []

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert len(cache.waypoints) == 1
        wp = cache.waypoints[0]
        assert wp.prefix == "PK"
        assert wp.wp_type == "Parking Area"
        assert wp.comment == "Street parking available."

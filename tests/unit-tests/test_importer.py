# tests/unit-tests/test_importer.py — GPX/ZIP importer tests.

import pytest
from pathlib import Path

from opensak.db.database import get_session, init_db
from opensak.db.models import Cache
from opensak.importer import import_gpx, import_zip, ImportResult, _count_wpts, _is_companion_gpx

from tests.data import (
    SAMPLE_GPX, SAMPLE_WPTS_GPX, EMPTY_GPX,
    make_variant_gpx, make_gpx_with_inline_wpt, write_gpx, make_zip,
    build_gpx, cache_wpt,
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
    # Verify all scalar fields are correctly parsed and stored.
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
    # Verify logs are imported with correct fields.
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
    # Verify attributes are imported correctly.
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert len(cache.attributes) == 2
        kids_attr = next(a for a in cache.attributes if a.attribute_id == 6)
        assert kids_attr.name == "Recommended for kids"
        assert kids_attr.is_on is True
        wheelchair = next(a for a in cache.attributes if a.attribute_id == 24)
        assert wheelchair.is_on is False


def test_import_gpx_second_cache(tmp_db, gpx_file):
    # Verify the second cache (Unknown) is also imported.
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC99999").one()
        assert cache.cache_type == "Unknown Cache"
        assert cache.container == "Micro"
        assert cache.difficulty == pytest.approx(4.0)
        assert len(cache.logs) == 0
        assert len(cache.attributes) == 0


# ── Waypoints tests ───────────────────────────────────────────────────────────

def test_import_with_companion_wpts(tmp_db, gpx_file, wpts_file):
    # Verify companion -wpts.gpx waypoints are linked to the correct cache.
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
        assert wp.parent_gc_code == "GC12345"


def test_waypoint_count_set_after_companion_import(tmp_db, gpx_file, wpts_file):
    # waypoint_count on the Cache row must reflect companion waypoints after import.
    with get_session() as s:
        import_gpx(gpx_file, s, wpts_path=wpts_file)

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert cache.waypoint_count == 1
        no_wpts = s.query(Cache).filter_by(gc_code="GC99999").one()
        assert no_wpts.waypoint_count == 0


def test_waypoint_count_set_after_inline_import(tmp_db, tmp_path):
    # waypoint_count must be set when waypoints are embedded in the same GPX.
    gpx = write_gpx(tmp_path, "inline.gpx", make_gpx_with_inline_wpt())
    import_gpx(gpx)

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert cache.waypoint_count == 1


def test_waypoint_count_reset_on_reimport_without_wpts(tmp_db, gpx_file, wpts_file, tmp_path):
    # Re-importing a cache without waypoints must zero out waypoint_count.
    with get_session() as s:
        import_gpx(gpx_file, s, wpts_path=wpts_file)

    with get_session() as s:
        assert s.query(Cache).filter_by(gc_code="GC12345").one().waypoint_count == 1

    # Re-import the same GPX without the companion file.
    with get_session() as s:
        import_gpx(gpx_file, s)

    with get_session() as s:
        assert s.query(Cache).filter_by(gc_code="GC12345").one().waypoint_count == 0


# ── ZIP import tests ──────────────────────────────────────────────────────────

def test_import_zip(tmp_db, zip_file):
    # Verify that a PQ zip file is imported correctly end-to-end.
    with get_session() as s:
        for cache in s.query(Cache).all():
            s.delete(cache)

    with get_session() as s:
        result = import_zip(zip_file, s)

    assert result.total == 2
    assert result.waypoints == 1
    assert result.errors == []


def test_import_zip_invalid(tmp_db, tmp_path):
    # A non-zip file should return an error, not raise an exception.
    bad = tmp_path / "bad.zip"
    bad.write_text("this is not a zip file")
    with get_session() as s:
        result = import_zip(bad, s)
    assert len(result.errors) > 0


def test_import_zip_multiple_files(tmp_db, multi_gpx_zip):
    # Verify that a zip with multiple GPX files imports all records.
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
    # Importing the same file twice should update, not duplicate.
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


# ── Issue #202: Lock a cache ────────────────────────────────────────────────

def test_reimport_skips_scalar_fields_when_locked(tmp_db, gpx_file, tmp_path):
    # A locked cache must keep its scalar fields exactly as they were,
    # even when a re-import would otherwise change them (e.g. a difficulty
    # rerate or a renamed listing).
    with get_session() as s:
        import_gpx(gpx_file, s)

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        cache.locked = True

    changed_gpx = (
        SAMPLE_GPX
        .replace(
            "<groundspeak:difficulty>2.0</groundspeak:difficulty>",
            "<groundspeak:difficulty>4.5</groundspeak:difficulty>",
        )
        .replace(
            "<groundspeak:name>Test Traditional</groundspeak:name>",
            "<groundspeak:name>Renamed Cache</groundspeak:name>",
        )
    )
    changed_file = write_gpx(tmp_path, "changed.gpx", changed_gpx)

    with get_session() as s:
        import_gpx(changed_file, s)

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert cache.difficulty == pytest.approx(2.0), \
            "Locked cache's difficulty was overwritten by re-import!"
        assert cache.name == "Test Traditional", \
            "Locked cache's name was overwritten by re-import!"
        # locked itself is untouched by import — only cleared by the user.
        assert cache.locked is True


def test_reimport_still_updates_when_unlocked(tmp_db, gpx_file, tmp_path):
    # Sanity counterpart to the test above: an *unlocked* cache (the
    # default) must still pick up changes on re-import, same as before
    # issue #202 — locking must not accidentally become the default.
    with get_session() as s:
        import_gpx(gpx_file, s)

    # tmp_db is module-scoped and GC12345 may have been left locked by an
    # earlier test in this module — explicitly unlock so this test is
    # order-independent.
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        cache.locked = False

    changed_gpx = SAMPLE_GPX.replace(
        "<groundspeak:difficulty>2.0</groundspeak:difficulty>",
        "<groundspeak:difficulty>4.5</groundspeak:difficulty>",
    )
    changed_file = write_gpx(tmp_path, "changed_unlocked.gpx", changed_gpx)

    with get_session() as s:
        import_gpx(changed_file, s)

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert cache.difficulty == pytest.approx(4.5)
        assert cache.locked is False


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_import_empty_gpx(tmp_db, tmp_path):
    # A GPX file with no <wpt> elements should import cleanly with 0 results.
    f = write_gpx(tmp_path, "empty.gpx", EMPTY_GPX)
    with get_session() as s:
        result = import_gpx(f, s)
    assert result.total == 0
    assert result.errors == []


def test_import_corrupt_gpx(tmp_db, tmp_path):
    # A corrupt XML file should return an error gracefully.
    bad = tmp_path / "corrupt.gpx"
    bad.write_text("<<<not xml at all>>>", encoding="utf-8")
    with get_session() as s:
        result = import_gpx(bad, s)
    assert len(result.errors) > 0


def test_import_zip_empty(tmp_db, tmp_path):
    # A zip with no GPX files should return an error.
    z = make_zip(tmp_path, "empty.zip", {"readme.txt": "no gpx here"})
    result = import_zip(z)
    assert result.total == 0
    assert len(result.errors) > 0


def test_import_zip_multiple_with_companion_wpts(tmp_db, tmp_path):
    # Verify companion -wpts.gpx files are linked per GPX in a multi-file zip.
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


def test_import_zip_companion_detected_by_content_not_name(tmp_db, tmp_path):
    # Companion file with a non-standard name (no -wpts suffix) must still be
    # detected and linked when its content identifies it as waypoints-only.
    with get_session() as s:
        for cache in s.query(Cache).all():
            s.delete(cache)

    z = make_zip(tmp_path, "renamed_wpts.zip", {
        "caches.gpx":    SAMPLE_GPX,
        "extras.gpx":    SAMPLE_WPTS_GPX,   # no -wpts in the name
    })

    result = import_zip(z)

    assert result.waypoints >= 1, "companion file should be linked by content, not name"
    assert result.errors == []
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert any(wp.prefix == "PK" for wp in cache.waypoints)


def test_parse_extra_waypoints_handles_name_element(tmp_db, tmp_path):
    # Real geocaching.com companion files use <name> not <n> for the waypoint
    # code.  _parse_extra_waypoints must handle both.
    wpts_name_fmt = """\
<?xml version="1.0" encoding="utf-8"?>
<gpx version="1.0" creator="Groundspeak, Inc."
     xmlns="http://www.topografix.com/GPX/1/0">
  <name>Waypoints</name>
  <wpt lat="55.6762" lon="12.5680">
    <name>PK2345</name>
    <desc>Parking</desc>
    <type>Waypoint|Parking Area</type>
    <cmt>Park here.</cmt>
  </wpt>
</gpx>
"""
    gpx  = write_gpx(tmp_path, "main.gpx",  SAMPLE_GPX)
    wpts = write_gpx(tmp_path, "wpts.gpx",  wpts_name_fmt)

    with get_session() as s:
        result = import_gpx(gpx, s, wpts_path=wpts)

    assert result.waypoints == 1, "<name> element in companion file not parsed"
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert any(wp.prefix == "PK" for wp in cache.waypoints)


def test_import_lb_lab_cache_codes(tmp_db, tmp_path):
    # lab2gpx exports Adventure Lab stages with LB* codes (Geocache|Lab Cache).
    # These were silently dropped because only GC and LC prefixes were accepted.
    gpx = write_gpx(tmp_path, "labs.gpx", build_gpx(
        cache_wpt("LB1AQD01", name="Lab Stage 1", cache_type="Lab Cache"),
        cache_wpt("LB1AQD02", name="Lab Stage 2", cache_type="Lab Cache"),
        cache_wpt("LC9XYZ01", name="Lab Stage LC", cache_type="Lab Cache"),
    ))
    with get_session() as s:
        result = import_gpx(gpx, s)
    assert result.total == 3, f"Expected 3 imported, got {result.total}"
    assert result.errors == []
    with get_session() as s:
        assert s.query(Cache).filter_by(gc_code="LB1AQD01").count() == 1
        assert s.query(Cache).filter_by(gc_code="LB1AQD02").count() == 1
        assert s.query(Cache).filter_by(gc_code="LC9XYZ01").count() == 1


def test_import_gpx_inline_extra_waypoints(tmp_db, tmp_path):
    # Verify extra waypoints embedded in the main GPX are linked to their cache.
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
        assert wp.parent_gc_code == "GC12345"


# ── ImportResult.__str__ ──────────────────────────────────────────────────────

def test_import_result_str_lists_warnings_and_caps_errors():
    r = ImportResult()
    r.created, r.updated, r.waypoints, r.skipped = 3, 1, 2, 4
    r.warnings.append("limited data")
    r.errors.extend(f"err{i}" for i in range(7))
    text = str(r)
    assert "Caches created : 3" in text
    assert "⚠ limited data" in text
    assert "Errors         : 7" in text
    assert text.count("    - err") == 5  # only first 5 shown


# ── import_gpx error/edge paths ───────────────────────────────────────────────

def test_import_gpx_reimport_updates_existing(tmp_path):
    init_db(db_path=tmp_path / "re.db")
    first = build_gpx(cache_wpt(
        "GCUP01", name="Original", difficulty=1.0,
        logs=[{"type": "Found it", "finder": "A"}],
        attributes=[{"id": 6, "inc": 1, "name": "Kids"}],
    ))
    second = build_gpx(cache_wpt(
        "GCUP01", name="Updated", difficulty=4.0,
        logs=[{"type": "Found it", "finder": "B"}],
        attributes=[{"id": 7, "inc": 0, "name": "Other"}],
    ))
    r1 = import_gpx(write_gpx(tmp_path, "a.gpx", first))
    r2 = import_gpx(write_gpx(tmp_path, "b.gpx", second))
    assert r1.created == 1
    assert r2.updated == 1
    with get_session() as s:
        c = s.query(Cache).filter_by(gc_code="GCUP01").one()
        assert c.name == "Updated"
        assert c.difficulty == pytest.approx(4.0)


def test_import_gpx_minimal_cache_without_optional_fields(tmp_path):
    init_db(db_path=tmp_path / "min.db")
    f = write_gpx(tmp_path, "min.gpx", build_gpx(cache_wpt("GCMIN1")))
    result = import_gpx(f)
    assert result.created == 1
    assert result.errors == []


def test_import_gpx_fatal_parse_error(tmp_path):
    init_db(db_path=tmp_path / "bad.db")
    f = write_gpx(tmp_path, "broken.gpx", "<gpx><wpt this is not valid xml")
    result = import_gpx(f)
    assert len(result.errors) > 0


def test_import_gpx_companion_wpts_file_error(tmp_path):
    init_db(db_path=tmp_path / "comp.db")
    gpx = write_gpx(tmp_path, "main.gpx", SAMPLE_GPX)
    bad_wpts = write_gpx(tmp_path, "main-wpts.gpx", "definitely not xml <<<")
    result = import_gpx(gpx, wpts_path=bad_wpts)
    assert any("Waypoints file error" in e for e in result.errors)


# ── import_zip edge paths ─────────────────────────────────────────────────────

def test_import_zip_no_gpx_in_archive(tmp_path):
    z = make_zip(tmp_path, "no_gpx.zip", {"readme.txt": "hello"})
    result = import_zip(z)
    assert any("No .gpx" in e for e in result.errors)


def test_count_wpts(tmp_path):
    # _count_wpts must return the exact number of <wpt> elements without importing
    gpx = build_gpx(cache_wpt("GC0001"), cache_wpt("GC0002"), cache_wpt("GC0003"))
    f = write_gpx(tmp_path, "count_test.gpx", gpx)
    assert _count_wpts(f) == 3


def test_count_wpts_empty(tmp_path):
    f = write_gpx(tmp_path, "empty.gpx", '<?xml version="1.0"?><gpx version="1.0"></gpx>')
    assert _count_wpts(f) == 0


# ── _is_companion_gpx ─────────────────────────────────────────────────────────

def test_is_companion_gpx_returns_false_for_cache_file(tmp_path):
    f = write_gpx(tmp_path, "caches.gpx", SAMPLE_GPX)
    assert _is_companion_gpx(f) is False


def test_is_companion_gpx_returns_true_for_wpts_file(tmp_path):
    f = write_gpx(tmp_path, "wpts.gpx", SAMPLE_WPTS_GPX)
    assert _is_companion_gpx(f) is True


def test_is_companion_gpx_returns_false_for_empty_file(tmp_path):
    f = write_gpx(tmp_path, "empty.gpx", EMPTY_GPX)
    assert _is_companion_gpx(f) is False


def test_is_companion_gpx_returns_false_for_nonexistent_file(tmp_path):
    assert _is_companion_gpx(tmp_path / "ghost.gpx") is False


# ── found_date derivation from logs (issue #457) ──────────────────────────────
# found_date was previously only derived from "Found it" logs, so caches whose
# find is logged with a different log_type (webcam caches, events) silently
# ended up with no found_date, even though found=True was set correctly from
# the GPX <sym>Geocache Found</sym> flag. Reproduced with a real My Finds PQ.

def test_import_found_date_webcam_cache(tmp_db, tmp_path):
    # Webcam Caches log a find as "Webcam Photo Taken", not "Found it".
    gpx = build_gpx(cache_wpt(
        "GCWEBCAM", cache_type="Webcam Cache", sym="Geocache Found", gs_id=45701,
        logs=[{"id": "45701001", "type": "Webcam Photo Taken", "date": "2013-12-26T20:00:00Z"}],
    ))
    f = write_gpx(tmp_path, "webcam.gpx", gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCWEBCAM").one()
        assert cache.found is True
        assert cache.found_date is not None
        assert cache.found_date.year == 2013


def test_import_found_date_event_attended(tmp_db, tmp_path):
    # Events log a find as "Attended", not "Found it".
    gpx = build_gpx(cache_wpt(
        "GCEVENT1", cache_type="Event Cache", sym="Geocache Found", gs_id=45702,
        logs=[{"id": "45702001", "type": "Attended", "date": "2010-07-31T19:00:00Z"}],
    ))
    f = write_gpx(tmp_path, "event.gpx", gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCEVENT1").one()
        assert cache.found is True
        assert cache.found_date is not None
        assert cache.found_date.year == 2010


def test_import_found_date_still_works_for_found_it(tmp_db, tmp_path):
    # Regression guard: the common "Found it" case must keep working.
    gpx = build_gpx(cache_wpt(
        "GCTRAD1", cache_type="Traditional Cache", sym="Geocache Found", gs_id=45703,
        logs=[{"id": "45703001", "type": "Found it", "date": "2022-06-15T00:00:00Z"}],
    ))
    f = write_gpx(tmp_path, "trad.gpx", gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCTRAD1").one()
        assert cache.found is True
        assert cache.found_date is not None
        assert cache.found_date.year == 2022


def test_import_found_date_ignores_other_log_types(tmp_db, tmp_path):
    # A "Write note" or "Didn't find it" log must NOT be picked up as a found
    # date, even when the cache is otherwise marked found (sym=Geocache Found)
    # — that combination shouldn't normally occur, but the derivation must
    # stay type-specific rather than "any log with a date".
    gpx = build_gpx(cache_wpt(
        "GCNODATE", cache_type="Traditional Cache", sym="Geocache Found", gs_id=45704,
        logs=[
            {"id": "45704001", "type": "Write note", "date": "2021-01-01T00:00:00Z"},
            {"id": "45704002", "type": "Didn't find it", "date": "2021-02-01T00:00:00Z"},
        ],
    ))
    f = write_gpx(tmp_path, "nodate.gpx", gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCNODATE").one()
        assert cache.found is True
        assert cache.found_date is None


def test_import_found_date_picks_oldest_among_found_log_types(tmp_db, tmp_path):
    # With multiple candidate "found" logs (mixed types), the oldest one wins —
    # same fallback behaviour as before, just across the full FOUND_LOG_TYPES set.
    gpx = build_gpx(cache_wpt(
        "GCMULTI1", cache_type="Webcam Cache", sym="Geocache Found", gs_id=45705,
        logs=[
            {"id": "45705001", "type": "Webcam Photo Taken", "date": "2015-05-05T00:00:00Z"},
            {"id": "45705002", "type": "Found it", "date": "2023-01-01T00:00:00Z"},
        ],
    ))
    f = write_gpx(tmp_path, "multi.gpx", gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCMULTI1").one()
        assert cache.found_date is not None
        assert cache.found_date.year == 2015


# ── FTF tag-based detection (issue: false positives from free-text match) ────
# The old FTF detection matched free-text phrases ("ftf", "first to find",
# "first finder", ...) anywhere in the user's own found-log text, which
# flagged logs that merely mention the concept without claiming an FTF —
# e.g. a "Thanks For The Cache" (TFTC) log describing a *failed* attempt at
# being first finder. ProjectGC (the de-facto FTF stats source) only credits
# an FTF when the log contains one of {FTF}, {*FTF*} or [FTF], so that's now
# the only thing OpenSAK matches on too.

def _ftf_gpx(gc_code: str, gs_id: int, log_id: str, log_text: str) -> str:
    return build_gpx(cache_wpt(
        gc_code, cache_type="Traditional Cache", sym="Geocache Found", gs_id=gs_id,
        logs=[{
            "id": log_id, "type": "Found it", "date": "2024-01-01T00:00:00Z",
            "finder": "AB Green", "finder_id": "12345", "text": log_text,
        }],
    ))


@pytest.fixture
def ftf_username(monkeypatch):
    # FTF detection requires a configured gc_username/gc_finder_id to identify
    # the user's own logs among the (possibly several) logs on a cache.
    from opensak.gui.settings import get_settings
    get_settings().gc_username = "AB Green"


@pytest.mark.parametrize("log_text", [
    "Great cache! {FTF}",
    "Great cache! {ftf}",       # case-insensitive
    "Great cache! {*FTF*}",
    "Great cache! [FTF]",
    "[ftf] nice one",
])
def test_import_ftf_detected_for_official_pgc_tags(tmp_db, tmp_path, ftf_username, log_text):
    gpx = _ftf_gpx("GCFTFOK1", 45801, "45801001", log_text)
    f = write_gpx(tmp_path, "ftf_ok.gpx", gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCFTFOK1").one()
        assert cache.first_to_find is True


@pytest.mark.parametrize("log_text", [
    "Fundet på vej hjem efter et forgæves forsøg på at blive first finder på Sort [:)] TFTC",
    "TFTC",
    "I was first to find since the cache was relocated",
    "Congrats to the actual FTF finder!",
])
def test_import_ftf_not_detected_for_free_text_mentions(tmp_db, tmp_path, ftf_username, log_text):
    gpx = _ftf_gpx("GCFTFNO1", 45802, "45802001", log_text)
    f = write_gpx(tmp_path, "ftf_no.gpx", gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCFTFNO1").one()
        assert cache.first_to_find is False


# ── Trackables (issue #489/#491) ────────────────────────────────────────────

def test_import_gpx_sets_trackable_count(tmp_db, tmp_path):
    # Issue #489/#491: trackable_count must be populated from the parsed
    # <groundspeak:travelbugs> block on real GPX import, not just when
    # Trackable rows are added directly in the DB.
    gpx_with_tb = SAMPLE_GPX.replace(
        "</groundspeak:logs>",
        "</groundspeak:logs>\n"
        '      <groundspeak:travelbugs>\n'
        '        <groundspeak:travelbug ref="TB1AAAA">\n'
        '          <groundspeak:name>Speedy the Snail</groundspeak:name>\n'
        '        </groundspeak:travelbug>\n'
        '        <groundspeak:travelbug ref="TB1BBBB">\n'
        '          <groundspeak:name>Wandering Gnome</groundspeak:name>\n'
        '        </groundspeak:travelbug>\n'
        '      </groundspeak:travelbugs>',
        1,
    )
    f = write_gpx(tmp_path, "with_tb.gpx", gpx_with_tb)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert cache.trackable_count == 2
        assert {tb.ref for tb in cache.trackables} == {"TB1AAAA", "TB1BBBB"}

    # The other cache in the same GPX has no travelbugs block at all.
    with get_session() as s:
        other = s.query(Cache).filter_by(gc_code="GC99999").one()
        assert other.trackable_count == 0


def test_reimport_updates_trackable_count(tmp_db, tmp_path):
    # Re-importing with fewer/more travelbugs must update the count, not
    # just accumulate stale rows (mirrors test_reimport_updates_not_duplicates).
    gpx_with_2_tb = SAMPLE_GPX.replace(
        "</groundspeak:logs>",
        "</groundspeak:logs>\n"
        '      <groundspeak:travelbugs>\n'
        '        <groundspeak:travelbug ref="TB1AAAA">\n'
        '          <groundspeak:name>Speedy the Snail</groundspeak:name>\n'
        '        </groundspeak:travelbug>\n'
        '        <groundspeak:travelbug ref="TB1BBBB">\n'
        '          <groundspeak:name>Wandering Gnome</groundspeak:name>\n'
        '        </groundspeak:travelbug>\n'
        '      </groundspeak:travelbugs>',
        1,
    )
    f1 = write_gpx(tmp_path, "tb_v1.gpx", gpx_with_2_tb)
    with get_session() as s:
        import_gpx(f1, s)
        assert s.query(Cache).filter_by(gc_code="GC12345").one().trackable_count == 2

    gpx_with_0_tb = SAMPLE_GPX  # no travelbugs block — the bug moved on
    f2 = write_gpx(tmp_path, "tb_v2.gpx", gpx_with_0_tb)
    with get_session() as s:
        import_gpx(f2, s)
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        assert cache.trackable_count == 0
        assert list(cache.trackables) == []


# ── found_log_count (issue #552) ──────────────────────────────────────────────
# Cache.found is a boolean ("have I found this cache at least once"), which
# undercounts relocatable/multi-visit caches where the same user can
# legitimately log multiple found-type entries — geocaching.com and GSAK both
# count found LOGS, not found CACHES, in their totals (Mike Wood's report).

def test_import_found_log_count_counts_relocatable_cache(tmp_db, tmp_path, ftf_username):
    # A relocatable cache (e.g. GCCF79 in the issue report) the user found
    # 3 separate times must contribute 3, not 1.
    gpx = build_gpx(cache_wpt(
        "GCRELOC1", cache_type="Traditional Cache", sym="Geocache Found", gs_id=45901,
        logs=[
            {"id": "45901001", "type": "Found it", "date": "2020-01-01T00:00:00Z",
             "finder": "AB Green", "finder_id": "12345"},
            {"id": "45901002", "type": "Found it", "date": "2021-01-01T00:00:00Z",
             "finder": "AB Green", "finder_id": "12345"},
            {"id": "45901003", "type": "Found it", "date": "2022-01-01T00:00:00Z",
             "finder": "AB Green", "finder_id": "12345"},
        ],
    ))
    f = write_gpx(tmp_path, "reloc.gpx", gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCRELOC1").one()
        assert cache.found is True
        assert cache.found_log_count == 3


def test_import_found_log_count_ignores_other_finders_logs(tmp_db, tmp_path, ftf_username):
    # Only the current user's own found-type logs count — other finders'
    # logs on the same (possibly relocatable) cache must not inflate the total.
    gpx = build_gpx(cache_wpt(
        "GCRELOC2", cache_type="Traditional Cache", sym="Geocache Found", gs_id=45902,
        logs=[
            {"id": "45902001", "type": "Found it", "date": "2020-01-01T00:00:00Z",
             "finder": "AB Green", "finder_id": "12345"},
            {"id": "45902002", "type": "Found it", "date": "2020-06-01T00:00:00Z",
             "finder": "Some Other Cacher", "finder_id": "99999"},
        ],
    ))
    f = write_gpx(tmp_path, "reloc2.gpx", gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCRELOC2").one()
        assert cache.found_log_count == 1


def test_import_found_log_count_counts_across_found_log_types(tmp_db, tmp_path, ftf_username):
    # Mirrors the found_date fix (#457): webcam/event found-type logs must
    # count too, not just "Found it".
    gpx = build_gpx(cache_wpt(
        "GCRELOC3", cache_type="Webcam Cache", sym="Geocache Found", gs_id=45903,
        logs=[
            {"id": "45903001", "type": "Webcam Photo Taken", "date": "2018-01-01T00:00:00Z",
             "finder": "AB Green", "finder_id": "12345"},
            {"id": "45903002", "type": "Webcam Photo Taken", "date": "2019-01-01T00:00:00Z",
             "finder": "AB Green", "finder_id": "12345"},
        ],
    ))
    f = write_gpx(tmp_path, "reloc3.gpx", gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCRELOC3").one()
        assert cache.found_log_count == 2


def test_import_found_log_count_zero_without_username_configured(tmp_db, tmp_path):
    # No gc_username/gc_finder_id configured — we can't identify the user's
    # own logs, so found_log_count stays at its default 0 rather than
    # guessing. mainwindow.py's footer count falls back to counting the
    # cache itself (found=True) in this case, so nothing regresses.
    gpx = build_gpx(cache_wpt(
        "GCNOUSER", cache_type="Traditional Cache", sym="Geocache Found", gs_id=45904,
        logs=[{"id": "45904001", "type": "Found it", "date": "2022-01-01T00:00:00Z",
               "finder": "AB Green", "finder_id": "12345"}],
    ))
    f = write_gpx(tmp_path, "nouser.gpx", gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCNOUSER").one()
        assert cache.found is True
        assert cache.found_log_count == 0

"""tests/unit-tests/test_gsak_corrected_coords.py — GSAK corrected-coords import (issue #129)."""

import textwrap
from pathlib import Path

import pytest

from opensak.db.database import get_session, init_db
from opensak.db.models import Cache
from opensak.importer import import_gpx


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def fresh_db(tmp_path):
    """Isolated DB per test — prevents state leaking between tests."""
    db_path = tmp_path / "corrected.db"
    init_db(db_path=db_path)
    return db_path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_gpx(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "test.gpx"
    p.write_text(content, encoding="utf-8")
    return p


def _gpx(gsak_extension: str, extra_logs: str = "") -> str:
    """Wrap a <gsak:wptExtension> block in a minimal GSAK-style GPX file."""
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="utf-8"?>
        <gpx xmlns="http://www.topografix.com/GPX/1/0"
             xmlns:groundspeak="http://www.groundspeak.com/cache/1/0/1"
             xmlns:gsak="http://www.gsak.net/xmlv1/6"
             version="1.0" creator="GSAK">
          <wpt lat="55.0000" lon="10.0000">
            <time>2024-01-01T00:00:00</time>
            <n>GCTEST1</n>
            <desc>Test Mystery by Owner, Unknown Cache (3/3)</desc>
            <type>Geocache|Unknown Cache</type>
            <groundspeak:cache id="1" archived="False" available="True">
              <groundspeak:name>Test Mystery</groundspeak:name>
              <groundspeak:placed_by>Owner</groundspeak:placed_by>
              <groundspeak:owner id="1">Owner</groundspeak:owner>
              <groundspeak:type>Unknown Cache</groundspeak:type>
              <groundspeak:container>Small</groundspeak:container>
              <groundspeak:difficulty>3.0</groundspeak:difficulty>
              <groundspeak:terrain>3.0</groundspeak:terrain>
              <groundspeak:country>Denmark</groundspeak:country>
              <groundspeak:state>Zealand</groundspeak:state>
              <groundspeak:encoded_hints>In a tree.</groundspeak:encoded_hints>
              <groundspeak:logs>{extra_logs}</groundspeak:logs>
            </groundspeak:cache>
            {gsak_extension}
          </wpt>
        </gpx>
    """)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_gsak_corrected_coords_imported(tmp_path, fresh_db):
    """Corrected LatN/LongE from GSAK wptExtension are stored in UserNote."""
    gpx = _gpx("""
        <gsak:wptExtension>
          <gsak:LatN>55.1234</gsak:LatN>
          <gsak:LongE>10.5678</gsak:LongE>
        </gsak:wptExtension>
    """)
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        assert cache.user_note is not None
        assert cache.user_note.is_corrected is True
        assert abs(cache.user_note.corrected_lat - 55.1234) < 1e-6
        assert abs(cache.user_note.corrected_lon - 10.5678) < 1e-6


def test_gsak_corrected_coords_zero_ignored(tmp_path, fresh_db):
    """GSAK writes 0.0/0.0 when no corrected coords are set — must be ignored."""
    gpx = _gpx("""
        <gsak:wptExtension>
          <gsak:LatN>0.0</gsak:LatN>
          <gsak:LongE>0.0</gsak:LongE>
        </gsak:wptExtension>
    """)
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        # UserNote may or may not exist, but must not be marked corrected
        note = cache.user_note
        if note is not None:
            assert note.is_corrected is False


def test_gsak_no_corrected_coords(tmp_path, fresh_db):
    """Cache without any wptExtension must not be marked as corrected."""
    gpx = _gpx("")  # no gsak extension at all
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        note = cache.user_note
        if note is not None:
            assert note.is_corrected is False


def test_gsak_corrected_coords_survive_reimport(tmp_path, fresh_db):
    """Re-importing without corrected coords must NOT overwrite existing ones."""
    # First import — with corrected coords
    gpx_with = _gpx("""
        <gsak:wptExtension>
          <gsak:LatN>55.9999</gsak:LatN>
          <gsak:LongE>10.8888</gsak:LongE>
        </gsak:wptExtension>
    """)
    import_gpx(_write_gpx(tmp_path, gpx_with), fresh_db)

    # Second import — no corrected coords (e.g. plain geocaching.com GPX)
    gpx_without = _gpx("")
    (tmp_path / "test.gpx").write_text(gpx_without, encoding="utf-8")
    import_gpx(tmp_path / "test.gpx", fresh_db)

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        # Corrected coords from first import must still be present
        assert cache.user_note is not None
        assert cache.user_note.is_corrected is True
        assert abs(cache.user_note.corrected_lat - 55.9999) < 1e-6
        assert abs(cache.user_note.corrected_lon - 10.8888) < 1e-6


def _gpx_format_b(orig_lat: str, orig_lon: str, wpt_lat: str = "55.1500", wpt_lon: str = "10.5500") -> str:
    """GPX in GSAK Format B: wpt has corrected coords, LatBeforeCorrect has originals."""
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="utf-8"?>
        <gpx xmlns="http://www.topografix.com/GPX/1/0"
             xmlns:groundspeak="http://www.groundspeak.com/cache/1/0/1"
             xmlns:gsak="http://www.gsak.net/xmlv1/6"
             version="1.0" creator="GSAK">
          <wpt lat="{wpt_lat}" lon="{wpt_lon}">
            <time>2024-01-01T00:00:00</time>
            <n>GCTEST1</n>
            <desc>Test Mystery by Owner, Unknown Cache (3/3)</desc>
            <type>Geocache|Unknown Cache</type>
            <groundspeak:cache id="1" archived="False" available="True">
              <groundspeak:name>Test Mystery</groundspeak:name>
              <groundspeak:placed_by>Owner</groundspeak:placed_by>
              <groundspeak:owner id="1">Owner</groundspeak:owner>
              <groundspeak:type>Unknown Cache</groundspeak:type>
              <groundspeak:container>Small</groundspeak:container>
              <groundspeak:difficulty>3.0</groundspeak:difficulty>
              <groundspeak:terrain>3.0</groundspeak:terrain>
              <groundspeak:country>Denmark</groundspeak:country>
              <groundspeak:state>Zealand</groundspeak:state>
              <groundspeak:encoded_hints>In a tree.</groundspeak:encoded_hints>
              <groundspeak:logs></groundspeak:logs>
            </groundspeak:cache>
            <gsak:wptExtension>
              <gsak:LatBeforeCorrect>{orig_lat}</gsak:LatBeforeCorrect>
              <gsak:LonBeforeCorrect>{orig_lon}</gsak:LonBeforeCorrect>
            </gsak:wptExtension>
          </wpt>
        </gpx>
    """)


def test_gsak_format_b_corrected_coords_imported(tmp_path, fresh_db):
    """Format B: wpt lat/lon are corrected coords; LatBeforeCorrect holds originals."""
    gpx = _gpx_format_b(orig_lat="55.0000", orig_lon="10.0000",
                         wpt_lat="55.1500", wpt_lon="10.5500")
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        # Cache lat/lon must be restored to original values
        assert abs(cache.latitude  - 55.0000) < 1e-6
        assert abs(cache.longitude - 10.0000) < 1e-6
        # Corrected coords must be stored in user_note
        assert cache.user_note is not None
        assert cache.user_note.is_corrected is True
        assert abs(cache.user_note.corrected_lat - 55.1500) < 1e-6
        assert abs(cache.user_note.corrected_lon - 10.5500) < 1e-6


def test_gsak_format_b_same_coords_still_corrected(tmp_path, fresh_db):
    """Format B: LatBeforeCorrect present = CC is set, even if coords are identical.

    GSAK users sometimes set CC to the same location as the original to mark
    a cache as 'want to find' — the presence of LatBeforeCorrect is the signal.
    """
    gpx = _gpx_format_b(orig_lat="55.0000", orig_lon="10.0000",
                         wpt_lat="55.0000", wpt_lon="10.0000")
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        # LatBeforeCorrect present → must be marked as corrected
        assert cache.user_note is not None
        assert cache.user_note.is_corrected is True
        assert abs(cache.user_note.corrected_lat - 55.0000) < 1e-6
        assert abs(cache.user_note.corrected_lon - 10.0000) < 1e-6


def test_gsak_ftf_and_corrected_coords_together(tmp_path, fresh_db):
    """FTF flag and corrected coords can coexist in the same wptExtension."""
    gpx = _gpx(
        gsak_extension="""
            <gsak:wptExtension>
              <gsak:FirstToFind>True</gsak:FirstToFind>
              <gsak:LatN>56.1111</gsak:LatN>
              <gsak:LongE>11.2222</gsak:LongE>
            </gsak:wptExtension>
        """,
        extra_logs="""
            <groundspeak:log id="999">
              <groundspeak:date>2024-06-01T10:00:00Z</groundspeak:date>
              <groundspeak:type>Found it</groundspeak:type>
              <groundspeak:finder id="1">Tester</groundspeak:finder>
              <groundspeak:text encoded="False">FTF!</groundspeak:text>
            </groundspeak:log>
        """,
    )
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        assert cache.first_to_find is True
        assert cache.user_note is not None
        assert cache.user_note.is_corrected is True
        assert abs(cache.user_note.corrected_lat - 56.1111) < 1e-6
        assert abs(cache.user_note.corrected_lon - 11.2222) < 1e-6

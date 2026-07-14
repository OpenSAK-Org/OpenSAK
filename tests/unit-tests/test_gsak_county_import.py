# tests/unit-tests/test_gsak_county_import.py — GSAK County import (issue #521).
#
# The official Groundspeak GPX schema has no <county> element at all (only
# country and state) — GSAK adds its own <gsak:County> inside wptExtension
# to fill that gap when exporting with "Include GSAK fields" checked. The
# importer previously only looked for county on groundspeak:cache, so
# GSAK-exported county data was silently dropped.

import textwrap
from pathlib import Path

import pytest

from opensak.db.database import get_session, init_db
from opensak.db.models import Cache
from opensak.importer import import_gpx


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def fresh_db(tmp_path):
    db_path = tmp_path / "county.db"
    init_db(db_path=db_path)
    return db_path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_gpx(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "test.gpx"
    p.write_text(content, encoding="utf-8")
    return p


def _gpx(gsak_extension: str, groundspeak_county: str = "") -> str:
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="utf-8"?>
        <gpx xmlns="http://www.topografix.com/GPX/1/0"
             xmlns:groundspeak="http://www.groundspeak.com/cache/1/0/1"
             xmlns:gsak="http://www.gsak.net/xmlv1/6"
             version="1.0" creator="GSAK">
          <wpt lat="55.0000" lon="10.0000">
            <time>2024-01-01T00:00:00</time>
            <n>GCTEST1</n>
            <desc>Test Cache by Owner, Traditional Cache (2/2)</desc>
            <type>Geocache|Traditional Cache</type>
            <groundspeak:cache id="1" archived="False" available="True">
              <groundspeak:name>Test Cache</groundspeak:name>
              <groundspeak:placed_by>Owner</groundspeak:placed_by>
              <groundspeak:owner id="1">Owner</groundspeak:owner>
              <groundspeak:type>Traditional Cache</groundspeak:type>
              <groundspeak:container>Small</groundspeak:container>
              <groundspeak:difficulty>2.0</groundspeak:difficulty>
              <groundspeak:terrain>2.0</groundspeak:terrain>
              <groundspeak:country>Denmark</groundspeak:country>
              <groundspeak:state>Zealand</groundspeak:state>
              {groundspeak_county}
              <groundspeak:encoded_hints>Under a rock.</groundspeak:encoded_hints>
              <groundspeak:logs></groundspeak:logs>
            </groundspeak:cache>
            {gsak_extension}
          </wpt>
        </gpx>
    """)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_gsak_county_imported(tmp_path, fresh_db):
    # <gsak:County> is parsed and stored on Cache.county (issue #521 — real
    # example from a GSAK "Include GSAK fields" export, confirmed against
    # sommerhus.gpx).
    gpx = _gpx("""
        <gsak:wptExtension>
          <gsak:County>Vestsjaelland</gsak:County>
        </gsak:wptExtension>
    """)
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1
    assert result.errors == []

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        assert cache.country == "Denmark"
        assert cache.state == "Zealand"
        assert cache.county == "Vestsjaelland"


def test_gsak_county_empty_stays_none(tmp_path, fresh_db):
    # An empty <gsak:County/> (GSAK db had no county filled in) must not
    # produce an empty-string county — it should stay None.
    gpx = _gpx("""
        <gsak:wptExtension>
          <gsak:County></gsak:County>
        </gsak:wptExtension>
    """)
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        assert cache.county is None


def test_no_wptextension_county_stays_none(tmp_path, fresh_db):
    # A plain (non-GSAK) GPX has no wptExtension at all — must not crash,
    # county stays None since the official Groundspeak schema never has it.
    gpx = _gpx("")
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        assert cache.county is None


def test_groundspeak_county_takes_precedence_over_gsak(tmp_path, fresh_db):
    # If a county ever does show up on groundspeak:cache (non-standard, but
    # some third-party tools might add it), it should win over gsak:County
    # rather than being silently overwritten.
    gpx = _gpx(
        gsak_extension="""
            <gsak:wptExtension>
              <gsak:County>GSAK County</gsak:County>
            </gsak:wptExtension>
        """,
        groundspeak_county="<groundspeak:county>Groundspeak County</groundspeak:county>",
    )
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        assert cache.county == "Groundspeak County"


def test_gsak_county_and_user_note_coexist(tmp_path, fresh_db):
    # County parsing must not interfere with other gsak:wptExtension fields
    # parsed from the same block.
    gpx = _gpx("""
        <gsak:wptExtension>
          <gsak:County>Vestsjaelland</gsak:County>
          <gsak:UserNote>Solved: N55 12.345 E010 23.456</gsak:UserNote>
        </gsak:wptExtension>
    """)
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        assert cache.county == "Vestsjaelland"
        assert cache.user_note is not None
        assert cache.user_note.note == "Solved: N55 12.345 E010 23.456"

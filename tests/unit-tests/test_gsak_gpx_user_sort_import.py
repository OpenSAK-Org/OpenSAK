# tests/unit-tests/test_gsak_gpx_user_sort_import.py — issue #830 (GPX path).
#
# GSAK does not allow a UserSort value of 0 — when the field is blank in
# GSAK's UI, a GSAK-generated GPX export (with "Include GSAK fields"
# checked) still writes <gsak:UserSort>0</gsak:UserSort>, not an empty or
# missing element. The direct-DB import path had the same bug (see
# test_gsak_importer.py::test_user_sort_zero_maps_to_none); this file covers
# the equivalent GPX gsak:wptExtension parsing path.

import textwrap
from pathlib import Path

import pytest

from opensak.db.database import get_session, init_db
from opensak.db.models import Cache
from opensak.importer import import_gpx


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def fresh_db(tmp_path):
    db_path = tmp_path / "user_sort.db"
    init_db(db_path=db_path)
    return db_path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_gpx(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "test.gpx"
    p.write_text(content, encoding="utf-8")
    return p


def _gpx(gsak_extension: str) -> str:
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
              <groundspeak:logs></groundspeak:logs>
            </groundspeak:cache>
            {gsak_extension}
          </wpt>
        </gpx>
    """)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_gsak_gpx_user_sort_zero_maps_to_none(tmp_path, fresh_db):
    # <gsak:UserSort>0</gsak:UserSort> is how GSAK exports a blank UserSort
    # field via GPX — it must come through as None, not the literal 0.
    gpx = _gpx("""
        <gsak:wptExtension>
          <gsak:UserSort>0</gsak:UserSort>
        </gsak:wptExtension>
    """)
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1
    assert result.errors == []

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        assert cache.user_sort is None


def test_gsak_gpx_user_sort_nonzero_value_preserved(tmp_path, fresh_db):
    # A real, non-zero UserSort value must still come through unchanged.
    gpx = _gpx("""
        <gsak:wptExtension>
          <gsak:UserSort>7</gsak:UserSort>
        </gsak:wptExtension>
    """)
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1
    assert result.errors == []

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        assert cache.user_sort == 7


def test_gsak_gpx_user_sort_missing_stays_none(tmp_path, fresh_db):
    # No gsak:UserSort element at all (e.g. plain Geocaching.com GPX) must
    # not regress — user_sort stays None.
    gpx = _gpx("""
        <gsak:wptExtension>
        </gsak:wptExtension>
    """)
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1
    assert result.errors == []

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        assert cache.user_sort is None

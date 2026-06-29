# tests/unit-tests/test_gsak_user_note.py — GSAK UserNote import (issue #389).

import textwrap
from pathlib import Path

import pytest

from opensak.db.database import get_session, init_db
from opensak.db.models import Cache
from opensak.importer import import_gpx


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def fresh_db(tmp_path):
    db_path = tmp_path / "notes.db"
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
              <groundspeak:country>Denmark</groundspeak:country>
              <groundspeak:state>Zealand</groundspeak:state>
              <groundspeak:encoded_hints>Under a rock.</groundspeak:encoded_hints>
              <groundspeak:logs></groundspeak:logs>
            </groundspeak:cache>
            {gsak_extension}
          </wpt>
        </gpx>
    """)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_gsak_user_note_imported(tmp_path, fresh_db):
    # <gsak:UserNote> is parsed and stored in UserNote.note.
    gpx = _gpx("""
        <gsak:wptExtension>
          <gsak:UserNote>Found near the big oak. Hint: look low.</gsak:UserNote>
        </gsak:wptExtension>
    """)
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        assert cache.user_note is not None
        assert cache.user_note.note == "Found near the big oak. Hint: look low."


def test_gsak_user_note_empty_not_stored(tmp_path, fresh_db):
    # An empty or whitespace-only <gsak:UserNote> must not create a UserNote row.
    gpx = _gpx("""
        <gsak:wptExtension>
          <gsak:UserNote>   </gsak:UserNote>
        </gsak:wptExtension>
    """)
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        if cache.user_note is not None:
            assert not cache.user_note.note


def test_gsak_no_user_note_no_row(tmp_path, fresh_db):
    # No wptExtension at all — UserNote row must not be created.
    gpx = _gpx("")
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        if cache.user_note is not None:
            assert not cache.user_note.note


def test_gsak_user_note_not_overwritten_on_reimport(tmp_path, fresh_db):
    # Re-importing with a different note must NOT overwrite an existing note.
    gpx_first = _gpx("""
        <gsak:wptExtension>
          <gsak:UserNote>Original note.</gsak:UserNote>
        </gsak:wptExtension>
    """)
    import_gpx(_write_gpx(tmp_path, gpx_first), fresh_db)

    gpx_second = _gpx("""
        <gsak:wptExtension>
          <gsak:UserNote>New conflicting note.</gsak:UserNote>
        </gsak:wptExtension>
    """)
    (tmp_path / "test.gpx").write_text(gpx_second, encoding="utf-8")
    import_gpx(tmp_path / "test.gpx", fresh_db)

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        assert cache.user_note is not None
        assert cache.user_note.note == "Original note."


def test_gsak_user_note_plain_reimport_preserves_note(tmp_path, fresh_db):
    # Re-importing without a UserNote (e.g. plain geocaching.com GPX) must not
    # wipe an existing note.
    gpx_with = _gpx("""
        <gsak:wptExtension>
          <gsak:UserNote>Keep this note.</gsak:UserNote>
        </gsak:wptExtension>
    """)
    import_gpx(_write_gpx(tmp_path, gpx_with), fresh_db)

    gpx_without = _gpx("")
    (tmp_path / "test.gpx").write_text(gpx_without, encoding="utf-8")
    import_gpx(tmp_path / "test.gpx", fresh_db)

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        assert cache.user_note is not None
        assert cache.user_note.note == "Keep this note."


def test_gsak_user_note_and_corrected_coords_together(tmp_path, fresh_db):
    # UserNote and corrected coordinates coexist on the same UserNote row.
    gpx = _gpx("""
        <gsak:wptExtension>
          <gsak:UserNote>Solved: N55 12.345 E010 23.456</gsak:UserNote>
          <gsak:LatN>55.2057</gsak:LatN>
          <gsak:LongE>10.3910</gsak:LongE>
        </gsak:wptExtension>
    """)
    result = import_gpx(_write_gpx(tmp_path, gpx), fresh_db)
    assert result.total == 1

    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCTEST1").one()
        assert cache.user_note is not None
        assert cache.user_note.note == "Solved: N55 12.345 E010 23.456"
        assert cache.user_note.is_corrected is True
        assert abs(cache.user_note.corrected_lat - 55.2057) < 1e-6
        assert abs(cache.user_note.corrected_lon - 10.3910) < 1e-6

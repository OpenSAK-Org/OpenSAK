# tests/unit-tests/test_export_import_roundtrip.py — exported GPX must re-import.
# Regression: exports wrap groundspeak:cache in <extensions> (GPX 1.1); the importer
# once read it only as a direct <wpt> child, so re-import gave 0 caches.

from types import SimpleNamespace

from opensak.db.database import init_db, get_session, reload_caches_full
from opensak.db.models import Cache, UserNote
from opensak.importer import import_gpx
from opensak.gps.garmin import generate_gpx
from tests.data import SAMPLE_GPX, write_gpx


# Minimal GPX 1.1 with the groundspeak block nested under <extensions> — the
# exact shape that used to import as 0 caches.
GPX_1_1 = """<?xml version="1.0" encoding="utf-8"?>
<gpx version="1.1" creator="OpenSAK"
     xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:groundspeak="http://www.groundspeak.com/cache/1/0/1">
  <wpt lat="55.0" lon="12.0">
    <name>GC11ABC</name>
    <desc>Eleven One</desc>
    <type>Geocache|Traditional Cache</type>
    <extensions>
      <groundspeak:cache id="1" available="True" archived="False">
        <groundspeak:name>Eleven One</groundspeak:name>
        <groundspeak:type>Traditional Cache</groundspeak:type>
        <groundspeak:container>Small</groundspeak:container>
        <groundspeak:difficulty>1.5</groundspeak:difficulty>
        <groundspeak:terrain>2.0</groundspeak:terrain>
        <groundspeak:encoded_hints>Look up.</groundspeak:encoded_hints>
      </groundspeak:cache>
    </extensions>
  </wpt>
</gpx>
"""


def test_importer_accepts_gpx_1_1_with_extensions(tmp_path):
    init_db(db_path=tmp_path / "v11.db")
    f = write_gpx(tmp_path, "v11.gpx", GPX_1_1)
    with get_session() as s:
        result = import_gpx(f, s)

    assert result.created == 1
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC11ABC").one()
        assert cache.cache_type == "Traditional Cache"
        assert cache.encoded_hints == "Look up."


def test_exported_gpx_reimports_with_full_data(tmp_path):
    # Seed a source DB and pull full cache objects, as the export menu would.
    init_db(db_path=tmp_path / "src.db")
    write_gpx(tmp_path, "src.gpx", SAMPLE_GPX)
    with get_session() as s:
        import_gpx(tmp_path / "src.gpx", s)
    with get_session() as s:
        caches = reload_caches_full(s.query(Cache).all())

    exported = generate_gpx(caches, "roundtrip")
    assert "GPX/1/1" in exported  # we export 1.1 with an <extensions> wrapper

    # Re-import into a fresh DB — used to yield 0 caches.
    out = tmp_path / "out.gpx"
    out.write_text(exported, encoding="utf-8")
    init_db(db_path=tmp_path / "dst.db")
    with get_session() as s:
        result = import_gpx(out, s)

    assert result.created == 2
    assert result.skipped == 0
    with get_session() as s:
        rows = {c.gc_code: c for c in s.query(Cache).all()}
        assert set(rows) == {"GC12345", "GC99999"}
        assert rows["GC12345"].cache_type == "Traditional Cache"
        assert rows["GC12345"].encoded_hints == "Under a rock."
        assert len(rows["GC12345"].logs) == 2


def test_personal_note_survives_gpx_roundtrip(tmp_path):
    # Build a minimal cache stub with a personal note attached.
    note = SimpleNamespace(is_corrected=False, corrected_lat=None, corrected_lon=None, note="My field note")
    cache = SimpleNamespace(
        id=1, gc_code="GCNOTE1", name="Note Cache", cache_type="Traditional Cache",
        latitude=55.0, longitude=12.0, difficulty=1.5, terrain=2.0,
        placed_by="Owner", available=True, archived=False, country="Denmark",
        encoded_hints=None, hidden_date=None, logs=[], user_note=note, container="Regular",
    )

    exported = generate_gpx([cache], "note_roundtrip")
    assert "gsak:UserNote" in exported
    assert "My field note" in exported

    out = tmp_path / "note_roundtrip.gpx"
    out.write_text(exported, encoding="utf-8")

    init_db(db_path=tmp_path / "dst.db")
    with get_session() as s:
        result = import_gpx(out, s)

    assert result.created == 1
    with get_session() as s:
        c = s.query(Cache).filter_by(gc_code="GCNOTE1").one()
        assert c.user_note is not None
        assert c.user_note.note == "My field note"

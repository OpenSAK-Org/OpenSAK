# tests/unit-tests/test_gpx_gsak_fields_1169.py — #1169: the GPX/GGZ export
# writes GSAK's full gsak:wptExtension (option "Include GSAK fields", on by
# default): user flag, lock, DNF, watch, user data, DNF date, the original
# coordinates of a corrected cache, county, user sort, GC note, GUID, custom
# fields (CustomData) and the child waypoints' Parent/Child_* fields, in
# GSAK's order. The GPX importer reads them back, custom fields by name, and
# tells GSAK's files from OpenSAK's own by <gpx creator> (#766).

import io
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from opensak.db import companion, custom_fields
from opensak.db.database import dispose_engine, get_session, init_db, reload_caches_full
from opensak.db.db_settings import ensure_seeded
from opensak.db.models import Cache, Log, UserNote, Waypoint
from opensak.export.file_export import write_export_file
from opensak.export.file_export_settings import FileExportSettings
from opensak.export.gpx_content import DEFAULT_CONTENT, GpxContent
from opensak.gps.garmin import custom_data_text, generate_ggz, generate_gpx
from opensak.importer import import_gpx, parse_custom_data
from opensak.lang import load_language
from opensak.utils import flags

GPX10 = "{http://www.topografix.com/GPX/1/0}"
GPX11 = "{http://www.topografix.com/GPX/1/1}"
GSAK = "{http://www.gsak.net/xmlv1/6}"

# The order of GSAK 9's own export (checked against its GPX files), without
# StateBeforeCorrect/CountyBeforeCorrect, which OpenSAK doesn't store.
GSAK_ORDER = [
    "UserFlag", "Lock", "DNF", "Watch", "UserData", "UserFound", "DNFDate",
    "LatBeforeCorrect", "LonBeforeCorrect", "FirstToFind", "User2", "User3", "User4",
    "County", "UserSort", "SmartName", "LastGpxDate", "Code", "Resolution", "IsPremium",
    "FavPoints", "GcNote", "Guid", "CacheImages", "LogImages", "CustomData",
]
GSAK_CHILD_ORDER = ["Parent", "Child_ByGSAK", "Child_Flag", "SmartName", "LastGpxDate", "Code"]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    load_language("en")
    monkeypatch.setattr(flags, "companion_db", True)
    companion.reset()
    custom_fields.changed()
    yield
    dispose_engine()
    companion.reset()


# ── helpers ───────────────────────────────────────────────────────────────────

def _child(**kw) -> SimpleNamespace:
    values = dict(
        prefix="PK", wp_type="Parking Area", name="Parking", description=None, comment=None,
        latitude=47.1, longitude=8.5, url=None, wp_date=None, wp_code=None, wp_flag=False,
        created_by_user=False,
    )
    values.update(kw)
    return SimpleNamespace(**values)


def _cache(**kw) -> SimpleNamespace:
    values = dict(
        id=1, gc_code="GCBWND7", gc_cache_id=10572559, name="Early Birds",
        cache_type="Event Cache", latitude=47.19, longitude=8.52,
        difficulty=1.0, terrain=1.5, placed_by="Valentin", owner_name="Valentin",
        owner_id=6179840, available=True, archived=False, country="Switzerland",
        state="Zug", encoded_hints=None, hidden_date=None, logs=[], user_note=None,
        container="Other", short_description=None, short_desc_html=False,
        long_description="Long text", long_desc_html=True, attributes=[],
        waypoints=[], trackables=[], found=False, elevation=None,
        user_flag=False, locked=False, dnf=False, dnf_date=None, watch=False,
        user_data_1=None, user_data_2=None, user_data_3=None, user_data_4=None,
        first_to_find=False, county=None, user_sort=None, last_gpx_update=None,
        premium_only=False, favorite_points=None, gc_note=None, guid=None,
    )
    values.update(kw)
    return SimpleNamespace(**values)


def _root(gpx: str) -> ET.Element:
    return ET.fromstring(gpx.split("\n", 1)[1])


def _ext(wpt: ET.Element) -> ET.Element:
    ext = wpt.find(f".//{GSAK}wptExtension")
    assert ext is not None
    return ext


def _local(el: ET.Element) -> list[str]:
    return [c.tag.split("}")[1] for c in el]


def _wpts(gpx: str, ns: str = GPX10) -> list[ET.Element]:
    return _root(gpx).findall(f"{ns}wpt")


def _full_cache() -> SimpleNamespace:
    note = SimpleNamespace(note="Solved", is_corrected=True, corrected_lat=47.2,
                           corrected_lon=8.6, updated_at=None)
    return _cache(
        user_note=note, found=True, found_date=datetime(2026, 6, 4), user_flag=True,
        locked=True, dnf=True, dnf_date=datetime(2025, 5, 5), watch=True,
        user_data_1="one", user_data_2="two\nlines", user_data_3="three", user_data_4="four",
        first_to_find=True, county="Zug", user_sort=7,
        last_gpx_update=datetime(2026, 11, 1, 14, 30), premium_only=True,
        favorite_points=20, gc_note="GC note", guid="7d1b5d0e-0f5e-4c1e-9d2b-5d2c3e4f5a6b",
        waypoints=[_child(prefix="P0", wp_code="P0BWND7", wp_flag=True, created_by_user=True)],
    )


# ── writer ────────────────────────────────────────────────────────────────────

class TestWriter:
    def test_full_field_set_in_gsak_order(self):
        cache_wpt, child_wpt = _wpts(generate_gpx([_full_cache()]))
        ext = _ext(cache_wpt)
        assert _local(ext) == GSAK_ORDER + ["UserNote"]
        values = {tag: ext.findtext(f"{GSAK}{tag}") for tag in GSAK_ORDER}
        assert values == {
            "UserFlag": "true", "Lock": "true", "DNF": "true", "Watch": "true",
            "UserData": "one", "UserFound": "2026-06-04", "DNFDate": "2025-05-05",
            "LatBeforeCorrect": "47.190000", "LonBeforeCorrect": "8.520000",
            "FirstToFind": "true", "User2": "two\nlines", "User3": "three", "User4": "four",
            "County": "Zug", "UserSort": "7", "SmartName": "", "LastGpxDate": "2026-11-01",
            "Code": "GCBWND7", "Resolution": "", "IsPremium": "true", "FavPoints": "20",
            "GcNote": "GC note", "Guid": "7d1b5d0e-0f5e-4c1e-9d2b-5d2c3e4f5a6b",
            "CacheImages": "", "LogImages": "",
            "CustomData": "\nCustom_Start:cCode:Custom_Data:GCBWND7:Custom_End\n\n",
        }
        # The <wpt> keeps the corrected position, as in GSAK.
        assert (cache_wpt.get("lat"), cache_wpt.get("lon")) == ("47.200000", "8.600000")
        child = _ext(child_wpt)
        assert _local(child) == GSAK_CHILD_ORDER
        assert [child.findtext(f"{GSAK}{t}") for t in GSAK_CHILD_ORDER] == [
            "GCBWND7", "true", "true", "", "2026-11-01", "P0BWND7"]

    def test_empty_values_as_gsak_writes_them(self):
        ext = _ext(_wpts(generate_gpx([_cache()]))[0])
        # No UserFound, DNFDate, LastGpxDate or original coordinates without a value.
        assert _local(ext) == [t for t in GSAK_ORDER if t not in (
            "UserFound", "DNFDate", "LatBeforeCorrect", "LonBeforeCorrect", "LastGpxDate")]
        assert ext.findtext(f"{GSAK}UserFlag") == "false"
        assert ext.findtext(f"{GSAK}UserSort") == "0"
        assert ext.findtext(f"{GSAK}FavPoints") == "0"
        assert ext.findtext(f"{GSAK}County") == ""

    def test_original_coordinates_only_when_the_wpt_is_corrected(self):
        # Exporting the original coordinates: the <wpt> is the original
        # position, so LatBeforeCorrect would make an import take it for
        # the corrected one.
        gpx = generate_gpx([_full_cache()], use_corrected=False)
        ext = _ext(_wpts(gpx)[0])
        assert ext.find(f"{GSAK}LatBeforeCorrect") is None
        assert ext.find(f"{GSAK}LonBeforeCorrect") is None

    def test_option_off_writes_only_user_found_and_note(self):
        gpx = generate_gpx([_full_cache()], content=GpxContent(gsak_fields=False))
        cache_wpt, child_wpt = _wpts(gpx)
        assert _local(_ext(cache_wpt)) == ["UserFound", "UserNote"]
        assert child_wpt.find(f"{GSAK}wptExtension") is None

    def test_gpx_1_1_puts_the_fields_into_extensions(self):
        gpx = generate_gpx([_full_cache()], content=GpxContent(version="1.1"))
        cache_wpt, child_wpt = _wpts(gpx, GPX11)
        assert _local(cache_wpt)[-1] == "extensions"
        assert _local(cache_wpt.find(f"{GPX11}extensions"))[0] == "wptExtension"
        assert _local(_ext(cache_wpt)) == GSAK_ORDER + ["UserNote"]
        assert _local(child_wpt.find(f"{GPX11}extensions")) == ["wptExtension"]

    def test_ggz_index_still_points_at_each_cache_wpt(self):
        caches = [_full_cache(), _cache(gc_code="GC2ABCD", id=2, latitude=47.5, longitude=8.1)]
        with zipfile.ZipFile(io.BytesIO(generate_ggz(caches, filename="g"))) as zf:
            gpx = zf.read("data/g.gpx")
            index = zf.read("index/com/garmin/geocaches/v0/index.xml").decode("utf-8")
        positions = [int(p.split("<")[0]) for p in index.split("<file_pos>")[1:]]
        lengths = [int(p.split("<")[0]) for p in index.split("<file_len>")[1:]]
        for code, pos, length in zip(("GCBWND7", "GC2ABCD"), positions, lengths):
            piece = gpx[pos:pos + length].decode("utf-8")
            assert piece.startswith("<wpt ") and piece.endswith("</wpt>")
            assert f"<name>{code}</name>" in piece
            assert "<gsak:CustomData>" in piece


# ── CustomData text ───────────────────────────────────────────────────────────

class TestCustomData:
    def test_text_as_gsak_writes_it(self):
        text = custom_data_text("GC1", [("Swissgrid", None), ("MyTour", 0), ("Apprec", 0.0),
                                        ("GCFinds", -1), ("EvStart", "05:45"), ("Flag", True)])
        assert text == (
            "\nCustom_Start:cCode:Custom_Data:GC1:Custom_End"
            "\nCustom_Start:Swissgrid:Custom_Data::Custom_End"
            "\nCustom_Start:MyTour:Custom_Data:0:Custom_End"
            "\nCustom_Start:Apprec:Custom_Data:0.0:Custom_End"
            "\nCustom_Start:GCFinds:Custom_Data:-1:Custom_End"
            "\nCustom_Start:EvStart:Custom_Data:05:45:Custom_End"
            "\nCustom_Start:Flag:Custom_Data:1:Custom_End"
            "\n\n"
        )

    def test_parse_reads_what_gsak_writes(self):
        # As in GSAK's own files, incl. the indentation before the end tag.
        text = (
            "\nCustom_Start:cCode:Custom_Data:GCBQT2B:Custom_End"
            "\nCustom_Start:Swissgrid:Custom_Data::Custom_End"
            "\nCustom_Start:EvStart:Custom_Data:05:45:Custom_End"
            "\nCustom_Start:Notes:Custom_Data:two\r\nlines: yes:Custom_End"
            "\nCustom_Start:Apprec:Custom_Data:0.0:Custom_End"
            "\n\n      "
        )
        assert parse_custom_data(text) == {
            "Swissgrid": "", "EvStart": "05:45", "Notes": "two\nlines: yes", "Apprec": "0.0",
        }

    def test_parse_round_trip(self):
        values = [("A", "x:y"), ("B", 3), ("C", None)]
        assert parse_custom_data(custom_data_text("GC1", values)) == {"A": "x:y", "B": "3", "C": ""}

    @pytest.mark.parametrize("text", [None, "", "garbage", "Custom_Start:A:Custom_Data:1"])
    def test_parse_nothing_usable(self, text):
        assert parse_custom_data(text) == {}


# ── database round trip ───────────────────────────────────────────────────────

FIELDS = (("Swissgrid", "string", None), ("MyTour", "boolean", "0"),
          ("GCFinds", "integer", "-1"), ("Apprec", "real", "0.0"), ("EvStart", "string", None))


def _database(path: Path, fields=FIELDS) -> Path:
    init_db(db_path=path)
    ensure_seeded(path, path.stem)
    for name, type_, default in fields:
        custom_fields.define_field(path, name, type_, default=default)
    return path


@pytest.fixture
def src_db(tmp_path):
    """A corrected, locked cache with every GSAK field set, custom values
    and a user-made, flagged child; and a plain cache with defaults."""
    db = _database(tmp_path / "src.db")
    with get_session() as s:
        one = Cache(
            gc_code="GCBQT2B", name="Ruhebank", cache_type="Unknown Cache",
            latitude=47.228633, longitude=8.609617, placed_by="brenzo",
            user_flag=True, locked=True, dnf=True, dnf_date=datetime(2025, 5, 5),
            watch=True, user_data_1="one", user_data_2="Horgen\nRichtig!",
            user_data_3="three", user_data_4="four", first_to_find=True, county="Zürich",
            user_sort=7, premium_only=True, favorite_points=20, gc_note="GC note",
            guid="7d1b5d0e-0f5e-4c1e-9d2b-5d2c3e4f5a6b",
        )
        one.user_note = UserNote(note="Final at the bench", is_corrected=True,
                                 corrected_lat=47.23, corrected_lon=8.61)
        one.waypoints.append(Waypoint(prefix="FL", wp_code="FLBQT2B", wp_type="Final Location",
                                      name="Final", latitude=47.23, longitude=8.61,
                                      created_by_user=True, wp_flag=True))
        two = Cache(gc_code="GC2BBBB", name="Two", cache_type="Traditional Cache",
                    latitude=47.1, longitude=8.1, placed_by="Owner")
        s.add_all([one, two])
        s.commit()
    custom_fields.write_values(db, [
        ("GCBQT2B", {"Swissgrid": "2'688'000", "MyTour": 1, "GCFinds": 150,
                     "Apprec": 95.5, "EvStart": "05:45"}),
    ])
    with get_session() as s:
        caches = s.query(Cache).order_by(Cache.gc_code).all()
        s.expunge_all()
    return caches


def _import(path: Path, db: Path):
    with get_session() as s:
        return import_gpx(path, s)


@pytest.mark.parametrize("version", ["1.0", "1.1"])
def test_round_trip_keeps_every_field(src_db, tmp_path, version):
    out = tmp_path / "out.gpx"
    write_export_file(src_db, out, "gpx", content=GpxContent(version=version))
    text = out.read_text(encoding="utf-8")
    assert "Custom_Start:EvStart:Custom_Data:05:45:Custom_End" in text
    # The default of an untouched cache, as GSAK writes it.
    assert "Custom_Start:GCFinds:Custom_Data:-1:Custom_End" in text

    dst = _database(tmp_path / "dst.db")
    result = _import(out, dst)
    assert result.created == 2 and not result.errors and not result.warnings
    with get_session() as s:
        one = s.query(Cache).filter_by(gc_code="GCBQT2B").one()
        assert (one.user_flag, one.locked, one.dnf, one.watch) == (True, True, True, True)
        assert one.dnf_date.date() == datetime(2025, 5, 5).date()
        assert (one.user_data_1, one.user_data_2, one.user_data_3, one.user_data_4) == (
            "one", "Horgen\nRichtig!", "three", "four")
        assert (one.first_to_find, one.county, one.user_sort) == (True, "Zürich", 7)
        assert (one.premium_only, one.favorite_points) == (True, 20)
        assert (one.gc_note, one.guid) == ("GC note", "7d1b5d0e-0f5e-4c1e-9d2b-5d2c3e4f5a6b")
        # Original and corrected coordinates each back in their place.
        assert (one.latitude, one.longitude) == (47.228633, 8.609617)
        assert one.user_note.is_corrected
        assert (one.user_note.corrected_lat, one.user_note.corrected_lon) == (47.23, 8.61)
        assert one.user_note.note == "Final at the bench"
        # Linked to its cache by <gsak:Parent>: the prefix rules alone would
        # split "FLBQT2B" as F + LBQT2B and miss GCBQT2B.
        [child] = one.waypoints
        assert (child.prefix, child.wp_type) == ("FL", "Final Location")
        assert (child.created_by_user, child.wp_flag) == (True, True)
        two = s.query(Cache).filter_by(gc_code="GC2BBBB").one()
        assert (two.user_flag, two.locked, two.user_sort, two.gc_note) == (False, False, None, None)
    values = custom_fields.read_values(dst, ["GCBQT2B", "GC2BBBB"])
    assert values["GCBQT2B"] == {"Swissgrid": "2'688'000", "MyTour": 1, "GCFinds": 150,
                                 "Apprec": 95.5, "EvStart": "05:45"}
    assert values["GC2BBBB"] == {"Swissgrid": "", "MyTour": 0, "GCFinds": -1,
                                 "Apprec": 0.0, "EvStart": ""}


def test_custom_fields_the_database_lacks_are_skipped_and_named(src_db, tmp_path):
    out = tmp_path / "out.gpx"
    write_export_file(src_db, out, "gpx")
    dst = _database(tmp_path / "dst.db", fields=(("MyTour", "boolean", "0"),))
    result = _import(out, dst)
    assert result.created == 2 and not result.errors
    assert result.warnings == [
        "4 custom field(s) in the file are not in this database and were skipped: "
        "Apprec, EvStart, GCFinds, Swissgrid"
    ]
    assert custom_fields.read_values(dst, ["GCBQT2B"])["GCBQT2B"] == {"MyTour": 1}
    assert [f.name for f in custom_fields.fields(dst)] == ["MyTour"]   # none created


def test_custom_values_are_converted_to_the_target_type(src_db, tmp_path):
    out = tmp_path / "out.gpx"
    write_export_file(src_db, out, "gpx")
    # GCFinds is text here: the number is stored as text.
    dst = _database(tmp_path / "dst.db", fields=(("GCFinds", "string", None),))
    _import(out, dst)
    assert custom_fields.read_values(dst, ["GCBQT2B"])["GCBQT2B"] == {"GCFinds": "150"}


def test_option_off_round_trip_keeps_only_found_date_and_note(src_db, tmp_path):
    out = tmp_path / "out.gpx"
    write_export_file(src_db, out, "gpx", content=GpxContent(gsak_fields=False))
    assert "CustomData" not in out.read_text(encoding="utf-8")
    dst = _database(tmp_path / "dst.db")
    _import(out, dst)
    with get_session() as s:
        one = s.query(Cache).filter_by(gc_code="GCBQT2B").one()
        assert (one.user_flag, one.locked, one.watch) == (False, False, False)
        assert one.user_note.note == "Final at the bench"
    assert custom_fields.read_values(dst, ["GCBQT2B"])["GCBQT2B"]["GCFinds"] == -1


def test_lock_only_ever_locks(src_db, tmp_path):
    # A cache locked here stays locked (and keeps its data) when a file
    # says it isn't locked.
    out = tmp_path / "out.gpx"
    write_export_file(src_db, out, "gpx")
    dst = _database(tmp_path / "dst.db")
    with get_session() as s:
        s.add(Cache(gc_code="GC2BBBB", name="Mine", cache_type="Traditional Cache",
                    latitude=47.0, longitude=8.0, locked=True))
        s.commit()
    _import(out, dst)
    with get_session() as s:
        two = s.query(Cache).filter_by(gc_code="GC2BBBB").one()
        assert (two.locked, two.name) == (True, "Mine")


def test_ftf_and_dnf_date_without_logs(tmp_path):
    # The flags come from the GSAK fields when the file has no logs to
    # derive them from.
    gpx = generate_gpx([_cache(first_to_find=True, dnf=True, dnf_date=datetime(2024, 3, 2))])
    out = tmp_path / "x.gpx"
    out.write_text(gpx, encoding="utf-8")
    _database(tmp_path / "dst.db", fields=())
    _import(out, tmp_path / "dst.db")
    with get_session() as s:
        cache = s.query(Cache).one()
        assert (cache.first_to_find, cache.dnf) == (True, True)
        assert cache.dnf_date.date() == datetime(2024, 3, 2).date()


# ── GSAK's files vs OpenSAK's own (#766) ──────────────────────────────────────

@pytest.fixture
def account():
    from opensak.gui.settings import get_settings
    settings = get_settings()
    old = settings.gc_username, settings.gc_finder_id
    settings.gc_username, settings.gc_finder_id = "Me", "999"
    yield
    settings.gc_username, settings.gc_finder_id = old


def _stale_found_cache() -> SimpleNamespace:
    """Not found, but with an old found log of the user's (unmarked by hand)."""
    log = SimpleNamespace(log_id="1", log_type="Found it", finder="Me", finder_id="999",
                          text="TFTC", log_date=datetime(2020, 1, 1, tzinfo=timezone.utc))
    return _cache(cache_type="Traditional Cache", logs=[log], user_flag=True)


def test_opensak_file_with_gsak_fields_keeps_its_rules(account, tmp_path):
    # OpenSAK's own export now has UserFlag & co.: its <sym> still decides,
    # so the stale log doesn't mark the cache found again.
    out = tmp_path / "opensak.gpx"
    out.write_text(generate_gpx([_stale_found_cache()]), encoding="utf-8")
    assert 'creator="OpenSAK"' in out.read_text(encoding="utf-8")
    _database(tmp_path / "dst.db", fields=())
    _import(out, tmp_path / "dst.db")
    with get_session() as s:
        assert s.query(Cache).one().found is False


def test_gsak_file_gets_gsak_rules_by_its_creator(account, tmp_path):
    # GSAK's <sym> doesn't say whether a cache is found: the user's found
    # log does — also when the file has none of GSAK's fields.
    gpx = generate_gpx([_stale_found_cache()], content=GpxContent(gsak_fields=False))
    out = tmp_path / "gsak.gpx"
    out.write_text(gpx.replace('creator="OpenSAK"', 'creator="GSAK"'), encoding="utf-8")
    _database(tmp_path / "dst.db", fields=())
    _import(out, tmp_path / "dst.db")
    with get_session() as s:
        assert s.query(Cache).one().found is True


def test_file_without_creator_falls_back_to_the_fields(account, tmp_path):
    gpx = generate_gpx([_stale_found_cache()]).replace(' creator="OpenSAK"', "")
    out = tmp_path / "other.gpx"
    out.write_text(gpx, encoding="utf-8")
    _database(tmp_path / "dst.db", fields=())
    _import(out, tmp_path / "dst.db")
    with get_session() as s:
        assert s.query(Cache).one().found is True   # GSAK's fields → GSAK's rules


# ── option: settings, dialog, Send to GPS ─────────────────────────────────────

class TestOption:
    def test_default_is_on(self):
        assert DEFAULT_CONTENT.gsak_fields is True
        assert FileExportSettings().content().gsak_fields is True

    def test_settings_round_trip(self):
        settings = FileExportSettings(gsak_fields=False)
        assert FileExportSettings.from_dict(settings.to_dict()) == settings
        assert settings.content() == GpxContent(gsak_fields=False)

    @pytest.mark.parametrize("data", [{}, {"gsak_fields": "no"}, {"gsak_fields": 0}])
    def test_old_or_invalid_settings_load_with_the_default(self, data):
        assert FileExportSettings.from_dict(data).gsak_fields is True

    def test_dialog_round_trip(self, qtbot):
        from opensak.gui.dialogs.file_export_dialog import FileExportDialog

        dlg = FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        assert dlg._chk_gsak_fields.isChecked()
        dlg._apply_settings(FileExportSettings(gsak_fields=False))
        assert not dlg._chk_gsak_fields.isChecked()
        assert dlg._collect_settings().gsak_fields is False
        assert dlg._content().gsak_fields is False

    def test_gps_export_follows_the_setting(self, src_db, tmp_path):
        from opensak.gui.dialogs.gps_dialog import ExportWorker

        worker = ExportWorker(src_db, tmp_path, "gps", max_caches=0,
                              settings=FileExportSettings(gsak_fields=False))
        worker.run()
        gpx = (tmp_path / "gps.gpx").read_text(encoding="utf-8")
        assert "<gsak:UserNote>" in gpx and "<gsak:UserFlag>" not in gpx

    def test_reloaded_caches_are_exported_with_custom_values(self, src_db):
        gpx = generate_gpx(reload_caches_full(src_db))
        assert "Custom_Start:Swissgrid:Custom_Data:2'688'000:Custom_End" in gpx

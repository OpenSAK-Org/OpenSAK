# tests/unit-tests/test_corrected_final_wp_1303.py — #1303: the GPX/GGZ
# export can add a "Final Location" child waypoint at the corrected
# coordinates of each corrected cache (option corrected_final_wp, off by
# default), whatever the children choice says. Phone apps that refresh a
# cache online lose its corrected coordinates but keep its waypoints. No
# second final when a written child already is a Final Location there; the
# waypoint is counted for "max counts waypoints" and in <bounds>, and a
# re-import skips it. In the export settings, the file export dialog and the
# Lua macros (test_macro_runtime.py).

import io
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from opensak.db import companion, custom_fields
from opensak.db.database import (
    dispose_engine, export_bounds, export_child_counts, get_session, init_db,
)
from opensak.db.db_settings import ensure_seeded
from opensak.db.models import Cache, UserNote, Waypoint
from opensak.export.file_export import select_for_export, wpt_counts, write_export_file
from opensak.export.file_export_settings import FileExportSettings
from opensak.export.gpx_content import (
    CORRECTED_FINAL_WP_COMMENT, GpxContent, is_corrected_final_wp,
)
from opensak.gps.garmin import generate_ggz, generate_gpx, gpx_bounds
from opensak.importer import import_gpx
from opensak.lang import load_language
from opensak.utils import flags

GPX10 = "{http://www.topografix.com/GPX/1/0}"
GPX11 = "{http://www.topografix.com/GPX/1/1}"
GSAK = "{http://www.gsak.net/xmlv1/6}"
ON = GpxContent(corrected_final_wp=True)


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


def _note(lat=47.2, lon=8.6, corrected=True) -> SimpleNamespace:
    return SimpleNamespace(note="", is_corrected=corrected, corrected_lat=lat,
                           corrected_lon=lon, updated_at=None)


def _cache(**kw) -> SimpleNamespace:
    values = dict(
        id=1, gc_code="GCBWND7", gc_cache_id=10572559, name="Mystery",
        cache_type="Unknown Cache", latitude=47.19, longitude=8.52,
        difficulty=3.0, terrain=1.5, placed_by="Owner", owner_name="Owner",
        owner_id=1, available=True, archived=False, country="Switzerland",
        state="Zug", encoded_hints=None, hidden_date=None, logs=[], user_note=_note(),
        container="Small", short_description=None, short_desc_html=False,
        long_description="Text", long_desc_html=True, attributes=[],
        waypoints=[], trackables=[], found=False, elevation=None,
        user_flag=False, locked=False, dnf=False, dnf_date=None, watch=False,
        user_data_1=None, user_data_2=None, user_data_3=None, user_data_4=None,
        first_to_find=False, county=None, user_sort=None, last_gpx_update=None,
        premium_only=False, favorite_points=None, gc_note=None, guid=None,
    )
    values.update(kw)
    return SimpleNamespace(**values)


def _wpts(gpx: str, ns: str = GPX10) -> list[ET.Element]:
    return ET.fromstring(gpx.split("\n", 1)[1]).findall(f"{ns}wpt")


def _names(gpx: str, ns: str = GPX10) -> list[str]:
    return [w.findtext(f"{ns}name") or "" for w in _wpts(gpx, ns)]


# ── writer ────────────────────────────────────────────────────────────────────

class TestWriter:
    def test_final_at_the_corrected_coordinates(self):
        cache_wpt, final = _wpts(generate_gpx([_cache()], content=ON))
        assert (final.get("lat"), final.get("lon")) == ("47.200000", "8.600000")
        assert final.findtext(f"{GPX10}name") == "FLBWND7"
        assert final.findtext(f"{GPX10}desc") == "Final Location"
        assert final.findtext(f"{GPX10}cmt") == CORRECTED_FINAL_WP_COMMENT
        assert final.findtext(f"{GPX10}type") == "Waypoint|Final Location"
        # GSAK's child fields: made by the user, under its cache.
        ext = final.find(f"{GSAK}wptExtension")
        assert ext.findtext(f"{GSAK}Parent") == "GCBWND7"
        assert ext.findtext(f"{GSAK}Child_ByGSAK") == "true"
        assert ext.findtext(f"{GSAK}Code") == "FLBWND7"

    def test_off_by_default(self):
        assert GpxContent().corrected_final_wp is False
        assert FileExportSettings().corrected_final_wp is False
        assert _names(generate_gpx([_cache()])) == ["GCBWND7"]

    @pytest.mark.parametrize("note", [None, _note(corrected=False), _note(lat=None)])
    def test_only_for_corrected_caches(self, note):
        assert _names(generate_gpx([_cache(user_note=note)], content=ON)) == ["GCBWND7"]

    def test_written_after_the_other_children(self):
        cache = _cache(waypoints=[_child(prefix="PK", wp_code="PKBWND7")])
        assert _names(generate_gpx([cache], content=ON)) == ["GCBWND7", "PKBWND7", "FLBWND7"]

    def test_written_whatever_the_children_choice(self):
        cache = _cache(waypoints=[_child(prefix="PK", wp_code="PKBWND7")])
        gpx = generate_gpx([cache], content=GpxContent(children="none", corrected_final_wp=True))
        assert _names(gpx) == ["GCBWND7", "FLBWND7"]
        assert "(HasChildren)" in gpx

    def test_children_only(self):
        content = GpxContent(children="none", children_only=True, corrected_final_wp=True)
        assert _names(generate_gpx([_cache()], content=content)) == ["FLBWND7"]

    def test_no_second_final_where_one_is(self):
        # A Final Location the user added at the corrected coordinates.
        own = _child(prefix="FN", wp_code="FNBWND7", wp_type="Final Location",
                     latitude=47.2000001, longitude=8.6)
        assert _names(generate_gpx([_cache(waypoints=[own])], content=ON)) == [
            "GCBWND7", "FNBWND7"]

    def test_final_elsewhere_or_not_written_doesnt_count(self):
        elsewhere = _child(prefix="FN", wp_code="FNBWND7", wp_type="Final Location",
                           latitude=47.3, longitude=8.6)
        assert _names(generate_gpx([_cache(waypoints=[elsewhere])], content=ON)) == [
            "GCBWND7", "FNBWND7", "FLBWND7"]
        there = _child(prefix="FN", wp_code="FNBWND7", wp_type="Final Location",
                       latitude=47.2, longitude=8.6, wp_flag=False)
        content = GpxContent(children="flagged", corrected_final_wp=True)
        assert _names(generate_gpx([_cache(waypoints=[there])], content=content)) == [
            "GCBWND7", "FLBWND7"]

    def test_code_stays_unique(self):
        taken = _child(prefix="FL", wp_code="FLBWND7", wp_type="Stage", latitude=47.3)
        assert _names(generate_gpx([_cache(waypoints=[taken])], content=ON)) == [
            "GCBWND7", "FLBWND7", "F1BWND7"]

    def test_with_original_coordinates(self):
        # The cache at its listed position, the solution in the final.
        cache_wpt, final = _wpts(generate_gpx([_cache()], use_corrected=False, content=ON))
        assert (cache_wpt.get("lat"), final.get("lat")) == ("47.190000", "47.200000")

    def test_gpx_1_1(self):
        gpx = generate_gpx([_cache()], content=GpxContent(version="1.1", corrected_final_wp=True))
        assert _names(gpx, GPX11) == ["GCBWND7", "FLBWND7"]

    def test_not_with_child_waypoints_left_out(self):
        gpx = generate_gpx([_cache()], child_waypoints=False, content=ON)
        assert _names(gpx) == ["GCBWND7"]

    def test_ggz_index_still_points_at_each_cache_wpt(self):
        caches = [_cache(), _cache(id=2, gc_code="GCAAAA2", latitude=47.0)]
        data = generate_ggz(caches, "t", content=ON)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            index = ET.fromstring(z.read("index/com/garmin/geocaches/v0/index.xml"))
            gpx = z.read("data/t.gpx")
        files = [el for el in index.iter() if el.tag.endswith("gch")]
        assert len(files) == 2
        for gch in files:
            code = next(el.text for el in gch if el.tag.endswith("code"))
            pos = int(next(el.text for el in gch if el.tag.endswith("file_pos")))
            length = int(next(el.text for el in gch if el.tag.endswith("file_len")))
            chunk = gpx[pos:pos + length].decode("utf-8")
            assert chunk.startswith("<wpt") and f"<name>{code}</name>" in chunk
        assert gpx.count(b"<name>FL") == 2

    def test_bounds_include_the_final(self):
        cache = _cache(latitude=47.0, user_note=_note(lat=47.5, lon=8.9))
        assert gpx_bounds([cache], use_corrected=False) == (47.0, 8.52, 47.0, 8.52)
        assert gpx_bounds([cache], use_corrected=False, content=ON) == (47.0, 8.52, 47.5, 8.9)


def test_wpt_counts_of_stand_ins():
    caches = [_cache(), _cache(id=2, gc_code="GCAAAA2", user_note=None)]
    assert wpt_counts(caches, "gpx", ON) == [2, 1]
    # "max counts waypoints": the cache with its final, or not at all.
    assert select_for_export(caches, 2, "waypoints", "gpx", ON) == caches[:1]
    assert wpt_counts(caches, "loc", ON) == [1, 1]


def test_is_corrected_final_wp():
    assert is_corrected_final_wp("Final Location", CORRECTED_FINAL_WP_COMMENT)
    assert not is_corrected_final_wp("Final Location", "my final")
    assert not is_corrected_final_wp("Stage", CORRECTED_FINAL_WP_COMMENT)


# ── database ──────────────────────────────────────────────────────────────────

def _database(path: Path) -> Path:
    init_db(db_path=path)
    ensure_seeded(path, path.stem)
    return path


@pytest.fixture
def src_db(tmp_path):
    """Three caches: corrected with a Parking child; corrected with its own
    Final Location at the corrected coordinates; not corrected."""
    _database(tmp_path / "src.db")
    with get_session() as s:
        one = Cache(gc_code="GCAAAA1", name="One", cache_type="Unknown Cache",
                    latitude=47.0, longitude=8.0, placed_by="Owner")
        one.user_note = UserNote(note="", is_corrected=True, corrected_lat=47.5,
                                 corrected_lon=8.9)
        one.waypoints.append(Waypoint(prefix="PK", wp_type="Parking Area", name="Parking",
                                      latitude=47.01, longitude=8.01))
        two = Cache(gc_code="GCAAAA2", name="Two", cache_type="Unknown Cache",
                    latitude=46.0, longitude=7.0, placed_by="Owner")
        two.user_note = UserNote(note="", is_corrected=True, corrected_lat=46.1,
                                 corrected_lon=7.1)
        two.waypoints.append(Waypoint(prefix="FN", wp_type="Final Location", name="Final",
                                      latitude=46.1, longitude=7.1, wp_flag=True))
        three = Cache(gc_code="GCAAAA3", name="Three", cache_type="Traditional Cache",
                      latitude=45.0, longitude=6.0, placed_by="Owner")
        s.add_all([one, two, three])
        s.commit()
    with get_session() as s:
        caches = s.query(Cache).order_by(Cache.gc_code).all()
        s.expunge_all()
    return caches


@pytest.mark.parametrize("children, expected", [
    ("all", [3, 2, 1]),        # GCAAAA2's own final is written: no second one
    ("unflagged", [3, 2, 1]),  # GCAAAA2's flagged final isn't: the added one counts
    ("none", [2, 2, 1]),
])
def test_database_counts_match_the_file(src_db, tmp_path, children, expected):
    content = GpxContent(children=children, corrected_final_wp=True)
    assert wpt_counts(src_db, "gpx", content) == expected
    out = tmp_path / "out.gpx"
    write_export_file(src_db, out, "gpx", content=content)
    assert len(_wpts(out.read_text(encoding="utf-8"))) == sum(expected)


def test_database_counts_without_the_option(src_db):
    ids = [c.id for c in src_db]
    assert export_child_counts(ids, "none") == {}
    assert export_child_counts(ids, "all") == {src_db[0].id: 1, src_db[1].id: 1}


def test_database_bounds_include_the_final(src_db):
    ids = [c.id for c in src_db]
    assert export_bounds(ids, use_corrected=False, children="none") == (45.0, 6.0, 47.0, 8.0)
    assert export_bounds(ids, use_corrected=False, children="none",
                         corrected_final_wp=True) == (45.0, 6.0, 47.5, 8.9)


def test_reimport_skips_the_added_final(src_db, tmp_path):
    out = tmp_path / "out.gpx"
    write_export_file(src_db, out, "gpx", content=ON)
    assert "<name>FLAAAA1</name>" in out.read_text(encoding="utf-8")
    _database(tmp_path / "dst.db")
    with get_session() as s:
        result = import_gpx(out, s)
    assert result.created == 3 and not result.errors
    with get_session() as s:
        one = s.query(Cache).filter_by(gc_code="GCAAAA1").one()
        assert [wp.prefix for wp in one.waypoints] == ["PK"]
        # The corrected coordinates come back with the cache itself.
        assert one.user_note.is_corrected
        assert (one.user_note.corrected_lat, one.user_note.corrected_lon) == (47.5, 8.9)
        two = s.query(Cache).filter_by(gc_code="GCAAAA2").one()
        assert [wp.prefix for wp in two.waypoints] == ["FN"]


# ── settings and dialog ───────────────────────────────────────────────────────

class TestOption:
    def test_settings_round_trip(self):
        settings = FileExportSettings(corrected_final_wp=True)
        assert FileExportSettings.from_dict(settings.to_dict()) == settings
        assert settings.content().corrected_final_wp is True

    @pytest.mark.parametrize("data", [{}, {"corrected_final_wp": "yes"}])
    def test_old_or_invalid_settings_load_with_the_default(self, data):
        assert FileExportSettings.from_dict(data).corrected_final_wp is False

    def test_dialog_round_trip(self, qtbot):
        from opensak.gui.dialogs.file_export_dialog import FileExportDialog

        dlg = FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        assert not dlg._chk_corrected_final.isChecked()
        dlg._apply_settings(FileExportSettings(children="none", corrected_final_wp=True))
        assert dlg._chk_corrected_final.isChecked()
        assert dlg._collect_settings().corrected_final_wp is True
        assert dlg._content().corrected_final_wp is True
        # Children only still makes sense: the final is a child.
        assert dlg._chk_children_only.isEnabled()
        dlg._chk_corrected_final.setChecked(False)
        assert not dlg._chk_children_only.isEnabled()

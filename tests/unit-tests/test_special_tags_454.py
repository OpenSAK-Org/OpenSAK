# tests/unit-tests/test_special_tags_454.py — #454 / #950: GSAK's special
# tags in the GPX/GGZ export. Templates for the waypoint name (cut to a
# maximum length and kept unique), the description (also as the cache name),
# characters to remove, and extra information written as the first log of
# each cache — with GSAK's tag names and forms (%tag=nn, %found=xx, %lat=M,
# %caches_<field>, %notGC). The importer skips the extra-info log and finds
# a renamed cache by gsak:Code. In the export settings, the file export
# dialog, Send to GPS and the Lua macros (test_macro_runtime.py).

import io
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from opensak.db import companion, custom_fields
from opensak.db.database import dispose_engine, get_session, init_db
from opensak.db.db_settings import ensure_seeded
from opensak.db.models import Cache, Log, UserNote
from opensak.export.cache_text import ExportText
from opensak.export.file_export import write_export_file
from opensak.export.file_export_settings import FileExportSettings
from opensak.export.gpx_content import (
    EXTRA_INFO_LOG_ID, GpxContent, is_extra_info_log,
)
from opensak.export.special_tags import (
    SUGGESTED_FIRST_LOG, ExportTemplates, TagContext, UniqueNames, expand,
    expand_lines, gc_id, remove_chars, uses_custom_fields,
)
from opensak.gps.garmin import generate_ggz, generate_gpx
from opensak.importer import import_gpx
from opensak.lang import load_language
from opensak.utils import flags

GPX10 = "{http://www.topografix.com/GPX/1/0}"
GPX11 = "{http://www.topografix.com/GPX/1/1}"
GS = "{http://www.groundspeak.com/cache/1/0/1}"


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

def _log(log_id, day, log_type="Found it", finder="Someone"):
    return SimpleNamespace(
        log_id=log_id, log_date=datetime(2026, 1, day, tzinfo=timezone.utc),
        log_type=log_type, finder=finder, finder_id="111", text=f"log {log_id}",
    )


def _cache(**kw) -> SimpleNamespace:
    values = dict(
        id=1, gc_code="GC1A2B3", gc_cache_id=None, name="Old Mill",
        cache_type="Multi-cache", latitude=47.123456, longitude=8.543210,
        difficulty=2.5, terrain=1.5, placed_by="Miller", owner_name="M. Miller",
        owner_id="42", available=True, archived=False, country="Switzerland",
        state="Zug", county="Baar", encoded_hints="under the root", hidden_date=None,
        logs=[], user_note=None, container="Micro", short_description=None,
        short_desc_html=False, long_description="Long", long_desc_html=False,
        attributes=[], waypoints=[], trackables=[], found=False, dnf=False,
        user_flag=False, elevation=None, favorite_points=12,
        last_log_date=None, last_found_date=None, found_date=None,
        user_data_1="one", user_data_2=None, user_data_3=None, user_data_4=None,
        user_sort=None, distance=None,
    )
    values.update(kw)
    return SimpleNamespace(**values)


def _root(gpx: str) -> ET.Element:
    return ET.fromstring(gpx.split("\n", 1)[1])


def _wpts(gpx: str, ns: str = GPX10) -> list[ET.Element]:
    return _root(gpx).findall(f"{ns}wpt")


def _logs(wpt: ET.Element) -> list[tuple]:
    return [(lg.get("id"), lg.findtext(f"{GS}finder"), lg.findtext(f"{GS}text"))
            for lg in wpt.iter(f"{GS}log")]


# ── the tags ──────────────────────────────────────────────────────────────────

class TestTags:
    @pytest.mark.parametrize("template, expected", [
        ("%code", "GC1A2B3"),
        ("%drop2%con1%typ1", "1A2B3MM"),
        ("%name by %by (%dif/%ter)", "Old Mill by Miller (2.5/1.5)"),
        ("%des", "Old Mill"),
        ("%ownerName / %ownerID", "M. Miller / 42"),
        ("%dif1 %dif1a %ter1 %ter1a", "4 B 2 A"),
        ("%typ=3 %name=3", "Mul Old"),
        ("%hint", "under the root"),
        ("%crypt", "haqre gur ebbg"),
        ("%state, %county, %country", "Zug, Baar, Switzerland"),
        ("%status", "Available"),
        ("%found %dnf %flag %correct %bug", "N N N N N"),
        ("%found=*!", "!"),
        ("%FavPoints %user", "12 one"),
        ("%lat %lon", "47.12346 8.54321"),
        ("%lat=M %lon=M", "N47 07.407 E008 32.593"),
        ("%lat=S", "N47° 07' 24.44\""),
        ("%gcid", str(gc_id("GC1A2B3"))),
        ("%blank", ""),
        ("%CODE %Name", "GC1A2B3 Old Mill"),          # not case sensitive
        ("%smart %macro %c_Name %nothing 100%", "%smart %macro %c_Name %nothing 100%"),
        ("%caches_FavPoints %caches_PlacedBy", "12 Miller"),
        ("%caches_Found={Yes|No}", "No"),
        ("%caches_NoSuchField", "%caches_NoSuchField"),
    ])
    def test_tag(self, template, expected):
        assert expand(template, _cache()) == expected

    def test_dates_and_last4(self):
        cache = _cache(
            found=True, found_date=datetime(2026, 2, 1), hidden_date=datetime(2020, 5, 6),
            logs=[_log("1", 9), _log("2", 7, "Didn't find it"), _log("3", 8, "Write note"),
                  _log("4", 5)],
        )
        assert expand("%foundbyme %datepl %datelf %LastLog %LastFound %last4", cache) == \
            "2026-02-01 2020-05-06 2026-01-09 2026-01-09 2026-01-09 FNF0"
        # Stored dates win over the logs.
        stored = _cache(last_log_date=datetime(2026, 3, 3), last_found_date=datetime(2026, 3, 1))
        assert expand("%LastLog %LastFound", stored) == "2026-03-03 2026-03-01"

    def test_yes_no_tags(self):
        cache = _cache(found=True, dnf=True, user_flag=True, trackables=[object()],
                       user_note=SimpleNamespace(note="n", is_corrected=True,
                                                 corrected_lat=47.0, corrected_lon=8.0))
        assert expand("%found %dnf %flag %correct %bug %correct=+-", cache) == "Y Y Y Y Y +"

    def test_corrected_coordinates_follow_the_export(self):
        cache = _cache(user_note=SimpleNamespace(note="", is_corrected=True,
                                                 corrected_lat=47.5, corrected_lon=8.25))
        assert expand("%lat %lon", cache) == "47.50000 8.25000"
        original = TagContext(use_corrected=False)
        assert expand("%lat %lon", cache, ctx=original) == "47.12346 8.54321"

    def test_context_tags(self):
        ctx = TagContext(use_miles=True, database="Home", centre="Office")
        assert expand("%dist|%database|%centre", _cache(distance=10.0), ctx=ctx) == \
            "6.2 mi|Home|Office"
        assert expand("%dist", _cache(distance=1.25)) == "1.2 km"

    def test_renamed_cache(self):
        assert expand("%name", _cache(), name="Renamed") == "Renamed"

    def test_custom_fields(self):
        custom = [("Swissgrid", "2'688'000"), ("MyTour", 1), ("Empty", None)]
        assert expand("%caches_swissgrid %custom_Swissgrid=5", _cache(), custom=custom) == \
            "2'688'000 2'688"
        assert expand("%caches_MyTour={on|off} %caches_Empty={on|off}", _cache(),
                      custom=custom) == "on off"
        assert uses_custom_fields(["x", "%Caches_Foo"]) and not uses_custom_fields(["%code"])

    def test_not_gc(self):
        template = "%drop2%con1%notGC%code"
        assert expand(template, _cache()) == "1A2B3M"
        assert expand(template, _cache(gc_code="LC1234")) == "LC1234"

    def test_lines_with_only_empty_tags_are_left_out(self):
        cache = _cache(encoded_hints=None, favorite_points=None)
        assert expand_lines(SUGGESTED_FIRST_LOG, cache) == ""
        cache = _cache(logs=[_log("1", 4)])
        assert expand_lines(SUGGESTED_FIRST_LOG, cache) == (
            "Hint: under the root\nLast Log: 2026-01-04\nLast Found: 2026-01-04\n"
            "Favorite Points: 12"
        )
        # A line without tags stays.
        assert expand_lines("Hello\n%hint", _cache(encoded_hints=None)) == "Hello"

    @pytest.mark.parametrize("code, number", [
        ("GC1", "1"), ("GCFFFF", "65535"), ("GCG000", "65536"), ("GC10000", "512401"),
        ("XX123", ""), ("GC", ""),
    ])
    def test_gc_id(self, code, number):
        assert gc_id(code) == number

    def test_remove_chars(self):
        assert remove_chars("Café & Bar!", "&!") == "Café  Bar"
        assert remove_chars("x", "") == "x"

    def test_unique_names(self):
        names = UniqueNames(6)
        assert [names.take(n) for n in ("Old Mill", "Old Mill", "Old Mill", "other")] == \
            ["Old Mi", "Old M1", "Old M2", "other"]
        unlimited = UniqueNames()
        assert [unlimited.take(n) for n in ("A", "a", "A")] == ["A", "a1", "A2"]


# ── the writer ────────────────────────────────────────────────────────────────

class TestWriter:
    def test_defaults_change_nothing(self):
        gpx = generate_gpx([_cache()])
        wpt = _wpts(gpx)[0]
        assert wpt.findtext(f"{GPX10}name") == "GC1A2B3"
        assert wpt.findtext(f"{GPX10}desc") == "Old Mill by Miller (2.5/1.5)"
        assert wpt.find(f".//{GS}name").text == "Old Mill"
        assert _logs(wpt) == []

    def test_name_template_cut_and_unique(self):
        caches = [_cache(), _cache(id=2, gc_code="GC1A2B4"), _cache(id=3, gc_code="GC9")]
        content = GpxContent(name_template="%name", name_max_chars=5)
        names = [w.findtext(f"{GPX10}name") for w in _wpts(generate_gpx(caches, content=content))]
        assert names == ["Old M", "Old 1", "Old 2"]

    def test_max_chars_alone_cuts_the_code(self):
        content = GpxContent(name_max_chars=4)
        assert _wpts(generate_gpx([_cache()], content=content))[0].findtext(f"{GPX10}name") \
            == "GC1A"

    def test_description_template_and_cache_name(self):
        content = GpxContent(desc_template="%typ1%dif1%ter1 %name", desc_for_cache_name=True,
                             remove_chars="M")
        wpt = _wpts(generate_gpx([_cache()], content=content))[0]
        assert wpt.findtext(f"{GPX10}desc") == "42 Old ill"
        assert wpt.find(f".//{GS}name").text == "42 Old ill"
        # <urlname> keeps "Name by Owner".
        assert wpt.findtext(f"{GPX10}urlname") == "Old Mill by Miller"

    def test_macro_texts_win_over_the_templates(self):
        text = ExportText(names={"GC1A2B3": "Macro"}, descriptions={"GC1A2B3": "Macro desc"},
                          first_logs={"GC1A2B3": "Macro log"})
        content = GpxContent(desc_template="%code", desc_for_cache_name=True,
                             first_log="%hint")
        wpt = _wpts(generate_gpx([_cache()], text=text, content=content))[0]
        assert wpt.findtext(f"{GPX10}desc") == "Macro desc"
        assert wpt.find(f".//{GS}name").text == "Macro"
        assert _logs(wpt)[0] == (EXTRA_INFO_LOG_ID, "OpenSAK", "Macro log")

    def test_first_log_comes_first_and_is_not_counted(self):
        note = SimpleNamespace(note="My note", is_corrected=False, corrected_lat=None,
                               corrected_lon=None, updated_at=None)
        cache = _cache(user_note=note, logs=[_log("1", 3), _log("2", 4)])
        content = GpxContent(first_log=SUGGESTED_FIRST_LOG, user_note_log=True, max_logs=1)
        logs = _logs(_wpts(generate_gpx([cache], content=content))[0])
        assert [(i, f) for i, f, _t in logs] == [
            (EXTRA_INFO_LOG_ID, "OpenSAK"), ("-2", "GSAK"), ("2", "Someone"),
        ]
        assert logs[0][2].startswith("Hint: under the root\nLast Log: 2026-01-04")

    def test_no_log_when_every_line_is_empty(self):
        content = GpxContent(first_log="Hint: %hint")
        assert _logs(_wpts(generate_gpx([_cache(encoded_hints=None)], content=content))[0]) == []

    def test_gpx_1_1(self):
        content = GpxContent(version="1.1", name_template="%drop2", first_log="%hint")
        wpt = _wpts(generate_gpx([_cache()], content=content), GPX11)[0]
        assert wpt.findtext(f"{GPX11}name") == "1A2B3"
        assert _logs(wpt)[0][0] == EXTRA_INFO_LOG_ID

    def test_ggz_index_and_offsets(self):
        caches = [_cache(), _cache(id=2, gc_code="GC1A2B4", name="Second")]
        content = GpxContent(name_template="%drop2", desc_template="%typ1 %name",
                             desc_for_cache_name=True, first_log=SUGGESTED_FIRST_LOG)
        with zipfile.ZipFile(io.BytesIO(generate_ggz(caches, content=content))) as zf:
            data = zf.read(next(n for n in zf.namelist() if n.endswith(".gpx")))
            index = ET.fromstring(zf.read("index/com/garmin/geocaches/v0/index.xml"))
        ns = "{http://www.opencaching.com/xmlschemas/ggz/1/0}"
        entries = index.findall(f".//{ns}gch")
        # The index keeps the GC code; its name follows groundspeak:name.
        assert [e.findtext(f"{ns}code") for e in entries] == ["GC1A2B3", "GC1A2B4"]
        assert [e.findtext(f"{ns}name") for e in entries] == ["M Old Mill", "M Second"]
        for entry in entries:
            pos, length = int(entry.findtext(f"{ns}file_pos")), int(entry.findtext(f"{ns}file_len"))
            chunk = data[pos:pos + length].decode("utf-8")
            assert chunk.startswith("<wpt") and chunk.rstrip().endswith("</wpt>")
            assert entry.findtext(f"{ns}code")[2:] in chunk


# ── database: custom fields, re-import ────────────────────────────────────────

@pytest.fixture
def db_caches(tmp_path):
    db = tmp_path / "src.db"
    init_db(db_path=db)
    ensure_seeded(db, db.stem)
    custom_fields.define_field(db, "Swissgrid", "string")
    with get_session() as s:
        cache = Cache(gc_code="GC1A2B3", name="Old Mill", cache_type="Multi-cache",
                      container="Micro", latitude=47.1, longitude=8.5, placed_by="Miller",
                      encoded_hints="under the root", favorite_points=12)
        cache.logs.append(Log(log_id="7001", log_type="Found it", finder="Someone",
                              log_date=datetime(2026, 1, 4), text="TFTC"))
        cache.user_note = UserNote(note="My note")
        s.add(cache)
        s.commit()
    custom_fields.write_values(db, [("GC1A2B3", {"Swissgrid": "2'688'000"})])
    with get_session() as s:
        caches = s.query(Cache).all()
        for c in caches:
            _ = c.logs, c.user_note, c.waypoints, c.attributes, c.trackables
        s.expunge_all()
    return caches


def test_custom_field_tag_without_gsak_fields(db_caches):
    # The custom values are loaded for a template even with GSAK's fields off.
    content = GpxContent(gsak_fields=False, desc_template="%name %caches_Swissgrid")
    gpx = generate_gpx(db_caches, content=content)
    assert _wpts(gpx)[0].findtext(f"{GPX10}desc") == "Old Mill 2'688'000"
    assert "CustomData" not in gpx


@pytest.mark.parametrize("version", ["1.0", "1.1"])
def test_reimport_skips_the_log_and_finds_the_renamed_cache(db_caches, tmp_path, version):
    out = tmp_path / "out.gpx"
    content = GpxContent(version=version, name_template="%drop2%con1", first_log=SUGGESTED_FIRST_LOG,
                         user_note_log=True)
    write_export_file(db_caches, out, "gpx", content=content)
    text = out.read_text(encoding="utf-8")
    assert f'id="{EXTRA_INFO_LOG_ID}"' in text and ">1A2B3M<" in text

    with get_session() as s:
        result = import_gpx(out, s)
    assert result.created == 0 and result.updated == 1 and not result.errors
    with get_session() as s:
        caches = s.query(Cache).all()
        assert [c.gc_code for c in caches] == ["GC1A2B3"]
        assert [lg.log_id for lg in caches[0].logs] == ["7001"]
        assert caches[0].last_log_date.date() == datetime(2026, 1, 4).date()


def test_is_extra_info_log():
    assert is_extra_info_log("-454", "OpenSAK")
    assert not is_extra_info_log("-454", "Someone")
    assert not is_extra_info_log("-2", "OpenSAK")


# ── settings, dialog, Send to GPS ─────────────────────────────────────────────

TEMPLATES = dict(name_template="%drop2%con1", name_max_chars=8, desc_template="%name",
                 desc_for_cache_name=True, remove_chars="&", first_log=SUGGESTED_FIRST_LOG)


class TestSettings:
    def test_defaults(self):
        content = FileExportSettings().content()
        assert (content.name_template, content.name_max_chars, content.desc_template,
                content.desc_for_cache_name, content.remove_chars, content.first_log) == \
            ("", 0, "", False, "", "")

    def test_round_trip(self):
        settings = FileExportSettings(**TEMPLATES)
        assert FileExportSettings.from_dict(settings.to_dict()) == settings
        assert settings.content() == GpxContent(**TEMPLATES)

    @pytest.mark.parametrize("data", [
        {}, {"name_template": 5, "name_max_chars": -1, "desc_for_cache_name": "yes",
             "first_log": None, "remove_chars": ["x"], "desc_template": False},
        {"name_max_chars": True},
    ])
    def test_old_or_invalid_settings_load_with_the_defaults(self, data):
        settings = FileExportSettings.from_dict(data)
        assert settings.content() == FileExportSettings().content()

    @pytest.mark.parametrize("kw", [{"name_max_chars": -1}, {"name_max_chars": True},
                                    {"first_log": None}])
    def test_content_rejects_bad_values(self, kw):
        with pytest.raises(ValueError):
            GpxContent(**kw)

    def test_templates_only_for_gpx_and_ggz(self):
        content = GpxContent(**TEMPLATES)
        assert content.for_format("ggz").first_log == content.first_log
        assert content.for_format("loc").first_log == ""


def test_dialog_round_trip(qtbot):
    from opensak.gui.dialogs.file_export_dialog import FileExportDialog

    dlg = FileExportDialog([_cache()])
    qtbot.addWidget(dlg)
    dlg._apply_settings(FileExportSettings(**TEMPLATES))
    assert dlg._edit_name_template.text() == "%drop2%con1"
    assert dlg._spin_name_max.value() == 8
    assert dlg._edit_first_log.toPlainText() == SUGGESTED_FIRST_LOG
    collected = dlg._collect_settings()
    assert {k: getattr(collected, k) for k in TEMPLATES} == TEMPLATES
    assert dlg._content() == GpxContent(**TEMPLATES)

    dlg._edit_first_log.clear()
    dlg._apply_settings(FileExportSettings())
    assert dlg._collect_settings().first_log == ""
    # "LOC" has no templates.
    dlg._btn_loc.setChecked(True)
    assert not dlg._texts_group.isEnabled()


def test_dialog_suggested_button(qtbot):
    from PySide6.QtWidgets import QPushButton

    from opensak.gui.dialogs.file_export_dialog import FileExportDialog
    from opensak.lang import tr

    dlg = FileExportDialog([_cache()])
    qtbot.addWidget(dlg)
    button = next(b for b in dlg.findChildren(QPushButton)
                  if b.text() == tr("file_export_first_log_suggested"))
    button.click()
    assert dlg._edit_first_log.toPlainText() == SUGGESTED_FIRST_LOG


def test_gps_export_follows_the_setting(db_caches, tmp_path):
    from opensak.gui.dialogs.gps_dialog import ExportWorker

    worker = ExportWorker(db_caches, tmp_path, "gps", max_caches=0,
                          settings=FileExportSettings(first_log="Hint: %hint"))
    worker.run()
    gpx = (tmp_path / "gps.gpx").read_text(encoding="utf-8")
    assert f'<groundspeak:log id="{EXTRA_INFO_LOG_ID}">' in gpx
    assert "Hint: under the root" in gpx


def test_export_templates_keep_names_unique_per_file():
    content = GpxContent(name_max_chars=3)
    first = ExportTemplates(content)
    assert [first.wpt_name(_cache(), "x"), first.wpt_name(_cache(), "x")] == ["GC1", "GC2"]
    # A new file starts again.
    assert ExportTemplates(content).wpt_name(_cache(), "x") == "GC1"

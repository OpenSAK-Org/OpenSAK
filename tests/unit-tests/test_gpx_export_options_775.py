# tests/unit-tests/test_gpx_export_options_775.py — #775: the GPX/GGZ export
# options that decide what a file contains and how big it is: which child
# waypoints (all, flagged, unflagged, none, or only the children), how many
# logs per cache (own logs kept on top), the user note as a synthetic first
# log (and the importer skipping it again), GPX 1.0 or 1.1, and a record
# limit that counts waypoints instead of caches. In the export settings, the
# file export dialog, Send to GPS and the Lua macros.

import io
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from opensak.db.database import (
    export_bounds, export_child_counts, get_session, init_db, reload_caches_full,
)
from opensak.db.models import Cache, Log, UserNote, Waypoint
from opensak.export.file_export import select_for_export, wpt_counts, write_export_file
from opensak.export.file_export_settings import FileExportSettings
from opensak.export.gpx_content import (
    DEFAULT_CONTENT, GpxContent, is_user_note_log, normalize_note,
)
from opensak.gps.garmin import generate_ggz, generate_gpx, gpx_bounds
from opensak.importer import import_gpx

GPX10 = "{http://www.topografix.com/GPX/1/0}"
GPX11 = "{http://www.topografix.com/GPX/1/1}"
GS = "{http://www.groundspeak.com/cache/1/0/1}"
GSAK = "{http://www.gsak.net/xmlv1/6}"


# ── helpers ───────────────────────────────────────────────────────────────────

def _child(prefix="PK", flag=False, **kw) -> SimpleNamespace:
    values = dict(
        prefix=prefix, wp_type="Parking Area", name=f"Child {prefix}", description=None,
        comment=None, latitude=47.1, longitude=8.5, url=None, wp_date=None,
        wp_code=None, wp_flag=flag,
    )
    values.update(kw)
    return SimpleNamespace(**values)


def _log(log_id, day, finder="Someone", finder_id="111", log_type="Found it"):
    return SimpleNamespace(
        log_id=log_id, log_date=datetime(2026, 1, day, tzinfo=timezone.utc),
        log_type=log_type, finder=finder, finder_id=finder_id, text=f"log {log_id}",
    )


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
    )
    values.update(kw)
    return SimpleNamespace(**values)


def _root(gpx: str) -> ET.Element:
    return ET.fromstring(gpx.split("\n", 1)[1])


def _names(gpx: str, ns: str = GPX10) -> list:
    return [w.findtext(f"{ns}name") for w in _root(gpx).findall(f"{ns}wpt")]


def _log_ids(gpx: str) -> list:
    return [log.get("id") for log in _root(gpx).iter(f"{GS}log")]


def _tags(el: ET.Element) -> list:
    return [c.tag.split("}")[1] if "}" in c.tag else c.tag for c in el]


def _family():
    """A cache with a flagged and an unflagged child."""
    return _cache(waypoints=[
        _child("PK", flag=True, latitude=46.0, longitude=8.0),
        _child("S1", flag=False, latitude=48.0, longitude=9.0),
    ])


@pytest.fixture
def account():
    from opensak.gui.settings import get_settings
    settings = get_settings()
    old = settings.gc_username, settings.gc_finder_id
    settings.gc_username, settings.gc_finder_id = "Me", "999"
    yield
    settings.gc_username, settings.gc_finder_id = old


# ── GpxContent ────────────────────────────────────────────────────────────────

class TestGpxContent:
    @pytest.mark.parametrize("kw", [
        {"children": "some"}, {"version": "2.0"}, {"max_logs": -1}, {"max_logs": True},
    ])
    def test_invalid_values_are_refused(self, kw):
        with pytest.raises(ValueError):
            GpxContent(**kw)

    def test_for_format(self):
        content = GpxContent(children_only=True, version="1.1", max_logs=2)
        assert content.for_format("gpx") == content
        # A GGZ index points at each cache's <wpt>: never children only.
        # Its GPX version follows the option (no GGZ spec says otherwise).
        assert content.for_format("ggz") == GpxContent(version="1.1", max_logs=2)
        assert content.for_format("loc") == DEFAULT_CONTENT

    def test_select_logs_limit_and_own_logs(self):
        logs = [_log(str(day), day, finder="Me" if day == 1 else "X") for day in (3, 1, 5, 4, 2)]
        assert [l.log_id for l in DEFAULT_CONTENT.select_logs(logs)] == ["5", "4", "3", "2", "1"]
        two = GpxContent(max_logs=2)
        assert [l.log_id for l in two.select_logs(logs)] == ["5", "4"]
        mine = GpxContent(max_logs=2, my_logs_always=True)
        is_mine = lambda log: log.finder == "Me"   # noqa: E731
        assert [l.log_id for l in mine.select_logs(logs, is_mine)] == ["5", "4", "1"]
        # Without an account there is nothing to keep.
        assert [l.log_id for l in mine.select_logs(logs, None)] == ["5", "4"]

    def test_select_logs_mixes_naive_and_aware_dates(self):
        logs = [_log("a", 1), SimpleNamespace(log_id="b", log_date=datetime(2026, 2, 1)),
                SimpleNamespace(log_id="c", log_date=None)]
        assert [l.log_id for l in DEFAULT_CONTENT.select_logs(logs)] == ["b", "a", "c"]

    def test_is_user_note_log(self):
        notes = {normalize_note("Solved:\r\nN 47")}
        args = ("-2", "GSAK", "0", "Write note")
        assert is_user_note_log(*args, "Solved:\nN 47", notes)
        assert not is_user_note_log(*args, "Something else", notes)
        assert not is_user_note_log("123", "GSAK", "0", "Write note", "Solved:\nN 47", notes)
        assert not is_user_note_log("-2", "Me", "0", "Write note", "Solved:\nN 47", notes)


# ── children ──────────────────────────────────────────────────────────────────

class TestChildren:
    @pytest.mark.parametrize("children, expected", [
        ("all", ["GCBWND7", "PKBWND7", "S1BWND7"]),
        ("flagged", ["GCBWND7", "PKBWND7"]),
        ("unflagged", ["GCBWND7", "S1BWND7"]),
        ("none", ["GCBWND7"]),
    ])
    def test_children_choice(self, children, expected):
        gpx = generate_gpx([_family()], content=GpxContent(children=children))
        assert _names(gpx) == expected
        assert ("(HasChildren)" in gpx) == (children != "none")

    def test_children_only(self):
        gpx = io.StringIO()
        from opensak.gps.garmin import write_gpx
        caches = [_family(), _cache(id=2, gc_code="GC2ABCD", waypoints=[])]
        count = write_gpx(gpx, caches, content=GpxContent(children_only=True))
        assert _names(gpx.getvalue()) == ["PKBWND7", "S1BWND7"]
        assert "groundspeak:cache" not in gpx.getvalue()
        # The caches whose children were written.
        assert count == 1

    def test_bounds_follow_the_children(self):
        caches = [_family()]
        assert gpx_bounds(caches) == (46.0, 8.0, 48.0, 9.0)
        assert gpx_bounds(caches, content=GpxContent(children="flagged")) == (46.0, 8.0, 47.19, 8.52)
        assert gpx_bounds(caches, content=GpxContent(children="none")) == (47.19, 8.52, 47.19, 8.52)
        assert gpx_bounds(caches, content=GpxContent(children_only=True)) == (46.0, 8.0, 48.0, 9.0)
        assert gpx_bounds(caches, content=GpxContent(children="unflagged", children_only=True)) \
            == (48.0, 9.0, 48.0, 9.0)

    def test_ggz_ignores_children_only(self):
        data = generate_ggz([_family()], content=GpxContent(children_only=True))
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            gpx = zf.read("data/opensak_export.gpx").decode("utf-8")
        assert _names(gpx) == ["GCBWND7", "PKBWND7", "S1BWND7"]


# ── logs ──────────────────────────────────────────────────────────────────────

class TestLogs:
    def _cache_with_logs(self):
        return _cache(logs=[_log(str(day), day, finder="Me" if day == 1 else "X",
                                 finder_id="999" if day == 1 else "5")
                            for day in (1, 2, 3, 4)])

    def test_max_logs(self):
        gpx = generate_gpx([self._cache_with_logs()], content=GpxContent(max_logs=2))
        assert _log_ids(gpx) == ["4", "3"]

    def test_my_logs_always(self, account):
        content = GpxContent(max_logs=2, my_logs_always=True)
        assert _log_ids(generate_gpx([self._cache_with_logs()], content=content)) == ["4", "3", "1"]

    def test_my_logs_always_without_account(self):
        from opensak.gui.settings import get_settings
        settings = get_settings()
        old = settings.gc_username, settings.gc_finder_id
        settings.gc_username, settings.gc_finder_id = "", ""
        try:
            content = GpxContent(max_logs=2, my_logs_always=True)
            assert _log_ids(generate_gpx([self._cache_with_logs()], content=content)) == ["4", "3"]
        finally:
            settings.gc_username, settings.gc_finder_id = old

    def test_user_note_log_form(self):
        note = SimpleNamespace(note="Solved:\r\nN 47 12.345", is_corrected=False,
                               updated_at=datetime(2025, 7, 6, 8, 0))
        cache = self._cache_with_logs()
        cache.user_note = note
        gpx = generate_gpx([cache], content=GpxContent(user_note_log=True, max_logs=1))
        logs = list(_root(gpx).iter(f"{GS}log"))
        # The note log comes first and doesn't count against max_logs.
        assert [l.get("id") for l in logs] == ["-2", "4"]
        first = logs[0]
        assert first.findtext(f"{GS}date") == "2025-07-06T08:00:00"
        assert first.findtext(f"{GS}type") == "Write note"
        assert first.find(f"{GS}finder").get("id") == "0"
        assert first.findtext(f"{GS}finder") == "GSAK"
        assert first.findtext(f"{GS}text") == "Solved:\nN 47 12.345"

    def test_no_user_note_log_without_a_note_or_option(self):
        cache = _cache(user_note=SimpleNamespace(note="  ", is_corrected=False, updated_at=None))
        assert "-2" not in _log_ids(generate_gpx([cache], content=GpxContent(user_note_log=True)))
        cache.user_note.note = "Note"
        assert "-2" not in _log_ids(generate_gpx([cache]))


# ── GPX 1.1 ───────────────────────────────────────────────────────────────────

class TestGpx11:
    def _gpx(self):
        cache = _family()
        cache.user_note = SimpleNamespace(note="My note", is_corrected=False, updated_at=None)
        cache.waypoints[0].url = "https://example.com/pk"
        return generate_gpx([cache], filename="export", content=GpxContent(version="1.1"))

    def test_root_and_metadata(self):
        gpx = self._gpx()
        root = _root(gpx)
        assert root.tag == f"{GPX11}gpx" and root.get("version") == "1.1"
        assert 'xsi:schemaLocation="http://www.topografix.com/GPX/1/1 ' in gpx
        assert _tags(root)[0] == "metadata"
        meta = root.find(f"{GPX11}metadata")
        assert _tags(meta) == ["name", "desc", "author", "time", "keywords", "bounds"]
        assert meta.findtext(f"{GPX11}author/{GPX11}name") == "OpenSAK"

    def test_cache_wpt_in_gpx_1_1_order(self):
        wpt = _root(self._gpx()).findall(f"{GPX11}wpt")[0]
        assert _tags(wpt) == ["name", "desc", "link", "sym", "type", "extensions"]
        link = wpt.find(f"{GPX11}link")
        assert link.get("href") == "https://coord.info/GCBWND7"
        assert link.findtext(f"{GPX11}text") == "Early Birds by Valentin"
        ext = wpt.find(f"{GPX11}extensions")
        assert [c.tag for c in ext] == [f"{GSAK}wptExtension", f"{GS}cache"]

    def test_child_link_always_carries_the_name(self):
        # Like GSAK's own GPX 1.1 export: <link href=""> without a URL, so
        # the waypoint name stays in the link text.
        children = _root(self._gpx()).findall(f"{GPX11}wpt")[1:]
        for child in children:
            # #1169: GSAK's child fields go into <extensions>, last.
            assert _tags(child) == ["name", "cmt", "desc", "link", "sym", "type", "extensions"]
        assert children[0].find(f"{GPX11}link").get("href") == "https://example.com/pk"
        assert children[1].find(f"{GPX11}link").get("href") == ""
        assert children[1].findtext(f"{GPX11}link/{GPX11}text") == "Child S1"

    def test_ggz_follows_the_version(self):
        data = generate_ggz([_family()], content=GpxContent(version="1.1"))
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            gpx = zf.read("data/opensak_export.gpx").decode("utf-8")
            index = zf.read("index/com/garmin/geocaches/v0/index.xml").decode("utf-8")
        assert 'version="1.1"' in gpx
        # The index still points at the cache's <wpt>.
        pos = int(index.split("<file_pos>")[1].split("<")[0])
        length = int(index.split("<file_len>")[1].split("<")[0])
        chunk = gpx.encode("utf-8")[pos:pos + length].decode("utf-8")
        assert chunk.startswith("<wpt") and chunk.endswith("</wpt>")
        assert "<name>GCBWND7</name>" in chunk


# ── database: counts, bounds, selection, export → import ──────────────────────

@pytest.fixture
def src_db(tmp_path):
    """Three caches in list order: GC1AAAA with a flagged and an unflagged
    child (and a child without coordinates), GC2BBBB without children,
    GC3CCCC with one unflagged child. GC1AAAA has a user note and logs."""
    init_db(db_path=tmp_path / "src.db")
    with get_session() as s:
        one = Cache(gc_code="GC1AAAA", name="One", cache_type="Multi-cache",
                    latitude=47.0, longitude=8.0, placed_by="Owner")
        one.waypoints.append(Waypoint(prefix="PK", wp_type="Parking Area", name="P",
                                      latitude=46.5, longitude=7.5, wp_flag=True))
        one.waypoints.append(Waypoint(prefix="S1", wp_type="Stage", name="S",
                                      latitude=47.5, longitude=8.5))
        one.waypoints.append(Waypoint(prefix="S2", wp_type="Stage", name="?",
                                      latitude=None, longitude=None))
        one.user_note = UserNote(note="Solved: N 47", updated_at=datetime(2025, 7, 6, 8, 0))
        for day in (1, 2, 3):
            one.logs.append(Log(log_id=str(1000 + day), log_type="Found it",
                                log_date=datetime(2026, 1, day), finder="X", finder_id="5",
                                text=f"log {day}"))
        two = Cache(gc_code="GC2BBBB", name="Two", cache_type="Traditional Cache",
                    latitude=47.1, longitude=8.1, placed_by="Owner")
        three = Cache(gc_code="GC3CCCC", name="Three", cache_type="Traditional Cache",
                      latitude=47.2, longitude=8.2, placed_by="Owner")
        three.waypoints.append(Waypoint(prefix="PK", wp_type="Parking Area", name="P",
                                        latitude=47.25, longitude=8.25))
        s.add_all([one, two, three])
        s.commit()
        caches = s.query(Cache).order_by(Cache.gc_code).all()
        s.expunge_all()
    return caches


def test_export_child_counts(src_db):
    ids = {c.gc_code: c.id for c in src_db}
    all_ids = list(ids.values())
    assert export_child_counts(all_ids) == {ids["GC1AAAA"]: 2, ids["GC3CCCC"]: 1}
    assert export_child_counts(all_ids, "flagged") == {ids["GC1AAAA"]: 1}
    assert export_child_counts(all_ids, "unflagged") == {ids["GC1AAAA"]: 1, ids["GC3CCCC"]: 1}
    assert export_child_counts(all_ids, "none") == {}


def test_export_bounds_children_choice(src_db):
    one = [c.id for c in src_db if c.gc_code == "GC1AAAA"]
    assert export_bounds(one) == (46.5, 7.5, 47.5, 8.5)
    assert export_bounds(one, children="flagged") == (46.5, 7.5, 47.0, 8.0)
    assert export_bounds(one, children="none") == (47.0, 8.0, 47.0, 8.0)
    assert export_bounds(one, children="unflagged", caches=False) == (47.5, 8.5, 47.5, 8.5)


def test_wpt_counts(src_db):
    assert wpt_counts(src_db) == [3, 1, 2]
    assert wpt_counts(src_db, "gpx", GpxContent(children="flagged")) == [2, 1, 1]
    assert wpt_counts(src_db, "gpx", GpxContent(children_only=True)) == [2, 0, 1]
    assert wpt_counts(src_db, "ggz", GpxContent(children_only=True)) == [3, 1, 2]
    assert wpt_counts(src_db, "loc") == [1, 1, 1]
    # Test stand-ins are counted from their attributes.
    assert wpt_counts([_family()], "gpx", GpxContent(children="unflagged")) == [2]


@pytest.mark.parametrize("max_records, expected", [
    (0, ["GC1AAAA", "GC2BBBB", "GC3CCCC"]),
    (3, ["GC1AAAA"]),            # GC2BBBB would be the 4th <wpt>
    (4, ["GC1AAAA", "GC2BBBB"]),
    (5, ["GC1AAAA", "GC2BBBB"]),  # GC3CCCC needs 2: whole caches only
    (6, ["GC1AAAA", "GC2BBBB", "GC3CCCC"]),
    (2, []),                     # the first cache doesn't fit: nothing
])
def test_select_for_export_counting_waypoints(src_db, max_records, expected):
    selected = select_for_export(src_db, max_records, "waypoints")
    assert [c.gc_code for c in selected] == expected


def test_select_for_export_stops_at_the_first_cache_that_does_not_fit(src_db):
    # 2 + 1 fits, GC3CCCC (2) doesn't — the cache after it isn't tried,
    # so the limit drops the far end of the list, as GSAK does.
    caches = [src_db[0], src_db[2], src_db[1]]
    assert [c.gc_code for c in select_for_export(caches, 4, "waypoints")] == ["GC1AAAA"]
    assert [c.gc_code for c in select_for_export(caches, 4, "caches")] == [
        "GC1AAAA", "GC3CCCC", "GC2BBBB"]


@pytest.mark.parametrize("fmt", ["gpx", "ggz"])
def test_file_export_with_options(src_db, tmp_path, fmt):
    out = tmp_path / f"out.{fmt}"
    content = GpxContent(children="flagged", max_logs=1, user_note_log=True)
    assert write_export_file(src_db, out, fmt, content=content) == 3
    if fmt == "ggz":
        with zipfile.ZipFile(out) as zf:
            gpx = zf.read("data/out.gpx").decode("utf-8")
    else:
        gpx = out.read_text(encoding="utf-8")
    assert _names(gpx) == ["GC1AAAA", "PK1AAAA", "GC2BBBB", "GC3CCCC"]
    assert _log_ids(gpx) == ["-2", "1003"]
    assert '<bounds minlat="46.500000" minlon="7.500000" maxlat="47.200000" maxlon="8.200000"' in gpx


def test_reimport_skips_the_user_note_log(src_db, tmp_path):
    out = tmp_path / "out.gpx"
    write_export_file(src_db, out, "gpx", content=GpxContent(user_note_log=True))

    init_db(db_path=tmp_path / "dst.db")
    with get_session() as s:
        import_gpx(out, s)
    with get_session() as s:
        one = s.query(Cache).filter_by(gc_code="GC1AAAA").one()
        assert sorted(l.log_id for l in one.logs) == ["1001", "1002", "1003"]
        assert one.user_note.note == "Solved: N 47"
        # Importing it a second time still adds nothing.
        import_gpx(out, s)
    with get_session() as s:
        one = s.query(Cache).filter_by(gc_code="GC1AAAA").one()
        assert len(one.logs) == 3


def test_gsak_note_log_with_other_text_stays_a_log(src_db, tmp_path):
    # GSAK writes other notes of its own in the same form: those are logs.
    gpx = generate_gpx(reload_caches_full(src_db), content=GpxContent(user_note_log=True))
    gpx = gpx.replace("<groundspeak:text encoded=\"False\">Solved: N 47</groundspeak:text>",
                      "<groundspeak:text encoded=\"False\">Certitude: OK</groundspeak:text>")
    out = tmp_path / "gsak.gpx"
    out.write_text(gpx, encoding="utf-8")
    init_db(db_path=tmp_path / "dst.db")
    with get_session() as s:
        import_gpx(out, s)
    with get_session() as s:
        one = s.query(Cache).filter_by(gc_code="GC1AAAA").one()
        assert "Certitude: OK" in [l.text for l in one.logs]


def test_gpx_1_1_export_imports_back(src_db, tmp_path):
    out = tmp_path / "out.gpx"
    write_export_file(src_db, out, "gpx", content=GpxContent(version="1.1"))
    init_db(db_path=tmp_path / "dst.db")
    with get_session() as s:
        result = import_gpx(out, s)
    assert result.created == 3
    with get_session() as s:
        one = s.query(Cache).filter_by(gc_code="GC1AAAA").one()
        assert one.name == "One"
        assert sorted(w.prefix for w in one.waypoints) == ["PK", "S1"]
        assert len(one.logs) == 3


# ── settings ──────────────────────────────────────────────────────────────────

class TestSettings:
    def test_round_trip_and_content(self):
        settings = FileExportSettings(
            max_counts="waypoints", children="unflagged", children_only=True,
            max_logs=3, my_logs_always=True, user_note_log=True, gpx_version="1.1",
        )
        assert FileExportSettings.from_dict(settings.to_dict()) == settings
        assert settings.content() == GpxContent(
            children="unflagged", children_only=True, max_logs=3,
            my_logs_always=True, user_note_log=True, version="1.1",
        )

    def test_old_settings_load_with_defaults(self):
        old = {"fmt": "ggz", "use_corrected_coords": False, "max_records": 10}
        settings = FileExportSettings.from_dict(old)
        assert settings.content() == DEFAULT_CONTENT
        assert settings.max_counts == "caches"

    def test_invalid_values_fall_back(self):
        settings = FileExportSettings.from_dict({
            "max_counts": "x", "children": 1, "children_only": "yes",
            "max_logs": -3, "my_logs_always": None, "user_note_log": 1, "gpx_version": 1.1,
        })
        assert settings == FileExportSettings()


# ── dialogs ───────────────────────────────────────────────────────────────────

class TestFileExportDialog:
    def test_settings_round_trip(self, qtbot, src_db):
        from opensak.gui.dialogs.file_export_dialog import FileExportDialog

        dlg = FileExportDialog(src_db)
        qtbot.addWidget(dlg)
        wanted = FileExportSettings(
            max_records=3, max_counts="waypoints", children="flagged",
            max_logs=2, my_logs_always=True, user_note_log=True, gpx_version="1.1",
        )
        dlg._apply_settings(wanted)
        got = dlg._collect_settings()
        for key in ("max_records", "max_counts", "children", "children_only", "max_logs",
                    "my_logs_always", "user_note_log", "gpx_version"):
            assert getattr(got, key) == getattr(wanted, key), key
        assert dlg._content() == wanted.content()
        # 3 waypoints, flagged children: GC1AAAA (2) + GC2BBBB (1) fit.
        assert [c.gc_code for c in dlg._select()] == ["GC1AAAA", "GC2BBBB"]

    def test_options_follow_the_format(self, qtbot):
        from opensak.gui.dialogs.file_export_dialog import FileExportDialog

        dlg = FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        dlg._apply_settings(FileExportSettings())
        assert dlg._content_group.isEnabled()
        assert dlg._chk_children_only.isEnabled()
        assert not dlg._chk_my_logs.isEnabled()          # no log limit yet
        dlg._spin_max_logs.setValue(2)
        assert dlg._chk_my_logs.isEnabled()
        dlg._btn_ggz.setChecked(True)
        assert dlg._content_group.isEnabled()
        assert not dlg._chk_children_only.isEnabled()    # GGZ index needs the caches
        dlg._btn_loc.setChecked(True)
        assert not dlg._content_group.isEnabled()
        dlg._btn_gpx.setChecked(True)
        dlg._combo_children.setCurrentIndex(dlg._combo_children.findData("none"))
        assert not dlg._chk_children_only.isEnabled()


def test_gps_export_uses_the_settings_content(src_db, tmp_path):
    from opensak.gui.dialogs.gps_dialog import ExportWorker

    settings = FileExportSettings(children="none", max_logs=1, gpx_version="1.1",
                                  max_counts="waypoints")
    worker = ExportWorker(src_db, tmp_path, "gps", max_caches=2, settings=settings)
    worker.run()
    gpx = (tmp_path / "gps.gpx").read_text(encoding="utf-8")
    assert _names(gpx, GPX11) == ["GC1AAAA", "GC2BBBB"]
    assert _log_ids(gpx) == ["1003"]


@pytest.fixture
def gps_dialog_env(monkeypatch, tmp_path):
    """No device; export settings and the settings store in tmp_path."""
    from opensak.export.file_export_settings import FileExportProfile
    import opensak.gui.dialogs.gps_dialog as gps_dialog

    monkeypatch.setattr("opensak.gps.garmin.find_garmin_devices", lambda: [])
    monkeypatch.setattr(FileExportProfile, "default_dir", classmethod(lambda cls: tmp_path))
    store: dict = {}
    monkeypatch.setattr(gps_dialog, "get_store", lambda: SimpleNamespace(
        get=lambda key, default=None: store.get(key, default),
        set=lambda key, value: store.__setitem__(key, value),
    ))
    return store


def test_gps_dialog_defaults_to_the_old_behaviour(qtbot, gps_dialog_env):
    # A file export with other options must not change Send to GPS by itself.
    from opensak.export.file_export_settings import FileExportProfile
    from opensak.gui.dialogs.gps_dialog import GpsExportDialog

    FileExportProfile.save_last_used(FileExportSettings(use_corrected_coords=False, max_logs=7))
    dlg = GpsExportDialog(caches=[])
    qtbot.addWidget(dlg)
    assert dlg._content_combo.currentData() == ""
    # The defaults, without GSAK's own fields (#1169).
    assert dlg._content_settings() == FileExportSettings(gsak_fields=False)


def test_gps_dialog_offers_the_saved_settings(qtbot, gps_dialog_env):
    from opensak.export.file_export_settings import FileExportProfile
    from opensak.gui.dialogs.gps_dialog import GpsExportDialog

    FileExportProfile("c:geo", FileExportSettings(max_logs=2)).save()
    FileExportProfile.save_last_used(FileExportSettings(max_logs=7))
    dlg = GpsExportDialog(caches=[])
    qtbot.addWidget(dlg)
    combo = dlg._content_combo
    assert [combo.itemData(i) for i in range(combo.count())] == ["", "last:", "profile:c:geo"]
    combo.setCurrentIndex(combo.findData("last:"))
    assert dlg._content_settings().max_logs == 7
    combo.setCurrentIndex(combo.findData("profile:c:geo"))
    assert dlg._content_settings().max_logs == 2
    # The choice is remembered for the next time.
    assert gps_dialog_env["gps.content_settings"] == "profile:c:geo"
    dlg2 = GpsExportDialog(caches=[])
    qtbot.addWidget(dlg2)
    assert dlg2._content_combo.currentData() == "profile:c:geo"
    # A setting deleted meanwhile falls back to the defaults.
    FileExportProfile.profile_path("c:geo").unlink()
    assert dlg2._content_settings() == FileExportSettings(gsak_fields=False)


def test_gps_dialog_opens_the_export_dialog_for_new_settings(qtbot, monkeypatch, gps_dialog_env):
    # The Edit button opens the file export dialog; a setting saved and
    # chosen there is offered and selected afterwards.
    from opensak.export.file_export_settings import FileExportProfile
    import opensak.gui.dialogs.file_export_dialog as fed
    from opensak.gui.dialogs.gps_dialog import GpsExportDialog

    opened = []

    def fake_exec(dlg):
        opened.append(dlg)
        FileExportProfile("New for GPS", FileExportSettings(max_logs=3)).save()
        dlg._load_profiles_into_combo()
        dlg._select_profile("New for GPS")
        return 0

    monkeypatch.setattr(fed.FileExportDialog, "exec", fake_exec)
    dlg = GpsExportDialog(caches=[_cache()])
    qtbot.addWidget(dlg)
    assert dlg._content_combo.findData("profile:New for GPS") == -1
    dlg._content_edit_btn.click()
    assert len(opened) == 1 and opened[0]._caches == dlg._caches
    assert dlg._content_combo.currentData() == "profile:New for GPS"
    assert dlg._content_settings().max_logs == 3


def test_gps_dialog_keeps_its_choice_when_nothing_is_chosen(qtbot, monkeypatch, gps_dialog_env):
    import opensak.gui.dialogs.file_export_dialog as fed
    from opensak.gui.dialogs.gps_dialog import GpsExportDialog

    monkeypatch.setattr(fed.FileExportDialog, "exec", lambda dlg: 0)
    dlg = GpsExportDialog(caches=[])
    qtbot.addWidget(dlg)
    dlg._content_combo.setCurrentIndex(dlg._content_combo.findData("last:"))
    dlg._content_edit_btn.click()
    assert dlg._content_combo.currentData() == "last:"

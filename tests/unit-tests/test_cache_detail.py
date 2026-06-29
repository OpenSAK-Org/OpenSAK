# tests/unit-tests/test_cache_detail.py — cache detail panel helpers.

from datetime import datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from opensak.gui import cache_detail as cd
from opensak.gui.cache_detail import CacheDetailPanel
from opensak.lang import tr
from opensak.utils.types import CoordFormat, DateFormat, TEXT_SIZE_MAP, TextSize


def _fake_settings(
    fmt: DateFormat = DateFormat.DMY,
    text_size: TextSize = TextSize.MEDIUM,
    coord_format: CoordFormat = CoordFormat.DMM,
) -> SimpleNamespace:
    return SimpleNamespace(date_format=fmt, text_size=text_size, coord_format=coord_format)


@pytest.mark.parametrize("fmt, expected", [
    (DateFormat.DMY, "25.12.2024"),
    (DateFormat.MDY, "12/25/2024"),
    (DateFormat.YMD, "2024-12-25"),
])
def test_format_date_respects_settings(monkeypatch, qapp, fmt, expected):
    # Regression for #322: dates in the detail panel were hardcoded to DMY.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings(fmt))
    result = cd._format_date(datetime(2024, 12, 25))
    assert result == expected


def test_refresh_sizes_updates_title_font(monkeypatch, qapp):
    # Regression for #371: text size change in Settings had no effect until a
    # new cache was selected because _apply_ui_sizes() was never called.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings(text_size=TextSize.SMALL))
    panel = CacheDetailPanel()
    panel.refresh_sizes()
    assert panel._title.font().pointSize() == TEXT_SIZE_MAP[TextSize.SMALL]["label"]

    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings(text_size=TextSize.LARGE))
    panel.refresh_sizes()
    assert panel._title.font().pointSize() == TEXT_SIZE_MAP[TextSize.LARGE]["label"]


def test_type_icon_cleared_on_clear(monkeypatch, qapp):
    # Regression for #286: clear() removes the type icon from the detail panel.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel.clear()
    pix = panel._type_icon_lbl.pixmap()
    assert pix is None or pix.isNull()


def test_decode_no_hint_shows_no_hint_label(qapp):
    # Regression for #324: decoding a cache with no hint showed an empty text
    # browser instead of keeping the "(no hint)" feedback visible.
    panel = CacheDetailPanel()
    panel._hint_plain = ""
    panel._hint_cipher = ""
    panel._hint_decoded = False

    panel._toggle_hint_decode()  # decode
    assert panel._hint_browser.toPlainText() != ""

    panel._toggle_hint_decode()  # encode back
    assert panel._hint_browser.toPlainText() != ""


def _fake_wp(prefix="PK", wp_type="Parking Area", name="Park here",
             lat=55.0, lon=12.0, description="", comment=""):
    return SimpleNamespace(
        prefix=prefix, wp_type=wp_type, name=name,
        latitude=lat, longitude=lon,
        description=description, comment=comment,
    )


def test_waypoints_tab_empty(monkeypatch, qapp):
    # Regression for #378: cache with no child waypoints shows the empty message.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._render_waypoints(SimpleNamespace(waypoints=[]))
    assert tr("detail_no_waypoints") in panel._wp_browser.toPlainText()
    assert panel._tabs.tabText(3) == tr("detail_tab_waypoints")


def test_waypoints_tab_count_in_title(monkeypatch, qapp):
    # Regression for #378: tab title shows count when waypoints are present.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._render_waypoints(SimpleNamespace(waypoints=[_fake_wp(), _fake_wp("SB", "Stages Begin", "Stage 1")]))
    assert panel._tabs.tabText(3) == tr("detail_tab_waypoints_count", count=2)


def test_waypoints_tab_renders_fields(monkeypatch, qapp):
    # Regression for #378: prefix, type, name, coords and description are shown.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    wp = _fake_wp(prefix="FN", wp_type="Final Location", name="The final", lat=55.1, lon=12.1, description="Dig here")
    panel._render_waypoints(SimpleNamespace(waypoints=[wp]))
    html = panel._wp_browser.toHtml()
    assert "FN" in html
    assert "Final Location" in html
    assert "The final" in html
    assert "Dig here" in html


def test_waypoints_tab_no_coords(monkeypatch, qapp):
    # Regression for #378: waypoint with missing coords shows fallback text.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    wp = _fake_wp(lat=None, lon=None)
    panel._render_waypoints(SimpleNamespace(waypoints=[wp]))
    assert tr("detail_wp_no_coords") in panel._wp_browser.toHtml()


def test_waypoints_tab_cleared_on_clear(monkeypatch, qapp):
    # Regression for #378: clear() resets the waypoints tab to its default state.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._render_waypoints(SimpleNamespace(waypoints=[_fake_wp()]))
    assert panel._tabs.tabText(3) == tr("detail_tab_waypoints_count", count=1)
    panel.clear()
    assert panel._tabs.tabText(3) == tr("detail_tab_waypoints")
    assert panel._wp_browser.toPlainText() == ""


def test_waypoints_tab_shown_signal_emits_coords(monkeypatch, qapp):
    # Regression for #393: switching to waypoints tab emits waypoints_tab_shown with coord JSON.
    import json
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._render_waypoints(SimpleNamespace(waypoints=[
        _fake_wp(prefix="PK", wp_type="Parking Area", name="Park", lat=55.1, lon=12.1),
        _fake_wp(prefix="FN", wp_type="Final Location", name="Final", lat=55.2, lon=12.2),
    ]))
    received = []
    panel.waypoints_tab_shown.connect(received.append)
    panel._tabs.setCurrentIndex(3)
    assert len(received) == 1
    data = json.loads(received[0])
    assert len(data) == 2
    prefixes = {d["prefix"] for d in data}
    assert prefixes == {"PK", "FN"}


def test_waypoints_tab_hidden_signal_on_leave(monkeypatch, qapp):
    # Regression for #393: switching away from the waypoints tab emits waypoints_tab_hidden.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._tabs.setCurrentIndex(3)
    hidden = []
    panel.waypoints_tab_hidden.connect(lambda: hidden.append(True))
    panel._tabs.setCurrentIndex(0)
    assert hidden == [True]


def test_waypoints_tab_excludes_no_coord_waypoints(monkeypatch, qapp):
    # Regression for #393: waypoints without coords are excluded from the map signal.
    import json
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._render_waypoints(SimpleNamespace(waypoints=[
        _fake_wp(lat=None, lon=None),
        _fake_wp(prefix="FN", lat=55.0, lon=12.0),
    ]))
    received = []
    panel.waypoints_tab_shown.connect(received.append)
    panel._tabs.setCurrentIndex(3)
    data = json.loads(received[0])
    assert len(data) == 1
    assert data[0]["prefix"] == "FN"


def test_waypoints_tab_shown_on_cache_change_while_active(monkeypatch, qapp):
    # Regression for #393: if the waypoints tab is already open when a new cache
    # is loaded, the map signal fires with the updated waypoints.
    import json
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._tabs.setCurrentIndex(3)
    received = []
    panel.waypoints_tab_shown.connect(received.append)
    panel._render_waypoints(SimpleNamespace(waypoints=[
        _fake_wp(prefix="SB", lat=55.5, lon=12.5),
    ]))
    assert len(received) == 1
    data = json.loads(received[0])
    assert data[0]["prefix"] == "SB"


def test_clear_emits_waypoints_hidden(monkeypatch, qapp):
    # Regression for #393: clear() emits waypoints_tab_hidden so the map removes markers.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    hidden = []
    panel.waypoints_tab_hidden.connect(lambda: hidden.append(True))
    panel.clear()
    assert hidden == [True]


# ── Notes tab tests (issue #390) ──────────────────────────────────────────────

import textwrap
from opensak.db.database import get_session, init_db
from opensak.importer import import_gpx


def _write_gpx(tmp_path, content: str):
    p = tmp_path / "test.gpx"
    p.write_text(content, encoding="utf-8")
    return p


def _minimal_gpx(gsak_ext: str = "") -> str:
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="utf-8"?>
        <gpx xmlns="http://www.topografix.com/GPX/1/0"
             xmlns:groundspeak="http://www.groundspeak.com/cache/1/0/1"
             xmlns:gsak="http://www.gsak.net/xmlv1/6"
             version="1.0" creator="GSAK">
          <wpt lat="55.0000" lon="10.0000">
            <time>2024-01-01T00:00:00</time>
            <n>GCNOTES1</n>
            <desc>Notes Cache by Owner, Traditional Cache (2/2)</desc>
            <type>Geocache|Traditional Cache</type>
            <groundspeak:cache id="1" archived="False" available="True">
              <groundspeak:name>Notes Cache</groundspeak:name>
              <groundspeak:placed_by>Owner</groundspeak:placed_by>
              <groundspeak:owner id="1">Owner</groundspeak:owner>
              <groundspeak:type>Traditional Cache</groundspeak:type>
              <groundspeak:container>Small</groundspeak:container>
              <groundspeak:difficulty>2.0</groundspeak:difficulty>
              <groundspeak:terrain>2.0</groundspeak:terrain>
              <groundspeak:country>Denmark</groundspeak:country>
              <groundspeak:state>Zealand</groundspeak:state>
              <groundspeak:encoded_hints></groundspeak:encoded_hints>
              <groundspeak:logs></groundspeak:logs>
            </groundspeak:cache>
            {gsak_ext}
          </wpt>
        </gpx>
    """)


def test_attrs_tab_exists_at_index_4(monkeypatch, qapp):
    # Regression for #417: Attributes tab is the fifth tab (index 4).
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    assert panel._tabs.tabText(4) == tr("filter_tab_attributes")


def test_notes_tab_exists_at_index_5(monkeypatch, qapp):
    # Notes tab shifted to index 5 when Attributes tab was added (#417).
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    assert panel._tabs.tabText(5) == tr("detail_tab_notes")


def test_notes_tab_loads_existing_note(monkeypatch, tmp_path, qapp):
    # When a cache with a UserNote is shown, the editor is pre-filled.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    db_path = tmp_path / "notes_load.db"
    init_db(db_path=db_path)
    gpx = _minimal_gpx("""
        <gsak:wptExtension>
          <gsak:UserNote>Pre-loaded note text.</gsak:UserNote>
        </gsak:wptExtension>
    """)
    import_gpx(_write_gpx(tmp_path, gpx), db_path)

    from opensak.db.models import Cache as CacheModel
    from sqlalchemy.orm import joinedload
    with get_session() as s:
        cache = (
            s.query(CacheModel)
            .options(
                joinedload(CacheModel.user_note),
                joinedload(CacheModel.logs),
                joinedload(CacheModel.waypoints),
                joinedload(CacheModel.attributes),
            )
            .filter_by(gc_code="GCNOTES1")
            .one()
        )
        s.expunge_all()

    panel = CacheDetailPanel()
    panel.show_cache(cache)
    assert panel._note_editor.toPlainText() == "Pre-loaded note text."


def test_notes_tab_save_roundtrip(monkeypatch, tmp_path, qapp):
    # Typing a note and calling _save_note() persists it to the DB.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    db_path = tmp_path / "notes_save.db"
    init_db(db_path=db_path)
    import_gpx(_write_gpx(tmp_path, _minimal_gpx()), db_path)

    from opensak.db.models import Cache as CacheModel
    from sqlalchemy.orm import joinedload
    with get_session() as s:
        cache = (
            s.query(CacheModel)
            .options(
                joinedload(CacheModel.user_note),
                joinedload(CacheModel.logs),
                joinedload(CacheModel.waypoints),
                joinedload(CacheModel.attributes),
            )
            .filter_by(gc_code="GCNOTES1")
            .one()
        )
        s.expunge_all()

    panel = CacheDetailPanel()
    panel.show_cache(cache)
    panel._note_editor.setPlainText("My personal note.")
    panel._save_note()

    with get_session() as s:
        cache2 = s.query(CacheModel).filter_by(gc_code="GCNOTES1").one()
        assert cache2.user_note is not None
        assert cache2.user_note.note == "My personal note."


def test_notes_tab_clear_resets_editor(monkeypatch, qapp):
    # clear() must empty the note editor.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._note_editor.setPlainText("Some text")
    panel.clear()
    assert panel._note_editor.toPlainText() == ""


# ── Type icon tests (issue #286) ──────────────────────────────────────────────

def _load_cache(tmp_path, db_suffix="icon"):
    from opensak.db.database import get_session, init_db
    from opensak.importer import import_gpx
    db_path = tmp_path / f"{db_suffix}.db"
    init_db(db_path=db_path)
    p = tmp_path / f"{db_suffix}.gpx"
    p.write_text(_minimal_gpx(), encoding="utf-8")
    import_gpx(p, db_path)
    from opensak.db.models import Cache as CacheModel
    from sqlalchemy.orm import joinedload
    with get_session() as s:
        cache = (
            s.query(CacheModel)
            .options(
                joinedload(CacheModel.user_note),
                joinedload(CacheModel.logs),
                joinedload(CacheModel.waypoints),
                joinedload(CacheModel.attributes),
            )
            .filter_by(gc_code="GCNOTES1")
            .one()
        )
        s.expunge_all()
    return cache


def test_type_icon_shown_on_show_cache(monkeypatch, tmp_path, qapp):
    # Regression for #286: a type icon is rendered before the cache name.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings(text_size=TextSize.MEDIUM))
    cache = _load_cache(tmp_path)
    panel = CacheDetailPanel()
    panel.show_cache(cache)
    pix = panel._type_icon_lbl.pixmap()
    assert pix is not None and not pix.isNull()
    expected = TEXT_SIZE_MAP[TextSize.MEDIUM]["detail_icon"]
    assert panel._type_icon_lbl.width() == expected
    assert panel._type_icon_lbl.height() == expected


def test_type_icon_resizes_on_refresh(monkeypatch, tmp_path, qapp):
    # Regression for #286: type icon tracks text-size setting changes.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings(text_size=TextSize.SMALL))
    cache = _load_cache(tmp_path, db_suffix="icon_resize")
    panel = CacheDetailPanel()
    panel.show_cache(cache)
    assert panel._type_icon_lbl.width() == TEXT_SIZE_MAP[TextSize.SMALL]["detail_icon"]

    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings(text_size=TextSize.LARGE))
    panel.refresh_sizes()
    assert panel._type_icon_lbl.width() == TEXT_SIZE_MAP[TextSize.LARGE]["detail_icon"]


# ── Attributes tab tests (issue #417) ────────────────────────────────────────

def _fake_attr(name, is_on=True, attribute_id=1):
    return SimpleNamespace(attribute_id=attribute_id, name=name, is_on=is_on)


def test_attrs_tab_empty(monkeypatch, qapp):
    # Regression for #417: cache with no attributes shows the empty message and base tab title.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._render_attributes(SimpleNamespace(attributes=[]))
    assert tr("detail_no_attrs") in panel._attr_browser.toPlainText()
    assert panel._tabs.tabText(4) == tr("filter_tab_attributes")


def test_attrs_tab_count_in_title(monkeypatch, qapp):
    # Regression for #417: tab title shows count when attributes are present.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._render_attributes(SimpleNamespace(attributes=[
        _fake_attr("Dogs allowed", attribute_id=1),
        _fake_attr("Kids", is_on=False, attribute_id=2),
    ]))
    assert panel._tabs.tabText(4) == tr("detail_tab_attrs_count", count=2)


def test_attrs_tab_renders_yes_attribute(monkeypatch, qapp):
    # Regression for #417: is_on=True attribute is shown with a check mark.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._render_attributes(SimpleNamespace(attributes=[_fake_attr("Dogs allowed", is_on=True)]))
    html = panel._attr_browser.toHtml()
    assert "Dogs allowed" in html
    assert "✓" in html


def test_attrs_tab_renders_no_attribute(monkeypatch, qapp):
    # Regression for #417: is_on=False attribute is shown with a cross mark.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._render_attributes(SimpleNamespace(attributes=[_fake_attr("Dogs allowed", is_on=False)]))
    html = panel._attr_browser.toHtml()
    assert "Dogs allowed" in html
    assert "✗" in html


def test_attrs_tab_cleared_on_clear(monkeypatch, qapp):
    # Regression for #417: clear() resets the attributes browser and tab title.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._render_attributes(SimpleNamespace(attributes=[_fake_attr("Dogs allowed")]))
    assert panel._tabs.tabText(4) == tr("detail_tab_attrs_count", count=1)
    panel.clear()
    assert panel._attr_browser.toPlainText() == ""
    assert panel._tabs.tabText(4) == tr("filter_tab_attributes")

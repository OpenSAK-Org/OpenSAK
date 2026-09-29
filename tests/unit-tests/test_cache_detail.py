# tests/unit-tests/test_cache_detail.py — cache detail panel helpers.

from datetime import datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QSizePolicy

from opensak.gui import cache_detail as cd
from opensak.gui.cache_detail import CacheDetailPanel
from opensak.lang import tr
from opensak.utils.types import CoordFormat, DateFormat, TEXT_SIZE_MAP, TextSize


def _fake_settings(
    fmt: DateFormat = DateFormat.DMY,
    text_size: TextSize = TextSize.MEDIUM,
    coord_format: CoordFormat = CoordFormat.DMM,
    default_decode_hints: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        date_format=fmt, text_size=text_size, coord_format=coord_format,
        default_decode_hints=default_decode_hints,
    )


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


def _load_cache_with_hint(monkeypatch, tmp_path, hint: str, default_decode_hints: bool):
    monkeypatch.setattr(
        cd, "get_settings",
        lambda: _fake_settings(default_decode_hints=default_decode_hints),
    )
    db_path = tmp_path / "hint.db"
    init_db(db_path=db_path)
    import_gpx(_write_gpx(tmp_path, _minimal_gpx(hint=hint)), db_path)

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
                joinedload(CacheModel.trackables),
            )
            .filter_by(gc_code="GCNOTES1")
            .one()
        )
        s.expunge_all()

    panel = CacheDetailPanel()
    panel.show_cache(cache)
    return panel


def test_hint_defaults_hidden_when_setting_off(monkeypatch, tmp_path, qapp):
    # Issue #499: default (unchanged) behaviour — hints start hidden.
    panel = _load_cache_with_hint(
        monkeypatch, tmp_path,
        hint="Under the big rock near the old oak tree by the river",
        default_decode_hints=False,
    )
    assert panel._hint_decoded is False
    assert panel._decode_btn.text() == tr("detail_decode_btn")
    assert panel._hint_browser.toPlainText() == panel._hint_cipher

def test_hint_defaults_decoded_when_setting_on(monkeypatch, tmp_path, qapp):
    # Issue #499: with the preference enabled, hints start decoded.
    panel = _load_cache_with_hint(
        monkeypatch, tmp_path,
        hint="Under the big rock near the old oak tree by the river",
        default_decode_hints=True,
    )
    assert panel._hint_decoded is True
    assert panel._decode_btn.text() == tr("detail_encode_btn")
    assert panel._hint_browser.toPlainText() == panel._hint_plain


def test_hint_br_markup_shown_as_line_break(monkeypatch, tmp_path, qapp):
    # Issue #595: [br] must render as an actual line break, not literal
    # "[br]" text and definitely not its ROT13'd form "[oe]".
    panel = _load_cache_with_hint(
        monkeypatch, tmp_path,
        hint="Under the big rock near the old oak tree [br] by the river",
        default_decode_hints=True,
    )
    shown = panel._hint_browser.toPlainText()
    assert "[br]" not in shown
    assert "[oe]" not in shown
    assert "\n" in shown


def test_hint_default_decoded_with_no_hint_shows_no_hint_label(monkeypatch, tmp_path, qapp):
    # Edge case: setting is on, but the cache has no hint at all.
    panel = _load_cache_with_hint(
        monkeypatch, tmp_path, hint="", default_decode_hints=True,
    )
    assert panel._hint_decoded is True
    assert panel._hint_browser.toPlainText() == tr("detail_no_hint")


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


def _minimal_gpx(gsak_ext: str = "", hint: str = "") -> str:
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
              <groundspeak:encoded_hints>{hint}</groundspeak:encoded_hints>
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


def test_trackables_tab_exists_at_index_5(monkeypatch, qapp):
    # Issue #538/#546: Trackables tab inserted between Attributes and Notes.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    assert panel._tabs.tabText(5) == tr("col_trackables")


def test_notes_tab_exists_at_index_6(monkeypatch, qapp):
    # Notes tab shifted to index 6 when the Trackables tab was added (#538/#546).
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    assert panel._tabs.tabText(6) == tr("detail_tab_notes")


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
                joinedload(CacheModel.trackables),
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
                joinedload(CacheModel.trackables),
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
                joinedload(CacheModel.trackables),
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


# ── Trackables tab tests (issue #538/#546) ───────────────────────────────────

def _fake_trackable(name, ref=None, tracking_code=None):
    return SimpleNamespace(name=name, ref=ref, tracking_code=tracking_code)


def test_trackables_tab_empty(monkeypatch, qapp):
    # Cache with no trackables shows the empty message and base tab title.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._render_trackables(SimpleNamespace(trackables=[]))
    assert tr("detail_no_trackables") in panel._tb_browser.toPlainText()
    assert panel._tabs.tabText(5) == tr("col_trackables")


def test_trackables_tab_count_in_title(monkeypatch, qapp):
    # Tab title shows count when trackables are present.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._render_trackables(SimpleNamespace(trackables=[
        _fake_trackable("Best TB ever", ref="TBAB12CD"),
        _fake_trackable("Another Bug", ref="TB999X"),
    ]))
    assert panel._tabs.tabText(5) == tr("detail_tab_trackables_count", count=2)


def test_trackables_tab_renders_name_and_geocaching_link(monkeypatch, qapp):
    # Each trackable is shown with its name and a clickable coord.info link.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._render_trackables(SimpleNamespace(trackables=[
        _fake_trackable("Best TB ever", ref="TBAB12CD"),
    ]))
    html = panel._tb_browser.toHtml()
    assert "Best TB ever" in html
    assert "https://coord.info/TBAB12CD" in html


def test_trackables_tab_renders_without_ref(monkeypatch, qapp):
    # A trackable with no ref (shouldn't normally happen, but the parser
    # allows it) is shown without a broken/empty link.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._render_trackables(SimpleNamespace(trackables=[_fake_trackable("No Ref Bug", ref=None)]))
    html = panel._tb_browser.toHtml()
    assert "No Ref Bug" in html
    assert "coord.info" not in html


def test_trackables_tab_cleared_on_clear(monkeypatch, qapp):
    # clear() resets the trackables browser and tab title.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    panel = CacheDetailPanel()
    panel._render_trackables(SimpleNamespace(trackables=[_fake_trackable("Best TB ever", ref="TBAB12CD")]))
    assert panel._tabs.tabText(5) == tr("detail_tab_trackables_count", count=1)
    panel.clear()
    assert panel._tb_browser.toPlainText() == ""
    assert panel._tabs.tabText(5) == tr("col_trackables")


# ── _DescWebPage.acceptNavigationRequest — issue #742 ──────────────────────
#
# Regression suite for #742: selecting cache GCKAGH auto-opened a browser tab
# that played a .wav file, with no click involved. Root cause: the long
# description contained a legacy tag (e.g. <bgsound>/<embed src="...wav">)
# that Chromium can't render inline, so it tried to navigate the whole frame
# to the resource. That navigation isn't a link click, but the old logic
# treated everything except the initial page load as one and forwarded it to
# the system browser via webbrowser.open().
#
# acceptNavigationRequest() is pure logic (no instance state), so we call it
# directly on the class without constructing a real QWebEnginePage — this
# keeps the test fast and avoids spawning Chromium in CI.

from PySide6.QtCore import QUrl as _QUrl
from PySide6.QtWebEngineCore import QWebEnginePage as _QWebEnginePage

_NavType = _QWebEnginePage.NavigationType


@pytest.mark.parametrize("scheme,url", [
    ("http", "http://freepages.misc.rootsweb.com/~lukesphotos/bosun/allhands.wav"),
    ("https", "https://example.com/page.html"),
])
def test_link_clicked_opens_system_browser(monkeypatch, scheme, url):
    opened = []
    monkeypatch.setattr(cd.webbrowser, "open", lambda u: opened.append(u))
    result = cd._DescWebPage.acceptNavigationRequest(
        None, _QUrl(url), _NavType.NavigationTypeLinkClicked, True
    )
    assert result is False
    assert opened == [url]


def test_link_clicked_non_http_scheme_not_opened(monkeypatch):
    # e.g. a mailto: link in a description — don't hand arbitrary schemes to webbrowser.open
    opened = []
    monkeypatch.setattr(cd.webbrowser, "open", lambda u: opened.append(u))
    result = cd._DescWebPage.acceptNavigationRequest(
        None, _QUrl("mailto:someone@example.com"), _NavType.NavigationTypeLinkClicked, True
    )
    assert result is False
    assert opened == []


@pytest.mark.parametrize("nav_type", [
    _NavType.NavigationTypeTyped,
    _NavType.NavigationTypeRedirect,
])
def test_initial_load_and_redirect_allowed_in_page(monkeypatch, nav_type):
    # setHtml()'s own load (and its redirects) must still render inside the widget.
    opened = []
    monkeypatch.setattr(cd.webbrowser, "open", lambda u: opened.append(u))
    result = cd._DescWebPage.acceptNavigationRequest(
        None, _QUrl("https://example.com/"), nav_type, True
    )
    assert result is True
    assert opened == []


@pytest.mark.parametrize("nav_type", [
    _NavType.NavigationTypeOther,        # <embed>/<bgsound> auto-navigation lands here
    _NavType.NavigationTypeFormSubmitted,
    _NavType.NavigationTypeBackForward,
    _NavType.NavigationTypeReload,
])
def test_non_click_navigation_blocked_without_opening_browser(monkeypatch, nav_type):
    # Regression for #742: no click happened, so nothing should reach webbrowser.open(),
    # and the navigation itself must not be allowed to proceed either.
    opened = []
    monkeypatch.setattr(cd.webbrowser, "open", lambda u: opened.append(u))
    result = cd._DescWebPage.acceptNavigationRequest(
        None,
        _QUrl("http://freepages.misc.rootsweb.com/~lukesphotos/bosun/allhands.wav"),
        nav_type,
        True,
    )
    assert result is False
    assert opened == []


def test_accept_navigation_request_accepts_str_url(monkeypatch):
    # acceptNavigationRequest's url parameter can arrive as a plain str in some
    # PySide6 call paths — make sure the QUrl(str) coercion at the top still works.
    opened = []
    monkeypatch.setattr(cd.webbrowser, "open", lambda u: opened.append(u))
    result = cd._DescWebPage.acceptNavigationRequest(
        None, "https://example.com/clicked", _NavType.NavigationTypeLinkClicked, True
    )
    assert result is False
    assert opened == ["https://example.com/clicked"]


# ── Issue #755: minimum size no longer blocks the main splitter ────────────
# self._tabs (the Description/Hint/Logs/Waypoints/Attributes/Trackables/
# Notes QTabWidget) used to report its minimumSizeHint as the largest of
# its tab pages' own minimums, which propagated up into the panel's — and
# therefore the whole vertical splitter's — minimum height, blocking the
# splitter from being dragged much past that point ("stuck around the
# middle of the window"). QSizePolicy.Ignored on the vertical component
# stops that propagation without affecting normal sizing/tab switching.

class TestSplitterMinimumSize:
    _EMPTY_PANEL_MAX_MIN_HEIGHT = 160  # generous ceiling; was ~226 before the fix

    def test_empty_panel_minimum_height_is_small(self, qapp):
        panel = CacheDetailPanel()
        assert panel.minimumSizeHint().height() <= self._EMPTY_PANEL_MAX_MIN_HEIGHT

    def test_populated_panel_minimum_height_stays_small(self, qapp, tmp_path):
        # A heavily-populated cache (many attributes/logs/waypoints, long
        # description) must not push the minimum height back up — this is
        # the exact scenario that made the splitter get stuck in practice.
        from opensak.db.database import get_session, init_db
        from opensak.db.models import Cache, Log, Attribute, Waypoint
        from sqlalchemy.orm import joinedload, selectinload

        db_path = tmp_path / "test_755.db"
        init_db(db_path=db_path)
        with get_session() as s:
            c = Cache(
                gc_code="GC75500", name="A Very Long Cache Name For Testing",
                cache_type="Traditional Cache", difficulty=3.5, terrain=4.0,
                container="Regular", country="Denmark", latitude=55.0, longitude=12.0,
                short_description="Short desc " * 20, short_desc_html=False,
                long_description="Long description text. " * 200, long_desc_html=False,
                encoded_hints="Some hint text here " * 10,
                hidden_date=datetime(2020, 1, 1), placed_by="SomeOwner",
            )
            s.add(c)
            s.flush()
            for i in range(20):
                s.add(Attribute(cache_id=c.id, attribute_id=i, name=f"Attribute {i}", is_on=True))
            for i in range(30):
                s.add(Log(cache_id=c.id, log_type="Found it", finder=f"User{i}",
                           log_date=datetime(2024, 1, i % 28 + 1), text="Log text " * 5))
            for i in range(10):
                s.add(Waypoint(cache_id=c.id, prefix=f"P{i}", wp_type="Parking Area",
                                name=f"WP {i}", description="desc", comment="",
                                latitude=55.0, longitude=12.0))

        with get_session() as s:
            cache = s.query(Cache).options(
                joinedload(Cache.user_note), selectinload(Cache.logs),
                selectinload(Cache.attributes), selectinload(Cache.waypoints),
                selectinload(Cache.trackables),
            ).filter_by(gc_code="GC75500").first()

            panel = CacheDetailPanel()
            panel.show_cache(cache)
            assert panel.minimumSizeHint().height() <= self._EMPTY_PANEL_MAX_MIN_HEIGHT

    def test_tabs_minimum_size_hint_is_zero(self, qapp):
        # Direct check on the mechanism itself, so a future refactor that
        # accidentally reintroduces the tab pages' minimum-height
        # propagation fails here immediately rather than only via the
        # pricier size-measurement tests above.
        panel = CacheDetailPanel()
        assert panel._tabs.minimumSizeHint() == QSize(0, 0)

    def test_tabs_keep_expanding_vertical_policy(self, qapp):
        # Regression: an earlier version of this fix (v1.17.1) achieved the
        # small-minimum-height goal above by setting the tab widget's
        # vertical QSizePolicy to Ignored instead of overriding
        # minimumSizeHint(). QSizePolicy.Ignored doesn't carry the
        # ExpandFlag that Expanding does, so the tabs stopped being the
        # preferred recipient of any leftover vertical space in the panel's
        # QVBoxLayout — Qt spread that space across every row instead,
        # including the compact header/meta rows, producing large empty
        # grey padding around them (reported by a tester after v1.17.1).
        # The tabs must keep their normal Expanding vertical policy so they
        # still get first claim on extra space, as before v1.17.1.
        panel = CacheDetailPanel()
        policy = panel._tabs.sizePolicy()
        assert policy.verticalPolicy() == QSizePolicy.Policy.Expanding
        assert bool(policy.expandingDirections() & Qt.Orientation.Vertical)

    def test_tab_bar_remains_visible_and_switchable(self, qapp):
        # The fix must not hide the tab bar or break switching — only the
        # *minimum size contribution* changes, not actual usability.
        panel = CacheDetailPanel()
        panel.resize(400, 300)
        panel.show()
        qapp.processEvents()
        assert panel._tabs.tabBar().isVisible()
        for i in range(panel._tabs.count()):
            panel._tabs.setCurrentIndex(i)
            assert panel._tabs.currentIndex() == i
        panel.hide()

    def test_splitter_can_shrink_bottom_panel_well_past_old_minimum(self, qapp):
        # End-to-end: the same QSplitter setup mainwindow.py uses (vertical,
        # non-collapsible, detail panel at the bottom) must now allow
        # shrinking the bottom panel to well under the old ~226-260px floor.
        from PySide6.QtWidgets import QSplitter, QWidget
        from PySide6.QtCore import Qt as _Qt

        splitter = QSplitter(_Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        top = QWidget()
        top.setMinimumHeight(50)
        splitter.addWidget(top)
        panel = CacheDetailPanel()
        splitter.addWidget(panel)
        splitter.resize(600, 800)
        splitter.setSizes([400, 400])
        splitter.show()
        qapp.processEvents()

        splitter.setSizes([750, 50])
        qapp.processEvents()

        assert splitter.sizes()[1] < 150  # well under the pre-fix ~226-260px floor
        splitter.hide()


# ── Issue #803: plain-text descriptions must be HTML-escaped ────────────────

class TestDescriptionEscaping:
    def _panel_for_description(self, tmp_path, monkeypatch, *, long_description, long_desc_html):
        monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
        from opensak.db.database import get_session, init_db
        from opensak.db.models import Cache
        from sqlalchemy.orm import joinedload

        db_path = tmp_path / "test_803.db"
        init_db(db_path=db_path)
        with get_session() as s:
            s.add(Cache(
                gc_code="GC80300", name="Escaping Test", cache_type="Traditional Cache",
                latitude=55.0, longitude=12.0,
                long_description=long_description, long_desc_html=long_desc_html,
            ))

        with get_session() as s:
            cache = s.query(Cache).options(
                joinedload(Cache.user_note),
            ).filter_by(gc_code="GC80300").first()

            panel = CacheDetailPanel()
            panel.show_cache(cache)
            return panel

    def test_plain_text_stray_angle_bracket_is_escaped(self, qapp, tmp_path, monkeypatch):
        # The exact scenario from #803: a plain-text (non-HTML) description
        # containing a literal '<' must render as literal text, not be
        # interpreted as the start of a tag.
        panel = self._panel_for_description(
            tmp_path, monkeypatch,
            long_description="Watch out, N < 5 caches left in this series!",
            long_desc_html=False,
        )
        html_out = panel._desc_view.toHtml()
        assert "N &lt; 5 caches" in html_out
        # The literal text must still be readable as plain text to the user:
        assert "N < 5 caches" in panel._desc_view.toPlainText()

    def test_plain_text_ampersand_is_escaped(self, qapp, tmp_path, monkeypatch):
        panel = self._panel_for_description(
            tmp_path, monkeypatch,
            long_description="R&D cache, sponsored by Acme & Co.",
            long_desc_html=False,
        )
        assert "R&amp;D cache" in panel._desc_view.toHtml()
        assert "R&D cache" in panel._desc_view.toPlainText()

    def test_real_html_description_still_renders_as_html(self, qapp, tmp_path, monkeypatch):
        # Sanity check the fix doesn't over-escape real HTML content.
        panel = self._panel_for_description(
            tmp_path, monkeypatch,
            long_description="<p>Real <b>HTML</b> content</p>",
            long_desc_html=True,
        )
        assert "Real" in panel._desc_view.toPlainText()
        assert "HTML" in panel._desc_view.toPlainText()


# ── Corrected coordinates dialog ──────────────────────────────────────────────

def test_edit_corrected_passes_original_coords(monkeypatch, tmp_path, qapp):
    # The detail panel must pass the cache's original coordinates so the
    # dialog shows the same "Original coordinates" panel as the table and
    # Edit Cache entry points.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings())
    captured = {}

    class FakeDlg:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def exec(self):
            return False

    monkeypatch.setattr(
        "opensak.gui.dialogs.corrected_coords_dialog.CorrectedCoordsDialog", FakeDlg
    )
    panel = CacheDetailPanel()
    panel.show_cache(_load_cache(tmp_path, db_suffix="corr"))
    panel._edit_corrected_coords()

    assert captured["gc_code"] == "GCNOTES1"
    assert captured["orig_lat"] == pytest.approx(55.0)
    assert captured["orig_lon"] == pytest.approx(10.0)

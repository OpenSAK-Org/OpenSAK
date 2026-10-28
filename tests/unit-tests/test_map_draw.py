# tests/unit-tests/test_map_draw.py — map draw tool for the Line/Polygon
# filter: MapWidget's shape API and bridge, the draw/right-click menus,
# MapDrawDialog and the main window's "Filter by this shape".

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from opensak.filters.engine import FilterSet, LinePolygonFilter, NameFilter
from opensak.filters.line_polygon import parse_points_text
from opensak.gui import map_widget as mw_mod
from opensak.gui.map_widget import MapBridge, MapWidget, _parse_shape_json

SQUARE = [(55.0, 10.0), (55.0, 11.0), (56.0, 11.0), (56.0, 10.0)]


class FakePage:
    def __init__(self):
        self.js = []
        self.html = None

    def runJavaScript(self, js, cb=None):
        self.js.append(js)
        if cb is not None:
            cb(True)

    def setHtml(self, html, url=None):
        self.html = html


@pytest.fixture
def fake_settings(monkeypatch):
    s = SimpleNamespace(home_lat=55.0, home_lon=12.0, active_home_name="Home",
                        use_miles=False, map_draw_dialog_geometry=None,
                        map_cluster_radius=40, map_labels="off")
    monkeypatch.setattr("opensak.gui.settings.get_settings", lambda: s)
    monkeypatch.setattr("opensak.gui.dialogs.map_draw_dialog.get_settings", lambda: s)
    return s


@pytest.fixture
def w(qtbot, fake_settings):
    widget = MapWidget(draw_button=True)
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def ready(w):
    w._ready = True
    w._page = FakePage()
    return w


def _shape_json(mode, points):
    return json.dumps({"mode": mode, "points": [list(p) for p in points]})


# ── template ──────────────────────────────────────────────────────────────────

class TestTemplate:
    TOKENS = ("MAP_ACTIONS_ENABLED", "MAP_DRAW_BUTTON", "MAP_TIP_DRAW",
              "AUTO_FIT_CACHES", "GEOMAN_LANG")

    def test_tokens_resolved(self):
        html = MapWidget._render_map_html(55.0, 10.0)
        for token in self.TOKENS:
            assert token not in html

    def test_options(self):
        html = MapWidget._render_map_html(55.0, 10.0, map_actions=False,
                                          draw_button=True, auto_fit_caches=False)
        assert "if (false) {\n    new L.Control.MapActions()" in html
        assert "if (true) {\n            var drawBtn" in html
        assert "} else if (false && Object.keys(markers)" in html

    def test_geoman_pinned(self):
        assert "@geoman-io/leaflet-geoman-free@2." in mw_mod.MAP_HTML
        assert mw_mod.MAP_HTML.count("leaflet-geoman-free@") == 2  # css + js

    def test_geoman_lang(self, monkeypatch):
        monkeypatch.setattr(mw_mod, "current_language", lambda: "cs")
        assert 'map.pm.setLang("cz")' in MapWidget._render_map_html(0, 0)
        monkeypatch.setattr(mw_mod, "current_language", lambda: "xx")
        assert 'map.pm.setLang("en")' in MapWidget._render_map_html(0, 0)

    def test_cache_markers_ignored_by_geoman(self):
        # Geoman must never make cache pins editable/draggable.
        start = mw_mod.MAP_HTML.index("function loadCaches")
        end = mw_mod.MAP_HTML.index("\nfunction afterCachesLoaded", start)
        assert "pmIgnore: true" in mw_mod.MAP_HTML[start:end]

    def test_shape_layer_separate_from_clusters(self):
        start = mw_mod.MAP_HTML.index("function loadCaches")
        end = mw_mod.MAP_HTML.index("\nfunction afterCachesLoaded", start)
        assert "drawLayer" not in mw_mod.MAP_HTML[start:end]

    def test_right_click_skipped_while_drawing(self):
        start = mw_mod.MAP_HTML.index("map.on('contextmenu'")
        body = mw_mod.MAP_HTML[start:start + 300]
        assert "globalDrawModeEnabled()" in body


# ── bridge / parsing ──────────────────────────────────────────────────────────

class TestBridge:
    def test_bridge_shape_signal(self, qapp):
        bridge = MapBridge()
        got = []
        bridge.shape_changed.connect(got.append)
        bridge.on_shape_changed('{"mode": "line", "points": []}')
        assert got == ['{"mode": "line", "points": []}']

    def test_bridge_draw_signal(self, qapp):
        bridge = MapBridge()
        got = []
        bridge.draw_clicked.connect(lambda: got.append(True))
        bridge.on_draw_clicked()
        assert got == [True]

    @pytest.mark.parametrize("payload", [
        "not json", "[]", '{"mode": "circle", "points": []}',
        '{"mode": "line"}', '{"mode": "line", "points": [[1]]}',
        '{"mode": "line", "points": [["a", "b"]]}',
    ])
    def test_parse_rejects(self, payload):
        assert _parse_shape_json(payload) is None

    def test_parse(self):
        assert _parse_shape_json(_shape_json("polygon", SQUARE)) == ("polygon", SQUARE)

    def test_widget_reemits(self, w):
        got = []
        w.shape_changed.connect(lambda mode, points: got.append((mode, points)))
        w._bridge.on_shape_changed(_shape_json("polygon", SQUARE))
        assert got == [("polygon", SQUARE)]
        assert w.shape() == ("polygon", SQUARE)
        assert w.shape_is_valid()

    def test_widget_ignores_malformed(self, w):
        got = []
        w.shape_changed.connect(lambda *a: got.append(a))
        w._bridge.on_shape_changed("garbage")
        assert got == [] and w.shape() is None

    def test_all_points_removed(self, w):
        w._bridge.on_shape_changed(_shape_json("points", [(1.0, 2.0)]))
        got = []
        w.shape_changed.connect(lambda mode, points: got.append((mode, points)))
        w._bridge.on_shape_changed(_shape_json("points", []))
        assert got == [("", [])]
        assert w.shape() is None


# ── shape API ─────────────────────────────────────────────────────────────────

class TestShapeApi:
    def test_load_shape_ready(self, ready):
        ready.load_shape("polygon", SQUARE)
        assert ready._page.js[-1].startswith('loadShape("polygon", [[55.0, 10.0]')
        assert ready._page.js[-1].endswith(", true)")
        assert ready.shape() == ("polygon", SQUARE)

    def test_load_shape_bad_mode(self, w):
        with pytest.raises(ValueError):
            w.load_shape("circle", SQUARE)

    def test_too_few_points_not_valid(self, w):
        w.load_shape("polygon", SQUARE[:2])
        assert w.shape() is not None
        assert not w.shape_is_valid()

    def test_shape_resent_after_reload(self, w):
        # Not ready yet (or reload_map() in progress): stored, then sent once
        # Leaflet is up — with the fit the caller asked for.
        w.load_shape("line", SQUARE, fit=True)
        w._page = FakePage()
        w._on_leaflet_ready(True)
        assert any(js.startswith('loadShape("line"') and js.endswith(", true)")
                   for js in w._page.js)
        # ...and a later reload only restores it, without zooming again.
        w._ready = False
        w._page = FakePage()
        w._on_leaflet_ready(True)
        assert any(js.startswith('loadShape("line"') and js.endswith(", false)")
                   for js in w._page.js)

    def test_shape_survives_load_caches(self, ready):
        ready.load_shape("line", SQUARE)
        ready.load_caches([])
        assert ready.shape() == ("line", SQUARE)

    def test_start_draw_ready(self, ready):
        ready.start_draw("polygon")
        assert ready._page.js[-1] == 'startDraw("polygon")'

    def test_start_draw_pending(self, w):
        w.start_draw("points")
        w._page = FakePage()
        w._on_leaflet_ready(True)
        assert w._page.js[-1] == 'startDraw("points")'

    def test_start_draw_other_mode_keeps_points(self, ready):
        ready.load_shape("line", SQUARE)
        ready.start_draw("polygon")
        assert ready.shape() == ("polygon", SQUARE)

    def test_clear_shape(self, ready):
        ready.load_shape("line", SQUARE)
        got = []
        ready.shape_changed.connect(lambda mode, points: got.append((mode, points)))
        ready.clear_shape()
        assert ready._page.js[-1] == "clearShape()"
        assert ready.shape() is None
        assert got == [("", [])]

    def test_clear_without_shape_is_silent(self, ready):
        got = []
        ready.shape_changed.connect(lambda *a: got.append(a))
        ready.clear_shape()
        assert got == []

    def test_set_draw_enabled(self, ready):
        ready.set_draw_enabled(False)
        assert ready._page.js[-1] == "setDrawEnabled(false)"

    def test_set_view(self, ready):
        ready.set_view(55.5, 10.5, 14)
        assert ready._page.js[-1] == "setView(55.5, 10.5, 14)"

    def test_methods_noop_headless(self, w):
        w.load_shape("line", SQUARE)
        w.start_draw("line")
        w.set_view(1, 2)
        w.set_draw_enabled(True)
        w.clear_shape()  # no page → nothing to run, no crash

    def test_center_used_for_initial_view(self, qtbot, fake_settings):
        widget = MapWidget(center=(47.0, 8.0))
        qtbot.addWidget(widget)
        widget._page = FakePage()
        widget.reload_map()
        assert "center: [47.0, 8.0]" in widget._page.html


# ── menus ─────────────────────────────────────────────────────────────────────

class FakeAction:
    def __init__(self, text):
        self.text = text
        self.enabled = True

    def setEnabled(self, enabled):
        self.enabled = enabled


def _fake_menu(monkeypatch, choose):
    """Patch QMenu so exec() picks the action whose text is *choose*."""
    menus = []

    class FakeMenu:
        def __init__(self, parent=None):
            self.actions = []
            menus.append(self)

        def addAction(self, text):
            action = FakeAction(text)
            self.actions.append(action)
            return action

        def addMenu(self, text):
            return FakeMenu()

        def addSeparator(self):
            pass

        def exec(self, pos=None):
            return next((a for a in self.actions if a.text == choose), None)

    monkeypatch.setattr(mw_mod, "QMenu", FakeMenu)
    return menus


class TestDrawMenu:
    def test_start_polygon(self, ready, monkeypatch):
        _fake_menu(monkeypatch, mw_mod.tr("map_draw_polygon"))
        ready._show_draw_menu()
        assert ready._page.js[-1] == 'startDraw("polygon")'

    def test_shape_actions_disabled_without_shape(self, ready, monkeypatch):
        menus = _fake_menu(monkeypatch, None)
        ready._show_draw_menu()
        state = {a.text: a.enabled for a in menus[0].actions}
        assert state[mw_mod.tr("map_draw_copy_text")] is False
        assert state[mw_mod.tr("map_draw_filter")] is False
        assert state[mw_mod.tr("map_draw_clear")] is False
        assert state[mw_mod.tr("map_draw_line")] is True

    def test_filter_disabled_for_too_few_points(self, ready, monkeypatch):
        ready.load_shape("polygon", SQUARE[:2])
        menus = _fake_menu(monkeypatch, None)
        ready._show_draw_menu()
        state = {a.text: a.enabled for a in menus[0].actions}
        assert state[mw_mod.tr("map_draw_copy_text")] is True
        assert state[mw_mod.tr("map_draw_filter")] is False

    def test_copy_as_text(self, ready, monkeypatch, qapp):
        ready.load_shape("polygon", SQUARE)
        _fake_menu(monkeypatch, mw_mod.tr("map_draw_copy_text"))
        ready._show_draw_menu()
        text = qapp.clipboard().text()
        assert text.splitlines()[0] == "# mode: polygon"
        assert parse_points_text(text) == (SQUARE, [])

    def test_filter_by_shape(self, ready, monkeypatch):
        ready.load_shape("line", SQUARE)
        got = []
        ready.filter_by_shape_requested.connect(lambda m, p: got.append((m, p)))
        _fake_menu(monkeypatch, mw_mod.tr("map_draw_filter"))
        ready._show_draw_menu()
        assert got == [("line", SQUARE)]

    def test_clear(self, ready, monkeypatch):
        ready.load_shape("line", SQUARE)
        _fake_menu(monkeypatch, mw_mod.tr("map_draw_clear"))
        ready._show_draw_menu()
        assert ready.shape() is None

    def test_right_click_menu_offers_shape_actions(self, ready, monkeypatch, fake_settings):
        from opensak.utils.types import CoordFormat
        fake_settings.coord_format = CoordFormat.DD
        ready.load_shape("line", SQUARE)
        got = []
        ready.filter_by_shape_requested.connect(lambda m, p: got.append((m, p)))
        menus = _fake_menu(monkeypatch, mw_mod.tr("map_draw_filter"))
        ready._on_map_right_click(55.5, 10.5)
        assert got == [("line", SQUARE)]
        texts = [a.text for a in menus[0].actions]
        assert texts[0].startswith(mw_mod.tr("map_ctx_copy_coords").split("{")[0])

    def test_right_click_menu_without_shape(self, ready, monkeypatch, fake_settings):
        from opensak.utils.types import CoordFormat
        fake_settings.coord_format = CoordFormat.DD
        menus = _fake_menu(monkeypatch, None)
        ready._on_map_right_click(55.5, 10.5)
        texts = [a.text for a in menus[0].actions]
        assert mw_mod.tr("map_draw_clear") not in texts


# ── active filter's shape (read-only overlay) ─────────────────────────────────

class TestFilterShape:
    def test_template_has_overlay(self):
        assert "function setFilterShape" in mw_mod.MAP_HTML
        # Read-only: Geoman must never make it editable.
        start = mw_mod.MAP_HTML.index("var FILTER_SHAPE_STYLE")
        assert "pmIgnore: true" in mw_mod.MAP_HTML[start:start + 300]

    def test_set_ready(self, ready):
        ready.set_filter_shape("polygon", SQUARE)
        assert ready._page.js[-1].startswith('setFilterShape("polygon", [[55.0, 10.0]')
        assert ready.filter_shape() == ("polygon", SQUARE)
        assert ready.shape() is None  # the editable shape is separate

    def test_unchanged_not_resent(self, ready):
        ready.set_filter_shape("line", SQUARE)
        n = len(ready._page.js)
        ready.set_filter_shape("line", list(SQUARE))
        assert len(ready._page.js) == n

    def test_remove(self, ready):
        ready.set_filter_shape("line", SQUARE)
        ready.set_filter_shape(None)
        assert ready._page.js[-1] == "setFilterShape(null, [])"
        assert ready.filter_shape() is None

    def test_remove_without_shape_is_silent(self, ready):
        ready.set_filter_shape(None)
        assert ready._page.js == []

    def test_bad_mode(self, w):
        with pytest.raises(ValueError):
            w.set_filter_shape("circle", SQUARE)

    def test_resent_after_reload(self, w):
        w.set_filter_shape("points", [(1.0, 2.0)])
        w._page = FakePage()
        w._on_leaflet_ready(True)
        assert 'setFilterShape("points", [[1.0, 2.0]])' in w._page.js

    def test_edit_filter_shape(self, ready):
        ready.set_filter_shape("polygon", SQUARE)
        got = []
        ready.shape_changed.connect(lambda m, p: got.append((m, p)))
        ready.edit_filter_shape()
        assert ready.shape() == ("polygon", SQUARE)
        assert ready._page.js[-1].startswith('loadShape("polygon"')
        assert ready._page.js[-1].endswith(", true)")
        assert got == [("polygon", SQUARE)]
        assert ready.filter_shape() == ("polygon", SQUARE)  # overlay stays

    def test_edit_without_filter_shape(self, ready):
        ready.edit_filter_shape()
        assert ready.shape() is None and ready._page.js == []

    def test_draw_menu_edit(self, ready, monkeypatch):
        menus = _fake_menu(monkeypatch, None)
        ready._show_draw_menu()
        state = {a.text: a.enabled for a in menus[0].actions}
        assert state[mw_mod.tr("map_draw_edit_filter")] is False
        ready.set_filter_shape("line", SQUARE)
        _fake_menu(monkeypatch, mw_mod.tr("map_draw_edit_filter"))
        ready._show_draw_menu()
        assert ready.shape() == ("line", SQUARE)

    def test_right_click_edit(self, ready, monkeypatch, fake_settings):
        from opensak.utils.types import CoordFormat
        fake_settings.coord_format = CoordFormat.DD
        ready.set_filter_shape("line", SQUARE)
        _fake_menu(monkeypatch, mw_mod.tr("map_draw_edit_filter"))
        ready._on_map_right_click(55.5, 10.5)
        assert ready.shape() == ("line", SQUARE)


class TestMainWindowFilterShape:
    def _update(self, filterset):
        from opensak.gui import mainwindow
        calls = []
        win = SimpleNamespace(
            _current_filterset=filterset,
            _map_widget=SimpleNamespace(
                set_filter_shape=lambda *a: calls.append(a)),
        )
        mainwindow.MainWindow._update_map_filter_shape(win)
        return calls

    def test_no_lp_filter(self):
        assert self._update(FilterSet().add(NameFilter("x"))) == [(None,)]

    def test_lp_filter(self):
        lp = LinePolygonFilter(SQUARE, "polygon", 1.0)
        assert self._update(FilterSet().add(NameFilter("x")).add(lp)) == [("polygon", SQUARE)]

    def test_nested_lp_filter(self):
        lp = LinePolygonFilter(SQUARE[:2], "line", 1.0)
        fs = FilterSet().add(FilterSet("OR").add(NameFilter("x")).add(lp))
        assert self._update(fs) == [("line", SQUARE[:2])]


# ── MapDrawDialog ─────────────────────────────────────────────────────────────

@pytest.fixture
def draw_dialog(qtbot, fake_settings):
    from opensak.gui.dialogs.map_draw_dialog import MapDrawDialog

    def make(**kw):
        dlg = MapDrawDialog(**kw)
        qtbot.addWidget(dlg)
        return dlg
    return make


def _ok(dlg):
    from PySide6.QtWidgets import QDialogButtonBox
    return dlg._buttons.button(QDialogButtonBox.StandardButton.Ok)


class TestMapDrawDialog:
    def test_existing_shape_loaded(self, draw_dialog):
        dlg = draw_dialog(mode="polygon", points=SQUARE)
        assert dlg._map.shape() == ("polygon", SQUARE)
        assert dlg._mode_buttons["polygon"].isChecked()
        assert _ok(dlg).isEnabled()
        assert dlg.mode() == "polygon" and dlg.points() == SQUARE

    def test_empty_starts_drawing(self, draw_dialog):
        dlg = draw_dialog(mode="line")
        assert dlg._map._pending_draw_mode == "line"
        assert not _ok(dlg).isEnabled()

    def test_no_map_actions_and_no_cache_fit_with_shape(self, draw_dialog):
        dlg = draw_dialog(mode="line", points=SQUARE[:2])
        assert dlg._map._map_actions is False
        assert dlg._map._auto_fit_caches is False

    def test_fit_caches_when_nothing_else(self, draw_dialog):
        assert draw_dialog()._map._auto_fit_caches is True
        assert draw_dialog(center=(55.0, 10.0))._map._auto_fit_caches is False

    def test_caches_shown(self, draw_dialog):
        caches = [SimpleNamespace(gc_code="GC1")]
        dlg = draw_dialog(caches=caches)
        assert dlg._map.loaded_caches() == caches

    def test_drawing_enables_ok(self, draw_dialog):
        dlg = draw_dialog(mode="polygon")
        dlg._map._bridge.on_shape_changed(_shape_json("polygon", SQUARE[:2]))
        assert not _ok(dlg).isEnabled()
        dlg._map._bridge.on_shape_changed(_shape_json("polygon", SQUARE))
        assert _ok(dlg).isEnabled()
        assert dlg.points() == SQUARE

    def test_mode_switch_keeps_points(self, draw_dialog):
        dlg = draw_dialog(mode="polygon", points=SQUARE)
        dlg._mode_buttons["line"].setChecked(True)
        assert dlg.mode() == "line"
        assert dlg._map.shape() == ("line", SQUARE)

    def test_mode_switch_min_points(self, draw_dialog):
        dlg = draw_dialog(mode="line", points=SQUARE[:2])
        assert _ok(dlg).isEnabled()
        dlg._mode_buttons["polygon"].setChecked(True)
        assert not _ok(dlg).isEnabled()

    def test_clear(self, draw_dialog):
        dlg = draw_dialog(mode="line", points=SQUARE)
        dlg._clear_btn.click()
        assert dlg.points() == []
        assert dlg._map.shape() is None
        assert not _ok(dlg).isEnabled()
        assert not dlg._clear_btn.isEnabled()

    def test_bad_mode_falls_back(self, draw_dialog):
        assert draw_dialog(mode="circle").mode() == "line"

    def test_geometry_saved_and_cleanup(self, draw_dialog, fake_settings):
        dlg = draw_dialog(mode="line", points=SQUARE)
        cleaned = []
        dlg._map._cleanup_webengine = lambda: cleaned.append(True)
        dlg.reject()
        assert fake_settings.map_draw_dialog_geometry
        assert cleaned == [True]
        # ...and restored next time.
        draw_dialog(mode="line")


# ── main window: "Filter by this shape" ───────────────────────────────────────

class TestFilterByMapShape:
    def _window(self, filterset):
        calls = []
        win = SimpleNamespace(
            _current_filterset=filterset,
            _trip_planner_active=lambda: False,
            _show_filter_dialog=lambda fs, name, initial_tab=None: calls.append(
                (fs, name, initial_tab)),
        )
        return win, calls

    def _run(self, win, mode, points, monkeypatch, use_miles=False):
        from opensak.gui import mainwindow
        monkeypatch.setattr(mainwindow, "get_settings",
                            lambda: SimpleNamespace(use_miles=use_miles))
        mainwindow.MainWindow._filter_by_map_shape(win, mode, points)

    def test_adds_to_active_filter(self, monkeypatch):
        active = FilterSet().add(NameFilter("park"))
        win, calls = self._window(active)
        self._run(win, "polygon", SQUARE, monkeypatch)
        [(fs, name, tab)] = calls
        assert name == "" and tab == "line_polygon"
        [kept, lp] = fs._filters
        assert isinstance(kept, NameFilter)
        assert isinstance(lp, LinePolygonFilter)
        assert lp.mode == "polygon" and lp.points == SQUARE
        assert lp.text.startswith("# mode: polygon")
        assert lp.distance_km == 1.0
        assert len(active) == 1  # the active filter itself is untouched

    def test_replaces_active_shape_keeps_distance(self, monkeypatch):
        old = LinePolygonFilter(SQUARE[:2], "line", 3.5, exclude=True)
        win, calls = self._window(FilterSet().add(old))
        self._run(win, "points", [(1.0, 2.0)], monkeypatch)
        [lp] = calls[0][0]._filters
        assert lp.mode == "points" and lp.points == [(1.0, 2.0)]
        assert lp.distance_km == 3.5 and lp.exclude is True

    def test_default_distance_in_miles(self, monkeypatch):
        win, calls = self._window(FilterSet())
        self._run(win, "line", SQUARE, monkeypatch, use_miles=True)
        [lp] = calls[0][0]._filters
        assert lp.distance_km == pytest.approx(1.60934)

    def test_keeps_mode_and_negate(self, monkeypatch):
        win, calls = self._window(FilterSet(mode="OR", negate=True))
        self._run(win, "line", SQUARE, monkeypatch)
        fs = calls[0][0]
        assert fs.mode == "OR" and fs.negate is True

    def test_too_few_points_ignored(self, monkeypatch):
        win, calls = self._window(FilterSet())
        self._run(win, "polygon", SQUARE[:2], monkeypatch)
        assert calls == []

    def test_trip_planner_blocks(self, monkeypatch):
        win, calls = self._window(FilterSet())
        warned = []
        win._trip_planner_active = lambda: True
        win._warn_trip_planner_active = lambda: warned.append(True)
        self._run(win, "line", SQUARE, monkeypatch)
        assert calls == [] and warned == [True]

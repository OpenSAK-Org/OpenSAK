# tests/unit-tests/test_map_widget.py — Leaflet map widget (headless mode).

from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from opensak.gui import map_widget as mw_mod
from opensak.gui.map_widget import MapBridge, MapWidget, TileInterceptor, _cache_pin_html


# ── fakes / helpers ───────────────────────────────────────────────────────────

class FakePage:
    # Records runJavaScript / setHtml; fires JS callbacks as if Leaflet is up.

    def __init__(self):
        self.js = []
        self.html = None

    def runJavaScript(self, js, cb=None):
        self.js.append(js)
        if cb is not None:
            cb(True)

    def setHtml(self, html, url=None):
        self.html = html


def _note(corrected=False):
    return SimpleNamespace(is_corrected=corrected, corrected_lat=55.1, corrected_lon=12.1)


def _cache(**kw):
    d = dict(gc_code="GC1", name="Name", cache_type="Traditional Cache",
             difficulty=1.5, terrain=2.0, latitude=55.0, longitude=12.0,
             found=False, dnf=False, user_note=None)
    d.update(kw)
    return SimpleNamespace(**d)


@pytest.fixture
def fake_settings(monkeypatch):
    s = SimpleNamespace(home_lat=55.0, home_lon=12.0, active_home_name="Home")
    monkeypatch.setattr("opensak.gui.settings.get_settings", lambda: s)
    return s


# ── TileInterceptor ───────────────────────────────────────────────────────────

class TestTileInterceptor:
    def test_sets_referer_for_osm(self):
        captured = []

        class Info:
            def requestUrl(self):
                return SimpleNamespace(
                    toString=lambda: "https://tile.openstreetmap.org/1/2/3.png")

            def setHttpHeader(self, k, v):
                captured.append((k, v))

        TileInterceptor().interceptRequest(Info())
        assert captured == [(b"Referer", b"https://www.openstreetmap.org/")]

    def test_ignores_other_urls(self):
        captured = []

        class Info:
            def requestUrl(self):
                return SimpleNamespace(toString=lambda: "https://example.com/x.png")

            def setHttpHeader(self, k, v):
                captured.append((k, v))

        TileInterceptor().interceptRequest(Info())
        assert captured == []


# ── module helpers ────────────────────────────────────────────────────────────

def test_cache_pin_html():
    html = _cache_pin_html("Traditional Cache", False)
    assert "data:image/svg+xml;base64," in html


# ── MapBridge ─────────────────────────────────────────────────────────────────

def test_bridge_emits(qapp):
    bridge = MapBridge()
    got = []
    bridge.cache_clicked.connect(got.append)
    bridge.on_cache_clicked("GC123")
    assert got == ["GC123"]


# ── MapWidget headless construction ───────────────────────────────────────────

class TestConstruction:
    def test_headless_no_webengine(self, qtbot):
        w = MapWidget()
        qtbot.addWidget(w)
        assert w._page is None
        assert w.is_ready() is False

    def test_bridge_wired_to_signal(self, qtbot):
        w = MapWidget()
        qtbot.addWidget(w)
        got = []
        w.cache_selected.connect(got.append)
        w._bridge.on_cache_clicked("GC9")
        assert got == ["GC9"]

    def test_run_js_noop_when_headless(self, qtbot):
        w = MapWidget()
        qtbot.addWidget(w)
        w._run_js("doStuff()")  # no page → no crash


# ── load_caches / _do_load_caches ─────────────────────────────────────────────

class TestLoadCaches:
    @pytest.fixture
    def w(self, qtbot):
        widget = MapWidget()
        qtbot.addWidget(widget)
        return widget

    def test_not_ready_defers(self, w):
        caches = [_cache()]
        w.load_caches(caches)
        assert w._pending_caches == caches
        assert w._caches == caches

    def test_ready_loads_immediately(self, w):
        w._ready = True
        w._page = FakePage()
        w.load_caches([_cache()])
        assert w._pending_caches is None
        assert any("loadCaches" in js for js in w._page.js)

    def test_do_load_skips_missing_coords_and_handles_corrected(self, w):
        w._page = FakePage()
        caches = [
            _cache(gc_code="GC_NONE", latitude=None),
            _cache(gc_code="GC_OK", found=True, user_note=_note(corrected=True)),
        ]
        w._do_load_caches(caches)
        assert any("GC_OK" in js for js in w._page.js)
        assert all("GC_NONE" not in js for js in w._page.js)


# ── ready-guarded JS methods ──────────────────────────────────────────────────

class TestJsMethods:
    @pytest.fixture
    def w(self, qtbot, fake_settings):
        widget = MapWidget()
        qtbot.addWidget(widget)
        widget._ready = True
        widget._page = FakePage()
        return widget

    def test_pan_to_cache(self, w):
        w.pan_to_cache("GC'1")
        assert any("panToCache" in js for js in w._page.js)

    def test_fit_all(self, w):
        w.fit_all()
        assert w._page.js == ["fitAllMarkers()"]

    def test_update_cache(self, w):
        w.update_cache(_cache(user_note=_note(corrected=True)))
        assert any("updateCacheMarker" in js for js in w._page.js)

    def test_pan_to_location(self, w):
        w.pan_to_location(1.0, 2.0, "Spot")
        assert any("setHomeLocation" in js for js in w._page.js)
        assert any("panToHome" in js for js in w._page.js)

    def test_pan_to_home(self, w):
        w.pan_to_home()
        assert w._page.js == ["panToHome()"]

    def test_update_home(self, w):
        w.update_home()
        assert any("setHomeLocation" in js for js in w._page.js)

    def test_show_waypoint_markers(self, w):
        import json
        wps = json.dumps([{"lat": 55.1, "lon": 12.1, "prefix": "PK", "wp_type": "Parking", "name": "P"}])
        w.show_waypoint_markers(wps)
        assert any("showWaypointMarkers" in js for js in w._page.js)

    def test_clear_waypoint_markers(self, w):
        w.clear_waypoint_markers()
        assert w._page.js == ["clearWaypointMarkers()"]

    def test_waypoint_methods_noop_when_not_ready(self, qtbot):
        # Regression for #393: waypoint marker methods no-op before map is loaded.
        widget = MapWidget()
        qtbot.addWidget(widget)
        widget._page = FakePage()
        widget.show_waypoint_markers("[]")
        widget.clear_waypoint_markers()
        assert widget._page.js == []

    def test_methods_noop_when_not_ready(self, qtbot):
        widget = MapWidget()
        qtbot.addWidget(widget)
        widget._page = FakePage()  # but _ready stays False
        widget.pan_to_cache("GC1")
        widget.fit_all()
        widget.update_cache(_cache())
        widget.pan_to_home()
        assert widget._page.js == []


# ── lifecycle: load finished / leaflet ready / reload / cleanup ────────────────

class TestLifecycle:
    @pytest.fixture
    def w(self, qtbot, fake_settings):
        widget = MapWidget()
        qtbot.addWidget(widget)
        return widget

    def test_on_load_finished_not_ok(self, w):
        w._page = FakePage()
        w._on_load_finished(False)
        assert w._page.js == []

    def test_on_load_finished_drives_ready(self, w):
        w._page = FakePage()
        pending = []
        w._pending_caches = [_cache()]
        w._pending_refresh = lambda: pending.append("refreshed")
        w._on_load_finished(True)
        assert w._ready is True
        assert pending == ["refreshed"]
        assert w._pending_caches is None

    def test_on_leaflet_ready_false(self, w):
        w._page = FakePage()
        w._on_leaflet_ready(False)
        assert w._ready is False

    def test_on_leaflet_ready_already_ready(self, w):
        w._page = FakePage()
        w._ready = True
        w._on_leaflet_ready(True)  # returns early, no crash

    def test_reload_map_headless_returns(self, w):
        cb = lambda: None
        w.reload_map(cb)  # page is None
        assert w._pending_refresh is cb

    def test_reload_map_with_page(self, w):
        w._page = FakePage()
        w._ready = True
        w.reload_map()
        assert w._ready is False
        assert w._page.html is not None

    def test_set_pending_refresh(self, w):
        cb = lambda: None
        w.set_pending_refresh(cb)
        assert w._pending_refresh is cb


class TestProductionSetup:
    # Cover the real-WebEngine wiring without spawning Chromium.

    def test_setup_with_fake_webengine(self, qtbot, fake_settings, monkeypatch):
        from PySide6.QtWidgets import QWidget

        class FakeView(QWidget):
            def setPage(self, p):
                self._p = p

        class FakeProfile:
            def setUrlRequestInterceptor(self, i):
                self.i = i

        class FakeProdPage:
            def __init__(self, profile=None):
                self.loadFinished = SimpleNamespace(connect=lambda cb: None)
                self.html = None

            def setWebChannel(self, c):
                self.channel = c

            def setHtml(self, html, url=None):
                self.html = html

            def runJavaScript(self, *a, **k):
                pass

        class FakeChannel:
            def registerObject(self, name, obj):
                self.obj = obj

        monkeypatch.setattr("opensak.gui._headless.webengine_disabled", lambda: False)
        monkeypatch.setattr(mw_mod, "QWebEngineProfile", FakeProfile)
        monkeypatch.setattr(mw_mod, "QWebEnginePage", FakeProdPage)
        monkeypatch.setattr(mw_mod, "QWebEngineView", FakeView)
        monkeypatch.setattr(mw_mod, "QWebChannel", FakeChannel)

        w = MapWidget()
        qtbot.addWidget(w)
        assert w._page is not None
        assert isinstance(w._view, FakeView)
        assert w._page.html is not None  # setHtml was called


class TestCleanup:
    def test_cleanup_headless_noop(self, qtbot):
        w = MapWidget()
        qtbot.addWidget(w)
        w._cleanup_webengine()  # page None → early return
        assert w._cleaned is False

    def test_cleanup_deletes_page_then_profile(self, qtbot):
        w = MapWidget()
        qtbot.addWidget(w)
        order = []
        w._page = SimpleNamespace(deleteLater=lambda: order.append("page"))
        w._profile = SimpleNamespace(deleteLater=lambda: order.append("profile"))
        w._view = SimpleNamespace(setPage=lambda p: order.append(("setPage", p)))
        w._cleanup_webengine()
        assert w._cleaned is True
        assert order == [("setPage", None), "page", "profile"]

    def test_cleanup_idempotent(self, qtbot):
        w = MapWidget()
        qtbot.addWidget(w)
        w._page = SimpleNamespace(deleteLater=lambda: None)
        w._profile = SimpleNamespace(deleteLater=lambda: None)
        w._view = SimpleNamespace(setPage=lambda p: None)
        w._cleanup_webengine()
        w._cleanup_webengine()  # second call short-circuits
        assert w._cleaned is True

    def test_cleanup_swallows_runtime_error(self, qtbot):
        w = MapWidget()
        qtbot.addWidget(w)

        def boom(p):
            raise RuntimeError("deleted")

        w._page = SimpleNamespace(deleteLater=lambda: None)
        w._profile = SimpleNamespace(deleteLater=lambda: None)
        w._view = SimpleNamespace(setPage=boom)
        w._cleanup_webengine()  # RuntimeError caught
        assert w._cleaned is True

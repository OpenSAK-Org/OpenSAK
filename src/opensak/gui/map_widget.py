"""
src/opensak/gui/map_widget.py — Interaktivt OSM kort via Leaflet.js + QtWebEngine.

Viser cache pins med farvekodet ikoner efter type.
Kommunikerer med Python via QWebChannel.
"""

from __future__ import annotations
import json
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot, QUrl, Qt
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineUrlRequestInterceptor, QWebEngineProfile
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWidgets import QWidget, QVBoxLayout

from opensak.db.models import Cache
from opensak.lang import tr
from opensak.utils.types import GcCode


# ── Tile request interceptor (sætter Referer header for OSM tiles) ───────────

class TileInterceptor(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info) -> None:
        url = info.requestUrl().toString()
        if "tile.openstreetmap.org" in url:
            info.setHttpHeader(b"Referer", b"https://www.openstreetmap.org/")

# ── Cache type → Leaflet marker HTML ─────────────────────────────────────────

from opensak.gui.icon_provider import get_map_pin_html as _get_pin_html


def _cache_pin_html(cache_type: str, found: bool) -> str:
    """Return Leaflet divIcon HTML for a cache — SVG ikon, smiley hvis fundet."""
    return _get_pin_html(cache_type, found=found)


# ── Python ↔ JavaScript bro ───────────────────────────────────────────────────

class MapBridge(QObject):
    """
    Eksponeres til JavaScript via QWebChannel.
    JavaScript kalder Python metoder via window.bridge.
    """
    # Signal afsendt når brugeren klikker en pin på kortet
    cache_clicked = Signal(str)   # gc_code

    @Slot(str)
    def on_cache_clicked(self, gc_code: GcCode) -> None:
        """Kaldes fra JavaScript når en pin klikkes."""
        self.cache_clicked.emit(gc_code)


# ── HTML template med Leaflet.js ──────────────────────────────────────────────

MAP_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OpenSAK Map</title>

<!-- Leaflet CSS -->
<link rel="stylesheet"
  href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>

<!-- Leaflet MarkerCluster CSS -->
<link rel="stylesheet"
  href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css"/>
<link rel="stylesheet"
  href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css"/>

<style>
  html, body, #map { height: 100%; margin: 0; padding: 0; }
  .cache-pin-corrected-ring {
    position: absolute;
    top: -3px; left: -3px;
    width: 30px; height: 30px;
    border: 3px solid #e65100;
    border-radius: 50% 50% 50% 0;
    pointer-events: none;
  }
  .cache-pin-found { opacity: 0.55; }
  .home-marker {
    width: 16px; height: 16px;
    background: #e53935;
    border-radius: 50%;
    border: 3px solid #fff;
    box-shadow: 0 1px 4px rgba(0,0,0,0.5);
  }
</style>
</head>
<body>
<div id="map"></div>

<!-- Leaflet JS -->
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

<!-- Leaflet MarkerCluster -->
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>

<!-- Qt WebChannel -->
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>

<script>
// ── Kort initialisering ───────────────────────────────────────────────────────
var map = L.map('map', {
    center: [INIT_LAT, INIT_LON],
    zoom: INIT_ZOOM,
    zoomControl: true
});

L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 19,
    crossOrigin: true
}).addTo(map);

// ── Marker cluster gruppe ─────────────────────────────────────────────────────
var clusterGroup = L.markerClusterGroup({
    maxClusterRadius: 40,
    showCoverageOnHover: false
});
map.addLayer(clusterGroup);

// ── State ─────────────────────────────────────────────────────────────────────
var markers = {};          // gc_code → marker
var homeMarker = null;
var selectedGcCode = null;
var bridge = null;

// ── WebChannel setup ──────────────────────────────────────────────────────────
new QWebChannel(qt.webChannelTransport, function(channel) {
    bridge = channel.objects.bridge;
});

// ── Hjælpefunktioner ──────────────────────────────────────────────────────────
function makePinIcon(pinHtml, found, corrected) {
    var wrapper = pinHtml;
    if (corrected) {
        wrapper = wrapper.replace('</div>', '<div class="cache-pin-corrected-ring"></div></div>');
    }
    return L.divIcon({
        className: '',
        html: wrapper,
        iconSize: [32, 32],
        iconAnchor: [16, 16],
        popupAnchor: [0, -18]
    });
}

function makeHomeIcon() {
    return L.divIcon({
        className: '',
        html: '<div class="home-marker"></div>',
        iconSize: [16, 16],
        iconAnchor: [8, 8]
    });
}

// ── Public API kaldt fra Python ───────────────────────────────────────────────

function loadCaches(cachesJson) {
    var caches = JSON.parse(cachesJson);

    // Recreate the cluster group to avoid stale internal state from clearLayers()
    map.removeLayer(clusterGroup);
    clusterGroup = L.markerClusterGroup({
        maxClusterRadius: 40,
        showCoverageOnHover: false
    });
    map.addLayer(clusterGroup);
    markers = {};

    caches.forEach(function(c) {
        if (!c.lat || !c.lon) return;

        var lat = c.corrected ? c.clat : c.lat;
        var lon = c.corrected ? c.clon : c.lon;
        var marker = L.marker([lat, lon], {
            icon: makePinIcon(c.pin_html, c.found, c.corrected),
            title: c.name + (c.corrected ? ' 📍' : '')
        });

        var coordNote = c.corrected
            ? '<br><span style="color:#e65100;font-size:11px">📍 ' + c.corrected_label + '</span>'
            : '';
        marker.bindPopup(
            '<b>' + c.gc_code + '</b><br>' +
            c.name + '<br>' +
            '<span style="color:gray">' + c.cache_type + ' D' + c.difficulty + '/T' + c.terrain + '</span>' +
            coordNote
        );

        marker.on('click', function() {
            if (bridge) bridge.on_cache_clicked(c.gc_code);
            selectMarker(c.gc_code);
        });

        markers[c.gc_code] = marker;
        clusterGroup.addLayer(marker);
    });

    // Pan/zoom efter markers er tilføjet
    if (window._panHomeAfterLoad) {
        window._panHomeAfterLoad = false;
        panToHome();
    } else if (Object.keys(markers).length > 0) {
        try {
            var bounds = clusterGroup.getBounds();
            if (bounds && bounds.isValid()) {
                map.fitBounds(bounds, {padding: [30, 30]});
            }
        } catch(e) {}
    }
}

function setHomeLocation(lat, lon, label) {
    if (homeMarker) map.removeLayer(homeMarker);
    homeMarker = L.marker([lat, lon], {
        icon: makeHomeIcon(),
        zIndexOffset: 1000,
        title: label
    }).addTo(map);
    homeMarker.bindPopup('<b>' + label + '</b>');
}

function panToCache(gcCode) {
    var marker = markers[gcCode];
    if (!marker) return;
    try {
        clusterGroup.zoomToShowLayer(marker, function() {
            try {
                map.panTo(marker.getLatLng());
                if (marker._icon) {
                    marker.openPopup();
                }
            } catch (e) {
                // marker may have been invalidated during animation
            }
        });
    } catch (e) {
        map.panTo(marker.getLatLng());
    }
    selectMarker(gcCode);
}

function selectMarker(gcCode) {
    // Reset previous
    if (selectedGcCode && markers[selectedGcCode]) {
        var prev = markers[selectedGcCode];
        var prevData = prev._cacheData;
        if (prevData) {
            prev.setIcon(makePinIcon(prevData.colour, prevData.found, prevData.corrected));
        }
    }
    selectedGcCode = gcCode;
}

function fitAllMarkers() {
    if (Object.keys(markers).length > 0) {
        try {
            var bounds = clusterGroup.getBounds();
            if (bounds && bounds.isValid()) {
                map.fitBounds(bounds, {padding: [30, 30]});
            }
        } catch (e) {
            // cluster not yet ready — skip fit
        }
    }
}

function panToHome() {
    if (homeMarker) {
        map.panTo(homeMarker.getLatLng());
        map.setZoom(12);
    }
}

function updateCacheMarker(cacheJson) {
    var c = JSON.parse(cacheJson);
    if (markers[c.gc_code]) {
        clusterGroup.removeLayer(markers[c.gc_code]);
        delete markers[c.gc_code];
    }
    if (!c.lat && !c.lon) return;

    var lat = c.corrected ? c.clat : c.lat;
    var lon = c.corrected ? c.clon : c.lon;
    var marker = L.marker([lat, lon], {
        icon: makePinIcon(c.pin_html, c.found, c.corrected),
        title: c.name + (c.corrected ? ' 📍' : '')
    });

    var coordNote = c.corrected
        ? '<br><span style="color:#e65100;font-size:11px">📍 ' + c.corrected_label + '</span>'
        : '';
    marker.bindPopup(
        '<b>' + c.gc_code + '</b><br>' +
        c.name + '<br>' +
        '<span style="color:gray">' + c.cache_type + ' D' + c.difficulty + '/T' + c.terrain + '</span>' +
        coordNote
    );

    marker.on('click', function() {
        if (bridge) bridge.on_cache_clicked(c.gc_code);
        selectMarker(c.gc_code);
    });

    markers[c.gc_code] = marker;
    clusterGroup.addLayer(marker);
    map.panTo([lat, lon]);
}
</script>
</body>
</html>
"""


# ── Map widget ────────────────────────────────────────────────────────────────

class MapWidget(QWidget):
    """
    Interaktivt OSM kort.
    Sender cache_selected signal når en pin klikkes.
    """

    cache_selected = Signal(str)   # gc_code

    def __init__(self, parent=None):
        super().__init__(parent)
        self._caches: list[Cache] = []
        self._ready = False
        self._cleaned = False
        self._pending_caches = None
        self._pending_refresh = None
        self._pending_home = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Under test (OPENSAK_DISABLE_WEBENGINE) oprettes INGEN QtWebEngine —
        # Chromium er ustabilt i headless CI og crasher (SIGTRAP). Kortet er ikke
        # genstand for e2e-testene, så vi viser en placeholder. Alle kort-metoder
        # no-op'er fordi _page er None og _ready forbliver False.
        from opensak.gui._headless import webengine_disabled
        if webengine_disabled():
            from PySide6.QtWidgets import QLabel
            self._profile = None
            self._interceptor = None
            self._page = None
            self._view = None
            self._channel = None
            self._bridge = MapBridge()
            self._bridge.cache_clicked.connect(self.cache_selected)
            self._ready = False
            layout.addWidget(QLabel("Map disabled (headless test mode)"))
            return

        # Brug en isoleret off-the-record profil til kortet så TileInterceptor
        # ikke påvirker andre QWebEngineView instanser (fx beskrivelsespanelet).
        # Dette løser et Windows 11-specifikt problem hvor den globale
        # defaultProfile interceptor blokerede setHtml() i cache_detail.py.
        # Ingen Qt-parent — vi rydder selv op i _cleanup_webengine().
        self._profile = QWebEngineProfile()
        self._interceptor = TileInterceptor()
        self._profile.setUrlRequestInterceptor(self._interceptor)

        self._page = QWebEnginePage(self._profile)
        self._view = QWebEngineView()
        self._view.setPage(self._page)

        # Sæt op WebChannel til Python ↔ JS kommunikation
        self._channel = QWebChannel()
        self._bridge = MapBridge()
        self._bridge.cache_clicked.connect(self.cache_selected)
        self._channel.registerObject("bridge", self._bridge)
        self._page.setWebChannel(self._channel)

        # Load kortet
        self._page.loadFinished.connect(self._on_load_finished)
        self._ready = False
        import time
        from opensak.gui.settings import get_settings
        s = get_settings()
        init_lat = s.home_lat
        init_lon = s.home_lon
        html = MAP_HTML.replace("INIT_LAT", str(init_lat))
        html = html.replace("INIT_LON", str(init_lon))
        html = html.replace("INIT_ZOOM", "12")
        self._page.setHtml(html, QUrl(f"qrc:///{int(time.time())}"))

        layout.addWidget(self._view)

        # Tilmeld nedlukningsoprydning så page slettes FØR profil
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._cleanup_webengine)

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            return
        # loadFinished fyres flere gange — tjek Leaflet er klar
        self._page.runJavaScript(
            "typeof L !== 'undefined' && typeof loadCaches === 'function'",
            self._on_leaflet_ready
        )

    def _on_leaflet_ready(self, ready: bool) -> None:
        if not ready or self._ready:
            return
        self._ready = True

        # Sæt home-markør (kortet er allerede centreret via INIT_LAT/LON)
        from opensak.gui.settings import get_settings
        s = get_settings()
        home_label = s.active_home_name or tr("map_home_label")
        self._run_js(f"setHomeLocation({s.home_lat}, {s.home_lon}, {json.dumps(home_label)})")

        # Indlæs ventende caches
        if self._pending_caches is not None:
            self._do_load_caches(self._pending_caches)
            self._pending_caches = None

        # Kald pending refresh callback
        if self._pending_refresh is not None:
            cb = self._pending_refresh
            self._pending_refresh = None
            cb()

    def _run_js(self, js: str) -> None:
        """Kør JavaScript i kortvisningen."""
        if self._page is None:
            return  # headless/test mode — no WebEngine
        self._page.runJavaScript(js)

    def load_caches(self, caches: list[Cache]) -> None:
        """Indlæs caches på kortet."""
        self._caches = caches
        if self._ready:
            self._do_load_caches(caches)
        else:
            self._pending_caches = caches

    def _do_load_caches(self, caches: list[Cache]) -> None:
        from opensak.gps.garmin import _effective_coords
        data = []
        for c in caches:
            if c.latitude is None or c.longitude is None:
                continue
            note = getattr(c, "user_note", None)
            has_corrected = bool(note and getattr(note, "is_corrected", False))
            eff_lat, eff_lon = _effective_coords(c)
            data.append({
                "gc_code":        c.gc_code,
                "name":           c.name or "",
                "cache_type":     c.cache_type or "",
                "difficulty":     c.difficulty or 0,
                "terrain":        c.terrain or 0,
                "lat":            c.latitude,
                "lon":            c.longitude,
                "clat":           eff_lat,
                "clon":           eff_lon,
                "corrected":      has_corrected,
                "corrected_label": tr("detail_corrected_coords"),
                "pin_html":       _cache_pin_html(c.cache_type or "", bool(c.found)),
                "found":          c.found,
            })

        json_str = json.dumps(data, ensure_ascii=False)
        # Escape backticks for JS template literal
        json_str = json_str.replace("\\", "\\\\").replace("`", "\\`")
        self._run_js(f"loadCaches(`{json_str}`)")

    def pan_to_cache(self, gc_code: GcCode) -> None:
        """Centrér kortet på en bestemt cache."""
        if self._ready:
            safe = gc_code.replace("'", "\\'")
            self._run_js(f"panToCache('{safe}')")



    def fit_all(self) -> None:
        if self._ready:
            self._run_js("fitAllMarkers()")

    def update_cache(self, cache: Cache) -> None:
        """Refresh a single cache marker without reloading the whole map."""
        if not self._ready:
            return
        from opensak.gps.garmin import _effective_coords
        note = getattr(cache, "user_note", None)
        has_corrected = bool(note and getattr(note, "is_corrected", False))
        eff_lat, eff_lon = _effective_coords(cache)
        data = {
            "gc_code":         cache.gc_code,
            "name":            cache.name or "",
            "cache_type":      cache.cache_type or "",
            "difficulty":      cache.difficulty or 0,
            "terrain":         cache.terrain or 0,
            "lat":             cache.latitude,
            "lon":             cache.longitude,
            "clat":            eff_lat,
            "clon":            eff_lon,
            "corrected":       has_corrected,
            "corrected_label": tr("detail_corrected_coords"),
            "pin_html":        _cache_pin_html(cache.cache_type or "", bool(cache.found)),
            "found":           cache.found,
        }
        json_str = json.dumps(data, ensure_ascii=False)
        json_str = json_str.replace("\\", "\\\\").replace("`", "\\`")
        self._run_js(f"updateCacheMarker(`{json_str}`)")

    def is_ready(self) -> bool:
        return self._ready

    def set_pending_refresh(self, callback) -> None:
        self._pending_refresh = callback

    def update_home(self) -> None:
        """Opdatér home-markøren på kortet."""
        from opensak.gui.settings import get_settings
        s = get_settings()
        if self._ready:
            home_label = s.active_home_name or tr("map_home_label")
            self._run_js(f"setHomeLocation({s.home_lat}, {s.home_lon}, {json.dumps(home_label)})")

    def pan_to_location(self, lat: float, lon: float, label: str) -> None:
        """Pan kortet til en specifik koordinat."""
        if self._ready:
            self._run_js(f"setHomeLocation({lat}, {lon}, {json.dumps(label)})")
            self._run_js("panToHome()")

    def reload_map(self, refresh_callback=None) -> None:
        """Genindlæs kort HTML med aktuelle koordinater."""
        self._pending_refresh = refresh_callback
        if self._page is None:
            return  # headless/test mode — no WebEngine
        # Genindlæs setHtml (koordinater hentes fra settings)
        import time
        from opensak.gui.settings import get_settings
        s = get_settings()
        init_lat = s.home_lat
        init_lon = s.home_lon
        html = MAP_HTML.replace("INIT_LAT", str(init_lat))
        html = html.replace("INIT_LON", str(init_lon))
        html = html.replace("INIT_ZOOM", "12")
        self._ready = False
        self._page.setHtml(html, QUrl(f"qrc:///{int(time.time())}"))

    def pan_to_home(self) -> None:
        if self._ready:
            self._run_js("panToHome()")

    def _cleanup_webengine(self) -> None:
        """Slet QWebEnginePage FØR QWebEngineProfile destrueres.

        The map uses a parent-less off-the-record QWebEngineProfile + QWebEnginePage.
        Without an explicit, ordered teardown they are released by Python GC in a
        nondeterministic order; when the profile is collected before its page Qt
        logs 'Release of profile requested but WebEnginePage still not deleted.
        Expect troubles!' and leaks Chromium state. Across a long e2e run that
        accumulation eventually crashes the render process (SIGTRAP).

        Must therefore run on every window close — not only QApplication.aboutToQuit
        — so each MapWidget tears its page down before its profile. Idempotent:
        connected to both closeEvent (via MainWindow) and aboutToQuit."""
        if self._cleaned or self._page is None:
            return  # already cleaned, or headless/test mode — nothing to release
        self._cleaned = True
        try:
            self._ready = False
            self._view.setPage(None)
            self._page.deleteLater()
            self._profile.deleteLater()
        except RuntimeError:
            # Widget er allerede slettet af Qt — intet at gøre
            pass

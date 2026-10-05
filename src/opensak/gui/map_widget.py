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
from PySide6.QtWidgets import QWidget, QVBoxLayout, QMenu
from PySide6.QtGui import QClipboard
from PySide6.QtWidgets import QApplication

from opensak.db.models import Cache
from opensak.lang import tr
from opensak.gui.theme import hint_text_color
from opensak.utils.types import GcCode


# ── Tile request interceptor (sætter Referer header for OSM tiles) ───────────

class TileInterceptor(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info) -> None:
        url = info.requestUrl().toString()
        if "tile.openstreetmap.org" in url:
            info.setHttpHeader(b"Referer", b"https://www.openstreetmap.org/")

# ── Cache type → Leaflet marker HTML ─────────────────────────────────────────

from opensak.gui.icon_provider import get_map_pin_html as _get_pin_html


def _cache_pin_html(cache_type: str, found: bool, dnf: bool = False) -> str:
    """Return Leaflet divIcon HTML for a cache — type icon with gold/blue smiley overlay."""
    return _get_pin_html(cache_type, found=found, dnf=dnf)


def _js_arg(value) -> str:
    """Serialise *value* as a JavaScript literal for a runJavaScript() call.

    JSON is valid JS, so the payload is passed straight as a function
    argument — no template-literal/quote splicing that imported data (cache
    names, GC codes from a crafted GPX) could break out of with ``${...}``,
    backslashes or quotes.
    """
    return json.dumps(value, ensure_ascii=False)


# ── Python ↔ JavaScript bro ───────────────────────────────────────────────────

class MapBridge(QObject):
    """
    Eksponeres til JavaScript via QWebChannel.
    JavaScript kalder Python metoder via window.bridge.
    """
    # Signal afsendt når brugeren klikker en pin på kortet
    cache_clicked = Signal(str)   # gc_code
    # Signal afsendt når brugeren højreklikker på kortet (ikke på en pin)
    map_right_clicked = Signal(float, float)   # lat, lon
    # Signals for map control buttons (maximize / pop-out)
    maximize_clicked = Signal()
    popout_clicked = Signal()

    @Slot(str)
    def on_cache_clicked(self, gc_code: GcCode) -> None:
        """Kaldes fra JavaScript når en pin klikkes."""
        self.cache_clicked.emit(gc_code)

    @Slot(float, float)
    def on_map_right_click(self, lat: float, lon: float) -> None:
        """Kaldes fra JavaScript når brugeren højreklikker på kortet."""
        self.map_right_clicked.emit(lat, lon)

    @Slot()
    def on_maximize_clicked(self) -> None:
        """Kaldes fra JavaScript når maximize-knappen klikkes."""
        self.maximize_clicked.emit()

    @Slot()
    def on_popout_clicked(self) -> None:
        """Kaldes fra JavaScript når pop-out-knappen klikkes."""
        self.popout_clicked.emit()


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
  .waypoint-marker {
    width: 22px; height: 22px;
    background: #7b1fa2;
    border-radius: 50%;
    border: 2px solid #fff;
    box-shadow: 0 1px 4px rgba(0,0,0,0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    color: #fff;
    font-size: 9px;
    font-weight: bold;
    font-family: sans-serif;
    text-align: center;
    line-height: 22px;
  }
  .leaflet-control-mapactions a {
    width: 30px;
    height: 30px;
    line-height: 30px;
    text-align: center;
    font-size: 16px;
    display: block;
    text-decoration: none;
    color: #333;
    background: #fff;
    border-bottom: 1px solid #ccc;
    cursor: pointer;
  }
  .leaflet-control-mapactions a:hover {
    background: #f4f4f4;
  }
  .leaflet-control-mapactions a:last-child {
    border-bottom: none;
  }
  .leaflet-control-nearbylabel {
    background: #fff;
    padding: 4px 10px;
    border-radius: 4px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.4);
    font-size: 11px;
    font-family: sans-serif;
    color: #333;
    white-space: nowrap;
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
    showCoverageOnHover: false,
    chunkedLoading: true,
    chunkInterval: 200,
    chunkDelay: 10
});
map.addLayer(clusterGroup);

// ── State ─────────────────────────────────────────────────────────────────────
var markers = {};          // gc_code → marker
var homeMarker = null;
var selectedGcCode = null;
var bridge = null;
var waypointMarkers = [];
var panRequestSeq = 0;     // issue #718 — see panToCache()
var nearbyCircle = null;   // issue #718 — split-screen map radius overlay
var nearbyLabelControl = null;   // issue #718 — "showing nearest X of Y" label

// ── WebChannel setup ──────────────────────────────────────────────────────────
new QWebChannel(qt.webChannelTransport, function(channel) {
    bridge = channel.objects.bridge;
});

// ── Højreklik på kortet ───────────────────────────────────────────────────────
map.on('contextmenu', function(e) {
    if (bridge) {
        bridge.on_map_right_click(e.latlng.lat, e.latlng.lng);
    }
});

// ── Map action buttons (maximize / pop-out) ──────────────────────────────────
L.Control.MapActions = L.Control.extend({
    options: { position: 'topleft' },
    onAdd: function(map) {
        var container = L.DomUtil.create('div', 'leaflet-control-mapactions leaflet-bar');

        var maxBtn = L.DomUtil.create('a', '', container);
        maxBtn.innerHTML = '⛶';
        maxBtn.title = 'MAP_TIP_MAXIMIZE';
        maxBtn.href = '#';
        maxBtn.setAttribute('role', 'button');
        L.DomEvent.disableClickPropagation(maxBtn);
        L.DomEvent.on(maxBtn, 'click', function(e) {
            L.DomEvent.preventDefault(e);
            if (bridge) bridge.on_maximize_clicked();
        });

        if (MAP_POPOUT_ENABLED) {
            var popBtn = L.DomUtil.create('a', '', container);
            popBtn.innerHTML = '⧉';
            popBtn.title = 'MAP_TIP_POPOUT';
            popBtn.href = '#';
            popBtn.setAttribute('role', 'button');
            L.DomEvent.disableClickPropagation(popBtn);
            L.DomEvent.on(popBtn, 'click', function(e) {
                L.DomEvent.preventDefault(e);
                if (bridge) bridge.on_popout_clicked();
            });
        }

        return container;
    }
});
new L.Control.MapActions().addTo(map);

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

// Cache/waypoint names, types and prefixes come from imported GPX/GSAK data
// written by cache owners — they must never reach the DOM as raw HTML.
function escapeHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function cachePopupHtml(c) {
    var coordNote = c.corrected
        ? '<br><span style="color:#e65100;font-size:11px">📍 ' + escapeHtml(c.corrected_label) + '</span>'
        : '';
    return '<b>' + escapeHtml(c.gc_code) + '</b><br>' +
        escapeHtml(c.name) + '<br>' +
        '<span style="color:HINT_TEXT_COLOR">' + escapeHtml(c.cache_type) +
        ' D' + escapeHtml(c.difficulty) + '/T' + escapeHtml(c.terrain) + '</span>' +
        coordNote;
}

// ── Public API kaldt fra Python ───────────────────────────────────────────────

// Data arguments arrive as plain JS literals (json.dumps() on the Python
// side), never as strings spliced into template literals — see _js_arg().
function loadCaches(caches) {
    // Issue #718: any call to the general load path (filter change, table
    // refresh, etc.) means we're back in overview mode — clear any
    // leftover nearby-selection circle/label from loadNearbyCaches() below,
    // so it never lingers over an unrelated marker set.
    clearNearbyOverlay();

    // Recreate the cluster group to avoid stale internal state from clearLayers()
    map.removeLayer(clusterGroup);
    clusterGroup = L.markerClusterGroup({
        maxClusterRadius: 40,
        showCoverageOnHover: false,
        chunkedLoading: true,
        chunkInterval: 200,
        chunkDelay: 10,
        chunkProgress: function(processed, total) {
            // Issue #630: chunkedLoading adds markers to the map in
            // background chunks (via requestAnimationFrame/setTimeout)
            // instead of all at once, so the post-load pan/fitBounds step
            // below can't run synchronously right after addLayers() — the
            // cluster bounds wouldn't include markers from chunks that
            // haven't been processed yet. chunkProgress fires repeatedly as
            // chunks complete; only act once every marker has been added.
            if (total > 0 && processed >= total) {
                afterCachesLoaded();
            }
        }
    });
    map.addLayer(clusterGroup);
    markers = {};

    var markerArray = [];
    caches.forEach(function(c) {
        if (!c.lat || !c.lon) return;

        var lat = c.corrected ? c.clat : c.lat;
        var lon = c.corrected ? c.clon : c.lon;
        var marker = L.marker([lat, lon], {
            icon: makePinIcon(c.pin_html, c.found, c.corrected),
            title: c.name + (c.corrected ? ' 📍' : '')
        });

        marker.bindPopup(cachePopupHtml(c));

        marker.on('click', function() {
            if (bridge) bridge.on_cache_clicked(c.gc_code);
            selectMarker(c.gc_code);
        });

        markers[c.gc_code] = marker;
        markerArray.push(marker);
    });

    // Issue #630: bulk-add via addLayers() instead of calling addLayer() once
    // per marker in the loop above. Leaflet.markercluster rebuilds its
    // spatial index on every single addLayer() call, which is dramatically
    // slower than the library's own bulk API at large marker counts — the
    // library's own recommendation for exactly this case.
    if (markerArray.length > 0) {
        clusterGroup.addLayers(markerArray);
    } else {
        // Nothing to chunk-load, so chunkProgress above will never fire —
        // run the pan/fitBounds step (or lack thereof) immediately instead.
        afterCachesLoaded();
    }
}

function afterCachesLoaded() {
    // Pan/zoom once every marker has actually been added to the map.
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
    homeMarker.bindPopup('<b>' + escapeHtml(label) + '</b>');
}

function panToCache(gcCode) {
    var marker = markers[gcCode];
    if (!marker) return;
    selectMarker(gcCode);

    // Issue #718 (Mike, Mac ARM, beta.7): selecting a new cache in the grid
    // updates the detail text but the map sometimes doesn't pan/pop up,
    // ~60% of the time. Root cause: Leaflet.markercluster's
    // zoomToShowLayer() registers a one-shot moveend/zoomend listener and
    // resolves it via that listener — but if this function is called again
    // (a new cache selected) before the previous call's listener has fired,
    // the plugin's internal bookkeeping can drop one of the two callbacks
    // silently (a known upstream race, worse here because WebEngine's
    // animation timing makes back-to-back selections more likely to
    // overlap — matches "fails about 60%+ of the time" on rapid grid
    // clicking). panRequestSeq ensures only the most recently requested
    // pan actually moves the map (a stale callback becomes a no-op instead
    // of yanking the view back to an old cache), and the setTimeout is a
    // safety net for when zoomToShowLayer's callback never fires at all.
    var mySeq = ++panRequestSeq;
    var doPan = function() {
        if (mySeq !== panRequestSeq) return;  // superseded by a later selection
        try {
            map.panTo(marker.getLatLng());
            if (marker._icon) {
                marker.openPopup();
            }
        } catch (e) {
            // marker may have been invalidated during animation
        }
    };
    try {
        clusterGroup.zoomToShowLayer(marker, doPan);
        setTimeout(doPan, 400);
    } catch (e) {
        doPan();
    }
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

// ── Issue #718: split-screen "nearby caches" view ────────────────────────────
//
// Called when a cache is selected in the table/detail panel — replaces
// whatever marker set is currently loaded with just that cache's
// neighbourhood (within radius_km, see get_nearby_caches() in
// filters/engine.py), draws a circle at radius_km so the user can see
// exactly where the view ends, and shows an optional "showing nearest X
// of Y" label when max_caches actually capped the result. Any subsequent
// call to loadCaches() (a normal overview refresh) clears this overlay —
// see the clearNearbyOverlay() call at the top of loadCaches() above.
function loadNearbyCaches(caches, centerLat, centerLon, radiusKm, gcCode, labelText) {
    loadCaches(caches);
    drawNearbyCircle(centerLat, centerLon, radiusKm);
    updateNearbyLabel(labelText);
    panToCache(gcCode);
}

function drawNearbyCircle(lat, lon, radiusKm) {
    clearNearbyCircle();
    if (!(radiusKm > 0)) return;
    nearbyCircle = L.circle([lat, lon], {
        radius: radiusKm * 1000,
        color: '#1976d2',
        weight: 2,
        fillColor: '#1976d2',
        fillOpacity: 0.05,
        interactive: false
    }).addTo(map);
}

function clearNearbyCircle() {
    if (nearbyCircle) {
        map.removeLayer(nearbyCircle);
        nearbyCircle = null;
    }
}

function updateNearbyLabel(labelText) {
    clearNearbyLabel();
    if (!labelText) return;   // cap not reached — circle alone is the indicator
    var Label = L.Control.extend({
        options: { position: 'bottomleft' },
        onAdd: function() {
            var div = L.DomUtil.create('div', 'leaflet-control-nearbylabel');
            div.textContent = labelText;
            return div;
        }
    });
    nearbyLabelControl = new Label().addTo(map);
}

function clearNearbyLabel() {
    if (nearbyLabelControl) {
        map.removeControl(nearbyLabelControl);
        nearbyLabelControl = null;
    }
}

function clearNearbyOverlay() {
    clearNearbyCircle();
    clearNearbyLabel();
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

function clearWaypointMarkers() {
    waypointMarkers.forEach(function(m) { map.removeLayer(m); });
    waypointMarkers = [];
}

function showWaypointMarkers(wps) {
    clearWaypointMarkers();
    wps.forEach(function(wp) {
        // Issue #546: hidden/unset coordinates come through as 0/0 (e.g.
        // finale waypoints with hidden coords after a GSAK import) — a
        // marker at null-island is never meaningful, and including it in
        // the bounds below made the map zoom out to show the whole world
        // instead of the cache's actual waypoints.
        if (!wp.lat || !wp.lon) return;
        var prefix = escapeHtml(wp.prefix);
        var icon = L.divIcon({
            className: '',
            html: '<div class="waypoint-marker">' + prefix + '</div>',
            iconSize: [22, 22],
            iconAnchor: [11, 11],
            popupAnchor: [0, -13]
        });
        var label = '[' + wp.prefix + '] ' + (wp.wp_type || '');
        var popup = '<b>[' + prefix + ']</b> ' + escapeHtml(wp.wp_type) + (wp.name ? '<br>' + escapeHtml(wp.name) : '');
        var m = L.marker([wp.lat, wp.lon], {icon: icon, title: label});
        m.bindPopup(popup);
        m.addTo(map);
        waypointMarkers.push(m);
    });
    if (waypointMarkers.length > 0) {
        try {
            var group = L.featureGroup(waypointMarkers);
            map.fitBounds(group.getBounds().pad(0.5));
        } catch(e) {}
    }
}

function updateCacheMarker(c) {
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

    marker.bindPopup(cachePopupHtml(c));

    marker.on('click', function() {
        if (bridge) bridge.on_cache_clicked(c.gc_code);
        selectMarker(c.gc_code);
    });

    markers[c.gc_code] = marker;
    clusterGroup.addLayer(marker);
    // Issue #474: a plain map.panTo() here only moves the view centre —
    // if the (possibly far-away) new location sits inside an unopened
    // cluster at the current zoom, the pin stays invisible. Reuse
    // panToCache()'s clusterGroup.zoomToShowLayer() reveal so the marker
    // is actually visible, exactly as when selecting a cache in the table.
    panToCache(c.gc_code);
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
    set_corrected_requested = Signal(str, float, float)  # gc_code, lat, lon
    maximize_requested = Signal()
    popout_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._caches: list[Cache] = []
        self._ready = False
        self._cleaned = False
        self._pending_caches = None
        self._pending_refresh = None
        self._pending_home = None
        self._last_selected_gc: str = ""   # senest valgte cache via pin-klik
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
            self._bridge.maximize_clicked.connect(self.maximize_requested)
            self._bridge.popout_clicked.connect(self.popout_requested)
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
        self._bridge.map_right_clicked.connect(self._on_map_right_click)
        self._bridge.maximize_clicked.connect(self.maximize_requested)
        self._bridge.popout_clicked.connect(self.popout_requested)
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
        html = self._render_map_html(init_lat, init_lon)
        self._page.setHtml(html, QUrl(f"qrc:///{int(time.time())}"))

        layout.addWidget(self._view)

        # Tilmeld nedlukningsoprydning så page slettes FØR profil
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._cleanup_webengine)

    def _on_load_finished(self, ok: bool) -> None:
        if not ok or self._page is None:
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
        self._run_js(f"setHomeLocation({s.home_lat}, {s.home_lon}, {_js_arg(home_label)})")

        # Indlæs ventende caches
        if self._pending_caches is not None:
            self._do_load_caches(self._pending_caches)
            self._pending_caches = None

        # Kald pending refresh callback
        if self._pending_refresh is not None:
            cb = self._pending_refresh
            self._pending_refresh = None
            cb()

    @staticmethod
    def _render_map_html(lat: float, lon: float) -> str:
        """Render MAP_HTML template with dynamic values and localized tooltips."""
        import opensak.utils.flags as flags
        html = MAP_HTML.replace("INIT_LAT", str(lat))
        html = html.replace("INIT_LON", str(lon))
        html = html.replace("INIT_ZOOM", "12")
        html = html.replace("MAP_TIP_MAXIMIZE", tr("toolbar_maximize_map_tooltip"))
        html = html.replace("MAP_TIP_POPOUT", tr("toolbar_popout_map_tooltip"))
        html = html.replace("MAP_POPOUT_ENABLED", "true" if flags.map_popout else "false")
        # Issue #806: HINT_TEXT_COLOR is a template token, not a JS symbol —
        # it must be resolved to a literal hex value here in Python before
        # the HTML is handed to QtWebEngine, same as the other INIT_/MAP_
        # tokens above. Calling hint_text_color() directly inside the JS
        # source (as opposed to here) throws "hint_text_color is not
        # defined", since that function only exists on the Python side.
        html = html.replace("HINT_TEXT_COLOR", hint_text_color())
        return html

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

    def _build_marker_data(self, caches: list[Cache]) -> list[dict]:
        """Shared marker-payload builder for load_caches()/show_nearby_for_selection()
        — see #718's docstring on show_nearby_for_selection() for why the
        latter needed this factored out rather than duplicated."""
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
                "pin_html":       _cache_pin_html(c.cache_type or "", bool(c.found), bool(c.dnf)),
                "found":          c.found,
            })
        return data

    def _do_load_caches(self, caches: list[Cache]) -> None:
        data = self._build_marker_data(caches)
        self._run_js(f"loadCaches({_js_arg(data)})")

    def show_nearby_for_selection(
        self,
        cache: Cache,
        nearby_caches: list[Cache],
        radius_km: float,
        label_text: str,
    ) -> None:
        """Issue #718: replace the currently-loaded marker set with just
        *cache*'s neighbourhood (already computed by
        filters.engine.get_nearby_caches() and passed in as
        *nearby_caches*) — independent of the overview map's own
        map_max_caches cap. Draws a radius circle at radius_km around
        *cache* so the user can see exactly where the view ends (Mike
        Wood's suggestion on #718), and shows *label_text* (pre-formatted
        by the caller, e.g. "Showing nearest 500 of 1,240 within 2 km")
        only when it's non-empty — the caller passes "" when max_caches
        didn't actually cap the result, so the circle alone is the
        indicator in the common case, matching the agreed UX.

        Issue #748: the circle is centred on *cache*'s effective_coords()
        (corrected coordinates when set) rather than its raw
        latitude/longitude, matching where markers are actually plotted
        (loadCaches() JS: `c.corrected ? c.clat : c.lat`) — a cache with
        corrected coordinates set previously got a circle centred on its
        posted location while its own marker (and any neighbours') could
        render at a corrected position outside that circle.

        Requires the map to already be ready/loaded — unlike load_caches(),
        this deliberately does not queue itself for the not-yet-ready case:
        selecting a cache before the map has finished loading is an edge
        case not worth the extra state, and the general load_caches()
        pending-queue path already covers the startup race for the
        overview map that matters in practice.
        """
        if not self._ready:
            return
        from opensak.filters.engine import effective_coords
        center_lat, center_lon = effective_coords(cache)
        if center_lat is None or center_lon is None:
            return
        data = self._build_marker_data(nearby_caches)
        self._run_js(
            f"loadNearbyCaches({_js_arg(data)}, {center_lat}, {center_lon}, "
            f"{radius_km}, {_js_arg(cache.gc_code)}, {_js_arg(label_text)})"
        )

    def pan_to_cache(self, gc_code: GcCode) -> None:
        """Centrér kortet på en bestemt cache."""
        if self._ready:
            self._run_js(f"panToCache({_js_arg(gc_code)})")



    def show_waypoint_markers(self, waypoints_json: str) -> None:
        """Render child waypoint markers on the map (called when Waypoints tab is activated)."""
        if not self._ready:
            return
        # Round-trip through json so only well-formed JSON reaches the page.
        self._run_js(f"showWaypointMarkers({_js_arg(json.loads(waypoints_json))})")

    def clear_waypoint_markers(self) -> None:
        """Remove all waypoint markers (called when Waypoints tab is left)."""
        if self._ready:
            self._run_js("clearWaypointMarkers()")

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
            "pin_html":        _cache_pin_html(cache.cache_type or "", bool(cache.found), bool(cache.dnf)),
            "found":           cache.found,
        }
        self._run_js(f"updateCacheMarker({_js_arg(data)})")

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
            self._run_js(f"setHomeLocation({s.home_lat}, {s.home_lon}, {_js_arg(home_label)})")

    def pan_to_location(self, lat: float, lon: float, label: str) -> None:
        """Pan kortet til en specifik koordinat."""
        if self._ready:
            self._run_js(f"setHomeLocation({lat}, {lon}, {_js_arg(label)})")
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
        html = self._render_map_html(init_lat, init_lon)
        self._ready = False
        self._page.setHtml(html, QUrl(f"qrc:///{int(time.time())}"))

    def pan_to_home(self) -> None:
        if self._ready:
            self._run_js("panToHome()")

    def set_active_cache(self, gc_code: str) -> None:
        """Sæt den aktuelt valgte cache — bruges af højreklik-menuen til
        at vide hvilken cache korrigerede koordinater skal sættes på.
        Kaldes fra mainwindow når cache-valg ændres i liste eller på kortet."""
        self._last_selected_gc = gc_code

    def _on_map_right_click(self, lat: float, lon: float) -> None:
        """Vis kontekstmenu når brugeren højreklikker på kortet."""
        from opensak.coords import format_coords, FORMATS
        from opensak.utils.types import CoordFormat
        from opensak.gui.settings import get_settings
        from opensak.lang import tr

        s = get_settings()
        preferred_fmt = s.coord_format

        menu = QMenu(self)

        # ── Kopier koordinater (foretrukket format øverst) ────────────────────
        preferred_str = format_coords(lat, lon, preferred_fmt)
        act_preferred = menu.addAction(
            tr("map_ctx_copy_coords").format(coords=preferred_str)
        )

        # ── Undermenu: kopier i andre formater ────────────────────────────────
        other_formats = [f for f in CoordFormat if f != preferred_fmt]
        sub = menu.addMenu(tr("map_ctx_copy_as"))
        sub_actions = {}
        for fmt in other_formats:
            fmt_str = format_coords(lat, lon, fmt)
            act = sub.addAction(fmt_str)
            sub_actions[act] = fmt_str

        menu.addSeparator()

        # ── Sæt korrigerede koordinater ───────────────────────────────────────
        act_corrected = menu.addAction(tr("map_ctx_set_corrected"))
        # Kun aktiv hvis en cache er valgt
        has_selected = bool(self._bridge and
                            hasattr(self, '_last_selected_gc') and
                            self._last_selected_gc)
        act_corrected.setEnabled(has_selected)

        # ── Vis menuen ved musemarkøren ───────────────────────────────────────
        from PySide6.QtGui import QCursor
        chosen = menu.exec(QCursor.pos())

        if chosen == act_preferred:
            QApplication.clipboard().setText(preferred_str)
        elif chosen in sub_actions:
            QApplication.clipboard().setText(sub_actions[chosen])
        elif chosen == act_corrected and has_selected:
            self._set_corrected_from_map(lat, lon)

    def _set_corrected_from_map(self, lat: float, lon: float) -> None:
        """Sæt korrigerede koordinater på den valgte cache."""
        gc_code = getattr(self, '_last_selected_gc', None)
        if not gc_code:
            return
        # Emit signal så mainwindow kan håndtere DB-skrivning
        self.set_corrected_requested.emit(gc_code, lat, lon)

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
        # _view/_page/_profile sættes altid samtidig i webengine-tilstand
        assert self._view is not None and self._profile is not None
        try:
            self._ready = False
            self._view.setPage(None)  # type: ignore[arg-type]
            self._page.deleteLater()
            self._profile.deleteLater()
        except RuntimeError:
            # Widget er allerede slettet af Qt — intet at gøre
            pass

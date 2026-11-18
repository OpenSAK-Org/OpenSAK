"""
map_layers.py — The base maps the map can show (#747).

Each MapLayer is one tile source. Settings → Map lets the user switch every
layer on or off; the map offers the switched-on ones in Leaflet's layer
switcher and remembers the one last chosen.

Only sources whose terms allow use in an app like OpenSAK are listed:

* Keyless: OpenStreetMap and OSM-based community maps (fair use, attribution)
  and national open-government-data services (open licences, attribution).
* With the user's own API key (Esri, MapTiler, Thunderforest): the user holds
  the agreement with the provider. A keyed layer is offered only once its key
  is entered.

Google and Bing tiles are deliberately absent — their terms forbid direct
tile access outside their own SDKs/APIs.

Offline (#1111): tiles normally go through the shared map profile's disk
HTTP cache (map_profile.py), which follows each provider's own cache headers.
A layer with offline=False comes from a provider whose terms do not allow
keeping tiles on the device (Esri: caching only while connected). Its tiles
are fetched through opensak://tiles/<id>/z/x/y (web_assets.py) instead —
Chromium keeps custom-scheme responses out of its HTTP cache, so they never
reach the disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from opensak.lang import tr

# Kinds, shown after the provider name ("swisstopo – Aerial imagery").
KIND_MAP = "map"
KIND_TOPO = "topo"
KIND_AERIAL = "aerial"

_KIND_KEYS = {
    KIND_MAP: "settings_tab_map",   # "Map" — same word as the settings tab
    KIND_TOPO: "map_layer_kind_topo",
    KIND_AERIAL: "map_layer_kind_aerial",
}

# Providers whose layers need the user's own API key, with the page where a
# key is obtained (shown in Settings → Map).
KEY_PROVIDERS: dict[str, tuple[str, str]] = {
    # id: (display name, sign-up URL)
    "esri": ("Esri ArcGIS", "https://location.arcgis.com/"),
    "maptiler": ("MapTiler", "https://cloud.maptiler.com/"),
    "thunderforest": ("Thunderforest", "https://manage.thunderforest.com/"),
}

DEFAULT_LAYER = "osm"
# Switched on for a new installation; everything else is opt-in.
DEFAULT_ENABLED = ("osm", "opentopomap")

_OSM_ATTR = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

# (south, west, north, east) — Leaflet requests no tiles outside these.
_BOUNDS_CH = (45.4, 5.4, 48.3, 11.0)
_BOUNDS_AT = (46.3, 9.4, 49.1, 17.3)
_BOUNDS_FR = (41.2, -5.6, 51.2, 9.8)
_BOUNDS_NL = (50.6, 3.0, 53.8, 7.4)
_BOUNDS_DE = (47.2, 5.8, 55.1, 15.1)
_BOUNDS_US = (18.0, -180.0, 72.0, -64.0)


@dataclass(frozen=True)
class MapLayer:
    """One tile source.

    *url* is a Leaflet URL template ({z}/{x}/{y}, optional {s}); "{key}" is
    replaced by the user's API key for *key_provider*. *max_native_zoom* is
    the deepest zoom the server has tiles for — the map zooms on to 19 (20
    for some aerial layers) by enlarging them."""

    id: str
    provider: str
    kind: str
    url: str
    attribution: str
    max_native_zoom: int
    max_zoom: int = 19
    subdomains: str = ""
    region: str = ""   # ISO country code for national services, "" = worldwide
    bounds: Optional[tuple[float, float, float, float]] = None
    key_provider: Optional[str] = None
    offline: bool = True

    @property
    def name(self) -> str:
        """Localised display name, e.g. "swisstopo – Aerial imagery"."""
        kind = tr(_KIND_KEYS[self.kind])
        return f"{self.provider} – {kind}"

    @property
    def label(self) -> str:
        """name plus the region code, e.g. "swisstopo – Aerial imagery (CH)"."""
        return f"{self.name} ({self.region})" if self.region else self.name

    def tile_url(self, api_key: str = "") -> str:
        """The URL template the page loads tiles from."""
        if not self.offline:
            # Fetched through web_assets.TileProxy, never cached on disk.
            from opensak.gui.web_assets import TILE_BASE
            return f"{TILE_BASE}{self.id}/{{z}}/{{x}}/{{y}}"
        return self.url.replace("{key}", api_key)

    def upstream_url(self, z: int, x: int, y: int, api_key: str = "") -> str:
        """The provider's URL of one tile (used by the tile proxy)."""
        url = self.url.replace("{key}", api_key)
        url = url.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
        if "{s}" in url:
            subs = self.subdomains or "a"
            url = url.replace("{s}", subs[(x + y) % len(subs)])
        return url


def _wmts_ign(layer: str, fmt: str) -> str:
    return (
        "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
        f"&LAYER={layer}&STYLE=normal&FORMAT={fmt}&TILEMATRIXSET=PM"
        "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
    )


LAYERS: tuple[MapLayer, ...] = (
    # ── Worldwide, no key ────────────────────────────────────────────────
    MapLayer(
        "osm", "OpenStreetMap", KIND_MAP,
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        _OSM_ATTR, max_native_zoom=19,
    ),
    MapLayer(
        "opentopomap", "OpenTopoMap", KIND_TOPO,
        "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        _OSM_ATTR + ', SRTM | © <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)',
        max_native_zoom=17, subdomains="abc",
    ),
    MapLayer(
        "cyclosm", "CyclOSM", KIND_MAP,
        "https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png",
        _OSM_ATTR + ' | <a href="https://www.cyclosm.org">CyclOSM</a>, hosted by OpenStreetMap France',
        max_native_zoom=19, subdomains="abc",
    ),
    MapLayer(
        "osm_hot", "OSM Humanitarian", KIND_MAP,
        "https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
        _OSM_ATTR + ' | Humanitarian OpenStreetMap Team, hosted by OpenStreetMap France',
        max_native_zoom=19, subdomains="abc",
    ),
    # ── National open-data services, no key ──────────────────────────────
    MapLayer(
        "swisstopo_map", "swisstopo", KIND_MAP,
        "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-farbe/default/current/3857/{z}/{x}/{y}.jpeg",
        '© <a href="https://www.swisstopo.admin.ch/">swisstopo</a>',
        max_native_zoom=19, region="CH", bounds=_BOUNDS_CH,
    ),
    MapLayer(
        "swisstopo_aerial", "swisstopo", KIND_AERIAL,
        "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.swissimage/default/current/3857/{z}/{x}/{y}.jpeg",
        '© <a href="https://www.swisstopo.admin.ch/">swisstopo</a> SWISSIMAGE',
        max_native_zoom=20, max_zoom=20, region="CH", bounds=_BOUNDS_CH,
    ),
    MapLayer(
        "basemap_at_map", "basemap.at", KIND_MAP,
        "https://mapsneu.wien.gv.at/basemap/geolandbasemap/normal/google3857/{z}/{y}/{x}.png",
        '<a href="https://basemap.at">basemap.at</a> (CC BY 4.0)',
        max_native_zoom=19, region="AT", bounds=_BOUNDS_AT,
    ),
    MapLayer(
        "basemap_at_aerial", "basemap.at", KIND_AERIAL,
        "https://mapsneu.wien.gv.at/basemap/bmaporthofoto30cm/normal/google3857/{z}/{y}/{x}.jpeg",
        '<a href="https://basemap.at">basemap.at</a> (CC BY 4.0)',
        max_native_zoom=19, region="AT", bounds=_BOUNDS_AT,
    ),
    MapLayer(
        "ign_map", "IGN Plan", KIND_MAP,
        _wmts_ign("GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2", "image/png"),
        '© <a href="https://www.ign.fr/">IGN</a> – Géoplateforme',
        max_native_zoom=19, region="FR", bounds=_BOUNDS_FR,
    ),
    MapLayer(
        "ign_aerial", "IGN", KIND_AERIAL,
        _wmts_ign("ORTHOIMAGERY.ORTHOPHOTOS", "image/jpeg"),
        '© <a href="https://www.ign.fr/">IGN</a> – Géoplateforme',
        max_native_zoom=19, region="FR", bounds=_BOUNDS_FR,
    ),
    MapLayer(
        "pdok_map", "PDOK BRT", KIND_MAP,
        "https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0/standaard/EPSG:3857/{z}/{x}/{y}.png",
        '<a href="https://www.kadaster.nl/">Kadaster</a> / <a href="https://www.pdok.nl/">PDOK</a> (CC BY 4.0)',
        max_native_zoom=19, region="NL", bounds=_BOUNDS_NL,
    ),
    MapLayer(
        "pdok_aerial", "PDOK", KIND_AERIAL,
        "https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0/Actueel_orthoHR/EPSG:3857/{z}/{x}/{y}.jpeg",
        '<a href="https://www.beeldmateriaal.nl/">Beeldmateriaal Nederland</a> / <a href="https://www.pdok.nl/">PDOK</a>',
        max_native_zoom=19, region="NL", bounds=_BOUNDS_NL,
    ),
    MapLayer(
        "basemap_de", "basemap.de", KIND_MAP,
        "https://sgx.geodatenzentrum.de/wmts_basemapde/tile/1.0.0/de_basemapde_web_raster_farbe/default/GLOBAL_WEBMERCATOR/{z}/{y}/{x}.png",
        '© <a href="https://basemap.de">basemap.de</a> / BKG (CC BY 4.0)',
        max_native_zoom=18, region="DE", bounds=_BOUNDS_DE,
    ),
    MapLayer(
        "usgs_topo", "USGS", KIND_TOPO,
        "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}",
        'Tiles courtesy of the <a href="https://www.usgs.gov/">U.S. Geological Survey</a>',
        max_native_zoom=16, region="US", bounds=_BOUNDS_US,
    ),
    MapLayer(
        "usgs_aerial", "USGS", KIND_AERIAL,
        "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
        'Tiles courtesy of the <a href="https://www.usgs.gov/">U.S. Geological Survey</a>',
        max_native_zoom=16, region="US", bounds=_BOUNDS_US,
    ),
    # ── Worldwide, user's own API key ────────────────────────────────────
    MapLayer(
        "esri_aerial", "Esri World Imagery", KIND_AERIAL,
        "https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}?token={key}",
        'Powered by <a href="https://www.esri.com/">Esri</a> | Esri, Maxar, Earthstar Geographics, and the GIS User Community',
        max_native_zoom=19, key_provider="esri",
        # Esri allows caching tiles only while connected — never on disk.
        offline=False,
    ),
    MapLayer(
        "maptiler_aerial", "MapTiler Satellite", KIND_AERIAL,
        "https://api.maptiler.com/tiles/satellite-v2/{z}/{x}/{y}.jpg?key={key}",
        '<a href="https://www.maptiler.com/copyright/">© MapTiler</a> ' + _OSM_ATTR,
        max_native_zoom=20, max_zoom=20, key_provider="maptiler",
    ),
    MapLayer(
        "maptiler_outdoor", "MapTiler Outdoor", KIND_TOPO,
        "https://api.maptiler.com/maps/outdoor-v2/256/{z}/{x}/{y}.png?key={key}",
        '<a href="https://www.maptiler.com/copyright/">© MapTiler</a> ' + _OSM_ATTR,
        max_native_zoom=19, key_provider="maptiler",
    ),
    MapLayer(
        "thunderforest_outdoors", "Thunderforest Outdoors", KIND_TOPO,
        "https://tile.thunderforest.com/outdoors/{z}/{x}/{y}.png?apikey={key}",
        'Maps © <a href="https://www.thunderforest.com/">Thunderforest</a>, Data ' + _OSM_ATTR,
        max_native_zoom=19, key_provider="thunderforest",
    ),
    MapLayer(
        "thunderforest_cycle", "OpenCycleMap", KIND_MAP,
        "https://tile.thunderforest.com/cycle/{z}/{x}/{y}.png?apikey={key}",
        'Maps © <a href="https://www.thunderforest.com/">Thunderforest</a>, Data ' + _OSM_ATTR,
        max_native_zoom=19, key_provider="thunderforest",
    ),
)

_BY_ID = {layer.id: layer for layer in LAYERS}


def get_layer(layer_id: str) -> Optional[MapLayer]:
    return _BY_ID.get(layer_id)


def available_layers(enabled_ids, api_keys: dict[str, str]) -> list[MapLayer]:
    """The layers the map offers: switched on in the settings and, for a
    keyed layer, with its key entered. Never empty — with nothing usable
    left, OpenStreetMap is offered so the map still has a background."""
    enabled = set(enabled_ids)
    out = [
        layer for layer in LAYERS
        if layer.id in enabled
        and (layer.key_provider is None or api_keys.get(layer.key_provider, "").strip())
    ]
    return out or [_BY_ID[DEFAULT_LAYER]]


def layer_js_defs(layers: list[MapLayer], api_keys: dict[str, str]) -> list[dict]:
    """The layer definitions the map page builds its tile layers from."""
    defs = []
    for layer in layers:
        key = api_keys.get(layer.key_provider, "").strip() if layer.key_provider else ""
        options: dict = {
            "attribution": layer.attribution,
            "maxZoom": layer.max_zoom,
            "maxNativeZoom": layer.max_native_zoom,
        }
        if layer.subdomains:
            options["subdomains"] = layer.subdomains
        if layer.bounds:
            s, w, n, e = layer.bounds
            options["bounds"] = [[s, w], [n, e]]
        defs.append({"id": layer.id, "name": layer.label,
                     "url": layer.tile_url(key), "options": options})
    return defs

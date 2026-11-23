# tests/unit-tests/test_map_layers_747.py — #747: more base maps. Every map
# can be switched on/off in Settings → Map, keyed maps appear only with the
# user's API key, the map offers the switched-on ones in a layer switcher and
# remembers the choice. Every map is fetched through opensak://tiles/ (#1361);
# maps whose provider forbids keeping tiles on the device never reach the
# disk cache, and the API key of a keyed map never reaches the page.

from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("pytestqt")

from opensak.gui import map_layers
from opensak.gui.map_layers import LAYERS, available_layers, get_layer, layer_js_defs
from opensak.gui.map_widget import MapWidget
from opensak.gui.settings import get_settings
from opensak.gui.web_assets import TILE_BASE, resolve_tile
from opensak.lang import tr
from opensak.settings_store import get_store


def _layers_in_html(html: str) -> list[dict]:
    m = re.search(r"var BASE_LAYERS = (.*);\n", html)
    assert m, "BASE_LAYERS not substituted"
    return json.loads(m.group(1).replace("<\\/", "</"))


class TestCatalogue:
    def test_ids_unique(self):
        ids = [layer.id for layer in LAYERS]
        assert len(ids) == len(set(ids))

    @pytest.mark.parametrize("layer", LAYERS, ids=lambda layer: layer.id)
    def test_layer_is_complete(self, layer):
        for token in ("{z}", "{x}", "{y}"):
            assert token in layer.url
        assert layer.url.startswith("https://")
        assert layer.attribution
        assert 0 < layer.max_native_zoom <= layer.max_zoom <= 20
        assert ("{s}" in layer.url) == bool(layer.subdomains)
        assert ("{key}" in layer.url) == (layer.key_provider is not None)
        if layer.key_provider:
            assert layer.key_provider in map_layers.KEY_PROVIDERS
        assert layer.name.startswith(layer.provider + " – ")

    def test_no_google_or_bing(self):
        # Their terms forbid direct tile access (#747).
        from urllib.parse import urlsplit
        for layer in LAYERS:
            host = urlsplit(layer.url.replace("{s}", "a")).hostname or ""
            assert "google" not in host and "virtualearth" not in host and "bing" not in host

    def test_defaults_exist(self):
        assert get_layer(map_layers.DEFAULT_LAYER) is not None
        assert all(get_layer(i) for i in map_layers.DEFAULT_ENABLED)

    def test_kind_is_translated(self):
        assert get_layer("swisstopo_aerial").name == "swisstopo – " + tr("map_layer_kind_aerial")
        assert get_layer("swisstopo_aerial").label.endswith("(CH)")


class TestAvailableLayers:
    def test_only_switched_on(self):
        ids = [layer.id for layer in available_layers(["opentopomap", "ign_aerial"], {})]
        assert ids == ["opentopomap", "ign_aerial"]

    def test_keyed_layer_needs_its_key(self):
        assert [layer.id for layer in available_layers(["osm", "esri_aerial"], {})] == ["osm"]
        ids = [layer.id for layer in available_layers(["osm", "esri_aerial"], {"esri": "K"})]
        assert ids == ["osm", "esri_aerial"]

    def test_never_empty(self):
        assert [layer.id for layer in available_layers([], {})] == ["osm"]
        assert [layer.id for layer in available_layers(["esri_aerial"], {})] == ["osm"]


class TestOfflineRouting:
    @pytest.mark.parametrize("layer", LAYERS, ids=lambda layer: layer.id)
    def test_every_layer_goes_through_proxy(self, layer):
        # #1361: Chromium never fetches a tile itself, so Offline mode can
        # serve cached tiles only, and keyless layers are no exception.
        defs = layer_js_defs([layer])
        assert defs[0]["url"] == f"{TILE_BASE}{layer.id}/{{z}}/{{x}}/{{y}}"
        assert "http" not in defs[0]["url"]

    def test_no_offline_layer_goes_through_proxy(self):
        # Esri allows caching only while connected: its tiles must not reach
        # the disk HTTP cache, so they come via opensak://tiles/.
        defs = layer_js_defs([get_layer("esri_aerial")])
        assert defs[0]["url"] == TILE_BASE + "esri_aerial/{z}/{x}/{y}"

    @pytest.mark.parametrize("layer", [layer for layer in LAYERS if layer.key_provider],
                             ids=lambda layer: layer.id)
    def test_keyed_layer_goes_through_proxy(self, layer):
        # The key is added by the proxy: never in the page, Leaflet's tile
        # URLs or Chromium's cache — also for layers that may be cached.
        defs = layer_js_defs([layer])
        assert defs[0]["url"] == f"{TILE_BASE}{layer.id}/{{z}}/{{x}}/{{y}}"
        assert "{key}" not in json.dumps(defs)

    def test_keys_never_reach_the_page(self):
        s = get_settings()
        s.map_api_keys = {p: f"SECRET-{p}" for p in map_layers.KEY_PROVIDERS}
        s.map_base_layers = [layer.id for layer in LAYERS]
        w = MapWidget.__new__(MapWidget)
        w._map_actions = True
        w._draw_button = False
        w._single_cache_button = False
        w._auto_fit_caches = True
        html = MapWidget._render_map_html(46.9, 7.4, **w._html_options())
        assert len(_layers_in_html(html)) == len(LAYERS)
        assert "SECRET" not in html

    def test_resolve_keyless_tile(self):
        assert resolve_tile("/osm/12/2145/1434") == (
            "https://tile.openstreetmap.org/12/2145/1434.png")

    def test_resolve_cacheable_keyed_tile(self):
        get_settings().map_api_keys = {"maptiler": "MYKEY"}
        assert resolve_tile("/maptiler_aerial/3/1/2") == (
            "https://api.maptiler.com/tiles/satellite-v2/3/1/2.jpg?key=MYKEY")

    def test_resolve_tile_with_key(self):
        get_settings().map_api_keys = {"esri": "SECRET"}
        assert resolve_tile("/esri_aerial/12/2145/1434") == (
            "https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/"
            "MapServer/tile/12/1434/2145?token=SECRET"
        )

    @pytest.mark.parametrize("path", [
        "/esri_aerial/12/2145/1434",   # no key entered
        "/maptiler_aerial/3/1/2",      # no key entered
        "/nope/12/2145/1434",          # unknown layer
        "/esri_aerial/12/2145",        # too short
        "/esri_aerial/12/x/1434",      # not a number
        "/esri_aerial/2/4/0",          # x out of range for the zoom
    ])
    def test_resolve_tile_refuses(self, path):
        get_settings().map_api_keys = {}
        assert resolve_tile(path) is None

    def test_subdomain_substituted(self):
        url = get_layer("opentopomap").upstream_url(3, 1, 2)
        assert re.fullmatch(r"https://[abc]\.tile\.opentopomap\.org/3/1/2\.png", url)


class TestSettings:
    def test_defaults(self):
        s = get_settings()
        assert s.map_base_layers == list(map_layers.DEFAULT_ENABLED)
        assert s.map_base_layer == "osm"
        assert s.map_api_keys == {}

    def test_unknown_ids_dropped(self):
        get_store().set("display.map_base_layers", ["osm", "gone", 5])
        assert get_settings().map_base_layers == ["osm"]

    def test_junk_values(self):
        get_store().set("display.map_base_layers", "junk")
        get_store().set("display.map_api_key_providers", "junk")
        assert get_settings().map_base_layers == list(map_layers.DEFAULT_ENABLED)
        assert get_settings().map_api_keys == {}

    def test_empty_keys_not_stored(self):
        get_settings().map_api_keys = {"esri": "  ", "maptiler": " K "}
        assert get_settings().map_api_keys == {"maptiler": "K"}

    def test_settings_dialog_round_trip(self, qtbot):
        from PySide6.QtCore import Qt
        from opensak.gui.dialogs.settings_dialog import SettingsDialog
        get_settings().map_base_layers = ["osm", "swisstopo_aerial"]
        get_settings().map_api_keys = {"thunderforest": "TF"}
        dlg = SettingsDialog()
        qtbot.addWidget(dlg)
        lst = dlg._map_layers_list
        assert lst.count() == len(LAYERS)
        checked = {}
        for row in range(lst.count()):
            item = lst.item(row)
            checked[item.data(Qt.ItemDataRole.UserRole)] = item
        assert checked["osm"].checkState() == Qt.CheckState.Checked
        assert checked["swisstopo_aerial"].checkState() == Qt.CheckState.Checked
        assert checked["opentopomap"].checkState() == Qt.CheckState.Unchecked
        assert tr("settings_map_layer_needs_key", provider="Esri ArcGIS") in checked["esri_aerial"].text()
        assert tr("settings_map_layer_no_offline") in checked["esri_aerial"].text()
        assert tr("settings_map_layer_needs_key", provider="MapTiler") in checked["maptiler_outdoor"].text()
        assert dlg._map_layers_list.wordWrap()
        # One key per provider: its field names every map the key unlocks
        assert len(dlg._map_api_key_edits) == len(map_layers.KEY_PROVIDERS)
        assert dlg._map_api_key_edits["maptiler"].placeholderText() == tr(
            "settings_map_api_key_for", maps="MapTiler Satellite, MapTiler Outdoor")
        assert dlg._map_api_key_edits["thunderforest"].placeholderText() == tr(
            "settings_map_api_key_for", maps="Thunderforest Outdoors, OpenCycleMap")
        assert dlg._map_api_key_edits["thunderforest"].text() == "TF"
        # Keys are masked on screen, like a password
        from PySide6.QtWidgets import QLineEdit
        assert all(edit.echoMode() == QLineEdit.EchoMode.Password
                   for edit in dlg._map_api_key_edits.values())
        # The section-1 layers can be switched off too
        checked["osm"].setCheckState(Qt.CheckState.Unchecked)
        checked["esri_aerial"].setCheckState(Qt.CheckState.Checked)
        dlg._map_api_key_edits["esri"].setText("E")
        dlg._save()
        assert get_settings().map_base_layers == ["swisstopo_aerial", "esri_aerial"]
        assert get_settings().map_api_keys == {"thunderforest": "TF", "esri": "E"}
        # Keys are not in the settings file (only which providers have one)
        assert "TF" not in json.dumps(get_store()._data)


class TestMapPage:
    def test_default_page_shows_osm_only(self):
        html = MapWidget._render_map_html(55.0, 10.0)
        layers = _layers_in_html(html)
        assert [d["id"] for d in layers] == ["osm"]
        assert layers[0]["url"] == TILE_BASE + "osm/{z}/{x}/{y}"   # #1361
        assert "MAP_BASE_LAYERS" not in html and "MAP_ACTIVE_BASE_LAYER" not in html

    def test_switched_on_layers_and_choice_reach_the_page(self):
        s = get_settings()
        s.map_base_layers = ["osm", "swisstopo_aerial"]
        s.map_base_layer = "swisstopo_aerial"
        w = MapWidget.__new__(MapWidget)
        w._map_actions = True
        w._draw_button = False
        w._single_cache_button = False
        w._auto_fit_caches = True
        html = MapWidget._render_map_html(46.9, 7.4, **w._html_options())
        layers = _layers_in_html(html)
        assert [d["id"] for d in layers] == ["osm", "swisstopo_aerial"]
        assert layers[1]["options"]["bounds"] == [[45.4, 5.4], [48.3, 11.0]]
        assert 'var ACTIVE_BASE_LAYER = "swisstopo_aerial";' in html
        assert "L.control.layers(baseLayerControl" in html
        assert "bridge.on_base_layer_changed(e.layer.opensakId)" in html

    def test_attribution_cannot_close_script(self):
        html = MapWidget._render_map_html(55.0, 10.0)
        script = html.split("var BASE_LAYERS = ", 1)[1].split(";\n", 1)[0]
        assert "</" not in script

    def test_choice_remembered(self):
        w = MapWidget.__new__(MapWidget)
        w._on_base_layer_changed("opentopomap")
        assert get_settings().map_base_layer == "opentopomap"
        w._on_base_layer_changed("not-a-layer")
        assert get_settings().map_base_layer == "opentopomap"

    def test_bridge_slot_emits(self, qtbot):
        from opensak.gui.map_widget import MapBridge
        bridge = MapBridge()
        with qtbot.waitSignal(bridge.base_layer_changed) as blocker:
            bridge.on_base_layer_changed("cyclosm")
        assert blocker.args == ["cyclosm"]

# tests/unit-tests/test_map_pan_744.py — issue #744.
#
# Selecting a cache should centre the map on it and keep the user's zoom.
# Two things went wrong in panToCache():
#   1. it called map.panTo() and then marker.openPopup(); the popup's
#      auto-pan stops a running pan, so the map only moved a few pixels;
#   2. a cache hidden in a cluster was revealed with zoomToShowLayer(),
#      which zooms in until the cluster breaks up — even for 2–3 caches.
#
# The map page is JavaScript, and unit tests run without QtWebEngine, so these
# tests check the page source. The behaviour was checked in a real
# QtWebEngine map: centred to the pixel and same zoom in all cases, except a
# large cluster, which still zooms in.

import re

from opensak.gui import map_widget as mw_mod


def _function_body(name: str) -> str:
    html = mw_mod.MAP_HTML
    start = html.index(f"function {name}(")
    end = html.index("\nfunction ", start + 1)
    return html[start:end]


class TestPanToCache:
    def test_popup_opens_before_the_pan(self):
        body = _function_body("panToCache")
        do_pan = body[body.index("var doPan"):body.index("};", body.index("var doPan"))]
        assert do_pan.index("marker.openPopup()") < do_pan.index("map.panTo(")

    def test_pans_to_where_the_pin_is_shown(self):
        # In a fanned-out cluster the pin is at the end of its leg.
        body = _function_body("panToCache")
        assert "map.panTo(marker.getLatLng())" in body

    def test_small_cluster_is_fanned_out_large_cluster_zooms(self):
        body = _function_body("panToCache")
        assert "cluster.getChildCount() <= SPIDERFY_MAX" in body
        assert "spiderfyToShow(" in body
        assert "clusterGroup.zoomToShowLayer(marker, doPan)" in body

    def test_spiderfy_limit_is_set(self):
        m = re.search(r"var SPIDERFY_MAX = (\d+);", mw_mod.MAP_HTML)
        assert m and 2 <= int(m.group(1)) <= 50

    def test_rapid_selection_guard_is_kept(self):
        # Issue #718: only the latest selection may move the map.
        body = _function_body("panToCache")
        assert "var mySeq = ++panRequestSeq;" in body
        assert "if (mySeq !== panRequestSeq) return;" in body
        assert "setTimeout(doPan, 400)" in body


class TestSpiderfyToShow:
    def test_keeps_zoom_and_spiderfies_after_the_move(self):
        body = _function_body("spiderfyToShow")
        assert "map.once('moveend'" in body
        assert "cluster.spiderfy()" in body
        assert "map.panTo(latlng)" in body
        assert "setZoom" not in body and "zoomToBounds" not in body
        # A later selection makes this one a no-op.
        assert "if (mySeq !== panRequestSeq) return;" in body

    def test_cluster_lookup_ignores_pins_shown_on_their_own(self):
        body = _function_body("clusterAtCurrentZoom")
        assert "if (marker._icon) return null;" in body
        assert "c._zoom < zoom" in body

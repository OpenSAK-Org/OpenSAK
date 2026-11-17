# tests/unit-tests/test_map_cluster_lift_1324.py — issue #1324.
#
# Selecting a cache hidden in a cluster fanned the whole cluster out
# (spiderfy). In a cache-dense area that put 10+ pins on the map at
# positions that were not their own, and it stayed that way until the
# user clicked the map or zoomed. Now only the selected pin is lifted out
# of the cluster group and shown at its real position; the rest stays
# clustered and the zoom is kept (issue #744).
#
# The map page is JavaScript, and unit tests run without QtWebEngine, so
# these tests check the page source. The behaviour was checked by running
# these functions with the bundled Leaflet and Leaflet.markercluster in
# jsdom: same zoom, no spider, one pin lifted at a time, and every pin back
# in the cluster group after another selection or a clustering toggle.

from opensak.gui import map_widget as mw_mod


def _function_body(name: str) -> str:
    html = mw_mod.MAP_HTML
    start = html.index(f"function {name}(")
    end = html.find("\nfunction ", start + 1)
    if end == -1:  # the last function on the page
        end = html.index("</script>", start)
    return html[start:end]


class TestNoSpiderfyOnSelection:
    def test_selection_never_fans_out_a_cluster(self):
        html = mw_mod.MAP_HTML
        assert ".spiderfy()" not in html
        assert "SPIDERFY_MAX" not in html
        assert "function spiderfyToShow(" not in html

    def test_cluster_hidden_cache_is_lifted_out(self):
        body = _function_body("panToCache")
        lookup = body.index("clusterAtCurrentZoom(marker)")
        assert body.index("liftOutOfCluster(marker)") > lookup

    def test_previous_pin_goes_back_before_the_next_is_lifted(self):
        body = _function_body("panToCache")
        assert "liftedMarker !== marker) restoreLiftedMarker();" in body
        assert body.index("restoreLiftedMarker()") < body.index("liftOutOfCluster(marker)")

    def test_reselecting_the_lifted_pin_only_pans(self):
        body = _function_body("panToCache")
        assert "if (liftedMarker === marker) {" in body


class TestLiftAndRestore:
    def test_lift_moves_the_pin_from_the_group_to_the_map(self):
        body = _function_body("liftOutOfCluster")
        assert "clusterGroup.removeLayer(marker)" in body
        assert "marker.addTo(map)" in body
        assert "liftedMarker = marker" in body
        # Drawn above the clusters around it.
        assert "setZIndexOffset(1000)" in body

    def test_restore_puts_the_pin_back_only_if_still_current(self):
        body = _function_body("restoreLiftedMarker")
        assert "map.removeLayer(marker)" in body
        assert "setZIndexOffset(0)" in body
        assert "clusterGroup.addLayer(marker)" in body
        # A pin that was replaced or dropped must not come back.
        assert "markers[k] === marker" in body
        assert body.index("liftedMarker = null") < body.index("clusterGroup.addLayer(marker)")

    def test_clustering_toggle_restores_first(self):
        body = _function_body("setClustering")
        assert body.index("restoreLiftedMarker()") < body.index("var layers")

    def test_reload_restores_first(self):
        body = _function_body("loadCaches")
        assert body.index("restoreLiftedMarker()") < body.index("markers = {}")

    def test_marker_update_restores_its_own_pin(self):
        body = _function_body("updateCacheMarker")
        restore = body.index("restoreLiftedMarker()")
        assert restore < body.index("clusterGroup.removeLayer(markers[c.gc_code])")
        assert "liftedMarker === markers[c.gc_code]" in body

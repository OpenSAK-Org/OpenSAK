# src/opensak/geo/boundaries.py — two-stage offline reverse geocoding.

from __future__ import annotations

from typing import Any, NamedTuple

from opensak.geo.store import BoundaryStore
from opensak.logger import get_logger
from opensak.debug_flags import is_debug_enabled

log = get_logger("geo.boundaries")

try:
    from shapely.geometry import Point as _Point
    from shapely.geometry import shape as _shape
    _HAS_SHAPELY = True
except ImportError:  # pragma: no cover
    _HAS_SHAPELY = False


class GeoLocation(NamedTuple):
    country: str | None
    state: str | None
    county: str | None


class TerritoryResolver:
    """Resolve (lat, lon) to country/state/county via the two-stage lookup.

    Stage 1 is an R-Tree bbox query; a single box hit returns directly, an
    overlap falls through to Stage 2 point-in-polygon on the few candidates.
    """

    def __init__(self, store: BoundaryStore) -> None:
        self._store = store
        # Shapely geometry objects cached by (layer, region_id). _shape() is
        # expensive for complex coastlines — construct once, reuse across rows.
        self._shape_cache: dict = {}
        if is_debug_enabled("geo"):
            log.debug("TerritoryResolver initialized, shapely=%s", _HAS_SHAPELY)

    def resolve(self, lat: float, lon: float) -> GeoLocation:
        result = GeoLocation(
            country=self._resolve_layer("country", lat, lon),
            state=self._resolve_layer("state", lat, lon),
            county=self._resolve_layer("county", lat, lon),
        )
        if is_debug_enabled("geo"):
            log.debug("resolve(%.4f, %.4f) -> %s", lat, lon, result)
        return result

    def _resolve_layer(self, layer: str, lat: float, lon: float) -> str | None:
        ids = self._store.candidates(layer, lat, lon)
        if is_debug_enabled("geo"):
            log.debug("_resolve_layer(%s, %.4f, %.4f): %d candidates", layer, lat, lon, len(ids))
        if not ids:
            return None
        for region_id in ids:  # always do polygon check — a single bbox hit is not enough near borders
            region = self._store.region(layer, region_id)
            if region is None:
                continue
            try:
                geom = self._store.geometry(layer, region)
            except FileNotFoundError:
                # pack missing and on-demand fetch failed (no network) — skip candidate
                if is_debug_enabled("geo"):
                    log.debug("geometry file not found for %s[%d]", layer, region_id)
                continue
            if _point_in_geometry(lat, lon, geom, self._shape_cache, (layer, region_id)):
                if is_debug_enabled("geo"):
                    log.debug("_resolve_layer(%s): matched %s", layer, region.name)
                return region.name
        return None


def _point_in_geometry(
    lat: float,
    lon: float,
    geometry: dict[str, Any],
    shape_cache: dict | None = None,
    cache_key: Any = None,
) -> bool:
    if _HAS_SHAPELY:
        if shape_cache is not None:
            shp = shape_cache.get(cache_key)
            if shp is None:
                shp = _shape(geometry)
                shape_cache[cache_key] = shp
        else:
            shp = _shape(geometry)
        return bool(shp.contains(_Point(lon, lat)))
    # pure-Python ray-cast fallback (shapely not installed)
    gtype = geometry.get("type")
    coords: Any = geometry.get("coordinates")
    if gtype == "Polygon":
        return _point_in_polygon(lon, lat, coords)
    if gtype == "MultiPolygon":
        return any(_point_in_polygon(lon, lat, poly) for poly in coords)
    return False


def _point_in_polygon(x: float, y: float, rings: Any) -> bool:
    # GeoJSON coords are [lon, lat]; rings[0] is the outer ring, the rest holes.
    if not rings or not _point_in_ring(x, y, rings[0]):
        return False
    return not any(_point_in_ring(x, y, hole) for hole in rings[1:])


def _point_in_ring(x: float, y: float, ring: Any) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside

"""
src/opensak/stats/maps.py — maps as SVG strings from the offline boundary data.

The country, state and county outlines are the ones Update location uses
(opensak.geo.BoundaryStore). They are clipped to the area shown, simplified
to about one pixel and projected with Web Mercator, so a map of Europe stays
a few hundred kB. Like charts.py: no script, text in currentColor, the fill
colour from --chart-color, a <title> tooltip on every region and dot.

Region names from the cache data are matched to the outlines ignoring case,
accents and punctuation ("Graubünden" = "Graubunden", "Basel-Landschaft" =
"Basel Landschaft"). States and counties belong to a country through a
country code in the boundary data; which country that is, is found once by
looking up a few of its regions in the country layer.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from html import escape
from typing import Any, Iterable, Mapping, Optional, Sequence

from opensak.stats.charts import (
    ChartError,
    _TITLE_H,
    _check_color,
    _check_size,
    _fmt,
    _series_style,
    _svg_open,
    _ticks,
)

LAYERS = ("country", "state", "county")
EXTENTS = ("data", "all")
MAX_MAP_POINTS = 50_000
# Web Mercator cannot show the poles; a world map ends here.
LAT_MIN, LAT_MAX = -58.0, 78.0
# Number of colour classes of a region map.
CLASSES = 5
# Longest list of names that are not on the map, written below it.
MAX_UNMATCHED_SHOWN = 8

Box = tuple[float, float, float, float]   # min_lon, min_lat, max_lon, max_lat


class MapError(ChartError):
    """A map that cannot be drawn."""


def normalize(name: Any) -> str:
    """Region name for matching: no case, accents or punctuation."""
    text = str(name)
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    words = re.findall(r"[0-9a-z]+", ascii_text.casefold())
    return " ".join(words) if words else text.casefold().strip()


@dataclass(frozen=True)
class Feature:
    layer: str
    id: int
    name: str
    parent: Optional[str]
    pack: str
    index: int
    box: Box

    @property
    def area(self) -> float:
        return (self.box[2] - self.box[0]) * (self.box[3] - self.box[1])


def _intersects(a: Box, b: Box) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _union(boxes: Iterable[Box]) -> Optional[Box]:
    boxes = list(boxes)
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


class MapData:
    """The boundary data of one macro run: region lists, outlines (cached)
    and which country each state/county code belongs to."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._features: dict[str, list[Feature]] = {}
        self._shapes: dict[tuple[str, int], Any] = {}
        self._countries_of: dict[str, dict[Optional[str], Optional[str]]] = {}

    def close(self) -> None:
        self._shapes.clear()
        self._store.close()

    def features(self, layer: str) -> list[Feature]:
        if layer not in self._features:
            self._features[layer] = [
                Feature(layer, r.id, r.name, r.parent, r.pack, r.feature_index,
                        (b[2], b[0], b[3], b[1]))
                for r, b in self._store.regions_with_boxes(layer)
            ]
        return self._features[layer]

    def shape(self, f: Feature) -> Any:
        """The shapely geometry of *f*; None if its outline file is missing."""
        key = (f.layer, f.id)
        if key not in self._shapes:
            from opensak.geo.store import Region
            from shapely.geometry import shape

            region = Region(f.id, f.name, f.parent, f.pack, f.index)
            try:
                self._shapes[key] = shape(self._store.geometry(f.layer, region))
            except (FileNotFoundError, ValueError, KeyError, IndexError):
                self._shapes[key] = None
        return self._shapes[key]

    def country_at(self, lon: float, lat: float) -> Optional[str]:
        from shapely.geometry import Point

        point = Point(lon, lat)
        for f in self.features("country"):
            b = f.box
            if b[0] <= lon <= b[2] and b[1] <= lat <= b[3]:
                geom = self.shape(f)
                if geom is not None and geom.covers(point):
                    return f.name
        return None

    def country_of(self, f: Feature) -> Optional[str]:
        """The country a state or county belongs to (a country: itself)."""
        if f.layer == "country":
            return f.name
        mapping = self._countries_of.get(f.layer)
        if mapping is None:
            mapping = self._countries_of[f.layer] = {}
        if f.parent not in mapping:
            # Vote with the smallest regions of the code: the centre of a
            # small box lies inside it (and its country) more often.
            members = sorted((g for g in self.features(f.layer) if g.parent == f.parent),
                             key=lambda g: g.area)[:7]
            votes = Counter(
                c for g in members
                if (c := self.country_at((g.box[0] + g.box[2]) / 2, (g.box[1] + g.box[3]) / 2))
            )
            mapping[f.parent] = votes.most_common(1)[0][0] if votes else None
        return mapping[f.parent]


def _within_filter(data: MapData, layer: str, within: Any) -> Optional[set[str]]:
    """Normalised country names of *within* (a name or a list), or None."""
    if within is None:
        return None
    names = [within] if isinstance(within, str) else within
    if not isinstance(names, (list, tuple)) or not all(isinstance(n, str) for n in names):
        raise MapError('within must be a country name or a list of them, e.g. "Switzerland"')
    return {normalize(n) for n in names}


def _check_layer(layer: Any) -> str:
    if layer not in LAYERS:
        raise MapError(f"layer must be one of {', '.join(LAYERS)}, got {layer!r}")
    return layer


def regions(data: MapData, layer: str = "country", within: Any = None) -> list[dict[str, Any]]:
    """The regions of *layer* (optionally only those in the *within*
    countries), one row per name: {name[, country]}, sorted."""
    _check_layer(layer)
    countries = _within_filter(data, layer, within)
    seen: dict[tuple[str, Optional[str]], dict[str, Any]] = {}
    for f in data.features(layer):
        country = data.country_of(f)
        if countries is not None and (country is None or normalize(country) not in countries):
            continue
        key = (f.name, None if layer == "country" else country)
        if key not in seen:
            row: dict[str, Any] = {"name": f.name}
            if layer != "country":
                row["country"] = country
            seen[key] = row
    return sorted(seen.values(), key=lambda r: (normalize(r.get("country") or ""), normalize(r["name"])))


# ── Projection and drawing ───────────────────────────────────────────────────

def _merc(lat: float) -> float:
    lat = max(min(lat, 85.0), -85.0)
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def _unmerc(y: float) -> float:
    return math.degrees(2 * math.atan(math.exp(math.radians(y))) - math.pi / 2)


class _Frame:
    """Projects lon/lat into the plot area, keeping the aspect ratio."""

    def __init__(self, box: Box, width: int, height: Optional[int], top: float, bottom: float):
        lon0, lat0, lon1, lat1 = box
        lat0, lat1 = max(lat0, LAT_MIN), min(lat1, LAT_MAX)
        # Some room around the regions, at least a little for a point.
        pad_x = max((lon1 - lon0) * 0.04, 0.05)
        pad_y = max((lat1 - lat0) * 0.04, 0.05)
        lon0, lon1 = max(lon0 - pad_x, -180.0), min(lon1 + pad_x, 180.0)
        lat0, lat1 = max(lat0 - pad_y, LAT_MIN), min(lat1 + pad_y, LAT_MAX)
        y0, y1 = _merc(lat0), _merc(lat1)
        span_x, span_y = max(lon1 - lon0, 1e-6), max(y1 - y0, 1e-6)
        if height is None:
            height = int(top + bottom + width * span_y / span_x)
            height = max(160, min(height, int(width * 1.3)))
        self.width, self.height = width, height
        plot_h = max(height - top - bottom, 40)
        self.scale = min(width / span_x, plot_h / span_y)
        # Centre the regions; the visible area is wider or taller than the box.
        cx, cy = (lon0 + lon1) / 2, (y0 + y1) / 2
        half_x, half_y = width / self.scale / 2, plot_h / self.scale / 2
        self.x0, self.ytop = cx - half_x, cy + half_y
        self.top = top
        self.visible: Box = (cx - half_x, _unmerc(cy - half_y), cx + half_x, _unmerc(cy + half_y))
        # About one pixel, in degrees: the simplification tolerance.
        self.tolerance = 1.0 / self.scale

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        return ((lon - self.x0) * self.scale, self.top + (self.ytop - _merc(lat)) * self.scale)


def _path(geom: Any, frame: _Frame) -> str:
    """SVG path data of a (multi)polygon, clipped, simplified, projected."""
    import shapely

    if geom is None or geom.is_empty:
        return ""
    v = frame.visible
    margin = frame.tolerance * 4
    try:
        geom = shapely.clip_by_rect(geom, v[0] - margin, v[1] - margin, v[2] + margin, v[3] + margin)
        geom = geom.simplify(frame.tolerance * 0.7, preserve_topology=False)
    except Exception:       # a broken outline must not stop the map
        return ""
    if geom.is_empty:
        return ""
    polygons = getattr(geom, "geoms", [geom])
    min_area = frame.tolerance ** 2
    parts = []
    for poly in polygons:
        if poly.geom_type != "Polygon" or poly.area < min_area:
            continue
        for ring in (poly.exterior, *poly.interiors):
            points = []
            last = None
            for lon, lat in ring.coords:
                x, y = frame.xy(lon, lat)
                p = (round(x, 1), round(y, 1))
                if p != last:
                    points.append(p)
                    last = p
            if len(points) >= 3:
                parts.append("M" + "L".join(f"{x:g},{y:g}" for x, y in points) + "Z")
    return "".join(parts)


def _region_path(data: MapData, f: Feature, frame: _Frame, style: str, tip: Optional[str]) -> str:
    d = _path(data.shape(f), frame)
    if not d:
        return ""
    title = f"<title>{escape(tip)}</title>" if tip else ""
    return f'<path d="{d}" fill-rule="evenodd" {style}>{title}</path>'


_EMPTY_STYLE = ('fill="currentColor" fill-opacity="0.06" stroke="currentColor" '
                'stroke-opacity="0.3" stroke-width="0.5"')
_BORDER_STYLE = ('fill="none" stroke="currentColor" stroke-opacity="0.45" stroke-width="0.8"')


def _nice_floor(v: float) -> float:
    if v <= 0:
        return 0.0
    magnitude = 10 ** math.floor(math.log10(v))
    return max(m * magnitude for m in (1, 2, 5) if m * magnitude <= v + 1e-12)


def class_edges(values: Sequence[float], classes: int = CLASSES) -> list[float]:
    """Lower bounds of the colour classes: logarithmic for skewed values
    (e.g. 24 000 finds in one country, 3 in another), linear otherwise."""
    positive = sorted(v for v in values if v > 0)
    if not positive:
        return []
    low, high = positive[0], positive[-1]
    if high / low >= 20:
        edges = [_nice_floor(low * (high / low) ** (i / classes)) for i in range(classes)]
    else:
        step = _nice_floor((high - low) / classes)
        if all(float(v).is_integer() for v in positive):
            step = max(step, 1.0)
        edges = [low + i * step for i in range(classes)] if step else [low]
        edges = [e for e in edges if e <= high]
    edges = sorted({e for e in edges if e > low} | {low})
    return edges


def _class_of(value: float, edges: Sequence[float]) -> int:
    i = 0
    for k, e in enumerate(edges):
        if value >= e:
            i = k
    return i


def _alpha(i: int, n: int) -> float:
    return 1.0 if n <= 1 else 0.14 + 0.86 * i / (n - 1)


def _legend(parts: list[str], edges: Sequence[float], fill: str, y: float) -> None:
    whole = all(float(e).is_integer() for e in edges)
    x = 0.0
    for i, e in enumerate(edges):
        if i + 1 < len(edges):
            upper = edges[i + 1] - 1 if whole else edges[i + 1]
            label = _fmt(e) if upper <= e else f"{_fmt(e)}–{_fmt(upper)}"
        else:
            label = f"≥ {_fmt(e)}"
        parts.append(f'<rect x="{x:.1f}" y="{y - 10:.1f}" width="14" height="10" rx="2" '
                     f'style="fill:{fill}" fill-opacity="{_alpha(i, len(edges)):.2f}"/>')
        parts.append(f'<text x="{x + 18:.1f}" y="{y:.1f}" fill="currentColor" '
                     f'fill-opacity="0.75">{escape(label)}</text>')
        x += 26 + len(label) * 7


@dataclass
class _Selection:
    values: dict[str, list[Any]]        # normalised name -> [name as given, sum]
    candidates: list[Feature]
    matched_ids: set[int]
    unmatched: list[str]
    box: Box


def _select(
    data: MapData,
    rows: Sequence[Mapping[str, Any]],
    key: str,
    value: str,
    layer: str,
    within: Any,
    extent: str,
) -> _Selection:
    """The regions of a map: the values of *rows* by region name, the
    regions to draw, the ones with a value, the names without a region and
    the area to show."""
    _check_layer(layer)
    if extent not in EXTENTS:
        raise MapError(f"extent must be one of {', '.join(EXTENTS)}, got {extent!r}")
    values: dict[str, list[Any]] = {}
    for i, row in enumerate(rows, 1):
        if not isinstance(row, Mapping):
            raise MapError(f"row {i} is not a table")
        name, v = row.get(key), row.get(value)
        if name is None:
            continue
        if v is None:
            v = 0
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise MapError(f"row {i}: {value} must be a number, got {v!r}")
        entry = values.setdefault(normalize(name), [str(name), 0])
        entry[1] += v
    countries = _within_filter(data, layer, within)

    def in_within(f: Feature) -> bool:
        if countries is None:
            return True
        country = data.country_of(f)
        return country is not None and normalize(country) in countries

    candidates = [f for f in data.features(layer) if in_within(f)]
    matched = [f for f in candidates if normalize(f.name) in values]
    if layer != "country" and countries is None:
        # States/counties of the countries the data is in, not of the world.
        parents = {f.parent for f in matched}
        candidates = [f for f in candidates if f.parent in parents]
    unmatched = sorted(
        (values[k][0] for k in values.keys() - {normalize(f.name) for f in matched}),
        key=normalize,
    )
    # Extent: the largest part of each region (not e.g. an overseas territory).
    largest: dict[str, Feature] = {}
    for f in (matched if extent == "data" and matched else candidates):
        k = normalize(f.name) + "|" + str(f.parent)
        if k not in largest or f.area > largest[k].area:
            largest[k] = f
    box = _union(f.box for f in largest.values()) or (-180.0, LAT_MIN, 180.0, LAT_MAX)
    return _Selection(values, candidates, {f.id for f in matched}, unmatched, box)


def choropleth(
    data: MapData,
    rows: Sequence[Mapping[str, Any]],
    key: str = "key",
    value: str = "count",
    layer: str = "country",
    within: Any = None,
    title: Optional[str] = None,
    width: int = 640,
    height: Optional[int] = None,
    color: Optional[str] = None,
    extent: str = "data",
    legend: bool = True,
) -> str:
    """A map of the regions of *layer*, coloured by field *value* of the
    row whose field *key* names them. *within* (a country or a list) limits
    a state or county map to those countries. *extent* "data" zooms to the
    regions with a value, "all" shows all regions of the layer (in
    *within*). Regions without a value are drawn grey; names that match no
    region are listed below the map."""
    width = _check_size("width", width)
    if height is not None:
        height = _check_size("height", height)
    fill = _series_style(_check_color(color))
    sel = _select(data, rows, key, value, layer, within, extent)
    values, candidates, matched_ids, unmatched = (
        sel.values, sel.candidates, sel.matched_ids, sel.unmatched)
    matched = [f for f in candidates if f.id in matched_ids]
    box = sel.box
    top = (_TITLE_H if title else 0) + 4
    has_note = bool(unmatched)
    bottom = (24 if legend else 4) + (18 if has_note else 0)
    frame = _Frame(box, width, height, top, bottom)

    edges = class_edges([values[normalize(f.name)][1] for f in matched])
    parts = _svg_open(frame.width, frame.height, title)
    parts.append('<g stroke-linejoin="round">')
    if layer != "country":
        for f in data.features("country"):
            if _intersects(f.box, frame.visible):
                parts.append(_region_path(data, f, frame, _EMPTY_STYLE, f.name))
    for f in (candidates if layer != "country" else data.features("country")):
        if not _intersects(f.box, frame.visible):
            continue
        hit = values.get(normalize(f.name)) if f.id in matched_ids else None
        if hit is not None and hit[1] > 0 and edges:
            i = _class_of(hit[1], edges)
            style = (f'style="fill:{fill}" fill-opacity="{_alpha(i, len(edges)):.2f}" '
                     'stroke="currentColor" stroke-opacity="0.35" stroke-width="0.5"')
            tip = f"{f.name}: {_fmt(hit[1])}"
        else:
            style, tip = _EMPTY_STYLE, f.name
        parts.append(_region_path(data, f, frame, style, tip))
    if layer != "country":
        for f in data.features("country"):
            if _intersects(f.box, frame.visible):
                parts.append(_region_path(data, f, frame, _BORDER_STYLE, None))
    parts.append("</g>")
    y = frame.height - (22 if has_note else 6)
    if legend and edges:
        _legend(parts, edges, fill, y)
    if has_note:
        shown = ", ".join(unmatched[:MAX_UNMATCHED_SHOWN])
        if len(unmatched) > MAX_UNMATCHED_SHOWN:
            shown += f" … (+{len(unmatched) - MAX_UNMATCHED_SHOWN})"
        parts.append(f'<text x="0" y="{frame.height - 4}" font-size="11" fill="currentColor" '
                     f'fill-opacity="0.6">Not on the map: {escape(shown)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def points(
    data: MapData,
    rows: Sequence[Mapping[str, Any]],
    lat: str = "lat",
    lon: str = "lon",
    label: Optional[str] = None,
    layer: str = "country",
    within: Any = None,
    title: Optional[str] = None,
    width: int = 640,
    height: Optional[int] = None,
    color: Optional[str] = None,
    radius: float = 2.5,
) -> str:
    """A dot per row at its *lat*/*lon* over the outlines of *layer*,
    zoomed to the dots (or to the *within* countries). *label* names the
    field shown as tooltip. Dots on the same pixel are drawn once."""
    _check_layer(layer)
    width = _check_size("width", width)
    if height is not None:
        height = _check_size("height", height)
    fill = _series_style(_check_color(color))
    if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not 0.5 <= radius <= 20:
        raise MapError(f"radius must be a number from 0.5 to 20, got {radius!r}")
    if len(rows) > MAX_MAP_POINTS:
        raise MapError(f"at most {MAX_MAP_POINTS} points can be drawn, got {len(rows)}")
    coords: list[tuple[float, float, str]] = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, Mapping):
            raise MapError(f"row {i} is not a table")
        y, x = row.get(lat), row.get(lon)
        if y is None or x is None:
            continue
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (x, y)) \
                or not (-90 <= y <= 90 and -180 <= x <= 180):
            raise MapError(f"row {i}: {lat}/{lon} must be coordinates, got {y!r}, {x!r}")
        coords.append((float(x), float(y), "" if label is None else str(row.get(label) or "")))
    countries = _within_filter(data, layer, within)
    if countries is not None:
        parts_of = [f for f in data.features("country") if normalize(f.name) in countries]
        largest: dict[str, Feature] = {}
        for f in parts_of:
            if f.name not in largest or f.area > largest[f.name].area:
                largest[f.name] = f
        box = _union(f.box for f in largest.values())
    else:
        box = _union((x, y, x, y) for x, y, _ in coords)
    box = box or (-180.0, LAT_MIN, 180.0, LAT_MAX)
    top = (_TITLE_H if title else 0) + 4
    frame = _Frame(box, width, height, top, 4)
    parts = _svg_open(frame.width, frame.height, title)
    parts.append('<g stroke-linejoin="round">')
    background = data.features("country") if layer == "country" else [
        f for f in data.features(layer) if _intersects(f.box, frame.visible)
    ]
    for f in background:
        if _intersects(f.box, frame.visible):
            parts.append(_region_path(data, f, frame, _EMPTY_STYLE, f.name))
    if layer != "country":
        for f in data.features("country"):
            if _intersects(f.box, frame.visible):
                parts.append(_region_path(data, f, frame, _BORDER_STYLE, None))
    parts.append("</g><g>")
    pixels: dict[tuple[int, int], list[Any]] = {}
    for x, y, text in coords:
        px, py = frame.xy(x, y)
        if not (0 <= px <= frame.width and 0 <= py <= frame.height):
            continue
        cell = pixels.setdefault((round(px), round(py)), [px, py, 0, text])
        cell[2] += 1
    for px, py, count, text in pixels.values():
        tip = text + (f" (+{count - 1})" if count > 1 and text else "") if text else f"{count}"
        parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{radius:g}" style="fill:{fill}" '
                     f'fill-opacity="0.75"><title>{escape(tip)}</title></circle>')
    parts.append("</g></svg>")
    return "".join(parts)


def _rounded(coords: Any) -> list:
    return [[round(x, 4), round(y, 4)] for x, y in coords]


def _geojson(geom: Any, frame_box: Box, tolerance: float) -> Optional[dict[str, Any]]:
    """A (multi)polygon clipped to *frame_box*, simplified, as GeoJSON."""
    import shapely

    if geom is None or geom.is_empty:
        return None
    margin = tolerance * 4
    try:
        geom = shapely.clip_by_rect(geom, frame_box[0] - margin, frame_box[1] - margin,
                                    frame_box[2] + margin, frame_box[3] + margin)
        geom = geom.simplify(tolerance * 0.7, preserve_topology=False)
    except Exception:       # a broken outline must not stop the map
        return None
    from shapely.geometry.polygon import orient

    polygons = []
    for poly in getattr(geom, "geoms", [geom]):
        if poly.geom_type != "Polygon" or poly.area < tolerance ** 2:
            continue
        # Outer rings clockwise, holes counter-clockwise: the winding d3-geo
        # (Vega's projections) expects — the other way round it fills the
        # whole globe except the region.
        poly = orient(poly, sign=-1.0)
        rings = [_rounded(poly.exterior.coords)] + [_rounded(r.coords) for r in poly.interiors]
        polygons.append([r for r in rings if len(r) >= 4])
    polygons = [p for p in polygons if p]
    if not polygons:
        return None
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def outlines(
    data: MapData,
    layer: str = "country",
    within: Any = None,
    rows: Optional[Sequence[Mapping[str, Any]]] = None,
    key: str = "key",
    value: str = "count",
    extent: str = "data",
    around: Optional[Sequence[Mapping[str, Any]]] = None,
    lat: str = "lat",
    lon: str = "lon",
    box: Optional[Sequence[float]] = None,
    detail: int = 800,
) -> tuple[dict[str, Any], list[str]]:
    """The outlines of *layer* as a GeoJSON FeatureCollection, e.g. for a
    Vega-Lite geoshape map → (collection, names without a region). Rings
    are wound as d3-geo expects (outer rings clockwise).

    Like choropleth(): *rows* puts the value (field *value*) of each row
    into the properties of the region its *key* names, *within* limits the
    countries, *extent* "data" zooms to the regions with a value. *around*
    (rows with *lat*/*lon*) zooms to those points instead, *box* = {min_lon,
    min_lat, max_lon, max_lat} to that area. Regions are clipped to the
    area and simplified for a map about *detail* pixels wide. Every feature
    has `name` (and `country` for states and counties) in its properties;
    the collection's `bbox` is the area, to pass on as *box*."""
    if isinstance(detail, bool) or not isinstance(detail, (int, float)) or not 100 <= detail <= 4000:
        raise MapError(f"detail must be a number from 100 to 4000, got {detail!r}")
    sel = _select(data, rows or [], key, value, layer, within, extent)
    area: Optional[Box] = sel.box
    if box is not None:
        if not isinstance(box, (list, tuple)) or len(box) != 4 or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) for v in box):
            raise MapError("box must be { min_lon, min_lat, max_lon, max_lat }")
        area = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
    elif around is not None:
        pts = []
        for i, row in enumerate(around, 1):
            if not isinstance(row, Mapping):
                raise MapError(f"around: row {i} is not a table")
            y, x = row.get(lat), row.get(lon)
            if isinstance(x, (int, float)) and isinstance(y, (int, float)) \
                    and not isinstance(x, bool) and not isinstance(y, bool):
                pts.append((float(x), float(y), float(x), float(y)))
        area = _union(pts) or area
    if area is None:
        area = (-180.0, LAT_MIN, 180.0, LAT_MAX)
    lon0, lat0, lon1, lat1 = area
    if box is None:
        pad_x, pad_y = max((lon1 - lon0) * 0.04, 0.05), max((lat1 - lat0) * 0.04, 0.05)
        area = (max(lon0 - pad_x, -180.0), max(lat0 - pad_y, LAT_MIN),
                min(lon1 + pad_x, 180.0), min(lat1 + pad_y, LAT_MAX))
    tolerance = max((area[2] - area[0]) / detail, 1e-5)
    if layer == "country" or sel.values:
        regions = sel.candidates
    else:
        # No values: every region of the area (in the *within* countries).
        countries = _within_filter(data, layer, within)
        regions = [
            f for f in data.features(layer) if _intersects(f.box, area) and (
                countries is None
                or normalize(data.country_of(f) or "") in countries)
        ]
    features = []
    for f in regions:
        if not _intersects(f.box, area):
            continue
        geometry = _geojson(data.shape(f), area, tolerance)
        if geometry is None:
            continue
        properties: dict[str, Any] = {"name": f.name}
        if layer != "country":
            properties["country"] = data.country_of(f)
        if f.id in sel.matched_ids:
            properties[value] = sel.values[normalize(f.name)][1]
        features.append({"type": "Feature", "properties": properties, "geometry": geometry})
    collection = {"type": "FeatureCollection", "bbox": [round(v, 4) for v in area],
                  "features": features}
    return collection, sel.unmatched

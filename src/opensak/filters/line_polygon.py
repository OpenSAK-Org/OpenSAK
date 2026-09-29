"""
src/opensak/filters/line_polygon.py — Geometry and point-list parsing for the
line/polygon filter (GSAK's "Line/Polygon" filter tab).

Pure helpers (no Qt, no database) shared by LinePolygonFilter in engine.py and
the filter dialog:

  parse_points_text()  the dialog's point list → [(lat, lon), …]
  read_points_file()   GPX / KML / plain-text file → [(lat, lon), …]
  LineShape            "is this coordinate on/near the shape?" queries

Distances are great-circle distances on a sphere (same radius as
engine._haversine_km). Polygon containment treats the edges as straight
lines in the latitude/longitude plane, like GSAK — neither works across the
antimeridian or around the poles.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Callable, Optional

from opensak.coords import parse_coords

Point = tuple[float, float]

EARTH_RADIUS_KM = 6371.0

LP_MODES: tuple[str, ...] = ("line", "polygon", "points")

# Fewest points each mode needs to describe a shape.
LP_MIN_POINTS: dict[str, int] = {"line": 2, "polygon": 3, "points": 1}

# "W,GC12345" — take the coordinates of a cache/waypoint from the database.
_CODE_LINE_RE = re.compile(r"^[Ww]\s*,\s*(\S+)$")

# Long segments are indexed as sub-arcs of at most this length, so a
# latitude/longitude box around each piece stays a safe superset of the arc
# (a great circle bulges poleward between its end points). _BOX_PAD_KM covers
# the remaining bulge of such a piece, plus rounding.
_MAX_PIECE_KM = 50.0
_BOX_PAD_KM = 1.0
# Conservative km per degree of latitude (the real value is 110.57–111.69).
_KM_PER_DEG = 110.0
# Upper bound on grid cells per axis for the proximity index.
_GRID_CELLS = 128


# ── Parsing ──────────────────────────────────────────────────────────────────

def parse_point(text: str) -> Optional[Point]:
    """One coordinate in any format parse_coords() accepts — also with a
    comma between latitude and longitude ("N 53 23.613, E 008 00.941")."""
    point = parse_coords(text)
    if point is None and "," in text:
        point = parse_coords(text.replace(",", " "))
    return point


def parse_points_text(
    text: str,
    resolve_code: Optional[Callable[[str], Optional[Point]]] = None,
) -> tuple[list[Point], list[str]]:
    """Parse the dialog's point list — one point per line.

    "W,<code>" lines are looked up with *resolve_code* (upper-cased code →
    (lat, lon) or None). Blank lines and everything after "#" are ignored.
    Returns (points, bad_lines): bad_lines holds every line that could not
    be read (a code *resolve_code* doesn't know included), stripped.
    """
    points: list[Point] = []
    bad: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = _CODE_LINE_RE.match(line)
        if m:
            point = resolve_code(m.group(1).upper()) if resolve_code else None
        else:
            point = parse_point(line)
        if point is None:
            bad.append(line)
        else:
            points.append(point)
    return points, bad


def _local_name(tag: str) -> str:
    """XML tag without its namespace ("{ns}trkpt" → "trkpt")."""
    return tag.rsplit("}", 1)[-1]


def read_points_file(
    path: Path,
    resolve_code: Optional[Callable[[str], Optional[Point]]] = None,
) -> list[Point]:
    """Points from a file, in file order.

    .gpx  track points; if there are none, route points; else waypoints
    .kml  every <coordinates> element ("lon,lat[,alt]" tuples)
    other plain text, one point per line as in the dialog (unreadable lines
          are skipped)

    Raises OSError for an unreadable file, xml.etree.ElementTree.ParseError
    for malformed XML and ValueError for malformed numbers.
    """
    suffix = path.suffix.lower()
    if suffix not in (".gpx", ".kml"):
        text = path.read_text(encoding="utf-8", errors="replace")
        return parse_points_text(text, resolve_code)[0]

    import xml.etree.ElementTree as ET
    root = ET.parse(path).getroot()
    if suffix == ".gpx":
        for kind in ("trkpt", "rtept", "wpt"):
            points = [
                (float(el.get("lat", "")), float(el.get("lon", "")))
                for el in root.iter()
                if _local_name(el.tag) == kind
            ]
            if points:
                return points
        return []
    points = []
    for el in root.iter():
        if _local_name(el.tag) != "coordinates":
            continue
        for tuple_text in (el.text or "").split():
            parts = tuple_text.split(",")
            points.append((float(parts[1]), float(parts[0])))
    return points


# ── Spherical geometry ───────────────────────────────────────────────────────

def _central_angle(phi1: float, lam1: float, phi2: float, lam2: float) -> float:
    """Haversine central angle (radians) between two points in radians."""
    a = (math.sin((phi2 - phi1) / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin((lam2 - lam1) / 2) ** 2)
    a = min(1.0, max(0.0, a))
    return 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _bearing(phi1: float, lam1: float, phi2: float, lam2: float) -> float:
    """Initial great-circle bearing (radians) from point 1 to point 2."""
    dlam = lam2 - lam1
    return math.atan2(
        math.sin(dlam) * math.cos(phi2),
        math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam),
    )


def _intermediate(a: Point, b: Point, fraction: float) -> Point:
    """The point *fraction* of the way along the great circle from a to b."""
    phi1, lam1 = math.radians(a[0]), math.radians(a[1])
    phi2, lam2 = math.radians(b[0]), math.radians(b[1])
    delta = _central_angle(phi1, lam1, phi2, lam2)
    if delta == 0.0:
        return a
    wa = math.sin((1 - fraction) * delta) / math.sin(delta)
    wb = math.sin(fraction * delta) / math.sin(delta)
    x = wa * math.cos(phi1) * math.cos(lam1) + wb * math.cos(phi2) * math.cos(lam2)
    y = wa * math.cos(phi1) * math.sin(lam1) + wb * math.cos(phi2) * math.sin(lam2)
    z = wa * math.sin(phi1) + wb * math.sin(phi2)
    return (
        math.degrees(math.atan2(z, math.hypot(x, y))),
        math.degrees(math.atan2(y, x)),
    )


class _Segment:
    """Great-circle arc a→b (a == b is a single point), with the per-arc
    values distance_km() needs precomputed."""
    __slots__ = ("a", "b", "_phi1", "_lam1", "_phi2", "_lam2", "_length", "_bearing")

    def __init__(self, a: Point, b: Point):
        self.a, self.b = a, b
        self._phi1, self._lam1 = math.radians(a[0]), math.radians(a[1])
        self._phi2, self._lam2 = math.radians(b[0]), math.radians(b[1])
        self._length = _central_angle(self._phi1, self._lam1, self._phi2, self._lam2)
        self._bearing = _bearing(self._phi1, self._lam1, self._phi2, self._lam2)

    def distance_km(self, lat: float, lon: float) -> float:
        """Shortest distance from (lat, lon) to any point of the arc."""
        phi, lam = math.radians(lat), math.radians(lon)
        d13 = _central_angle(self._phi1, self._lam1, phi, lam)
        if self._length == 0.0 or d13 == 0.0:
            return d13 * EARTH_RADIUS_KM
        rel = _bearing(self._phi1, self._lam1, phi, lam) - self._bearing
        if math.cos(rel) <= 0.0:
            return d13 * EARTH_RADIUS_KM  # behind the start point
        cross = math.asin(max(-1.0, min(1.0, math.sin(d13) * math.sin(rel))))
        cos_cross = math.cos(cross)
        if cos_cross == 0.0:
            return d13 * EARTH_RADIUS_KM
        along = math.acos(max(-1.0, min(1.0, math.cos(d13) / cos_cross)))
        if along >= self._length:
            return _central_angle(self._phi2, self._lam2, phi, lam) * EARTH_RADIUS_KM
        return abs(cross) * EARTH_RADIUS_KM


def point_segment_km(lat: float, lon: float, a: Point, b: Point) -> float:
    """Shortest great-circle distance (km) from (lat, lon) to the arc a→b."""
    return _Segment(a, b).distance_km(lat, lon)


def _pieces(a: Point, b: Point) -> list[_Segment]:
    """Arc a→b split into sub-arcs of at most _MAX_PIECE_KM."""
    whole = _Segment(a, b)
    count = max(1, math.ceil(whole._length * EARTH_RADIUS_KM / _MAX_PIECE_KM))
    if count == 1:
        return [whole]
    stops = [a] + [_intermediate(a, b, i / count) for i in range(1, count)] + [b]
    return [_Segment(p, q) for p, q in zip(stops, stops[1:])]


# ── Shape ────────────────────────────────────────────────────────────────────

BBox = tuple[float, float, float, float]  # lat_lo, lat_hi, lon_lo, lon_hi


class LineShape:
    """A line, polygon or point set plus a distance, answering contains().

    line     within *distance_km* of the polyline through the points
    polygon  inside the polygon (closed automatically), or within
             *distance_km* of its outline
    points   within *distance_km* of any single point

    Built once per filter; contains() is called once per cache, so both
    queries go through small spatial indexes instead of looping over every
    segment/edge.
    """

    def __init__(self, points: list[Point], mode: str, distance_km: float):
        if mode not in LP_MODES:
            raise ValueError(f"mode must be one of {LP_MODES}, got {mode!r}")
        if len(points) < LP_MIN_POINTS[mode]:
            raise ValueError(f"{mode} needs at least {LP_MIN_POINTS[mode]} points")
        self.mode = mode
        self.distance_km = max(0.0, float(distance_km))

        if mode == "points":
            pairs = [(p, p) for p in points]
        elif mode == "line":
            pairs = list(zip(points, points[1:]))
        else:
            pairs = list(zip(points, points[1:] + points[:1]))
        self._segments = [piece for a, b in pairs for piece in _pieces(a, b)]

        self._edges: list[tuple[float, float, float, float]] = []
        if mode == "polygon":
            self._edges = [(a[0], a[1], b[0], b[1]) for a, b in pairs]
            self._build_band_index(points)

        # Bounding box of everything contains() can accept, or None when it
        # would reach a pole or wrap the antimeridian (no shortcuts then).
        self.bbox: Optional[BBox] = None
        self._grid: Optional[dict[tuple[int, int], list[_Segment]]] = None
        reach = self.distance_km + _BOX_PAD_KM
        self._dlat = reach / _KM_PER_DEG
        lats = [p[0] for seg in self._segments for p in (seg.a, seg.b)]
        lons = [p[1] for seg in self._segments for p in (seg.a, seg.b)]
        lat_lo, lat_hi = min(lats) - self._dlat, max(lats) + self._dlat
        if max(abs(lat_lo), abs(lat_hi)) >= 89.0:
            return
        self._dlon = reach / (_KM_PER_DEG * math.cos(math.radians(max(abs(lat_lo), abs(lat_hi)))))
        lon_lo, lon_hi = min(lons) - self._dlon, max(lons) + self._dlon
        if lon_lo < -180.0 or lon_hi > 180.0:
            return
        self.bbox = (lat_lo, lat_hi, lon_lo, lon_hi)
        if self.distance_km > 0:
            self._build_grid()

    # ── indexes ──────────────────────────────────────────────────────────────

    def _build_band_index(self, points: list[Point]) -> None:
        """Bucket polygon edges by latitude band: a horizontal ray at some
        latitude can only cross edges whose latitude range includes it."""
        self._poly_lat_lo = min(p[0] for p in points)
        self._poly_lat_hi = max(p[0] for p in points)
        span = self._poly_lat_hi - self._poly_lat_lo
        self._band_count = max(1, min(len(self._edges), 512))
        self._band_size = span / self._band_count if span > 0 else 1.0
        self._bands: list[list[tuple[float, float, float, float]]] = [
            [] for _ in range(self._band_count)
        ]
        for edge in self._edges:
            lo = self._band_of(min(edge[0], edge[2]))
            hi = self._band_of(max(edge[0], edge[2]))
            for band in range(lo, hi + 1):
                self._bands[band].append(edge)

    def _band_of(self, lat: float) -> int:
        band = int((lat - self._poly_lat_lo) / self._band_size)
        return min(self._band_count - 1, max(0, band))

    def _build_grid(self) -> None:
        """Map grid cells to the segments whose distance-grown box touches
        them, so a query only measures against nearby segments."""
        assert self.bbox is not None
        lat_lo, lat_hi, lon_lo, lon_hi = self.bbox
        self._cell = max((lat_hi - lat_lo) / _GRID_CELLS,
                         (lon_hi - lon_lo) / _GRID_CELLS, 1e-4)
        grid: dict[tuple[int, int], list[_Segment]] = {}
        for seg in self._segments:
            r0, c0 = self._cell_of(min(seg.a[0], seg.b[0]) - self._dlat,
                                   min(seg.a[1], seg.b[1]) - self._dlon)
            r1, c1 = self._cell_of(max(seg.a[0], seg.b[0]) + self._dlat,
                                   max(seg.a[1], seg.b[1]) + self._dlon)
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    grid.setdefault((r, c), []).append(seg)
        self._grid = grid

    def _cell_of(self, lat: float, lon: float) -> tuple[int, int]:
        assert self.bbox is not None
        return (int((lat - self.bbox[0]) // self._cell),
                int((lon - self.bbox[2]) // self._cell))

    # ── queries ──────────────────────────────────────────────────────────────

    def contains(self, lat: float, lon: float) -> bool:
        """True if (lat, lon) is inside/near the shape (see class docstring)."""
        if self.bbox is not None:
            lat_lo, lat_hi, lon_lo, lon_hi = self.bbox
            if not (lat_lo <= lat <= lat_hi and lon_lo <= lon <= lon_hi):
                return False
        if self.mode == "polygon" and self._inside_polygon(lat, lon):
            return True
        if self.distance_km <= 0:
            return False
        return self._near(lat, lon)

    def _inside_polygon(self, lat: float, lon: float) -> bool:
        if not (self._poly_lat_lo <= lat <= self._poly_lat_hi):
            return False
        inside = False
        for lat1, lon1, lat2, lon2 in self._bands[self._band_of(lat)]:
            if (lat1 > lat) != (lat2 > lat):
                cross_lon = lon1 + (lat - lat1) * (lon2 - lon1) / (lat2 - lat1)
                if lon < cross_lon:
                    inside = not inside
        return inside

    def _near(self, lat: float, lon: float) -> bool:
        if self._grid is not None:
            candidates = self._grid.get(self._cell_of(lat, lon), ())
        else:
            candidates = self._segments
        return any(seg.distance_km(lat, lon) <= self.distance_km for seg in candidates)

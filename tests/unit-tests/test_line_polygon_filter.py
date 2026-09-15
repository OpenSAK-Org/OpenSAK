# tests/unit-tests/test_line_polygon_filter.py — line/polygon filter (GSAK's
# "Line/Polygon" tab): parsing, geometry, engine filter and SQL pushdown.

import json
import math
import random
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from opensak.db.models import Cache, UserNote, Waypoint
from opensak.filters import line_polygon
from opensak.filters.engine import (
    FILTER_REGISTRY, FilterSet, LinePolygonFilter,
    apply_filters, apply_filters_lightweight, lookup_code_coords, user_flagged_codes,
)
from opensak.filters.line_polygon import (
    LineShape, parse_point, parse_points_text, point_segment_km, read_points_file,
)

KM_PER_DEG = 6371.0 * math.pi / 180  # one degree of latitude on the sphere


def _cache(lat, lon, corrected=None):
    note = None
    if corrected is not None:
        note = SimpleNamespace(is_corrected=True, corrected_lat=corrected[0],
                               corrected_lon=corrected[1])
    return SimpleNamespace(latitude=lat, longitude=lon, user_note=note)


def _brute_force(points, mode, distance_km, lat, lon):
    """Reference LineShape.contains() without any index."""
    if mode == "points":
        pairs = [(p, p) for p in points]
    elif mode == "line":
        pairs = list(zip(points, points[1:]))
    else:
        pairs = list(zip(points, points[1:] + points[:1]))
    if mode == "polygon":
        inside = False
        for (lat1, lon1), (lat2, lon2) in pairs:
            if (lat1 > lat) != (lat2 > lat) and \
                    lon < lon1 + (lat - lat1) * (lon2 - lon1) / (lat2 - lat1):
                inside = not inside
        if inside:
            return True
    return distance_km > 0 and min(
        point_segment_km(lat, lon, a, b) for a, b in pairs
    ) <= distance_km


# ── parsing ───────────────────────────────────────────────────────────────────

class TestParsing:
    def test_decimal_degrees(self):
        assert parse_point("53.18346, 8.71113") == (53.18346, 8.71113)

    def test_dmm_with_comma(self):
        lat, lon = parse_point("N 53 23.613, E 008 00.941")
        assert lat == pytest.approx(53 + 23.613 / 60)
        assert lon == pytest.approx(8 + 0.941 / 60)

    def test_unparseable(self):
        # Decimal commas are rejected rather than misread.
        assert parse_point("53,18346 8,71113") is None
        assert parse_point("hello") is None

    def test_comments_blank_lines_and_bad_lines(self):
        text = "# header\n\n55.0, 12.0  # start\n  N 55 30.000 E 012 30.000\nnonsense\n"
        points, bad = parse_points_text(text)
        assert points == [(55.0, 12.0), (55.5, 12.5)]
        assert bad == ["nonsense"]

    def test_code_lines_use_resolver(self):
        seen = []

        def resolve(code):
            seen.append(code)
            return (56.0, 10.0) if code == "GC12345" else None

        points, bad = parse_points_text("W,gc12345\nw, GCNOPE", resolve)
        assert points == [(56.0, 10.0)]
        assert bad == ["w, GCNOPE"]
        assert seen == ["GC12345", "GCNOPE"]

    def test_code_line_without_resolver_is_bad(self):
        assert parse_points_text("W,GC12345") == ([], ["W,GC12345"])


# ── files ─────────────────────────────────────────────────────────────────────

_GPX = """<?xml version="1.0"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">
  <wpt lat="1.0" lon="2.0"/>
  {body}
</gpx>"""


class TestReadPointsFile:
    def test_gpx_prefers_track(self, tmp_path):
        path = tmp_path / "t.gpx"
        path.write_text(_GPX.format(body=(
            '<rte><rtept lat="3" lon="4"/></rte>'
            '<trk><trkseg><trkpt lat="5" lon="6"/><trkpt lat="7" lon="8"/></trkseg></trk>'
        )), encoding="utf-8")
        assert read_points_file(path) == [(5.0, 6.0), (7.0, 8.0)]

    def test_gpx_route_then_waypoints(self, tmp_path):
        path = tmp_path / "r.gpx"
        path.write_text(_GPX.format(body='<rte><rtept lat="3" lon="4"/></rte>'), encoding="utf-8")
        assert read_points_file(path) == [(3.0, 4.0)]
        path.write_text(_GPX.format(body=""), encoding="utf-8")
        assert read_points_file(path) == [(1.0, 2.0)]

    def test_kml(self, tmp_path):
        path = tmp_path / "a.kml"
        path.write_text(
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><LineString>'
            '<coordinates>8.7,53.1,0 8.8,53.2</coordinates>'
            '</LineString></Placemark></kml>',
            encoding="utf-8",
        )
        assert read_points_file(path) == [(53.1, 8.7), (53.2, 8.8)]

    def test_text_file_skips_bad_lines(self, tmp_path):
        path = tmp_path / "pts.txt"
        path.write_text("# route\n55.0, 12.0\nbad\nN 55 30.000, E 012 30.000\n", encoding="utf-8")
        assert read_points_file(path) == [(55.0, 12.0), (55.5, 12.5)]

    def test_malformed_xml_raises(self, tmp_path):
        path = tmp_path / "x.gpx"
        path.write_text("<gpx>", encoding="utf-8")
        with pytest.raises(ET.ParseError):
            read_points_file(path)


# ── geometry ──────────────────────────────────────────────────────────────────

class TestPointSegmentDistance:
    def test_perpendicular(self):
        # Cross-track distance to the equator is the latitude itself.
        d = point_segment_km(0.1, 0.5, (0.0, 0.0), (0.0, 1.0))
        assert d == pytest.approx(0.1 * KM_PER_DEG, rel=1e-6)

    def test_on_segment(self):
        assert point_segment_km(0.0, 0.5, (0.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0, abs=1e-6)

    def test_beyond_end_and_behind_start(self):
        assert point_segment_km(0.0, 2.0, (0.0, 0.0), (0.0, 1.0)) == pytest.approx(KM_PER_DEG, rel=1e-6)
        assert point_segment_km(0.0, -1.0, (0.0, 0.0), (0.0, 1.0)) == pytest.approx(KM_PER_DEG, rel=1e-6)

    def test_degenerate_segment_is_a_point(self):
        assert point_segment_km(1.0, 0.0, (0.0, 0.0), (0.0, 0.0)) == pytest.approx(KM_PER_DEG, rel=1e-6)


_SQUARE = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]


class TestLineShape:
    def test_line(self):
        shape = LineShape([(0.0, 0.0), (0.0, 1.0), (1.0, 1.0)], "line", 5.0)
        assert shape.contains(0.03, 0.5)        # ~3.3 km from the first leg
        assert not shape.contains(0.1, 0.5)     # ~11 km
        assert shape.contains(0.5, 1.04)        # ~4.4 km from the second leg
        assert not shape.contains(0.5, 0.5)     # between the legs, far from both

    def test_polygon_inside_outside(self):
        shape = LineShape(_SQUARE, "polygon", 0.0)
        assert shape.contains(0.5, 0.5)
        assert not shape.contains(1.01, 0.5)
        assert not shape.contains(0.5, -0.01)

    def test_polygon_distance_includes_band_around_outline(self):
        shape = LineShape(_SQUARE, "polygon", 5.0)
        assert shape.contains(0.5, 0.5)
        assert shape.contains(1.03, 0.5)       # 3.3 km outside the top edge
        assert not shape.contains(1.1, 0.5)

    def test_concave_polygon(self):
        # L-shape — the notch at the top right is outside.
        l_shape = [(0.0, 0.0), (0.0, 2.0), (1.0, 2.0), (1.0, 1.0), (2.0, 1.0), (2.0, 0.0)]
        shape = LineShape(l_shape, "polygon", 0.0)
        assert shape.contains(0.5, 1.5)
        assert shape.contains(1.5, 0.5)
        assert not shape.contains(1.5, 1.5)

    def test_points(self):
        shape = LineShape([(0.0, 0.0), (0.0, 1.0)], "points", 5.0)
        assert shape.contains(0.03, 0.0)
        assert shape.contains(0.0, 1.03)
        assert not shape.contains(0.0, 0.5)     # between the points, ~55 km from both

    def test_long_segment_follows_great_circle(self):
        # A ~1000 km east-west leg at 50°N bulges ~20 km north of the 50°
        # parallel midway; the bounding box / grid must not cut that off.
        a, b = (50.0, 0.0), (50.0, 14.0)
        mid_lat, mid_lon = line_polygon._intermediate(a, b, 0.5)
        assert mid_lat > 50.15
        shape = LineShape([a, b], "line", 0.5)
        assert shape.contains(mid_lat, mid_lon)
        assert not shape.contains(50.0, 7.0)

    def test_no_bbox_across_antimeridian_still_matches(self):
        shape = LineShape([(0.0, 179.9), (0.0, 179.99)], "line", 20.0)
        assert shape.bbox is None
        assert shape.contains(0.0, -179.95)    # ~6.7 km past the end, across ±180°

    @pytest.mark.parametrize("mode", ["line", "polygon", "points"])
    def test_index_agrees_with_brute_force(self, mode):
        rng = random.Random(42)
        points = [(55 + rng.uniform(-1, 1), 10 + rng.uniform(-1, 1)) for _ in range(40)]
        shape = LineShape(points, mode, 8.0)
        for _ in range(3000):
            lat, lon = 55 + rng.uniform(-1.3, 1.3), 10 + rng.uniform(-1.3, 1.3)
            assert shape.contains(lat, lon) == _brute_force(points, mode, 8.0, lat, lon), (lat, lon)

    @pytest.mark.parametrize("mode, count", [("line", 1), ("polygon", 2), ("points", 0)])
    def test_too_few_points(self, mode, count):
        with pytest.raises(ValueError):
            LineShape([(0.0, float(i)) for i in range(count)], mode, 1.0)

    def test_unknown_mode(self):
        with pytest.raises(ValueError):
            LineShape(_SQUARE, "circle", 1.0)


# ── LinePolygonFilter ─────────────────────────────────────────────────────────

_LINE = [(55.0, 10.0), (55.0, 11.0)]


class TestLinePolygonFilter:
    def test_matches_and_exclude(self):
        near, far = _cache(55.01, 10.5), _cache(55.2, 10.5)
        f = LinePolygonFilter(_LINE, "line", 2.0)
        assert f.matches(near) and not f.matches(far)
        g = LinePolygonFilter(_LINE, "line", 2.0, exclude=True)
        assert not g.matches(near) and g.matches(far)

    def test_uses_corrected_coordinates(self):
        f = LinePolygonFilter(_LINE, "line", 2.0)
        assert f.matches(_cache(56.0, 10.5, corrected=(55.01, 10.5)))
        assert not f.matches(_cache(55.01, 10.5, corrected=(56.0, 10.5)))

    def test_roundtrip(self):
        f = LinePolygonFilter(
            [(55.0, 10.0), (55.0, 11.0), (56.0, 10.5)], "polygon", 0.5,
            exclude=True, text="W,GC1\n55.0, 11.0",
        )
        data = json.loads(json.dumps(f.to_dict()))
        restored = FILTER_REGISTRY[data["filter_type"]].from_dict(data)
        assert type(restored) is LinePolygonFilter
        assert restored.to_dict() == f.to_dict()

    def test_filterset_roundtrip(self):
        fs = FilterSet().add(LinePolygonFilter(_LINE, "line", 1.5))
        restored = FilterSet.from_dict(json.loads(json.dumps(fs.to_dict())))
        assert [f.to_dict() for f in restored._filters] == [f.to_dict() for f in fs._filters]

    def test_invalid(self):
        with pytest.raises(ValueError):
            LinePolygonFilter(_LINE, "circle", 1.0)
        with pytest.raises(ValueError):
            LinePolygonFilter(_LINE, "polygon", 1.0)   # needs three points


# ── database: SQL pushdown, code lookup, flagged caches ───────────────────────

@pytest.fixture
def seeded(db_session):
    db_session.add_all([
        Cache(gc_code="GCNEAR", name="near", cache_type="Traditional Cache",
              latitude=55.01, longitude=10.5),
        Cache(gc_code="GCFAR", name="far", cache_type="Traditional Cache",
              latitude=55.5, longitude=10.5),
        # Posted far away, solved final on the line.
        Cache(gc_code="GCSOLVED", name="solved", cache_type="Unknown Cache",
              latitude=57.0, longitude=10.5,
              user_note=UserNote(is_corrected=True, corrected_lat=55.005, corrected_lon=10.2)),
        # Posted on the line, solved final far away.
        Cache(gc_code="GCMOVED", name="moved", cache_type="Unknown Cache",
              latitude=55.0, longitude=10.8,
              user_note=UserNote(is_corrected=True, corrected_lat=58.0, corrected_lon=10.8)),
    ])
    db_session.commit()
    return db_session


@pytest.mark.parametrize("apply", [apply_filters, apply_filters_lightweight])
def test_query_uses_effective_coordinates(seeded, apply):
    fs = FilterSet().add(LinePolygonFilter(_LINE, "line", 2.0))
    assert {c.gc_code for c in apply(seeded, fs)} == {"GCNEAR", "GCSOLVED"}


@pytest.mark.parametrize("apply", [apply_filters, apply_filters_lightweight])
def test_query_exclude(seeded, apply):
    fs = FilterSet().add(LinePolygonFilter(_LINE, "line", 2.0, exclude=True))
    assert {c.gc_code for c in apply(seeded, fs)} == {"GCFAR", "GCMOVED"}


def test_bbox_pushdown_is_a_superset(seeded):
    # Raw OR corrected coordinates inside the box pass the SQL pre-filter;
    # matches() then drops GCMOVED, whose final is far away.
    f = LinePolygonFilter(_LINE, "line", 2.0)
    query = f.apply_to_query(seeded.query(Cache))
    assert {c.gc_code for c in query.all()} == {"GCNEAR", "GCSOLVED", "GCMOVED"}
    assert f.sql_exact is False
    assert LinePolygonFilter(_LINE, "line", 2.0, exclude=True).apply_to_query(seeded.query(Cache)) is None


def test_lookup_code_coords(seeded):
    cache = seeded.query(Cache).filter_by(gc_code="GCNEAR").one()
    seeded.add_all([
        Waypoint(cache_id=cache.id, prefix="PK", wp_type="Parking Area",
                 latitude=55.02, longitude=10.51),
        Waypoint(cache_id=cache.id, prefix="S1", wp_type="Stage", wp_code="S1XYZ",
                 latitude=55.03, longitude=10.52),
        Waypoint(cache_id=cache.id, prefix="FN", wp_type="Final"),
    ])
    seeded.commit()
    assert lookup_code_coords(seeded, "gcnear") == (55.01, 10.5)
    assert lookup_code_coords(seeded, "GCSOLVED") == (55.005, 10.2)   # corrected
    assert lookup_code_coords(seeded, "s1xyz") == (55.03, 10.52)
    assert lookup_code_coords(seeded, "PKNEAR") == (55.02, 10.51)
    assert lookup_code_coords(seeded, "FNNEAR") is None                # no coordinates
    assert lookup_code_coords(seeded, "GCNOPE") is None
    assert lookup_code_coords(seeded, " ") is None


def test_user_flagged_codes(db_session):
    def make(code, **kwargs):
        return Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                     latitude=55.0, longitude=10.0, **kwargs)
    db_session.add_all([
        make("GCB", user_flag=True),
        make("GCA", user_flag=True),
        make("GCC", user_flag=True, user_sort=1),
        make("GCD", user_flag=False),
    ])
    db_session.commit()
    assert user_flagged_codes(db_session) == ["GCC", "GCA", "GCB"]

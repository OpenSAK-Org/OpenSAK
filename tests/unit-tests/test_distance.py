"""tests/unit-tests/test_distance.py — distance/bearing math + DistanceFilter.

Batch (numpy) haversine/bearing must match the scalar versions, and the bbox
pre-narrow must return the same caches as the exact Python filter.
Also covers Vincenty accuracy, distance_km_batch dispatcher, and
recalculate_distances() DB population.
"""

import math

import pytest

from opensak.db.database import get_session, make_session
from opensak.db.models import Cache
from opensak.filters.engine import (
    _haversine_km, haversine_km_batch, apply_filters, FilterSet, DistanceFilter,
    _vincenty_km, vincenty_km_batch, distance_km, distance_km_batch,
)
from opensak.gui.cache_table import _bearing_deg, _bearing_deg_batch


# ── Batch vs scalar parity ────────────────────────────────────────────────────

SAMPLE_POINTS = [
    (55.6761, 12.5683),   # Copenhagen
    (56.1629, 10.2039),   # Aarhus
    (52.5200, 13.4050),   # Berlin
    (-33.8688, 151.2093), # Sydney (southern hemisphere)
    (64.1466, -21.9426),  # Reykjavik
    (0.0, 0.0),           # null island
    (55.6761, 12.5683),   # exact same as origin → distance 0
]


@pytest.mark.parametrize("origin", [(55.6761, 12.5683), (-10.0, 170.0)])
def test_haversine_batch_matches_scalar(origin):
    lat0, lon0 = origin
    lats = [p[0] for p in SAMPLE_POINTS]
    lons = [p[1] for p in SAMPLE_POINTS]
    batch = haversine_km_batch(lat0, lon0, lats, lons)
    for i, (la, lo) in enumerate(SAMPLE_POINTS):
        assert float(batch[i]) == pytest.approx(_haversine_km(lat0, lon0, la, lo), abs=1e-6)


@pytest.mark.parametrize("origin", [(55.6761, 12.5683), (-10.0, 170.0)])
def test_bearing_batch_matches_scalar(origin):
    lat0, lon0 = origin
    lats = [p[0] for p in SAMPLE_POINTS]
    lons = [p[1] for p in SAMPLE_POINTS]
    batch = _bearing_deg_batch(lat0, lon0, lats, lons)
    for i, (la, lo) in enumerate(SAMPLE_POINTS):
        assert float(batch[i]) == pytest.approx(_bearing_deg(lat0, lon0, la, lo), abs=1e-6)


def test_haversine_batch_empty():
    assert list(haversine_km_batch(55.0, 12.0, [], [])) == []


# ── Bounding-box DistanceFilter parity ────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def seed_grid(tmp_db):
    # A grid of caches around Copenhagen + a couple of far-away ones.
    rows = []
    n = 0
    for dlat in range(-10, 11):
        for dlon in range(-10, 11):
            n += 1
            rows.append(Cache(
                gc_code=f"GCG{n:04d}", name=f"grid {n}", cache_type="Traditional Cache",
                latitude=55.6761 + dlat * 0.05, longitude=12.5683 + dlon * 0.05,
            ))
    # Far away — must always be excluded by a Copenhagen-centred filter
    rows.append(Cache(gc_code="GCFAR1", name="Sydney", cache_type="Traditional Cache",
                      latitude=-33.8688, longitude=151.2093))
    rows.append(Cache(gc_code="GCFAR2", name="Berlin", cache_type="Traditional Cache",
                      latitude=52.52, longitude=13.405))
    s = make_session()
    for r in rows:
        s.add(r)
    s.commit()
    s.close()


def _python_only(fs):
    # Result if the filter ran purely in Python (no SQL pre-narrow).
    with get_session() as s:
        rows = s.query(Cache).all()
        return {c.gc_code for c in rows if fs.matches(c)}


def _via_apply_filters(fs):
    with get_session() as s:
        return {c.gc_code for c in apply_filters(s, fs)}


@pytest.mark.parametrize("max_km", [1.0, 5.0, 10.0, 25.0, 100.0])
def test_distance_filter_bbox_matches_python(max_km):
    fs = FilterSet().add(DistanceFilter(lat=55.6761, lon=12.5683, max_km=max_km))
    assert _via_apply_filters(fs) == _python_only(fs)


def test_distance_filter_with_min_km():
    fs = FilterSet().add(DistanceFilter(lat=55.6761, lon=12.5683, max_km=20.0, min_km=5.0))
    assert _via_apply_filters(fs) == _python_only(fs)


def test_distance_filter_bbox_skipped_near_antimeridian():
    # lon close to 180 → box would wrap; apply_to_query must return None so the
    # filter falls back to Python and still returns the correct set.
    f = DistanceFilter(lat=0.0, lon=179.9, max_km=50.0)
    with get_session() as s:
        assert f.apply_to_query(s.query(Cache)) is None
    fs = FilterSet().add(f)
    assert _via_apply_filters(fs) == _python_only(fs)


def test_distance_filter_bbox_skipped_for_zero_radius():
    f = DistanceFilter(lat=55.0, lon=12.0, max_km=0.0)
    with get_session() as s:
        assert f.apply_to_query(s.query(Cache)) is None


# ── Index exists ──────────────────────────────────────────────────────────────

def test_lat_lon_index_created(tmp_db):
    from sqlalchemy import text
    with get_session() as s:
        names = {
            row[0] for row in s.execute(text(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='caches'"
            ))
        }
    assert "ix_caches_lat_lon" in names


# ── Vincenty ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lat1, lon1, lat2, lon2", [
    (55.6761, 12.5683, 56.1629, 10.2039),   # Copenhagen → Aarhus
    (55.6761, 12.5683, 52.5200, 13.4050),   # Copenhagen → Berlin
    (0.0,     0.0,     1.0,     1.0),        # short equatorial leg
    (0.0,     0.0,     0.0,    90.0),        # quarter-meridian
])
def test_vincenty_agrees_with_haversine_within_half_percent(lat1, lon1, lat2, lon2):
    h = _haversine_km(lat1, lon1, lat2, lon2)
    v = _vincenty_km(lat1, lon1, lat2, lon2)
    # Ellipsoidal vs spherical; agreement should be within 0.5 % for real cases.
    assert abs(h - v) / max(h, 1e-9) < 0.005


def test_vincenty_more_accurate_than_haversine_long_distance():
    # For long distances, Vincenty should differ slightly from Haversine
    # (Earth is oblate, not spherical); both should be within 0.5 % of each other.
    lat1, lon1 = 55.6761, 12.5683   # Copenhagen
    lat2, lon2 = -33.8688, 151.2093  # Sydney
    h = _haversine_km(lat1, lon1, lat2, lon2)
    v = _vincenty_km(lat1, lon1, lat2, lon2)
    assert abs(h - v) / v < 0.005   # < 0.5 % difference
    assert v != h                    # they must actually differ


def test_vincenty_batch_matches_scalar():
    origin = (55.6761, 12.5683)
    lats = [p[0] for p in SAMPLE_POINTS]
    lons = [p[1] for p in SAMPLE_POINTS]
    lat0, lon0 = origin
    batch = vincenty_km_batch(lat0, lon0, lats, lons)
    for i, (la, lo) in enumerate(SAMPLE_POINTS):
        assert batch[i] == pytest.approx(_vincenty_km(lat0, lon0, la, lo), abs=1e-9)


def test_vincenty_coincident_points():
    assert _vincenty_km(55.0, 12.0, 55.0, 12.0) == 0.0


# ── distance_km / distance_km_batch dispatcher ────────────────────────────────

def test_dispatcher_uses_haversine_by_default(monkeypatch):
    from opensak.gui import settings as smod
    monkeypatch.setattr(smod.get_settings(), "distance_method", "haversine")
    d = distance_km(55.6761, 12.5683, 56.1629, 10.2039)
    expected = _haversine_km(55.6761, 12.5683, 56.1629, 10.2039)
    assert d == pytest.approx(expected, abs=1e-9)


def test_dispatcher_uses_vincenty_when_set(monkeypatch):
    from opensak.gui import settings as smod
    monkeypatch.setattr(smod.get_settings(), "distance_method", "vincenty")
    d = distance_km(55.6761, 12.5683, 56.1629, 10.2039)
    expected = _vincenty_km(55.6761, 12.5683, 56.1629, 10.2039)
    assert d == pytest.approx(expected, abs=1e-9)


# ── recalculate_distances() DB population ─────────────────────────────────────

@pytest.fixture()
def two_cache_db(tmp_path):
    from opensak.db.database import init_db
    db_path = tmp_path / "two.db"
    init_db(db_path=db_path)
    s = make_session()
    s.add(Cache(gc_code="GCAAA1", name="Alpha", cache_type="Traditional Cache",
                latitude=55.6761, longitude=12.5683))
    s.add(Cache(gc_code="GCAAA2", name="Beta",  cache_type="Traditional Cache",
                latitude=56.1629, longitude=10.2039))
    s.commit()
    s.close()
    return db_path


def test_recalculate_distances_populates_column(two_cache_db):
    from opensak.db.database import recalculate_distances
    home_lat, home_lon = 55.6761, 12.5683  # Copenhagen
    count = recalculate_distances(home_lat, home_lon)
    assert count == 2

    with get_session() as s:
        caches = {c.gc_code: c for c in s.query(Cache).all()}

    # Alpha is at the home point — distance must be 0
    assert caches["GCAAA1"].distance == pytest.approx(0.0, abs=0.01)
    assert caches["GCAAA1"].bearing is not None

    # Beta (Aarhus) is ~157 km NW — distance must be in that ballpark
    assert 140.0 < caches["GCAAA2"].distance < 175.0
    assert caches["GCAAA2"].bearing is not None


def test_recalculate_distances_returns_zero_on_empty_db(tmp_path):
    from opensak.db.database import init_db, recalculate_distances
    db_path = tmp_path / "empty.db"
    init_db(db_path=db_path)
    assert recalculate_distances(55.0, 12.0) == 0


def test_recalculate_distances_consistent_with_haversine(two_cache_db):
    from opensak.db.database import recalculate_distances
    home_lat, home_lon = 55.6761, 12.5683
    recalculate_distances(home_lat, home_lon)

    with get_session() as s:
        caches = {c.gc_code: c for c in s.query(Cache).all()}

    # Default method is Haversine — stored value must match scalar exactly.
    expected = _haversine_km(home_lat, home_lon, 56.1629, 10.2039)
    assert caches["GCAAA2"].distance == pytest.approx(expected, rel=1e-4)

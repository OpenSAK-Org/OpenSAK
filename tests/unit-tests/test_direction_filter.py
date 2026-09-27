"""tests/unit-tests/test_direction_filter.py — DirectionFilter (GSAK "Direction").

Keeps caches whose persisted Cache.bearing (from the home point) falls in one
of the selected 45° compass sectors. SQL pushdown must agree with matches(),
including sector boundaries, the N wrap-around at 0°/360° and NULL bearings.
"""

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.filters.engine import (
    DIRECTIONS, FILTER_REGISTRY, DirectionFilter, FilterSet, apply_filters,
    bearing_direction,
)

# gc_code -> bearing; chosen to hit every sector plus the boundaries.
_BEARINGS = {
    "GCDIR01": 0.0,     # N
    "GCDIR02": 359.9,   # N (wraps)
    "GCDIR03": 22.4,    # N
    "GCDIR04": 22.5,    # NE (half-open boundary)
    "GCDIR05": 90.0,    # E
    "GCDIR06": 135.0,   # SE
    "GCDIR07": 180.0,   # S
    "GCDIR08": 225.0,   # SW
    "GCDIR09": 270.0,   # W
    "GCDIR10": 315.0,   # NW
    "GCDIR11": 337.5,   # N (boundary)
    "GCDIR12": None,    # no bearing — never matches
}


@pytest.fixture(scope="module", autouse=True)
def seed_direction_data(tmp_db):
    with get_session() as s:
        for code, bearing in _BEARINGS.items():
            s.add(Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                        latitude=55.0, longitude=12.0, bearing=bearing))


def _run(fs):
    with get_session() as s:
        pushed = {c.gc_code for c in apply_filters(s, fs)}
        python = {c.gc_code for c in apply_filters(s, None) if fs.matches(c)}
    assert pushed == python, (
        f"only in SQL-pushed: {pushed - python}\n"
        f"only in Python matches(): {python - pushed}"
    )
    return pushed


@pytest.mark.parametrize("deg,expected", [
    (0.0, "N"), (22.4, "N"), (22.5, "NE"), (67.5, "E"), (180.0, "S"),
    (337.4, "NW"), (337.5, "N"), (359.9, "N"), (360.0, "N"), (-10.0, "N"),
])
def test_bearing_direction(deg, expected):
    assert bearing_direction(deg) == expected


def test_north_wraps_around_zero():
    assert _run(FilterSet().add(DirectionFilter(["N"]))) == {
        "GCDIR01", "GCDIR02", "GCDIR03", "GCDIR11"}


def test_boundary_belongs_to_next_sector():
    assert _run(FilterSet().add(DirectionFilter(["NE"]))) == {"GCDIR04"}


def test_multiple_directions():
    assert _run(FilterSet().add(DirectionFilter(["E", "S", "W"]))) == {
        "GCDIR05", "GCDIR07", "GCDIR09"}


@pytest.mark.parametrize("d", DIRECTIONS)
def test_each_direction_parity(d):
    _run(FilterSet().add(DirectionFilter([d])))


def test_all_directions_exclude_null_bearing():
    codes = _run(FilterSet().add(DirectionFilter(list(DIRECTIONS))))
    assert "GCDIR12" not in codes
    assert len(codes) == len(_BEARINGS) - 1


def test_empty_selection_matches_nothing():
    assert _run(FilterSet().add(DirectionFilter([]))) == set()


def test_in_or_group_uses_python_path():
    fs = FilterSet().add(FilterSet(mode="OR")
                         .add(DirectionFilter(["S"]))
                         .add(DirectionFilter(["W"])))
    assert _run(fs) == {"GCDIR07", "GCDIR09"}


def test_normalises_and_orders_directions():
    f = DirectionFilter(["sw", " n ", "NE", "bogus"])
    assert f.directions == ["N", "NE", "SW"]


def test_serialisation_roundtrip():
    f = DirectionFilter(["NW", "SE"])
    data = f.to_dict()
    assert data == {"filter_type": "direction", "directions": ["SE", "NW"]}
    restored = FILTER_REGISTRY["direction"].from_dict(data)
    assert restored.to_dict() == data

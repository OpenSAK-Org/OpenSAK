"""tests/unit-tests/test_direction_filter.py — DirectionFilter (GSAK "Direction").

Two forms: the original compass sectors (the persisted Cache.bearing from the
home point falls in one of the selected 45° sectors), and a bearing condition
in degrees, from Home or from an arbitrary centre point. SQL pushdown must
agree with matches(), including sector boundaries, the N wrap-around at
0°/360° and NULL bearings.
"""

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.filters.engine import (
    DIRECTIONS, FILTER_REGISTRY, DirectionFilter, FilterSet, apply_filters,
    bearing_direction, bearing_op_ok,
)
from opensak.filters.engine import _bearing_deg

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


# ── Bearing condition (degrees, same operators as the other number filters) ──

@pytest.mark.parametrize("op,a,b,expected", [
    ("equal",       90, 0,   {"GCDIR05"}),
    ("less_than",   22.5, 0, {"GCDIR01", "GCDIR03"}),
    ("at_most",     22.5, 0, {"GCDIR01", "GCDIR03", "GCDIR04"}),
    ("more_than",   315, 0,  {"GCDIR02", "GCDIR11"}),
    ("at_least",    315, 0,  {"GCDIR02", "GCDIR10", "GCDIR11"}),
    ("between",     90, 180, {"GCDIR05", "GCDIR06", "GCDIR07"}),
    # clockwise through north
    ("between",     315, 22.4, {"GCDIR01", "GCDIR02", "GCDIR03", "GCDIR10", "GCDIR11"}),
    ("not_between", 22.5, 315, {"GCDIR01", "GCDIR02", "GCDIR03", "GCDIR11"}),
])
def test_bearing_condition_home_sql_parity(op, a, b, expected):
    # No centre: uses the persisted Cache.bearing, pushed to SQL.
    assert _run(FilterSet().add(DirectionFilter(op=op, deg1=a, deg2=b))) == expected


def test_bearing_condition_null_bearing_never_matches():
    codes = _run(FilterSet().add(DirectionFilter(op="not_between", deg1=1, deg2=2)))
    assert "GCDIR12" not in codes


@pytest.mark.parametrize("op,bearing,a,b,expected", [
    ("between", 350, 315, 45, True),
    ("between", 180, 315, 45, False),
    ("between", 180, 45, 315, True),
    ("not_between", 180, 315, 45, True),
    ("equal", 90.4, 90, 0, True),
    ("equal", 90.6, 90, 0, False),
])
def test_bearing_op_ok(op, bearing, a, b, expected):
    assert bearing_op_ok(op, bearing, a, b) is expected


def test_bearing_deg():
    assert _bearing_deg(55.0, 12.0, 56.0, 12.0) == pytest.approx(0.0)
    assert _bearing_deg(55.0, 12.0, 54.0, 12.0) == pytest.approx(180.0)
    assert _bearing_deg(0.0, 0.0, 0.0, 1.0) == pytest.approx(90.0)
    assert _bearing_deg(0.0, 0.0, 0.0, -1.0) == pytest.approx(270.0)


def test_bearing_from_center_ignores_persisted_bearing():
    # All seeded caches sit at 55.0/12.0; seen from due south they are
    # all due north, whatever their stored bearing says.
    f = DirectionFilter(op="between", deg1=350, deg2=10, lat=54.0, lon=12.0)
    assert f.apply_to_query(None) is None  # Python only
    codes = _run(FilterSet().add(f))
    assert codes == set(_BEARINGS)  # the NULL-bearing one too: it has coordinates
    f = DirectionFilter(op="between", deg1=90, deg2=270, lat=54.0, lon=12.0)
    assert _run(FilterSet().add(f)) == set()


def test_unknown_bearing_operator_rejected():
    with pytest.raises(ValueError):
        DirectionFilter(op="bogus")


def test_bearing_serialisation_roundtrip():
    f = DirectionFilter(op="between", deg1=315, deg2=45, lat=56.5, lon=10.1,
                        center_state={"kind": "custom", "text": "56.5, 10.1"})
    data = f.to_dict()
    assert data["op"] == "between" and "directions" not in data
    restored = FILTER_REGISTRY["direction"].from_dict(data)
    assert restored.to_dict() == data


def test_legacy_profile_still_loads_as_sectors():
    # Profiles saved before the bearing condition keep matching exactly.
    restored = FILTER_REGISTRY["direction"].from_dict(
        {"filter_type": "direction", "directions": ["N"]})
    assert restored.op is None
    assert _run(FilterSet().add(restored)) == {"GCDIR01", "GCDIR02", "GCDIR03", "GCDIR11"}


def test_sectors_from_center():
    # Seen from due south, all seeded caches lie north — whatever their
    # stored (home) bearing says.
    f = DirectionFilter(["N"], lat=54.0, lon=12.0)
    assert f.apply_to_query(None) is None  # Python only
    assert _run(FilterSet().add(f)) == set(_BEARINGS)
    assert _run(FilterSet().add(DirectionFilter(["E", "W"], lat=54.0, lon=12.0))) == set()


def test_sectors_with_center_serialisation_roundtrip():
    f = DirectionFilter(["E", "W"], lat=56.5, lon=10.1,
                        center_state={"kind": "home"})
    data = f.to_dict()
    assert data["directions"] == ["E", "W"] and data["lat"] == 56.5
    restored = FILTER_REGISTRY["direction"].from_dict(data)
    assert restored.to_dict() == data

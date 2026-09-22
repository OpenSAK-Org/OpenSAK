"""tests/unit-tests/test_waypoint_filter.py — WaypointFilter (child waypoints).

Covers each criterion, the count operators, serialisation, and that the SQL
push-down, the prepare()-count path and the pure-Python cache.waypoints path
all agree — for both apply_filters() and apply_filters_lightweight().
"""

from datetime import date, datetime

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache, Waypoint
from opensak.filters.engine import (
    FILTER_REGISTRY, FilterSet, NameFilter, WaypointFilter,
    apply_filters, apply_filters_lightweight,
)


@pytest.fixture(scope="module", autouse=True)
def seed_waypoint_data(tmp_db):
    def cache(code, name, waypoints):
        c = Cache(gc_code=code, name=name, cache_type="Multi-cache",
                  latitude=47.0, longitude=8.0)
        c.waypoints = waypoints
        c.waypoint_count = len(waypoints)
        return c

    caches = [
        # GSAK import: real waypoint codes, one user-created final
        cache("GCW0001", "Alpha", [
            Waypoint(prefix="PK", wp_code="PK0001", wp_type="Parking Area",
                     name="Parkplatz Bahnhof", comment="Gratis",
                     wp_date=datetime(2024, 5, 1, 10, 30)),
            Waypoint(prefix="FN", wp_code="FN0001", wp_type="Final Location",
                     name="Final", comment=None, created_by_user=True,
                     wp_date=datetime(2025, 1, 15)),
        ]),
        # GPX import: no wp_code, only the prefix
        cache("GCW0002", "Beta", [
            Waypoint(prefix="S1", wp_type="Physical Stage", name="Stage 1",
                     comment="Zähle die Fenster"),
            Waypoint(prefix="S2", wp_type="Physical Stage", name="Stage 2"),
            Waypoint(prefix="PK", wp_type="Parking Area", name="Parking"),
        ]),
        # No waypoints at all
        cache("GCW0003", "Gamma", []),
    ]
    with get_session() as s:
        for c in caches:
            s.add(c)


def _codes(results) -> set[str]:
    return {c.gc_code for c in results}


def _all_paths(f: WaypointFilter) -> set[str]:
    """Run *f* through every evaluation path and require they agree."""
    fs = FilterSet().add(f)
    with get_session() as s:
        orm = _codes(apply_filters(s, fs))
        light = _codes(apply_filters_lightweight(s, fs))
        f._counts = None  # pure Python: walk cache.waypoints
        python = {c.gc_code for c in s.query(Cache).all() if f.matches(c)}
    assert orm == light == python, (orm, light, python)
    return orm


# ── Criteria ──────────────────────────────────────────────────────────────────

def test_code_uses_wp_code_or_prefix():
    assert _all_paths(WaypointFilter(texts={"code": ("PK", "starts_with")})) == {"GCW0001", "GCW0002"}
    assert _all_paths(WaypointFilter(texts={"code": ("fn0001", "equals")})) == {"GCW0001"}
    assert _all_paths(WaypointFilter(texts={"code": ("S2", "equals")})) == {"GCW0002"}


def test_type_name_comment():
    assert _all_paths(WaypointFilter(texts={"wp_type": ("stage", "contains")})) == {"GCW0002"}
    assert _all_paths(WaypointFilter(texts={"name": ("bahnhof", "contains")})) == {"GCW0001"}
    assert _all_paths(WaypointFilter(texts={"comment": ("", "not_empty")})) == {"GCW0001", "GCW0002"}


def test_criteria_apply_to_the_same_waypoint():
    # Alpha has a parking waypoint and a user-created one, but not both in one.
    f = WaypointFilter(texts={"wp_type": ("Parking", "contains")}, by_user=True)
    assert _all_paths(f) == set()
    assert _all_paths(WaypointFilter(by_user=True)) == {"GCW0001"}
    assert _all_paths(WaypointFilter(by_user=False)) == {"GCW0001", "GCW0002"}


def test_date():
    f = WaypointFilter(date_op="between", date1=date(2025, 1, 1), date2=date(2025, 12, 31))
    assert _all_paths(f) == {"GCW0001"}
    f = WaypointFilter(date_op="on_or_before", date1=date(2024, 5, 1))
    assert _all_paths(f) == {"GCW0001"}
    # not_during also keeps waypoints without a date
    f = WaypointFilter(date_op="not_during", date_amount=1, date_unit="days")
    assert _all_paths(f) == {"GCW0001", "GCW0002"}


def test_inexact_sql_paths_regex_and_non_ascii():
    assert _all_paths(WaypointFilter(texts={"name": (r"^stage \d$", "regex")})) == {"GCW0002"}
    assert _all_paths(WaypointFilter(texts={"comment": ("ZÄHLE", "contains")})) == {"GCW0002"}
    assert _all_paths(WaypointFilter(texts={"name": ("stage", "not_regex")})) == {"GCW0001", "GCW0002"}


# ── Count ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("op, c1, c2, expected", [
    ("equal",    0, 0, {"GCW0003"}),
    ("equal",    2, 0, {"GCW0001"}),
    ("at_least", 3, 0, {"GCW0002"}),
    ("at_most",  2, 0, {"GCW0001", "GCW0003"}),
    ("between",  3, 1, {"GCW0001", "GCW0002"}),
])
def test_total_count(op, c1, c2, expected):
    assert _all_paths(WaypointFilter(count_op=op, count1=c1, count2=c2)) == expected


def test_count_of_matching_waypoints():
    # Caches without any stage waypoint
    f = WaypointFilter(texts={"wp_type": ("Stage", "contains")}, count_op="equal", count1=0)
    assert _all_paths(f) == {"GCW0001", "GCW0003"}
    # Regex path counts too
    f = WaypointFilter(texts={"name": (r"stage", "regex")}, count_op="at_least", count1=2)
    assert _all_paths(f) == {"GCW0002"}


def test_noop_matches_everything():
    f = WaypointFilter()
    assert f.is_noop()
    assert _all_paths(f) == {"GCW0001", "GCW0002", "GCW0003"}


def test_combined_with_other_filters():
    fs = FilterSet().add(NameFilter("a", "contains")).add(
        WaypointFilter(texts={"code": ("PK", "starts_with")}))
    or_fs = FilterSet("OR").add(NameFilter("Gamma", "equals")).add(
        WaypointFilter(by_user=True))
    with get_session() as s:
        assert _codes(apply_filters_lightweight(s, fs)) == {"GCW0001", "GCW0002"}
        assert _codes(apply_filters_lightweight(s, or_fs)) == {"GCW0001", "GCW0003"}


# ── Validation / serialisation ────────────────────────────────────────────────

def test_empty_text_is_dropped_and_regex_error_reported():
    assert WaypointFilter(texts={"name": ("", "contains")}).texts == {}
    assert WaypointFilter(texts={"name": ("(", "regex")}).regex_error


def test_invalid_arguments():
    with pytest.raises(ValueError):
        WaypointFilter(texts={"url": ("x", "contains")})
    with pytest.raises(ValueError):
        WaypointFilter(date_op="compare")
    with pytest.raises(ValueError):
        WaypointFilter(date_op="between", date1=date(2025, 1, 1))
    with pytest.raises(ValueError):
        WaypointFilter(count_op="most")


def test_round_trip():
    f = WaypointFilter(
        texts={"code": ("PK", "starts_with"), "comment": ("gratis", "contains")},
        date_op="during", date_amount=3, date_unit="months",
        by_user=False, count_op="between", count1=1, count2=4,
    )
    data = FilterSet().add(f).to_dict()
    restored = FilterSet.from_dict(data)._filters[0]
    assert FILTER_REGISTRY["waypoint"] is WaypointFilter
    assert isinstance(restored, WaypointFilter)
    assert restored.to_dict() == f.to_dict()

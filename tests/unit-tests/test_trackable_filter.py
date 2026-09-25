"""tests/unit-tests/test_trackable_filter.py — TrackableFilter (trackables in a cache).

Covers the name / tracking code text criteria, the count operators,
serialisation, and that the SQL push-down, the prepare()-count path and the
pure-Python cache.trackables path all agree — for both apply_filters() and
apply_filters_lightweight().
"""

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache, Trackable
from opensak.filters.engine import (
    FILTER_REGISTRY, FilterSet, NameFilter, TrackableFilter,
    apply_filters, apply_filters_lightweight,
)


@pytest.fixture(scope="module", autouse=True)
def seed_trackable_data(tmp_db):
    def cache(code, name, trackables):
        c = Cache(gc_code=code, name=name, cache_type="Traditional Cache",
                  latitude=47.0, longitude=8.0)
        c.trackables = trackables
        c.trackable_count = len(trackables)
        return c

    caches = [
        cache("GCT0001", "Alpha", [
            Trackable(ref="TB1A", tracking_code="ABC123", name="Reise-Käfer"),
            Trackable(ref="TB1B", tracking_code="XYZ789", name="Swiss Geocoin"),
        ]),
        cache("GCT0002", "Beta", [
            Trackable(ref="TB2A", tracking_code="ABC999", name="Travel Bug 1"),
            Trackable(ref="TB2B", tracking_code=None, name="Travel Bug 2"),
            Trackable(ref="TB2C", tracking_code="QQQ111", name="Travel Bug 3"),
        ]),
        cache("GCT0003", "Gamma", []),
    ]
    with get_session() as s:
        for c in caches:
            s.add(c)


def _codes(results) -> set[str]:
    return {c.gc_code for c in results if c.gc_code.startswith("GCT")}


def _all_paths(f: TrackableFilter) -> set[str]:
    """Run *f* through every evaluation path and require they agree."""
    fs = FilterSet().add(f)
    with get_session() as s:
        orm = _codes(apply_filters(s, fs))
        light = _codes(apply_filters_lightweight(s, fs))
        f._counts = None  # pure Python: walk cache.trackables
        python = _codes(c for c in s.query(Cache).all() if f.matches(c))
    assert orm == light == python, (orm, light, python)
    return orm


# ── Criteria ──────────────────────────────────────────────────────────────────

def test_name():
    assert _all_paths(TrackableFilter(texts={"name": ("travel bug", "contains")})) == {"GCT0002"}
    assert _all_paths(TrackableFilter(texts={"name": ("swiss geocoin", "equals")})) == {"GCT0001"}
    assert _all_paths(TrackableFilter(texts={"name": ("travel", "not_contains")})) == {"GCT0001"}
    assert _all_paths(TrackableFilter(texts={"name": ("Reise-Käfer;Travel Bug 3", "in_list")})) == {"GCT0001", "GCT0002"}


def test_tracking_code():
    assert _all_paths(TrackableFilter(texts={"tracking_code": ("abc", "starts_with")})) == {"GCT0001", "GCT0002"}
    assert _all_paths(TrackableFilter(texts={"tracking_code": ("789", "ends_with")})) == {"GCT0001"}
    assert _all_paths(TrackableFilter(texts={"tracking_code": ("", "empty")})) == {"GCT0002"}


def test_criteria_apply_to_the_same_trackable():
    f = TrackableFilter(texts={"name": ("geocoin", "contains"),
                               "tracking_code": ("ABC", "starts_with")})
    assert _all_paths(f) == set()


def test_inexact_sql_paths_regex_and_non_ascii():
    assert _all_paths(TrackableFilter(texts={"name": (r"bug \d$", "regex")})) == {"GCT0002"}
    assert _all_paths(TrackableFilter(texts={"name": ("KÄFER", "contains")})) == {"GCT0001"}


# ── Count ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("op, c1, c2, expected", [
    ("equal",    0, 0, {"GCT0003"}),
    ("equal",    2, 0, {"GCT0001"}),
    ("at_least", 3, 0, {"GCT0002"}),
    ("at_most",  2, 0, {"GCT0001", "GCT0003"}),
    ("between",  3, 1, {"GCT0001", "GCT0002"}),
])
def test_total_count(op, c1, c2, expected):
    assert _all_paths(TrackableFilter(count_op=op, count1=c1, count2=c2)) == expected


def test_count_of_matching_trackables():
    # Caches without a travel bug
    f = TrackableFilter(texts={"name": ("travel bug", "contains")}, count_op="equal", count1=0)
    assert _all_paths(f) == {"GCT0001", "GCT0003"}
    # Regex path counts too
    f = TrackableFilter(texts={"name": (r"bug", "regex")}, count_op="at_least", count1=2)
    assert _all_paths(f) == {"GCT0002"}


def test_noop_matches_everything():
    f = TrackableFilter()
    assert f.is_noop()
    assert _all_paths(f) == {"GCT0001", "GCT0002", "GCT0003"}


def test_combined_with_other_filters():
    or_fs = FilterSet("OR").add(NameFilter("Gamma", "equals")).add(
        TrackableFilter(texts={"name": ("geocoin", "contains")}))
    with get_session() as s:
        assert _codes(apply_filters_lightweight(s, or_fs)) == {"GCT0001", "GCT0003"}


# ── Validation / serialisation ────────────────────────────────────────────────

def test_empty_text_is_dropped_and_regex_error_reported():
    assert TrackableFilter(texts={"name": ("", "contains")}).texts == {}
    assert TrackableFilter(texts={"name": ("(", "regex")}).regex_error


def test_invalid_arguments():
    with pytest.raises(ValueError):
        TrackableFilter(texts={"ref": ("x", "contains")})
    with pytest.raises(ValueError):
        TrackableFilter(count_op="most")


def test_round_trip():
    f = TrackableFilter(
        texts={"name": ("coin", "contains"), "tracking_code": ("AB", "starts_with")},
        count_op="between", count1=1, count2=4,
    )
    data = FilterSet().add(f).to_dict()
    restored = FilterSet.from_dict(data)._filters[0]
    assert FILTER_REGISTRY["trackable"] is TrackableFilter
    assert isinstance(restored, TrackableFilter)
    assert restored.to_dict() == f.to_dict()

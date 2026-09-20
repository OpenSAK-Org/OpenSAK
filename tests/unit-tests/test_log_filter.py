"""tests/unit-tests/test_log_filter.py — LogFilter (GSAK's Logs tab).

Covers the scope (log categories and the last-N window), each per-log
criterion, the count operators, include/exclude and serialisation, and that
the SQL push-down, the prepare()-count path and the pure-Python cache.logs
path all agree — for both apply_filters() and apply_filters_lightweight().
"""

from datetime import date, datetime

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache, Log
from opensak.filters.engine import (
    FILTER_REGISTRY, FilterSet, LOG_TYPE_OTHER, LogFilter, NameFilter,
    apply_filters, apply_filters_lightweight, log_category,
)


@pytest.fixture(scope="module", autouse=True)
def seed_log_data(tmp_db):
    def cache(code, name, logs):
        c = Cache(gc_code=code, name=name, cache_type="Traditional Cache",
                  latitude=47.0, longitude=8.0)
        c.logs = logs
        c.log_count = len(logs)
        return c

    def log(log_type, day, finder="alice", finder_id=None):
        return Log(log_type=log_type, finder=finder, finder_id=finder_id,
                   log_date=datetime(2025, 1, day) if day else None)

    caches = [
        # Newest first: a maintenance log sits on top of two finds.
        cache("GCL0001", "Alpha", [
            log("Owner Maintenance", 20, finder="owner", finder_id="U-1"),
            log("Found it", 10, finder="Bob", finder_id="U-2"),
            log("Found it", 5, finder="alice", finder_id="U-3"),
        ]),
        # A DNF is the most recent log, older finds below it.
        cache("GCL0002", "Beta", [
            log("Didn't find it", 25, finder="Bob", finder_id="U-2"),
            log("Found it", 12, finder="Zoë", finder_id="U-4"),
            log("Write note", 8, finder="alice", finder_id="U-3"),
            log("Found it", 2, finder="alice", finder_id="U-3"),
        ]),
        # Only a reviewer log, plus one with no date at all.
        cache("GCL0003", "Gamma", [
            log("Publish Listing", 3, finder="Reviewer", finder_id="U-9"),
            log("Geoart Souvenir", None, finder="hq", finder_id="U-9"),
        ]),
        # No logs at all.
        cache("GCL0004", "Delta", []),
    ]
    with get_session() as s:
        for c in caches:
            s.add(c)


def _codes(results) -> set[str]:
    return {c.gc_code for c in results}


def _all_paths(f: LogFilter) -> set[str]:
    """Run *f* through every evaluation path and require they agree."""
    fs = FilterSet().add(f)
    with get_session() as s:
        orm = _codes(apply_filters(s, fs))
        light = _codes(apply_filters_lightweight(s, fs))
        f._counts = None  # pure Python: walk cache.logs
        python = {c.gc_code for c in s.query(Cache).all() if f.matches(c)}
    assert orm == light == python, (orm, light, python)
    return orm


# ── Categories ────────────────────────────────────────────────────────────────

def test_log_category_classifies_by_type():
    assert log_category("Found it") == "found"
    assert log_category("attended") == "found"
    assert log_category("Webcam Photo Taken") == "found"
    assert log_category("Didn't find it") == "not_found"
    assert log_category("Write note") == "other"
    assert log_category(None) == "other"


def test_category_scope():
    assert _all_paths(LogFilter(categories=["found"])) == {"GCL0001", "GCL0002"}
    assert _all_paths(LogFilter(categories=["not_found"])) == {"GCL0002"}
    assert _all_paths(LogFilter(categories=["other"])) == {"GCL0001", "GCL0002", "GCL0003"}
    # Nothing to search at all — no cache can have a qualifying log.
    assert _all_paths(LogFilter(categories=[])) == set()


# ── Last-N window ─────────────────────────────────────────────────────────────

def test_last_n_limits_which_logs_are_searched():
    # Alpha's newest log is the maintenance one and Beta's is the DNF, so
    # neither has a find in a one-log window; both do in a two-log one.
    assert _all_paths(LogFilter(last_n=1, types=["Found it"])) == set()
    assert _all_paths(LogFilter(last_n=2, types=["Found it"])) == {"GCL0001", "GCL0002"}
    # Beta's second find is its oldest log — only an unlimited window sees it.
    assert _all_paths(LogFilter(types=["Found it"], count_op="at_least", count1=2)) == \
        {"GCL0001", "GCL0002"}
    assert _all_paths(LogFilter(last_n=3, types=["Found it"],
                                count_op="at_least", count1=2)) == {"GCL0001"}


def test_last_n_applies_after_the_categories():
    # Only found logs are searched, so "the last one" is the newest find —
    # Beta's is Zoë's, not the DNF above it.
    f = LogFilter(categories=["found"], last_n=1, finder_text="Zoë", finder_op="equals")
    assert _all_paths(f) == {"GCL0002"}


def test_last_n_orders_undated_logs_last():
    # Gamma's undated log must not displace its dated one from a 1-log window.
    assert _all_paths(LogFilter(last_n=1, types=["Publish Listing"])) == {"GCL0003"}


# ── Criteria ──────────────────────────────────────────────────────────────────

def test_types_and_the_other_catch_all():
    assert _all_paths(LogFilter(types=["Write note"])) == {"GCL0002"}
    assert _all_paths(LogFilter(types=["Owner Maintenance", "Publish Listing"])) == \
        {"GCL0001", "GCL0003"}
    # "Geoart Souvenir" is in none of LOG_TYPES.
    assert _all_paths(LogFilter(types=[LOG_TYPE_OTHER])) == {"GCL0003"}
    assert _all_paths(LogFilter(types=[LOG_TYPE_OTHER, "Write note"])) == \
        {"GCL0002", "GCL0003"}


def test_date():
    f = LogFilter(date_op="on_or_after", date1=date(2025, 1, 20))
    assert _all_paths(f) == {"GCL0001", "GCL0002"}
    f = LogFilter(date_op="between", date1=date(2025, 1, 1), date2=date(2025, 1, 5))
    assert _all_paths(f) == {"GCL0001", "GCL0002", "GCL0003"}
    # not_during also keeps logs without a date
    f = LogFilter(date_op="not_during", date_amount=1, date_unit="days")
    assert _all_paths(f) == {"GCL0001", "GCL0002", "GCL0003"}


def test_finder_by_name_and_by_id():
    assert _all_paths(LogFilter(finder_text="bob", finder_op="equals")) == \
        {"GCL0001", "GCL0002"}
    assert _all_paths(LogFilter(finder_text="U-9", finder_op="equals",
                                finder_by_id=True)) == {"GCL0003"}
    assert _all_paths(LogFilter(finder_text="", finder_op="empty")) == set()


def test_criteria_apply_to_the_same_log():
    # Bob logged a find on Alpha and a DNF on Beta — neither is a Bob find
    # on Beta.
    f = LogFilter(types=["Found it"], finder_text="Bob", finder_op="equals")
    assert _all_paths(f) == {"GCL0001"}


def test_inexact_sql_paths_regex_and_non_ascii():
    assert _all_paths(LogFilter(finder_text=r"^U-\d$", finder_op="regex",
                                finder_by_id=True)) == \
        {"GCL0001", "GCL0002", "GCL0003"}
    assert _all_paths(LogFilter(finder_text="ZOË", finder_op="equals")) == {"GCL0002"}
    assert _all_paths(LogFilter(finder_text="alice", finder_op="not_equals")) == \
        {"GCL0001", "GCL0002", "GCL0003"}


# ── Count ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("op, c1, c2, expected", [
    ("equal",    0, 0, {"GCL0004"}),
    ("equal",    3, 0, {"GCL0001"}),
    ("at_least", 4, 0, {"GCL0002"}),
    ("at_most",  2, 0, {"GCL0003", "GCL0004"}),
    ("between",  4, 2, {"GCL0001", "GCL0002", "GCL0003"}),
])
def test_total_count(op, c1, c2, expected):
    assert _all_paths(LogFilter(count_op=op, count1=c1, count2=c2)) == expected


def test_count_of_qualifying_logs():
    # Caches with at least two finds
    f = LogFilter(types=["Found it"], count_op="at_least", count1=2)
    assert _all_paths(f) == {"GCL0001", "GCL0002"}
    # Caches without a single find — Delta has no logs at all
    f = LogFilter(types=["Found it"], count_op="equal", count1=0)
    assert _all_paths(f) == {"GCL0003", "GCL0004"}
    # Counting works on the inexact (regex) path too
    f = LogFilter(finder_text=r"^alice$", finder_op="regex",
                  count_op="at_least", count1=2)
    assert _all_paths(f) == {"GCL0002"}


# ── Include / exclude ─────────────────────────────────────────────────────────

def test_exclude_inverts_the_verdict():
    assert _all_paths(LogFilter(types=["Found it"])) == {"GCL0001", "GCL0002"}
    assert _all_paths(LogFilter(types=["Found it"], exclude=True)) == \
        {"GCL0003", "GCL0004"}


def test_exclude_on_the_last_n_window():
    # "No maintenance log among the last two" — Alpha's newest log is one.
    f = LogFilter(last_n=2, types=["Owner Maintenance", "Needs Maintenance"],
                  exclude=True)
    assert _all_paths(f) == {"GCL0002", "GCL0003", "GCL0004"}


def test_exclude_with_a_count_operator():
    f = LogFilter(types=["Found it"], count_op="at_least", count1=2, exclude=True)
    assert _all_paths(f) == {"GCL0003", "GCL0004"}


# ── No-op and combination ─────────────────────────────────────────────────────

def test_noop_matches_everything():
    f = LogFilter()
    assert f.is_noop()
    assert _all_paths(f) == {"GCL0001", "GCL0002", "GCL0003", "GCL0004"}


def test_combines_with_another_filter():
    fs = FilterSet().add(NameFilter("a")).add(LogFilter(types=["Found it"]))
    with get_session() as s:
        assert _codes(apply_filters(s, fs)) == {"GCL0001", "GCL0002"}


# ── Validation and serialisation ──────────────────────────────────────────────

@pytest.mark.parametrize("kwargs", [
    {"date_op": "compare"},
    {"date_op": "nonsense"},
    {"date_unit": "fortnights"},
    {"count_op": "roughly"},
    {"categories": ["found", "maybe"]},
    {"date_op": "equal"},            # needs date1
    {"date_op": "between", "date1": date(2025, 1, 1)},  # needs date2
])
def test_invalid_arguments_are_rejected(kwargs):
    with pytest.raises(ValueError):
        LogFilter(**kwargs)


def test_roundtrip():
    f = LogFilter(
        date_op="during", date_amount=6, date_unit="months",
        categories=["found", "other"], last_n=10,
        types=["Found it", LOG_TYPE_OTHER],
        finder_text="alice", finder_op="starts_with", finder_by_id=True,
        count_op="between", count1=1, count2=4, exclude=True,
    )
    data = f.to_dict()
    assert FILTER_REGISTRY[data["filter_type"]] is LogFilter
    assert LogFilter.from_dict(data).to_dict() == data


def test_filterset_roundtrip_keeps_the_filter():
    fs = FilterSet().add(LogFilter(types=["Found it"], exclude=True))
    restored = FilterSet.from_dict(fs.to_dict())
    assert [f.to_dict() for f in restored._filters] == [f.to_dict() for f in fs._filters]

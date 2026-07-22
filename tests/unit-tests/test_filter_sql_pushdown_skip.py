# tests/unit-tests/test_filter_sql_pushdown_skip.py — issue #631.
#
# apply_filters() skips its Python-level `[c for c in all_caches if
# filterset.matches(c)]` re-scan when the entire filterset was already
# translated into the SQL WHERE clause, since every row query.all() returns
# already satisfies it in that case. These tests verify the skip actually
# happens when it's safe, and — just as importantly — that it does NOT
# happen for filter types/compositions where it would silently return wrong
# results (DistanceFilter's bounding-box pre-narrowing, WhereClauseFilter,
# OR-subtrees).

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.filters.engine import (
    ArchivedFilter, CacheTypeFilter, DistanceFilter, FilterSet,
    WhereClauseFilter, apply_filters,
)


@pytest.fixture(scope="module", autouse=True)
def seed_data(tmp_db):
    with get_session() as s:
        s.add_all([
            Cache(gc_code="GCS0001", name="Near Traditional", cache_type="Traditional Cache",
                  latitude=55.6761, longitude=12.5683, archived=False),
            Cache(gc_code="GCS0002", name="Near Mystery", cache_type="Unknown Cache",
                  latitude=55.68, longitude=12.57, archived=False),
            Cache(gc_code="GCS0003", name="Far Traditional", cache_type="Traditional Cache",
                  latitude=60.0, longitude=20.0, archived=False),
            Cache(gc_code="GCS0004", name="Archived Traditional", cache_type="Traditional Cache",
                  latitude=55.677, longitude=12.569, archived=True),
        ])


def _count_matches_calls(monkeypatch) -> list:
    """Patch FilterSet.matches to record every call, returning the call log."""
    calls = []
    original = FilterSet.matches

    def _tracked(self, cache):
        calls.append(cache.gc_code)
        return original(self, cache)

    monkeypatch.setattr(FilterSet, "matches", _tracked)
    return calls


class TestSkipsWhenFullySqlPushed:
    def test_single_exact_filter_skips_python_pass(self, monkeypatch):
        calls = _count_matches_calls(monkeypatch)
        with get_session() as s:
            fs = FilterSet(mode="AND").add(ArchivedFilter())
            apply_filters(s, fs)
        assert calls == []

    def test_multiple_exact_and_filters_skip_python_pass(self, monkeypatch):
        calls = _count_matches_calls(monkeypatch)
        with get_session() as s:
            fs = FilterSet(mode="AND")
            fs.add(CacheTypeFilter(["Traditional Cache"]))
            fs.add(ArchivedFilter())
            apply_filters(s, fs)
        assert calls == []

    def test_no_filterset_never_calls_matches(self, monkeypatch):
        calls = _count_matches_calls(monkeypatch)
        with get_session() as s:
            apply_filters(s, None)
        assert calls == []

    def test_empty_filterset_skips_python_pass(self, monkeypatch):
        calls = _count_matches_calls(monkeypatch)
        with get_session() as s:
            apply_filters(s, FilterSet(mode="AND"))
        assert calls == []


class TestDoesNotSkipWhenNotFullySqlPushed:
    def test_distance_filter_still_runs_python_pass(self, monkeypatch):
        # DistanceFilter.apply_to_query() only pushes a bounding-box
        # pre-narrowing (see its sql_exact = False) — the exact haversine +
        # min_km check still has to run in Python, or results would be wrong
        # (this is exactly what caught the original #631 bug: a naive
        # "non-None apply_to_query() == fully handled" heuristic silently
        # dropped the min_km check — see test_distance_filter_min_max in
        # test_filters.py for the correctness regression test).
        calls = _count_matches_calls(monkeypatch)
        with get_session() as s:
            fs = FilterSet(mode="AND").add(
                DistanceFilter(lat=55.6761, lon=12.5683, max_km=50.0)
            )
            apply_filters(s, fs)
        assert calls != []

    def test_where_clause_filter_still_runs_python_pass(self, monkeypatch):
        # WhereClauseFilter has no apply_to_query() SQL form at all — its
        # matching_ids are pre-populated separately and only actually
        # applied via matches().
        calls = _count_matches_calls(monkeypatch)
        with get_session() as s:
            fs = FilterSet(mode="AND").add(WhereClauseFilter("archived = 0"))
            apply_filters(s, fs)
        assert calls != []

    def test_or_subtree_still_runs_python_pass(self, monkeypatch):
        # An OR filterset can't be pushed into the WHERE clause as a plain
        # AND term, so it (and anything inside it) always falls back to
        # the Python pass.
        calls = _count_matches_calls(monkeypatch)
        with get_session() as s:
            or_fs = FilterSet(mode="OR")
            or_fs.add(CacheTypeFilter(["Traditional Cache"]))
            or_fs.add(ArchivedFilter())
            fs = FilterSet(mode="AND").add(or_fs)
            apply_filters(s, fs)
        assert calls != []

    def test_mixed_exact_and_non_exact_still_runs_python_pass(self, monkeypatch):
        # A single non-exact filter anywhere in an otherwise-fully-pushed
        # AND set must still force the Python pass for the whole result.
        calls = _count_matches_calls(monkeypatch)
        with get_session() as s:
            fs = FilterSet(mode="AND")
            fs.add(ArchivedFilter())
            fs.add(DistanceFilter(lat=55.6761, lon=12.5683, max_km=50.0))
            apply_filters(s, fs)
        assert calls != []


class TestResultsIdenticalEitherWay:
    """The skip is a pure performance shortcut — results must never differ
    between the fully-pushed (skipped) and partially-pushed (Python-pass)
    paths for the same logical query."""

    def test_archived_filter_same_result_pushed_vs_forced_python(self, monkeypatch):
        with get_session() as s:
            fs = FilterSet(mode="AND").add(ArchivedFilter())
            pushed_results = {c.gc_code for c in apply_filters(s, fs)}

        # Force the Python pass by wrapping the exact same filter in an OR
        # set of one — same logical result, but sql_exact tracking must
        # bail out to the Python path instead of the SQL-only path.
        with get_session() as s:
            or_fs = FilterSet(mode="OR").add(ArchivedFilter())
            forced_python_results = {c.gc_code for c in apply_filters(s, or_fs)}

        assert pushed_results == forced_python_results

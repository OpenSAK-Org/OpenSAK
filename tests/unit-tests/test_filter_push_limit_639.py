"""tests/unit-tests/test_filter_push_limit_639.py — issue #639.

push_limit=True on apply_filters_lightweight()/apply_filters_auto() pushes
`limit` into the SQL query (LIMIT after ORDER BY) instead of fetching every
filtered row and slicing in Python — but only when it's actually safe to do
so: the whole filterset must be SQL-pushed (no relationship filters, no
OR-subtree left to Python) AND the sort field must be SQL-sortable. These
tests specifically verify the two conditions where it must NOT activate
(non-SQL-sorted field; not-fully-SQL-pushed filterset), since a SQL LIMIT
applied before a Python-only sort or Python-only filter pass would silently
return the wrong N rows — exactly the class of bug #631's DistanceFilter
case caught for a different feature.
"""

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.filters.engine import (
    ArchivedFilter, FilterSet, SortSpec, WhereClauseFilter,
    apply_filters_auto, apply_filters_lightweight,
)


@pytest.fixture(scope="module", autouse=True)
def seed_push_limit_data(tmp_db):
    # 10 caches at increasing distance from home (55.6761, 12.5683), plus 2
    # with NULL distance (never recalculated) to confirm they sort last, not
    # first or crash. A few archived, to test filtered + limited together.
    caches = []
    for i in range(10):
        caches.append(Cache(
            gc_code=f"GCPL{i:03d}", name=f"Cache {i}", cache_type="Traditional Cache",
            latitude=55.6761 + i * 0.05, longitude=12.5683 + i * 0.05,
            distance=float(i),  # 0km, 1km, 2km, ... 9km — already "calculated"
            archived=(i % 4 == 0),  # GCPL000, GCPL004, GCPL008 archived
        ))
    for i in range(2):
        caches.append(Cache(
            gc_code=f"GCPLNULL{i}", name=f"No distance {i}", cache_type="Traditional Cache",
            latitude=56.0 + i, longitude=13.0 + i,
            distance=None,
        ))
    with get_session() as s:
        for c in caches:
            s.add(c)


class TestPushLimitActivatesWhenSafe:
    def test_no_filter_distance_sort_returns_nearest_n(self):
        with get_session() as s:
            result = apply_filters_lightweight(
                s, None, SortSpec("distance", ascending=True), limit=3, push_limit=True,
            )
        assert [c.gc_code for c in result] == ["GCPL000", "GCPL001", "GCPL002"]

    def test_null_distance_caches_sort_last_not_first(self):
        with get_session() as s:
            result = apply_filters_lightweight(
                s, None, SortSpec("distance", ascending=True), limit=12, push_limit=True,
            )
        codes = [c.gc_code for c in result]
        # All 10 real-distance caches must come before either NULL-distance one.
        null_positions = [i for i, c in enumerate(codes) if c.startswith("GCPLNULL")]
        real_positions = [i for i, c in enumerate(codes) if not c.startswith("GCPLNULL")]
        assert max(real_positions) < min(null_positions)

    def test_fully_sql_pushed_filter_plus_limit(self):
        # ArchivedFilter is sql_exact and pushes cleanly — combined with a
        # SQL-sorted distance limit, both conditions for push_limit are met.
        with get_session() as s:
            fs = FilterSet().add(ArchivedFilter())
            result = apply_filters_lightweight(
                s, fs, SortSpec("distance", ascending=True), limit=2, push_limit=True,
            )
        # Archived: GCPL000 (0km), GCPL004 (4km), GCPL008 (8km) — nearest 2.
        assert [c.gc_code for c in result] == ["GCPL000", "GCPL004"]

    def test_fewer_rows_than_limit_returns_all_without_error(self):
        with get_session() as s:
            fs = FilterSet().add(ArchivedFilter())
            result = apply_filters_lightweight(
                s, fs, SortSpec("distance", ascending=True), limit=100, push_limit=True,
            )
        assert len(result) == 3  # only 3 archived caches exist total

    def test_apply_filters_auto_passes_push_limit_through(self):
        with get_session() as s:
            result = apply_filters_auto(
                s, None, SortSpec("distance", ascending=True), limit=3, push_limit=True,
            )
        assert [c.gc_code for c in result] == ["GCPL000", "GCPL001", "GCPL002"]


class TestPushLimitDoesNotActivateWhenUnsafe:
    # These are the correctness-critical cases: push_limit=True must be
    # silently ignored (falling back to the existing, always-correct
    # Python-slice behavior) whenever SQL can't safely express the limit.

    def test_python_only_sort_field_ignores_push_limit(self):
        # "name" is never SQL-sorted (Unicode correctness — see
        # _sql_order_expr's docstring), so a SQL LIMIT before that Python
        # sort would return an arbitrary N rows, not the first N by name.
        with get_session() as s:
            result_pushed = apply_filters_lightweight(
                s, None, SortSpec("name", ascending=True), limit=3, push_limit=True,
            )
            result_unpushed = apply_filters_lightweight(
                s, None, SortSpec("name", ascending=True), limit=3, push_limit=False,
            )
        assert [c.gc_code for c in result_pushed] == [c.gc_code for c in result_unpushed]

    def test_where_clause_filter_ignores_push_limit(self):
        # WhereClauseFilter has no apply_to_query() — always needs the
        # Python matches() pass, so fully_sql_pushed is False and a SQL
        # LIMIT would truncate before that pass runs, potentially losing
        # valid matches further down the unfiltered order.
        with get_session() as s:
            fs = FilterSet().add(WhereClauseFilter("archived = 1"))
            result_pushed = apply_filters_lightweight(
                s, fs, SortSpec("distance", ascending=True), limit=2, push_limit=True,
            )
            result_unpushed = apply_filters_lightweight(
                s, fs, SortSpec("distance", ascending=True), limit=2, push_limit=False,
            )
        assert [c.gc_code for c in result_pushed] == [c.gc_code for c in result_unpushed]
        assert [c.gc_code for c in result_pushed] == ["GCPL000", "GCPL004"]

    def test_or_subtree_ignores_push_limit(self):
        with get_session() as s:
            fs = FilterSet(mode="OR")
            fs.add(ArchivedFilter())
            wrapped = FilterSet(mode="AND").add(fs)
            result_pushed = apply_filters_lightweight(
                s, wrapped, SortSpec("distance", ascending=True), limit=2, push_limit=True,
            )
            result_unpushed = apply_filters_lightweight(
                s, wrapped, SortSpec("distance", ascending=True), limit=2, push_limit=False,
            )
        assert [c.gc_code for c in result_pushed] == [c.gc_code for c in result_unpushed]


class TestPushLimitDefaultUnchanged:
    def test_default_push_limit_false_behaves_as_before(self):
        with get_session() as s:
            explicit_false = apply_filters_lightweight(
                s, None, SortSpec("distance", ascending=True), limit=3, push_limit=False,
            )
            default = apply_filters_lightweight(
                s, None, SortSpec("distance", ascending=True), limit=3,
            )
        assert [c.gc_code for c in explicit_false] == [c.gc_code for c in default]

# tests/unit-tests/test_filter_sql_index_usage.py — issue #628.
#
# Cache.<col>.is_(True) / .is_(False) compiles to "<col> IS true" / "IS
# false", which SQLite's query planner cannot satisfy with an index on
# <col> — it silently falls back to a full table scan, even when an index
# exists specifically for that filter (e.g. ix_caches_archived_available,
# ix_caches_found from #214). "<col> = true" / "= false" is functionally
# identical but IS index-usable. .is_(None) (NULL checks) is unaffected.
#
# These tests run EXPLAIN QUERY PLAN against a real SQLite database (with
# migrations applied, so the #214 indexes exist) to verify each affected
# filter's apply_to_query() actually produces an index SEARCH, not a SCAN.

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.filters.engine import (
    ArchivedFilter, AvailabilityFilter, AvailableFilter, FoundFilter,
    NotFoundFilter,
)
from sqlalchemy import text


def _plan_uses_index(session, query, index_name: str | None = None) -> bool:
    """Return True if EXPLAIN QUERY PLAN shows an index SEARCH (not a SCAN)
    for *query*, optionally requiring a specific index name."""
    compiled = query.statement.compile(compile_kwargs={"literal_binds": True})
    rows = session.execute(text(f"EXPLAIN QUERY PLAN {compiled}")).fetchall()
    plan = " | ".join(str(r[3]) for r in rows)
    if "SCAN caches" in plan and "USING INDEX" not in plan:
        return False
    if index_name is not None and index_name not in plan:
        return False
    return True


class TestBooleanFiltersUseTheirIndex:
    # tmp_db runs init_db(), which runs migrations — the #214 indexes
    # (ix_caches_found, ix_caches_archived_available, ...) exist on every
    # test database, not just production ones.

    def test_found_filter_uses_index(self, tmp_db):
        with get_session() as s:
            q = FoundFilter().apply_to_query(s.query(Cache))
            assert _plan_uses_index(s, q, "ix_caches_found")

    def test_not_found_filter_syntax_fixed_but_or_null_shape_still_scans(self, tmp_db):
        # NotFoundFilter's predicate is "found = false OR found IS NULL".
        # The == False half of that fix is still correct and consistent
        # with the other filters here, but SQLite's planner doesn't apply
        # its OR-optimization to a "col = X OR col IS NULL" shape on this
        # column regardless — verified directly: even "found IS NULL" in
        # isolation scans on this schema/data. So this filter keeps
        # scanning either way; that's a planner limitation with the OR
        # shape itself, not something the True/False fix could address.
        # Documented here rather than silently dropped so a future reader
        # doesn't assume this filter was missed.
        with get_session() as s:
            q = NotFoundFilter().apply_to_query(s.query(Cache))
            compiled = q.statement.compile(compile_kwargs={"literal_binds": True})
            assert "found = false" in str(compiled) or "found = 0" in str(compiled)
            assert "IS true" not in str(compiled) and "IS false" not in str(compiled)

    def test_archived_filter_uses_index(self, tmp_db):
        with get_session() as s:
            q = ArchivedFilter().apply_to_query(s.query(Cache))
            assert _plan_uses_index(s, q, "ix_caches_archived_available")

    def test_available_filter_uses_index(self, tmp_db):
        with get_session() as s:
            q = AvailableFilter().apply_to_query(s.query(Cache))
            assert _plan_uses_index(s, q, "ix_caches_archived_available")

    def test_availability_filter_uses_index_all_combinations(self, tmp_db):
        with get_session() as s:
            for show_avail, show_unavail, show_archived in [
                (True, False, False), (False, True, False),
                (False, False, True), (True, True, True),
            ]:
                fs = AvailabilityFilter(show_avail, show_unavail, show_archived)
                q = fs.apply_to_query(s.query(Cache))
                assert _plan_uses_index(s, q, "ix_caches_archived_available"), (
                    f"show_avail={show_avail} show_unavail={show_unavail} "
                    f"show_archived={show_archived}"
                )

    def test_no_regression_on_is_none_null_checks(self, tmp_db):
        # Control case: .is_(None) (NULL checks, e.g. DifficultyFilter's
        # unknown-difficulty branch) was never affected by this bug and
        # must keep using its index.
        from opensak.filters.engine import DifficultyFilter
        with get_session() as s:
            q = DifficultyFilter(2.0, 4.0).apply_to_query(s.query(Cache))
            assert _plan_uses_index(s, q, "ix_caches_difficulty")

"""tests/unit-tests/test_filter_sql_parity_633.py — issue #633.

apply_filters(session, fs) must equal [c for c in all_rows if fs.matches(c)]
for the 10 filters that previously had no SQL pushdown at all (UserFlagFilter,
LockedFilter, DnfFilter, FtfFilter, FavoritePointsFilter, HasCorrectedFilter/
NoCorrectedFilter, FoundByMeDateFilter, DnfDateFilter, LastLogDateFilter) —
including NULL edge cases for every boolean/date field involved, since that's
exactly the class of bug #631's DistanceFilter case caught. Deliberately sets
these to None explicitly (not just relying on defaults) to simulate legacy
data, since none of these columns are nullable=False at the DB level.
"""

from datetime import datetime

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache, UserNote
from opensak.filters.engine import (
    DnfDateFilter, DnfFilter, FavoritePointsFilter, FilterSet, FoundByMeDateFilter,
    FtfFilter, HasCorrectedFilter, LastLogDateFilter, LockedFilter,
    NoCorrectedFilter, UserFlagFilter, apply_filters,
)


@pytest.fixture(scope="module", autouse=True)
def seed_633_data(tmp_db):
    caches = [
        # Explicit True/False/None for every boolean flag, to exercise
        # NULL-as-falsy handling for UserFlagFilter/LockedFilter/DnfFilter/
        # FtfFilter.
        Cache(gc_code="GC6330001", name="AllTrue", cache_type="Traditional Cache",
              latitude=55.0, longitude=12.0,
              user_flag=True, locked=True, dnf=True, first_to_find=True,
              favorite_points=10),
        Cache(gc_code="GC6330002", name="AllFalse", cache_type="Traditional Cache",
              latitude=55.1, longitude=12.1,
              user_flag=False, locked=False, dnf=False, first_to_find=False,
              favorite_points=0),
        Cache(gc_code="GC6330003", name="AllNone", cache_type="Traditional Cache",
              latitude=55.2, longitude=12.2,
              user_flag=None, locked=None, dnf=None, first_to_find=None,
              favorite_points=None),
        # found=True with a found_date set, vs found=True with no date, vs
        # not found at all — for FoundByMeDateFilter.
        Cache(gc_code="GC6330004", name="FoundWithDate", cache_type="Traditional Cache",
              latitude=55.3, longitude=12.3,
              found=True, found_date=datetime(2026, 3, 15)),
        Cache(gc_code="GC6330005", name="FoundNoDate", cache_type="Traditional Cache",
              latitude=55.4, longitude=12.4,
              found=True, found_date=None),
        Cache(gc_code="GC6330006", name="NotFound", cache_type="Traditional Cache",
              latitude=55.5, longitude=12.5,
              found=False, found_date=None),
        # Same three-way split for dnf/dnf_date.
        Cache(gc_code="GC6330007", name="DnfWithDate", cache_type="Traditional Cache",
              latitude=55.6, longitude=12.6,
              dnf=True, dnf_date=datetime(2026, 4, 1)),
        Cache(gc_code="GC6330008", name="DnfNoDate", cache_type="Traditional Cache",
              latitude=55.7, longitude=12.7,
              dnf=True, dnf_date=None),
        # last_log_date present vs NULL (opposite NULL semantics from the
        # found/dnf date filters — NULL excludes here).
        Cache(gc_code="GC6330009", name="HasLastLog", cache_type="Traditional Cache",
              latitude=55.8, longitude=12.8,
              last_log_date=datetime(2026, 5, 1)),
        Cache(gc_code="GC6330010", name="NoLastLog", cache_type="Traditional Cache",
              latitude=55.9, longitude=12.9,
              last_log_date=None),
    ]
    with get_session() as s:
        for c in caches:
            s.add(c)
        s.flush()

        corrected = next(c for c in caches if c.gc_code == "GC6330001")
        not_corrected = next(c for c in caches if c.gc_code == "GC6330002")
        s.add(UserNote(cache_id=corrected.id, is_corrected=True))
        s.add(UserNote(cache_id=not_corrected.id, is_corrected=False))
        # GC6330003 deliberately has NO UserNote row at all — the other
        # "falsy" case HasCorrectedFilter/NoCorrectedFilter must handle.


def assert_parity(fs):
    with get_session() as s:
        pushed_codes = {c.gc_code for c in apply_filters(s, fs)}
        all_caches = apply_filters(s, None)
        python_codes = {c.gc_code for c in all_caches if fs.matches(c)}
    assert pushed_codes == python_codes, (
        f"only in SQL-pushed: {pushed_codes - python_codes}\n"
        f"only in Python matches(): {python_codes - pushed_codes}"
    )
    return pushed_codes


class TestBooleanFlagFilters:
    @pytest.mark.parametrize("flagged", [True, False])
    def test_user_flag_filter(self, flagged):
        assert_parity(FilterSet().add(UserFlagFilter(flagged)))

    @pytest.mark.parametrize("locked", [True, False])
    def test_locked_filter(self, locked):
        assert_parity(FilterSet().add(LockedFilter(locked)))

    @pytest.mark.parametrize("has_dnf", [True, False])
    def test_dnf_filter(self, has_dnf):
        assert_parity(FilterSet().add(DnfFilter(has_dnf)))

    @pytest.mark.parametrize("has_ftf", [True, False])
    def test_ftf_filter(self, has_ftf):
        assert_parity(FilterSet().add(FtfFilter(has_ftf)))

    def test_user_flag_null_excluded_from_true(self):
        # AllNone (user_flag=None) must NOT appear in the flagged=True result.
        codes = assert_parity(FilterSet().add(UserFlagFilter(True)))
        assert "GC6330003" not in codes

    def test_user_flag_null_included_in_false(self):
        # AllNone (user_flag=None) MUST appear in the flagged=False result
        # (bool(None) == False).
        codes = assert_parity(FilterSet().add(UserFlagFilter(False)))
        assert "GC6330003" in codes


class TestFavoritePointsFilter:
    def test_default_range(self):
        assert_parity(FilterSet().add(FavoritePointsFilter()))

    def test_narrow_range_excludes_none_as_zero(self):
        # AllNone has favorite_points=None -> treated as 0 by matches().
        codes = assert_parity(FilterSet().add(FavoritePointsFilter(min_pts=1, max_pts=9999)))
        assert "GC6330003" not in codes  # None -> 0, excluded by min_pts=1
        assert "GC6330002" not in codes  # explicit 0, excluded by min_pts=1
        assert "GC6330001" in codes      # 10, included

    def test_zero_inclusive_range_includes_none(self):
        codes = assert_parity(FilterSet().add(FavoritePointsFilter(min_pts=0, max_pts=0)))
        assert "GC6330003" in codes  # None -> 0
        assert "GC6330002" in codes  # explicit 0


class TestHasCorrectedFilter:
    def test_has_corrected(self):
        codes = assert_parity(FilterSet().add(HasCorrectedFilter()))
        assert "GC6330001" in codes       # is_corrected=True
        assert "GC6330002" not in codes   # UserNote exists but is_corrected=False
        assert "GC6330003" not in codes   # no UserNote row at all

    def test_no_corrected(self):
        codes = assert_parity(FilterSet().add(NoCorrectedFilter()))
        assert "GC6330001" not in codes
        assert "GC6330002" in codes
        assert "GC6330003" in codes


class TestFoundByMeDateFilter:
    def test_no_range_matches_any_found(self):
        codes = assert_parity(FilterSet().add(FoundByMeDateFilter()))
        assert "GC6330004" in codes  # found, with date
        assert "GC6330005" in codes  # found, no date -> still included
        assert "GC6330006" not in codes  # not found

    def test_range_still_includes_null_date(self):
        codes = assert_parity(FilterSet().add(FoundByMeDateFilter(
            from_date=datetime(2026, 1, 1), to_date=datetime(2026, 2, 1),
        )))
        # GC6330004's found_date (March) is outside this range, but a NULL
        # found_date is included regardless of range per matches()'s
        # `if fd is None: return True` short-circuit.
        assert "GC6330004" not in codes
        assert "GC6330005" in codes


class TestDnfDateFilter:
    def test_no_range_matches_any_dnf(self):
        codes = assert_parity(FilterSet().add(DnfDateFilter()))
        assert "GC6330007" in codes
        assert "GC6330008" in codes

    def test_range_still_includes_null_date(self):
        codes = assert_parity(FilterSet().add(DnfDateFilter(
            from_date=datetime(2026, 1, 1), to_date=datetime(2026, 2, 1),
        )))
        assert "GC6330007" not in codes  # dnf_date (April) outside range
        assert "GC6330008" in codes      # NULL dnf_date -> included regardless


class TestLastLogDateFilter:
    def test_null_last_log_date_excluded(self):
        # Opposite NULL semantics from Found/Dnf date filters above.
        codes = assert_parity(FilterSet().add(LastLogDateFilter()))
        assert "GC6330009" in codes
        assert "GC6330010" not in codes

    def test_range_excludes_out_of_range(self):
        codes = assert_parity(FilterSet().add(LastLogDateFilter(
            from_date=datetime(2026, 6, 1),
        )))
        assert "GC6330009" not in codes  # May 1st, before the range
        assert "GC6330010" not in codes  # NULL, always excluded


class TestComposition:
    def test_and_with_or_subtree(self):
        inner = FilterSet(mode="OR")
        inner.add(UserFlagFilter(True))
        inner.add(FtfFilter(True))
        outer = FilterSet(mode="AND")
        outer.add(inner)
        assert_parity(outer)

    def test_top_level_or(self):
        fs = FilterSet(mode="OR")
        fs.add(DnfFilter(True))
        fs.add(HasCorrectedFilter())
        assert_parity(fs)


class TestLightweightQueryPathSpecifically:
    # HasCorrectedFilter/NoCorrectedFilter's EXISTS subquery needs an
    # explicit .correlate(Cache): apply_filters_lightweight()'s select()
    # already outerjoins UserNote (for corrected-coords display), which
    # confuses SQLAlchemy's auto-correlation without it -- raises
    # InvalidRequestError ("no FROM clauses due to auto-correlation").
    # apply_filters()'s plain session.query(Cache) has no such outer
    # UserNote reference, so this only ever broke on the lightweight path.
    # Regression test for exactly that: run both filters through
    # apply_filters_lightweight() directly, not just apply_filters().

    def test_has_corrected_via_lightweight_path(self):
        from opensak.filters.engine import LightweightCache, apply_filters_lightweight

        with get_session() as s:
            result = apply_filters_lightweight(s, FilterSet().add(HasCorrectedFilter()))
        codes = {c.gc_code for c in result}
        assert codes == {"GC6330001"}
        assert all(isinstance(c, LightweightCache) for c in result)

    def test_no_corrected_via_lightweight_path(self):
        from opensak.filters.engine import apply_filters_lightweight

        with get_session() as s:
            result = apply_filters_lightweight(s, FilterSet().add(NoCorrectedFilter()))
        codes = {c.gc_code for c in result}
        assert "GC6330001" not in codes
        assert "GC6330002" in codes
        assert "GC6330003" in codes  # no UserNote row at all

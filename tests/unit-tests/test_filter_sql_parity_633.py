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

from datetime import date, datetime

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache, UserNote
from opensak.filters import engine
from opensak.filters.engine import (
    DATE_COMPARE_OPS, DATE_FILTER_FIELDS, DateFilter,
    DnfDateFilter, DnfFilter, FavoritePointsFilter, FilterSet, FoundByMeDateFilter,
    FtfFilter, HasCorrectedFilter, HiddenDateFilter, LastLogDateFilter, LockedFilter,
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
        # hidden_date present vs NULL — #857. Same NULL-exclusion semantics
        # as last_log_date above (a cache with no hidden_date never matches
        # a hidden-date range).
        Cache(gc_code="GC6330011", name="HasHiddenDate", cache_type="Traditional Cache",
              latitude=56.0, longitude=13.0,
              hidden_date=datetime(2026, 5, 15)),
        Cache(gc_code="GC6330012", name="NoHiddenDate", cache_type="Traditional Cache",
              latitude=56.1, longitude=13.1,
              hidden_date=None),
        # Several dates on one cache, with times of day, for DateFilter's
        # calendar-day comparisons: last found on the hidden day but earlier
        # in the day, last log 3 calendar days (2.6 x 24h) after hiding.
        Cache(gc_code="GC6330013", name="ManyDates", cache_type="Traditional Cache",
              latitude=56.2, longitude=13.2,
              hidden_date=datetime(2026, 5, 1, 18, 30),
              last_found_date=datetime(2026, 5, 1, 8, 0),
              last_log_date=datetime(2026, 5, 4, 9, 0),
              last_updated=datetime(2026, 4, 1, 23, 59, 59)),
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


class TestHiddenDateFilter:
    # #857: HiddenDateFilter used to be defined inline in filter_dialog.py
    # with no apply_to_query() at all (always fell back to Python matches()).
    # Now promoted to a proper class with SQL pushdown — verify parity.
    def test_null_hidden_date_excluded(self):
        codes = assert_parity(FilterSet().add(HiddenDateFilter()))
        assert "GC6330011" in codes
        assert "GC6330012" not in codes

    def test_range_excludes_out_of_range(self):
        codes = assert_parity(FilterSet().add(HiddenDateFilter(
            from_date=datetime(2026, 6, 1),
        )))
        assert "GC6330011" not in codes  # May 15th, before the range
        assert "GC6330012" not in codes  # NULL, always excluded


class TestDateFilter:
    # GSAK-style DateFilter: SQL pushdown (day-boundary ranges, and
    # date()/julianday() for compare) must agree with Python matches().

    @pytest.fixture(autouse=True)
    def fixed_today(self, monkeypatch):
        monkeypatch.setattr(engine, "_today", lambda: date(2026, 5, 10))

    @pytest.mark.parametrize("field", list(DATE_FILTER_FIELDS))
    @pytest.mark.parametrize("op, kwargs", [
        ("on_or_before", {"date1": date(2026, 4, 1)}),
        ("on_or_after",  {"date1": date(2026, 4, 1)}),
        ("equal",        {"date1": date(2026, 5, 1)}),
        ("between",      {"date1": date(2026, 5, 15), "date2": date(2026, 3, 15)}),
        ("during",       {"amount": 10, "unit": "days"}),
        ("not_during",   {"amount": 10, "unit": "days"}),
        ("during",       {"amount": 2, "unit": "months"}),
        ("not_during",   {"amount": 1, "unit": "years"}),
    ])
    def test_range_ops(self, field, op, kwargs):
        assert_parity(FilterSet().add(DateFilter(field, op, **kwargs)))

    @pytest.mark.parametrize("compare_op", DATE_COMPARE_OPS)
    @pytest.mark.parametrize("days", [2, 3])
    def test_compare_ops(self, compare_op, days):
        assert_parity(FilterSet().add(DateFilter(
            "last_log_date", "compare", other_field="hidden_date",
            compare_op=compare_op, compare_days=days,
        )))

    def test_equal_ignores_time_of_day(self):
        codes = assert_parity(FilterSet().add(DateFilter("hidden_date", "equal", date1=date(2026, 5, 1))))
        assert codes == {"GC6330013"}

    def test_on_or_before_includes_end_of_day(self):
        codes = assert_parity(FilterSet().add(DateFilter("changed_date", "on_or_before", date1=date(2026, 4, 1))))
        assert "GC6330013" in codes  # last_updated 23:59:59 on that day

    def test_compare_equal_by_calendar_day(self):
        codes = assert_parity(FilterSet().add(DateFilter(
            "last_found_date", "compare", other_field="hidden_date", compare_op="equal",
        )))
        assert codes == {"GC6330013"}

    def test_compare_within_counts_calendar_days(self):
        # 1 May 18:30 -> 4 May 09:00 is 2.6 x 24h but 3 calendar days.
        within_3 = assert_parity(FilterSet().add(DateFilter(
            "last_log_date", "compare", other_field="hidden_date",
            compare_op="within", compare_days=3,
        )))
        within_2 = assert_parity(FilterSet().add(DateFilter(
            "last_log_date", "compare", other_field="hidden_date",
            compare_op="within", compare_days=2,
        )))
        assert "GC6330013" in within_3
        assert "GC6330013" not in within_2

    def test_not_during_includes_missing_date(self):
        codes = assert_parity(FilterSet().add(DateFilter("last_log_date", "not_during", amount=10)))
        assert "GC6330010" in codes      # NULL last_log_date
        assert "GC6330009" not in codes  # 1 May — within 10 days of 10 May
        assert "GC6330013" not in codes  # 4 May

    def test_lightweight_path(self):
        from opensak.filters.engine import apply_filters_lightweight
        fs = FilterSet()
        fs.add(DateFilter("hidden_date", "during", amount=1, unit="months"))
        fs.add(DateFilter("last_log_date", "compare", other_field="hidden_date",
                          compare_op="newer"))
        with get_session() as s:
            codes = {c.gc_code for c in apply_filters_lightweight(s, fs)}
        assert codes == assert_parity(fs) == {"GC6330013"}


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

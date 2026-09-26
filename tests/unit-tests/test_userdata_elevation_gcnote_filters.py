"""tests/unit-tests/test_userdata_elevation_gcnote_filters.py — UserData1–4,
GC.com note and elevation filters: matching semantics, serialisation and
SQL-pushdown parity with matches() (same shape as test_filter_sql_parity_633).
"""

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.filters.engine import (
    FILTER_REGISTRY, ElevationFilter, FilterSet, GcNoteFilter, UserData1Filter,
    UserData2Filter, UserData3Filter, UserData4Filter, apply_filters,
)

_UD_FILTERS = (UserData1Filter, UserData2Filter, UserData3Filter, UserData4Filter)


@pytest.fixture(scope="module", autouse=True)
def seed(tmp_db):
    caches = [
        Cache(gc_code="GCUD0001", name="Solved", cache_type="Unknown Cache",
              latitude=47.0, longitude=8.0,
              user_data_1="Solved", user_data_2="N47 12.345", user_data_3="",
              user_data_4="Zürich", gc_note="Final at the big oak", elevation=412.0),
        Cache(gc_code="GCUD0002", name="Unsolved", cache_type="Unknown Cache",
              latitude=47.1, longitude=8.1,
              user_data_1="unsolved", user_data_2=None, user_data_3="x",
              user_data_4=None, gc_note=None, elevation=0.0),
        Cache(gc_code="GCUD0003", name="Nothing", cache_type="Traditional Cache",
              latitude=47.2, longitude=8.2,
              user_data_1=None, user_data_2=None, user_data_3=None,
              user_data_4=None, gc_note="", elevation=None),
        Cache(gc_code="GCUD0004", name="High", cache_type="Traditional Cache",
              latitude=46.5, longitude=8.0,
              user_data_1="SOLVED later", gc_note="FINAL checked", elevation=3454.5),
    ]
    with get_session() as s:
        for c in caches:
            s.add(c)


def assert_parity(fs):
    with get_session() as s:
        pushed = {c.gc_code for c in apply_filters(s, fs)}
        python = {c.gc_code for c in apply_filters(s, None) if fs.matches(c)}
    assert pushed == python, (
        f"only in SQL-pushed: {pushed - python}\n"
        f"only in Python matches(): {python - pushed}"
    )
    return pushed


class TestRegistry:
    @pytest.mark.parametrize("cls", _UD_FILTERS + (GcNoteFilter, ElevationFilter))
    def test_registered_under_its_filter_type(self, cls):
        assert FILTER_REGISTRY[cls.filter_type] is cls

    @pytest.mark.parametrize("cls", _UD_FILTERS + (GcNoteFilter,))
    def test_text_filter_round_trip(self, cls):
        f = FILTER_REGISTRY[cls.filter_type].from_dict(cls("abc", "starts_with").to_dict())
        assert (type(f), f.text, f.op) == (cls, "abc", "starts_with")

    def test_elevation_round_trip(self):
        f = ElevationFilter.from_dict(ElevationFilter(100.0, 1500.0).to_dict())
        assert (f.min_m, f.max_m) == (100.0, 1500.0)

    def test_filterset_round_trip(self):
        fs = FilterSet().add(UserData2Filter("N47")).add(ElevationFilter(0, 500))
        restored = FilterSet.from_dict(fs.to_dict())
        assert [type(f) for f in restored._filters] == [UserData2Filter, ElevationFilter]


class TestUserDataFilters:
    def test_contains_is_case_insensitive(self):
        assert assert_parity(FilterSet().add(UserData1Filter("solved"))) == \
            {"GCUD0001", "GCUD0002", "GCUD0004"}

    def test_equals(self):
        assert assert_parity(FilterSet().add(UserData1Filter("solved", "equals"))) == {"GCUD0001"}

    def test_empty_counts_null_and_blank(self):
        codes = assert_parity(FilterSet().add(UserData3Filter("", "empty")))
        assert {"GCUD0001", "GCUD0003", "GCUD0004"} <= codes
        assert "GCUD0002" not in codes

    def test_not_contains_lets_null_through(self):
        codes = assert_parity(FilterSet().add(UserData2Filter("N47", "not_contains")))
        assert "GCUD0001" not in codes
        assert {"GCUD0002", "GCUD0003"} <= codes

    def test_non_ascii_needle(self):
        assert assert_parity(FilterSet().add(UserData4Filter("ZÜRICH"))) == {"GCUD0001"}

    def test_regex(self):
        assert assert_parity(FilterSet().add(UserData2Filter(r"^N\d{2} "))) == set()
        assert assert_parity(FilterSet().add(UserData2Filter(r"^N\d{2} ", "regex"))) == {"GCUD0001"}


class TestGcNoteFilter:
    def test_contains(self):
        assert assert_parity(FilterSet().add(GcNoteFilter("final"))) == {"GCUD0001", "GCUD0004"}

    def test_not_empty(self):
        assert assert_parity(FilterSet().add(GcNoteFilter("", "not_empty"))) == {"GCUD0001", "GCUD0004"}

    def test_reads_gc_note_not_user_note(self):
        c = Cache(gc_code="GCX", name="n", gc_note=None)
        assert not GcNoteFilter("oak").matches(c)
        c.gc_note = "big OAK"
        assert GcNoteFilter("oak").matches(c)


class TestElevationFilter:
    def test_default_range_excludes_unknown(self):
        codes = assert_parity(FilterSet().add(ElevationFilter()))
        assert codes == {"GCUD0001", "GCUD0002", "GCUD0004"}

    def test_zero_is_a_real_elevation(self):
        assert assert_parity(FilterSet().add(ElevationFilter(0, 0))) == {"GCUD0002"}

    def test_bounds_are_inclusive(self):
        assert assert_parity(FilterSet().add(ElevationFilter(412, 3454.5))) == {"GCUD0001", "GCUD0004"}

    def test_high_only(self):
        assert assert_parity(FilterSet().add(ElevationFilter(1000, 9000))) == {"GCUD0004"}

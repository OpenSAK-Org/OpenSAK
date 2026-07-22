"""tests/unit-tests/test_filter_lightweight.py — issue #627 beta.9.

apply_filters_lightweight() must return the same gc_codes as apply_filters()
for every filterset it can safely handle, and must transparently fall back
to real Cache ORM objects (via apply_filters()) for anything that needs a
relationship or deferred text field. Reuses the same diverse seed dataset
(including NULL edge cases) as test_filter_sql_parity.py.
"""

import pytest

from opensak.db.database import get_session
from opensak.db.models import Attribute, Cache, Log, Trackable, UserNote
from opensak.filters.engine import (
    ArchivedFilter, AttributeFilter, AvailabilityFilter, AvailableFilter,
    CacheTypeFilter, ContainerFilter, CountryFilter, DifficultyFilter,
    DistanceFilter, FilterSet, FoundFilter, HasTrackableFilter,
    LightweightCache, NonPremiumFilter, NotFoundFilter, PlacedByFilter,
    PremiumFilter, SortSpec, TerrainFilter, TextSearchFilter,
    WhereClauseFilter, apply_filters, apply_filters_auto, apply_filters_lightweight,
)


# ── Seed: same diverse set as test_filter_sql_parity.py, plus a UserNote and
#    a relationship row so fallback behavior is exercisable ───────────────────

@pytest.fixture(scope="module", autouse=True)
def seed_lightweight_data(tmp_db):
    caches = [
        Cache(gc_code="GCL0001", name="Alpha", cache_type="Traditional Cache",
              container="Small", latitude=55.0, longitude=12.0,
              difficulty=1.5, terrain=2.0, placed_by="Alice", owner_name="Alice",
              country="Denmark", state="Zealand", county="Copenhagen",
              available=True, archived=False, found=True, premium_only=False),
        Cache(gc_code="GCL0002", name="Beta", cache_type="Unknown Cache",
              container="Micro", latitude=55.1, longitude=12.1,
              difficulty=5.0, terrain=4.0, placed_by="Bob", owner_name="Bobby",
              country="Denmark", state="Zealand", county="Roskilde",
              available=False, archived=False, found=False, premium_only=True),
        Cache(gc_code="GCL0003", name="Gamma", cache_type="Multi-cache",
              container="Regular", latitude=56.0, longitude=10.0,
              difficulty=3.0, terrain=3.0, placed_by="Alice", owner_name="Alice",
              country="Germany", state="Berlin", county="Mitte",
              available=True, archived=True, found=False, premium_only=False),
        Cache(gc_code="GCL0004", name="Delta", cache_type="Traditional Cache",
              container="Large", latitude=57.0, longitude=9.0,
              difficulty=None, terrain=None, placed_by="Carol", owner_name=None,
              country="Denmark", state=None, county=None,
              available=True, archived=False, found=False, premium_only=False),
        Cache(gc_code="GCL0005", name="Epsilon", cache_type="Letterbox Hybrid",
              container=None, latitude=52.0, longitude=13.0,
              difficulty=2.0, terrain=1.0, placed_by=None, owner_name="Dave",
              country=None, state=None, county=None,
              available=True, archived=False, found=True, premium_only=False),
    ]
    with get_session() as s:
        for c in caches:
            s.add(c)
        s.flush()

        alpha = next(c for c in caches if c.gc_code == "GCL0001")
        s.add(UserNote(cache_id=alpha.id, is_corrected=True,
                        corrected_lat=55.05, corrected_lon=12.05))
        s.add(Attribute(cache_id=alpha.id, attribute_id=1, name="Dogs allowed", is_on=True))
        s.add(Trackable(cache_id=alpha.id, ref="TB1", name="Bug One"))
        s.add(Log(cache_id=alpha.id, log_id="L1", log_type="Found it",
                   text="Great cache, found the special widget here"))


# ── Parity helper ─────────────────────────────────────────────────────────────

def assert_lightweight_parity(fs, sort=None):
    with get_session() as s:
        full = apply_filters(s, fs, sort)
        light = apply_filters_lightweight(s, fs, sort)
    full_codes = [c.gc_code for c in full]
    light_codes = [c.gc_code for c in light]
    assert set(light_codes) == set(full_codes), (
        f"apply_filters_lightweight() diverged from apply_filters():\n"
        f"  only in full:       {set(full_codes) - set(light_codes)}\n"
        f"  only in lightweight: {set(light_codes) - set(full_codes)}"
    )
    if sort is not None:
        assert light_codes == full_codes, (
            "Same gc_codes but different order — sort not applied consistently\n"
            f"  full:       {full_codes}\n"
            f"  lightweight: {light_codes}"
        )


# ── Single-filter parity ──────────────────────────────────────────────────────

def test_parity_no_filter():
    assert_lightweight_parity(None)
    assert_lightweight_parity(FilterSet())


def test_parity_cache_type():
    assert_lightweight_parity(FilterSet().add(CacheTypeFilter(["Traditional Cache"])))


def test_parity_container():
    assert_lightweight_parity(FilterSet().add(ContainerFilter(["Micro"])))


def test_parity_difficulty_terrain_including_null():
    assert_lightweight_parity(FilterSet().add(DifficultyFilter(1.0, 2.0)))
    assert_lightweight_parity(FilterSet().add(TerrainFilter(1.0, 2.0)))


def test_parity_found_not_found():
    assert_lightweight_parity(FilterSet().add(FoundFilter()))
    assert_lightweight_parity(FilterSet().add(NotFoundFilter()))


def test_parity_available_archived():
    assert_lightweight_parity(FilterSet().add(AvailableFilter()))
    assert_lightweight_parity(FilterSet().add(ArchivedFilter()))


def test_parity_availability_combinations():
    assert_lightweight_parity(FilterSet().add(AvailabilityFilter(True, True, True)))


def test_parity_country_including_null():
    assert_lightweight_parity(FilterSet().add(CountryFilter("denmark")))


def test_parity_placed_by_including_null():
    assert_lightweight_parity(FilterSet().add(PlacedByFilter("alice")))


def test_parity_premium_non_premium():
    assert_lightweight_parity(FilterSet().add(PremiumFilter()))
    assert_lightweight_parity(FilterSet().add(NonPremiumFilter()))


def test_parity_distance_filter_min_max():
    # DistanceFilter has sql_exact = False (#631) — must still be handled
    # correctly by the lightweight path's Python matches() fallback.
    assert_lightweight_parity(
        FilterSet().add(DistanceFilter(lat=55.0, lon=12.0, min_km=5.0, max_km=500.0))
    )


def test_parity_where_clause_filter():
    assert_lightweight_parity(FilterSet().add(WhereClauseFilter("archived = 0")))


# ── Composition parity ────────────────────────────────────────────────────────

def test_parity_and_containing_or_subtree():
    inner = FilterSet(mode="OR")
    inner.add(PremiumFilter())
    inner.add(ContainerFilter(["Large"]))
    outer = FilterSet(mode="AND")
    outer.add(AvailableFilter())
    outer.add(inner)
    assert_lightweight_parity(outer)


def test_parity_top_level_or():
    fs = FilterSet(mode="OR")
    fs.add(CacheTypeFilter(["Multi-cache"]))
    fs.add(FoundFilter())
    assert_lightweight_parity(fs)


# ── Sort parity ────────────────────────────────────────────────────────────────

def test_parity_sql_sorted_field():
    assert_lightweight_parity(FilterSet(), sort=SortSpec("difficulty", ascending=True))
    assert_lightweight_parity(FilterSet(), sort=SortSpec("difficulty", ascending=False))


def test_parity_python_sorted_field():
    # "name" is deliberately never SQL-sorted (Unicode correctness, see
    # _sql_order_expr's docstring) — exercises the Python-side sort path
    # for both apply_filters() and apply_filters_lightweight().
    assert_lightweight_parity(FilterSet(), sort=SortSpec("name", ascending=True))


# ── Fallback: relationship-needing filters must return real Cache objects ────

class TestFallbackToFullOrm:
    def test_attribute_filter_falls_back(self):
        with get_session() as s:
            fs = FilterSet().add(AttributeFilter(attribute_id=1, is_on=True))
            result = apply_filters_lightweight(s, fs)
        assert len(result) == 1
        assert result[0].gc_code == "GCL0001"
        assert isinstance(result[0], Cache)
        assert not isinstance(result[0], LightweightCache)

    def test_has_trackable_filter_falls_back(self):
        with get_session() as s:
            fs = FilterSet().add(HasTrackableFilter())
            result = apply_filters_lightweight(s, fs)
        assert len(result) == 1
        assert isinstance(result[0], Cache)

    def test_text_search_with_logs_falls_back(self):
        with get_session() as s:
            fs = FilterSet().add(TextSearchFilter("special widget", search_logs=True))
            result = apply_filters_lightweight(s, fs)
        assert len(result) == 1
        assert isinstance(result[0], Cache)

    def test_no_relationship_filter_uses_lightweight_path(self):
        with get_session() as s:
            fs = FilterSet().add(ArchivedFilter())
            result = apply_filters_lightweight(s, fs)
        assert len(result) == 1
        assert isinstance(result[0], LightweightCache)


# ── user_note wiring ──────────────────────────────────────────────────────────

class TestUserNoteWiring:
    def test_corrected_cache_has_user_note(self):
        with get_session() as s:
            fs = FilterSet().add(CacheTypeFilter(["Traditional Cache"]))
            result = apply_filters_lightweight(s, fs)
        alpha = next(c for c in result if c.gc_code == "GCL0001")
        assert alpha.user_note is not None
        assert alpha.user_note.is_corrected is True
        assert alpha.user_note.corrected_lat == pytest.approx(55.05)
        assert alpha.user_note.corrected_lon == pytest.approx(12.05)

    def test_uncorrected_cache_has_no_user_note(self):
        with get_session() as s:
            fs = FilterSet().add(CacheTypeFilter(["Traditional Cache"]))
            result = apply_filters_lightweight(s, fs)
        delta = next(c for c in result if c.gc_code == "GCL0004")
        assert delta.user_note is None


# ── LightweightCache safety net ───────────────────────────────────────────────

class TestLightweightCacheSafetyNet:
    def test_scalar_attribute_access_works(self):
        with get_session() as s:
            fs = FilterSet().add(ArchivedFilter())
            result = apply_filters_lightweight(s, fs)
        cache = result[0]
        assert cache.gc_code == "GCL0003"
        assert cache.name == "Gamma"
        assert cache.archived is True

    def test_relationship_attribute_raises(self):
        with get_session() as s:
            fs = FilterSet().add(ArchivedFilter())
            result = apply_filters_lightweight(s, fs)
        cache = result[0]
        for attr in ("logs", "attributes", "trackables", "waypoints",
                     "short_description", "long_description", "encoded_hints"):
            with pytest.raises(AttributeError):
                getattr(cache, attr)


class TestLightweightCacheMutableFields:
    # CacheTableModel.setData() (user_flag/locked/first_to_find quick-toggle)
    # mutates the table's own cache object in place after persisting via a
    # freshly-queried real Cache ORM object, purely so the UI reflects the
    # change without a full table reload. LightweightCache must support
    # exactly that for these three fields, and nothing else.

    def test_user_flag_can_be_set_in_place(self):
        with get_session() as s:
            fs = FilterSet().add(ArchivedFilter())
            cache = apply_filters_lightweight(s, fs)[0]
        assert cache.user_flag is False
        cache.user_flag = True
        assert cache.user_flag is True

    def test_locked_can_be_set_in_place(self):
        with get_session() as s:
            fs = FilterSet().add(ArchivedFilter())
            cache = apply_filters_lightweight(s, fs)[0]
        cache.locked = True
        assert cache.locked is True

    def test_first_to_find_can_be_set_in_place(self):
        with get_session() as s:
            fs = FilterSet().add(ArchivedFilter())
            cache = apply_filters_lightweight(s, fs)[0]
        cache.first_to_find = True
        assert cache.first_to_find is True

    def test_other_fields_remain_read_only(self):
        with get_session() as s:
            fs = FilterSet().add(ArchivedFilter())
            cache = apply_filters_lightweight(s, fs)[0]
        with pytest.raises(AttributeError):
            cache.name = "Renamed"
        with pytest.raises(AttributeError):
            cache.archived = False

    def test_override_does_not_leak_between_instances(self):
        with get_session() as s:
            fs = FilterSet().add(CacheTypeFilter(["Traditional Cache"]))
            result = apply_filters_lightweight(s, fs)
        assert len(result) >= 2
        result[0].user_flag = True
        assert result[1].user_flag is False


# ── apply_filters_auto() — feature-flag dispatch (#627 beta.10) ──────────────
#
# mainwindow.py calls apply_filters_auto() exclusively (not apply_filters()
# or apply_filters_lightweight() directly) so the flag check lives in one
# place. These tests exercise that dispatch directly, since there's no
# unit-testable MainWindow test suite to wire an integration test through
# (only an e2e suite, which needs a real QtWebEngine).

class TestApplyFiltersAutoDispatch:
    # #627 beta.11: apply_filters_auto() no longer checks a feature flag —
    # both the lightweight and full-ORM paths are now confirmed stable, so
    # it always attempts apply_filters_lightweight() (which itself falls
    # back to apply_filters() automatically when needed).

    def test_returns_lightweight_objects_when_safe(self):
        with get_session() as s:
            fs = FilterSet().add(ArchivedFilter())
            result = apply_filters_auto(s, fs)
        assert len(result) == 1
        assert isinstance(result[0], LightweightCache)

    def test_falls_back_to_real_cache_for_relationship_filters(self):
        with get_session() as s:
            fs = FilterSet().add(AttributeFilter(attribute_id=1, is_on=True))
            result = apply_filters_auto(s, fs)
        assert len(result) == 1
        assert isinstance(result[0], Cache)
        assert not isinstance(result[0], LightweightCache)

    def test_result_matches_apply_filters(self):
        # apply_filters_auto() must never diverge from apply_filters() —
        # it's purely a faster path to the same answer.
        with get_session() as s:
            fs = FilterSet().add(CacheTypeFilter(["Traditional Cache"]))
            auto_codes = {c.gc_code for c in apply_filters_auto(s, fs)}
            full_codes = {c.gc_code for c in apply_filters(s, fs)}
        assert auto_codes == full_codes

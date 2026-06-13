"""tests/unit-tests/test_filters.py — filter engine tests."""

import json
import pytest
from pathlib import Path
from datetime import datetime, timezone

from opensak.db.database import get_session
from opensak.db.models import Cache, Attribute, Trackable
from opensak.filters.engine import (
    FilterSet, SortSpec, FilterProfile, apply_filters, annotate_distances,
    # All filter classes
    CacheTypeFilter, ContainerFilter, DifficultyFilter, TerrainFilter,
    FoundFilter, NotFoundFilter, AvailableFilter, ArchivedFilter,
    CountryFilter, StateFilter, CountyFilter, NameFilter, GcCodeFilter, PlacedByFilter,
    DistanceFilter, AttributeFilter, HasTrackableFilter,
    PremiumFilter, NonPremiumFilter,
    WhereClauseFilter,
    # Helpers
    _haversine_km, _iter_filters, FILTER_REGISTRY, SORT_FIELDS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def seed_data(tmp_db):
    """Insert a set of test caches covering all filter scenarios."""
    caches = [
        Cache(
            gc_code="GC00001", name="Easy Traditional",
            cache_type="Traditional Cache", container="Small",
            latitude=55.6761, longitude=12.5683,
            difficulty=1.5, terrain=1.5,
            placed_by="OwnerA", country="Denmark", state="Zealand",
            county="Copenhagen", available=True, archived=False, found=False,
            premium_only=False,
        ),
        Cache(
            gc_code="GC00002", name="Hard Mystery",
            cache_type="Unknown Cache", container="Micro",
            latitude=55.6800, longitude=12.5700,
            difficulty=5.0, terrain=4.0,
            placed_by="OwnerB", country="Denmark", state="Zealand",
            county="Copenhagen", available=True, archived=False, found=True,
            premium_only=False,
        ),
        Cache(
            gc_code="GC00003", name="Medium Multi",
            cache_type="Multi-cache", container="Regular",
            latitude=56.1629, longitude=10.2039,
            difficulty=3.0, terrain=3.0,
            placed_by="OwnerA", country="Denmark", state="Region Midtjylland",
            county="Aarhus", available=True, archived=False, found=False,
            premium_only=True,
        ),
        Cache(
            gc_code="GC00004", name="Archived Cache",
            cache_type="Traditional Cache", container="Large",
            latitude=57.0480, longitude=9.9187,
            difficulty=2.0, terrain=2.0,
            placed_by="OwnerC", country="Denmark", state="Region Nordjylland",
            county="Aalborg", available=False, archived=True, found=False,
            premium_only=False,
        ),
        Cache(
            gc_code="GC00005", name="German Traditional",
            cache_type="Traditional Cache", container="Small",
            latitude=52.5200, longitude=13.4050,
            difficulty=1.0, terrain=1.0,
            placed_by="OwnerD", country="Germany", state="Berlin",
            county="Mitte", available=True, archived=False, found=False,
            premium_only=False,
        ),
        Cache(
            gc_code="GC00006", name="Letterbox Cache",
            cache_type="Letterbox Hybrid", container="Regular",
            latitude=55.4000, longitude=10.3833,
            difficulty=2.5, terrain=2.5,
            placed_by="OwnerB", country="Denmark", state="Region Syddanmark",
            county="Odense", available=True, archived=False, found=False,
            premium_only=False,
        ),
    ]

    with get_session() as s:
        for cache in caches:
            s.add(cache)

    # Add attributes to GC00001
    with get_session() as s:
        c = s.query(Cache).filter_by(gc_code="GC00001").one()
        c.attributes.append(Attribute(attribute_id=6, name="Recommended for kids", is_on=True))
        c.attributes.append(Attribute(attribute_id=24, name="Wheelchair accessible", is_on=False))

    # Add trackable to GC00003
    with get_session() as s:
        c = s.query(Cache).filter_by(gc_code="GC00003").one()
        c.trackables.append(Trackable(ref="TB12345", name="Travel Bug"))


# ── Haversine distance helper ─────────────────────────────────────────────────

def test_haversine_same_point():
    assert _haversine_km(55.0, 10.0, 55.0, 10.0) == pytest.approx(0.0)


def test_haversine_known_distance():
    # Copenhagen to Aarhus ≈ 155 km
    dist = _haversine_km(55.6761, 12.5683, 56.1629, 10.2039)
    assert 150 < dist < 165


# ── Individual filter tests ───────────────────────────────────────────────────

def test_cache_type_filter(tmp_db):
    with get_session() as s:
        fs = FilterSet().add(CacheTypeFilter(["Traditional Cache"]))
        results = apply_filters(s, fs)
    codes = {c.gc_code for c in results}
    assert "GC00001" in codes
    assert "GC00004" in codes   # archived traditional still matches type filter
    assert "GC00002" not in codes
    assert "GC00003" not in codes


def test_cache_type_filter_multiple(tmp_db):
    with get_session() as s:
        fs = FilterSet().add(CacheTypeFilter(["Traditional Cache", "Multi-cache"]))
        results = apply_filters(s, fs)
    types = {c.cache_type for c in results}
    assert "Unknown Cache" not in types
    assert "Traditional Cache" in types
    assert "Multi-cache" in types


def test_difficulty_filter(tmp_db):
    with get_session() as s:
        fs = FilterSet().add(DifficultyFilter(max_difficulty=2.0))
        results = apply_filters(s, fs)
    for c in results:
        assert c.difficulty <= 2.0


def test_difficulty_filter_range(tmp_db):
    with get_session() as s:
        fs = FilterSet().add(DifficultyFilter(min_difficulty=2.0, max_difficulty=3.0))
        results = apply_filters(s, fs)
    codes = {c.gc_code for c in results}
    assert "GC00003" in codes   # D=3.0
    assert "GC00004" in codes   # D=2.0
    assert "GC00001" not in codes  # D=1.5
    assert "GC00002" not in codes  # D=5.0


def test_terrain_filter(tmp_db):
    with get_session() as s:
        fs = FilterSet().add(TerrainFilter(max_terrain=2.0))
        results = apply_filters(s, fs)
    for c in results:
        assert c.terrain <= 2.0


def test_found_filter(tmp_db):
    with get_session() as s:
        results = apply_filters(s, FilterSet().add(FoundFilter()))
    assert all(c.found for c in results)
    codes = {c.gc_code for c in results}
    assert "GC00002" in codes


def test_not_found_filter(tmp_db):
    with get_session() as s:
        results = apply_filters(s, FilterSet().add(NotFoundFilter()))
    assert all(not c.found for c in results)
    assert "GC00002" not in {c.gc_code for c in results}


def test_available_filter(tmp_db):
    with get_session() as s:
        results = apply_filters(s, FilterSet().add(AvailableFilter()))
    for c in results:
        assert c.available is True
        assert c.archived is False
    assert "GC00004" not in {c.gc_code for c in results}


def test_archived_filter(tmp_db):
    with get_session() as s:
        results = apply_filters(s, FilterSet().add(ArchivedFilter()))
    codes = {c.gc_code for c in results}
    assert "GC00004" in codes
    assert "GC00001" not in codes


def test_country_filter(tmp_db):
    with get_session() as s:
        results = apply_filters(s, FilterSet().add(CountryFilter("Denmark")))
    for c in results:
        assert c.country == "Denmark"
    assert "GC00005" not in {c.gc_code for c in results}


def test_state_filter(tmp_db):
    with get_session() as s:
        results = apply_filters(s, FilterSet().add(StateFilter("Zealand")))
    codes = {c.gc_code for c in results}
    assert "GC00001" in codes
    assert "GC00002" in codes
    assert "GC00003" not in codes


def test_county_filter(tmp_db):
    with get_session() as s:
        results = apply_filters(s, FilterSet().add(CountyFilter("Copenhagen")))
    codes = {c.gc_code for c in results}
    assert "GC00001" in codes
    assert "GC00002" in codes
    assert "GC00003" not in codes


def test_name_filter(tmp_db):
    with get_session() as s:
        results = apply_filters(s, FilterSet().add(NameFilter("traditional")))
    for c in results:
        assert "traditional" in c.name.lower()


def test_gc_code_filter(tmp_db):
    with get_session() as s:
        results = apply_filters(s, FilterSet().add(GcCodeFilter("GC00001")))
    assert len(results) == 1
    assert results[0].gc_code == "GC00001"


def test_placed_by_filter(tmp_db):
    with get_session() as s:
        results = apply_filters(s, FilterSet().add(PlacedByFilter("ownera")))
    codes = {c.gc_code for c in results}
    assert "GC00001" in codes
    assert "GC00003" in codes
    assert "GC00002" not in codes


def test_distance_filter(tmp_db):
    # From Copenhagen (55.6761, 12.5683), within 5km
    with get_session() as s:
        fs = FilterSet().add(DistanceFilter(lat=55.6761, lon=12.5683, max_km=5.0))
        results = apply_filters(s, fs)
    codes = {c.gc_code for c in results}
    assert "GC00001" in codes   # at the reference point
    assert "GC00002" in codes   # very close
    assert "GC00005" not in codes  # Berlin


def test_distance_filter_min_max(tmp_db):
    # Only caches 1–200km from Copenhagen
    with get_session() as s:
        fs = FilterSet().add(DistanceFilter(lat=55.6761, lon=12.5683, min_km=1.0, max_km=200.0))
        results = apply_filters(s, fs)
    codes = {c.gc_code for c in results}
    assert "GC00001" not in codes  # < 1km
    assert "GC00005" not in codes  # > 200km
    assert "GC00003" in codes      # ~155km


def test_attribute_filter(tmp_db):
    with get_session() as s:
        fs = FilterSet().add(AttributeFilter(attribute_id=6, is_on=True))
        results = apply_filters(s, fs)
    codes = {c.gc_code for c in results}
    assert "GC00001" in codes


def test_has_trackable_filter(tmp_db):
    with get_session() as s:
        results = apply_filters(s, FilterSet().add(HasTrackableFilter()))
    codes = {c.gc_code for c in results}
    assert "GC00003" in codes
    assert "GC00001" not in codes


def test_premium_filter(tmp_db):
    with get_session() as s:
        results = apply_filters(s, FilterSet().add(PremiumFilter()))
    assert all(c.premium_only for c in results)


def test_non_premium_filter(tmp_db):
    with get_session() as s:
        results = apply_filters(s, FilterSet().add(NonPremiumFilter()))
    assert all(not c.premium_only for c in results)


def test_container_filter(tmp_db):
    with get_session() as s:
        results = apply_filters(s, FilterSet().add(ContainerFilter(["Micro", "Small"])))
    for c in results:
        assert c.container in ("Micro", "Small")


# ── FilterSet AND / OR logic ──────────────────────────────────────────────────

def test_filterset_and(tmp_db):
    """AND: must match ALL filters."""
    with get_session() as s:
        fs = FilterSet(mode="AND")
        fs.add(CacheTypeFilter(["Traditional Cache"]))
        fs.add(DifficultyFilter(max_difficulty=2.0))
        results = apply_filters(s, fs)
    for c in results:
        assert c.cache_type == "Traditional Cache"
        assert c.difficulty <= 2.0


def test_filterset_or(tmp_db):
    """OR: must match AT LEAST ONE filter."""
    with get_session() as s:
        fs = FilterSet(mode="OR")
        fs.add(CacheTypeFilter(["Multi-cache"]))
        fs.add(CacheTypeFilter(["Letterbox Hybrid"]))
        results = apply_filters(s, fs)
    types = {c.cache_type for c in results}
    assert "Traditional Cache" not in types
    assert "Multi-cache" in types
    assert "Letterbox Hybrid" in types


def test_filterset_nested(tmp_db):
    """Nested FilterSets: (Traditional OR Multi) AND available."""
    with get_session() as s:
        inner = FilterSet(mode="OR")
        inner.add(CacheTypeFilter(["Traditional Cache"]))
        inner.add(CacheTypeFilter(["Multi-cache"]))

        outer = FilterSet(mode="AND")
        outer.add(inner)
        outer.add(AvailableFilter())

        results = apply_filters(s, outer)

    for c in results:
        assert c.cache_type in ("Traditional Cache", "Multi-cache")
        assert c.available is True
        assert c.archived is False
    # GC00004 is Traditional but archived — should be excluded
    assert "GC00004" not in {c.gc_code for c in results}


def test_empty_filterset_returns_all(tmp_db):
    """An empty FilterSet should return all caches."""
    with get_session() as s:
        all_results = apply_filters(s)
        filtered = apply_filters(s, FilterSet())
    assert len(filtered) == len(all_results)


# ── Sorting ───────────────────────────────────────────────────────────────────

def test_sort_by_name(tmp_db):
    with get_session() as s:
        results = apply_filters(s, sort=SortSpec("name", ascending=True))
    names = [c.name for c in results]
    assert names == sorted(names, key=str.lower)


def test_sort_by_difficulty_desc(tmp_db):
    with get_session() as s:
        results = apply_filters(s, sort=SortSpec("difficulty", ascending=False))
    diffs = [c.difficulty for c in results if c.difficulty is not None]
    assert diffs == sorted(diffs, reverse=True)


def test_sort_by_distance(tmp_db):
    home = (55.6761, 12.5683)
    with get_session() as s:
        results = apply_filters(s, sort=SortSpec("name"), distance_from=home)
    # Just verify it runs without error; distance sort tested separately
    assert len(results) > 0


def test_invalid_sort_field():
    with pytest.raises(ValueError):
        SortSpec("nonexistent_field")


# ── Serialisation / deserialisation ──────────────────────────────────────────

def test_filter_serialisation_roundtrip():
    """Every filter should serialise and deserialise correctly."""
    filters = [
        CacheTypeFilter(["Traditional Cache", "Multi-cache"]),
        ContainerFilter(["Small", "Micro"]),
        DifficultyFilter(1.5, 4.0),
        TerrainFilter(1.0, 3.0),
        FoundFilter(),
        NotFoundFilter(),
        AvailableFilter(),
        ArchivedFilter(),
        CountryFilter("Denmark"),
        StateFilter("Zealand"),
        CountyFilter("Copenhagen"),
        NameFilter("traditional"),
        GcCodeFilter("GC123"),
        PlacedByFilter("owner"),
        DistanceFilter(55.6, 12.5, 50.0, 1.0),
        AttributeFilter(6, True),
        HasTrackableFilter(),
        PremiumFilter(),
        NonPremiumFilter(),
    ]
    for f in filters:
        data = f.to_dict()
        assert "filter_type" in data
        ftype = data["filter_type"]
        assert ftype in FILTER_REGISTRY
        restored = FILTER_REGISTRY[ftype].from_dict(data)
        assert restored.to_dict() == data, f"Roundtrip failed for {f}"


def test_filterset_serialisation_roundtrip():
    fs = FilterSet(mode="AND")
    fs.add(CacheTypeFilter(["Traditional Cache"]))
    inner = FilterSet(mode="OR")
    inner.add(DifficultyFilter(max_difficulty=2.0))
    inner.add(TerrainFilter(max_terrain=2.0))
    fs.add(inner)

    data = fs.to_dict()
    restored = FilterSet.from_dict(data)
    assert restored.mode == "AND"
    assert len(restored._filters) == 2


def test_sort_spec_serialisation():
    s = SortSpec("difficulty", ascending=False)
    data = s.to_dict()
    restored = SortSpec.from_dict(data)
    assert restored.field == "difficulty"
    assert restored.ascending is False


# ── Filter profiles ───────────────────────────────────────────────────────────

def test_save_and_load_profile(tmp_path):
    fs = FilterSet()
    fs.add(CacheTypeFilter(["Traditional Cache"]))
    fs.add(DifficultyFilter(max_difficulty=2.0))
    profile = FilterProfile("test-profile", fs, SortSpec("difficulty"))

    saved = profile.save(profiles_dir=tmp_path)
    assert saved.exists()

    loaded = FilterProfile.load(saved)
    assert loaded.name == "test-profile"
    assert loaded.sort.field == "difficulty"
    assert len(loaded.filterset._filters) == 2


def test_list_profiles(tmp_path):
    fs = FilterSet()
    FilterProfile("profile-a", fs).save(tmp_path)
    FilterProfile("profile-b", fs).save(tmp_path)
    profiles = FilterProfile.list_profiles(tmp_path)
    assert len(profiles) == 2


def test_empty_profiles_dir(tmp_path):
    empty_dir = tmp_path / "no_profiles"
    profiles = FilterProfile.list_profiles(empty_dir)
    assert profiles == []


# ── Distance annotation ───────────────────────────────────────────────────────

def test_annotate_distances(tmp_db):
    home = (55.6761, 12.5683)
    with get_session() as s:
        caches = apply_filters(s)
        distances = annotate_distances(caches, *home)
    assert len(distances) == len(caches)
    for cache_id, dist in distances.items():
        assert isinstance(dist, float)
        assert dist >= 0


# ── Limit ─────────────────────────────────────────────────────────────────────

def test_limit(tmp_db):
    with get_session() as s:
        results = apply_filters(s, limit=3)
    assert len(results) == 3


# ── WhereClauseFilter — class behaviour (pure, no DB) ─────────────────────────

class TestWhereClauseFilterClass:
    def test_in_registry(self):
        assert "where_clause" in FILTER_REGISTRY
        assert FILTER_REGISTRY["where_clause"] is WhereClauseFilter

    def test_to_dict(self):
        f = WhereClauseFilter("difficulty >= 3")
        assert f.to_dict() == {"filter_type": "where_clause", "sql": "difficulty >= 3"}

    def test_from_dict(self):
        f = WhereClauseFilter.from_dict({"filter_type": "where_clause", "sql": "terrain < 2"})
        assert f.sql == "terrain < 2"

    def test_roundtrip(self):
        f = WhereClauseFilter("difficulty >= 3 AND country = 'Denmark'")
        restored = FILTER_REGISTRY["where_clause"].from_dict(f.to_dict())
        assert restored.to_dict() == f.to_dict()

    def test_empty_sql_roundtrip(self):
        f = WhereClauseFilter("")
        restored = WhereClauseFilter.from_dict(f.to_dict())
        assert restored.sql == ""

    def test_sql_is_stripped_on_init(self):
        f = WhereClauseFilter("  difficulty >= 3  ")
        assert f.sql == "difficulty >= 3"

    def test_matches_returns_true_when_no_prerun(self, make_cache):
        """_matching_ids is None before any apply_filters call — pass everything."""
        f = WhereClauseFilter("difficulty >= 3")
        assert f._matching_ids is None
        assert f.matches(make_cache()) is True

    def test_matches_true_when_id_in_set(self):
        f = WhereClauseFilter("difficulty >= 3")
        f._matching_ids = {42}
        c = Cache(id=42, gc_code="GC00001", name="X",
                  cache_type="Traditional Cache", latitude=0.0, longitude=0.0)
        assert f.matches(c) is True

    def test_matches_false_when_id_not_in_set(self):
        f = WhereClauseFilter("difficulty >= 3")
        f._matching_ids = {42}
        c = Cache(id=99, gc_code="GC00002", name="Y",
                  cache_type="Traditional Cache", latitude=0.0, longitude=0.0)
        assert f.matches(c) is False

    def test_empty_set_excludes_all(self):
        f = WhereClauseFilter("difficulty >= 99")
        f._matching_ids = set()
        c = Cache(id=1, gc_code="GC00001", name="X",
                  cache_type="Traditional Cache", latitude=0.0, longitude=0.0)
        assert f.matches(c) is False

    def test_filter_type_constant(self):
        assert WhereClauseFilter.filter_type == "where_clause"

    def test_serialisation_in_global_roundtrip(self):
        """WhereClauseFilter participates correctly in the global serialisation test."""
        f = WhereClauseFilter("placed_by = 'OwnerA'")
        data = f.to_dict()
        ftype = data["filter_type"]
        assert ftype in FILTER_REGISTRY
        restored = FILTER_REGISTRY[ftype].from_dict(data)
        assert restored.to_dict() == data


# ── _iter_filters helper ───────────────────────────────────────────────────────

class TestIterFilters:
    def test_flat_yields_all_leaves(self):
        f1 = CacheTypeFilter(["Traditional Cache"])
        f2 = DifficultyFilter(max_difficulty=3.0)
        fs = FilterSet(mode="AND")
        fs.add(f1)
        fs.add(f2)
        assert list(_iter_filters(fs)) == [f1, f2]

    def test_nested_yields_all_leaves_recursively(self):
        f1 = CacheTypeFilter(["Traditional Cache"])
        f2 = DifficultyFilter(max_difficulty=3.0)
        f3 = FoundFilter()
        inner = FilterSet(mode="OR")
        inner.add(f2)
        inner.add(f3)
        outer = FilterSet(mode="AND")
        outer.add(f1)
        outer.add(inner)
        assert list(_iter_filters(outer)) == [f1, f2, f3]

    def test_empty_filterset_yields_nothing(self):
        assert list(_iter_filters(FilterSet())) == []

    def test_where_clause_found_inside_nested(self):
        wf = WhereClauseFilter("difficulty >= 3")
        inner = FilterSet(mode="OR")
        inner.add(wf)
        outer = FilterSet(mode="AND")
        outer.add(CacheTypeFilter(["Traditional Cache"]))
        outer.add(inner)
        result = list(_iter_filters(outer))
        assert wf in result
        assert len(result) == 2

    def test_deeply_nested(self):
        f1 = FoundFilter()
        f2 = ArchivedFilter()
        f3 = PremiumFilter()
        level3 = FilterSet()
        level3.add(f3)
        level2 = FilterSet()
        level2.add(f2)
        level2.add(level3)
        level1 = FilterSet()
        level1.add(f1)
        level1.add(level2)
        assert list(_iter_filters(level1)) == [f1, f2, f3]


# ── WhereClauseFilter integration with apply_filters ─────────────────────────

def test_where_clause_valid_sql_filters(tmp_db):
    """Valid SQL narrows results; only caches whose difficulty >= 4.0 pass."""
    with get_session() as s:
        fs = FilterSet().add(WhereClauseFilter("difficulty >= 4.0"))
        results = apply_filters(s, fs)
    codes = {c.gc_code for c in results}
    assert "GC00002" in codes      # D=5.0
    assert "GC00001" not in codes  # D=1.5
    assert "GC00003" not in codes  # D=3.0


def test_where_clause_string_column(tmp_db):
    """String column filter: country = 'Germany' returns only GC00005."""
    with get_session() as s:
        fs = FilterSet().add(WhereClauseFilter("country = 'Germany'"))
        results = apply_filters(s, fs)
    codes = {c.gc_code for c in results}
    assert "GC00005" in codes
    assert len(codes) == 1


def test_where_clause_invalid_sql_returns_empty(tmp_db):
    """Invalid SQL silently produces zero matches (no exception raised)."""
    with get_session() as s:
        fs = FilterSet().add(WhereClauseFilter("this is NOT valid SQL!!!"))
        results = apply_filters(s, fs)
    assert results == []


def test_where_clause_empty_sql_passes_all(tmp_db):
    """Empty SQL string skips the pre-run; _matching_ids stays None → all pass."""
    with get_session() as s:
        all_results = apply_filters(s)
        filtered = apply_filters(s, FilterSet().add(WhereClauseFilter("")))
    assert len(filtered) == len(all_results)


def test_where_clause_combined_with_type_filter(tmp_db):
    """WhereClauseFilter AND CacheTypeFilter: both constraints must be satisfied."""
    with get_session() as s:
        fs = FilterSet(mode="AND")
        fs.add(CacheTypeFilter(["Traditional Cache"]))
        fs.add(WhereClauseFilter("difficulty <= 2.0"))
        results = apply_filters(s, fs)
    codes = {c.gc_code for c in results}
    assert "GC00001" in codes      # Traditional, D=1.5
    assert "GC00004" in codes      # Traditional, D=2.0 (archived but matches both)
    assert "GC00005" in codes      # Traditional, D=1.0
    assert "GC00002" not in codes  # Unknown Cache — excluded by type filter
    assert "GC00003" not in codes  # Multi-cache — excluded by type filter


def test_where_clause_in_nested_filterset(tmp_db):
    """_iter_filters reaches WhereClauseFilter nested inside another FilterSet."""
    with get_session() as s:
        inner = FilterSet(mode="AND")
        inner.add(WhereClauseFilter("difficulty >= 4.0"))
        outer = FilterSet(mode="AND")
        outer.add(inner)
        results = apply_filters(s, outer)
    codes = {c.gc_code for c in results}
    assert "GC00002" in codes      # D=5.0
    assert "GC00001" not in codes  # D=1.5


def test_apply_filters_defers_description_blobs(tmp_db):
    """The large free-text blobs are deferred on the table query.

    short_description / long_description / encoded_hints are never shown in the
    cache table (only in the detail panel, which loads each cache separately).
    Deferring them keeps the refresh light on large databases. They must remain
    unloaded after apply_filters() returns.
    """
    from sqlalchemy import inspect as sa_inspect

    with get_session() as s:
        results = apply_filters(s)
        assert results, "expected seeded caches"
        for c in results:
            unloaded = sa_inspect(c).unloaded
            assert "short_description" in unloaded
            assert "long_description" in unloaded
            assert "encoded_hints" in unloaded
            # A column the table actually reads must be loaded eagerly.
            assert "name" not in unloaded
            assert "cache_type" not in unloaded


def test_apply_filters_results_unchanged_with_deferral(tmp_db):
    """Deferring blobs must not change which caches are returned."""
    with get_session() as s:
        all_codes = {c.gc_code for c in apply_filters(s)}
        fs = FilterSet(mode="AND")
        fs.add(CacheTypeFilter(["Traditional Cache"]))
        traditional = {c.gc_code for c in apply_filters(s, fs)}
    assert "GC00001" in all_codes
    assert traditional and traditional <= all_codes


def test_where_clause_profile_save_load(tmp_path):
    """FilterProfile containing WhereClauseFilter round-trips the SQL through JSON."""
    sql = "difficulty >= 3 AND terrain <= 4"
    fs = FilterSet()
    fs.add(WhereClauseFilter(sql))
    saved = FilterProfile("where-test", fs).save(profiles_dir=tmp_path)
    loaded = FilterProfile.load(saved)
    where_filters = [
        f for f in _iter_filters(loaded.filterset)
        if isinstance(f, WhereClauseFilter)
    ]
    assert len(where_filters) == 1
    assert where_filters[0].sql == sql

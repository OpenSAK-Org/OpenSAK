"""
tests/unit-tests/test_filter_sql_parity.py — Phase 2 parity tests.

Phase 2 pushes the cheap, index-friendly filters into the SQL WHERE clause via
BaseFilter.apply_to_query(). This must never change *which* caches are returned
compared with the pure-Python matches() pass.

Every test here asserts:

    apply_filters(session, fs)  ==  [c for c in all_rows if fs.matches(c)]

over the *same* seeded data, including NULL edge cases (unknown difficulty,
missing country/owner, etc.) and AND/OR composition — the cases where a careless
SQL translation would silently diverge from the Python semantics.

The seed data lives in its own module-scoped database so the NULL rows here do
not leak into test_filters.py (whose assertions would choke on None values).
"""

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.filters.engine import (
    FilterSet, apply_filters,
    CacheTypeFilter, ContainerFilter, DifficultyFilter, TerrainFilter,
    FoundFilter, NotFoundFilter, AvailableFilter, ArchivedFilter,
    AvailabilityFilter, CountryFilter, StateFilter, CountyFilter,
    PlacedByFilter, OwnerFilter, PremiumFilter, NonPremiumFilter,
)


# ── Seed: a deliberately diverse set, including NULL edge cases ────────────────

@pytest.fixture(scope="module", autouse=True)
def seed_parity_data(tmp_db):
    caches = [
        # Fully-populated traditional, available, found
        Cache(gc_code="GCP0001", name="Alpha", cache_type="Traditional Cache",
              container="Small", latitude=55.0, longitude=12.0,
              difficulty=1.5, terrain=2.0, placed_by="Alice", owner_name="Alice",
              country="Denmark", state="Zealand", county="Copenhagen",
              available=True, archived=False, found=True, premium_only=False),
        # Mystery, premium, not found, unavailable (disabled, not archived)
        Cache(gc_code="GCP0002", name="Beta", cache_type="Unknown Cache",
              container="Micro", latitude=55.1, longitude=12.1,
              difficulty=5.0, terrain=4.0, placed_by="Bob", owner_name="Bobby",
              country="Denmark", state="Zealand", county="Roskilde",
              available=False, archived=False, found=False, premium_only=True),
        # Multi, archived
        Cache(gc_code="GCP0003", name="Gamma", cache_type="Multi-cache",
              container="Regular", latitude=56.0, longitude=10.0,
              difficulty=3.0, terrain=3.0, placed_by="Alice", owner_name="Alice",
              country="Germany", state="Berlin", county="Mitte",
              available=True, archived=True, found=False, premium_only=False),
        # NULL difficulty + NULL terrain (must pass D/T range filters by default)
        Cache(gc_code="GCP0004", name="Delta", cache_type="Traditional Cache",
              container="Large", latitude=57.0, longitude=9.0,
              difficulty=None, terrain=None, placed_by="Carol", owner_name=None,
              country="Denmark", state=None, county=None,
              available=True, archived=False, found=False, premium_only=False),
        # NULL container + NULL country + NULL placed_by
        Cache(gc_code="GCP0005", name="Epsilon", cache_type="Letterbox Hybrid",
              container=None, latitude=52.0, longitude=13.0,
              difficulty=2.0, terrain=1.0, placed_by=None, owner_name="Dave",
              country=None, state=None, county=None,
              available=True, archived=False, found=True, premium_only=False),
        # Another mystery, different owner casing for case-insensitive checks
        Cache(gc_code="GCP0006", name="Zeta", cache_type="Unknown Cache",
              container="Micro", latitude=55.4, longitude=10.3,
              difficulty=4.5, terrain=2.5, placed_by="ALICE", owner_name="alice",
              country="denmark", state="ZEALAND", county="ODENSE",
              available=True, archived=False, found=False, premium_only=True),
    ]
    with get_session() as s:
        for c in caches:
            s.add(c)


# ── Parity helper ─────────────────────────────────────────────────────────────

def assert_parity(fs: FilterSet | None):
    """SQL push-down result must equal the pure-Python matches() result."""
    with get_session() as s:
        sql_codes = {c.gc_code for c in apply_filters(s, fs)}
        all_rows = s.query(Cache).all()
        py_codes = {
            c.gc_code for c in all_rows
            if (fs.matches(c) if fs is not None else True)
        }
    assert sql_codes == py_codes, (
        f"SQL push-down diverged from Python matches():\n"
        f"  only in SQL:    {sql_codes - py_codes}\n"
        f"  only in Python: {py_codes - sql_codes}"
    )


# ── Single-filter parity (each pushed filter) ─────────────────────────────────

def test_parity_no_filter():
    assert_parity(None)
    assert_parity(FilterSet())  # empty AND → everything


def test_parity_cache_type():
    assert_parity(FilterSet().add(CacheTypeFilter(["Traditional Cache"])))
    assert_parity(FilterSet().add(CacheTypeFilter(["Unknown Cache", "Multi-cache"])))
    assert_parity(FilterSet().add(CacheTypeFilter([])))  # empty list → nothing


def test_parity_container():
    assert_parity(FilterSet().add(ContainerFilter(["Micro"])))
    assert_parity(FilterSet().add(ContainerFilter(["Small", "Large"])))


def test_parity_difficulty_including_null():
    # NULL-difficulty cache (GCP0004) must pass by default in both paths.
    assert_parity(FilterSet().add(DifficultyFilter(min_difficulty=1.0, max_difficulty=2.0)))
    assert_parity(FilterSet().add(DifficultyFilter(min_difficulty=4.0, max_difficulty=5.0)))


def test_parity_terrain_including_null():
    assert_parity(FilterSet().add(TerrainFilter(min_terrain=1.0, max_terrain=2.0)))
    assert_parity(FilterSet().add(TerrainFilter(min_terrain=3.0, max_terrain=5.0)))


def test_parity_found_and_not_found():
    assert_parity(FilterSet().add(FoundFilter()))
    assert_parity(FilterSet().add(NotFoundFilter()))


def test_parity_available_archived():
    assert_parity(FilterSet().add(AvailableFilter()))
    assert_parity(FilterSet().add(ArchivedFilter()))


@pytest.mark.parametrize("avail,unavail,archived", [
    (True, False, False),
    (True, True, False),
    (True, False, True),
    (False, True, True),
    (True, True, True),
    (False, False, False),  # nothing shown
])
def test_parity_availability_combinations(avail, unavail, archived):
    assert_parity(FilterSet().add(AvailabilityFilter(avail, unavail, archived)))


def test_parity_country_state_county_including_null():
    # Case-insensitive substring; NULL rows must be excluded identically.
    assert_parity(FilterSet().add(CountryFilter("denmark")))
    assert_parity(FilterSet().add(StateFilter("zealand")))
    assert_parity(FilterSet().add(CountyFilter("odense")))
    assert_parity(FilterSet().add(CountryFilter("")))  # empty → Python fallback


def test_parity_placed_by_owner_including_null():
    assert_parity(FilterSet().add(PlacedByFilter("alice")))
    assert_parity(FilterSet().add(OwnerFilter("alice")))
    assert_parity(FilterSet().add(OwnerFilter("")))  # empty → Python fallback


def test_parity_premium_non_premium():
    assert_parity(FilterSet().add(PremiumFilter()))
    assert_parity(FilterSet().add(NonPremiumFilter()))


# ── Composition parity (AND / OR / nesting) ───────────────────────────────────

def test_parity_top_level_and():
    fs = FilterSet(mode="AND")
    fs.add(CacheTypeFilter(["Traditional Cache"]))
    fs.add(AvailableFilter())
    fs.add(DifficultyFilter(max_difficulty=2.0))
    assert_parity(fs)


def test_parity_top_level_or_pushes_nothing():
    # Top-level OR must NOT be AND-ed into the WHERE clause — the whole set is
    # evaluated in Python. Parity proves the result still matches.
    fs = FilterSet(mode="OR")
    fs.add(CacheTypeFilter(["Multi-cache"]))   # GCP0003
    fs.add(FoundFilter())                       # GCP0001, GCP0005
    assert_parity(fs)


def test_parity_and_containing_or_subtree():
    # AND( Available, OR( Premium, Container=Large ) )
    inner = FilterSet(mode="OR")
    inner.add(PremiumFilter())
    inner.add(ContainerFilter(["Large"]))
    outer = FilterSet(mode="AND")
    outer.add(AvailableFilter())
    outer.add(inner)
    assert_parity(outer)


def test_parity_or_containing_and_subtree():
    # OR( AND( Traditional, Found ), Archived ) — root is OR so nothing is pushed.
    inner = FilterSet(mode="AND")
    inner.add(CacheTypeFilter(["Traditional Cache"]))
    inner.add(FoundFilter())
    outer = FilterSet(mode="OR")
    outer.add(inner)
    outer.add(ArchivedFilter())
    assert_parity(outer)


def test_parity_deeply_nested_all_and():
    # AND( AND( CacheType ), AND( Country, NonPremium ) ) — all-AND path, all pushed.
    a = FilterSet(mode="AND").add(CacheTypeFilter(["Traditional Cache", "Letterbox Hybrid"]))
    b = FilterSet(mode="AND").add(CountryFilter("denmark")).add(NonPremiumFilter())
    root = FilterSet(mode="AND").add(a).add(b)
    assert_parity(root)

"""tests/unit-tests/test_invert_filter.py — FilterSet(negate=True), the filter
dialog's global "Invert filter".

Covers that an inverted set returns exactly the complement, that it is never
pushed into SQL (AND-ing its filters would select the wrong caches), that a
nested inverted group keeps the rest of the filter pushable, and serialisation.
"""

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.filters.engine import (
    DifficultyFilter, FilterProfile, FilterSet, FoundFilter, NameFilter,
    _sql_pushdown_candidates, apply_filters, apply_filters_lightweight,
)


@pytest.fixture(scope="module", autouse=True)
def seed_invert_data(tmp_db):
    def cache(code, name, difficulty, found):
        return Cache(gc_code=code, name=name, cache_type="Traditional Cache",
                     latitude=47.0, longitude=8.0, difficulty=difficulty,
                     terrain=1.5, found=found)

    with get_session() as s:
        for c in (
            cache("GCI0001", "Alpha", 1.5, False),
            cache("GCI0002", "Beta", 3.0, False),
            cache("GCI0003", "Gamma", 1.5, True),
            cache("GCI0004", "Delta", 4.0, True),
        ):
            s.add(c)


ALL = {"GCI0001", "GCI0002", "GCI0003", "GCI0004"}


def _codes(results) -> set[str]:
    return {c.gc_code for c in results if c.gc_code.startswith("GCI")}


def _all_paths(fs: FilterSet) -> set[str]:
    """Run *fs* through every evaluation path and require they agree."""
    with get_session() as s:
        orm = _codes(apply_filters(s, fs))
        light = _codes(apply_filters_lightweight(s, fs))
        python = _codes(c for c in s.query(Cache).all() if fs.matches(c))
    assert orm == light == python, (orm, light, python)
    return orm


def _easy_not_found(negate: bool) -> FilterSet:
    return (FilterSet(negate=negate)
            .add(DifficultyFilter(max_difficulty=2.0))
            .add(FilterSet().add(NameFilter("a")).add(FilterSet(mode="OR")
                 .add(FoundFilter()).add(NameFilter("Alpha")))))


def test_invert_returns_complement():
    plain = _all_paths(_easy_not_found(negate=False))
    inverted = _all_paths(_easy_not_found(negate=True))
    assert plain == {"GCI0001", "GCI0003"}
    assert inverted == ALL - plain


def test_invert_or_set():
    fs = FilterSet(mode="OR", negate=True).add(FoundFilter()).add(NameFilter("Beta"))
    assert _all_paths(fs) == {"GCI0001"}


def test_empty_inverted_set_shows_everything():
    assert _all_paths(FilterSet(negate=True)) >= ALL


def test_nested_inverted_group():
    # Found AND NOT (difficulty <= 2): only the outer filter may be pushed.
    inner = FilterSet(negate=True).add(DifficultyFilter(max_difficulty=2.0))
    fs = FilterSet().add(FoundFilter()).add(inner)
    assert _all_paths(fs) == {"GCI0004"}
    assert [type(f) for f in _sql_pushdown_candidates(fs)] == [FoundFilter]


def test_inverted_set_is_not_pushed_down():
    fs = FilterSet(negate=True).add(FoundFilter())
    assert list(_sql_pushdown_candidates(fs)) == []


def test_serialisation_roundtrip(tmp_path):
    fs = _easy_not_found(negate=True)
    data = fs.to_dict()
    assert data["negate"] is True
    assert FilterSet.from_dict(data).negate is True

    FilterProfile("inv", fs).save(tmp_path)
    loaded = FilterProfile.load(tmp_path / "inv.json").filterset
    assert loaded.negate is True
    assert _all_paths(loaded) == _all_paths(fs)


def test_plain_set_omits_negate_key():
    # Profiles saved without inversion stay unchanged on disk.
    assert "negate" not in FilterSet().add(FoundFilter()).to_dict()
    assert FilterSet.from_dict({"mode": "AND", "filters": []}).negate is False

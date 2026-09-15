"""tests/unit-tests/test_text_match_filter.py — text filter operators.

The cache name / GC code / placed by / owner / country / state / county
filters take an operator (contains, equals, in_list, regex, …). Covers each
operator's semantics, SQL push-down vs Python matches() parity (NULL and
empty columns, LIKE wildcards in the text, non-ASCII case folding), the
lightweight query path, and saved-profile serialisation.
"""

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.filters.engine import (
    FILTER_REGISTRY, TEXT_OPS, FilterSet,
    CountryFilter, CountyFilter, GcCodeFilter, NameFilter, OwnerFilter,
    PlacedByFilter, StateFilter,
    apply_filters, apply_filters_lightweight,
)

TEXT_FILTER_CLASSES = [
    NameFilter, GcCodeFilter, PlacedByFilter, OwnerFilter,
    CountryFilter, StateFilter, CountyFilter,
]

ALL = {"GCT0001", "GCT0002", "GCT0003", "GCT0004", "GCT0005", "GCAGCT1"}


def _cache(gc_code, **kwargs):
    return Cache(gc_code=gc_code, cache_type="Traditional Cache",
                 latitude=47.0, longitude=8.0, **kwargs)


@pytest.fixture(scope="module", autouse=True)
def seed(tmp_db):
    caches = [
        _cache("GCT0001", name="Zürich Tour", placed_by="Alice & Bob", owner_name="Alice",
               country="Switzerland", state="Zürich", county=None),
        _cache("GCT0002", name="ZÜRICH NIGHT", placed_by="Bob", owner_name="alice",
               country="switzerland", state="ZÜRICH", county=""),
        _cache("GCT0003", name="100% Fun_Cache", placed_by=None, owner_name="Bob",
               country="Germany", state="Bayern", county="München"),
        _cache("GCT0004", name="1000 Fun Cache", placed_by="Carol", owner_name=None,
               country=None, state=None, county=None),
        _cache("GCT0005", name="Old Mill", placed_by="", owner_name="Dave",
               country="Denmark", state="Zealand", county="Roskilde"),
        _cache("GCAGCT1", name="Mill Creek", placed_by="eve", owner_name="Eve",
               country="Denmark", state="Region Midtjylland", county="Aarhus"),
    ]
    with get_session() as s:
        for c in caches:
            s.add(c)


def matching_codes(text_filter) -> set[str]:
    """Codes matched by *text_filter*, asserting the SQL push-down path, the
    lightweight path and pure-Python matches() all agree."""
    fs = FilterSet().add(text_filter)
    with get_session() as s:
        sql = {c.gc_code for c in apply_filters(s, fs)}
        light = {c.gc_code for c in apply_filters_lightweight(s, fs)}
        py = {c.gc_code for c in s.query(Cache).all() if fs.matches(c)}
    assert sql == py, f"{text_filter!r}: SQL {sorted(sql)} != Python {sorted(py)}"
    assert light == py, f"{text_filter!r}: lightweight {sorted(light)} != Python {sorted(py)}"
    return py


# ── Operator semantics ────────────────────────────────────────────────────────

@pytest.mark.parametrize("cls,op,text,expected", [
    # Unicode case folding: "ZÜRICH" must find "Zürich" (SQLite lower() can't)
    (NameFilter, "contains", "ZÜRICH", {"GCT0001", "GCT0002"}),
    (NameFilter, "not_contains", "zürich", {"GCT0003", "GCT0004", "GCT0005", "GCAGCT1"}),
    (NameFilter, "contains", "mill", {"GCT0005", "GCAGCT1"}),
    (NameFilter, "equals", "old mill", {"GCT0005"}),
    (NameFilter, "not_equals", "OLD MILL", ALL - {"GCT0005"}),
    # % and _ in the text are literal characters, not LIKE wildcards
    (NameFilter, "starts_with", "100%", {"GCT0003"}),
    (NameFilter, "ends_with", "_cache", {"GCT0003"}),
    (NameFilter, "in_list", "Old Mill; mill creek", {"GCT0005", "GCAGCT1"}),
    (NameFilter, "not_in_list", "old mill;zürich tour", ALL - {"GCT0005", "GCT0001"}),
    (NameFilter, "regex", r"^z.*(tour|night)$", {"GCT0001", "GCT0002"}),
    (NameFilter, "not_regex", r"\d", {"GCT0001", "GCT0002", "GCT0005", "GCAGCT1"}),
    # empty / not_empty: NULL and "" both count as empty
    (PlacedByFilter, "empty", "", {"GCT0003", "GCT0005"}),
    (PlacedByFilter, "not_empty", "", {"GCT0001", "GCT0002", "GCT0004", "GCAGCT1"}),
    (CountyFilter, "empty", "", {"GCT0001", "GCT0002", "GCT0004"}),
    # Negated operators keep NULL columns (GCT0004 has no owner)
    (OwnerFilter, "not_contains", "alice", {"GCT0003", "GCT0004", "GCT0005", "GCAGCT1"}),
    (OwnerFilter, "not_equals", "bob", ALL - {"GCT0003"}),
    (CountyFilter, "equals", "MÜNCHEN", {"GCT0003"}),
    (StateFilter, "in_list", "ZÜRICH; bayern", {"GCT0001", "GCT0002", "GCT0003"}),
    # GC code: "contains" with the GC prefix stays a prefix match (legacy)
    (GcCodeFilter, "contains", "GCT", ALL - {"GCAGCT1"}),
    (GcCodeFilter, "contains", "CT", ALL),
    (GcCodeFilter, "equals", "gct0001", {"GCT0001"}),
])
def test_operator_semantics(cls, op, text, expected):
    assert matching_codes(cls(text, op)) == expected


# ── SQL / lightweight / Python parity for every operator ─────────────────────

_PARITY_TEXTS = {
    NameFilter:     ["zürich", "ZÜRICH", "fun", "100%", "fun_c", "old mill",
                     "Old Mill; mill creek", "^z.*h", ""],
    GcCodeFilter:   ["GCT", "gct0001", "T000", "GCT0001; GCAGCT1"],
    PlacedByFilter: ["bob", "alice & bob", ""],
    OwnerFilter:    ["alice", "Alice; Bob"],
    CountryFilter:  ["switzerland", "SWITZ"],
    StateFilter:    ["zürich", "ZÜRICH; Bayern"],
    CountyFilter:   ["münchen", "MÜNCHEN", "rosk"],
}


@pytest.mark.parametrize("cls,op,text", [
    (cls, op, text)
    for cls, texts in _PARITY_TEXTS.items()
    for op in TEXT_OPS
    for text in texts
])
def test_parity(cls, op, text):
    matching_codes(cls(text, op))


# ── Edge cases ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("op", ["regex", "not_regex"])
def test_invalid_regex_matches_nothing(op):
    f = NameFilter("(unclosed", op)
    assert f.regex_error
    assert matching_codes(f) == set()


@pytest.mark.parametrize("f", [
    NameFilter("", "not_contains"),
    NameFilter("", "equals"),
    NameFilter(" ; ; ", "in_list"),
    NameFilter("", "regex"),
])
def test_no_text_matches_everything(f):
    assert matching_codes(f) == ALL


def test_non_ascii_text_is_only_pre_narrowed_in_sql():
    # SQLite's lower() doesn't fold "Ü", so non-ASCII needles are pushed as a
    # superset LIKE (sql_exact False) or, for negations, not pushed at all.
    assert NameFilter("zurich").sql_exact is True
    assert NameFilter("zürich").sql_exact is False
    with get_session() as s:
        q = s.query(Cache)
        assert NameFilter("zürich", "contains").apply_to_query(q) is not None
        assert NameFilter("zürich", "not_contains").apply_to_query(q) is None
        assert NameFilter("z.*h", "regex").apply_to_query(q) is None


def test_unknown_operator_rejected():
    with pytest.raises(ValueError):
        NameFilter("x", "like")


# ── Serialisation ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cls", TEXT_FILTER_CLASSES)
@pytest.mark.parametrize("op", TEXT_OPS)
def test_serialisation_roundtrip(cls, op):
    data = cls("Some Text; other", op).to_dict()
    assert data["op"] == op
    assert data["text"] == "Some Text; other"   # stored as entered, not lowered
    restored = FILTER_REGISTRY[data["filter_type"]].from_dict(data)
    assert type(restored) is cls
    assert restored.to_dict() == data


@pytest.mark.parametrize("cls", TEXT_FILTER_CLASSES)
def test_profile_without_op_loads_as_contains(cls):
    restored = cls.from_dict({"filter_type": cls.filter_type, "text": "abc"})
    assert (restored.op, restored.text) == ("contains", "abc")


def test_legacy_country_list_format_still_loads():
    restored = CountryFilter.from_dict({"filter_type": "country", "countries": ["Denmark"]})
    assert (restored.op, restored.text) == ("contains", "Denmark")

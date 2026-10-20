"""tests/unit-tests/test_macro_types.py — opensak.cache_types(),
cache_type(), waypoint_types() and waypoint_type() (GSAK's CacheType /
ChildType): read-only lookups, available without the write flag."""

import pytest

from opensak.macro import MacroError, MacroRuntime
from opensak.utils.constants import CACHE_TYPES, CHILD_WP_TYPES


class Host:
    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def end_macro(self):
        pass


def _run(source):
    out: list[str] = []
    MacroRuntime(Host(), output=out.append).run(source)  # type: ignore[arg-type]
    return out


def test_cache_types_list_every_type_with_its_gsak_letter():
    out = _run("for _, t in ipairs(opensak.cache_types()) do print(t.name, t.code) end")
    assert [line.split("\t")[0] for line in out] == CACHE_TYPES
    assert "Traditional Cache\tT" in out and "Unknown Cache\tU" in out
    assert "Hotel/POI\tnil" in out


@pytest.mark.parametrize("value, expected", [
    ("T", "Traditional Cache\tT"),
    ("u", "Unknown Cache\tU"),
    ("Traditional", "Traditional Cache\tT"),
    ("multi-cache", "Multi-cache\tM"),
    ("EARTHCACHE", "Earthcache\tR"),
    ("Benchmark", "Benchmark\tG"),
    ("Hotel/POI", "Hotel/POI\tnil"),
    ("Y", "nil\tnil"),
    ("Nope", "nil\tnil"),
])
def test_cache_type_converts_letters_and_names(value, expected):
    assert _run(f'print(opensak.cache_type("{value}"))') == [expected]


def test_waypoint_types_list_the_dialog_types_with_a_prefix():
    out = _run("for _, t in ipairs(opensak.waypoint_types()) do print(t.prefix, t.name) end")
    assert [line.split("\t")[1] for line in out] == CHILD_WP_TYPES
    assert "PK\tParking Area" in out and "FN\tFinal Location" in out


@pytest.mark.parametrize("value, expected", [
    ("PK", "Parking Area\tPK"),
    ("s3", "Stage\tS1"),
    ("F", "Final Location\tFN"),
    ("parking area", "Parking Area\tPK"),
    ("Question to Answer", "Question to Answer\tQA"),
    ("XX", "nil\tnil"),
])
def test_waypoint_type_from_a_name_or_prefix(value, expected):
    assert _run(f'print(opensak.waypoint_type("{value}"))') == [expected]


@pytest.mark.parametrize("call", ["opensak.cache_type()", "opensak.cache_type(3)",
                                  'opensak.waypoint_type("  ")', "opensak.waypoint_type({})"])
def test_bad_arguments(call):
    with pytest.raises(MacroError, match="expects"):
        _run(call)


def test_available_without_the_write_flag(monkeypatch):
    from opensak.utils import flags
    monkeypatch.setattr(flags, "lua_macro_write", False)
    assert _run('print(#opensak.cache_types() > 0, opensak.waypoint_type("PK"))') == [
        "true\tParking Area\tPK"]

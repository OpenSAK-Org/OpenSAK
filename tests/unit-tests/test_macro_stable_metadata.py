"""tests/unit-tests/test_macro_stable_metadata.py — what a stable release
has of the macro API, for the OpenSAK-Macros CI (N6 of the v1.22.0 plan):
the newest stable API version and the beta-only functions.

The beta-only list follows the stable release defaults of the feature
flags, so the tests that depend on which flags those are patch
flags.BETA_ONLY.
"""

import pytest

import opensak.utils.flags as flags_module
from opensak.macro import runtime as rt
from opensak.macro.metadata import (
    BetaOnlyName,
    beta_only_names,
    find_beta_only,
    newest_stable_api,
)


# ── flags.BETA_ONLY ──────────────────────────────────────────────────────────

def test_beta_only_flags_are_those_off_in_a_stable_release():
    stable = flags_module._release_defaults(False)
    assert flags_module.BETA_ONLY == {k for k, on in stable.items() if not on}
    assert set(stable) == set(flags_module._RELEASE_DEFAULTS)
    # get_url() stays beta-only in v1.22.0 (D1)
    assert "lua-macro-net" in flags_module.BETA_ONLY
    assert "reverse-geocoding" not in flags_module.BETA_ONLY


def test_beta_builds_have_every_flag_on():
    assert all(flags_module._release_defaults(True).values())


# ── newest_stable_api() ──────────────────────────────────────────────────────

def test_newest_stable_api_is_the_newest_in_api_stable():
    assert newest_stable_api() == max(rt.API_STABLE)
    assert newest_stable_api() <= rt.API_VERSION


@pytest.mark.parametrize("stable, expected", [
    ({}, 0),
    ({4: "1.22.0"}, 4),
    ({4: "1.22.0", 5: "1.23.0"}, 5),
])
def test_newest_stable_api(monkeypatch, stable, expected):
    monkeypatch.setattr(rt, "API_STABLE", stable)
    assert newest_stable_api() == expected


# ── beta_only_names() ────────────────────────────────────────────────────────

def test_beta_only_names_follow_the_registry():
    names = beta_only_names()
    functions = {n.name: n.flag for n in names if n.kind == "function"}
    assert functions == {f"opensak.{f.name}": f.flag for f in rt.API
                         if f.flag in flags_module.BETA_ONLY}
    assert functions["opensak.get_url"] == "lua-macro-net"


def test_beta_only_names_ignore_this_builds_flags(monkeypatch):
    # A beta build, or features.json, turning a flag on changes nothing
    monkeypatch.setattr(flags_module, "lua_macro_net", True)
    before = beta_only_names()
    monkeypatch.setattr(flags_module, "lua_macro_net", False)
    assert beta_only_names() == before


def test_beta_only_names_after_the_flags_flip(monkeypatch):
    # As planned for v1.22.0: only lua-macro-net stays beta-only
    monkeypatch.setattr(flags_module, "BETA_ONLY", frozenset({"lua-macro-net"}))
    names = beta_only_names()
    assert {n.flag for n in names} == {"lua-macro-net"}
    assert {n.name for n in names} == {f"opensak.{f.name}" for f in rt.API if f.net}
    assert all(n.kind == "function" for n in names)


def test_custom_field_is_beta_only_with_companion_db(monkeypatch):
    monkeypatch.setattr(flags_module, "BETA_ONLY", frozenset({"companion-db"}))
    names = beta_only_names()
    assert BetaOnlyName("field", "custom", "companion-db") in names
    assert "opensak.set_companion_table" in {n.name for n in names}
    assert "opensak.get_url" not in {n.name for n in names}


def test_nothing_is_beta_only_without_beta_only_flags(monkeypatch):
    monkeypatch.setattr(flags_module, "BETA_ONLY", frozenset())
    assert beta_only_names() == ()
    assert find_beta_only('local body = opensak.get_url("https://example.com")') == []


# ── find_beta_only() ─────────────────────────────────────────────────────────

def test_find_beta_only(monkeypatch):
    monkeypatch.setattr(flags_module, "BETA_ONLY",
                        frozenset({"lua-macro-net", "companion-db"}))
    found = find_beta_only(
        "\ufeff-- Name: x\n"
        "-- Uses opensak.get_url in the header comment\n"
        'local ok, body = pcall(opensak.get_url, "https://example.com")\n'
        "local f = opensak.get_url_cached   -- another name\n"
        "if opensak.get_url then end\n"
        "print(c.custom.LFL, c['custom'])\n"
        "print(c.customer)\n")
    assert [(line, msg.split(" is ")[0]) for line, msg in found] == [
        (3, "opensak.get_url"),
        (5, "opensak.get_url"),
        (6, "cache field custom"),
    ]
    assert "lua-macro-net" in found[0][1]
    assert "not in stable releases" in found[0][1]

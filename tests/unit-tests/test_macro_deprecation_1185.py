"""tests/unit-tests/test_macro_deprecation_1185.py — deprecating and removing
macro API functions, parameters, cache fields and header keys (#1185).

Nothing in API 4 is deprecated, so the tests mark entries of the registries
as deprecated (or removed) with monkeypatch.
"""

import dataclasses
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from opensak.macro import FolderApproval, MacroError, MacroRuntime, api_docs, cache_data
from opensak.macro import metadata
from opensak.macro import runtime as rt
from opensak.macro.api_baseline import (
    check,
    load_baseline,
    removal_allowed,
    snapshot,
    to_json,
)
from opensak.macro.metadata import (
    HeaderKey,
    deprecated_names,
    find_deprecated,
    parse_header,
)
from opensak.macro.permissions import FolderPermission
from opensak.macro.runtime import API, API_STABLE, ApiFunction, Param, RemovedFunction

ROOT = Path(__file__).resolve().parents[2]


class _Host:
    """Just enough of a MacroHost for these macros."""

    def clear_filter(self):
        pass

    def cache_count(self):
        return 7

    def end_macro(self):
        pass

    def approve_folder(self, target, folder, write, macro=None):
        return FolderApproval.DENY


def _deprecate(monkeypatch, name: str, instead: str, version: int = 5) -> None:
    """Mark opensak.<name> as deprecated in API *version*."""
    api = tuple(dataclasses.replace(f, deprecated=(version, instead)) if f.name == name else f
                for f in rt.API)
    monkeypatch.setattr(rt, "API", api)


def _add(monkeypatch, *funcs: ApiFunction) -> None:
    monkeypatch.setattr(rt, "API", rt.API + funcs)


def _run(source: str, folder: Path | None = None, **kwargs) -> list[str]:
    out: list[str] = []
    permissions = [FolderPermission(str(folder), read=True, write=False)] if folder else []
    MacroRuntime(_Host(), output=out.append,  # type: ignore[arg-type]
                 folder_permissions=permissions).run(
        source, base_dir=folder, **kwargs)
    return out


# ── Functions ────────────────────────────────────────────────────────────────

def test_deprecated_function_warns_once_with_the_line_and_still_works(monkeypatch):
    _deprecate(monkeypatch, "count", "opensak.new_count()")
    out = _run("local n = 0\n"
               "for i = 1, 3 do n = n + opensak.count() end\n"
               "print(opensak.count(), n)")
    assert out == [
        "opensak.count is deprecated since API 5 and will be removed in a later "
        "version; use opensak.new_count() instead (line 2)",
        "7\t21",
    ]


def test_deprecation_warning_comes_again_in_the_next_run(monkeypatch):
    _deprecate(monkeypatch, "count", "opensak.new_count()")
    runtime = MacroRuntime(_Host(), output=(out := []).append)  # type: ignore[arg-type]
    runtime.run("opensak.count()")
    runtime.run("opensak.count()")
    assert len(out) == 2 and all("opensak.count is deprecated" in line for line in out)


def test_deprecated_function_in_a_namespace(monkeypatch):
    _deprecate(monkeypatch, "html.escape", "opensak.text.escape()")
    out = _run('\nprint(opensak.html.escape("<b>"))')
    assert out[0].startswith("opensak.html.escape is deprecated since API 5")
    assert out[0].endswith("(line 2)")
    assert out[1] == "&lt;b&gt;"


def test_errors_of_a_deprecated_function_still_name_the_macro_line(monkeypatch):
    _deprecate(monkeypatch, "html.escape", "opensak.text.escape()")
    with pytest.raises(MacroError, match=r"^macro:3: "):
        _run("\n\nopensak.html.escape({})")


def test_deprecated_lua_function_warns_from_a_lua_wrapper(monkeypatch):
    # Like opensak.transaction: written in Lua, so not wrapped in Python.
    _add(monkeypatch, ApiFunction(
        name="double", description="", example="", since=4,
        bind=lambda runtime, lua: lua.eval(
            'function(x) if not x then error("double needs x", 2) end return x * 2 end'),
        deprecated=(5, "opensak.twice()"),
    ))
    out = _run("\nprint(opensak.double(4))\nprint(opensak.double(5))")
    assert out == [
        "opensak.double is deprecated since API 5 and will be removed in a later "
        "version; use opensak.twice() instead (line 2)",
        "8", "10",
    ]
    # error(…, 2) inside it still points at the macro, not at the wrapper
    with pytest.raises(MacroError, match=r"^macro:3: double needs x"):
        _run("\n\nopensak.double()")


def test_macro_started_with_run_macro_gets_its_own_warning(monkeypatch, tmp_path):
    _deprecate(monkeypatch, "count", "opensak.new_count()")
    (tmp_path / "child.lua").write_text("\n\nopensak.count()", encoding="utf-8")
    out = _run('opensak.count()\nopensak.run_macro("child")\nopensak.run_macro("child")',
               folder=tmp_path, macro_path=tmp_path / "main.lua")
    assert len(out) == 2
    assert out[0].endswith("(line 1)")
    assert out[1].endswith("(child.lua, line 3)")


def test_deprecated_parameter_warns_where_the_function_maps_it(monkeypatch):
    def bind(runtime, lua):
        def echo(new=None, old=None):
            if old is not None:
                runtime._deprecated_param("echo", "old")
                new = old
            return new
        return echo

    _add(monkeypatch, ApiFunction(
        name="echo", description="", example="", since=4, bind=bind,
        params=(Param("new", "string", "", optional=True),
                Param("old", "string", "", optional=True, deprecated=(5, "new"))),
    ))
    out = _run('print(opensak.echo("a"))\nprint(opensak.echo(nil, "b"))\n'
               'print(opensak.echo(nil, "c"))')
    assert out == [
        "a",
        "opensak.echo parameter old is deprecated since API 5 and will be removed "
        "in a later version; use new instead (line 2)",
        "b", "c",
    ]


# ── Removed functions ────────────────────────────────────────────────────────

@pytest.fixture
def removed(monkeypatch):
    monkeypatch.setattr(rt, "REMOVED", (
        RemovedFunction("old_count", 6, "opensak.count()"),
        RemovedFunction("html.old_escape", 6, "opensak.html.escape()"),
    ))


def test_removed_function_is_still_nil(removed):
    out = _run("print(opensak.old_count, opensak.html.old_escape, opensak.whatever)\n"
               "if opensak.old_count then print('yes') else print('no') end")
    assert out == ["nil\tnil\tnil", "no"]


def test_calling_a_removed_function_gives_a_clear_error(removed):
    with pytest.raises(MacroError) as exc:
        _run("print(1)\nopensak.old_count()")
    assert str(exc.value).startswith(
        "macro:2: opensak.old_count was removed in API 6; use opensak.count() instead")


def test_calling_a_removed_function_in_a_namespace(removed):
    with pytest.raises(MacroError, match=r"opensak\.html\.old_escape was removed in API 6; "
                                         r"use opensak\.html\.escape\(\) instead"):
        _run('opensak.html.old_escape("x")')


def test_other_nil_calls_keep_luas_message(removed):
    with pytest.raises(MacroError, match=r"attempt to call a nil value \(field 'nope'\)"):
        _run("opensak.nope()")


# ── Header keys ──────────────────────────────────────────────────────────────

@pytest.fixture
def old_key(monkeypatch):
    """Requires-API deprecated in favour of a made-up Needs-API."""
    keys = tuple(dataclasses.replace(k, deprecated=(5, "Needs-API"))
                 if k.name == "Requires-API" else k for k in metadata.HEADER_KEYS)
    monkeypatch.setattr(metadata, "HEADER_KEYS", keys)


def test_header_info_lists_deprecated_keys(old_key):
    info = parse_header("-- requires-api: 3\nprint(1)", Path("m.lua"))
    assert [k.name for k in info.deprecated_keys] == ["Requires-API"]
    assert info.deprecation_lines == (
        "header key Requires-API is deprecated since API 5 and will be removed in "
        "a later version; use Needs-API instead",)
    assert parse_header("-- Name: x\nprint(1)", Path("m.lua")).deprecated_keys == ()


def test_run_prints_deprecated_header_keys_at_the_start(old_key, tmp_path):
    (tmp_path / "child.lua").write_text("-- Requires-API: 3\nprint('child')", encoding="utf-8")
    out = _run('-- Requires-API: 3\nprint("main")\nopensak.run_macro("child")',
               folder=tmp_path)
    assert out == [
        "header key Requires-API is deprecated since API 5 and will be removed in "
        "a later version; use Needs-API instead",
        "main",
        "child.lua: header key Requires-API is deprecated since API 5 and will be "
        "removed in a later version; use Needs-API instead",
        "child",
    ]


def test_macro_manager_marks_deprecated_header_keys(old_key, qtbot, tmp_path, monkeypatch):
    from opensak.gui.dialogs import macro_manager as mm
    from opensak.lang import load_language

    load_language("en")
    folder = tmp_path / "macros"
    folder.mkdir()
    monkeypatch.setattr("opensak.config.get_macros_dir", lambda: folder)
    (folder / "old.lua").write_text("-- Requires-API: 3\nprint(1)\n", encoding="utf-8")
    (folder / "new.lua").write_text("-- Name: new\nprint(1)\n", encoding="utf-8")
    d = mm.MacroManagerDialog(host=MagicMock())
    qtbot.addWidget(d)
    items = {d._tree.topLevelItem(i).text(mm.COL_NAME): d._tree.topLevelItem(i)
             for i in range(d._tree.topLevelItemCount())}
    old, new = items["old"], items["new"]
    assert not old.icon(mm.COL_API).isNull()
    assert "use Needs-API instead" in old.toolTip(mm.COL_API)
    assert new.icon(mm.COL_API).isNull()


# ── Docs and editor files ────────────────────────────────────────────────────

def test_docs_show_deprecated_functions(monkeypatch):
    _deprecate(monkeypatch, "count", "opensak.new_count()")
    monkeypatch.setattr(api_docs, "API", rt.API)
    md = api_docs.render_api_markdown()
    assert "| [`opensak.count`](#opensakcount) | 1, deprecated in 5 |" in md
    section = md.split("### opensak.count\n", 1)[1]
    assert section.lstrip().startswith(
        "**Deprecated in API version 5: use `opensak.new_count()`.**")
    stub = api_docs.render_lua_stub()
    after = stub.split("---@deprecated Use opensak.new_count()\n", 1)[1]
    assert after.split("\nfunction ", 1)[1].startswith("opensak.count() end")
    npp = api_docs.render_npp_autocomplete()
    assert 'descr="(deprecated) Use opensak.new_count() instead.&#x0a;' in npp


def test_docs_show_deprecated_parameters_fields_and_header_keys(monkeypatch, old_key):
    api = tuple(
        dataclasses.replace(f, params=(dataclasses.replace(
            f.params[0], deprecated=(5, "gc")), *f.params[1:])) if f.name == "cache" else f
        for f in rt.API)
    monkeypatch.setattr(api_docs, "API", api)
    fields = tuple(dataclasses.replace(f, deprecated=(5, "available"))
                   if f.name == "archived" else f for f in cache_data.CACHE_FIELDS)
    monkeypatch.setattr(api_docs, "CACHE_FIELDS", fields)
    monkeypatch.setattr(api_docs, "HEADER_KEYS", metadata.HEADER_KEYS)
    md = api_docs.render_api_markdown()
    assert "- `code` (`string`) — **Deprecated in API version 5: use `gc`.** GC code" in md
    assert "| `archived` | `boolean` | **Deprecated in API version 5: use `available`.** " in md
    assert "- `Requires-API` — Deprecated in API version 5: use `Needs-API`." in md
    stub = api_docs.render_lua_stub()
    assert "---@param code string (deprecated, use gc) GC code" in stub
    assert "---@field archived boolean (deprecated, use available) Archived." in stub


def test_nothing_is_deprecated_in_api_4():
    # #1185 only adds the mechanism.
    assert deprecated_names() == ()
    assert rt.REMOVED == ()


# ── Exported names for the OpenSAK-Macros CI ─────────────────────────────────

def test_deprecated_names_and_find_deprecated(monkeypatch, old_key):
    _deprecate(monkeypatch, "html.escape", "opensak.text.escape()")
    fields = tuple(dataclasses.replace(f, deprecated=(5, "available"))
                   if f.name == "archived" else f for f in cache_data.CACHE_FIELDS)
    monkeypatch.setattr(cache_data, "CACHE_FIELDS", fields)
    names = {(n.kind, n.name) for n in deprecated_names()}
    assert names == {("function", "opensak.html.escape"), ("field", "archived"),
                     ("header", "Requires-API")}
    found = find_deprecated(
        "-- Name: x\n"
        "-- Requires-API: 3\n"
        "local s = opensak.html.escape(c.name)\n"
        "local t = opensak.html.escaped   -- another name\n"
        "if c.archived or c['archived'] then end\n"
        "-- opensak.html.escape in a comment\n"
        "print(c.archived_date)\n")
    assert [(line, msg.split(" is ")[0]) for line, msg in found] == [
        (2, "header key Requires-API"),
        (3, "opensak.html.escape"),
        (5, "cache field archived"),
    ]


# ── API baseline ─────────────────────────────────────────────────────────────

def test_api_matches_the_committed_baseline():
    """Removing or renaming anything of the released macro API fails here.
    Deprecate it first (deprecation.py); see api_baseline.py."""
    baseline = load_baseline(ROOT)
    assert check(baseline) == []
    # The baseline was taken at (or after) the last stable API version:
    # after adding one to API_STABLE, run scripts/update_macro_api_baseline.py
    assert baseline["api_version"] >= max(API_STABLE)


def test_snapshot_records_deprecations_and_flags(monkeypatch):
    _deprecate(monkeypatch, "count", "opensak.new_count()")
    data = snapshot()
    assert data["functions"]["count"] == {"deprecated": 5, "params": {}}
    assert data["functions"]["transaction"]["flag"] == "lua-macro-write"
    assert data["functions"]["set_companion_table"]["flag"] == "companion-db"
    assert data["cache_fields"]["custom"] == {"flag": "companion-db"}
    assert set(data["header_keys"]) == {k.name for k in metadata.HEADER_KEYS}
    assert to_json(data) == to_json(snapshot())


STABLE = {4: "1.22.0", 5: "1.23.0"}


@pytest.mark.parametrize("deprecated, stable, allowed", [
    (None, STABLE, False),
    (4, {4: "1.22.0"}, False),          # no later stable release yet
    (4, STABLE, True),
    (5, STABLE, False),
    (6, STABLE, False),                 # not in a stable release
    (4, {4: "1.22.0", 5: "1.22.0"}, False),
])
def test_removal_allowed(deprecated, stable, allowed):
    assert removal_allowed(deprecated, stable) is allowed


def _api(**functions) -> dict:
    return {"api_version": 5, "functions": functions, "cache_fields": {}, "header_keys": {}}


def test_check_accepts_additions_and_deprecations():
    old = _api(a={"params": {"x": {}}})
    new = _api(a={"deprecated": 5, "params": {"x": {"deprecated": 5}, "y": {}}}, b={"params": {}})
    assert check(old, new, STABLE, set()) == []


def test_check_fails_on_removal_or_rename_without_deprecation():
    old = _api(a={"params": {"x": {}}}, b={"params": {}})
    new = _api(a={"params": {"x2": {}}}, c={"params": {}})
    assert check(old, new, STABLE, set()) == [
        "opensak.a parameter x was removed or renamed, but it was not deprecated first",
        "opensak.b was removed or renamed, but it was not deprecated first",
    ]


def test_check_fails_on_removal_before_the_deprecation_period_is_over():
    old = _api(a={"deprecated": 5, "params": {}})
    assert check(old, _api(), STABLE, {"a"}) == [
        "opensak.a was removed or renamed, but it was deprecated in API 5, which is "
        "not followed by another stable release yet"]


def test_check_wants_removed_functions_in_REMOVED():
    old = _api(a={"deprecated": 4, "params": {}})
    assert check(old, _api(), STABLE, set()) == [
        "opensak.a was removed: add it to runtime.REMOVED"]
    assert check(old, _api(), STABLE, {"a"}) == []


def test_check_ignores_beta_only_entries_but_not_moving_behind_a_flag():
    old = _api(a={"flag": "companion-db", "params": {"x": {}}}, b={"params": {}})
    new = _api(b={"flag": "lua-macro-write", "params": {}})
    assert check(old, new, STABLE, set()) == [
        "opensak.b was removed or renamed, but it was not deprecated first"]


def test_check_cache_fields_and_header_keys():
    old = {"functions": {}, "cache_fields": {"a": {}, "b": {"deprecated": 4},
                                             "c": {"flag": "companion-db"}},
           "header_keys": {"Name": {}}}
    new = {"functions": {}, "cache_fields": {}, "header_keys": {}}
    assert check(old, new, STABLE, set()) == [
        "cache field a was removed or renamed, but it was not deprecated first",
        "header key Name was removed or renamed, but it was not deprecated first",
    ]


def test_header_key_is_documented_in_header_keys():
    assert HeaderKey("X").deprecated is None
    assert [k.name.lower() for k in metadata.HEADER_KEYS] == [
        *metadata._KEYS, *metadata._LIST_KEYS]


def test_api_entries_have_no_deprecation_by_default():
    assert all(f.deprecated is None for f in API)

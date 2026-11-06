"""tests/unit-tests/test_macro_log_vars_1276.py — opensak.log_vars() (#1276).

log_vars() writes the variables in scope at the calling line to the log,
one `name = value` line each: locals, varargs, upvalues and, on request,
the globals the macro added or changed. It reads them with the debug
library the macro has no access to, so it must only ever read the macro's
own functions — never the sandbox's, whose locals include e.g. the
unrestricted load().
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from opensak.macro import FolderPermission, MacroError, MacroRuntime
from opensak.macro.inspect_text import MAX_INSPECT_TEXT, TRUNCATED


def _run(source: str, folder: Path | None = None, name: str = "macro") -> tuple[list[str], list[str]]:
    """(print output, log lines) of running *source*."""
    out: list[str] = []
    log: list[str] = []
    permissions = [FolderPermission(str(folder), read=True, write=False)] if folder else None
    MacroRuntime(MagicMock(), output=out.append, log=log.append,
                 folder_permissions=permissions).run(
        source, chunk_name=name, base_dir=folder)
    return out, log


def _vars(source: str, **kwargs) -> list[str]:
    """The lines of the one log_vars() call in *source*."""
    _, log = _run(source, **kwargs)
    assert len(log) == 1
    return log[0].split("\n")


def _fails(source: str) -> str:
    with pytest.raises(MacroError) as info:
        _run(source)
    return str(info.value)


# ── locals ───────────────────────────────────────────────────────────────────


def test_locals_one_line_each_in_declaration_order():
    assert _vars('local n, s = 12, "Hut"\n'
                 'local t = { "abc", "def", "ghi" }\n'
                 'local none\n'
                 'opensak.log_vars()') == [
        "-- variables at macro:4",
        "n = 12",
        's = "Hut"',
        't = { "abc", "def", "ghi" }',
        "none = nil",
    ]


def test_goes_to_the_log_not_print():
    out, log = _run("local x = 1\nopensak.log_vars()")
    assert out == []
    assert log == ["-- variables at macro:2\nx = 1"]


def test_nothing_in_scope():
    assert _vars("opensak.log_vars()") == ["-- variables at macro:1: none"]


def test_only_locals_declared_so_far():
    assert _vars("local a = 1\nopensak.log_vars()\nlocal b = 2") == [
        "-- variables at macro:2", "a = 1"]


def test_inner_local_hides_outer_one():
    assert _vars("local x = 1\ndo\n  local x = 2\n  opensak.log_vars()\nend") == [
        "-- variables at macro:4", "x = 2"]


def test_loop_variables_without_lua_internals():
    # The for loop's hidden "(for state)" locals are not shown.
    assert _vars("for i, v in ipairs({ 'a' }) do\n  opensak.log_vars()\nend") == [
        "-- variables at macro:2", "i = 1", 'v = "a"']


def test_function_parameters_and_varargs():
    assert _vars("local function f(a, ...)\n  opensak.log_vars()\nend\n"
                 'f(1, "x", nil)') == [
        "-- variables at macro:2", "a = 1", '... = "x", nil']


def test_upvalues_after_locals_and_hidden_by_them():
    assert _vars("local count, name = 3, 'outer'\n"
                 "local function f()\n"
                 "  local name = 'inner'\n"
                 "  local total = count + 1\n"
                 "  opensak.log_vars()\n"
                 "end\n"
                 "f()") == [
        "-- variables at macro:5",
        'name = "inner"',
        "total = 4",
        "-- upvalues",
        "count = 3",
    ]


def test_inside_a_coroutine_of_the_macro():
    assert _vars("coroutine.wrap(function()\n  local inside = true\n"
                 "  opensak.log_vars()\nend)()") == [
        "-- variables at macro:3", "inside = true"]


# ── values ───────────────────────────────────────────────────────────────────


def test_tables_on_one_line_to_depth_two_by_default():
    assert _vars("local t = { a = { b = { c = 1 } }, 1 }\nopensak.log_vars()")[1] \
        == "t = { 1, a = { b = {...} } }"


def test_depth_option():
    assert _vars("local t = { a = { b = { c = 1 } } }\n"
                 "opensak.log_vars({ depth = 3 })")[1] == "t = { a = { b = { c = 1 } } }"
    assert _vars("local t = { a = { b = 1 } }\n"
                 "opensak.log_vars({ depth = 1 })")[1] == "t = { a = {...} }"


def test_cycles_and_functions():
    assert _vars("local t = {}\nt.self = t\nlocal f = print\nopensak.log_vars()")[1:] == [
        "t = { self = <cycle> }", "f = <function>"]


def test_long_values_are_cut():
    lines = _vars(f'local s = string.rep("x", {MAX_INSPECT_TEXT + 10})\n'
                  "local after = 1\nopensak.log_vars()")
    assert lines[-1] == TRUNCATED.strip()
    assert not any(line.startswith("after") for line in lines)
    assert sum(map(len, lines)) < MAX_INSPECT_TEXT + 100


# ── globals ──────────────────────────────────────────────────────────────────


def test_globals_only_on_request():
    assert _vars("total = 3\nopensak.log_vars()") == ["-- variables at macro:2: none"]


def test_globals_the_macro_added_or_changed_sorted():
    assert _vars("wp10 = 1\nwp2 = { 'a' }\nprint = print\n"
                 "tostring = function() end\n_G[1] = 'one'\n"
                 "opensak.log_vars({ globals = true })") == [
        "-- variables at macro:6",
        "-- globals",
        '[1] = "one"',
        "tostring = <function>",
        'wp2 = { "a" }',
        "wp10 = 1",
    ]


def test_globals_in_a_function_without_env_upvalue():
    # f uses no global name except opensak, so the lookup must still find
    # the macro's globals.
    assert _vars("seen = 1\nlocal log = opensak.log_vars\n"
                 "local function f() log({ globals = true }) end\nf()") == [
        "-- variables at macro:3", "-- upvalues", "log = <function>",
        "-- globals", "seen = 1"]


def test_local_env_is_used_for_globals():
    assert _vars("local log = opensak.log_vars\n"
                 "local _ENV = { mine = 1 }\nlog({ globals = true })") == [
        "-- variables at macro:3", "log = <function>", "-- globals", "mine = 1"]


def test_called_macro_shows_its_own_globals(tmp_path):
    (tmp_path / "called.lua").write_text(
        "local a = ...\nown = 2\nopensak.log_vars({ globals = true })\n", encoding="utf-8")
    _, log = _run('caller_global = 1\nopensak.run_macro("called.lua", "arg")',
                  folder=tmp_path, name="main.lua")
    assert log[-1].split("\n") == [
        "-- variables at called.lua:3", 'a = "arg"', '... = "arg"', "-- globals", "own = 2"]


# ── options and errors ───────────────────────────────────────────────────────


@pytest.mark.parametrize("options, message", [
    ("{ globals = 1 }", "globals must be true or false, got 1"),
    ("{ depth = 0 }", "depth must be a number from 1"),
    ("{ colour = 1 }", "unknown option(s)"),
    ("5", "options must be a table"),
])
def test_bad_options(options, message):
    error = _fails(f"\n\nopensak.log_vars({options})")
    assert error.startswith("macro:3: ")
    assert message in error


@pytest.mark.parametrize("call", [
    "error(select(2, pcall(opensak.log_vars)), 0)",
    # The handler is called by the sandbox's own xpcall, written in Lua.
    "error(select(2, xpcall(error, opensak.log_vars)), 0)",
    "coroutine.wrap(opensak.log_vars)()",
    "table.sort({ 2, 1 }, opensak.log_vars)",
    'string.gsub("ab", ".", opensak.log_vars)',
])
def test_never_reads_functions_that_are_not_the_macros(call):
    # The function calling log_vars() here is pcall, the sandbox or a library
    # function: its variables must not be shown, and no line of the sandbox
    # is named.
    out, log = _run(f"print(pcall(function() {call} end))")
    assert log == []
    assert out[0].startswith("false\t")
    assert "must be called from the macro's own code" in out[0]
    assert "<python>" not in out[0]


def test_xpcall_handler_of_the_macro_shows_its_own_variables():
    out, log = _run("print(xpcall(error, function(message)\n"
                    "  opensak.log_vars()\nend, 'oops'))")
    assert out == ["false"]
    assert log == ['-- variables at macro:2\nmessage = "oops"']

"""tests/unit-tests/test_macro_inspect_1275.py — opensak.inspect() and
opensak.log() with tables (#1275).

print() and opensak.log() used to show a table only as its address. These
tests pin down the text inspect() writes (inspect_text.py) and that log()
writes tables out the same way.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from opensak.macro import MacroError, MacroRuntime
from opensak.macro.inspect_text import MAX_INSPECT_TEXT, TRUNCATED, inspect
from opensak.macro.runtime import _lua_describe


def _run(source: str) -> list[str]:
    out: list[str] = []
    MacroRuntime(MagicMock(), output=out.append).run(source)
    return out


def _show(expr: str, options: str = "") -> str:
    args = f"{expr}, {options}" if options else expr
    return "\n".join(_run(f"print(opensak.inspect({args}))"))


# ── scalars ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("expr, shown", [
    ("nil", "nil"), ("true", "true"), ("false", "false"),
    ("3", "3"), ("-7", "-7"), ("1.5", "1.5"), ("2.0", "2.0"), ("1e100", "1e+100"),
    ("math.huge", "inf"), ("-math.huge", "-inf"),
    ('"Zürich"', '"Zürich"'), ('""', '""'), ('"1"', '"1"'),
    (r'"a\"b\\c"', r'"a\"b\\c"'), (r'"x\ny\tz\r"', r'"x\ny\tz\r"'),
    (r'"\0\1\127"', r'"\0\1\127"'),
])
def test_scalars(expr, shown):
    assert _show(expr) == shown


def test_bytes_that_are_not_utf8_are_escaped():
    # lupa hands a Lua string that is not UTF-8 to Python as bytes.
    assert inspect(b"a\xff", lambda v: None) == r'"a\255"'


def test_functions():
    # lupa hands a coroutine to Python as a function object, so it shows as one too.
    assert _show("{ print, opensak.count, function() end, coroutine.create(function() end) }") \
        == "{ <function>, <function>, <function>, <function> }"


def test_true_and_one_are_different_keys():
    assert _show('{ "one", [true] = "yes", [false] = "no" }') \
        == '{ "one", [false] = "no", [true] = "yes" }'


# ── tables ───────────────────────────────────────────────────────────────────


def test_empty_table():
    assert _show("{}") == "{}"


def test_list_part_first_then_sorted_keys():
    assert _show('{ 10, 20, z = 1, a = 2, [5] = "five", [true] = 0, [1.5] = "x" }') \
        == '{ 10, 20, [1.5] = "x", [5] = "five", a = 2, z = 1, [true] = 0 }'


def test_list_with_gap_shows_keys_after_the_gap():
    assert _show("{ [1] = 'a', [2] = 'b', [4] = 'd' }") == '{ "a", "b", [4] = "d" }'


def test_string_keys_sort_naturally():
    assert _show("{ wp10 = 1, wp2 = 2, wp1 = 3 }") == "{ wp1 = 3, wp2 = 2, wp10 = 1 }"


def test_keys_that_are_not_names_are_bracketed():
    assert _show('{ ["end"] = 1, ["a b"] = 2, ["1x"] = 3, _ok = 4 }') \
        == '{ ["1x"] = 3, _ok = 4, ["a b"] = 2, ["end"] = 1 }'


def test_long_table_has_one_entry_per_line():
    assert _run("""
        local t = { name = "A cache with a rather long name", code = "GC12345",
                    tags = { "a", "b" } }
        print(opensak.inspect(t))
    """) == ['{\n'
             '  code = "GC12345",\n'
             '  name = "A cache with a rather long name",\n'
             '  tags = { "a", "b" }\n'
             '}']


def test_nested_long_tables_are_indented():
    assert _show('{ outer = { a = string.rep("x", 40), b = string.rep("y", 40) } }') == (
        '{\n'
        '  outer = {\n'
        f'    a = "{"x" * 40}",\n'
        f'    b = "{"y" * 40}"\n'
        '  }\n'
        '}'
    )


@pytest.mark.parametrize("indent, shown", [
    (0, '{ outer = { a = "%s", b = "%s" } }' % ("x" * 40, "y" * 40)),
    (4, '{\n    outer = {\n        a = "%s",\n        b = "%s"\n    }\n}' % ("x" * 40, "y" * 40)),
])
def test_indent_option(indent, shown):
    assert _show('{ outer = { a = string.rep("x", 40), b = string.rep("y", 40) } }',
                 f"{{ indent = {indent} }}") == shown


def test_cycle():
    assert _run("""
        local t = { name = "loop" }
        t.self = t
        t.child = { parent = t }
        print(opensak.inspect(t))
    """) == ['{ child = { parent = <cycle> }, name = "loop", self = <cycle> }']


def test_same_table_twice_is_not_a_cycle():
    assert _run("""
        local shared = { 1 }
        print(opensak.inspect({ a = shared, b = shared }))
    """) == ["{ a = { 1 }, b = { 1 } }"]


@pytest.mark.parametrize("depth, shown", [
    (1, "{ a = {...} }"),
    (2, "{ a = { b = {...} } }"),
    (3, "{ a = { b = { c = 1 } } }"),
])
def test_depth_option(depth, shown):
    assert _show("{ a = { b = { c = 1 } } }", f"{{ depth = {depth} }}") == shown


def test_default_depth_is_ten():
    assert _run("""
        local t = { 1 }
        for _ = 1, 11 do t = { t } end
        print(opensak.inspect(t, { indent = 0 }))
    """) == ["{ " * 10 + "{...}" + " }" * 10]


def test_metatables_are_ignored():
    assert _run("""
        local t = setmetatable({ x = 1 }, {
            __index = function() return "dflt" end,
            __tostring = function() return "custom" end,
            __pairs = function() error("not used") end })
        print(opensak.inspect(t))
    """) == ["{ x = 1 }"]


def test_table_keys_are_written_out():
    assert _show("{ [{ 1 }] = true }") == "{ [{ 1 }] = true }"


# ── limits and options ───────────────────────────────────────────────────────


def test_long_text_is_truncated():
    text = _show(f'string.rep("x", {MAX_INSPECT_TEXT})')
    assert text.endswith(TRUNCATED)
    assert len(text) == MAX_INSPECT_TEXT + len(TRUNCATED)


def test_huge_table_stops_walking_at_the_limit():
    from lupa.lua54 import LuaRuntime

    lua = LuaRuntime()
    table = lua.eval("(function() local t = {} for i = 1, 5000 do t[i] = i end return t end)()")
    text = inspect(table, _lua_describe(lua), limit=100)
    assert text.startswith("{\n  1,\n  2,\n")
    assert text.endswith(TRUNCATED)
    assert len(text) == 100 + len(TRUNCATED)


@pytest.mark.parametrize("options, message", [
    ("{ depth = 0 }", "depth must be a number from 1 to 100"),
    ("{ depth = 101 }", "depth must be a number from 1 to 100"),
    ("{ indent = 9 }", "indent must be a number from 0 to 8"),
    ("{ indent = true }", "indent must be a number"),
    ("{ width = 3 }", "unknown option"),
    ('"x"', "options must be a table"),
])
def test_bad_options(options, message):
    with pytest.raises(MacroError, match=message):
        _run(f"opensak.inspect({{}}, {options})")


# ── opensak.log ──────────────────────────────────────────────────────────────


def test_log_writes_tables_out():
    log: list[str] = []
    MacroRuntime(MagicMock(), output=lambda _: None, log=log.append).run(
        'opensak.log("caches:", { "GC1", "GC2" }, 3, nil, { n = { x = true } })')
    assert log == ['caches:\t{ "GC1", "GC2" }\t3\tnil\t{ n = { x = true } }']


def test_log_keeps_strings_unquoted():
    log: list[str] = []
    MacroRuntime(MagicMock(), output=lambda _: None, log=log.append).run(
        'opensak.log("plain", 1.5, true)')
    assert log == ["plain\t1.5\ttrue"]


def test_print_is_still_standard_lua():
    out = _run("print({})")
    assert out[0].startswith("<Lua table") or out[0].startswith("table:")

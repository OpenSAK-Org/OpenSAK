"""tests/unit-tests/test_macro_json.py — opensak.json.decode() / encode().

JSON and Lua do not map one to one: a null cannot be stored in a table, {} and
[] are the same table, and tables have no order. These tests pin down the
mapping (json_codec.py).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from opensak.macro import MacroError, MacroRuntime
from opensak.macro.json_codec import MAX_JSON_TEXT
from opensak.macro.settings import MAX_DEPTH


def _run(source: str, **kwargs) -> list[str]:
    out: list[str] = []
    MacroRuntime(MagicMock(), output=out.append, **kwargs).run(source)
    return out


# ── decode ───────────────────────────────────────────────────────────────────


def test_decode_objects_arrays_and_scalars():
    assert _run(r"""
        local v = opensak.json.decode('{"s": "Zürich", "i": 3, "f": 1.5, "t": true,'
                                      .. ' "a": [10, {"x": "y"}], "o": {"k": "v"}}')
        print(v.s, v.i, v.f, v.t, #v.a, v.a[1], v.a[2].x, v.o.k)
        print(math.type(v.i), math.type(v.f))
    """) == ["Zürich\t3\t1.5\ttrue\t2\t10\ty\tv", "integer\tfloat"]


@pytest.mark.parametrize("text, shown", [
    ('"x"', "x"), ("12", "12"), ("false", "false"), ("null", "nil"),
    ('"\\u00fc"', "ü"), ('"\\ud83d\\ude00"', "😀"),
])
def test_decode_top_level_values(text, shown):
    assert _run(f"print(opensak.json.decode({text!r}))") == [shown]


def test_decode_null_member_is_absent():
    assert _run("""
        local v = opensak.json.decode('{"a": null, "b": 1}')
        print(v.a, rawlen(v))
        local n = 0
        for _ in pairs(v) do n = n + 1 end
        print(n)
    """) == ["nil\t0", "1"]


def test_decode_null_array_item_leaves_a_gap():
    assert _run("""
        local v = opensak.json.decode("[1, null, 3]")
        print(v[1], v[2], v[3])
    """) == ["1\tnil\t3"]


@pytest.mark.parametrize("value, shown", [
    ('"<null>"', "<null>"), ("0", "0"), ("false", "false"),
])
def test_decode_null_option_stands_in_for_null(value, shown):
    assert _run(f"""
        local v = opensak.json.decode('{{"a": [null, 1], "b": null}}', {{ null = {value} }})
        print(v.b, v.a[1], #v.a)
    """) == [f"{shown}\t{shown}\t2"]


def test_decode_empty_containers_are_empty_tables():
    assert _run("""
        local v = opensak.json.decode('{"a": [], "o": {}}')
        print(type(v.a), #v.a, type(v.o), next(v.o))
    """) == ["table\t0\ttable\tnil"]


def test_decode_accepts_a_byte_order_mark():
    assert _run("""print(opensak.json.decode("\\u{FEFF}[1]")[1])""") == ["1"]


def test_decode_big_integer_becomes_a_float():
    assert _run("""
        local v = opensak.json.decode("[9223372036854775807, 9223372036854775808]")
        print(math.type(v[1]), math.type(v[2]))
    """) == ["integer\tfloat"]


def test_decode_lone_surrogate_does_not_fail():
    assert _run(r"""print(#opensak.json.decode('"\\ud800"') > 0)""") == ["true"]


@pytest.mark.parametrize("text", ["{", "[1,]", "{'a': 1}", "", "NaN", "Infinity", "[-Infinity]"])
def test_decode_refuses_invalid_json(text):
    with pytest.raises(MacroError, match="invalid JSON"):
        _run(f"opensak.json.decode({text!r})")


@pytest.mark.parametrize("value", ["nil", "1", "{}", "true"])
def test_decode_expects_a_string(value):
    with pytest.raises(MacroError, match="expects a JSON string"):
        _run(f"opensak.json.decode({value})")


def test_invalid_json_can_be_caught_by_the_macro():
    out = _run("""
        local ok, err = pcall(opensak.json.decode, "{")
        print(ok, tostring(err):find("invalid JSON", 1, true) ~= nil)
    """)
    assert out == ["false\ttrue"]


def test_decode_refuses_too_deep_nesting():
    deep = "[" * (MAX_DEPTH + 1) + "]" * (MAX_DEPTH + 1)
    with pytest.raises(MacroError, match="levels deep"):
        _run(f'opensak.json.decode("{deep}")')
    extreme = 200_000
    with pytest.raises(MacroError, match="levels deep"):
        _run(f'opensak.json.decode(string.rep("[", {extreme}) .. string.rep("]", {extreme}))')


def test_decode_refuses_too_long_text():
    with pytest.raises(MacroError, match="longer than"):
        _run(f'opensak.json.decode(string.rep(" ", {MAX_JSON_TEXT + 1}))')


def test_decode_refuses_an_invalid_null_option():
    with pytest.raises(MacroError, match="null must be"):
        _run('opensak.json.decode("[]", { null = {} })')


def test_unknown_option_is_refused():
    with pytest.raises(MacroError, match="unknown option"):
        _run('opensak.json.decode("[]", { nul = 1 })')


# ── encode ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value, text", [
    ('"Zürich"', '"Zürich"'),
    ("3", "3"),
    ("1.5", "1.5"),
    ("true", "true"),
    ('{1, "a", false}', '[1,"a",false]'),
    ("{b = 1, a = 2, c = {d = 3}}", '{"a":2,"b":1,"c":{"d":3}}'),
    ('"a\\"b\\n"', r'"a\"b\n"'),
])
def test_encode_values(value, text):
    assert _run(f"print(opensak.json.encode({value}))") == [text]


def test_encode_empty_table_is_an_array_unless_asked():
    assert _run("""
        print(opensak.json.encode({}))
        print(opensak.json.encode({ a = {} }))
        print(opensak.json.encode({ a = {} }, { empty = "object" }))
    """) == ["[]", '{"a":[]}', '{"a":{}}']


def test_encode_null_option_writes_null():
    assert _run("""
        print(opensak.json.encode({ a = "<null>", b = { "<null>", "x" } }, { null = "<null>" }))
        print(opensak.json.encode({ false, true, 0 }, { null = false }))
        print(opensak.json.encode("<null>", { null = "<null>" }))
    """) == ['{"a":null,"b":[null,"x"]}', "[null,true,0]", "null"]


def test_encode_indent():
    assert _run('print(opensak.json.encode({ a = {1} }, { indent = 2 }))') == [
        '{\n  "a": [\n    1\n  ]\n}'
    ]


@pytest.mark.parametrize("value, message", [
    ("nil", "expects a value"),
    ("print", "cannot encode a function"),
    ("{f = print}", "cannot encode a function at f"),
    ("{[1] = 'a', [3] = 'b'}", "cannot encode a list with gaps"),
    ("{[1.5] = 'a'}", "cannot encode the key 1.5"),
    ("{a = 1, 'b'}", "cannot encode the key 1"),
    ("math.huge", "cannot encode inf"),
    ("0/0", "cannot encode nan"),
    ("(function() local t = {} t.t = t return t end)()", "cyclic"),
])
def test_encode_refuses(value, message):
    with pytest.raises(MacroError, match=message):
        _run(f"opensak.json.encode({value})")


@pytest.mark.parametrize("options, message", [
    ('{ empty = "x" }', "empty must be"),
    ("{ indent = 9 }", "indent must be"),
    ("{ null = {} }", "null must be"),
    ("{ x = 1 }", "unknown option"),
])
def test_encode_refuses_invalid_options(options, message):
    with pytest.raises(MacroError, match=message):
        _run(f'opensak.json.encode("x", {options})')


def test_settings_save_still_says_save(tmp_path):
    macro = tmp_path / "m.lua"
    source = "opensak.settings.save({ f = print })"
    macro.write_text(source, encoding="utf-8")
    with pytest.raises(MacroError, match="cannot save a function at f"):
        MacroRuntime(MagicMock(), settings_dir=tmp_path).run(
            source, base_dir=tmp_path, macro_path=macro)


# ── Round trip ───────────────────────────────────────────────────────────────


def test_round_trip_keeps_null_with_the_option():
    text = '{"MultiChoiceOptions":null,"Name":"Hut","Tags":[null,"a"]}'
    assert _run(f"""
        local v = opensak.json.decode('{text}', {{ null = "<null>" }})
        print(opensak.json.encode(v, {{ null = "<null>" }}))
    """) == [text]


def test_round_trip_without_the_option_drops_null_members():
    assert _run("""
        print(opensak.json.encode(opensak.json.decode('{"a": null, "b": 1}')))
    """) == ['{"b":1}']


def test_large_document_needs_no_lua_instructions():
    """The point of the API: a parser in Lua would run into the limit."""
    assert _run("""
        local t = {}
        for i = 1, 1000 do t[i] = { id = i, name = "x" } end
        local v = opensak.json.decode(opensak.json.encode(t))
        print(#v, v[1000].id)
    """, instruction_limit=1_000_000) == ["1000\t1000"]


@pytest.mark.parametrize("item, count", [
    ('{"a":1},', 200_000), ("{},", 300_000), ("1,", 2_000_000), ('"xxxxxxxxxx",', 200_000),
])
def test_decode_over_the_memory_limit_is_an_error(item, count):
    """Not a crash of the whole process (json_codec.py)."""
    with pytest.raises(MacroError, match="not enough memory"):
        _run(f"""opensak.json.decode("[" .. string.rep('{item}', {count}) .. "1]")""",
             memory_limit=8 * 1024 * 1024)


@pytest.mark.parametrize("megabytes", [6, 7, 8, 9, 10, 12, 16, 24, 32, 48])
def test_decode_never_crashes_near_the_memory_limit(megabytes):
    """Fits or is refused, whichever side of the limit it is on."""
    count = 60_000
    source = f"""
        local ok = pcall(opensak.json.decode, "[" .. string.rep('{{"a":"b"}},', {count}) .. "1]")
        print(ok)
    """
    assert _run(source, memory_limit=megabytes * 1024 * 1024)[0] in ("true", "false")


def test_encode_result_over_the_memory_limit_is_an_error():
    source = """
        local t = {}
        local s = string.rep("x", 1000000)
        for i = 1, 3 do t[i] = s end
        opensak.json.encode(t)
    """
    with pytest.raises(MacroError, match="not enough memory"):
        _run(source, memory_limit=8 * 1024 * 1024)


def test_a_document_that_fits_is_not_refused():
    """Well under the default limit: the estimate must leave room for real use."""
    source = """
        local t = {}
        for i = 1, 50000 do
          t[i] = { code = "GC" .. i, name = "Some cache name", lat = 47.5, lon = 8.5, found = false }
        end
        local v = opensak.json.decode(opensak.json.encode(t))
        print(#v, v[50000].code)
    """
    assert _run(source) == ["50000\tGC50000"]

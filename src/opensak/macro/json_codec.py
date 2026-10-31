"""
src/opensak/macro/json_codec.py — opensak.json.encode() / decode().

A macro otherwise needs a JSON parser of its own, running under its
instruction limit, e.g. for what opensak.get_url() downloads.

JSON and Lua do not map one to one, so three things are defined here:

  * null: Lua tables cannot hold nil. By default a null object member is
    left out, which reads as nil, and a null array item leaves a gap at its
    index. With a null value (a string, number or boolean of the macro's
    choice) decode() puts that value in instead and encode() writes it back
    as null, so arrays keep their length and null survives a round trip.
  * The empty table: {} and [] are the same in Lua. encode() writes an empty
    table as [] unless asked for {}.
  * Object member order: Lua tables have none, so encode() sorts the keys and
    the same table always gives the same text.

What can be encoded follows opensak.settings.save (settings.to_json).

Running out of Lua's memory while Python creates a table or string does not
give a Lua error: it ends the whole process (lupa creates them outside Lua's
error protection). So what a result will take is estimated generously first,
and a result that may not fit is refused with an error (check_memory).
"""

from __future__ import annotations

import json
from typing import Any

from opensak.macro.errors import MacroError
from opensak.macro.settings import MAX_DEPTH

# The longest JSON text, like the largest file opensak.write_text() writes.
MAX_JSON_TEXT = 20_000_000
# No null value: decode() leaves nulls out, encode() writes none.
NO_NULL = object()

_LUA_INTEGER_MIN, _LUA_INTEGER_MAX = -(2 ** 63), 2 ** 63 - 1

# Upper estimates of what Lua needs, in bytes (measured: a table 77, a member
# up to 72, while its table grows old and new slots exist side by side).
_TABLE_COST = 128
_MEMBER_COST = 128
_STRING_COST = 40


def check_memory(lua: Any, needed: int, func: str) -> None:
    """Refuse a result of about *needed* bytes that may not fit in Lua's memory."""
    limit = lua.get_max_memory()
    if limit and lua.get_memory_used() + needed > limit:
        raise MacroError(f"{func}: not enough memory for the result "
                         f"(about {needed // (1024 * 1024) + 1} MB)")


def string_cost(text: str) -> int:
    """About how many bytes of Lua memory *text* takes (UTF-8: up to 3 per
    character of a Python string that fits in Lua)."""
    return _STRING_COST + len(text) * 3


def _cost(value: Any, func: str, depth: int = 0) -> int:
    """About how many bytes of Lua memory decode()'s *value* takes."""
    if isinstance(value, str):
        return string_cost(value)
    if not isinstance(value, (list, dict)):
        return 0
    if depth >= MAX_DEPTH:
        raise MacroError(f"{func}: nested more than {MAX_DEPTH} levels deep")
    cost = _TABLE_COST + _MEMBER_COST * len(value)
    if isinstance(value, dict):
        cost += sum(_cost(k, func, depth) for k in value)
        value = list(value.values())
    return cost + sum(_cost(v, func, depth + 1) for v in value)


def _check_null(null: Any, func: str) -> None:
    if null is not NO_NULL and not isinstance(null, (str, int, float)):
        raise MacroError(f"{func}: null must be a string, number or boolean")


def _is_null(value: Any, null: Any) -> bool:
    # In Python, 1 == True.
    return (null is not NO_NULL and isinstance(value, bool) == isinstance(null, bool)
            and value == null)


def _prepare(data: Any, null: Any, empty_object: bool) -> Any:
    """to_json() data with the null value as None and empty tables as asked."""
    if _is_null(data, null):
        return None
    if isinstance(data, list):
        return [_prepare(v, null, empty_object) for v in data]
    if isinstance(data, dict):
        if not data:
            return {} if empty_object else []
        return {k: _prepare(v, null, empty_object) for k, v in data.items()}
    return data


def encode(lua: Any, data: Any, func: str, null: Any = NO_NULL, empty_object: bool = False,
           indent: int = 0) -> str:
    """*data* (settings.to_json) as JSON text."""
    _check_null(null, func)
    text = json.dumps(
        _prepare(data, null, empty_object), ensure_ascii=False, sort_keys=True,
        allow_nan=False, indent=indent or None,
        separators=(",", ": ") if indent else (",", ":"),
    )
    if len(text) > MAX_JSON_TEXT:
        raise MacroError(f"{func}: the JSON text is longer than {MAX_JSON_TEXT:,} characters")
    check_memory(lua, string_cost(text), func)
    return text


def _parse_int(text: str) -> int | float:
    number = int(text)
    # A Lua integer has 64 bits; a larger number can only be a float.
    return number if _LUA_INTEGER_MIN <= number <= _LUA_INTEGER_MAX else float(text)


def _refuse_constant(name: str) -> None:
    raise ValueError(f"{name} is not valid JSON")


def decode(text: Any, func: str) -> Any:
    """JSON text → Python data."""
    if isinstance(text, bytes):     # e.g. opensak.get_url(url, { binary = true })
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            raise MacroError(f"{func}: the text is not valid UTF-8") from None
    if not isinstance(text, str):
        raise MacroError(f"{func} expects a JSON string")
    if len(text) > MAX_JSON_TEXT:
        raise MacroError(f"{func}: the text is longer than {MAX_JSON_TEXT:,} characters")
    try:
        return json.loads(text.removeprefix("﻿"), parse_int=_parse_int,
                          parse_constant=_refuse_constant)
    except RecursionError:
        raise MacroError(f"{func}: nested more than {MAX_DEPTH} levels deep") from None
    except ValueError as exc:
        raise MacroError(f"{func}: invalid JSON: {exc}") from None


def _lua_text(text: str) -> str:
    # "\ud800" is valid JSON but no text: it cannot be UTF-8, as Lua needs.
    return text.encode("utf-8", "replace").decode("utf-8")


def to_lua(lua: Any, data: Any, func: str, null: Any = NO_NULL) -> Any:
    """decode()'s *data* as Lua values, a null being *null* (see above)."""
    _check_null(null, func)
    check_memory(lua, _cost(data, func), func)

    def convert(value: Any) -> Any:
        if value is None:
            return None if null is NO_NULL else null
        if isinstance(value, str):
            return _lua_text(value)
        if not isinstance(value, (list, dict)):
            return value
        items = value.items() if isinstance(value, dict) else enumerate(value, 1)
        return lua.table_from({
            _lua_text(k) if isinstance(k, str) else k: convert(v)
            for k, v in items
        })

    return convert(data)

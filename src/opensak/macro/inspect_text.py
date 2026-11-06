"""
src/opensak/macro/inspect_text.py — opensak.inspect() (#1275): any Lua value
as readable text, e.g. to see what a table holds while writing a macro.

print() and tostring() only show a table's address. inspect() writes the
table out the way it would be written in Lua source:

  * the list part (1, 2, 3, … without a gap) first and without keys, then
    the other keys sorted — numbers, strings (naturally: "wp2" before
    "wp10"), booleans, then the rest — so the same table always gives the
    same text;
  * a string key that is a Lua name as `name = `, any other key in brackets;
  * strings quoted and escaped like string.format("%q"), so "1" and 1, or
    trailing spaces, can be told apart;
  * a table nested deeper than *depth* as {...}, a table inside itself as
    <cycle>, functions as <function> (coroutines too: lupa hands them to
    Python as functions) and userdata as <userdata> — inspect() never fails
    on a value;
  * a table on one line when it fits, otherwise one entry per line.

Metatables are ignored: the entries are read raw, like rawget() and next().

The text is at most MAX_INSPECT_TEXT characters; past that the walk stops
and the text ends with TRUNCATED, so a huge table cannot flood the log.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable

# Longer text is cut: about what a log window can still show usefully.
MAX_INSPECT_TEXT = 1_000_000
TRUNCATED = "\n... (truncated)"
DEFAULT_DEPTH = 10
DEFAULT_INDENT = 2
# A table whose entries are all on one line is written on one line when that
# line is no longer than this.
ONE_LINE_WIDTH = 80

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_DIGITS = re.compile(r"(\d+)")
_KEYWORDS = frozenset((
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "goto", "if", "in", "local", "nil", "not", "or", "repeat", "return", "then",
    "true", "until", "while",
))
_ESCAPES = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


class _Budget:
    """Characters still allowed; once spent, values are no longer walked."""

    def __init__(self, limit: int) -> None:
        self.left = limit

    def spend(self, text: str) -> str:
        self.left -= len(text)
        return text

    @property
    def spent(self) -> bool:
        return self.left < 0


def _number(value: int | float) -> str:
    """What Lua 5.4's tostring() gives for the number."""
    if isinstance(value, int):
        return str(value)
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    text = "%.14g" % value
    return text if any(c in text for c in ".en") else text + ".0"


def _escape(char: str) -> str:
    if char in _ESCAPES:
        return _ESCAPES[char]
    code = ord(char)
    if 0xDC80 <= code <= 0xDCFF:          # a byte that is not UTF-8
        return f"\\{code - 0xDC00}"
    if code < 32 or code == 127:
        return f"\\{code}"
    return char


def _string(value: str | bytes) -> str:
    if isinstance(value, bytes):           # a Lua string that is not UTF-8
        value = value.decode("utf-8", errors="surrogateescape")
    return '"' + "".join(_escape(c) for c in value) + '"'


def _natural(text: str) -> tuple:
    """Sort key putting "wp2" before "wp10"."""
    return tuple((0, int(part), "") if part.isdigit() else (1, 0, part)
                 for part in _DIGITS.split(text) if part)


def _key_order(key: Any) -> tuple:
    if isinstance(key, bool):
        return (2, key, (), "")
    if isinstance(key, (int, float)):
        return (0, key, (), "")
    if isinstance(key, (str, bytes)):
        shown = _string(key)
        return (1, 0, _natural(shown), shown)
    return (3, 0, (), "")


def inspect(value: Any, describe: Callable[[Any], Any], depth: int = DEFAULT_DEPTH,
            indent: int = DEFAULT_INDENT, limit: int = MAX_INSPECT_TEXT) -> str:
    """*value* as readable text (see the module docstring).

    *describe* is the Lua function of runtime._lua_describe(): type(v) and,
    for a table, its address — lupa wraps the same table in a new Python
    object on every access, so the address detects a table inside itself.
    *indent* 0 writes every table on one line.
    """
    from lupa.lua54 import lua_type

    budget = _Budget(limit)

    def show(v: Any, level: int, active: set[str]) -> str:
        if budget.spent:
            return ""
        if v is None:
            return budget.spend("nil")
        if isinstance(v, bool):
            return budget.spend("true" if v else "false")
        if isinstance(v, (int, float)):
            return budget.spend(_number(v))
        if isinstance(v, (str, bytes)):
            return budget.spend(_string(v))
        kind = lua_type(v)
        if kind != "table":
            # The opensak functions are Python callables, userdata to Lua.
            shown = kind or ("function" if callable(v) else "userdata")
            return budget.spend(f"<{shown}>")
        _, address = describe(v)
        if address in active:
            return budget.spend("<cycle>")
        items = list(v.items())
        if not items:
            return budget.spend("{}")
        if level >= depth:
            return budget.spend("{...}")
        # Not a dict of all keys: in Python true == 1, in Lua they are two keys.
        listed = {k: item for k, item in items if type(k) is int}
        count = 0
        while count + 1 in listed:
            count += 1
        rest = sorted(((k, item) for k, item in items
                       if not (type(k) is int and 1 <= k <= count)),
                      key=lambda pair: _key_order(pair[0]))
        active.add(address)
        try:
            entries = []
            for i in range(1, count + 1):
                if budget.spent:
                    break
                entries.append(show(listed[i], level + 1, active))
            for key, item in rest:
                if budget.spent:
                    break
                if isinstance(key, str) and _NAME.match(key) and key not in _KEYWORDS:
                    shown_key = budget.spend(key)
                else:
                    shown_key = "[" + show(key, level + 1, active) + "]"
                entries.append(shown_key + budget.spend(" = ") + show(item, level + 1, active))
        finally:
            active.discard(address)
        one_line = "{ " + ", ".join(entries) + " }"
        if indent == 0 or (len(one_line) <= ONE_LINE_WIDTH - level * indent
                           and "\n" not in one_line):
            return one_line
        pad = " " * indent
        lines = ",\n".join(pad + entry.replace("\n", "\n" + pad) for entry in entries)
        return "{\n" + lines + "\n}"

    text = show(value, 0, set())
    if budget.spent or len(text) > limit:
        return text[:limit] + TRUNCATED
    return text

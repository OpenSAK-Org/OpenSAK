"""
src/opensak/macro/form_code.py — read and write the Lua of opensak.form{}.

The form designer edits the table passed to opensak.form{} inside a macro:
it finds the table around the cursor (find_form_table), reads it without
running the macro (parse_table), and writes the edited form back
(form_to_lua). Literal values (strings, numbers, booleans, nested tables)
become Python values; anything else — a callback, a variable, a string
concatenation — is kept as RawLua with its exact source text, so code the
designer does not understand survives an edit unchanged. Comments inside
the table are not kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from opensak.macro.forms import FIELD_KEY_DOCS, FORM_KEY_DOCS, RawLua, format_number

KEYWORDS = frozenset({
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "goto", "if", "in", "local", "nil", "not", "or", "repeat", "return", "then",
    "true", "until", "while",
})
# Keywords opening a block that a matching "end" (or "until") closes.
_BLOCK_OPEN = frozenset({"function", "if", "do", "repeat"})
_BLOCK_CLOSE = frozenset({"end", "until"})
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(
    r"0[xX](?:[0-9a-fA-F]*\.?[0-9a-fA-F]+|[0-9a-fA-F]+\.?)(?:[pP][+-]?\d+)?"
    r"|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
)
_SYMBOLS = ("...", "..", "==", "~=", "<=", ">=", "//", "::", "<<", ">>")
_LONG_BRACKET = re.compile(r"\[(=*)\[")
# Lines longer than this are split over several lines.
LINE_WIDTH = 100


class LuaSyntaxError(ValueError):
    """The text is not a Lua table the designer can read."""


@dataclass(frozen=True)
class Token:
    kind: str       # "name", "string", "number", "sym", "eof"
    value: Any      # decoded string / number / name / symbol
    start: int
    end: int


# ── Tokenizer ────────────────────────────────────────────────────────────────


_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
            "v": "\v", "\\": "\\", '"': '"', "'": "'", "\n": "\n"}


def _long_end(text: str, pos: int, level: str) -> int:
    close = f"]{level}]"
    end = text.find(close, pos)
    if end < 0:
        raise LuaSyntaxError("unfinished long string or comment")
    return end


def _quoted(text: str, pos: int) -> tuple[str, int]:
    """Decode the string starting with the quote at *pos*; return it and
    the position after the closing quote."""
    quote = text[pos]
    out: list[str] = []
    raw = bytearray()

    def flush() -> None:
        if raw:
            out.append(raw.decode("utf-8", errors="replace"))
            raw.clear()

    i = pos + 1
    while i < len(text):
        ch = text[i]
        if ch == quote:
            flush()
            return "".join(out), i + 1
        if ch == "\n":
            break
        if ch != "\\":
            flush()
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= len(text):
            break
        esc = text[i]
        if esc in _ESCAPES:
            flush()
            out.append(_ESCAPES[esc])
            i += 1
        elif esc == "x":
            raw.append(int(text[i + 1:i + 3], 16))
            i += 3
        elif esc == "z":
            i += 1
            while i < len(text) and text[i].isspace():
                i += 1
        elif esc.isdigit():
            m = re.match(r"\d{1,3}", text[i:])
            assert m
            raw.append(int(m.group()) & 0xFF)
            i += len(m.group())
        elif esc == "u":
            m = re.match(r"u\{([0-9a-fA-F]+)\}", text[i:])
            if not m:
                raise LuaSyntaxError("bad \\u escape")
            flush()
            out.append(chr(int(m.group(1), 16)))
            i += len(m.group())
        else:
            raise LuaSyntaxError(f"bad escape \\{esc}")
    raise LuaSyntaxError("unfinished string")


def tokenize(text: str) -> list[Token]:
    """All tokens of *text*, comments and white space left out."""
    tokens: list[Token] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if text.startswith("--", i):
            m = _LONG_BRACKET.match(text, i + 2)
            if m:
                i = _long_end(text, m.end(), m.group(1)) + len(m.group(1)) + 2
            else:
                end = text.find("\n", i)
                i = n if end < 0 else end + 1
            continue
        if ch in "\"'":
            value, end = _quoted(text, i)
            tokens.append(Token("string", value, i, end))
            i = end
            continue
        m = _LONG_BRACKET.match(text, i)
        if m:
            body = m.end()
            if text.startswith("\r\n", body):
                body += 2
            elif text.startswith("\n", body):
                body += 1
            end = _long_end(text, m.end(), m.group(1))
            stop = end + len(m.group(1)) + 2
            tokens.append(Token("string", text[body:end], i, stop))
            i = stop
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            m = _NUMBER.match(text, i)
            assert m
            literal = m.group()
            number: int | float
            if literal[:2].lower() == "0x":
                number = int(literal, 16) if re.fullmatch(r"0[xX][0-9a-fA-F]+", literal) \
                    else float.fromhex(literal)
            elif re.fullmatch(r"\d+", literal):
                number = int(literal)
            else:
                number = float(literal)
            tokens.append(Token("number", number, i, m.end()))
            i = m.end()
            continue
        m = _NAME.match(text, i)
        if m:
            tokens.append(Token("name", m.group(), i, m.end()))
            i = m.end()
            continue
        for sym in _SYMBOLS:
            if text.startswith(sym, i):
                tokens.append(Token("sym", sym, i, i + len(sym)))
                i += len(sym)
                break
        else:
            tokens.append(Token("sym", ch, i, i + 1))
            i += 1
    tokens.append(Token("eof", None, n, n))
    return tokens


# ── Table parser ─────────────────────────────────────────────────────────────


class _TableParser:
    def __init__(self, text: str, tokens: list[Token]):
        self.text = text
        self.tokens = tokens

    def at(self, i: int, kind: str, value: Any = None) -> bool:
        tok = self.tokens[i]
        return tok.kind == kind and (value is None or tok.value == value)

    def ends_value(self, i: int) -> bool:
        return self.at(i, "sym", ",") or self.at(i, "sym", ";") or self.at(i, "sym", "}")

    def matching(self, i: int) -> int:
        """Index of the token closing the bracket or block opened at *i*."""
        depth = 0
        while True:
            tok = self.tokens[i]
            if tok.kind == "eof":
                raise LuaSyntaxError("unbalanced brackets")
            if tok.kind == "sym" and tok.value in "({[":
                depth += 1
            elif tok.kind == "sym" and tok.value in ")}]":
                depth -= 1
            elif tok.kind == "name" and tok.value in _BLOCK_OPEN:
                depth += 1
            elif tok.kind == "name" and tok.value in _BLOCK_CLOSE:
                depth -= 1
            if depth == 0:
                return i
            i += 1

    def table(self, i: int) -> tuple[Any, int]:
        """Parse the table constructor whose "{" is token *i*; return the
        value and the index after its "}"."""
        assert self.at(i, "sym", "{")
        i += 1
        positional: list[Any] = []
        named: dict[Any, Any] = {}
        while not self.at(i, "sym", "}"):
            if self.at(i, "eof"):
                raise LuaSyntaxError("unfinished table")
            if self.at(i, "sym", "["):
                close = self.matching(i)
                key, after = self.value(i + 1, stop=close)
                if after != close:
                    key = RawLua(self.source(i + 1, close))
                if not self.at(close + 1, "sym", "="):
                    raise LuaSyntaxError("expected = after [key]")
                value, i = self.value(close + 2)
                named[key] = value
            elif self.at(i, "name") and self.at(i + 1, "sym", "=") \
                    and self.tokens[i].value not in KEYWORDS:
                key = self.tokens[i].value
                value, i = self.value(i + 2)
                named[key] = value
            else:
                value, i = self.value(i)
                positional.append(value)
            if self.at(i, "sym", ",") or self.at(i, "sym", ";"):
                i += 1
            elif not self.at(i, "sym", "}"):
                raise LuaSyntaxError(f"unexpected {self.tokens[i].value!r} in table")
        if not named:
            return positional, i + 1
        for index, value in enumerate(positional, 1):
            named[index] = value
        if all(isinstance(k, int) for k in named) and sorted(named) == list(range(1, len(named) + 1)):
            return [named[k] for k in range(1, len(named) + 1)], i + 1
        return named, i + 1

    def value(self, i: int, stop: Optional[int] = None) -> tuple[Any, int]:
        """One expression starting at token *i*: a literal or table, or the
        raw source text up to the next separator."""
        start = i

        def done(j: int) -> bool:
            return j == stop if stop is not None else self.ends_value(j)

        tok = self.tokens[i]
        if tok.kind == "sym" and tok.value == "{":
            value, after = self.table(i)
            if done(after):
                return value, after
        elif tok.kind in ("string", "number") and done(i + 1):
            return tok.value, i + 1
        elif tok.kind == "name" and tok.value in ("true", "false", "nil") and done(i + 1):
            return {"true": True, "false": False, "nil": None}[tok.value], i + 1
        elif tok.kind == "sym" and tok.value == "-" and self.at(i + 1, "number") and done(i + 2):
            return -self.tokens[i + 1].value, i + 2
        # Anything else: up to the separator at bracket/block depth 0.
        j = i
        while not done(j):
            if self.at(j, "eof"):
                raise LuaSyntaxError("unfinished expression")
            t = self.tokens[j]
            if (t.kind == "sym" and t.value in "({[") or (t.kind == "name" and t.value in _BLOCK_OPEN):
                j = self.matching(j)
            elif t.kind == "sym" and t.value in ")}]" or (t.kind == "name" and t.value in _BLOCK_CLOSE):
                raise LuaSyntaxError(f"unexpected {t.value!r}")
            j += 1
        if j == start:
            raise LuaSyntaxError("missing value")
        return RawLua(self.source(start, j)), j

    def source(self, first: int, stop: int) -> str:
        """Source text of tokens first .. stop-1."""
        return self.text[self.tokens[first].start:self.tokens[stop - 1].end]


def parse_table(text: str) -> Any:
    """Parse *text*, a Lua table constructor "{ … }", into Python: a list
    for an array (also for {}), a dict otherwise. Values that are not
    literals become RawLua."""
    tokens = tokenize(text)
    if not tokens or tokens[0].kind != "sym" or tokens[0].value != "{":
        raise LuaSyntaxError("expected a table { … }")
    value, after = _TableParser(text, tokens).table(0)
    if tokens[after].kind != "eof":
        raise LuaSyntaxError("text after the table")
    return value


def find_form_table(source: str, pos: int) -> Optional[tuple[int, int]]:
    """The (start, end) character span of the form table around *pos*: the
    outermost table constructor containing *pos* that has a `fields` key
    (an opensak.form{…} call also counts when the cursor is on "opensak.form").
    None if there is none or the source cannot be tokenized."""
    try:
        tokens = tokenize(source)
    except LuaSyntaxError:
        return None
    stack: list[int] = []
    best: Optional[tuple[int, int]] = None
    for i, tok in enumerate(tokens):
        if tok.kind != "sym":
            continue
        if tok.value == "{":
            stack.append(i)
        elif tok.value == "}" and stack:
            open_index = stack.pop()
            if not _has_fields_key(tokens, open_index, i):
                continue
            start, end = tokens[open_index].start, tok.end
            reach = _form_call_start(tokens, open_index)
            if (reach if reach is not None else start) <= pos <= end:
                if best is None or start < best[0]:
                    best = (start, end)
    return best


def find_form_tables(source: str) -> list[tuple[int, int]]:
    """The (start, end) spans of all outermost tables with a `fields` key,
    in source order; [] if the source cannot be tokenized."""
    try:
        tokens = tokenize(source)
    except LuaSyntaxError:
        return []
    stack: list[int] = []
    spans: list[tuple[int, int]] = []
    for i, tok in enumerate(tokens):
        if tok.kind == "sym" and tok.value == "{":
            stack.append(i)
        elif tok.kind == "sym" and tok.value == "}" and stack:
            open_index = stack.pop()
            if _has_fields_key(tokens, open_index, i):
                start = tokens[open_index].start
                spans = [s for s in spans if not start <= s[0] < tok.end]
                spans.append((start, tok.end))
    return sorted(spans)


def _has_fields_key(tokens: list[Token], first: int, last: int) -> bool:
    depth = 0
    for j in range(first + 1, last):
        tok = tokens[j]
        if tok.kind == "sym" and tok.value in "({[":
            depth += 1
        elif tok.kind == "sym" and tok.value in ")}]":
            depth -= 1
        elif depth == 0 and tok.kind == "name" and tok.value == "fields" \
                and tokens[j + 1].kind == "sym" and tokens[j + 1].value == "=":
            return True
    return False


def _form_call_start(tokens: list[Token], open_index: int) -> Optional[int]:
    """Start of "opensak.form" right before the "{" (or "({"), else None."""
    j = open_index - 1
    if j >= 0 and tokens[j].kind == "sym" and tokens[j].value == "(":
        j -= 1
    if j >= 2 and [t.value for t in tokens[j - 2:j + 1]] == ["opensak", ".", "form"]:
        return tokens[j - 2].start
    return None


# ── Code generation ──────────────────────────────────────────────────────────


_FORM_ORDER = [d.key for d in FORM_KEY_DOCS if d.key not in ("fields", "buttons")] \
    + ["fields", "buttons"]
_FIELD_ORDER = list(dict.fromkeys(
    ["type", "id", "label", "x", "y", "w", "h", "default", "options"]
    + [d.key for d in FIELD_KEY_DOCS]
))
_INDENT = "  "


def lua_string(text: str) -> str:
    """*text* as a double-quoted Lua string."""
    out = ['"']
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append(f"\\{ord(ch):03d}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _key(key: Any) -> str:
    if isinstance(key, str) and _NAME.fullmatch(key) and key not in KEYWORDS:
        return key
    if isinstance(key, RawLua):
        return f"[{key.text}]"
    if isinstance(key, str):
        return f"[{lua_string(key)}]"
    return f"[{_scalar(key)}]"


def _scalar(value: Any) -> str:
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format_number(value) if value.is_integer() else repr(value)
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, RawLua):
        return value.text
    raise TypeError(f"cannot write {value!r} as Lua")


def _ordered(table: dict, order: list[str]) -> list[Any]:
    rank = {k: i for i, k in enumerate(order)}
    return sorted(table, key=lambda k: (0, rank[k]) if k in rank else (1, str(k)))


def to_lua(value: Any, indent: str = "", order: Optional[list[str]] = None) -> str:
    """*value* as Lua source; nested tables continue at *indent*."""
    if isinstance(value, list):
        items = [to_lua(v, indent + _INDENT, _FIELD_ORDER) for v in value]
        return _join(items, indent, as_list=True)
    if isinstance(value, dict):
        keys = _ordered(value, order or _FIELD_ORDER)
        items = []
        for k in keys:
            if value[k] is None:
                continue
            child_order = _FIELD_ORDER if k in ("fields", "buttons") else None
            items.append(f"{_key(k)} = {to_lua(value[k], indent + _INDENT, child_order)}")
        return _join(items, indent, as_list=False)
    return _scalar(value)


def _join(items: list[str], indent: str, as_list: bool) -> str:
    if not items:
        return "{}"
    inline = "{ " + ", ".join(items) + " }"
    multiline = any("\n" in item for item in items)
    if not multiline and len(indent) + len(inline) <= LINE_WIDTH:
        return inline
    inner = indent + _INDENT
    return "{\n" + "".join(f"{inner}{item},\n" for item in items) + indent + "}"


def form_to_lua(model: dict, indent: str = "") -> str:
    """The form *model* (a dict as parse_table returns it) as the Lua table
    constructor for opensak.form{…}. Lines after the first are prefixed
    with *indent*. The form table and its field list are always multi-line,
    so each field gets a line of its own."""
    inner = indent + _INDENT
    lines = ["{"]
    for key in _ordered(model, _FORM_ORDER):
        value = model[key]
        if value is None:
            continue
        if key in ("fields", "buttons") and isinstance(value, list) and value:
            lines.append(f"{inner}{key} = {{")
            for item in value:
                lines.append(f"{inner}{_INDENT}{_field_to_lua(item, inner + _INDENT)},")
            lines.append(f"{inner}}},")
        else:
            lines.append(f"{inner}{_key(key)} = {to_lua(value, inner)},")
    lines.append(f"{indent}}}")
    return "\n".join(lines)


def _field_to_lua(item: Any, indent: str) -> str:
    if isinstance(item, dict) and isinstance(item.get("fields"), list) and item["fields"]:
        # A group: its own keys on the first line, its fields one per line.
        inner = indent + _INDENT
        items = [f"{_key(k)} = {to_lua(item[k], inner)}"
                 for k in _ordered(item, _FIELD_ORDER) if k != "fields" and item[k] is not None]
        lines = ["{"]
        head = ", ".join(items)
        if items and "\n" not in head and len(inner) + len(head) < LINE_WIDTH:
            lines.append(f"{inner}{head},")
        else:
            lines += [f"{inner}{entry}," for entry in items]
        lines.append(f"{inner}fields = {{")
        for child in item["fields"]:
            lines.append(f"{inner}{_INDENT}{_field_to_lua(child, inner + _INDENT)},")
        lines.append(f"{inner}}},")
        lines.append(f"{indent}}}")
        return "\n".join(lines)
    return to_lua(item, indent, _FIELD_ORDER)

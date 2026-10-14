"""
src/opensak/macro/helpers.py — the pure helpers behind opensak.re, opensak.text,
opensak.date and opensak.coords.format.

No Lua and no Qt here: every function takes and returns plain Python values
and raises MacroError for bad arguments, so it can be tested on its own.
runtime.py converts the results into Lua tables.

Regular expressions use the third-party `regex` module with a timeout, not
Python's `re`: patterns come from macros, and a backtracking pattern such as
"(x+x+)+y" could otherwise hang the application (see the note at the top of
runtime.py). The syntax is Python's.
"""

from __future__ import annotations

import html.parser
import re
import unicodedata
from datetime import datetime
from typing import Any, Callable, Optional

from opensak.geodesy import format_ch1903, format_utm
from opensak.coords import format_coords
from opensak.macro.errors import MacroError
from opensak.utils.types import CoordFormat

# Upper bound for one regex call — enough for any sane pattern on a cache
# description, short enough that a catastrophic pattern only stalls briefly.
REGEX_TIMEOUT_S = 2.0


# ── Regular expressions ──────────────────────────────────────────────────────


def _compile(pattern: Any, func: str):
    import regex

    if not isinstance(pattern, str):
        raise MacroError(f"{func}: pattern must be a string, got {pattern!r}")
    try:
        return regex.compile(pattern, regex.VERSION0)
    except regex.error as exc:
        raise MacroError(f"{func}: invalid pattern {pattern!r}: {exc}") from None


def _text(value: Any, func: str) -> str:
    if not isinstance(value, str):
        raise MacroError(f"{func}: text must be a string, got {value!r}")
    return value


def _timed(func: str, call: Callable[[], Any]) -> Any:
    try:
        return call()
    except TimeoutError:
        raise MacroError(
            f"{func}: the pattern took longer than {REGEX_TIMEOUT_S:g} s — simplify it"
        ) from None


def re_find(text: Any, pattern: Any) -> Optional[tuple]:
    """(whole match, capture 1, …) of the first match, or None."""
    func = "opensak.re.find"
    rx, s = _compile(pattern, func), _text(text, func)
    m = _timed(func, lambda: rx.search(s, timeout=REGEX_TIMEOUT_S))
    return None if m is None else (m.group(0), *m.groups())


def re_match(text: Any, pattern: Any) -> bool:
    func = "opensak.re.match"
    rx, s = _compile(pattern, func), _text(text, func)
    return _timed(func, lambda: rx.search(s, timeout=REGEX_TIMEOUT_S)) is not None


def re_findall(text: Any, pattern: Any) -> list:
    """Every match: the whole match without capture groups, the capture
    with one group, a list of captures with several (like Python)."""
    func = "opensak.re.findall"
    rx, s = _compile(pattern, func), _text(text, func)
    found = _timed(func, lambda: rx.findall(s, timeout=REGEX_TIMEOUT_S))
    return [list(f) if isinstance(f, tuple) else f for f in found]


def re_replace(text: Any, pattern: Any, repl: Any, count: Any = None) -> tuple[str, int]:
    """(new text, number of replacements). *repl* is a string with \\1 or
    \\g<name> references, or a callable (whole match, captures…) → string;
    a None/False result keeps the match unchanged."""
    import regex

    func = "opensak.re.replace"
    rx, s = _compile(pattern, func), _text(text, func)
    if count is None:
        count = 0
    elif isinstance(count, bool) or not isinstance(count, (int, float)) or count < 0:
        raise MacroError(f"{func}: count must be a number >= 0, got {count!r}")

    if isinstance(repl, str):
        template = repl

        def replacement(m):
            try:
                return m.expand(template)
            except (IndexError, regex.error) as exc:
                raise MacroError(f"{func}: invalid replacement {template!r}: {exc}") from None
    elif callable(repl):
        def replacement(m):
            result = repl(m.group(0), *m.groups())
            if result is None or result is False:
                return m.group(0)
            return lua_tostring(result)
    else:
        raise MacroError(f"{func}: replacement must be a string or a function, got {repl!r}")

    return _timed(func, lambda: rx.subn(replacement, s, count=int(count),
                                        timeout=REGEX_TIMEOUT_S))


def re_split(text: Any, pattern: Any) -> list[str]:
    """The pieces between matches. Empty matches do not split, and capture
    groups are not included."""
    func = "opensak.re.split"
    rx, s = _compile(pattern, func), _text(text, func)
    pieces: list[str] = []
    start = 0
    for m in _timed(func, lambda: list(rx.finditer(s, timeout=REGEX_TIMEOUT_S))):
        if m.start() == m.end():
            continue
        pieces.append(s[start:m.start()])
        start = m.end()
    pieces.append(s[start:])
    return pieces


# ── Text ─────────────────────────────────────────────────────────────────────


_BLOCK_TAGS = {
    "address", "article", "blockquote", "br", "dd", "div", "dl", "dt",
    "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
    "ol", "p", "pre", "section", "table", "tr", "ul",
}
_SKIP_TAGS = {"script", "style", "head", "title"}


class _TextExtractor(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append("\t")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(text: Any) -> str:
    """Strip tags, decode entities, turn block elements into line breaks."""
    s = _text(text, "opensak.text.html_to_text")
    parser = _TextExtractor()
    parser.feed(s)
    parser.close()
    raw = "".join(parser.parts).replace("\xa0", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in raw.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def digit_sum(value: Any) -> int:
    """Sum of all digits in a number or string (other characters ignored)."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise MacroError(f"opensak.text.digit_sum expects a number or string, got {value!r}")
    return sum(int(ch) for ch in lua_tostring(value) if ch.isdecimal() and ch.isascii())


def word_value(value: Any) -> int:
    """A=1 … Z=26, summed; accents are dropped (Ä counts as A), every other
    character is ignored."""
    s = _text(value, "opensak.text.word_value")
    plain = unicodedata.normalize("NFKD", s).upper()
    return sum(ord(ch) - ord("A") + 1 for ch in plain if "A" <= ch <= "Z")


# Not allowed in file names on Windows (and "/" nowhere)
_INVALID_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
}
MAX_NAME_LENGTH = 100


def normalize_name(value: Any) -> str:
    """A name that is safe as a database or file name on every platform."""
    s = _text(value, "opensak.text.normalize_name")
    name = re.sub(r"\s+", " ", unicodedata.normalize("NFC", s))
    name = _INVALID_NAME_RE.sub("_", name).strip(" .")[:MAX_NAME_LENGTH].rstrip(" .")
    if name.split(".", 1)[0].upper() in _RESERVED_NAMES:
        name = f"_{name}"
    return name or "_"


# ── Dates ────────────────────────────────────────────────────────────────────


_DATE_FORMATS = ("%d.%m.%Y", "%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S")


def date_parse(text: Any, fmt: Any = None) -> Optional[int]:
    """Seconds since the epoch, like os.time(); None if *text* does not parse.

    Without *fmt*: ISO 8601 ("2026-10-06", "2026-10-06T14:30:00Z", …) or
    "06.10.2026 [14:30[:00]]". Times without a zone are local time.
    """
    func = "opensak.date.parse"
    s = _text(text, func).strip()
    if fmt is not None and not isinstance(fmt, str):
        raise MacroError(f"{func}: format must be a string, got {fmt!r}")
    try:
        if fmt is not None:
            dt = datetime.strptime(s, fmt)
        else:
            try:
                dt = datetime.fromisoformat(s)
            except ValueError:
                for candidate in _DATE_FORMATS:
                    try:
                        dt = datetime.strptime(s, candidate)
                        break
                    except ValueError:
                        continue
                else:
                    return None
        return int(dt.timestamp())
    except (ValueError, OverflowError, OSError):
        return None


def date_format(t: Any, fmt: Any = None) -> str:
    """Format seconds since the epoch (local time) with strftime codes."""
    func = "opensak.date.format"
    if isinstance(t, bool) or not isinstance(t, (int, float)):
        raise MacroError(f"{func} expects a time in seconds (e.g. os.time()), got {t!r}")
    if fmt is None:
        fmt = "%Y-%m-%d"
    elif not isinstance(fmt, str):
        raise MacroError(f"{func}: format must be a string, got {fmt!r}")
    try:
        return datetime.fromtimestamp(t).strftime(fmt)
    except (ValueError, OverflowError, OSError) as exc:
        raise MacroError(f"{func}: cannot format {t!r} with {fmt!r}: {exc}") from None


# ── Coordinate formats ───────────────────────────────────────────────────────


COORD_FORMATS = ("dmm", "dms", "dd", "utm", "ch1903", "ch1903+")


def format_coordinate(lat: float, lon: float, fmt: Any = None) -> str:
    func = "opensak.coords.format"
    key = "dmm" if fmt is None else str(fmt).strip().lower()
    try:
        if key == "dmm":
            return format_coords(lat, lon, CoordFormat.DMM)
        if key == "dms":
            return format_coords(lat, lon, CoordFormat.DMS)
        if key == "dd":
            return format_coords(lat, lon, CoordFormat.DD)
        if key == "utm":
            return format_utm(lat, lon)
        if key in ("ch1903", "lv03"):
            return format_ch1903(lat, lon)
        if key in ("ch1903+", "lv95"):
            return format_ch1903(lat, lon, lv95=True)
    except ValueError as exc:
        raise MacroError(f"{func}: {exc}") from None
    raise MacroError(f"{func}: unknown format {fmt!r}; valid: {', '.join(COORD_FORMATS)}")


# ── Lua values ───────────────────────────────────────────────────────────────


def lua_tostring(value: Any) -> str:
    """What Lua's tostring() gives for a nil, boolean, number or string."""
    if value is None:
        return "nil"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)

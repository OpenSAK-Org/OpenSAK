"""
src/opensak/export/special_tags.py — GSAK's special tags for GPX/GGZ
export texts (#454, #950).

A template such as ``%name by %by (%dif/%ter)`` is filled in per cache:
each tag is replaced with the cache's value. The tags and their forms follow
GSAK's own special tags (GSAK help, "Special Tags"), so a template can be
copied from GSAK:

* tags are not case sensitive; anything that is not a tag stays as typed,
  so a typo shows up in the file instead of disappearing;
* ``%tag=nn`` keeps the first nn characters of a text tag (``%typ=3``);
* ``%found=xx`` and the other yes/no tags write the first character of
  *xx* for yes and the second for no (``%found=*!``), "Y"/"N" without it;
* ``%lat=M`` / ``%lon=S`` choose decimal degrees (D, the default), decimal
  minutes (M) or degrees, minutes and seconds (S);
* ``%caches_<field>`` (and the older ``%custom_<field>``) is any column of
  GSAK's Caches table by its GSAK name (``%caches_FavPoints``) or a custom
  field (#1028) by its name; ``={Yes|No}`` chooses the text of a
  true/false value;
* ``%notGC`` splits a template: the part before it is used for caches whose
  code starts with "GC", the part after it for all others.

OpenSAK adds ``%LastLog``, ``%LastFound``, ``%FavPoints``, ``%country``,
``%user3`` and ``%user4``, which GSAK only offers through ``%caches_``.
GSAK's tags that need its smart names, its macros, SQL, UTM / British grid
coordinates or a child waypoint (``%smart``, ``%macro``, ``%mtag``,
``%sqlite``, ``%UTMZone``, ``%c_Name``, ``%children``, …) are not supported
and stay as typed; a macro passes a function instead (opensak.export_gpx{}).

Dates are written as YYYY-MM-DD. Qt-free: the GPX/GGZ writer, the export
dialog's previews and Lua macros share it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Optional, Sequence

from opensak.utils.constants import DNF_LOG_TYPES, FOUND_LOG_TYPES

# A cache's custom field values: (name, value) pairs (see
# gps.garmin.CustomData).
CustomValues = Sequence[tuple[str, Any]]

# The template "Suggested" fills in for the extra-info log (#454).
SUGGESTED_FIRST_LOG = (
    "Hint: %Hint\n"
    "Last Log: %LastLog\n"
    "Last Found: %LastFound\n"
    "Favorite Points: %FavPoints"
)

# GSAK's default waypoint description, which OpenSAK writes when no
# description template is set (#700): "Name by Owner (D/T)".
DEFAULT_DESCRIPTION = "%name by %by (%dif/%ter)"

# The tags as listed to the user (dialog help, User Guide), in GSAK's order.
LISTED_TAGS = (
    "%code", "%name", "%by", "%ownerName", "%ownerID", "%typ", "%typ1",
    "%con", "%con1", "%dif", "%dif1", "%dif1a", "%ter", "%ter1", "%ter1a",
    "%hint", "%crypt", "%state", "%county", "%country", "%status",
    "%found", "%dnf", "%flag", "%correct", "%bug", "%foundbyme", "%datepl",
    "%datelf", "%LastLog", "%LastFound", "%FavPoints", "%last4",
    "%user", "%user2", "%user3", "%user4", "%UserNote", "%UserSort",
    "%gcid", "%lat", "%lon", "%dist", "%drop2", "%database", "%centre",
    "%blank", "%notGC", "%caches_<field>",
)


@dataclass(frozen=True)
class TagContext:
    """What the tags need beyond the cache: the export's options."""

    use_corrected: bool = True   # %lat/%lon/%correct use corrected coordinates
    use_miles: bool = False      # %dist in miles
    database: str = ""           # %database
    centre: str = ""             # %centre


DEFAULT_CONTEXT = TagContext()


# ── Values ──────────────────────────────────────────────────────────────────

def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _date(value: Any) -> str:
    return value.strftime("%Y-%m-%d") if isinstance(value, datetime) else ""


def _rating(value: Any) -> str:
    return f"{float(value):g}" if value is not None else ""


def _rating1(value: Any) -> str:
    """GSAK's %dif1: 1=1, 1.5=2, 2=3, …"""
    return str(int(round(float(value) * 2 - 1))) if value is not None else ""


def _rating1a(value: Any) -> str:
    """GSAK's %dif1a: 1, A (1.5), 2, B (2.5), … 5."""
    if value is None:
        return ""
    halves = int(round(float(value) * 2))
    return str(halves // 2) if halves % 2 == 0 else "ABCD"[max(0, min(3, halves // 2 - 1))]


# GSAK's %typ1 letters, matched against OpenSAK's cache type names in order
# (CITO before Event).
_TYPE_LETTERS = (
    ("trash", "C"), ("cito", "C"), ("traditional", "T"), ("multi", "M"),
    ("letterbox", "B"), ("event", "E"), ("celebration", "E"),
    ("locationless", "L"), ("virtual", "V"), ("webcam", "W"),
    ("benchmark", "G"), ("earth", "R"), ("wherigo", "I"), ("unknown", "U"),
    ("mystery", "U"),
)
# GSAK's %con1 letters; any other container is U.
_CONTAINER_LETTERS = {
    "regular": "R", "large": "L", "micro": "M", "small": "S", "virtual": "V",
}


def _type_letter(cache_type: Optional[str]) -> str:
    lowered = (cache_type or "").lower()
    for word, letter in _TYPE_LETTERS:
        if word in lowered:
            return letter
    return "O"


def _hint(cache) -> str:
    raw = getattr(cache, "encoded_hints", None) or ""
    if not raw:
        return ""
    from opensak.hint_detect import split_hint

    try:
        plain, _cipher = split_hint(raw)
    except Exception:
        plain = raw
    return plain


def _crypt(cache) -> str:
    from opensak.hint_detect import rot13

    return rot13(_hint(cache))


def _note(cache):
    return getattr(cache, "user_note", None)


def _corrected(cache) -> Optional[tuple[float, float]]:
    note = _note(cache)
    if note is not None and getattr(note, "is_corrected", False) \
            and note.corrected_lat is not None and note.corrected_lon is not None:
        return note.corrected_lat, note.corrected_lon
    return None


def _coords(cache, ctx: TagContext) -> tuple[Optional[float], Optional[float]]:
    corrected = _corrected(cache) if ctx.use_corrected else None
    if corrected is not None:
        return corrected
    return getattr(cache, "latitude", None), getattr(cache, "longitude", None)


def _format_coord(value: Optional[float], is_lat: bool, fmt: str) -> str:
    """One coordinate in GSAK's %lat/%lon formats: D (decimal degrees, the
    default), M (N47 07.407) or S (N47° 07' 24.42")."""
    if value is None:
        return ""
    fmt = (fmt or "D").upper()
    if fmt not in ("M", "S"):
        return f"{value:.5f}"
    hemi = ("N" if value >= 0 else "S") if is_lat else ("E" if value >= 0 else "W")
    width = 2 if is_lat else 3
    total = abs(value)
    deg = int(total)
    minutes = (total - deg) * 60
    if fmt == "M":
        minutes = round(minutes, 3)
        if minutes >= 60:
            deg, minutes = deg + 1, 0.0
        return f"{hemi}{deg:0{width}d} {minutes:06.3f}"
    whole = int(minutes)
    seconds = round((minutes - whole) * 60, 2)
    if seconds >= 60:
        whole, seconds = whole + 1, 0.0
    if whole >= 60:
        deg, whole = deg + 1, 0
    return f"{hemi}{deg:0{width}d}° {whole:02d}' {seconds:05.2f}\""


def _sorted_logs(cache) -> list:
    logs = [lg for lg in (getattr(cache, "logs", None) or []) if lg.log_date is not None]
    return sorted(logs, key=lambda lg: _naive(lg.log_date), reverse=True)


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None)


def _last_log(cache) -> str:
    stored = getattr(cache, "last_log_date", None)
    if stored is not None:
        return _date(stored)
    logs = _sorted_logs(cache)
    return _date(logs[0].log_date) if logs else ""


def _last_found(cache) -> str:
    stored = getattr(cache, "last_found_date", None)
    if stored is not None:
        return _date(stored)
    for log in _sorted_logs(cache):
        if log.log_type in FOUND_LOG_TYPES:
            return _date(log.log_date)
    return ""


def _last4(cache) -> str:
    """GSAK's %last4: the latest four found (F) / not found (N) logs,
    newest first, padded with 0; other logs don't count."""
    letters = ""
    for log in _sorted_logs(cache):
        if log.log_type in FOUND_LOG_TYPES:
            letters += "F"
        elif log.log_type in DNF_LOG_TYPES:
            letters += "N"
        if len(letters) == 4:
            break
    return letters.ljust(4, "0")


_GC_ALPHABET = "0123456789ABCDEFGHJKMNPQRTVWXYZ"


def gc_id(code: Optional[str]) -> str:
    """The geocaching.com cache number of a GC code (GSAK's %gcid): hex up
    to GCFFFF, base 31 after that. "" for any other code."""
    code = (code or "").upper()
    if not code.startswith("GC") or len(code) < 3:
        return ""
    rest = code[2:]
    if len(rest) <= 4 and all(c in "0123456789ABCDEF" for c in rest):
        return str(int(rest, 16))
    value = 0
    for char in rest:
        digit = _GC_ALPHABET.find(char)
        if digit < 0:
            return ""
        value = value * 31 + digit
    return str(value - 411120)


def _distance(cache, ctx: TagContext) -> str:
    km = getattr(cache, "distance", None)
    if km is None:
        return ""
    return f"{km * 0.621371:.1f} mi" if ctx.use_miles else f"{km:.1f} km"


# Text tags (they take =nn): lower-case name → value(cache, name, ctx).
_TextGetter = Callable[[Any, str, TagContext], str]
_TEXT_TAGS: dict[str, _TextGetter] = {
    "code": lambda c, n, x: _text(c.gc_code),
    "name": lambda c, n, x: n,
    "des": lambda c, n, x: n,   # GSAK's old name for %name
    "by": lambda c, n, x: _text(getattr(c, "placed_by", None)),
    "ownername": lambda c, n, x: _text(getattr(c, "owner_name", None)),
    "ownerid": lambda c, n, x: _text(getattr(c, "owner_id", None)),
    "typ": lambda c, n, x: _text(getattr(c, "cache_type", None)),
    "typ1": lambda c, n, x: _type_letter(getattr(c, "cache_type", None)),
    "con": lambda c, n, x: _text(getattr(c, "container", None)),
    "con1": lambda c, n, x: _CONTAINER_LETTERS.get(
        (getattr(c, "container", None) or "").lower(), "U"),
    "dif": lambda c, n, x: _rating(getattr(c, "difficulty", None)),
    "dif1": lambda c, n, x: _rating1(getattr(c, "difficulty", None)),
    "dif1a": lambda c, n, x: _rating1a(getattr(c, "difficulty", None)),
    "ter": lambda c, n, x: _rating(getattr(c, "terrain", None)),
    "ter1": lambda c, n, x: _rating1(getattr(c, "terrain", None)),
    "ter1a": lambda c, n, x: _rating1a(getattr(c, "terrain", None)),
    "hint": lambda c, n, x: _hint(c),
    "crypt": lambda c, n, x: _crypt(c),
    "state": lambda c, n, x: _text(getattr(c, "state", None)),
    "county": lambda c, n, x: _text(getattr(c, "county", None)),
    "country": lambda c, n, x: _text(getattr(c, "country", None)),
    "status": lambda c, n, x: (
        "Archived" if getattr(c, "archived", False)
        else "Available" if getattr(c, "available", True) else "Unavailable"),
    "foundbyme": lambda c, n, x: _date(getattr(c, "found_date", None))
    if getattr(c, "found", False) else "",
    "datepl": lambda c, n, x: _date(getattr(c, "hidden_date", None)),
    "datelf": lambda c, n, x: _last_found(c),
    "lastlog": lambda c, n, x: _last_log(c),
    "lastfound": lambda c, n, x: _last_found(c),
    "favpoints": lambda c, n, x: _text(getattr(c, "favorite_points", None)),
    "last4": lambda c, n, x: _last4(c),
    "user": lambda c, n, x: _text(getattr(c, "user_data_1", None)),
    "user2": lambda c, n, x: _text(getattr(c, "user_data_2", None)),
    "user3": lambda c, n, x: _text(getattr(c, "user_data_3", None)),
    "user4": lambda c, n, x: _text(getattr(c, "user_data_4", None)),
    "usernote": lambda c, n, x: _text(getattr(_note(c), "note", None)).strip(),
    "usersort": lambda c, n, x: _text(getattr(c, "user_sort", None)),
    "gcid": lambda c, n, x: _text(getattr(c, "gc_cache_id", None)) or gc_id(c.gc_code),
    "dist": lambda c, n, x: _distance(c, x),
    "drop2": lambda c, n, x: _text(c.gc_code)[2:],
    "database": lambda c, n, x: x.database,
    "centre": lambda c, n, x: x.centre,
    "center": lambda c, n, x: x.centre,
    "blank": lambda c, n, x: "",
}

# Yes/no tags (they take =xx): lower-case name → test(cache, ctx).
_YES_NO_TAGS: dict[str, Callable[[Any, TagContext], bool]] = {
    "found": lambda c, x: bool(getattr(c, "found", False)),
    "dnf": lambda c, x: bool(getattr(c, "dnf", False)),
    "flag": lambda c, x: bool(getattr(c, "user_flag", False)),
    "correct": lambda c, x: _corrected(c) is not None,
    "bug": lambda c, x: bool(getattr(c, "trackables", None)
                             or getattr(c, "trackable_count", 0)),
}

_COORD_TAGS = ("lat", "lon")

# GSAK's Caches table columns for %caches_<column>, lower-case → value.
_CACHES_COLUMNS: dict[str, Callable[[Any, str, TagContext], Any]] = {
    "code": lambda c, n, x: c.gc_code,
    "name": lambda c, n, x: n,
    "placedby": lambda c, n, x: getattr(c, "placed_by", None),
    "ownername": lambda c, n, x: getattr(c, "owner_name", None),
    "ownerid": lambda c, n, x: getattr(c, "owner_id", None),
    "cachetype": lambda c, n, x: getattr(c, "cache_type", None),
    "container": lambda c, n, x: getattr(c, "container", None),
    "difficulty": lambda c, n, x: _rating(getattr(c, "difficulty", None)),
    "terrain": lambda c, n, x: _rating(getattr(c, "terrain", None)),
    "country": lambda c, n, x: getattr(c, "country", None),
    "state": lambda c, n, x: getattr(c, "state", None),
    "county": lambda c, n, x: getattr(c, "county", None),
    "favpoints": lambda c, n, x: getattr(c, "favorite_points", None),
    "placeddate": lambda c, n, x: _date(getattr(c, "hidden_date", None)),
    "lastlog": lambda c, n, x: _last_log(c),
    "lastfounddate": lambda c, n, x: _last_found(c),
    "foundbymedate": lambda c, n, x: _date(getattr(c, "found_date", None))
    if getattr(c, "found", False) else "",
    "dnfdate": lambda c, n, x: _date(getattr(c, "dnf_date", None)),
    "userdata": lambda c, n, x: getattr(c, "user_data_1", None),
    "user2": lambda c, n, x: getattr(c, "user_data_2", None),
    "user3": lambda c, n, x: getattr(c, "user_data_3", None),
    "user4": lambda c, n, x: getattr(c, "user_data_4", None),
    "usersort": lambda c, n, x: getattr(c, "user_sort", None),
    "userflag": lambda c, n, x: bool(getattr(c, "user_flag", False)),
    "found": lambda c, n, x: bool(getattr(c, "found", False)),
    "dnf": lambda c, n, x: bool(getattr(c, "dnf", False)),
    "archived": lambda c, n, x: bool(getattr(c, "archived", False)),
    "tempdisabled": lambda c, n, x: not getattr(c, "available", True)
    and not getattr(c, "archived", False),
    "watch": lambda c, n, x: bool(getattr(c, "watch", False)),
    "lock": lambda c, n, x: bool(getattr(c, "locked", False)),
    "ftf": lambda c, n, x: bool(getattr(c, "first_to_find", False)),
    "isowner": lambda c, n, x: bool(getattr(c, "is_owner", False)),
    "ispremium": lambda c, n, x: bool(getattr(c, "premium_only", False)),
    "hascorrected": lambda c, n, x: _corrected(c) is not None,
    "hastravelbug": lambda c, n, x: _YES_NO_TAGS["bug"](c, x),
    "elevation": lambda c, n, x: getattr(c, "elevation", None),
    "distance": lambda c, n, x: _distance(c, x),
    "gcnote": lambda c, n, x: getattr(c, "gc_note", None),
    "guid": lambda c, n, x: getattr(c, "guid", None),
    "numberoflogs": lambda c, n, x: getattr(c, "log_count", None),
    "latitude": lambda c, n, x: _format_coord(_coords(c, x)[0], True, "D"),
    "longitude": lambda c, n, x: _format_coord(_coords(c, x)[1], False, "D"),
    "changed": lambda c, n, x: _date(getattr(c, "last_updated", None)),
}

# Every tag name, longest first so %dif1a wins over %dif1 and %dif.
_ALL_TAGS = sorted(
    list(_TEXT_TAGS) + list(_YES_NO_TAGS) + list(_COORD_TAGS),
    key=len, reverse=True,
)
_FIELD_PREFIXES = ("caches_", "custom_")
_FIELD_NAME_RE = re.compile(r"[A-Za-z0-9_]+")
_NUMBER_ARG_RE = re.compile(r"=(\d+)")
_CHOICE_ARG_RE = re.compile(r"=\{([^}|]*)\|([^}]*)\}")
_NOT_GC_RE = re.compile(r"%notGC", re.IGNORECASE)


def _field_value(value: Any) -> Any:
    """A %caches_ value as text, keeping True/False for ={Yes|No}."""
    if isinstance(value, bool) or value is None:
        return value
    return str(value)


def _custom_lookup(custom: CustomValues, field: str) -> tuple[bool, Any]:
    """(found, value) of the custom field called *field* (any case)."""
    lowered = field.lower()
    for name, value in custom:
        if name.lower() == lowered:
            return True, value
    return False, None


# ── Expansion ───────────────────────────────────────────────────────────────

@dataclass
class _Result:
    text: str
    tags: int = 0      # tags recognised
    filled: int = 0    # of those, how many gave a non-empty value


def _expand(template: str, cache, name: str, custom: CustomValues,
            ctx: TagContext) -> _Result:
    out: list[str] = []
    tags = filled = 0
    i, n = 0, len(template)
    while i < n:
        j = template.find("%", i)
        if j < 0:
            out.append(template[i:])
            break
        out.append(template[i:j])
        value, end = _tag_at(template, j + 1, cache, name, custom, ctx)
        if value is None:
            out.append("%")
            i = j + 1
            continue
        tags += 1
        if value:
            filled += 1
        out.append(value)
        i = end
    return _Result("".join(out), tags, filled)


def _tag_at(template: str, pos: int, cache, name: str, custom: CustomValues,
            ctx: TagContext) -> tuple[Optional[str], int]:
    """The value of the tag starting at *pos* (just after its "%") and the
    position after it, or (None, pos) when there is no tag there."""
    lowered = template[pos:pos + 32].lower()
    for prefix in _FIELD_PREFIXES:
        if lowered.startswith(prefix):
            m = _FIELD_NAME_RE.match(template, pos + len(prefix))
            if not m:
                return None, pos
            value, end = _field_tag(template, m.end(), m.group(0), cache, name, custom, ctx)
            return (value, end) if value is not None else (None, pos)
    for tag in _ALL_TAGS:
        if not lowered.startswith(tag):
            continue
        end = pos + len(tag)
        if tag in _YES_NO_TAGS:
            yes = _YES_NO_TAGS[tag](cache, ctx)
            if template.startswith("=", end) and len(template) >= end + 3:
                pair = template[end + 1:end + 3]
                return (pair[0] if yes else pair[1]), end + 3
            return ("Y" if yes else "N"), end
        if tag in _COORD_TAGS:
            fmt = "D"
            if template.startswith("=", end) and end + 1 < len(template) \
                    and template[end + 1].upper() in "DMSU":
                fmt = template[end + 1].upper()
                end += 2
            lat, lon = _coords(cache, ctx)
            return _format_coord(lat if tag == "lat" else lon, tag == "lat", fmt), end
        value = _TEXT_TAGS[tag](cache, name, ctx)
        m = _NUMBER_ARG_RE.match(template, end)
        if m:
            return value[:int(m.group(1))], m.end()
        return value, end
    return None, pos


def _field_tag(template: str, end: int, field: str, cache, name: str,
               custom: CustomValues, ctx: TagContext) -> tuple[Optional[str], int]:
    found, value = _custom_lookup(custom, field)
    if not found:
        getter = _CACHES_COLUMNS.get(field.lower())
        if getter is None:
            return None, end   # unknown field: the caller keeps it as typed
        value = _field_value(getter(cache, name, ctx))
    m = _CHOICE_ARG_RE.match(template, end)
    if m:
        truthy = value if isinstance(value, bool) else (
            str(value).strip().lower() in ("1", "true", "yes", "y")
            if value is not None else False)
        return (m.group(1) if truthy else m.group(2)), m.end()
    if isinstance(value, bool):
        text = "1" if value else "0"
    else:
        text = _text(value)
    m = _NUMBER_ARG_RE.match(template, end)
    if m:
        return text[:int(m.group(1))], m.end()
    return text, end


def _for_code(template: str, code: Optional[str]) -> str:
    """The part of *template* that applies to *code* (see %notGC)."""
    parts = _NOT_GC_RE.split(template, maxsplit=1)
    if len(parts) == 1:
        return template
    return parts[0] if (code or "").upper().startswith("GC") else parts[1]


def expand(template: str, cache, *, name: Optional[str] = None,
           custom: CustomValues = (), ctx: TagContext = DEFAULT_CONTEXT) -> str:
    """*template* with its special tags replaced by the values of *cache*.
    *name* is the cache name to use for %name (a macro may have renamed the
    cache), *custom* its custom field values for %caches_<field>."""
    name = (cache.name or "") if name is None else name
    return _expand(_for_code(template, cache.gc_code), cache, name, custom, ctx).text


def expand_lines(template: str, cache, *, name: Optional[str] = None,
                 custom: CustomValues = (), ctx: TagContext = DEFAULT_CONTEXT) -> str:
    """Like expand(), line by line: a line whose tags all came out empty is
    left out (no "Hint:" line for a cache without a hint), as are empty
    lines at the start and end."""
    name = (cache.name or "") if name is None else name
    lines = []
    for line in _for_code(template, cache.gc_code).replace("\r\n", "\n").split("\n"):
        result = _expand(line, cache, name, custom, ctx)
        if result.tags and not result.filled:
            continue
        lines.append(result.text.rstrip())
    return "\n".join(lines).strip("\n")


def uses_custom_fields(templates: Iterable[str]) -> bool:
    """True if any of *templates* may read a custom field (%caches_ or
    %custom_), so the export loads the custom values."""
    return any(prefix in (t or "").lower() for t in templates
               for prefix in ("%caches_", "%custom_"))


def remove_chars(text: str, chars: str) -> str:
    """*text* without any of *chars* (GSAK's "problem characters")."""
    if not chars:
        return text
    return text.translate({ord(c): None for c in chars})


class UniqueNames:
    """Waypoint names kept unique within one file after cutting them to a
    maximum length: a name already used ends in a number instead (ABCD →
    ABC1, ABC2, …), still within the length."""

    def __init__(self, max_chars: int = 0) -> None:
        self.max_chars = max_chars
        self._used: set[str] = set()

    def take(self, name: str) -> str:
        if self.max_chars:
            name = name[:self.max_chars]
        candidate = name
        number = 1
        while candidate.upper() in self._used:
            suffix = str(number)
            keep = len(name) if not self.max_chars else self.max_chars - len(suffix)
            candidate = name[:max(0, keep)] + suffix
            number += 1
        self._used.add(candidate.upper())
        return candidate


def export_context(use_corrected: bool = True) -> TagContext:
    """The TagContext of an export now: the active database, the centre
    point and the distance unit from Settings."""
    database = centre = ""
    use_miles = False
    try:
        from opensak.export.file_export import active_database_name
        from opensak.gui.settings import get_settings

        settings = get_settings()
        database = active_database_name()
        centre = settings.active_home_name or ""
        use_miles = bool(settings.use_miles)
    except Exception:
        pass
    return TagContext(use_corrected=use_corrected, use_miles=use_miles,
                      database=database, centre=centre)


class ExportTemplates:
    """The #454/#950 templates of one GPX/GGZ export (see GpxContent),
    filled in per cache. Keeps the waypoint names unique within the file,
    so one instance per file."""

    def __init__(self, content, ctx: Optional[TagContext] = None,
                 use_corrected: bool = True) -> None:
        self.content = content
        # Built on first use (see ctx): an export without templates never
        # reads Settings or the database list.
        self._ctx = ctx
        self._use_corrected = use_corrected
        self._names = (
            UniqueNames(content.name_max_chars)
            if content.name_template or content.name_max_chars else None
        )
        # The custom field values are only loaded when a template may use
        # them (or GSAK's fields are written, #1169).
        self.uses_custom = uses_custom_fields(
            (content.name_template, content.desc_template, content.first_log))
        # The cache name of the cache last written (see cache_name()): the
        # GGZ index repeats it.
        self.current_name = ""

    @property
    def ctx(self) -> TagContext:
        if self._ctx is None:
            self._ctx = export_context(self._use_corrected)
        return self._ctx

    def _clean(self, text: str) -> str:
        return remove_chars(text, self.content.remove_chars).strip()

    def wpt_name(self, cache, name: str, custom: CustomValues = ()) -> str:
        """The cache's <wpt> <name>: the GC code, or the name template,
        without the removed characters, cut and kept unique."""
        code = cache.gc_code or ""
        if self._names is None:
            return code
        text = code
        if self.content.name_template:
            text = expand(self.content.name_template, cache, name=name,
                          custom=custom, ctx=self.ctx)
        return self._names.take(self._clean(text) or code)

    def description(self, cache, name: str, custom: CustomValues, default: str) -> str:
        """The cache's <wpt> <desc>: *default* ("Name by Owner (D/T)") or
        the description template."""
        if self.content.desc_template:
            return self._clean(expand(self.content.desc_template, cache, name=name,
                                      custom=custom, ctx=self.ctx))
        return self._clean(default) if self.content.remove_chars else default

    def cache_name(self, cache, name: str, custom: CustomValues = ()) -> str:
        """The cache name (groundspeak:name, the GGZ index): the
        description template with desc_for_cache_name, else *name*."""
        if self.content.desc_for_cache_name and self.content.desc_template:
            return self._clean(expand(self.content.desc_template, cache, name=name,
                                      custom=custom, ctx=self.ctx)) or name
        return self._clean(name) if self.content.remove_chars else name

    def first_log(self, cache, name: str, custom: CustomValues = ()) -> str:
        """The extra-info log's text, or "" for no log."""
        if not self.content.first_log:
            return ""
        return expand_lines(self.content.first_log, cache, name=name,
                            custom=custom, ctx=self.ctx)

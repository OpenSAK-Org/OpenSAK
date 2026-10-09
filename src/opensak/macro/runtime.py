"""
src/opensak/macro/runtime.py — Lua macro runtime (proof of concept).

Runs a user's Lua script in a sandboxed interpreter and exposes a small
`opensak` API table to it. The runtime is kept free of Qt: everything that
touches the main window goes through a MacroHost, so the runtime can be
unit-tested with a fake host.

The Lua API is defined once, in API and FILTER_KEY_DOCS below: run() builds
the `opensak` table from API, and docs/macros/api.md plus the Lua Language
Server stub macros/types/opensak.lua are generated from both
(python scripts/generate_macro_api_docs.py). test_macro_api_docs fails when
a function is exposed without documentation or a generated file is stale.

Known limitation (#938 step 4): the instruction limit only counts Lua VM
instructions, not work inside C functions. Lua pattern matching backtracks
in C, so e.g. string.rep("a", 100):find(".-.-.-.-.-b") runs ~12 s despite
instruction_limit=100_000, and longer subjects take minutes. The memory
limit does not help either (matching allocates nothing). Consequences:
  * The opensak.re functions therefore use the `regex` module with its
    timeout= argument, not Python's `re` (see helpers.py).
  * A worker thread keeps the GUI responsive and lets a Cancel button
    abandon the run, but cannot stop a call that is already running; only a
    subprocess can be terminated hard. This belongs to the threading decision.

"""

from __future__ import annotations

import csv
import io
import locale
import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Protocol

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from opensak.filters.engine import (
    DATETIME_FILTER_FIELDS,
    ArchivedFilter,
    AttributeFilter,
    AvailabilityFilter,
    AvailableFilter,
    CacheTypeFilter,
    ContainerFilter,
    CountryFilter,
    CountyFilter,
    DateFilter,
    DifficultyFilter,
    DirectionFilter,
    DistanceFilter,
    DnfFilter,
    ElevationFilter,
    FavoritePointsFilter,
    FilterProfile,
    FilterSet,
    FoundFilter,
    FtfFilter,
    GcCodeFilter,
    GcNoteFilter,
    HasCorrectedFilter,
    HasTrackableFilter,
    LinePolygonFilter,
    LockedFilter,
    NameFilter,
    NoCorrectedFilter,
    NonPremiumFilter,
    NotFoundFilter,
    OwnerFilter,
    PlacedByFilter,
    PremiumFilter,
    SortSpec,
    StateFilter,
    TerrainFilter,
    TextSearchFilter,
    UserData1Filter,
    UserData2Filter,
    UserData3Filter,
    UserData4Filter,
    UserFlagFilter,
    UserNoteFilter,
    WhereClauseFilter,
    apply_filters_auto,
)
from opensak import __version__, geodesy
from opensak.coords import parse_coords
from opensak.db.database import SCHEMA_VERSION, get_engine
from opensak.db.manager import DatabaseInfo, get_db_manager
from opensak.db.transfer import IF_EXISTS, transfer_caches
from opensak.export.cache_text import ExportText
from opensak.export.file_export import EXPORT_FORMATS, select_for_export, write_export_file
from opensak.export.file_export_settings import (
    FileExportProfile,
    FileExportSettings,
    expand_file_name,
)
from opensak.filters.line_polygon import LineShape, parse_point, read_points_file
from opensak.hint_detect import rot13
from opensak.macro import helpers
from opensak.macro.errors import MacroError
from opensak.db.database import get_session
from opensak.macro.cache_data import (
    CACHE_FIELDS,
    CacheField,
    UnknownField,
    iter_records,
    load_description,
    load_records,
    resolve_fields,
)
from opensak.macro.permissions import (
    FolderAccessDenied,
    FolderNotApproved,
    FolderPermission,
    approvable_folder,
    check_access,
    grant_permanently,
    load_permissions,
    macros_dir,
    protected_reason,
    resolve_path,
    temp_dir,
    with_grant,
)
from opensak.macro.sql import (
    MAX_ROWS,
    QUERY_TIMEOUT_S,
    ReadOnlyDatabase,
    SqlError,
    connect_read_only,
)
from opensak.utils.constants import ATTRIBUTES, CACHE_TYPES

logger = logging.getLogger(__name__)

# A runaway `while true do end` would freeze the GUI thread, so the script is
# aborted after this many Lua VM instructions.
DEFAULT_INSTRUCTION_LIMIT = 50_000_000
# Upper bound for the Lua heap, so e.g. string.rep("x", 1e10) cannot exhaust RAM.
DEFAULT_MEMORY_LIMIT = 256 * 1024 * 1024
# opensak.choose_file(): longest accepted title / file type filter
MAX_CHOOSE_FILE_TEXT = 200
# opensak.read_csv() refuses larger files — it is meant for small lists
# (solved puzzles, corrections), not for bulk imports.
MAX_CSV_BYTES = 10 * 1024 * 1024
# opensak.sleep(): longest single pause, and total pause time per run (the
# instruction limit does not count time spent sleeping).
MAX_SLEEP_MS = 10_000
SLEEP_BUDGET_S = 60.0
# opensak.export_gpx{}: the keys of its table, and what to do with an
# existing file.
EXPORT_KEYS = frozenset({
    "path", "format", "corrected", "max", "rename", "description", "pois",
    "if_exists", "target", "device",
})
EXPORT_IF_EXISTS = ("overwrite", "skip", "ask")
# Formats a Garmin device reads from its GPX / GGZ folder.
DEVICE_FORMATS = ("gpx", "ggz")
# opensak.exit(code): highest accepted exit code (portable process exit codes)
MAX_EXIT_CODE = 255

# Held while a macro runs: one macro at a time, whether started in the macro
# window or from the command line (#1013). A dialog opened by a macro runs a
# nested event loop on the same thread, so this must not be reentrant.
_RUN_LOCK = threading.Lock()


class MacroBusy(MacroError):
    """Another macro is still running."""


def macro_running() -> bool:
    """True while a macro runs."""
    return _RUN_LOCK.locked()



@contextmanager
def _c_locale() -> Iterator[None]:
    """Keep LC_NUMERIC and LC_CTYPE at "C" while a macro runs (#1015).

    Lua turns numbers into text (tostring, "..", string.format) with the C
    library, which follows LC_NUMERIC. Qt sets the locale from the system,
    so on e.g. a Danish or German system 2.5 would become "2,5".

    Lua strings are UTF-8 bytes, and string.upper/lower and %a-style
    patterns classify each byte by LC_CTYPE. Python sets that from the
    system; with a single-byte code page (e.g. Windows cp1252) the lead
    byte of a Chinese or Arabic character counts as a Latin letter and
    gets case-mapped, which breaks the UTF-8. In "C" only ASCII letters
    are touched.

    Qt and Python do their own number and text handling and are not
    affected.
    """
    categories = (locale.LC_NUMERIC, locale.LC_CTYPE)
    previous = [(cat, locale.setlocale(cat)) for cat in categories]
    for cat in categories:
        locale.setlocale(cat, "C")
    try:
        yield
    finally:
        for cat, value in previous:
            locale.setlocale(cat, value)


_TEXT_FILTERS = {
    "name": NameFilter,
    "code": GcCodeFilter,
    "owner": OwnerFilter,
    "placed_by": PlacedByFilter,
    "country": CountryFilter,
    "state": StateFilter,
    "county": CountyFilter,
    "user_data1": UserData1Filter,
    "user_data2": UserData2Filter,
    "user_data3": UserData3Filter,
    "user_data4": UserData4Filter,
    "gc_note": GcNoteFilter,
    "note": UserNoteFilter,
}
# key → (filter for true, filter for false)
_FLAG_FILTERS: dict[str, tuple[Callable[[], Any], Callable[[], Any]]] = {
    "corrected": (HasCorrectedFilter, NoCorrectedFilter),
    "user_flag": (lambda: UserFlagFilter(True), lambda: UserFlagFilter(False)),
    "locked": (lambda: LockedFilter(True), lambda: LockedFilter(False)),
    "dnf": (lambda: DnfFilter(True), lambda: DnfFilter(False)),
    "ftf": (lambda: FtfFilter(True), lambda: FtfFilter(False)),
    "premium": (PremiumFilter, NonPremiumFilter),
    "archived": (ArchivedFilter, lambda: AvailabilityFilter(True, True, False)),
    "has_trackables": (HasTrackableFilter,
                       lambda: FilterSet(negate=True).add(HasTrackableFilter())),
}
# key → DateFilter field
_DATE_FILTERS = {
    "hidden": "hidden_date",
    "found_date": "found_date",
    "last_gpx_update": "last_gpx_update",
}
FILTER_KEYS = sorted(
    {
        "type",
        "container",
        "difficulty",
        "terrain",
        "found",
        "available",
        "near",
        "distance",
        "bearing",
        "owned",
        "attributes",
        "favorites",
        "elevation",
        "text",
        "polygon",
        "codes",
        "where",
        "mode",
        "label",
    }
    | set(_TEXT_FILTERS) | set(_FLAG_FILTERS) | set(_DATE_FILTERS)
)
# opensak.sort(): cache field name → SortSpec field. Only fields the
# database query orders by (not the grid-only placeholders in SORT_FIELDS),
# so opensak.caches() runs in the same order as the grid.
SORT_KEYS = {
    "name": "name",
    "code": "gc_code",
    "type": "cache_type",
    "container": "container",
    "difficulty": "difficulty",
    "terrain": "terrain",
    "hidden": "hidden_date",
    "placed_by": "placed_by",
    "country": "country",
    "state": "state",
    "county": "county",
    "found": "found",
    "found_date": "found_date",
    "dnf": "dnf",
    "dnf_date": "dnf_date",
    "ftf": "first_to_find",
    "archived": "archived",
    "premium": "premium_only",
    "distance": "distance",
    "bearing": "bearing",
    "favorite_points": "favorite_points",
    "trackable_count": "trackables",
    "user_flag": "user_flag",
    "locked": "locked",
    "user_sort": "user_sort",
    "user_data1": "user_data_1",
    "user_data2": "user_data_2",
    "user_data3": "user_data_3",
    "user_data4": "user_data_4",
}


@dataclass(frozen=True)
class FilterKeyDoc:
    """Documentation of one or more opensak.filter{} keys."""

    keys: tuple[str, ...]
    # Lua Language Server type of the value
    type: str
    value: str
    description: str


# Every key in FILTER_KEYS must be documented here (checked by a test).
FILTER_KEY_DOCS: tuple[FilterKeyDoc, ...] = (
    FilterKeyDoc(("type",), "string|string[]",
                 '"Traditional" | {"Traditional", "Multi-cache", ...}',
                 'Cache type(s); the " Cache" suffix may be left out.'),
    FilterKeyDoc(("container",), "string|string[]",
                 '"Small" | {"Micro", "Small", ...}', "Container size(s)."),
    FilterKeyDoc(("difficulty",), "number|number[]", "2 | {1, 2.5}",
                 "Exact value or {min, max}."),
    FilterKeyDoc(("terrain",), "number|number[]", "2 | {1, 2.5}",
                 "Exact value or {min, max}."),
    FilterKeyDoc(("found",), "boolean", "true | false",
                 "Only found or only unfound caches."),
    FilterKeyDoc(("available",), "boolean", "true",
                 "Only available caches (not disabled or archived)."),
    FilterKeyDoc(("archived",), "boolean", "true | false",
                 "Only archived caches, or only caches that are not archived."),
    FilterKeyDoc(("corrected",), "boolean", "true | false",
                 "With or without corrected coordinates."),
    FilterKeyDoc(("user_flag", "locked", "dnf", "ftf", "premium"), "boolean",
                 "true | false", "That flag set, or not set."),
    FilterKeyDoc(("has_trackables",), "boolean", "true | false",
                 "With or without trackables in the cache."),
    FilterKeyDoc(("owned",), "boolean", "true | false",
                 "Owned by you (owner = your geocaching.com username in "
                 "Settings), or not."),
    FilterKeyDoc(("near",), "table",
                 '{lat = 47.37, lon = 8.54, km = 10} | {point = "Home", km = 10}',
                 "Within `km` of a coordinate or of a saved centre point."),
    FilterKeyDoc(("distance",), "number|number[]", "25 | {5, 25} | {10, nil}",
                 "Km from the active centre point: at most a number, or "
                 "{min, max} where nil leaves a side open."),
    FilterKeyDoc(("bearing",), "number[]", "{45, 135} | {315, 45}",
                 "Bearing from the active centre point, clockwise from the "
                 "first to the second value."),
    FilterKeyDoc(("favorites", "elevation"), "number|number[]",
                 "10 | {10, nil} | {nil, 500}",
                 "Favourite points / elevation in metres: exact value, or "
                 "{min, max} where nil leaves a side open."),
    FilterKeyDoc(tuple(_DATE_FILTERS), "string[]",
                 '{"2020-01-01", "2020-12-31"} | {nil, "2026-09-01"}',
                 'Date range {from, to}, both inclusive, "YYYY-MM-DD"; nil '
                 "leaves a side open. `last_gpx_update` also takes a time, "
                 '"2026-09-01T18:00".'),
    FilterKeyDoc(("attributes",), "string|string[]",
                 '"Dogs" | {"Dogs", "-Night cache", 13}',
                 "Attributes the cache must have (all of them): English "
                 "name or Groundspeak id; a leading `-` means the attribute's "
                 '"no" form (e.g. "-Dogs" = no dogs allowed).'),
    FilterKeyDoc(tuple(_TEXT_FILTERS), "string", '"text"',
                 '"Contains" match on that field (`note` = your local note).'),
    FilterKeyDoc(("text",), "string", '"Brücke"',
                 "Full-text search in description, logs and notes."),
    FilterKeyDoc(("polygon",), "string|table",
                 '"area.kml" | {{47.1, 8.1}, {47.2, 8.1}, {47.2, 8.3}}',
                 "Inside a polygon: a file (as for opensak.coords.inside) or "
                 "a table of points."),
    FilterKeyDoc(("codes",), "string[]", '{"GC1", "GC2"}',
                 "Exactly these GC codes, e.g. from an opensak.sql() result."),
    FilterKeyDoc(("where",), "string", '"SQL WHERE clause"',
                 "Raw clause against the caches table."),
    FilterKeyDoc(("mode",), '"AND"|"OR"', '"OR"',
                 "How the criteria are combined (default AND)."),
    FilterKeyDoc(("label",), "string", '"text"',
                 'Shown in the toolbar (optional, default "Macro").'),
)

# Instruction budget + removal of globals that give file/process access, load
# other code, or bridge back into Python.
#
# The instruction limit is enforced by a count hook, which needs care:
#   * The abort is an ordinary Lua error, so pcall/xpcall could catch it.
#     Once the budget is spent the hook therefore becomes "sticky" (fires on
#     every instruction), so the script cannot execute anything after it.
#   * Hooks are per thread (coroutine) in Lua, so coroutine.create/wrap are
#     replaced to install the hook in every new coroutine as well.
#   * The budget is shared by all threads, so spreading the work over many
#     coroutines does not multiply it.
#
# The chunk returns two functions for the runtime:
#   * stop(msg) makes the hook sticky with *msg* right away — opensak.exit()
#     and a cancelled run end the macro this way, so pcall cannot catch it.
#   * where() returns "macro.lua:12: " for the macro line that called the
#     API function currently running, so API errors name it like Lua's own
#     errors do (luaL_where; level 3 = where, the API function, its caller).
_SANDBOX_SETUP = """
local limit = ...
local sethook, getinfo = debug.sethook, debug.getinfo
local co_create, co_resume = coroutine.create, coroutine.resume
local pack, unpack = table.pack, table.unpack
local main = coroutine.running()
local step = math.min(limit, 1000)
local used, tripped, stopped = 0, false, nil
local function hook()
  if stopped then
    sethook(hook, "", 1)
    sethook(main, hook, "", 1)
    error(stopped, 0)
  end
  if not tripped then
    used = used + step
    if used < limit then return end
    tripped = true
  end
  sethook(hook, "", 1)
  sethook(main, hook, "", 1)
  error("macro aborted: instruction limit reached", 2)
end
sethook(hook, "", step)
coroutine.create = function(f)
  local co = co_create(f)
  sethook(co, hook, "", (tripped or stopped) and 1 or step)
  return co
end
coroutine.wrap = function(f)
  local co = coroutine.create(f)
  return function(...)
    local r = pack(co_resume(co, ...))
    if not r[1] then error(r[2], 0) end
    return unpack(r, 2, r.n)
  end
end
-- Lua runs xpcall message handlers and __gc finalizers with hooks disabled,
-- so an endless loop there could not be stopped. The handler is therefore
-- called after the stack has unwound, and finalizers are not allowed.
local pcall, raw_setmetatable, rawget = pcall, setmetatable, rawget
xpcall = function(f, handler, ...)
  local r = pack(pcall(f, ...))
  if r[1] then return unpack(r, 1, r.n) end
  return false, handler(r[2])
end
setmetatable = function(t, mt)
  if type(mt) == "table" and rawget(mt, "__gc") ~= nil then
    error("__gc metamethods are not allowed in macros", 2)
  end
  return raw_setmetatable(t, mt)
end
local os_time, os_date, os_clock = os.time, os.date, os.clock
os = { time = os_time, date = os_date, clock = os_clock }
io, debug, package, require, dofile, loadfile, load, collectgarbage, python = nil
local function stop(msg)
  stopped = msg
  sethook(hook, "", 1)
  sethook(main, hook, "", 1)
end
local function where()
  local info = getinfo(3, "Sl")
  if info and info.currentline > 0 then
    return info.short_src .. ":" .. info.currentline .. ": "
  end
  return ""
end
return stop, where
"""


class FolderApproval(Enum):
    """The user's answer when a macro needs a folder that is not permitted."""

    ONCE = "once"       # for the rest of this run
    ALWAYS = "always"   # added to Settings → Folder permissions
    DENY = "deny"


class MacroHost(Protocol):
    """What a macro may do to the running application."""

    def apply_filter(self, filterset: FilterSet, label: str) -> int:
        """Apply *filterset* to the cache list; return the match count.

        When nothing matches, the current view must be left unchanged and 0
        returned (same as GSAK's MFILTER / $_FilterCount behaviour).
        """

    def clear_filter(self) -> None:
        """Remove the active filter."""

    def cache_count(self) -> int:
        """Number of caches matching the active filter.

        Must be up to date right after apply_filter()/clear_filter(), even if
        the host refreshes its view asynchronously — i.e. ask the database,
        not the UI.
        """

    def set_corrected_coords(
        self, gc_code: str, lat: Optional[float], lon: Optional[float]
    ) -> bool:
        """Set (or clear, with lat/lon = None) corrected coordinates.

        Returns False if the cache is not in the database. The host should
        refresh whatever shows the cache (table row, map pin, detail panel),
        but may defer that to end_macro() so a macro changing thousands of
        caches does not refresh the view thousands of times.
        """

    def filtered_caches(self) -> list:
        """The caches matching the active filter, in the order shown.

        Like cache_count(), must be up to date right after apply_filter()/
        clear_filter() — ask the database, not the UI.
        """

    def current_code(self) -> Optional[str]:
        """GC code of the cache selected in the grid (None = no selection)."""

    def selected_codes(self) -> list[str]:
        """GC codes of all selected grid rows, in grid order."""

    def filter_name(self) -> str:
        """Name of the active filter ("" = none) — the {filter} variable of
        an export file name."""

    def set_sort(self, sort: SortSpec) -> None:
        """Sort the cache list, like a click on a column header (also when
        the column is hidden). filtered_caches() must return the new order
        right away."""

    def database_name(self) -> str:
        """Name of the active database — the {database} variable."""

    def center_name(self) -> str:
        """Name of the active centre point ("" = none) — the {center}
        variable."""

    def switch_database(self, name: str) -> None:
        """Make the database *name* active and clear the active filter.

        Raises if the database cannot be opened; the previous one then
        stays active."""

    def database_list_changed(self) -> None:
        """A database was added to the list (e.g. the toolbar dropdown
        needs reloading)."""

    def caches_removed(self) -> None:
        """Caches were moved out of the active database. The host should
        reload the view, but may defer that to end_macro()."""

    def confirm(self, message: str) -> bool:
        """Ask the user a Yes/No question; True on Yes."""

    def approve_folder(self, target: Path, folder: Path, write: bool) -> FolderApproval:
        """Ask the user whether the macro may read (or write) *target*, by
        permitting *folder* for this run only or always.

        Must be OpenSAK's own dialog showing the full path — never anything
        the macro can word or answer itself. Read and write are asked
        separately. Anything but ONCE or ALWAYS counts as DENY.
        """

    def choose_file(
        self, title: str, file_filter: str, save: bool, start_dir: Path
    ) -> Optional[Path]:
        """Let the user pick a file to open (or, with *save*, a file to
        write) in OpenSAK's file dialog; None if cancelled. *title* and
        *file_filter* come from the macro and may be empty."""

    def end_macro(self) -> None:
        """Called once after every run, also when the macro failed — apply
        any refreshes deferred while it was running."""


# ── Lua table → FilterSet ────────────────────────────────────────────────────


def _as_list(value: Any) -> list:
    """A Lua value that may be a scalar or an array table → Python list."""
    if isinstance(value, (list, tuple)):
        return list(value)
    if hasattr(value, "values"):  # lupa LuaTable
        return list(value.values())
    return [value]


def _resolve_cache_type(name: str) -> str:
    """Accept both "Traditional Cache" and the short "Traditional"."""
    wanted = str(name).strip().lower()
    for full in CACHE_TYPES:
        if full.lower() in (wanted, f"{wanted} cache"):
            return full
    raise MacroError(f"unknown cache type {name!r}")


def _range(key: str, value: Any) -> tuple[float, float]:
    items = _as_list(value)
    try:
        if len(items) == 1:
            return float(items[0]), float(items[0])
        if len(items) == 2:
            return float(items[0]), float(items[1])
    except (TypeError, ValueError):
        pass
    raise MacroError(f"{key} must be a number or {{min, max}}, got {value!r}")


def _pair(value: Any) -> Optional[tuple[Any, Any]]:
    """{a, b} → (a, b), where either may be nil — so read by index: a Lua
    {nil, 5} has no [1], and its values() would be just (5,)."""
    if isinstance(value, (list, tuple)):
        items = list(value)
    elif hasattr(value, "items"):
        d = dict(value.items())
        if not set(d) <= {1, 2}:
            return None
        items = [d.get(1), d.get(2)]
    else:
        return None
    if len(items) != 2:
        return None
    return items[0], items[1]


def _open_range(key: str, value: Any, number_op: str) -> tuple[str, float, float]:
    """A number (→ *number_op*) or {min, max} with nil for an open side →
    (DISTANCE_OPS operator, value1, value2)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return number_op, float(value), 0.0
    pair = _pair(value)
    if pair is not None:
        lo, hi = (_number(v) if v is not None else None for v in pair)
        if (lo is not None or pair[0] is None) and (hi is not None or pair[1] is None):
            if lo is not None and hi is not None:
                return "between", lo, hi
            if lo is not None:
                return "at_least", lo, 0.0
            if hi is not None:
                return "at_most", hi, 0.0
    raise MacroError(f"{key} must be a number or {{min, max}} (nil = open), got {value!r}")


def _date_bound(key: str, value: Any, with_time: bool) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            pass
        else:
            return parsed if with_time else parsed.date()
    raise MacroError(f'{key}: dates must be "YYYY-MM-DD", got {value!r}')


def _date_filter(key: str, field: str, value: Any) -> DateFilter:
    pair = _pair(value)
    if pair is None or pair == (None, None):
        raise MacroError(f'{key} must be {{from, to}}, e.g. {{"2020-01-01", nil}}, got {value!r}')
    with_time = field in DATETIME_FILTER_FIELDS
    lo, hi = (_date_bound(key, v, with_time) for v in pair)
    if lo is not None and hi is not None:
        return DateFilter(field, "between", lo, hi)
    if lo is not None:
        return DateFilter(field, "on_or_after", lo)
    return DateFilter(field, "on_or_before", hi)


def _bool_value(key: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise MacroError(f"{key} must be true or false, got {value!r}")
    return value


def _attribute_names() -> dict[str, int]:
    """Lower-cased English attribute names and ids → Groundspeak id."""
    from opensak.lang.en import STRINGS
    names: dict[str, int] = {}
    for attr_id, key in ATTRIBUTES:
        names[str(attr_id)] = attr_id
        names[key.removeprefix("attr_")] = attr_id
        if key in STRINGS:
            names[STRINGS[key].lower()] = attr_id
    return names


def _attribute_filter(value: Any) -> AttributeFilter:
    text = str(int(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        else str(value).strip()
    is_on = not text.startswith("-")
    name = text.lstrip("-").strip().lower()
    attr_id = _attribute_names().get(name)
    if attr_id is None:
        raise MacroError(f"attributes: unknown attribute {value!r}")
    return AttributeFilter(attr_id, is_on)


def _active_center() -> tuple[float, float]:
    from opensak.gui.settings import get_settings
    settings = get_settings()
    return settings.home_lat, settings.home_lon


def _near_filter(value: Any) -> DistanceFilter:
    if not hasattr(value, "items") and not isinstance(value, dict):
        raise MacroError('near must be a table, e.g. { lat = 47.37, lon = 8.54, km = 10 }')
    opts = dict(value.items())
    unknown = set(opts) - {"lat", "lon", "point", "km"}
    if unknown:
        raise MacroError(f"near: unknown key(s) {sorted(unknown)}; valid: lat, lon, point, km")
    km = _number(opts.get("km"))
    if km is None or km < 0:
        raise MacroError(f"near needs km, a distance >= 0, got {opts.get('km')!r}")
    if "point" in opts:
        from opensak.gui.settings import get_settings
        name = str(opts["point"])
        point = next((p for p in get_settings().home_points if p.name == name), None)
        if point is None:
            raise MacroError(f"near: no centre point named {name!r}")
        lat, lon = point.lat, point.lon
    else:
        lat, lon = resolve_coords(opts.get("lat"), opts.get("lon"))
    return DistanceFilter(lat, lon, op="at_most", dist1_km=km)


def _owned_filter(value: Any) -> OwnerFilter:
    from opensak.gui.settings import get_settings
    username = get_settings().gc_username.strip()
    if not username:
        raise MacroError("owned: set your geocaching.com username in Settings first")
    return OwnerFilter(username, op="equals" if _bool_value("owned", value) else "not_equals")


def build_filterset(
    spec: dict, polygon: Optional[Callable[[Any], list[tuple[float, float]]]] = None
) -> tuple[FilterSet, str]:
    """Translate the table passed to opensak.filter{} into a FilterSet.

    *polygon* reads the `polygon` key's file or point table (the runtime
    passes one that checks the folder permissions); without it, the key is
    refused. Returns (filterset, label). Raises MacroError for unknown keys
    or values.
    """
    unknown = set(spec) - set(FILTER_KEYS)
    if unknown:
        raise MacroError(
            f"unknown filter key(s) {sorted(unknown)}; valid keys: {', '.join(FILTER_KEYS)}"
        )

    mode = str(spec.get("mode", "AND")).upper()
    if mode not in ("AND", "OR"):
        raise MacroError(f'mode must be "AND" or "OR", got {spec["mode"]!r}')
    fs = FilterSet(mode=mode)
    if "type" in spec:
        fs.add(
            CacheTypeFilter([_resolve_cache_type(t) for t in _as_list(spec["type"])])
        )
    if "container" in spec:
        fs.add(ContainerFilter([str(c) for c in _as_list(spec["container"])]))
    if "difficulty" in spec:
        fs.add(DifficultyFilter(*_range("difficulty", spec["difficulty"])))
    if "terrain" in spec:
        fs.add(TerrainFilter(*_range("terrain", spec["terrain"])))
    if "found" in spec:
        fs.add(FoundFilter() if spec["found"] else NotFoundFilter())
    if spec.get("available"):
        fs.add(AvailableFilter())
    for key, (on, off) in _FLAG_FILTERS.items():
        if key in spec:
            fs.add(on() if _bool_value(key, spec[key]) else off())
    if "owned" in spec:
        fs.add(_owned_filter(spec["owned"]))
    if "near" in spec:
        fs.add(_near_filter(spec["near"]))
    if "distance" in spec:
        op, d1, d2 = _open_range("distance", spec["distance"], "at_most")
        fs.add(DistanceFilter(*_active_center(), op=op, dist1_km=d1, dist2_km=d2))
    if "bearing" in spec:
        pair = _pair(spec["bearing"])
        deg1, deg2 = (_number(v) for v in pair) if pair else (None, None)
        if deg1 is None or deg2 is None:
            raise MacroError(f"bearing must be {{from, to}} in degrees, got {spec['bearing']!r}")
        fs.add(DirectionFilter(op="between", deg1=deg1, deg2=deg2))
    if "favorites" in spec:
        op, a, b = _open_range("favorites", spec["favorites"], "equal")
        fs.add(FavoritePointsFilter(op, int(a), int(b)))
    if "elevation" in spec:
        op, a, b = _open_range("elevation", spec["elevation"], "equal")
        fs.add(ElevationFilter(op, a, b))
    for key, field in _DATE_FILTERS.items():
        if key in spec:
            fs.add(_date_filter(key, field, spec[key]))
    if "attributes" in spec:
        for attr in _as_list(spec["attributes"]):
            fs.add(_attribute_filter(attr))
    for key, cls in _TEXT_FILTERS.items():
        if key in spec:
            fs.add(cls(str(spec[key])))
    if "text" in spec:
        fs.add(TextSearchFilter(str(spec["text"])))
    if "polygon" in spec:
        if polygon is None:
            raise MacroError("polygon is only available in macros")
        fs.add(LinePolygonFilter(polygon(spec["polygon"]), mode="polygon"))
    if "codes" in spec:
        codes = [str(c).strip().upper() for c in _as_list(spec["codes"])]
        # An empty in_list would match everything; an empty code list must
        # match nothing, like the empty SQL result it may come from (every
        # cache has a GC code).
        fs.add(GcCodeFilter(";".join(codes), op="in_list") if codes
               else GcCodeFilter("", op="empty"))
    if "where" in spec:
        fs.add(WhereClauseFilter(str(spec["where"])))

    if len(fs) == 0:
        raise MacroError("opensak.filter{} needs at least one criterion")
    return fs, str(spec.get("label") or "Macro")


def sort_spec(field: Any, direction: Any = None) -> SortSpec:
    """The arguments of opensak.sort() → a SortSpec."""
    if not isinstance(field, str) or field not in SORT_KEYS:
        raise MacroError(f"opensak.sort: cannot sort by {field!r}; valid: {', '.join(SORT_KEYS)}")
    if direction is None:
        direction = "asc"
    if not isinstance(direction, str) or direction.lower() not in ("asc", "desc"):
        raise MacroError(f'opensak.sort: direction must be "asc" or "desc", got {direction!r}')
    return SortSpec(SORT_KEYS[field], ascending=direction.lower() == "asc")


# ── Corrected coordinates / CSV ──────────────────────────────────────────────


def _gc_code(code: Any, func: str) -> str:
    if not isinstance(code, str) or not code.strip():
        raise MacroError(f"{func} expects a GC code as first argument, got {code!r}")
    return code.strip().upper()


def _db_name(name: Any, func: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise MacroError(f"{func} expects a database name")
    return name.strip()


def _sql_args(func: str, query: Any, params: Any) -> tuple[str, Any]:
    """Check the arguments of opensak.sql()/sql_each(); a Lua params table
    becomes a list (for ?) or a dict (for :name)."""
    if not isinstance(query, str) or not query.strip():
        raise MacroError(f"{func} expects an SQL query")
    if params is None:
        return query, None
    if not hasattr(params, "items"):
        raise MacroError(f"{func}: parameters must be a table, e.g. {{ 1, \"text\" }}")
    items = dict(params.items())
    if all(isinstance(k, str) for k in items):
        return query, items
    if all(isinstance(k, int) for k in items) and sorted(items) == list(range(1, len(items) + 1)):
        return query, [items[i] for i in range(1, len(items) + 1)]
    raise MacroError(
        f"{func}: parameters must be an array {{ v1, v2 }} or a table {{ name = v }}, not both"
    )


def _cache_fields(value: Any) -> tuple[CacheField, ...]:
    """The `fields` option of opensak.caches{} → the fields to load."""
    try:
        return resolve_fields(str(v) for v in _as_list(value))
    except UnknownField as exc:
        raise MacroError(str(exc)) from None


def _number(value: Any) -> Optional[float]:
    """A Lua number, or a string holding one (CSV cells are strings)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def resolve_coords(lat: Any, lon: Any = None) -> tuple[float, float]:
    """(lat, lon) from two numbers or from one coordinate string.

    Raises MacroError if the values cannot be parsed or are out of range.
    """
    if lon is None:
        if not isinstance(lat, str):
            raise MacroError(
                'expected lat, lon or a coordinate string such as "N47 22.123 E008 32.456"'
            )
        parsed = parse_coords(lat)
        if parsed is None:
            raise MacroError(f"cannot parse coordinates {lat!r}")
        return parsed
    la, lo = _number(lat), _number(lon)
    if la is None or lo is None:
        raise MacroError(f"lat/lon must be numbers, got {lat!r}, {lon!r}")
    if not (-90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0):
        raise MacroError(f"coordinates out of range: {la}, {lo}")
    return la, lo


def read_csv_rows(path: Path, sep: Optional[str] = None) -> list[dict[str, str]]:
    """Parse a UTF-8 CSV file (BOM allowed) into a list of header-keyed dicts.

    Header names and cells are stripped; blank lines are skipped. Without
    *sep* the separator is sniffed among "," ";" and tab.
    """
    try:
        if path.stat().st_size > MAX_CSV_BYTES:
            raise MacroError(
                f"{path.name} is larger than {MAX_CSV_BYTES // (1024 * 1024)} MB"
            )
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise MacroError(f"file not found: {path}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise MacroError(f"cannot read {path}: {exc}") from None

    if sep is None:
        try:
            first_line = text.split("\n", 1)[0]
            sep = csv.Sniffer().sniff(first_line, delimiters=",;\t").delimiter
        except csv.Error:
            sep = ","
    elif len(sep) != 1:
        raise MacroError(f"separator must be a single character, got {sep!r}")

    reader = csv.reader(io.StringIO(text), delimiter=sep)
    header = [h.strip() for h in next(reader, [])]
    rows = []
    for cells in reader:
        if not any(c.strip() for c in cells):
            continue
        rows.append({
            h: (cells[i].strip() if i < len(cells) else "")
            for i, h in enumerate(header) if h
        })
    return rows


# ── Coordinate helpers ───────────────────────────────────────────────────────


def take_points(args: tuple, count: int, func: str) -> tuple[list[tuple[float, float]], list]:
    """Read *count* points from the front of *args*, then return them and
    the remaining arguments.

    Each point is either two numbers (lat, lon) or one coordinate string
    such as "N47 22.123 E008 32.456", so opensak.coords.distance(a, b) and
    opensak.coords.distance(lat1, lon1, lat2, lon2) both work.
    """
    rest = list(args)
    points = []
    for _ in range(count):
        if not rest:
            raise MacroError(
                f"{func} expects {count} point(s), each lat, lon or a coordinate string"
            )
        first = rest.pop(0)
        if isinstance(first, str) and _number(first) is None:
            points.append(resolve_coords(first))
        else:
            points.append(resolve_coords(first, rest.pop(0) if rest else None))
    return points, rest


def polygon_points(value: Any) -> list[tuple[float, float]]:
    """A Lua array of points → [(lat, lon), …]. A point is a coordinate
    string, {lat, lon} or {lat = …, lon = …}."""
    points = []
    for item in _as_list(value):
        if isinstance(item, str):
            point = parse_point(item)
            if point is None:
                raise MacroError(f"cannot parse coordinates {item!r}")
            points.append(point)
        elif hasattr(item, "values"):
            lat = item["lat"] if item["lat"] is not None else item[1]
            lon = item["lon"] if item["lon"] is not None else item[2]
            points.append(resolve_coords(lat, lon))
        else:
            raise MacroError(
                f"polygon points must be coordinate strings or {{lat, lon}} tables, got {item!r}"
            )
    return points


# ── Runtime ──────────────────────────────────────────────────────────────────


class MacroRuntime:
    """Run Lua macros against a MacroHost.

    A fresh Lua state is created for every run(), so macros cannot leak
    state into each other.
    """

    def __init__(
        self,
        host: MacroHost,
        output: Optional[Callable[[str], None]] = None,
        log: Optional[Callable[[str], None]] = None,
        profiles_dir: Optional[Path] = None,
        export_settings_dir: Optional[Path] = None,
        instruction_limit: int = DEFAULT_INSTRUCTION_LIMIT,
        memory_limit: int = DEFAULT_MEMORY_LIMIT,
        folder_permissions: Optional[list[FolderPermission]] = None,
    ):
        """*folder_permissions* limits which folders file functions may
        touch; None means the list saved in Settings, read at every run so
        changes apply without reopening the macro window. Folders the user
        approves during a run are added to a copy of the list for that run.

        print() goes to *output*; opensak.log() to *log* (default: *output*)
        and to OpenSAK's log file."""
        self._host = host
        self._output = output or print
        self._log = log or self._output
        self._profiles_dir = profiles_dir
        self._export_settings_dir = export_settings_dir
        self._instruction_limit = instruction_limit
        self._memory_limit = memory_limit
        self._folder_permissions = folder_permissions
        self._base_dir: Optional[Path] = None
        self._run_permissions: list[FolderPermission] = []
        # (folder, write) the user denied during this run — not asked again
        self._denied: set[tuple[Path, bool]] = set()
        # (file, write) picked by the user in opensak.choose_file() — usable
        # for the rest of this run only, whatever the folder list says
        self._picked: set[tuple[Path, bool]] = set()
        self._cancelled = threading.Event()
        # Per-run state, reset by run()
        self._exit_code: Optional[int] = None
        # The sandbox's stop(msg), set while a run is in progress
        self._stop: Optional[Callable[[str], None]] = None
        # The sandbox's where(), set while a run is in progress
        self._where: Optional[Callable[[], str]] = None
        self._slept = 0.0
        self._polygons: dict[Path, LineShape] = {}
        self._boundaries: Optional[tuple[Any, Any]] = None  # (store, resolver)
        # Opened by the first opensak.sql*() call of a run, closed after it.
        self._sql_db: Optional[ReadOnlyDatabase] = None
        # Databases other than the active one, read with the `database`
        # option: opened read-only on first use, closed after the run.
        self._other_sql: dict[Path, ReadOnlyDatabase] = {}
        self._other_engines: dict[Path, Engine] = {}

    def cancel(self) -> None:
        """Ask the running macro to stop. Safe to call from another thread;
        takes effect at the next opensak.sleep(), print() or API call, or
        when a dialog the macro opened returns."""
        self._cancelled.set()

    def _check_cancelled(self) -> None:
        """End a cancelled run — through the sandbox's stop(), so that
        pcall cannot catch it."""
        if self._cancelled.is_set():
            if self._stop is not None:
                self._stop("macro cancelled")
            raise MacroError("macro cancelled")

    # -- API functions exposed to Lua -----------------------------------------

    def _filter(self, spec=None) -> int:
        if spec is None or not hasattr(spec, "items"):
            raise MacroError(
                "opensak.filter expects a table, e.g. opensak.filter{ found = false }"
            )
        fs, label = build_filterset(dict(spec.items()), self._filter_polygon)
        return self._host.apply_filter(fs, label)

    def _filter_polygon(self, value: Any) -> list[tuple[float, float]]:
        if isinstance(value, str):
            value = self._readable_file(value)
        return self._polygon_points(value, "opensak.filter: polygon")

    def _sort(self, field=None, direction=None) -> None:
        self._host.set_sort(sort_spec(field, direction))

    def _load_profile(self, name: str) -> FilterProfile:
        for path in FilterProfile.list_profiles(self._profiles_dir):
            try:
                profile = FilterProfile.load(path)
            except Exception:
                continue
            if profile.name == name:
                return profile
        raise MacroError(f"no saved filter profile named {name!r}")

    def _filter_profile(self, name=None) -> int:
        if not isinstance(name, str):
            raise MacroError("opensak.filter_profile expects the profile name")
        profile = self._load_profile(name)
        return self._host.apply_filter(profile.filterset, profile.name)

    def _profile_names(self) -> list[str]:
        names = []
        for path in FilterProfile.list_profiles(self._profiles_dir):
            try:
                names.append(FilterProfile.load(path).name)
            except Exception:
                continue
        return names

    def _set_corrected(self, code=None, lat=None, lon=None) -> bool:
        gc_code = _gc_code(code, "opensak.set_corrected")
        la, lo = resolve_coords(lat, lon)
        return bool(self._host.set_corrected_coords(gc_code, la, lo))

    def _clear_corrected(self, code=None) -> bool:
        gc_code = _gc_code(code, "opensak.clear_corrected")
        return bool(self._host.set_corrected_coords(gc_code, None, None))

    # Raw SQL: a separate read-only connection (sql.py), opened on first use.

    def _close_sql(self) -> None:
        if self._sql_db is not None:
            self._sql_db.close()
            self._sql_db = None

    def _sql(self, db: Optional[DatabaseInfo] = None) -> ReadOnlyDatabase:
        if db is not None:
            if db.path not in self._other_sql:
                self._other_sql[db.path] = ReadOnlyDatabase(db.path)
            return self._other_sql[db.path]
        if self._sql_db is None:
            self._sql_db = ReadOnlyDatabase(Path(get_engine().url.database or ""))
        return self._sql_db

    def _query(self, lua, query=None, params=None, options=None):
        query, params = _sql_args("opensak.sql", query, params)
        db = self._read_options("opensak.sql", options)
        try:
            rows = self._sql(db).query(query, params)
        except SqlError as exc:
            raise MacroError(str(exc)) from None
        return lua.table_from([lua.table_from(r) for r in rows])

    def _query_each(self, lua, query=None, params=None, options=None):
        query, params = _sql_args("opensak.sql_each", query, params)
        db = self._read_options("opensak.sql_each", options)
        rows = self._sql(db).iterate(query, params)

        def step(*_):
            try:
                row = next(rows, None)
            except SqlError as exc:
                raise MacroError(str(exc)) from None
            return None if row is None else lua.table_from(row)

        # The query runs on the first step, so errors surface in the loop.
        return self._wrap(step)

    def _tables(self, lua, options=None):
        db = self._read_options("opensak.tables", options)
        try:
            return lua.table_from(self._sql(db).tables())
        except SqlError as exc:
            raise MacroError(str(exc)) from None

    def _columns(self, lua, table=None, options=None):
        if not isinstance(table, str) or not table.strip():
            raise MacroError("opensak.columns expects a table name")
        db = self._read_options("opensak.columns", options)
        try:
            columns = self._sql(db).columns(table)
        except SqlError as exc:
            raise MacroError(str(exc)) from None
        return lua.table_from([lua.table_from(c) for c in columns])

    # Cache access: plain-table snapshots (cache_data.py), straight from the
    # database, so they are current even right after a write.

    def _active_codes(self) -> list[str]:
        return [c.gc_code for c in self._host.filtered_caches()]

    def _cache(self, lua, code=None, options=None):
        gc_code = _gc_code(code, "opensak.cache")
        db = self._read_options("opensak.cache", options)
        with self._session_factory(db, "opensak.cache")() as session:
            records = load_records(session, [gc_code])
        return lua.table_from(records[0], recursive=True) if records else None

    def _caches(self, lua, spec=None):
        if spec is not None and not hasattr(spec, "items"):
            raise MacroError(
                "opensak.caches expects nothing or a table, e.g. "
                'opensak.caches{ found = true, fields = {"code", "name"} }'
            )
        spec = dict(spec.items()) if spec is not None else {}
        fields = CACHE_FIELDS
        if "fields" in spec:
            fields = _cache_fields(spec.pop("fields"))
        db = self._other_database("opensak.caches", spec.pop("database", None))
        session_factory = self._session_factory(db, "opensak.caches")
        if spec or db is not None:
            # Another database has no active filter: no keys = all its caches.
            filterset = build_filterset(spec, self._filter_polygon)[0] if spec else None
            with session_factory() as session:
                codes = [c.gc_code for c in apply_filters_auto(session, filterset)]
        else:
            codes = self._active_codes()
        records = iter_records(session_factory, codes, fields)

        def step(*_):
            record = next(records, None)
            return None if record is None else lua.table_from(record, recursive=True)

        return step

    def _current(self, lua):
        code = self._host.current_code()
        return self._cache(lua, code) if code else None

    def _description(self, lua, code=None, options=None):
        gc_code = _gc_code(code, "opensak.description")
        db = self._read_options("opensak.description", options)
        with self._session_factory(db, "opensak.description")() as session:
            description = load_description(session, gc_code)
        return lua.table_from(description) if description else None

    # Reading another database (the `database` option of the read
    # functions): no switch, so the active filter stays. The file is opened
    # read-only, for raw SQL (_sql) and for the cache snapshots alike.

    def _read_options(self, func: str, options: Any) -> Optional[DatabaseInfo]:
        """The options table of a read function → the database to read,
        None for the active one."""
        if options is None:
            return None
        if not hasattr(options, "items"):
            raise MacroError(f'{func}: options must be a table, e.g. {{ database = "Found" }}')
        opts = dict(options.items())
        unknown = set(opts) - {"database"}
        if unknown:
            raise MacroError(f"{func}: unknown option(s) {sorted(unknown)}; valid: database")
        return self._other_database(func, opts.get("database"))

    def _other_database(self, func: str, name: Any) -> Optional[DatabaseInfo]:
        """The database named *name*; None if *name* is nil or names the
        active database."""
        if name is None:
            return None
        db = self._require_database(name, func)
        if db.path == get_db_manager().active_path:
            return None
        if not db.exists:
            raise MacroError(f"{func}: the file of database {db.name!r} is missing: {db.path}")
        return db

    def _session_factory(self, db: Optional[DatabaseInfo], func: str) -> Callable[[], Any]:
        """get_session for the active database, else read-only sessions on *db*."""
        if db is None:
            return get_session
        engine = self._other_engines.get(db.path)
        if engine is None:
            engine = self._open_other_engine(db, func)
            self._other_engines[db.path] = engine
        factory = sessionmaker(bind=engine, autoflush=False)

        @contextmanager
        def session() -> Iterator[Session]:
            s = factory()
            try:
                yield s
            except SQLAlchemyError as exc:
                raise MacroError(f"{func}: cannot read database {db.name!r}: {exc}") from None
            finally:
                s.close()

        return session

    @staticmethod
    def _open_other_engine(db: DatabaseInfo, func: str) -> Engine:
        # No migrations (they would write), so an older schema may lack
        # columns the cache fields read: refuse it with a clear message.
        engine = create_engine("sqlite://", creator=lambda: connect_read_only(db.path))
        try:
            with engine.connect() as conn:
                version = conn.exec_driver_sql("PRAGMA user_version").scalar() or 0
        except SQLAlchemyError as exc:
            engine.dispose()
            raise MacroError(f"{func}: cannot open database {db.name!r} read-only: {exc}") from None
        if version < SCHEMA_VERSION:
            engine.dispose()
            raise MacroError(
                f"{func}: database {db.name!r} was last opened by an older OpenSAK "
                "version; open it once (Manage Databases or opensak.switch_database) "
                "so it is updated"
            )
        return engine

    def _close_other_databases(self) -> None:
        for conn in self._other_sql.values():
            conn.close()
        self._other_sql = {}
        for engine in self._other_engines.values():
            engine.dispose()
        self._other_engines = {}

    # Databases: thin wrappers around DatabaseManager and db/transfer.py.

    @staticmethod
    def _find_database(name: str) -> Optional[DatabaseInfo]:
        return next((db for db in get_db_manager().databases if db.name == name), None)

    def _require_database(self, name: Any, func: str) -> DatabaseInfo:
        db = self._find_database(_db_name(name, func))
        if db is None:
            raise MacroError(f"{func}: no database named {name!r} — see opensak.databases()")
        return db

    def _databases(self, lua):
        manager = get_db_manager()
        rows = [
            lua.table_from({
                "name": db.name,
                "path": str(db.path),
                "active": db.path == manager.active_path,
                "size_mb": round(db.size_mb, 2),
            })
            for db in manager.databases
        ]
        return lua.table_from(rows)

    def _database_exists(self, name=None) -> bool:
        return self._find_database(_db_name(name, "opensak.database_exists")) is not None

    def _create_database(self, name=None) -> str:
        name = _db_name(name, "opensak.create_database")
        if self._find_database(name):
            raise MacroError(f"opensak.create_database: a database named {name!r} exists already")
        try:
            db = get_db_manager().new_database(name)
        except ValueError as exc:
            raise MacroError(f"opensak.create_database: {exc}") from None
        self._host.database_list_changed()
        return db.name

    def _switch_database(self, name=None) -> None:
        db = self._require_database(name, "opensak.switch_database")
        # The read-only SQL connection belongs to the old database.
        self._close_sql()
        try:
            self._host.switch_database(db.name)
        except Exception as exc:
            raise MacroError(f"opensak.switch_database: cannot open {db.name!r}: {exc}") from None

    def _transfer(self, func: str, copy_only: bool, target=None, options=None) -> int:
        db = self._require_database(target, func)
        active = get_db_manager().active
        if active is None or db.path == active.path:
            raise MacroError(f"{func}: {db.name!r} is the active database")
        if not db.exists:
            raise MacroError(f"{func}: the file of database {db.name!r} is missing: {db.path}")
        if options is not None and not hasattr(options, "items"):
            raise MacroError(f'{func}: options must be a table, e.g. {{ if_exists = "skip" }}')
        opts = dict(options.items()) if options is not None else {}
        unknown = set(opts) - {"codes", "if_exists"}
        if unknown:
            raise MacroError(f"{func}: unknown option(s) {sorted(unknown)}; valid: codes, if_exists")
        if "codes" in opts:
            codes = [_gc_code(c, func) for c in _as_list(opts["codes"])]
        else:
            codes = self._active_codes()
        if_exists = opts.get("if_exists", "newer")
        if if_exists not in IF_EXISTS:
            raise MacroError(f"{func}: if_exists must be one of {', '.join(IF_EXISTS)}, got {if_exists!r}")
        try:
            count = transfer_caches(codes, active.path, db.path, copy_only=copy_only,
                                    if_exists=if_exists)
        except Exception as exc:
            raise MacroError(f"{func}: {exc}") from None
        if count and not copy_only:
            self._host.caches_removed()
        return count

    def _confirm(self, message=None) -> bool:
        if not isinstance(message, str) or not message.strip():
            raise MacroError("opensak.confirm expects a message")
        return bool(self._host.confirm(message))

    def _readable_file(self, path: str) -> Path:
        """*path* resolved against the macro's folder and checked against the
        folder permissions (asking the user for an unapproved folder)."""
        file = Path(path).expanduser()
        if not file.is_absolute():
            file = (self._base_dir or macros_dir()) / file
        return self._check_access(file, write=False)

    def _read_csv(self, lua, path=None, sep=None):
        if not isinstance(path, str) or not path.strip():
            raise MacroError("opensak.read_csv expects a file path")
        if sep is not None and not isinstance(sep, str):
            raise MacroError("opensak.read_csv: separator must be a string")
        rows = read_csv_rows(self._readable_file(path), sep)
        return lua.table_from([lua.table_from(r) for r in rows])

    def _choose_file(self, title=None, file_filter=None, mode=None) -> Optional[str]:
        for name, value in (("title", title), ("filter", file_filter)):
            if value is not None and (
                not isinstance(value, str) or len(value) > MAX_CHOOSE_FILE_TEXT
            ):
                raise MacroError(
                    f"opensak.choose_file: {name} must be a string of at most "
                    f"{MAX_CHOOSE_FILE_TEXT} characters"
                )
        if mode not in (None, "open", "save"):
            raise MacroError('opensak.choose_file: mode must be "open" or "save"')
        save = mode == "save"
        chosen = self._host.choose_file(
            title or "", file_filter or "", save, self._base_dir or macros_dir()
        )
        if not chosen:
            return None
        try:
            target = resolve_path(chosen)
        except (OSError, RuntimeError) as exc:
            raise MacroError(f"cannot resolve {chosen}: {exc}") from None
        reason = protected_reason(target)
        if reason is not None:
            kind = "write" if save else "read"
            raise MacroError(f"macros may not {kind} {target} — {reason}")
        self._picked.add((target, save))
        return str(target)

    def _check_access(self, path: Path, write: bool) -> Path:
        """check_access() against this run's list. A folder that is merely
        not listed is put to the user (host.approve_folder) instead of
        failing; protected data and roots fail without asking. A file the
        user picked in opensak.choose_file() needs no folder permission."""
        try:
            return check_access(path, write=write, permissions=self._run_permissions)
        except FolderNotApproved as exc:
            if (exc.target, write) in self._picked:
                return exc.target
            folder = approvable_folder(exc.target)
            if folder is None:
                raise MacroError(str(exc)) from None
            if (folder, write) not in self._denied:
                answer = self._host.approve_folder(exc.target, folder, write)
                if answer not in (FolderApproval.ONCE, FolderApproval.ALWAYS):
                    self._denied.add((folder, write))
            if (folder, write) in self._denied:
                raise MacroError(f"{exc} (denied by the user)") from None
            if answer is FolderApproval.ALWAYS:
                grant_permanently(folder, write)
            self._run_permissions = with_grant(self._run_permissions, folder, write)
        except FolderAccessDenied as exc:
            raise MacroError(str(exc)) from None
        try:
            return check_access(path, write=write, permissions=self._run_permissions)
        except FolderAccessDenied as exc:
            raise MacroError(str(exc)) from None

    def _exit(self, code=None) -> None:
        value = 0 if code is None else _number(code)
        if value is None or value != int(value) or not 0 <= value <= MAX_EXIT_CODE:
            raise MacroError(
                f"opensak.exit expects a whole number 0-{MAX_EXIT_CODE}, got {code!r}"
            )
        self._exit_code = int(value)
        assert self._stop is not None
        self._stop("opensak.exit")

    def _lua_log(self, *args) -> None:
        text = "\t".join(helpers.lua_tostring(a) for a in args)
        logger.info("macro: %s", text)
        self._log(text)

    def _sleep(self, ms=None) -> None:
        value = _number(ms)
        if value is None or value < 0:
            raise MacroError(f"opensak.sleep expects milliseconds >= 0, got {ms!r}")
        seconds = min(value, MAX_SLEEP_MS) / 1000
        if self._slept + seconds > SLEEP_BUDGET_S:
            raise MacroError(
                f"opensak.sleep: a macro may sleep at most {SLEEP_BUDGET_S:g} s in total"
            )
        self._slept += seconds
        if self._cancelled.wait(seconds):
            raise MacroError("macro cancelled")

    # -- opensak.coords --------------------------------------------------------

    @staticmethod
    def _coords_parse(text=None):
        if not isinstance(text, str):
            raise MacroError(f"opensak.coords.parse expects a string, got {text!r}")
        return parse_point(text)

    @staticmethod
    def _coords_format(*args) -> str:
        (point,), rest = take_points(args, 1, "opensak.coords.format")
        return helpers.format_coordinate(*point, rest[0] if rest else None)

    @staticmethod
    def _coords_distance(*args) -> float:
        (a, b), _ = take_points(args, 2, "opensak.coords.distance")
        return geodesy.distance_km(*a, *b)

    @staticmethod
    def _coords_bearing(*args) -> float:
        (a, b), _ = take_points(args, 2, "opensak.coords.bearing")
        return geodesy.bearing(*a, *b)

    @staticmethod
    def _coords_project(*args) -> tuple[float, float]:
        func = "opensak.coords.project"
        (point,), rest = take_points(args, 1, func)
        brng, km = (_number(v) for v in (rest + [None, None])[:2])
        if brng is None or km is None:
            raise MacroError(f"{func} expects a point, a bearing in degrees and a distance in km")
        return geodesy.project(*point, brng, km)

    @staticmethod
    def _coords_midpoint(*args) -> tuple[float, float]:
        (a, b), _ = take_points(args, 2, "opensak.coords.midpoint")
        return geodesy.midpoint(*a, *b)

    @staticmethod
    def _polygon_points(value: Any, func: str) -> list[tuple[float, float]]:
        """Points of a polygon file (a Path, already permission-checked) or
        of a Lua table of points."""
        if isinstance(value, Path):
            try:
                points = read_points_file(value)
            except Exception as exc:  # OSError, ParseError, ValueError
                raise MacroError(f"{func}: cannot read {value}: {exc}") from None
        elif hasattr(value, "values"):
            points = polygon_points(value)
        else:
            raise MacroError(f"{func} expects a polygon file path or a table of points")
        if len(points) < 3:
            raise MacroError(f"{func}: a polygon needs at least 3 points, got {len(points)}")
        return points

    def _polygon(self, value: Any) -> LineShape:
        file = self._readable_file(value) if isinstance(value, str) else None
        if file is not None and file in self._polygons:
            return self._polygons[file]
        points = self._polygon_points(file or value, "opensak.coords.inside")
        shape = LineShape(points, "polygon", 0.0)
        if file is not None:
            self._polygons[file] = shape
        return shape

    def _coords_inside(self, *args) -> bool:
        (point,), rest = take_points(args, 1, "opensak.coords.inside")
        if not rest:
            raise MacroError("opensak.coords.inside expects a polygon after the point")
        return self._polygon(rest[0]).contains(*point)

    def _coords_location(self, lua, *args):
        (point,), _ = take_points(args, 1, "opensak.coords.location")
        if self._boundaries is None:
            from opensak.geo import BoundaryStore, TerritoryResolver

            store = BoundaryStore()
            if not store.available():
                return None
            self._boundaries = (store, TerritoryResolver(store))
        loc = self._boundaries[1].resolve(*point)
        return lua.table_from(loc._asdict())

    def _load_export_settings(self, name: str) -> FileExportSettings:
        for path in FileExportProfile.list_profiles(self._export_settings_dir):
            try:
                profile = FileExportProfile.load(path)
            except Exception:
                continue
            if profile.name == name:
                return profile.settings
        raise MacroError(f"no saved export setting named {name!r}")

    def _export_file(self, name=None, folder=None):
        if not isinstance(name, str) or not name.strip():
            raise MacroError("opensak.export_file expects the name of a saved export setting")
        if folder is not None and (not isinstance(folder, str) or not folder.strip()):
            raise MacroError("opensak.export_file: folder must be a non-empty string")
        settings = self._load_export_settings(name)
        if folder is None and not settings.folder.strip():
            raise MacroError(
                f"export setting {name!r} has no folder — choose one in the "
                "export dialog and save the setting again, or pass a folder"
            )
        caches = select_for_export(self._host.filtered_caches(), settings.max_records)
        if not caches:
            return None
        file_name = expand_file_name(
            settings.file_name,
            database=self._host.database_name(),
            filter_name=self._host.filter_name(),
            center_name=self._host.center_name(),
            fmt=settings.fmt,
            count=len(caches),
        )
        folder = Path((folder or settings.folder).strip()).expanduser()
        if not folder.is_absolute():
            folder = (self._base_dir or macros_dir()) / folder
        target = self._check_access(folder / f"{file_name}.{settings.fmt}", write=True)
        if target.exists():
            if settings.if_exists == "skip":
                return None
            if settings.if_exists == "ask":
                from opensak.lang import tr

                if not self._host.confirm(tr("file_export_overwrite_msg", path=str(target))):
                    return None
        try:
            count = write_export_file(
                caches, target, settings.fmt,
                use_corrected=settings.use_corrected_coords,
            )
        except OSError as exc:
            raise MacroError(f"cannot write {target}: {exc}") from None
        return str(target), count

    # Ad-hoc export (opensak.export_gpx): like export_file, but every option
    # comes from the macro, including per-cache names and descriptions.

    def _export_gpx(self, lua, spec=None):
        func = "opensak.export_gpx"
        if spec is None or not hasattr(spec, "items"):
            raise MacroError(
                f'{func} expects a table, e.g. {func}{{ path = "out.gpx" }}'
            )
        opts = dict(spec.items())
        unknown = set(opts) - EXPORT_KEYS
        if unknown:
            raise MacroError(
                f"{func}: unknown key(s) {sorted(unknown)}; valid: {', '.join(sorted(EXPORT_KEYS))}"
            )
        target = opts.get("target", "file")
        if target not in ("file", "device"):
            raise MacroError(f'{func}: target must be "file" or "device", got {target!r}')
        path = opts.get("path")
        if path is None and target == "device":
            path = "{database}"
        if not isinstance(path, str) or not path.strip():
            raise MacroError(f"{func} needs path, the file to write")
        fmt = opts.get("format")
        if fmt is None:
            suffix = Path(path).suffix.lower().lstrip(".")
            fmt = suffix if suffix in EXPORT_FORMATS else "gpx"
        allowed = DEVICE_FORMATS if target == "device" else EXPORT_FORMATS
        if fmt not in allowed:
            raise MacroError(f"{func}: format must be one of {', '.join(allowed)}, got {fmt!r}")
        corrected = opts.get("corrected", True)
        if not isinstance(corrected, bool):
            raise MacroError(f"{func}: corrected must be true or false, got {corrected!r}")
        max_records = opts.get("max", 0)
        if isinstance(max_records, bool) or not isinstance(max_records, (int, float)) \
                or max_records < 0 or max_records != int(max_records):
            raise MacroError(f"{func}: max must be a whole number >= 0, got {max_records!r}")
        if_exists = opts.get("if_exists", "overwrite")
        if if_exists not in EXPORT_IF_EXISTS:
            raise MacroError(
                f"{func}: if_exists must be one of {', '.join(EXPORT_IF_EXISTS)}, got {if_exists!r}"
            )
        rename = self._lua_function(func, "rename", opts.get("rename"))
        describe = self._lua_function(func, "description", opts.get("description"))
        attributes, child_waypoints = self._export_pois(func, opts.get("pois"))
        if target == "device":
            folder = self._garmin_folder(func, opts.get("device"), fmt)
        else:
            if "device" in opts:
                raise MacroError(f'{func}: device needs target = "device"')
            folder = Path(path).expanduser().parent
            if not folder.is_absolute():
                folder = (self._base_dir or macros_dir()) / folder

        caches = select_for_export(self._host.filtered_caches(), int(max_records))
        if not caches:
            return None
        file_name = expand_file_name(
            Path(path).name,
            database=self._host.database_name(),
            filter_name=self._host.filter_name(),
            center_name=self._host.center_name(),
            fmt=fmt,
            count=len(caches),
        )
        output = self._check_access(folder / f"{file_name}.{fmt}", write=True)
        if output.exists():
            if if_exists == "skip":
                return None
            if if_exists == "ask":
                from opensak.lang import tr

                if not self._host.confirm(tr("file_export_overwrite_msg", path=str(output))):
                    return None
        text = self._export_text(lua, func, caches, rename, describe)
        try:
            count = write_export_file(
                caches, output, fmt, use_corrected=corrected, text=text,
                attributes=attributes, child_waypoints=child_waypoints,
            )
        except OSError as exc:
            raise MacroError(f"cannot write {output}: {exc}") from None
        return str(output), count

    @staticmethod
    def _lua_function(func: str, key: str, value: Any) -> Optional[Any]:
        if value is None:
            return None
        from lupa.lua54 import lua_type

        if lua_type(value) != "function":
            raise MacroError(f"{func}: {key} must be a function(c), got {value!r}")
        return value

    @staticmethod
    def _export_pois(func: str, value: Any) -> tuple[bool, bool]:
        """The pois table → (attributes, child_waypoints), both on by default."""
        if value is None:
            return True, True
        if not hasattr(value, "items"):
            raise MacroError(f"{func}: pois must be a table, e.g. {{ child_waypoints = false }}")
        pois = dict(value.items())
        unknown = set(pois) - {"attributes", "child_waypoints"}
        if unknown:
            raise MacroError(
                f"{func}: unknown pois key(s) {sorted(unknown)}; valid: attributes, child_waypoints"
            )
        result = []
        for key in ("attributes", "child_waypoints"):
            on = pois.get(key, True)
            if not isinstance(on, bool):
                raise MacroError(f"{func}: pois.{key} must be true or false, got {on!r}")
            result.append(on)
        return result[0], result[1]

    def _export_text(self, lua, func: str, caches: list, rename, describe) -> ExportText:
        """Call the macro's rename / description functions once per cache,
        with the cache table (see Cache fields)."""
        if rename is None and describe is None:
            return ExportText()
        names: dict[str, str] = {}
        descriptions: dict[str, str] = {}
        codes = [c.gc_code for c in caches]
        for record in iter_records(get_session, codes):
            table = lua.table_from(record, recursive=True)
            for fn, key, result in ((rename, "rename", names),
                                    (describe, "description", descriptions)):
                if fn is None:
                    continue
                value = fn(table)
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                    raise MacroError(
                        f"{func}: {key} must return a string (or nil to keep the "
                        f"default), got {value!r} for {record['code']}"
                    )
                result[record["code"]] = str(value)
        return ExportText(names, descriptions)

    def _garmin_folder(self, func: str, device: Any, fmt: str) -> Path:
        """The GPX or GGZ folder of the connected Garmin device (the one
        named by *device* when several are connected)."""
        from opensak.gps.garmin import (
            find_garmin_devices,
            get_garmin_ggz_path,
            get_garmin_gpx_path,
            is_mtp_device,
        )

        if device is not None and (not isinstance(device, str) or not device.strip()):
            raise MacroError(f"{func}: device must be the device's folder, e.g. \"E:\\\"")
        devices = find_garmin_devices()
        if not devices:
            raise MacroError(f"{func}: no Garmin device connected")
        if device is not None:
            wanted = Path(device.strip()).expanduser()
            devices = [d for d in devices if str(d) == device.strip() or Path(str(d)) == wanted]
            if not devices:
                raise MacroError(f"{func}: no Garmin device at {device!r}")
        elif len(devices) > 1:
            names = ", ".join(repr(str(d)) for d in devices)
            raise MacroError(f"{func}: several Garmin devices connected ({names}); choose one with device = ...")
        root = devices[0]
        if not isinstance(root, Path) or is_mtp_device(root):
            raise MacroError(
                f"{func}: {root} is an MTP device, which macros cannot write to yet "
                "— use GPS → Send to GPS"
            )
        return get_garmin_ggz_path(root) if fmt == "ggz" else get_garmin_gpx_path(root)

    # -- Running ---------------------------------------------------------------

    def run(
        self, source: str, chunk_name: str = "macro", base_dir: Optional[Path] = None
    ) -> int:
        """Execute *source* and return its exit code: the one passed to
        opensak.exit(), 0 otherwise. Raises MacroError on any failure, and
        MacroBusy (without running anything) while another macro runs.

        *base_dir* (usually the macro file's folder) is where relative paths
        given to opensak.read_csv() are looked up; the macros folder
        otherwise.
        """
        if not _RUN_LOCK.acquire(blocking=False):
            raise MacroBusy("another macro is still running")
        try:
            with _c_locale():
                return self._run(source, chunk_name, base_dir)
        finally:
            _RUN_LOCK.release()

    def _run(self, source: str, chunk_name: str, base_dir: Optional[Path]) -> int:
        self._base_dir = base_dir
        self._run_permissions = (
            list(self._folder_permissions)
            if self._folder_permissions is not None
            else load_permissions()
        )
        self._denied = set()
        self._picked = set()
        self._cancelled.clear()
        self._exit_code = None
        self._slept = 0.0
        self._polygons = {}
        try:
            from lupa.lua54 import LuaError, LuaMemoryError, LuaRuntime
        except ImportError as exc:
            raise MacroError(
                'Lua support is not installed — run: pip install "lupa>=2.0,<3"'
            ) from exc

        lua = LuaRuntime(
            register_eval=False,
            register_builtins=False,
            unpack_returned_tuples=True,
            max_memory=self._memory_limit,
            # No attribute access on Python objects from Lua at all: the
            # script only gets the plain functions in the opensak table.
            attribute_filter=self._deny_attribute,
        )
        self._stop, self._where = lua.execute(_SANDBOX_SETUP, self._instruction_limit)

        g = lua.globals()
        g.print = self._wrap(self._lua_print)
        # "coords.parse" → opensak.coords.parse
        api: dict[str, Any] = {}
        for func in API:
            *namespaces, name = func.name.split(".")
            table = api
            for ns in namespaces:
                table = table.setdefault(ns, {})
            table[name] = self._wrap(func.bind(self, lua))
        g.opensak = lua.table_from(api, recursive=True)

        try:
            fn = lua.compile(source, name=f"={chunk_name}")
            fn()
            self._check_cancelled()
            return self._exit_code or 0
        except MacroError:
            raise
        except LuaError as exc:
            # opensak.exit() and a cancelled run end the script with a Lua
            # error; that is the expected way out, not a failure of the macro.
            if self._exit_code is not None:
                return self._exit_code
            if self._cancelled.is_set():
                raise MacroError("macro cancelled") from exc
            if isinstance(exc, LuaMemoryError):
                raise MacroError(
                    "macro aborted: memory limit reached "
                    f"({self._memory_limit // (1024 * 1024)} MB)"
                ) from exc
            raise MacroError(str(exc)) from exc
        except Exception as exc:
            # A Python exception raised inside a callback (e.g. the
            # attribute_filter) propagates as itself, not as a LuaError.
            raise MacroError(f"{type(exc).__name__}: {exc}") from exc
        finally:
            self._stop = self._where = None
            if self._boundaries is not None:
                self._boundaries[0].close()
                self._boundaries = None
            self._close_sql()
            self._close_other_databases()
            self._host.end_macro()

    @staticmethod
    def _deny_attribute(obj, attr_name, is_setting):
        raise AttributeError("access to Python objects is not allowed in macros")

    def _wrap(self, func: Callable) -> Callable:
        """Turn MacroError into a clean Lua error message (no Python
        traceback) naming the calling line, and end a cancelled run before
        and after the call — a dialog the macro opened may be where the user
        cancelled."""
        from lupa.lua54 import LuaError

        def call(*args):
            try:
                self._check_cancelled()
                result = func(*args)
                self._check_cancelled()
                return result
            except MacroError as exc:
                message = str(exc)
                if self._where is not None and not self._cancelled.is_set():
                    where = self._where()
                    # An error from a Lua callback (e.g. an export rename
                    # function) names its line already.
                    if not message.startswith(where.split(":", 1)[0] + ":"):
                        message = where + message
                raise LuaError(message) from None

        return call

    def _lua_print(self, *args) -> None:
        self._output("\t".join(helpers.lua_tostring(a) for a in args))


# ── Lua API registry ─────────────────────────────────────────────────────────
#
# Single source of truth for the `opensak` table. To add a function: add an
# ApiFunction here (bump API_VERSION and use it as `since` if the release
# already shipped the current version), then regenerate the docs and the
# Lua Language Server stub with
#   python scripts/generate_macro_api_docs.py

# Raised whenever functions are added or changed in a released build, so
# macros can check opensak.api_version() before using newer functions.
API_VERSION = 3


@dataclass(frozen=True)
class Param:
    """One parameter of an API function."""

    name: str
    # Lua Language Server type, e.g. "string", "number|string", "string[]"
    type: str
    description: str
    optional: bool = False


@dataclass(frozen=True)
class ApiFunction:
    """One function of the `opensak` table, with its documentation.

    The signatures in docs/macros/api.md and the Lua Language Server stub
    are generated from *params*, *returns* and *overloads*.
    """

    name: str
    description: str
    example: str
    since: int
    # (runtime, lua) → the Python callable exposed to Lua
    bind: Callable[[MacroRuntime, Any], Callable]
    params: tuple[Param, ...] = ()
    # (Lua Language Server type, description), or None if nothing is returned
    returns: Optional[tuple[str, str]] = None
    # Further accepted parameter lists (same return value)
    overloads: tuple[tuple[Param, ...], ...] = ()

    @property
    def signatures(self) -> list[str]:
        """E.g. ["opensak.read_csv(path [, sep])"]."""
        result = []
        for params in (self.params, *self.overloads):
            text = ""
            for i, p in enumerate(params):
                sep = ", " if i else ""
                text += f" [{sep}{p.name}]" if p.optional else f"{sep}{p.name}"
            result.append(f"opensak.{self.name}({text.strip()})")
        return result


_CODE = Param("code", "string", 'GC code, e.g. "GC12345".')
_READ_OPTIONS = Param("options", "opensak.ReadOptions",
                      "`database`: read another database instead of the active one.",
                      optional=True)
_LAT = Param("lat", "number", "Latitude in decimal degrees.")
_LON = Param("lon", "number", "Longitude in decimal degrees.")
_COORDS = Param("coords", "string", 'Coordinates, e.g. "N47 22.123 E008 32.456".')
_TWO_POINTS = (
    Param("lat1", "number", "Latitude of the first point."),
    Param("lon1", "number", "Longitude of the first point."),
    Param("lat2", "number", "Latitude of the second point."),
    Param("lon2", "number", "Longitude of the second point."),
)
_TWO_POINT_STRINGS = (
    Param("a", "string", "First point as a coordinate string."),
    Param("b", "string", "Second point as a coordinate string."),
)
_BEARING = Param("bearing", "number", "Direction in degrees, 0 = North, clockwise.")
_DIST = Param("km", "number", "Distance in km.")
_POLYGON = Param("polygon", "string|table", "Polygon file path, or a table of points.")
_TEXT = Param("text", "string", "The text.")
_PATTERN = Param("pattern", "string", "Regular expression (Python syntax); a "
                                      "long bracket string [[...]] avoids "
                                      "doubling backslashes.")


def _rot13(text=None) -> str:
    if not isinstance(text, str):
        raise MacroError(f"opensak.text.rot13 expects a string, got {text!r}")
    return rot13(text)


API: tuple[ApiFunction, ...] = (
    ApiFunction(
        name="api_version",
        description="The API version of this OpenSAK build. Each function "
                    "lists the version it was added in.",
        example='if opensak.api_version() < 1 then\n'
                '    error("this macro needs a newer OpenSAK")\nend',
        since=1,
        bind=lambda rt, lua: lambda: API_VERSION,
        returns=("integer", "The API version."),
    ),
    ApiFunction(
        name="filter",
        description="Build a filter from the given keys (see Filter keys; "
                    "combined with AND unless `mode = \"OR\"`) and apply it. Usually called with "
                    "table syntax: `opensak.filter{ ... }`. When nothing "
                    "matches, the view is left unchanged.",
        example='local n = opensak.filter{ type = "Traditional", difficulty = {1, 2}, found = false }\n'
                'print("Easy unfound traditionals: " .. n)',
        since=1,
        bind=lambda rt, lua: rt._filter,
        params=(Param("spec", "opensak.FilterSpec", "The filter keys."),),
        returns=("integer", "Number of matching caches (0 = view unchanged)."),
    ),
    ApiFunction(
        name="filter_name",
        description="The name of the active filter: the `label` of "
                    "opensak.filter{}, a profile name, or \"\" when no "
                    "filter is active. The same as the {filter} variable of "
                    "an export file name.",
        example='print("Filter: " .. opensak.filter_name())',
        since=2,
        bind=lambda rt, lua: lambda: rt._host.filter_name(),
        returns=("string", "The filter name; \"\" for none."),
    ),
    ApiFunction(
        name="sort",
        description="Sort the cache list, like a click on a column header. "
                    "opensak.caches() and opensak.codes() then return the "
                    "caches in this order. Fields: "
                    + ", ".join(f"`{k}`" for k in SORT_KEYS) + ".",
        example='opensak.sort("difficulty", "desc")',
        since=2,
        bind=lambda rt, lua: rt._sort,
        params=(
            Param("field", "string", "A cache field, e.g. \"difficulty\"."),
            Param("direction", '"asc"|"desc"', '"asc" if omitted.', optional=True),
        ),
    ),
    ApiFunction(
        name="filter_profile",
        description="Apply a saved filter profile.",
        example='local n = opensak.filter_profile("Unfound nearby")',
        since=1,
        bind=lambda rt, lua: rt._filter_profile,
        params=(Param("name", "string", "Name of the saved profile."),),
        returns=("integer", "Number of matching caches."),
    ),
    ApiFunction(
        name="clear_filter",
        description="Remove the active filter, so all caches are shown again.",
        example="opensak.clear_filter()",
        since=1,
        bind=lambda rt, lua: rt._host.clear_filter,
    ),
    ApiFunction(
        name="count",
        description="The number of caches matching the active filter.",
        example='print(opensak.count() .. " caches shown")',
        since=1,
        bind=lambda rt, lua: rt._host.cache_count,
        returns=("integer", "Number of caches shown."),
    ),
    ApiFunction(
        name="profiles",
        description="The names of all saved filter profiles.",
        example="for _, name in ipairs(opensak.profiles()) do\n"
                "    print(name)\nend",
        since=1,
        bind=lambda rt, lua: lambda: lua.table_from(rt._profile_names()),
        returns=("string[]", "Profile names."),
    ),
    ApiFunction(
        name="cache",
        description="One cache as a table (see Cache fields). It is a "
                    "snapshot: changing it changes nothing in the database.",
        example='local c = opensak.cache("GC12345")\n'
                'if c and c.corrected then print(c.name, c.corrected.lat, c.corrected.lon) end\n'
                'local found = opensak.cache("GC12345", { database = "Found" })',
        since=2,
        bind=lambda rt, lua: lambda *a: rt._cache(lua, *a),
        params=(_CODE, _READ_OPTIONS),
        returns=("opensak.Cache?", "The cache, or nil if it is not in the database."),
    ),
    ApiFunction(
        name="caches",
        description="Iterate over caches, one table per cache (see Cache "
                    "fields), in a generic `for`. Without arguments: the "
                    "caches of the active filter, in grid order. With filter "
                    "keys (see Filter keys): the caches matching them, sorted "
                    "by name; the view and the active filter stay "
                    "unchanged. `database` reads another database, without "
                    "switching: its caches matching the filter keys, or all "
                    "of them, sorted by name. `fields` limits the fields "
                    "loaded (`code` is always included), which makes loops "
                    "over many caches "
                    "faster. Caches are loaded in chunks, so large databases "
                    "do not hit the memory limit.",
        example="for c in opensak.caches() do print(c.code, c.name) end\n"
                'for c in opensak.caches{ found = true, country = "Switzerland",\n'
                '                         fields = {"difficulty", "terrain"} } do\n'
                "    print(c.code, c.difficulty, c.terrain)\nend\n"
                'for c in opensak.caches{ database = "Found", fields = {"corrected"} } do\n'
                "    print(c.code, c.corrected and c.corrected.lat)\nend",
        since=2,
        bind=lambda rt, lua: lambda *a: rt._caches(lua, *a),
        params=(Param("spec", "opensak.CachesSpec",
                      "Filter keys, `database` and/or `fields`; nothing = the "
                      "active filter.",
                      optional=True),),
        returns=("fun(): opensak.Cache?", "Iterator for a generic `for`."),
    ),
    ApiFunction(
        name="current",
        description="The cache selected in the grid.",
        example="local c = opensak.current()\n"
                'if c then print(c.code .. " " .. c.name) end',
        since=2,
        bind=lambda rt, lua: lambda: rt._current(lua),
        returns=("opensak.Cache?", "The cache, or nil if no row is selected."),
    ),
    ApiFunction(
        name="selected",
        description="The GC codes of the rows selected in the grid.",
        example="for _, code in ipairs(opensak.selected()) do print(code) end",
        since=2,
        bind=lambda rt, lua: lambda: lua.table_from(list(rt._host.selected_codes())),
        returns=("string[]", "GC codes; empty if nothing is selected."),
    ),
    ApiFunction(
        name="codes",
        description="The GC codes of the caches of the active filter, in grid "
                    "order. Cheaper than opensak.caches() when only the codes "
                    "are needed.",
        example='print(table.concat(opensak.codes(), ", "))',
        since=2,
        bind=lambda rt, lua: lambda: lua.table_from(rt._active_codes()),
        returns=("string[]", "GC codes."),
    ),
    ApiFunction(
        name="description",
        description="The listing description of a cache. Not part of the "
                    "cache table because it can be large.",
        example='local d = opensak.description("GC12345")\n'
                'if d and d.long and d.long:find("bonus") then print("bonus cache") end',
        since=2,
        bind=lambda rt, lua: lambda *a: rt._description(lua, *a),
        params=(_CODE, _READ_OPTIONS),
        returns=("{short: string?, long: string?, html: boolean}?",
                 "Short and long description and whether they are HTML; "
                 "nil if the cache is not in the database."),
    ),
    ApiFunction(
        name="sql",
        description="Run a read-only SQL query (SQLite) against the active "
                    "database (or the one named by the `database` option) "
                    "and return all rows. Only reading statements "
                    "are allowed; the connection itself is read-only. "
                    "Column names follow the database schema, which may "
                    "change between versions (see opensak.tables() and "
                    "opensak.columns()). Use `AS` to name computed columns. "
                    "NULL values are nil. At most "
                    f"{MAX_ROWS:,} rows; use opensak.sql_each() for more. A "
                    f"query is aborted after {QUERY_TIMEOUT_S:g} s.",
        example='local rows = opensak.sql(\n'
                '  "SELECT country, COUNT(*) AS n FROM caches WHERE found = ? GROUP BY country", { 1 })\n'
                "for _, r in ipairs(rows) do print(r.country, r.n) end",
        since=2,
        bind=lambda rt, lua: lambda *a: rt._query(lua, *a),
        params=(
            Param("query", "string", "One SQL statement."),
            Param("params", "table",
                  "Values for `?` placeholders ({ v1, v2 }) or for `:name` "
                  "placeholders ({ name = v }); `nil` or `{}` for none.",
                  optional=True),
            _READ_OPTIONS,
        ),
        returns=("table<string, any>[]", "One table per row, keyed by column name."),
    ),
    ApiFunction(
        name="sql_each",
        description="Like opensak.sql(), but returns an iterator for a "
                    "generic `for` that fetches the rows in chunks — for "
                    "results of any size.",
        example='for r in opensak.sql_each("SELECT gc_code, name FROM caches WHERE found = 0") do\n'
                "    print(r.gc_code, r.name)\nend",
        since=2,
        bind=lambda rt, lua: lambda *a: rt._query_each(lua, *a),
        params=(
            Param("query", "string", "One SQL statement."),
            Param("params", "table", "As for opensak.sql().", optional=True),
            _READ_OPTIONS,
        ),
        returns=("fun(): table<string, any>?", "Iterator for a generic `for`."),
    ),
    ApiFunction(
        name="tables",
        description="The tables and views of the active database (or the "
                    "one named by the `database` option), for use with "
                    "opensak.sql().",
        example='print(table.concat(opensak.tables(), ", "))',
        since=2,
        bind=lambda rt, lua: lambda *a: rt._tables(lua, *a),
        params=(_READ_OPTIONS,),
        returns=("string[]", "Table and view names, sorted."),
    ),
    ApiFunction(
        name="columns",
        description="The columns of a table or view of the active database "
                    "(or the one named by the `database` option).",
        example='for _, c in ipairs(opensak.columns("caches")) do print(c.name, c.type) end',
        since=2,
        bind=lambda rt, lua: lambda *a: rt._columns(lua, *a),
        params=(Param("table", "string", "Table or view name."), _READ_OPTIONS),
        returns=("{name: string, type: string}[]", "Column names and SQL types, in table order."),
    ),
    ApiFunction(
        name="databases",
        description="All databases in OpenSAK's database list, sorted by name.",
        example="for _, db in ipairs(opensak.databases()) do\n"
                '    print(db.name, db.active and "(active)" or "", db.size_mb .. " MB")\nend',
        since=2,
        bind=lambda rt, lua: lambda: rt._databases(lua),
        returns=("{name: string, path: string, active: boolean, size_mb: number}[]",
                 "One table per database."),
    ),
    ApiFunction(
        name="database",
        description="The name of the active database.",
        example='print("Working on " .. opensak.database())',
        since=2,
        bind=lambda rt, lua: lambda: rt._host.database_name(),
        returns=("string", "Database name."),
    ),
    ApiFunction(
        name="database_exists",
        description="Whether a database with this name is in the database "
                    "list (exact, case-sensitive match).",
        example='if not opensak.database_exists("CH_Zurich") then\n'
                '    opensak.create_database("CH_Zurich")\nend',
        since=2,
        bind=lambda rt, lua: rt._database_exists,
        params=(Param("name", "string", "Database name."),),
        returns=("boolean", "true if it exists."),
    ),
    ApiFunction(
        name="create_database",
        description="Create a new, empty database in the default database "
                    "folder and add it to the list. The active database does "
                    "not change. Fails if the name is taken or its file "
                    "already exists.",
        example='local name = opensak.create_database("CH_Zurich")',
        since=2,
        bind=lambda rt, lua: rt._create_database,
        params=(Param("name", "string", "Name of the new database."),),
        returns=("string", "The name actually used (surrounding spaces removed)."),
    ),
    ApiFunction(
        name="switch_database",
        description="Make another database the active one, as the toolbar "
                    "dropdown does. The active filter is cleared.",
        example='opensak.switch_database("CH_Zurich")\n'
                'print(opensak.count() .. " caches in " .. opensak.database())',
        since=2,
        bind=lambda rt, lua: rt._switch_database,
        params=(Param("name", "string", "Database name."),),
    ),
    ApiFunction(
        name="move_caches",
        description="Move caches from the active database to another one, "
                    "with all logs, waypoints, attributes, trackables and "
                    "notes (like Database → Move caches). By default the "
                    "caches of the active filter. When a cache already "
                    "exists in the target, `if_exists` decides: `\"newer\"` "
                    "(default) replaces it only if the active database's copy "
                    "was imported later, `\"replace\"` always, `\"skip\"` "
                    "never. A cache not written to the target stays in the "
                    "active database.",
        example='local n = opensak.move_caches("CH_Zurich")\n'
                'print(n .. " caches moved")',
        since=2,
        bind=lambda rt, lua: lambda *a: rt._transfer("opensak.move_caches", False, *a),
        params=(
            Param("target", "string", "Name of the target database (not the active one)."),
            Param("options", "opensak.TransferOptions", "codes, if_exists.", optional=True),
        ),
        returns=("integer", "Number of caches moved."),
    ),
    ApiFunction(
        name="copy_caches",
        description="Like opensak.move_caches(), but the caches stay in the "
                    "active database.",
        example='local n = opensak.copy_caches("CH_Zurich", { codes = {"GC1", "GC2"}, if_exists = "replace" })',
        since=2,
        bind=lambda rt, lua: lambda *a: rt._transfer("opensak.copy_caches", True, *a),
        params=(
            Param("target", "string", "Name of the target database (not the active one)."),
            Param("options", "opensak.TransferOptions", "codes, if_exists.", optional=True),
        ),
        returns=("integer", "Number of caches copied."),
    ),
    ApiFunction(
        name="set_corrected",
        description="Set corrected coordinates, either as decimal degrees or "
                    "as one coordinate string in any format OpenSAK "
                    "understands (DMM, DMS, decimal degrees).",
        example='opensak.set_corrected("GC12345", 47.36872, 8.54093)\n'
                'opensak.set_corrected("GC12345", "N47 22.123 E008 32.456")',
        since=1,
        bind=lambda rt, lua: rt._set_corrected,
        params=(
            _CODE,
            Param("lat", "number|string", "Latitude in decimal degrees."),
            Param("lon", "number|string", "Longitude in decimal degrees."),
        ),
        overloads=((
            _CODE,
            Param("coords", "string", 'Coordinates, e.g. "N47 22.123 E008 32.456".'),
        ),),
        returns=("boolean", "false if the cache is not in the database."),
    ),
    ApiFunction(
        name="clear_corrected",
        description="Remove the corrected coordinates of a cache.",
        example='opensak.clear_corrected("GC12345")',
        since=1,
        bind=lambda rt, lua: rt._clear_corrected,
        params=(_CODE,),
        returns=("boolean", "false if the cache is not in the database."),
    ),
    ApiFunction(
        name="read_csv",
        description="Read a CSV file (UTF-8) into an array of rows keyed by the "
                    "header line. A relative path is resolved against the "
                    "macro file's folder. If the file's folder has no read "
                    "permission (Settings → Folder permissions), OpenSAK asks "
                    "the user to allow it for this run or always. The file "
                    f"may be at most {MAX_CSV_BYTES // (1024 * 1024)} MB.",
        example='for _, row in ipairs(opensak.read_csv("solved.csv")) do\n'
                "    opensak.set_corrected(row.code, row.coords)\nend",
        since=1,
        bind=lambda rt, lua: lambda *a: rt._read_csv(lua, *a),
        params=(
            Param("path", "string", "The CSV file."),
            Param("sep", "string",
                  "Separator character; detected among , ; and tab if omitted.",
                  optional=True),
        ),
        returns=("table<string, string>[]", "One table per data row, keyed by header."),
    ),
    ApiFunction(
        name="export_file",
        description="Export the caches of the active filter with a saved export "
                    "setting (File → Export → GPX/LOC/GGZ: format, folder, file "
                    "name, if the file exists, corrected coordinates, max. "
                    "caches). The file name variables are filled in as in the "
                    "dialog, {filter} with the name of the active filter and "
                    "{center} with the active centre point. The file goes "
                    "into *folder* if given, else into the setting's folder. "
                    "That folder needs write permission (Settings → Folder "
                    "permissions); for an unapproved one the user is asked "
                    "first. Nothing is "
                    "written when no cache with coordinates is shown, or when "
                    "the file exists and the setting says skip (or ask, and "
                    "the user answers No).",
        example='local path, n = opensak.export_file("GPX Export")\n'
                'if path then print(n .. " caches → " .. path) end',
        since=2,
        bind=lambda rt, lua: rt._export_file,
        params=(
            Param("setting", "string", "Name of the saved export setting."),
            Param("folder", "string", "Folder to write to instead of the "
                  "setting's folder, e.g. opensak.temp_dir().", optional=True),
        ),
        returns=("string?, integer?",
                 "The file written and the number of caches in it; nil if "
                 "nothing was written."),
    ),
    ApiFunction(
        name="export_gpx",
        description="Export the caches of the active filter without a saved "
                    "export setting. `path` is the file to write; its name "
                    "may use the variables of the export dialog ({database}, "
                    "{filter}, {center}, {date}, {count}, ...), and a "
                    "relative path is resolved against the macro file's "
                    "folder. Its folder needs write permission, like "
                    "opensak.export_file(). `rename(c)` and `description(c)` "
                    "are called with each cache table (see Cache fields) and "
                    "return the name and the waypoint description to write "
                    "(nil keeps the default). With `target = \"device\"`, "
                    "the file goes into the GPX or GGZ folder of the "
                    "connected Garmin device instead (`path` is then only "
                    "the file name; MTP devices are not supported yet). "
                    "Nothing is written when no cache with coordinates is "
                    "shown, or when the file exists and `if_exists` says "
                    "skip (or ask, and the user answers No).",
        example='local path, n = opensak.export_gpx{\n'
                '  path = opensak.temp_dir() .. "/{database}_{filter}.gpx",\n'
                '  rename = function(c) return c.difficulty .. "/" .. c.terrain .. " " .. c.name end,\n'
                '  pois = { child_waypoints = false },\n'
                '}\n'
                'if path then print(n .. " caches → " .. path) end',
        since=2,
        bind=lambda rt, lua: lambda *a: rt._export_gpx(lua, *a),
        params=(Param("spec", "opensak.ExportSpec", "What to export and where."),),
        returns=("string?, integer?",
                 "The file written and the number of caches in it; nil if "
                 "nothing was written."),
    ),
    ApiFunction(
        name="confirm",
        description="Ask the user a Yes/No question.",
        example='if not opensak.confirm("Update 12 caches?") then return end',
        since=1,
        bind=lambda rt, lua: rt._confirm,
        params=(Param("message", "string", "The question."),),
        returns=("boolean", "true on Yes."),
    ),
    ApiFunction(
        name="choose_file",
        description="Let the user pick a file in a file dialog. The picked "
                    "file may be used for the rest of this run without a "
                    "folder permission: read with mode \"open\" (the "
                    "default), written with mode \"save\". OpenSAK's own "
                    "settings and database files cannot be picked. The "
                    "dialog starts in the macro file's folder.",
        example='local path = opensak.choose_file("Solved puzzles", "CSV files (*.csv)")\n'
                'if not path then return end      -- cancelled\n'
                'for _, row in ipairs(opensak.read_csv(path)) do\n'
                '    opensak.set_corrected(row.code, row.coords)\nend',
        since=2,
        bind=lambda rt, lua: rt._choose_file,
        params=(
            Param("title", "string", "Dialog title.", optional=True),
            Param("filter", "string",
                  'File types, e.g. "CSV files (*.csv);;All files (*)".',
                  optional=True),
            Param("mode", '"open"|"save"',
                  '"open" picks an existing file to read (default), '
                  '"save" a file to write (the dialog asks before '
                  "replacing an existing one).",
                  optional=True),
        ),
        returns=("string?", "Full path of the picked file, or nil if cancelled."),
    ),
    ApiFunction(
        name="temp_dir",
        description="OpenSAK's folder inside the system temp folder (read "
                    "and write permission by default), without a trailing "
                    "separator. \"/\" works as separator on every platform.",
        example='local rows = opensak.read_csv(opensak.temp_dir() .. "/solved.csv")',
        since=1,
        bind=lambda rt, lua: lambda: str(temp_dir()),
        returns=("string", "Folder path."),
    ),
    ApiFunction(
        name="macros_dir",
        description="OpenSAK's macros folder (read permission by default), "
                    "without a trailing separator.",
        example='local rows = opensak.read_csv(opensak.macros_dir() .. "/data/solved.csv")',
        since=1,
        bind=lambda rt, lua: lambda: str(macros_dir()),
        returns=("string", "Folder path."),
    ),
    ApiFunction(
        name="version",
        description="The OpenSAK version this macro runs in.",
        example='print("Running in OpenSAK " .. opensak.version())',
        since=2,
        bind=lambda rt, lua: lambda: __version__,
        returns=("string", 'Version, e.g. "1.21.0-beta.3".'),
    ),
    ApiFunction(
        name="sleep",
        description=f"Pause the macro. One pause lasts at most {MAX_SLEEP_MS // 1000} s "
                    f"and all pauses of a run together at most {SLEEP_BUDGET_S:g} s; "
                    "a cancelled macro stops at its next pause.",
        example="opensak.sleep(500)",
        since=2,
        bind=lambda rt, lua: rt._sleep,
        params=(Param("ms", "number", "Milliseconds."),),
    ),
    ApiFunction(
        name="log",
        description="Write a message to OpenSAK's log file and the macro "
                    "output. Run with `opensak --run-macro`, it goes to "
                    "stderr while print() goes to stdout.",
        example='opensak.log("checked " .. opensak.count() .. " caches")',
        since=3,
        bind=lambda rt, lua: rt._lua_log,
        params=(Param("...", "any", "Values, joined with tabs like print()."),),
    ),
    ApiFunction(
        name="exit",
        description="End the macro right away. pcall cannot catch it, and it "
                    "is not an error: the macro window shows the code, and "
                    "`opensak --run-macro` exits with it.",
        example='if opensak.count() == 0 then\n'
                '    opensak.log("nothing to do")\n'
                '    opensak.exit(1)\nend',
        since=3,
        bind=lambda rt, lua: rt._exit,
        params=(Param("code", "integer",
                      f"Exit code 0-{MAX_EXIT_CODE} (default 0).", optional=True),),
    ),

    # -- opensak.coords: pure coordinate math, no permissions needed ----------
    # Every point can be given as lat, lon (decimal degrees) or as one
    # coordinate string in any format opensak.coords.parse() understands.
    ApiFunction(
        name="coords.parse",
        description="Parse a coordinate string in any format OpenSAK "
                    "understands (DMM, DMS, decimal degrees).",
        example='local lat, lon = opensak.coords.parse("N47 22.123 E008 32.456")\n'
                'if not lat then error("not a coordinate") end',
        since=2,
        bind=lambda rt, lua: rt._coords_parse,
        params=(Param("text", "string", "The coordinates."),),
        returns=("number?, number?", "Latitude and longitude, or nil if the "
                                    "text cannot be parsed."),
    ),
    ApiFunction(
        name="coords.format",
        description="Format coordinates. Formats: `\"dmm\"` (default), `\"dms\"`, "
                    "`\"dd\"`, `\"utm\"`, `\"ch1903\"` (Swiss LV03) and "
                    "`\"ch1903+\"` (Swiss LV95). The Swiss formats are only "
                    "meaningful in and around Switzerland.",
        example='print(opensak.coords.format(47.36872, 8.54093, "utm"))\n'
                'print(opensak.coords.format("N47 22.123 E008 32.456", "ch1903"))',
        since=2,
        bind=lambda rt, lua: rt._coords_format,
        params=(
            _LAT, _LON,
            Param("fmt", "string", 'Output format, "dmm" if omitted.', optional=True),
        ),
        overloads=((_COORDS, Param("fmt", "string", "Output format.", optional=True)),),
        returns=("string", 'E.g. "N47 22.123  E008 32.456" or "32T E 465123 N 5247123".'),
    ),
    ApiFunction(
        name="coords.distance",
        description="Great-circle distance between two points.",
        example='local km = opensak.coords.distance("N47 22.123 E008 32.456",\n'
                '                                   "N47 23.000 E008 33.000")',
        since=2,
        bind=lambda rt, lua: rt._coords_distance,
        params=_TWO_POINTS,
        overloads=(_TWO_POINT_STRINGS,),
        returns=("number", "Distance in km."),
    ),
    ApiFunction(
        name="coords.bearing",
        description="Initial bearing from the first point to the second.",
        example='local deg = opensak.coords.bearing(47.36872, 8.54093, 47.38333, 8.55)',
        since=2,
        bind=lambda rt, lua: rt._coords_bearing,
        params=_TWO_POINTS,
        overloads=(_TWO_POINT_STRINGS,),
        returns=("number", "Degrees, 0 = North, clockwise."),
    ),
    ApiFunction(
        name="coords.project",
        description="Waypoint projection: the point a given distance away in a "
                    "given direction.",
        example='local lat, lon = opensak.coords.project("N47 22.123 E008 32.456", 45, 0.25)\n'
                "print(opensak.coords.format(lat, lon))",
        since=2,
        bind=lambda rt, lua: rt._coords_project,
        params=(_LAT, _LON, _BEARING, _DIST),
        overloads=((_COORDS, _BEARING, _DIST),),
        returns=("number, number", "Latitude and longitude of the projected point."),
    ),
    ApiFunction(
        name="coords.midpoint",
        description="The point halfway between two points (along the great circle).",
        example='local lat, lon = opensak.coords.midpoint(47.0, 8.0, 48.0, 9.0)',
        since=2,
        bind=lambda rt, lua: rt._coords_midpoint,
        params=_TWO_POINTS,
        overloads=(_TWO_POINT_STRINGS,),
        returns=("number, number", "Latitude and longitude of the midpoint."),
    ),
    ApiFunction(
        name="coords.inside",
        description="Whether a point lies inside a polygon. The polygon is "
                    "either a file (GPX track/route/waypoints, KML, or a text "
                    "file with one coordinate per line; read permission "
                    "needed, relative paths are resolved against the macro "
                    "file's folder) or a table of points, each a coordinate "
                    "string, `{lat, lon}` or `{lat = ..., lon = ...}`. Edges "
                    "are straight lines in latitude/longitude, as in the "
                    "line/polygon filter.",
        example='local area = { "N47 20 E008 30", "N47 25 E008 30", "N47 25 E008 40" }\n'
                "print(opensak.coords.inside(47.37, 8.54, area))",
        since=2,
        bind=lambda rt, lua: rt._coords_inside,
        params=(_LAT, _LON, _POLYGON),
        overloads=((_COORDS, _POLYGON),),
        returns=("boolean", "true if the point is inside."),
    ),
    ApiFunction(
        name="coords.location",
        description="Offline reverse geocoding with the boundary data used by "
                    "Update location. A field is nil where no region matches.",
        example='local loc = opensak.coords.location(47.36872, 8.54093)\n'
                'if loc then print(loc.country, loc.state, loc.county) end',
        since=2,
        bind=lambda rt, lua: lambda *a: rt._coords_location(lua, *a),
        params=(_LAT, _LON),
        overloads=((_COORDS,),),
        returns=("{country: string?, state: string?, county: string?}?",
                 "nil if the boundary data is not installed."),
    ),

    # -- opensak.re: regular expressions (Python syntax, with a time limit) ---
    ApiFunction(
        name="re.find",
        description="Search for the first match of a regular expression "
                    f"(Python syntax). Each call may run at most "
                    f"{helpers.REGEX_TIMEOUT_S:g} s.",
        example='local whole, n, e = opensak.re.find(desc, [[N\\s*(\\d+)\\D+E\\s*(\\d+)]])',
        since=2,
        bind=lambda rt, lua: helpers.re_find,
        params=(_TEXT, _PATTERN),
        returns=("string?, string?...", "The whole match followed by the captures "
                                        "(nil for a group that did not take part), "
                                        "or nil if there is no match."),
    ),
    ApiFunction(
        name="re.match",
        description="Whether the text contains a match. Use `^` and `$` to "
                    "match the whole text.",
        example='if opensak.re.match(c.name, [[(?i)^bonus]]) then print("bonus cache") end',
        since=2,
        bind=lambda rt, lua: helpers.re_match,
        params=(_TEXT, _PATTERN),
        returns=("boolean", "true if the pattern matches."),
    ),
    ApiFunction(
        name="re.findall",
        description="All non-overlapping matches. Without capture groups each "
                    "item is the whole match, with one group it is the "
                    "capture, with several it is an array of the captures.",
        example='for _, number in ipairs(opensak.re.findall("A=3, B=12", [[\\d+]])) do\n'
                "    print(number)\nend",
        since=2,
        bind=lambda rt, lua: lambda *a: lua.table_from(helpers.re_findall(*a), recursive=True),
        params=(_TEXT, _PATTERN),
        returns=("string[]|string[][]", "The matches."),
    ),
    ApiFunction(
        name="re.replace",
        description="Replace matches. In a replacement string, `\\1` or "
                    "`\\g<name>` insert a capture. A replacement function gets "
                    "the whole match and the captures and returns the new "
                    "text (nil or false keeps the match).",
        example='local text = opensak.re.replace("A=3 B=12", [[\\d+]], function(n)\n'
                "    return n * 2\nend)",
        since=2,
        bind=lambda rt, lua: helpers.re_replace,
        params=(
            _TEXT, _PATTERN,
            Param("repl", "string|fun(match: string, ...: string?): any",
                  "Replacement text or function."),
            Param("count", "integer", "Replace at most this many matches; all if "
                                      "omitted.", optional=True),
        ),
        returns=("string, integer", "The new text and the number of replacements."),
    ),
    ApiFunction(
        name="re.split",
        description="Split the text at every non-empty match.",
        example='local parts = opensak.re.split("a, b;c", [[[,;]\\s*]])  -- {"a", "b", "c"}',
        since=2,
        bind=lambda rt, lua: lambda *a: lua.table_from(helpers.re_split(*a)),
        params=(_TEXT, _PATTERN),
        returns=("string[]", "The pieces between the matches."),
    ),

    # -- opensak.text ---------------------------------------------------------
    ApiFunction(
        name="text.html_to_text",
        description="Turn HTML (e.g. a cache description) into plain text: "
                    "tags removed, entities decoded, block elements and "
                    "`<br>` become line breaks.",
        example='print(opensak.text.html_to_text("<p>Stage&nbsp;1:<br>N47 22.123</p>"))',
        since=2,
        bind=lambda rt, lua: helpers.html_to_text,
        params=(Param("html", "string", "The HTML."),),
        returns=("string", "The text."),
    ),
    ApiFunction(
        name="text.rot13",
        description="ROT13 as used for hints. Text in [square brackets] stays "
                    "unchanged, like on geocaching.com.",
        example='print(opensak.text.rot13("haqre gur fgbar [Ubhfr]"))',
        since=2,
        bind=lambda rt, lua: _rot13,
        params=(_TEXT,),
        returns=("string", "The decoded (or encoded) text."),
    ),
    ApiFunction(
        name="text.digit_sum",
        description="Cross sum: the sum of all digits; other characters are ignored.",
        example="print(opensak.text.digit_sum(1987))  -- 25",
        since=2,
        bind=lambda rt, lua: helpers.digit_sum,
        params=(Param("n", "number|string", "The number or text."),),
        returns=("integer", "Sum of the digits."),
    ),
    ApiFunction(
        name="text.word_value",
        description="Letter value sum (A=1 … Z=26). Accents are dropped (Ä "
                    "counts as A); other characters are ignored.",
        example='print(opensak.text.word_value("Geocache"))  -- 47',
        since=2,
        bind=lambda rt, lua: helpers.word_value,
        params=(_TEXT,),
        returns=("integer", "Sum of the letter values."),
    ),
    ApiFunction(
        name="text.normalize_name",
        description="Make a string safe as a database or file name on every "
                    "platform: characters such as `\\ / : * ? \" < > |` become "
                    "`_`, white space is collapsed, leading/trailing dots and "
                    f"spaces are removed and the length is limited to "
                    f"{helpers.MAX_NAME_LENGTH}. Never empty.",
        example='local name = opensak.text.normalize_name("CH: Zürich / Nord")  -- "CH_ Zürich _ Nord"',
        since=2,
        bind=lambda rt, lua: helpers.normalize_name,
        params=(Param("s", "string", "The name."),),
        returns=("string", "The safe name."),
    ),

    # -- opensak.date ---------------------------------------------------------
    ApiFunction(
        name="date.parse",
        description="Parse a date into seconds since the epoch, the same kind "
                    "of value as `os.time()`. Without a format, ISO 8601 "
                    '(`"2026-10-06"`, `"2026-10-06T14:30:00Z"`) and '
                    '`"06.10.2026 [14:30[:00]]"` are understood. Times without '
                    "a zone are local time.",
        example='local t = opensak.date.parse("2026-10-06")\n'
                'local t2 = opensak.date.parse("10/06/2026", "%m/%d/%Y")',
        since=2,
        bind=lambda rt, lua: helpers.date_parse,
        params=(
            _TEXT,
            Param("fmt", "string", "strptime format, e.g. \"%d/%m/%Y\".", optional=True),
        ),
        returns=("integer?", "Seconds since the epoch, or nil if the text does not parse."),
    ),
    ApiFunction(
        name="date.format",
        description="Format seconds since the epoch (e.g. from `os.time()` or "
                    "`opensak.date.parse()`) in local time.",
        example='print(opensak.date.format(os.time(), "%d.%m.%Y %H:%M"))',
        since=2,
        bind=lambda rt, lua: helpers.date_format,
        params=(
            Param("t", "number", "Seconds since the epoch."),
            Param("fmt", "string", 'strftime format, "%Y-%m-%d" if omitted.', optional=True),
        ),
        returns=("string", "The formatted date."),
    ),
)

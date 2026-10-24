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
a function is exposed without documentation. The generated files are not
committed on feature branches; the Macro API docs workflow regenerates them
after the merge to beta.

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

import base64
import binascii
import codecs
import csv
import fnmatch
import functools
import io
import json
import locale
import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Protocol

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
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
    IsOwnerFilter,
    LockedFilter,
    MacroFlagFilter,
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
from opensak.coord_formats import INPUT_KEYS, parse_any
from opensak.db.cache_delete import delete_caches
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
from opensak.export.poi_export import write_poi_export
from opensak.export.poi_export_settings import PoiExportProfile, PoiExportSettings
from opensak.filters.line_polygon import LineShape, read_points_file
from opensak.importer.gpx_import_settings import (
    FOLDER_IMPORT_SUFFIXES,
    GpxImportProfile,
    GpxImportSettings,
    folder_import_files,
    import_file,
)
from opensak.hint_detect import rot13
from opensak.macro import forms as macro_forms
from opensak.macro import helpers
from opensak.macro import settings as macro_settings
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
from opensak.macro import db_access
from opensak.macro.cache_write import (
    MACRO_FLAG_ACTIONS,
    CacheWriteError,
    insert_cache,
    parse_values,
    set_macro_flag,
    update_cache,
)
from opensak.macro.child_write import (
    ATTRIBUTE,
    LOG,
    TRACKABLE,
    WAYPOINT,
    ChildTable,
    delete_child,
    insert_child,
    list_children,
    parse_child_values,
    update_child,
)
from opensak.macro.db_access import WriteApproval
from opensak.macro.permissions import (
    FolderAccessDenied,
    FolderNotApproved,
    FolderPermission,
    approvable_folder,
    check_access,
    expand_path,
    grant_permanently,
    load_permissions,
    macros_dir,
    protected_reason,
    resolve_path,
    safe_copy,
    safe_delete,
    safe_write,
    safe_write_stream,
    temp_dir,
    with_grant,
    write_denied_reason,
)
from opensak.macro.sql import (
    MAX_ROWS,
    QUERY_TIMEOUT_S,
    ReadOnlyDatabase,
    WRITABLE_TABLES,
    SqlError,
    WritableDatabase,
    connect_read_only,
    default_schema_name,
)
from opensak.stats import charts
from opensak.stats import html as report_html
from opensak.stats import maps as report_maps
from opensak.stats import tables as stats_tables
from opensak.utils import flags
from opensak.utils.constants import (
    ATTRIBUTES,
    CACHE_TYPES,
    CHILD_WP_TYPES,
    KNOWN_PREFIXES,
    KNOWN_SINGLE_PREFIXES,
)

logger = logging.getLogger(__name__)

# A runaway `while true do end` would freeze the GUI thread, so the script is
# aborted after this many Lua VM instructions.
DEFAULT_INSTRUCTION_LIMIT = 50_000_000
# Upper bound for the Lua heap, so e.g. string.rep("x", 1e10) cannot exhaust RAM.
DEFAULT_MEMORY_LIMIT = 256 * 1024 * 1024
# opensak.choose_file(): longest accepted title / file type filter
MAX_CHOOSE_FILE_TEXT = 200
# opensak.message() / input() / choose() texts
MAX_UI_TEXT = 100_000
# A form image (file or base64 data)
MAX_IMAGE_BYTES = 10 * 1024 * 1024
# Where opensak.form{ remember = true } keeps the last values: a folder of
# the macros' settings folder. normalize_name() strips leading dots, so no
# macro subfolder can have this name.
FORM_MEMORY_DIRNAME = ".forms"
# opensak.read_csv() refuses larger files — it is meant for small lists
# (solved puzzles, corrections), not for bulk imports.
MAX_CSV_BYTES = 10 * 1024 * 1024
# opensak.sleep(): longest single pause, and total pause time per run (the
# instruction limit does not count time spent sleeping).
MAX_SLEEP_MS = 10_000
SLEEP_BUDGET_S = 60.0
# opensak.run_macro(): how deep macros may call each other (a macro calling
# itself by mistake would otherwise only stop at the C stack limit), and the
# largest macro file it reads.
MAX_MACRO_DEPTH = 10
MAX_MACRO_BYTES = 1024 * 1024
# opensak.export_gpx{}: the keys of its table, and what to do with an
# existing file.
EXPORT_KEYS = frozenset({
    "path", "format", "corrected", "max", "rename", "description", "pois",
    "if_exists", "target", "device",
})
EXPORT_IF_EXISTS = ("overwrite", "skip", "ask")
# opensak.import_gsak(): the keys of its options table, and how many cache
# errors per database its report lists.
GSAK_IMPORT_KEYS = ("databases", "exclude", "target", "if_exists", "create",
                    "filters", "locations", "quiet")
GSAK_REPORT_MAX_ERRORS = 20
# opensak.import_gpx(): the keys of its options table, and how many cache
# errors per file its report lists.
GPX_IMPORT_KEYS = ("database", "quiet")
GPX_REPORT_MAX_ERRORS = 20
# Formats a Garmin device reads from its GPX / GGZ folder.
DEVICE_FORMATS = ("gpx", "ggz")
# opensak.write_text(): the file types it may write, and the largest file.
WRITE_TEXT_SUFFIXES = (".txt", ".csv", ".tsv", ".html", ".htm", ".svg", ".md", ".json")
MAX_WRITE_BYTES = 20 * 1024 * 1024
# opensak.copy_file / move_file / delete_file: the file types they handle
# (what write_text writes plus geocaching data and pictures — no programs
# or scripts), the largest file copied, and what to do with an existing
# target.
FILE_SUFFIXES = WRITE_TEXT_SUFFIXES + (
    ".gpx", ".loc", ".kml", ".kmz", ".ggz", ".zip", ".xml", ".log",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf",
)
MAX_COPY_BYTES = 1024 * 1024 * 1024
COPY_IF_EXISTS = ("overwrite", "skip")
# opensak.open(): the file types it opens in their default app (documents
# only — never a script, program or shortcut), and how often per run.
OPEN_SUFFIXES = (".html", ".htm", ".svg", ".txt", ".csv", ".tsv", ".md", ".json",
                 ".gpx", ".kml", ".pdf")
MAX_OPENS_PER_RUN = 5

# opensak.html.*: the keys of their option tables (Lua name → Python keyword).
HTML_OPTIONS = {
    "table": {"columns": "columns", "link": "link", "caption": "caption", "bars": "bars",
              "color": "color"},
    "page": {"title": "title", "subtitle": "subtitle", "css": "css", "lang": "lang",
             "footer": "footer", "body": "body"},
    "matrix": {"shade": "shade", "percent": "percent", "totals": "totals",
               "row_title": "row_title", "col_title": "col_title", "color": "color"},
}
# opensak.stats.*: the keys of their option tables (Lua name → Python keyword).
STATS_OPTIONS = {
    "group": {"period": "period", "count_distinct": "count_distinct", "sum": "sum",
              "sort": "sort", "top": "top", "other": "other"},
    "fill": {"key": "key", "period": "period", "from": "start", "to": "end"},
    "pivot": {"rows": "rows_order", "cols": "cols_order", "sum": "sum"},
    "every": {"first": "first", "last": "last", "date": "date_field"},
}
# opensak.chart.bar() / chart.line() / ...: the keys of the option table.
CHART_KEYS = {
    "bar": frozenset({"x", "y", "title", "width", "height", "color", "horizontal", "labels",
                      "names"}),
    "line": frozenset({"x", "y", "title", "width", "height", "color", "area", "labels"}),
    "heatmap": frozenset({"title", "width", "color", "labels", "row_title", "col_title"}),
    "rose": frozenset({"x", "y", "title", "width", "color", "labels"}),
}
# opensak.chart.map() / chart.points(): the keys of the option table.
MAP_KEYS = {
    "map": frozenset({"key", "value", "layer", "within", "title", "width", "height", "color",
                      "extent", "legend"}),
    "points": frozenset({"lat", "lon", "label", "layer", "within", "title", "width", "height",
                         "color", "radius"}),
}
# opensak.geo.*: the keys of their option tables (Lua name → Python keyword).
GEO_OPTIONS = {
    "regions": {"within": "within", "rows": "rows", "key": "key", "value": "value"},
    "outlines": {"within": "within", "rows": "rows", "key": "key", "value": "value",
                 "extent": "extent", "around": "around", "lat": "lat", "lon": "lon",
                 "box": "box", "detail": "detail"},
}

# opensak.read_text(): the largest file, and the encodings it reads (GSAK
# macros often wrote files in the Windows code page).
MAX_READ_BYTES = 10 * 1024 * 1024
READ_TEXT_ENCODINGS = ("utf-8", "latin-1", "cp1252")
# opensak.list_dir(): most entries returned; a pattern narrows a big folder.
MAX_DIR_ENTRIES = 10_000

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
    "macro_flag": (lambda: MacroFlagFilter(True), lambda: MacroFlagFilter(False)),
    # The `owned` cache field (is_owner), not a match on the owner name.
    "owned": (lambda: IsOwnerFilter(True), lambda: IsOwnerFilter(False)),
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
    "last_user_update": "last_user_update",
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
    "macro_sort": "macro_sort",
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
    FilterKeyDoc(("user_flag", "locked", "macro_flag", "dnf", "ftf", "premium"), "boolean",
                 "true | false", "That flag set, or not set."),
    FilterKeyDoc(("has_trackables",), "boolean", "true | false",
                 "With or without trackables in the cache."),
    FilterKeyDoc(("owned",), "boolean", "true | false",
                 "Owned by you, or not: the `owned` cache field (owner ID = "
                 "your finder ID, or owner name = your username in Settings)."),
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
                 "leaves a side open. `last_gpx_update` and `last_user_update` "
                 'also take a time, "2026-09-01T18:00", in local time.'),
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
# The chunk returns three functions for the runtime:
#   * stop(msg) makes the hook sticky with *msg* right away — opensak.exit()
#     and a cancelled run end the macro this way, so pcall cannot catch it.
#     This also ends macros started by opensak.run_macro(), since they run
#     on the same Lua thread.
#   * where() returns "macro.lua:12: " for the macro line that called the
#     API function currently running, so API errors name it like Lua's own
#     errors do (luaL_where; level 3 = where, the API function, its caller).
#   * make_loader() returns the loader opensak.run_macro() compiles other
#     macros with (see below).
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
local raw_load, next = load, next
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
-- For opensak.run_macro(): called once the opensak table is installed, it
-- takes a snapshot of the globals and returns a loader that compiles another
-- macro as text only (never bytecode) in a fresh copy of that snapshot, so
-- macros share nothing but what they pass as arguments and return values.
local function make_loader()
  local base = {}
  for k, v in next, _G do base[k] = v end
  return function(source, name)
    local env = {}
    for k, v in next, base do env[k] = v end
    env._G = env
    local fn, err = raw_load(source, name, "t", env)
    return fn, err
  end
end
return stop, where, make_loader
"""

# opensak.transaction(fn), written in Lua: an error raised by fn passes
# through a Python function only as a message with a traceback appended
# (and an error table as ""), so pcall stays on the Lua side. begin/finish
# are MacroRuntime._tx_begin/_tx_end. If the instruction limit is hit inside
# fn, finish() never runs; _close_sql_write() rolls back at the macro's end.
_TRANSACTION_LUA = """
local begin, finish = ...
local pack, unpack, pcall, error, type = table.pack, table.unpack, pcall, error, type
return function(fn)
  if type(fn) ~= "function" then
    error("opensak.transaction expects a function, e.g. "
          .. "opensak.transaction(function() ... end)", 2)
  end
  begin()
  local r = pack(pcall(fn))
  finish(r[1])
  if not r[1] then error(r[2], 0) end
  return unpack(r, 2, r.n)
end
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

    def active_filterset(self) -> FilterSet:
        """The whole active filter as one FilterSet — the advanced filter
        combined with any quick filter or search box — so that applying it
        with apply_filter() shows the same caches. Empty when no filter is
        active."""

    def set_sort(self, sort: SortSpec) -> None:
        """Sort the cache list, like a click on a column header (also when
        the column is hidden). filtered_caches() must return the new order
        right away."""

    def database_name(self) -> str:
        """Name of the active database — the {database} variable."""

    def center_name(self) -> str:
        """Name of the active centre point ("" = none) — the {center}
        variable."""

    def set_center(self, name: str, lat: float, lon: float) -> None:
        """Make (*lat*, *lon*) the active centre point, shown as *name*,
        like the centre drop-down: store it, recalculate every cache's
        distance and bearing and refresh the list and map. Optional — without
        it opensak.set_center() reports that it is not available."""

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

    def approve_macro_write(self, macro: Optional[Path]) -> WriteApproval:
        """Ask the user whether the macro file *macro* may change the
        OpenSAK databases: never, until OpenSAK closes, or always. *macro*
        is None for an unsaved macro, which cannot be allowed always.

        Asked before the first write of a macro that is not approved yet.
        Must be OpenSAK's own dialog. Anything but SESSION or ALWAYS counts
        as DENY.
        """

    def caches_changed(self, codes: list[str], added: bool) -> None:
        """A macro changed the caches *codes* (opensak.update, insert,
        sql_write); *added* if new caches were inserted. Like
        set_corrected_coords(), the host may defer the refresh to
        end_macro()."""

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

    # The user-interface functions below (#982) are optional: without them
    # (e.g. in a headless run) the opensak functions using them fail with
    # a clear error instead of waiting for input nobody can give.

    def message(self, text: str, title: str) -> None:
        """Show *text* with an OK button."""

    def input(self, prompt: str, default: str, title: str) -> Optional[str]:
        """Ask for one line of text; None if cancelled."""

    def choose(
        self, prompt: str, options: list[str], default: int, title: str
    ) -> Optional[int]:
        """Let the user pick one of *options* (*default*: index, -1 for
        none); the index picked, or None if cancelled."""

    def choose_folder(self, title: str, start_dir: Path) -> Optional[Path]:
        """Let the user pick a folder in OpenSAK's folder dialog; None if
        cancelled."""

    def show_form(
        self, form: macro_forms.Form, events: macro_forms.FormEvents
    ) -> Optional[tuple[dict[str, Any], str]]:
        """Show *form* modally. Returns (values by field id, id of the
        button or field that closed it), or None when cancelled. User
        actions are reported through *events* (see forms.FormEvents);
        "browse" asks the runtime to fill a file or folder field."""

    def progress(self, title: str, total: int) -> "ProgressHandle":
        """Open a progress window with a Cancel button (*total* 0 = no
        known end)."""

    def select_cache(self, gc_code: str) -> bool:
        """Select the cache in the grid and show it in the detail panel
        and map; False if it is not among the caches of the active filter."""

    def open_file(self, path: Path) -> bool:
        """Open the existing file *path* in its default app (e.g. an HTML
        report in the browser); False if none could open it."""

    def refresh(self) -> None:
        """Apply the refreshes deferred so far and redraw the grid now."""

    # Optional too: told after opensak.import_gsak() changed OpenSAK's user
    # locations or filter profiles, so lists showing them can reload.

    def locations_changed(self) -> None:
        """User locations were added or replaced."""

    def filter_profiles_changed(self) -> None:
        """Filter profiles were added or replaced."""


class ProgressHandle(Protocol):
    """The window opened by MacroHost.progress()."""

    def update(self, value: Optional[int], text: str) -> bool:
        """Show *value* (None = keep it) and *text*; False once the user
        has pressed Cancel."""

    def close(self) -> None: ...


# ── Lua table → FilterSet ────────────────────────────────────────────────────


def _as_list(value: Any) -> list:
    """A Lua value that may be a scalar or an array table → Python list."""
    if isinstance(value, (list, tuple)):
        return list(value)
    if hasattr(value, "values"):  # lupa LuaTable
        return list(value.values())
    return [value]


def _gsak_type_codes() -> dict[str, str]:
    """GSAK's one-letter cache type codes: letter → OpenSAK cache type."""
    from opensak.importer.gsak_importer import GSAK_CACHE_TYPE_MAP
    return GSAK_CACHE_TYPE_MAP


def _cache_types(lua):
    letters = {name: code for code, name in _gsak_type_codes().items()}
    return lua.table_from([
        lua.table_from({"name": name, "code": letters.get(name)}) for name in CACHE_TYPES
    ])


def _cache_type(value: Any = None) -> tuple[Optional[str], Optional[str]]:
    """(cache type, GSAK letter) for a letter, a full or a short type name,
    like GSAK's CacheType(); (None, None) if unknown."""
    if not isinstance(value, str) or not value.strip():
        raise MacroError("opensak.cache_type expects a type name or a GSAK letter, "
                         'e.g. "Traditional" or "T"')
    codes = _gsak_type_codes()
    letters = {name: code for code, name in codes.items()}
    wanted = value.strip()
    if len(wanted) == 1:
        name = codes.get(wanted.upper())
        return (name, wanted.upper()) if name else (None, None)
    low = wanted.lower()
    for name in (*CACHE_TYPES, *codes.values()):
        if name.lower() in (low, f"{low} cache"):
            return name, letters.get(name)
    return None, None


def _wp_prefixes() -> dict[str, str]:
    """Waypoint type → its usual (first listed) prefix."""
    prefixes: dict[str, str] = {}
    for prefix, name in KNOWN_PREFIXES.items():
        prefixes.setdefault(name, prefix)
    return prefixes


def _waypoint_types(lua):
    prefixes = _wp_prefixes()
    return lua.table_from([
        lua.table_from({"name": name, "prefix": prefixes[name]}) for name in CHILD_WP_TYPES
    ])


def _waypoint_type(value: Any = None) -> tuple[Optional[str], Optional[str]]:
    """(waypoint type, its usual prefix) for a type name or a waypoint
    prefix, like GSAK's ChildType(); (None, None) if unknown."""
    if not isinstance(value, str) or not value.strip():
        raise MacroError("opensak.waypoint_type expects a waypoint type or a prefix, "
                         'e.g. "Parking Area" or "PK"')
    wanted = value.strip()
    low = wanted.lower()
    name = next((t for t in CHILD_WP_TYPES if t.lower() == low), None)
    if name is None:
        upper = wanted.upper()
        name = KNOWN_PREFIXES.get(upper) or KNOWN_SINGLE_PREFIXES.get(upper)
    if name is None:
        return None, None
    return name, _wp_prefixes().get(name)


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


# opensak.save_filter(): marks (and versions) the string it returns
_SAVED_FILTER_KEY = "opensak_filter"
_SAVED_FILTER_VERSION = 1


def _parse_saved_filter(saved: Any) -> tuple[FilterSet, str]:
    """The filter and its name from a string returned by
    opensak.save_filter()."""
    bad = MacroError(
        "opensak.restore_filter expects a string returned by opensak.save_filter()"
    )
    if not isinstance(saved, str):
        raise bad
    try:
        data = json.loads(saved)
    except ValueError:
        raise bad from None
    if not isinstance(data, dict) or _SAVED_FILTER_KEY not in data:
        raise bad
    if data[_SAVED_FILTER_KEY] != _SAVED_FILTER_VERSION:
        raise MacroError(
            "opensak.restore_filter: the filter was saved by a newer OpenSAK"
        )
    name, spec = data.get("name", ""), data.get("filter")
    if not isinstance(name, str) or not isinstance(spec, dict):
        raise bad
    try:
        return FilterSet.from_dict(spec), name
    except (TypeError, ValueError, KeyError, AttributeError):
        raise bad from None


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


def _row_id(func: str, table: ChildTable, row_id: Any) -> int:
    """The id of a log, waypoint, ... a macro passed."""
    if isinstance(row_id, float) and row_id.is_integer():
        row_id = int(row_id)
    if isinstance(row_id, bool) or not isinstance(row_id, int):
        raise MacroError(
            f"{func} expects the {table.singular}'s id from opensak.{table.plural}(), got {row_id!r}"
        )
    return row_id


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


def _lua_to_python(value: Any) -> Any:
    """A Lua table → dict (nested tables too); other values as they are."""
    if hasattr(value, "items"):
        return {k: _lua_to_python(v) for k, v in value.items()}
    return value


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
        parsed = parse_any(lat)
        if parsed is None:
            raise MacroError(f"cannot parse coordinates {lat!r}")
        return parsed[0], parsed[1]
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
            points.append(resolve_coords(item))
        elif hasattr(item, "values"):
            lat = item["lat"] if item["lat"] is not None else item[1]
            lon = item["lon"] if item["lon"] is not None else item[2]
            points.append(resolve_coords(lat, lon))
        else:
            raise MacroError(
                f"polygon points must be coordinate strings or {{lat, lon}} tables, got {item!r}"
            )
    return points


# ── Lua values for the UI functions ──────────────────────────────────────────


def _lua_kind(value: Any) -> Optional[str]:
    from lupa.lua54 import lua_type
    return lua_type(value)


def _is_lua_table(value: Any) -> bool:
    return _lua_kind(value) == "table"


def _is_lua_function(value: Any) -> bool:
    return _lua_kind(value) == "function"


def _from_lua(value: Any, depth: int = 0) -> Any:
    """A Lua value → Python: an array table (keys 1..n) → list, another
    table → dict, functions stay as they are. Read by index, since the
    order of pairs() is undefined."""
    if not _is_lua_table(value):
        return value
    if depth > macro_forms.MAX_DEPTH * 2:
        raise MacroError("tables nested too deeply")
    items = list(value.items())
    keys = [k for k, _ in items]
    if items and all(isinstance(k, int) and not isinstance(k, bool) for k in keys) \
            and sorted(keys) == list(range(1, len(keys) + 1)):
        by_index = dict(items)
        return [_from_lua(by_index[i], depth + 1) for i in range(1, len(keys) + 1)]
    return {k: _from_lua(v, depth + 1) for k, v in items}


def _to_lua(lua, value: Any) -> Any:
    """Python form values → Lua (lists become arrays)."""
    if isinstance(value, (dict, list, tuple)):
        return lua.table_from(value, recursive=True)
    return value


def _ui_text(func: str, name: str, value: Any, optional: bool = False) -> str:
    """A text argument of a UI function; numbers are accepted, nil only
    when *optional* (→ "")."""
    if value is None and optional:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return macro_forms.format_number(value)
    if not isinstance(value, str):
        raise MacroError(f"{func}: {name} must be a string, got {helpers.lua_tostring(value)}")
    if len(value) > MAX_UI_TEXT:
        raise MacroError(f"{func}: {name} is longer than {MAX_UI_TEXT:,} characters")
    return value


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
        poi_settings_dir: Optional[Path] = None,
        import_settings_dir: Optional[Path] = None,
        instruction_limit: int = DEFAULT_INSTRUCTION_LIMIT,
        memory_limit: int = DEFAULT_MEMORY_LIMIT,
        folder_permissions: Optional[list[FolderPermission]] = None,
        settings_dir: Optional[Path] = None,
    ):
        """*folder_permissions* limits which folders file functions may
        touch; None means the list saved in Settings, read at every run so
        changes apply without reopening the macro window. Folders the user
        approves during a run are added to a copy of the list for that run.
        *settings_dir* holds the macros' saved settings (opensak.settings);
        None means the settings/ folder of the macros folder.

        print() goes to *output*; opensak.log() to *log* (default: *output*)
        and to OpenSAK's log file."""
        self._host = host
        self._output = output or print
        self._log = log or self._output
        self._profiles_dir = profiles_dir
        self._export_settings_dir = export_settings_dir
        self._poi_settings_dir = poi_settings_dir
        self._import_settings_dir = import_settings_dir
        self._instruction_limit = instruction_limit
        self._memory_limit = memory_limit
        self._folder_permissions = folder_permissions
        self._settings_dir = settings_dir
        self._base_dir: Optional[Path] = None
        # The running macro's file — switched by opensak.run_macro() so that
        # per-macro state (saved settings, #983) belongs to the called macro,
        # not its caller. None for an unsaved macro.
        self._macro_path: Optional[Path] = None
        self._chunk_name = "macro"
        # opensak.macro_db(): the file attached during this run
        self._macro_db_path: Optional[Path] = None
        # opensak.attach(): schema → (database name, file), attached
        # read-only; kept here so they survive a reopened connection
        self._attachments: dict[str, tuple[str, Path]] = {}
        self._run_permissions: list[FolderPermission] = []
        # (folder, write) the user denied during this run — not asked again
        self._denied: set[tuple[Path, bool]] = set()
        # (file, write) picked by the user in opensak.choose_file() — usable
        # for the rest of this run only, whatever the folder list says
        self._picked: set[tuple[Path, bool]] = set()
        # Files this run wrote (write_text, export_*, copy/move), resolved —
        # the ones opensak.open() shows without asking.
        self._written: set[Path] = set()
        self._opened = 0
        self._cancelled = threading.Event()
        # opensak.progress() windows still open; closed after the run.
        self._progress_windows: list[ProgressHandle] = []
        # Per-run state, reset by run()
        self._exit_code: Optional[int] = None
        # The sandbox's stop(msg), set while a run is in progress
        self._stop: Optional[Callable[[str], None]] = None
        # The sandbox's where(), set while a run is in progress
        self._where: Optional[Callable[[], str]] = None
        self._slept = 0.0
        self._polygons: dict[Path, LineShape] = {}
        self._boundaries: Optional[tuple[Any, Any]] = None  # (store, resolver)
        # opensak.chart.map / points / geo.*: the boundary data, False once
        # it was found missing in this run.
        self._map_data: Any = None
        # The active database's connection for opensak.sql*() and
        # transaction(), opened on first use and closed after the run (or on
        # switch_database), which drops the macro's TEMP tables.
        self._sql_write_db: Optional[WritableDatabase] = None
        # Macro files (None: unsaved) the user refused to let change
        # databases during this run
        self._write_denied: set[Optional[Path]] = set()
        # Caches deleted during this run, by database name — reported at
        # its end. Inside opensak.transaction() they are counted per level
        # (_tx_deleted) and only kept when it commits.
        self._deleted: dict[str, int] = {}
        self._tx_deleted: list[dict[str, int]] = []
        # Databases other than the active one, read with the `database`
        # option: opened read-only on first use, closed after the run.
        self._other_sql: dict[Path, ReadOnlyDatabase] = {}
        self._other_engines: dict[Path, Engine] = {}
        # opensak.run_macro(): compiles a macro in fresh globals (set by
        # _run) and the number of macros currently running inside this one
        self._load_macro: Optional[Callable] = None
        self._macro_depth = 0

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

    def _save_filter(self) -> str:
        fs = self._host.active_filterset()
        return json.dumps(
            {
                _SAVED_FILTER_KEY: _SAVED_FILTER_VERSION,
                "name": self._host.filter_name() if len(fs) else "",
                "filter": fs.to_dict(),
            },
            ensure_ascii=False, separators=(",", ":"),
        )

    def _restore_filter(self, saved=None) -> int:
        fs, name = _parse_saved_filter(saved)
        if not len(fs):
            self._host.clear_filter()
            return self._host.cache_count()
        return self._host.apply_filter(fs, name)

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
        self._require_write("opensak.set_corrected")
        if self._in_transaction():
            return self._update_in_transaction(gc_code, {"corrected": (la, lo)})
        return bool(self._host.set_corrected_coords(gc_code, la, lo))

    def _clear_corrected(self, code=None) -> bool:
        gc_code = _gc_code(code, "opensak.clear_corrected")
        self._require_write("opensak.clear_corrected")
        if self._in_transaction():
            return self._update_in_transaction(gc_code, {"corrected": None})
        return bool(self._host.set_corrected_coords(gc_code, None, None))

    # Changing caches (cache_write.py): only the active database, and only
    # after the user allowed the running macro to write (db_access.py).

    def _require_write(self, func: str) -> None:
        """Raise unless the user lets the running macro change databases —
        asking (host.approve_macro_write) if that was not decided yet. A
        macro started with opensak.run_macro() is asked about on its own
        (_macro_path is the called macro's). A refusal holds for the rest
        of the run.

        Without the lua-macro-write flag only set_corrected/clear_corrected,
        create_database and move_caches/copy_caches get here (the other
        writing functions are not registered); they change the databases
        without asking, as before the write API."""
        if not flags.lua_macro_write:
            return
        macro = self._macro_path
        if db_access.is_approved(macro):
            return
        if macro not in self._write_denied:
            answer = self._host.approve_macro_write(macro)
            if answer in (WriteApproval.SESSION, WriteApproval.ALWAYS):
                db_access.approve(macro, answer)
                return
            self._write_denied.add(macro)
        name = macro.name if macro is not None else "(unsaved)"
        raise MacroError(f"{func}: the user did not allow macro {name!r} to change databases")

    def _field_values(self, func: str, values: Any, insert: bool) -> dict[str, Any]:
        if values is None or not hasattr(values, "items"):
            raise MacroError(f"{func} expects a table of fields, e.g. {{ user_flag = true }}")
        try:
            return parse_values(func, _lua_to_python(values), insert=insert)
        except CacheWriteError as exc:
            raise MacroError(str(exc)) from None

    def _update(self, code=None, values=None) -> bool:
        gc_code = _gc_code(code, "opensak.update")
        parsed = self._field_values("opensak.update", values, insert=False)
        self._require_write("opensak.update")
        if self._in_transaction():
            return self._update_in_transaction(gc_code, parsed)
        with get_session() as session:
            found = update_cache(session, gc_code, parsed)
        if found:
            self._host.caches_changed([gc_code], False)
        return found

    def _macro_flag(self, action=None, target=None) -> int:
        func = "opensak.macro_flag"
        if not isinstance(action, str) or action.lower() not in MACRO_FLAG_ACTIONS:
            raise MacroError(f'{func}: action must be "set", "clear" or "swap", got {action!r}')
        codes: Optional[list[str]]
        if target is None:
            codes = self._active_codes()
        elif isinstance(target, str):
            codes = [_gc_code(target, func)]
        elif hasattr(target, "items") and dict(target.items()).keys() == {"all"}:
            if dict(target.items())["all"] is not True:
                raise MacroError(f"{func}: all must be true")
            codes = None
        elif hasattr(target, "values"):
            codes = [_gc_code(c, func) for c in _as_list(target)]
        else:
            raise MacroError(
                f"{func} expects a GC code, a list of codes or {{ all = true }}, got {target!r}"
            )
        if codes == []:
            return 0
        self._require_write(func)
        with self._active_session() as session:
            changed = set_macro_flag(session, action.lower(), codes)
        if changed:
            self._host.caches_changed(sorted(changed), False)
        return len(changed)

    def _update_in_transaction(self, gc_code: str, parsed: dict[str, Any]) -> bool:
        with self._active_session() as session:
            found = update_cache(session, gc_code, parsed, refresh=False)
        if found:
            self._host.caches_changed([gc_code], False)
        return found

    def _insert(self, values=None) -> str:
        parsed = self._field_values("opensak.insert", values, insert=True)
        self._require_write("opensak.insert")
        try:
            with self._active_session() as session:
                code = insert_cache(session, parsed, refresh=not self._in_transaction())
        except CacheWriteError as exc:
            raise MacroError(str(exc)) from None
        self._host.caches_changed([code], True)
        return code

    # The rows of a cache (child_write.py): logs, waypoints, attributes and
    # trackables, read and written like the cache itself.

    def _children(self, lua, table: ChildTable, code=None, options=None):
        func = f"opensak.{table.plural}"
        gc_code = _gc_code(code, func)
        db = self._read_options(func, options)
        with self._session_factory(db, func)() as session:
            rows = list_children(session, table, gc_code)
        return None if rows is None else lua.table_from([lua.table_from(r) for r in rows])

    def _child_values(self, func: str, table: ChildTable, values: Any, insert: bool) -> dict[str, Any]:
        if values is None or not hasattr(values, "items"):
            raise MacroError(f"{func} expects a table of fields, e.g. {{ {table.fields[0].name} = .. }}")
        try:
            return parse_child_values(func, table, _lua_to_python(values), insert=insert)
        except CacheWriteError as exc:
            raise MacroError(str(exc)) from None

    def _write_child(self, func: str, write: Callable[[Any], Any]) -> Any:
        """*write(session)* in the active database (in the open transaction,
        if any); a broken constraint, e.g. a duplicate, is a MacroError."""
        try:
            with self._active_session() as session:
                return write(session)
        except IntegrityError as exc:
            raise MacroError(f"{func}: {exc.orig}") from None

    def _insert_child(self, table: ChildTable, code=None, values=None) -> Optional[int]:
        func = f"opensak.insert_{table.singular}"
        gc_code = _gc_code(code, func)
        parsed = self._child_values(func, table, values, insert=True)
        self._require_write(func)
        refresh = not self._in_transaction()
        row_id = self._write_child(
            func, lambda s: insert_child(s, table, gc_code, parsed, refresh=refresh))
        if row_id is not None:
            self._host.caches_changed([gc_code], False)
        return row_id

    def _update_child(self, table: ChildTable, row_id=None, values=None) -> bool:
        func = f"opensak.update_{table.singular}"
        row_id = _row_id(func, table, row_id)
        parsed = self._child_values(func, table, values, insert=False)
        self._require_write(func)
        refresh = not self._in_transaction()
        code = self._write_child(
            func, lambda s: update_child(s, table, row_id, parsed, refresh=refresh))
        if code is None:
            return False
        self._host.caches_changed([code], False)
        return True

    # Deleting (#1035): needs the same write permission. A deleted cache
    # takes its logs, waypoints, attributes, trackables and user note along.

    def _delete(self, codes=None) -> Any:
        func = "opensak.delete"
        single = isinstance(codes, str)
        if not single and not hasattr(codes, "values"):
            raise MacroError(
                f'{func} expects a GC code or a list of codes, e.g. opensak.delete("GC12345"), '
                f"got {codes!r}"
            )
        gc_codes = [_gc_code(c, func) for c in _as_list(codes)]
        if not gc_codes:
            return 0
        self._require_write(func)
        with self._active_session() as session:
            deleted = delete_caches(session, gc_codes)
        self._caches_deleted(len(deleted))
        return bool(deleted) if single else len(deleted)

    def _delete_child(self, table: ChildTable, row_id=None) -> bool:
        func = f"opensak.delete_{table.singular}"
        row_id = _row_id(func, table, row_id)
        self._require_write(func)
        refresh = not self._in_transaction()
        code = self._write_child(func, lambda s: delete_child(s, table, row_id, refresh=refresh))
        if code is None:
            return False
        self._host.caches_changed([code], False)
        return True

    def _caches_deleted(self, count: int) -> None:
        """Tell the host, and count *count* deleted caches for the report
        at the end of the run."""
        if not count:
            return
        self._host.caches_removed()
        counts = self._tx_deleted[-1] if self._tx_deleted else self._deleted
        name = self._host.database_name()
        counts[name] = counts.get(name, 0) + count

    def _report_deleted(self) -> None:
        """One line per database in the macro output, so deletes never go
        unnoticed. What a transaction left open (rolled back) is dropped."""
        self._tx_deleted = []
        for name, count in self._deleted.items():
            caches = "1 cache" if count == 1 else f"{count} caches"
            self._output(f"opensak: {caches} deleted from database {name!r}")
        self._deleted = {}

    def _writable_db(self) -> WritableDatabase:
        if self._sql_write_db is None:
            db = WritableDatabase(Path(get_engine().url.database or ""))
            # Attached again after opensak.switch_database().
            active = self._active_db_path()
            try:
                if self._macro_db_path is not None:
                    db.attach_macro_db(self._macro_db_path)
                for schema, (name, path) in list(self._attachments.items()):
                    if path == active:
                        del self._attachments[schema]
                        self._output(f"opensak.attach: detached {name!r} ({schema}): "
                                     "it is the active database now")
                    else:
                        db.attach_read_only(path, schema)
            except SqlError as exc:
                db.close()
                raise MacroError(str(exc)) from None
            self._sql_write_db = db
        return self._sql_write_db

    @staticmethod
    def _active_db_path() -> Path:
        return Path(get_engine().url.database or "").resolve()

    # opensak.attach(): other OpenSAK databases, attached read-only to the
    # active database's SQL connection, so one statement can join them. A
    # macro started with opensak.run_macro() sees its caller's; what it
    # changes is undone when it returns.

    def _attach(self, name=None, schema=None) -> str:
        func = "opensak.attach"
        db = self._require_database(name, func)
        if schema is None:
            schema = default_schema_name(db.name)
        elif not isinstance(schema, str):
            raise MacroError(f"{func}: the schema name must be a string, got {schema!r}")
        path = Path(db.path).resolve()
        if self._attachments.get(schema) == (db.name, path):
            return schema                       # e.g. by a caller of run_macro()
        self._no_transaction(func)
        if path == self._active_db_path():
            raise MacroError(f"{func}: {db.name!r} is the active database; its tables need no schema")
        if not db.exists:
            raise MacroError(f"{func}: the file of database {db.name!r} is missing: {db.path}")
        try:
            self._writable_db().attach_read_only(path, schema)
        except SqlError as exc:
            raise MacroError(f"{func}: {exc}") from None
        self._attachments[schema] = (db.name, path)
        self._output(f"{func}: attached {db.name!r} read-only as {schema}")
        return schema

    def _detach(self, schema=None) -> None:
        func = "opensak.detach"
        if not isinstance(schema, str) or schema not in self._attachments:
            raise MacroError(f"{func}: no database is attached as {schema!r} — see opensak.attached()")
        self._no_transaction(func)
        try:
            self._writable_db().detach(schema)
        except SqlError as exc:
            raise MacroError(f"{func}: {exc}") from None
        del self._attachments[schema]

    def _attached(self, lua):
        return lua.table_from([
            lua.table_from({"schema": schema, "name": name, "path": str(path), "opensak": True})
            for schema, (name, path) in self._attachments.items()
        ])

    def _restore_attachments(self, wanted: dict[str, tuple[str, Path]]) -> None:
        """Make *wanted* the attached databases again (after run_macro())."""
        if wanted == self._attachments:
            return
        db = self._sql_write_db
        for schema in [s for s, a in self._attachments.items() if wanted.get(s) != a]:
            if db is not None:
                db.detach(schema)
            del self._attachments[schema]
        for schema, (name, path) in wanted.items():
            if schema not in self._attachments:
                if db is not None:
                    db.attach_read_only(path, schema)
                self._attachments[schema] = (name, path)

    # The macro's own database: <macro file name>.db3 next to the macro file,
    # attached to the active database's SQL connection as `macro`. A macro
    # started with opensak.run_macro() gets its own file there once it calls
    # opensak.macro_db(); the caller's is attached again when it returns.

    def _macro_db(self) -> str:
        func = "opensak.macro_db"
        if self._macro_path is not None:
            file = self._macro_path
        elif self._base_dir is not None:
            file = self._base_dir / self._chunk_name
        else:
            raise MacroError(f"{func}: save the macro first — its database is named after the file")
        path = (file.parent / f"{file.stem}.db3").resolve()
        manager = get_db_manager()
        opensak_dbs = {Path(db.path).resolve() for db in manager.databases}
        opensak_dbs.add(Path(get_engine().url.database or "").resolve())
        if path in opensak_dbs:
            raise MacroError(f"{func}: {path} is an OpenSAK database; rename the macro")
        if self._in_transaction():
            raise MacroError(f"{func} cannot be used inside opensak.transaction()")
        if path != self._macro_db_path:
            # Creates (and writes) a file, so the macro's folder must be
            # writable for macros like any other file a macro writes; the
            # user is asked as usual if it is not listed.
            try:
                self._check_access(path, write=True, allow_database=True)
            except MacroError as exc:
                raise MacroError(f"{func}: {exc}") from None
        created = not path.exists()
        try:
            self._writable_db()
            self._attach_macro_db(path)
        except SqlError as exc:
            raise MacroError(f"{func}: {exc}") from None
        # Recorded so OpenSAK's backups include it (#942); best-effort.
        try:
            from opensak.backup.backupset import remember_macro_database
            remember_macro_database(path)
        except Exception:
            logger.warning("%s: could not record %s for backups", func, path, exc_info=True)
        if created:
            # Shown in the macro output, so a new file never appears silently.
            self._output(f"{func}: created {path}")
        return str(path)

    def _attach_macro_db(self, path: Optional[Path]) -> None:
        """Make *path* (None: no file) the one attached as `macro`. Without
        a write connection yet, _writable_db() attaches it when it opens."""
        if path == self._macro_db_path:
            return
        if self._sql_write_db is not None:
            self._sql_write_db.detach_macro_db()
            self._macro_db_path = None
            if path is not None:
                self._sql_write_db.attach_macro_db(path)
        self._macro_db_path = path

    def _sql_write(self, query=None, params=None) -> int:
        query, params = _sql_args("opensak.sql_write", query, params)
        self._require_write("opensak.sql_write")
        try:
            result = self._writable_db().execute(query, params)
        except SqlError as exc:
            raise MacroError(str(exc)) from None
        if result.codes:
            self._host.caches_changed(result.codes, result.added)
        self._caches_deleted(len(result.removed))
        return result.rows

    def _close_sql_write(self) -> None:
        """Close the write connection; rolls back a transaction still open
        (a macro that ended or failed inside opensak.transaction())."""
        if self._sql_write_db is not None:
            self._sql_write_db.close()
            self._sql_write_db = None

    # opensak.transaction(): the Lua wrapper (_TRANSACTION_LUA) calls these
    # around the macro's function. While it runs, all writes and reads of
    # the active database go through the WritableDatabase connection.

    def _in_transaction(self) -> bool:
        return self._sql_write_db is not None and self._sql_write_db.in_transaction

    def _tx_begin(self) -> None:
        self._require_write("opensak.transaction")
        try:
            self._writable_db().begin()
        except SqlError as exc:
            raise MacroError(f"opensak.transaction: {exc}") from None
        self._tx_deleted.append({})

    def _tx_end(self, commit=None) -> None:
        deleted = self._tx_deleted.pop() if self._tx_deleted else {}
        try:
            self._writable_db().end(bool(commit))
        except SqlError as exc:
            raise MacroError(f"opensak.transaction: {exc}") from None
        if commit:
            counts = self._tx_deleted[-1] if self._tx_deleted else self._deleted
            for name, count in deleted.items():
                counts[name] = counts.get(name, 0) + count

    def _resync(self) -> int:
        if not self._in_transaction():
            return 0
        try:
            return self._writable_db().resync()
        except SqlError as exc:
            raise MacroError(f"opensak.resync: {exc}") from None

    def _no_transaction(self, func: str) -> None:
        if self._in_transaction():
            raise MacroError(f"{func} cannot be used inside opensak.transaction()")

    @contextmanager
    def _active_session(self) -> Iterator[Any]:
        """get_session(), or a session in the open transaction — decided
        per use, so a loop may run on past the transaction's end."""
        if self._in_transaction():
            with self._writable_db().session() as session:
                yield session
        else:
            with get_session() as session:
                yield session

    # Raw SQL on the active database reads through the write connection
    # (sql.py), so its TEMP tables serve opensak.sql_write() as well.

    def _sql(self, db: Optional[DatabaseInfo] = None) -> ReadOnlyDatabase:
        if db is not None:
            if db.path not in self._other_sql:
                self._other_sql[db.path] = ReadOnlyDatabase(db.path)
            return self._other_sql[db.path]
        return self._writable_db().reader()

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
        every = spec.pop("all", False)
        if not isinstance(every, bool):
            raise MacroError(f"opensak.caches: all must be true or false, got {every!r}")
        db = self._other_database("opensak.caches", spec.pop("database", None))
        session_factory = self._session_factory(db, "opensak.caches")
        if every and spec:
            raise MacroError("opensak.caches: all = true cannot be combined with filter keys")
        if spec or db is not None or every:
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
        """Sessions on the active database (in the open transaction, if
        any), else read-only sessions on *db*."""
        if db is None:
            return self._active_session
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
        self._require_write("opensak.create_database")
        try:
            db = get_db_manager().new_database(name)
        except ValueError as exc:
            raise MacroError(f"opensak.create_database: {exc}") from None
        self._host.database_list_changed()
        return db.name

    # Centre points (GSAK's Centre): the saved ones are the user locations of
    # Settings; set_center() works like the centre drop-down of the toolbar.

    @staticmethod
    def _center(lua):
        from opensak.gui.settings import get_settings

        s = get_settings()
        name = s.active_home_name or ""
        return lua.table_from({
            "name": name,
            "lat": s.home_lat,
            "lon": s.home_lon,
            "saved": any(p.name == name for p in s.home_points) if name else False,
        })

    @staticmethod
    def _centers(lua):
        from opensak.gui.settings import get_settings, sort_home_points

        s = get_settings()
        active = s.active_home_name
        return lua.table_from([
            lua.table_from({"name": p.name, "lat": p.lat, "lon": p.lon,
                            "active": p.name == active})
            for p in sort_home_points(s.home_points)
        ])

    def _set_center(self, lua, *args):
        func = "opensak.set_center"
        from opensak.gui.settings import get_settings

        if not args or args[0] is None:
            raise MacroError(f"{func} expects the name of a saved centre point, "
                             "lat, lon or a coordinate string")
        points = get_settings().home_points
        saved = (next((p for p in points if p.name == args[0]), None)
                 if isinstance(args[0], str) and len(args) == 1 else None)
        if saved is not None:
            name, lat, lon = saved.name, saved.lat, saved.lon
        else:
            try:
                (point,), rest = take_points(args, 1, func)
            except MacroError as exc:
                if isinstance(args[0], str) and len(args) == 1:
                    raise MacroError(f"{func}: {args[0]!r} is neither a saved centre point "
                                     f"(see opensak.centers()) nor coordinates") from None
                raise MacroError(f"{func}: {exc}") from None
            if len(rest) > 1 or (rest and rest[0] is not None and not isinstance(rest[0], str)):
                raise MacroError(f"{func}: the name after the coordinates must be a string")
            lat, lon = point
            label = rest[0].strip() if rest and rest[0] else ""
            if any(p.name == label for p in points):
                raise MacroError(f"{func}: {label!r} is a saved centre point — use "
                                 f"opensak.set_center({label!r}) to make it the centre")
            name = label or f"📍 {helpers.format_coordinate(lat, lon)}"
        set_center = getattr(self._host, "set_center", None)
        if not callable(set_center):
            raise MacroError(f"{func} is not available here")
        # Every cache's distance and bearing is rewritten through OpenSAK's
        # own session, which would wait for the transaction's write lock.
        self._no_transaction(func)
        self._require_write(func)
        try:
            set_center(name, lat, lon)
        except Exception as exc:
            raise MacroError(f"{func}: {exc}") from None
        return self._center(lua)

    def _switch_database(self, name=None) -> None:
        self._no_transaction("opensak.switch_database")
        db = self._require_database(name, "opensak.switch_database")
        # The SQL connection, TEMP tables included, belongs to the old database.
        self._close_sql_write()
        try:
            self._host.switch_database(db.name)
        except Exception as exc:
            raise MacroError(f"opensak.switch_database: cannot open {db.name!r}: {exc}") from None

    def _transfer(self, func: str, copy_only: bool, target=None, options=None) -> int:
        # It reads (and for a move, deletes) through its own connections,
        # which would not see the transaction's changes, or would wait for
        # its write lock.
        self._no_transaction(func)
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
        self._require_write(func)
        try:
            count = transfer_caches(codes, active.path, db.path, copy_only=copy_only,
                                    if_exists=if_exists)
        except Exception as exc:
            raise MacroError(f"{func}: {exc}") from None
        if count and not copy_only:
            self._host.caches_removed()
        return count

    # opensak.import_gsak(): the GSAK import dialog without its questions
    # (importer/gsak_import_service.py). What the dialog asks is decided by
    # the options up front; the folder and write approvals still apply.

    def _import_gsak(self, lua, source=None, options=None):
        func = "opensak.import_gsak"
        from opensak.importer import gsak_import_service as service

        # It writes through its own sessions, which would wait for the
        # transaction's write lock.
        self._no_transaction(func)
        if not isinstance(source, str) or not source.strip():
            raise MacroError(f"{func} expects the path of a GSAK backup .zip, "
                             "GSAK folder or .db3 file")
        opts = self._gsak_import_options(func, options)
        quiet = bool(opts.pop("quiet", False))
        settings = service.GsakImportOptions(**opts)
        self._require_write(func)

        # %APPDATA%\gsak, $HOME/... — as the folder permissions expand them.
        path = expand_path(source.strip())
        if not path.is_absolute():
            path = (self._base_dir or macros_dir()) / path
        if not path.exists():
            raise MacroError(f"{func}: {path} does not exist")
        # GSAK databases are SQLite files, which the file functions never
        # read; this one only hands them to the importer, which opens them
        # read-only. A folder is approved as a whole, not per database.
        self._check_access(path / service.GSAK_SETTINGS_DB_NAME if path.is_dir() else path,
                           write=False, allow_database=True)

        handle = None
        if callable(getattr(self._host, "progress", None)):
            handle = self._host.progress("GSAK import", 0)
            self._progress_windows.append(handle)
        cancelled = False

        def on_progress(text: str, done: int, total: int) -> None:
            nonlocal cancelled
            if handle is not None:
                shown = f"{text}: {done} / {total}" if total else text
                if not handle.update(None, shown):
                    cancelled = True

        try:
            report = service.run_gsak_import(
                path, settings,
                progress_cb=on_progress,
                cancel_cb=lambda: cancelled or self._cancelled.is_set(),
                check_file=lambda p: self._check_access(p, write=False, allow_database=True),
            )
        except service.GsakImportError as exc:
            raise MacroError(f"{func}: {exc}") from None
        finally:
            if handle is not None:
                handle.close()
                self._progress_windows.remove(handle)

        self._gsak_import_notify(report)
        if not quiet:
            for line in report.summary_lines():
                self._output(line)
        self._check_cancelled()
        return self._gsak_report_table(lua, report)

    # opensak.import_gpx(): the GPX import dialog without its window — a
    # saved import setting, or one file or folder. The folder and write
    # approvals still apply.

    def _import_gpx(self, lua, source=None, options=None):
        func = "opensak.import_gpx"
        from opensak.db.database import session_for

        # It writes through its own sessions, which would wait for the
        # transaction's write lock.
        self._no_transaction(func)
        if not isinstance(source, str) or not source.strip():
            raise MacroError(f"{func} expects the name of a saved import setting, "
                             "or the path of a .gpx / .zip file or a folder")
        source = source.strip()
        opts = self._gpx_import_options(func, options)
        manager = get_db_manager()

        # (path, is a folder) of everything to import, and the target.
        db: Optional[DatabaseInfo] = None
        settings = self._load_import_settings(source)
        if settings is not None:
            entries = [(Path(f), False) for f in settings.files]
            entries += [(Path(f), True) for f in settings.folders]
            if settings.database and "database" not in opts:
                db = next((d for d in manager.databases
                           if str(d.path) == settings.database), None)
                if db is None:
                    raise MacroError(f"{func}: the database of import setting {source!r} "
                                     f"is no longer in the list: {settings.database}")
        else:
            # %USERPROFILE%\..., $HOME/... — as the folder permissions expand them.
            path = expand_path(source)
            if not path.is_absolute():
                path = (self._base_dir or macros_dir()) / path
            if not path.exists():
                raise MacroError(f"{func}: {source!r} is neither a saved import setting "
                                 f"nor an existing file or folder ({path})")
            if not path.is_dir() and path.suffix.lower() not in FOLDER_IMPORT_SUFFIXES:
                raise MacroError(f"{func}: {path} is not a .gpx or .zip file")
            entries = [(path, path.is_dir())]
        if "database" in opts:
            db = self._require_database(opts["database"], func)
        if db is None:
            db = manager.active
            if db is None:
                raise MacroError(f"{func}: no database is open")
        if not db.exists:
            raise MacroError(f"{func}: the file of database {db.name!r} is missing: {db.path}")
        self._require_write(func)

        # The files to import, each once. Files and folders of a setting
        # that were moved or deleted since are left out, as in the dialog,
        # and a refused one fails only that entry.
        missing: list[str] = []
        rows: list[dict[str, Any]] = []
        run: list[Path] = []
        for entry, is_folder in entries:
            if not (entry.is_dir() if is_folder else entry.is_file()):
                missing.append(str(entry))
                continue
            try:
                self._check_access(entry / "_" if is_folder else entry, write=False)
            except MacroError as exc:
                if settings is None:
                    raise
                rows.append({"path": str(entry), "error": str(exc)})
                continue
            for f in folder_import_files(entry) if is_folder else [entry]:
                if f not in run:
                    run.append(f)

        handle = None
        if callable(getattr(self._host, "progress", None)):
            handle = self._host.progress("GPX import", len(run))
            self._progress_windows.append(handle)
        cancelled = False
        other_db = db.path != manager.active_path
        try:
            for i, f in enumerate(run):
                if self._cancelled.is_set() or (
                        handle is not None and not handle.update(i, f.name)):
                    cancelled = True
                    break

                def on_progress(count: int, name: str = f.name) -> None:
                    nonlocal cancelled
                    if handle is not None and not handle.update(None, f"{name}: {count} caches"):
                        cancelled = True

                row: dict[str, Any] = {"path": str(f)}
                try:
                    with (session_for(db.path) if other_db else get_session()) as session:
                        r = import_file(f, session, progress_cb=on_progress)
                except Exception as exc:
                    logger.exception("%s: %s failed", func, f)
                    row["error"] = str(exc) or type(exc).__name__
                else:
                    row.update(created=r.created, updated=r.updated, skipped=r.skipped,
                               waypoints=r.waypoints, warnings=len(r.warnings),
                               errors=r.errors)
                rows.append(row)
                # A file is not stopped half way; Cancel stops before the next.
                if cancelled:
                    break
        finally:
            if handle is not None:
                handle.close()
                self._progress_windows.remove(handle)

        def total(key: str) -> int:
            return sum(r.get(key, 0) for r in rows)

        errors = sum(len(r["errors"]) if "errors" in r else 1 for r in rows)
        if not other_db and (total("created") or total("updated")):
            self._host.caches_changed([], True)

        kind = "saved setting" if settings is not None else "folder" if entries[0][1] else "file"
        lines = [f"GPX import from {source} ({kind}) into {db.name}"]
        for r in rows:
            name = Path(r["path"]).name or r["path"]
            if "error" in r:
                lines.append(f"  {name}: failed ({r['error']})")
            else:
                lines.append(f"  {name}: {r['created']} new, {r['updated']} updated, "
                             f"{r['skipped']} skipped, {r['waypoints']} waypoints, "
                             f"{len(r['errors'])} errors")
        if not rows:
            lines.append("  no .gpx or .zip files to import")
        if missing:
            lines.append(f"  {len(missing)} files or folders of the setting no longer exist")
        if cancelled:
            lines.append("  cancelled")
        for line in lines:
            logger.info("%s", line)
        if not opts.get("quiet", False):
            for line in lines:
                self._output(line)
        self._check_cancelled()

        for r in rows:
            if "errors" in r:
                r["errors"] = lua.table_from(r["errors"][:GPX_REPORT_MAX_ERRORS])
        result: dict[str, Any] = {
            "ok": not errors and not cancelled,
            "cancelled": cancelled,
            "source": source,
            "kind": kind.split()[-1],
            "database": db.name,
            "created": total("created"),
            "updated": total("updated"),
            "skipped": total("skipped"),
            "waypoints": total("waypoints"),
            "errors": errors,
            "files": lua.table_from([lua.table_from(r) for r in rows]),
            "missing": lua.table_from(missing),
        }
        return lua.table_from(result)

    def _load_import_settings(self, name: str) -> Optional[GpxImportSettings]:
        """The GPX import setting saved as *name*, or None."""
        for path in GpxImportProfile.list_profiles(self._import_settings_dir):
            try:
                profile = GpxImportProfile.load(path)
            except Exception:
                continue
            if profile.name == name:
                return profile.settings
        return None

    @staticmethod
    def _gpx_import_options(func: str, options: Any) -> dict[str, Any]:
        """The options table of opensak.import_gpx() as a dict."""
        if options is None:
            return {}
        if not hasattr(options, "items"):
            raise MacroError(f'{func}: options must be a table, e.g. {{ database = "PQ" }}')
        raw = {k: _from_lua(v) for k, v in options.items()}
        unknown = set(raw) - set(GPX_IMPORT_KEYS)
        if unknown:
            raise MacroError(f"{func}: unknown option(s) {sorted(map(str, unknown))}; "
                             f"valid: {', '.join(GPX_IMPORT_KEYS)}")
        if "quiet" in raw and not isinstance(raw["quiet"], bool):
            raise MacroError(f"{func}: quiet must be true or false")
        return raw

    @staticmethod
    def _gsak_import_options(func: str, options: Any) -> dict[str, Any]:
        """The options table of opensak.import_gsak() as GsakImportOptions
        keyword arguments (plus `quiet`)."""
        from opensak.importer.gsak_import_service import IF_EXISTS, settings_mode

        if options is None:
            return {}
        if not hasattr(options, "items"):
            raise MacroError(f'{func}: options must be a table, e.g. {{ if_exists = "merge" }}')
        raw = {k: _from_lua(v) for k, v in options.items()}
        unknown = set(raw) - set(GSAK_IMPORT_KEYS)
        if unknown:
            raise MacroError(f"{func}: unknown option(s) {sorted(map(str, unknown))}; "
                             f"valid: {', '.join(GSAK_IMPORT_KEYS)}")
        result: dict[str, Any] = {}

        def names(key: str) -> list[str]:
            value = raw[key]
            values = [value] if isinstance(value, str) else ([] if value == {} else value)
            if not isinstance(values, list) or not all(
                    isinstance(v, str) and v.strip() for v in values):
                raise MacroError(f'{func}: {key} must be a name or a list of names, '
                                 f'e.g. {{"AllCH", "Holidays"}}')
            return [v.strip() for v in values]

        if "databases" in raw:
            result["databases"] = names("databases")
        if "exclude" in raw:
            result["exclude"] = names("exclude")
        if "target" in raw:
            target = raw["target"]
            if isinstance(target, str) and target.strip():
                result["target"] = target.strip()
            elif isinstance(target, dict) and target and all(
                    isinstance(k, str) and isinstance(v, str) and v.strip()
                    for k, v in target.items()):
                result["targets"] = {k: v.strip() for k, v in target.items()}
            else:
                raise MacroError(f'{func}: target must be a database name, or a table '
                                 f'of GSAK name = database name, e.g. {{ AllCH = "Switzerland" }}')
        if_exists = raw.get("if_exists", "skip")
        if if_exists not in IF_EXISTS:
            raise MacroError(f"{func}: if_exists must be one of {', '.join(IF_EXISTS)}, "
                             f"got {if_exists!r}")
        result["if_exists"] = if_exists
        for key in ("filters", "locations"):
            value = raw.get(key, False)
            if value is True:
                result[key] = settings_mode(if_exists)
            elif value in ("skip", "overwrite"):
                result[key] = value
            elif value is not False:
                raise MacroError(f'{func}: {key} must be true, false, "skip" or '
                                 f'"overwrite", got {value!r}')
        for key in ("create", "quiet"):
            if key in raw:
                if not isinstance(raw[key], bool):
                    raise MacroError(f"{func}: {key} must be true or false")
                result[key] = raw[key]
        return result

    def _gsak_import_notify(self, report) -> None:
        """Tell the host what the import changed, like the dialog's signals."""
        from opensak.importer.gsak_import_service import ACTION_OVERWRITTEN

        if report.new_databases:
            self._host.database_list_changed()
        active = [d for d in report.databases if d.active and d.result is not None]
        if any(d.action == ACTION_OVERWRITTEN for d in active):
            self._host.caches_removed()
        elif any(d.result.created or d.result.updated for d in active):
            self._host.caches_changed([], True)
        loc = report.locations
        if loc is not None and (loc.added or loc.updated):
            notify = getattr(self._host, "locations_changed", None)
            if callable(notify):
                notify()
        if report.filters is not None and report.filters.written:
            notify = getattr(self._host, "filter_profiles_changed", None)
            if callable(notify):
                notify()

    @staticmethod
    def _gsak_report_table(lua, report):
        databases = []
        for d in report.databases:
            row: dict[str, Any] = {"gsak": d.gsak_name, "target": d.target,
                                   "action": d.action, "cleared": d.cleared}
            if d.reason:
                row["reason"] = d.reason
            r = d.result
            if r is not None:
                row.update(
                    created=r.created, updated=r.updated, skipped=r.skipped,
                    waypoints=r.waypoints, logs=r.logs, attributes=r.attributes,
                    notes=r.notes, trackables=r.trackables, corrected=r.corrected,
                    note_images=r.note_images_replaced, warnings=len(r.warnings),
                    errors=lua.table_from(r.errors[:GSAK_REPORT_MAX_ERRORS]),
                )
            databases.append(lua.table_from(row))
        result: dict[str, Any] = {
            "ok": report.ok,
            "cancelled": report.cancelled,
            "source": str(report.source),
            "kind": report.kind,
            "created": report.total("created"),
            "updated": report.total("updated"),
            "skipped": report.total("skipped"),
            "errors": report.errors,
            "databases": lua.table_from(databases),
            "new_databases": lua.table_from(report.new_databases),
            "notes": lua.table_from(report.notes),
        }
        if report.locations is not None:
            loc = report.locations
            result["locations"] = lua.table_from({
                "added": len(loc.added), "updated": len(loc.updated),
                "skipped": len(loc.skipped), "invalid": report.locations_invalid,
            })
        elif report.locations_error:
            result["locations"] = lua.table_from({"error": report.locations_error})
        if report.filters is not None:
            f = report.filters
            result["filters"] = lua.table_from({
                "imported": f.written, "skipped": f.skipped, "failed": f.failed,
            })
        elif report.filters_error:
            result["filters"] = lua.table_from({"error": report.filters_error})
        return lua.table_from(result)

    def _confirm(self, message=None) -> bool:
        if not isinstance(message, str) or not message.strip():
            raise MacroError("opensak.confirm expects a message")
        return bool(self._host.confirm(message))

    def _readable_file(self, path: str, allow_database: bool = False) -> Path:
        """*path* resolved against the macro's folder and checked against the
        folder permissions (asking the user for an unapproved folder)."""
        file = Path(path).expanduser()
        if not file.is_absolute():
            file = (self._base_dir or macros_dir()) / file
        return self._check_access(file, write=False, allow_database=allow_database)

    def _read_csv(self, lua, path=None, sep=None):
        if not isinstance(path, str) or not path.strip():
            raise MacroError("opensak.read_csv expects a file path")
        if sep is not None and not isinstance(sep, str):
            raise MacroError("opensak.read_csv: separator must be a string")
        rows = read_csv_rows(self._readable_file(path), sep)
        return lua.table_from([lua.table_from(r) for r in rows])

    def _write_text(self, path=None, text=None, options=None) -> str:
        func = "opensak.write_text"
        if not isinstance(path, str) or not path.strip():
            raise MacroError(f"{func} expects a file path")
        if isinstance(text, (int, float)) and not isinstance(text, bool):
            text = macro_forms.format_number(text)
        if not isinstance(text, (str, bytes)):
            raise MacroError(f"{func}: text must be a string, got {_lua_kind(text) or repr(text)}")
        opts = {} if options is None else _from_lua(options)
        if not isinstance(opts, dict) or set(opts) - {"append"}:
            raise MacroError(f"{func}: options must be a table, e.g. {{ append = true }}")
        append = opts.get("append", False)
        if not isinstance(append, bool):
            raise MacroError(f"{func}: append must be true or false, got {append!r}")
        data = text.encode("utf-8") if isinstance(text, str) else text
        file = Path(path.strip()).expanduser()
        if not file.is_absolute():
            file = (self._base_dir or macros_dir()) / file
        # Refused before the user is asked to approve the folder.
        try:
            reason = write_denied_reason(resolve_path(file), WRITE_TEXT_SUFFIXES)
        except (OSError, RuntimeError) as exc:
            raise MacroError(f"cannot resolve {file}: {exc}") from None
        if reason is not None:
            raise MacroError(f"{func}: macros may not write {file} — {reason}")
        target = self._check_access(file, write=True)
        permissions = self._run_permissions
        if (target, True) in self._picked:
            permissions = with_grant(permissions, target.parent, True)
        try:
            if append and target.is_file():
                data = target.read_bytes() + data
            if len(data) > MAX_WRITE_BYTES:
                raise MacroError(
                    f"{func}: the file would be larger than "
                    f"{MAX_WRITE_BYTES // (1024 * 1024)} MB"
                )
            written = safe_write(target, data, permissions=permissions,
                                 allowed_suffixes=WRITE_TEXT_SUFFIXES)
            self._written.add(written)
            return str(written)
        except FolderAccessDenied as exc:
            raise MacroError(str(exc)) from None
        except OSError as exc:
            raise MacroError(f"cannot write {target}: {exc}") from None

    def _open(self, path=None) -> None:
        func = "opensak.open"
        if not isinstance(path, str) or not path.strip():
            raise MacroError(f"{func} expects a file path")
        if path.strip().find(":") > 1:
            # file:, http:, mailto:, ... — only a drive letter (C:\) has its
            # colon second.
            raise MacroError(f"{func} opens files only, not URLs: {path}")
        file = Path(path.strip()).expanduser()
        if not file.is_absolute():
            file = (self._base_dir or macros_dir()) / file
        try:
            target = resolve_path(file)
        except (OSError, RuntimeError) as exc:
            raise MacroError(f"cannot resolve {file}: {exc}") from None
        # A ":" would name an NTFS alternate data stream ("a.html:x.bat").
        if ":" in target.name or target.suffix.lower() not in OPEN_SUFFIXES:
            raise MacroError(f"{func}: only these file types can be opened: "
                             f"{' '.join(OPEN_SUFFIXES)}")
        reason = protected_reason(target)
        if reason is not None:
            raise MacroError(f"{func}: macros may not open {target} — {reason}")
        if not target.is_file():
            raise MacroError(f"{func}: file not found: {target}")
        if self._opened >= MAX_OPENS_PER_RUN:
            raise MacroError(f"{func}: at most {MAX_OPENS_PER_RUN} files per run")
        open_file = self._ui("open_file", func)
        # The macro's own output opens directly; any other file is put to
        # the user first.
        if target not in self._written and not target.is_relative_to(temp_dir()):
            if not self._host.confirm(
                    f"The macro wants to open this file in its default app:\n\n{target}\n\n"
                    "Open it?"):
                raise MacroError(f"{func}: the user did not allow opening {target}")
        self._opened += 1
        if not open_file(target):
            raise MacroError(f"{func}: no app could open {target}")

    # ── opensak.stats.* / opensak.html.* ─────────────────────────────────
    # The rows are Lua tables; only the fields a function needs are read,
    # which keeps a list of 20 000 finds fast.

    @staticmethod
    def _row_list(func: str, rows: Any) -> list:
        """The Lua rows 1..n, each checked to be a table."""
        if not _is_lua_table(rows):
            raise MacroError(f"{func} expects a list of rows (tables)")
        result = [rows[i] for i in range(1, len(rows) + 1)]
        if result:
            # All rows are of one Python class (a Lua table) unless one is not.
            table_class = type(result[0])
            if not _is_lua_table(result[0]) or any(type(r) is not table_class for r in result):
                for i, row in enumerate(result, 1):
                    if not _is_lua_table(row):
                        raise MacroError(f"{func}: row {i} is not a table")
        return result

    @staticmethod
    def _fields(rows: list, names: Any) -> list[dict[str, Any]]:
        """{name: value} of the given fields of each Lua row."""
        names = [n for n in dict.fromkeys(names) if n is not None]
        plain = (str, int, float, bool, type(None))
        result = []
        for row in rows:
            values = {}
            for n in names:
                v = row[n]
                values[n] = v if type(v) in plain else _from_lua(v)
            result.append(values)
        return result

    @staticmethod
    def _field_name(func: str, name: Any, what: str = "field") -> str:
        if not isinstance(name, str) or not name:
            raise MacroError(f"{func}: {what} must be a field name, got {name!r}")
        return name

    @staticmethod
    def _html_options(func: str, kind: str, options: Any) -> dict[str, Any]:
        return MacroRuntime._options(func, HTML_OPTIONS[kind], options)

    @staticmethod
    def _stats_options(func: str, kind: str, options: Any) -> dict[str, Any]:
        return MacroRuntime._options(func, STATS_OPTIONS[kind], options)

    @staticmethod
    def _options(func: str, names: dict[str, str], options: Any) -> dict[str, Any]:
        opts = {} if options is None else _from_lua(options)
        if opts == []:
            opts = {}
        if not isinstance(opts, dict):
            raise MacroError(f"{func}: options must be a table")
        unknown = set(opts) - set(names)
        if unknown:
            raise MacroError(
                f"{func}: unknown option(s) {sorted(map(str, unknown))}; "
                f"valid: {', '.join(sorted(names))}"
            )
        # 10.0 from Lua arithmetic counts as the whole number 10.
        return {
            names[k]: int(v) if isinstance(v, float) and v.is_integer() else v
            for k, v in opts.items()
        }

    @staticmethod
    def _report_call(func: str, fn: Callable, *args, **kwargs) -> Any:
        try:
            return fn(*args, **kwargs)
        except (report_html.HtmlError, stats_tables.StatsError, TypeError) as exc:
            raise MacroError(f"{func}: {exc}") from None

    def _stats_group(self, lua, rows=None, by=None, options=None):
        func = "opensak.stats.group"
        rows = self._row_list(func, rows)
        by = self._field_name(func, by, "by")
        opts = self._stats_options(func, "group", options)
        for key in ("count_distinct", "sum"):
            if key in opts:
                self._field_name(func, opts[key], key)
        data = self._fields(rows, (by, opts.get("count_distinct"), opts.get("sum")))
        return lua.table_from(self._report_call(func, stats_tables.group, data, by, **opts),
                              recursive=True)

    def _stats_fill(self, lua, rows=None, options=None):
        func = "opensak.stats.fill"
        data = [_from_lua(r) for r in self._row_list(func, rows)]
        opts = self._stats_options(func, "fill", options)
        return lua.table_from(self._report_call(func, stats_tables.fill, data, **opts),
                              recursive=True)

    def _stats_running(self, lua, rows=None, field="count", into="total"):
        func = "opensak.stats.running"
        data = [_from_lua(r) for r in self._row_list(func, rows)]
        field = self._field_name(func, field)
        into = self._field_name(func, into, "into")
        return lua.table_from(self._report_call(func, stats_tables.running, data, field, into),
                              recursive=True)

    def _stats_pivot(self, lua, rows=None, row_field=None, col_field=None, options=None):
        func = "opensak.stats.pivot"
        rows = self._row_list(func, rows)
        row_field = self._field_name(func, row_field, "row field")
        col_field = self._field_name(func, col_field, "column field")
        opts = self._stats_options(func, "pivot", options)
        for key in ("rows_order", "cols_order"):
            if key in opts and not isinstance(opts[key], list):
                raise MacroError(f"{func}: {'rows' if key == 'rows_order' else 'cols'} "
                                 "must be a list of values")
        data = self._fields(rows, (row_field, col_field, opts.get("sum")))
        return lua.table_from(
            self._report_call(func, stats_tables.pivot, data, row_field, col_field, **opts),
            recursive=True,
        )

    def _stats_every(self, lua, rows=None, step=None, options=None):
        func = "opensak.stats.every"
        rows = self._row_list(func, rows)
        if isinstance(step, float) and step.is_integer():
            step = int(step)
        opts = self._stats_options(func, "every", options)
        date_field = opts.get("date_field")
        if date_field is not None:
            self._field_name(func, date_field, "date")
        data = [{"_index": i, **({date_field: _from_lua(r[date_field])} if date_field else {})}
                for i, r in enumerate(rows)]
        picked = self._report_call(func, stats_tables.every, data, step, **opts)
        result = []
        for p in picked:
            copy = lua.table()
            for k, v in rows[p["_index"]].items():
                copy[k] = v
            copy["n"] = p["n"]
            if "days" in p:
                copy["days"] = p["days"]
            result.append(copy)
        return lua.table_from(result)

    def _stats_summary(self, lua, rows=None, field=None):
        func = "opensak.stats.summary"
        rows = self._row_list(func, rows)
        field = self._field_name(func, field)
        data = self._fields(rows, (field,))
        return lua.table_from(self._report_call(func, stats_tables.summary, data, field))

    def _stats_sort(self, lua, rows=None, *fields):
        func = "opensak.stats.sort"
        rows = self._row_list(func, rows)
        if not fields:
            raise MacroError(f'{func} needs at least one field, e.g. {func}(rows, "found_date")')
        names = [self._field_name(func, f).lstrip("-") for f in fields]
        order = self._report_call(func, stats_tables.sort_order, self._fields(rows, names), fields)
        return lua.table_from([rows[i] for i in order])

    def _stats_streaks(self, lua, rows=None, field=None):
        func = "opensak.stats.streaks"
        rows = self._row_list(func, rows)
        field = self._field_name(func, field)
        result = self._report_call(func, stats_tables.streaks, self._fields(rows, (field,)), field)
        return lua.table_from(result, recursive=True)

    def _date_diff(self, a=None, b=None) -> int:
        return self._report_call("opensak.date.diff", stats_tables.days_between, a, b)

    def _date_period(self, value=None, length=None) -> str:
        return self._report_call("opensak.date.period", stats_tables.period, value, length)

    # ── opensak.chart.* / opensak.geo.* ──────────────────────────────────
    # Plain SVG strings (opensak.stats.charts / maps): no script, so they
    # work in any HTML page or as a .svg file.

    def _chart(self, kind: str, rows=None, options=None) -> str:
        func = f"opensak.chart.{kind}"
        rows = _from_lua(rows)
        matrix_input = isinstance(rows, dict) and "cells" in rows
        if kind == "heatmap":
            if not isinstance(rows, dict):
                raise MacroError(f"{func} expects a matrix, e.g. from opensak.stats.pivot()")
        elif rows == {}:
            rows = []    # an empty Lua table
        if kind != "heatmap" and not isinstance(rows, list) \
                and not (kind == "bar" and matrix_input):
            raise MacroError(
                f"{func} expects a list of rows, e.g. {func}({{ {{ key = 2024, count = 12 }} }})"
            )
        opts = {} if options is None else _from_lua(options)
        if opts == []:
            opts = {}
        if not isinstance(opts, dict):
            raise MacroError(f'{func}: options must be a table, e.g. {{ title = "Finds" }}')
        unknown = set(opts) - CHART_KEYS[kind]
        if unknown:
            raise MacroError(
                f"{func}: unknown option(s) {sorted(map(str, unknown))}; "
                f"valid: {', '.join(sorted(CHART_KEYS[kind]))}"
            )
        for key in ("x", "title", "color", "row_title", "col_title"):
            if key in opts and not isinstance(opts[key], str):
                raise MacroError(f"{func}: {key} must be a string, got {opts[key]!r}")
        if "y" in opts and not isinstance(opts["y"], str) \
                and not (kind == "bar" and isinstance(opts["y"], list)):
            raise MacroError(f"{func}: y must be a field name"
                             + (" or a list of them" if kind == "bar" else "")
                             + f", got {opts['y']!r}")
        for key in ("horizontal", "area", "labels"):
            if key in opts and not isinstance(opts[key], bool):
                raise MacroError(f"{func}: {key} must be true or false, got {opts[key]!r}")
        try:
            if kind == "bar" and matrix_input:
                # A matrix from stats.pivot: one stacked bar per row.
                if "x" in opts or "y" in opts:
                    raise MacroError(f"{func}: x and y are taken from the matrix")
                rows, fields, names = charts.matrix_rows(rows)
                opts = {"names": names, **opts, "y": fields}
            # kind is one of CHART_KEYS: charts.bar, line, heatmap or rose.
            draw: Callable[..., str] = getattr(charts, kind)
            return draw(rows, **opts)
        except charts.ChartError as exc:
            raise MacroError(f"{func}: {exc}") from None

    def _maps(self) -> Any:
        """The boundary data for maps, or None if it is not installed."""
        if self._map_data is None:
            from opensak.geo import BoundaryStore

            store = BoundaryStore()
            self._map_data = report_maps.MapData(store) if store.available() else False
        return self._map_data or None

    @staticmethod
    def _no_map(title: Any) -> str:
        text = "Map not available: the boundary data is not installed."
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="40" role="img" '
                f'aria-label="{report_html.escape(title or text)}" style="max-width:100%">'
                f'<text x="0" y="24" font-size="13" fill="currentColor">'
                f'{report_html.escape(text)}</text></svg>')

    def _map_options(self, func: str, kind: str, options: Any) -> dict[str, Any]:
        opts = {} if options is None else _from_lua(options)
        if opts == []:
            opts = {}
        if not isinstance(opts, dict):
            raise MacroError(f'{func}: options must be a table, e.g. {{ layer = "state" }}')
        unknown = set(opts) - MAP_KEYS[kind]
        if unknown:
            raise MacroError(
                f"{func}: unknown option(s) {sorted(map(str, unknown))}; "
                f"valid: {', '.join(sorted(MAP_KEYS[kind]))}"
            )
        for key in ("key", "value", "lat", "lon", "label", "layer", "title", "color", "extent"):
            if key in opts and not isinstance(opts[key], str):
                raise MacroError(f"{func}: {key} must be a string, got {opts[key]!r}")
        if "legend" in opts and not isinstance(opts["legend"], bool):
            raise MacroError(f"{func}: legend must be true or false, got {opts['legend']!r}")
        return opts

    def _chart_map(self, rows=None, options=None) -> str:
        func = "opensak.chart.map"
        rows = self._row_list(func, rows)
        opts = self._map_options(func, "map", options)
        data = self._maps()
        if data is None:
            return self._no_map(opts.get("title"))
        fields = self._fields(rows, (opts.get("key", "key"), opts.get("value", "count")))
        try:
            return report_maps.choropleth(data, fields, **opts)
        except charts.ChartError as exc:
            raise MacroError(f"{func}: {exc}") from None

    def _chart_points(self, rows=None, options=None) -> str:
        func = "opensak.chart.points"
        rows = self._row_list(func, rows)
        opts = self._map_options(func, "points", options)
        data = self._maps()
        if data is None:
            return self._no_map(opts.get("title"))
        fields = self._fields(rows, (opts.get("lat", "lat"), opts.get("lon", "lon"),
                                     opts.get("label")))
        try:
            return report_maps.points(data, fields, **opts)
        except charts.ChartError as exc:
            raise MacroError(f"{func}: {exc}") from None

    def _geo_options(self, lua, func: str, kind: str, options: Any) -> dict[str, Any]:
        """The options of opensak.geo.*; rows / around are read field by
        field, like the rows of stats.*."""
        if options is not None and not _is_lua_table(options):
            raise MacroError(f'{func}: options must be a table, e.g. {{ within = "Switzerland" }}')
        rows_value = options["rows"] if options is not None else None
        around_value = options["around"] if options is not None else None
        plain = {k: v for k, v in (options.items() if options is not None else [])
                 if k not in ("rows", "around")}
        opts = self._options(func, GEO_OPTIONS[kind], lua.table_from(plain))
        for key in ("key", "value", "lat", "lon", "extent"):
            if key in opts and not isinstance(opts[key], str):
                raise MacroError(f"{func}: {key} must be a string, got {opts[key]!r}")
        if rows_value is not None:
            opts["rows"] = self._fields(self._row_list(f"{func}: rows", rows_value),
                                        (opts.get("key", "key"), opts.get("value", "count")))
        if around_value is not None and "around" in GEO_OPTIONS[kind]:
            opts["around"] = self._fields(self._row_list(f"{func}: around", around_value),
                                          (opts.get("lat", "lat"), opts.get("lon", "lon")))
        elif around_value is not None:
            raise MacroError(f"{func}: unknown option(s) ['around']; "
                             f"valid: {', '.join(sorted(GEO_OPTIONS[kind]))}")
        return opts

    def _geo_regions(self, lua, layer="country", options=None):
        func = "opensak.geo.regions"
        opts = self._geo_options(lua, func, "regions", options)
        data = self._maps()
        if data is None:
            return None
        try:
            if "rows" not in opts:
                return lua.table_from(report_maps.regions(data, layer, opts.get("within")),
                                      recursive=True)
            found, unmatched = report_maps.match(data, opts.pop("rows"), layer, **opts)
        except charts.ChartError as exc:
            raise MacroError(f"{func}: {exc}") from None
        return lua.table_from(found, recursive=True), lua.table_from(unmatched)

    def _geo_outlines(self, lua, layer="country", options=None):
        func = "opensak.geo.outlines"
        opts = self._geo_options(lua, func, "outlines", options)
        data = self._maps()
        if data is None:
            return None
        try:
            collection, unmatched = report_maps.outlines(data, layer, **opts)
        except charts.ChartError as exc:
            raise MacroError(f"{func}: {exc}") from None
        return (lua.table_from(collection, recursive=True),
                lua.table_from(unmatched))

    def _html_matrix(self, matrix=None, options=None) -> str:
        func = "opensak.html.matrix"
        data = _from_lua(matrix)
        if not isinstance(data, dict):
            raise MacroError(f"{func} expects a matrix, e.g. from opensak.stats.pivot()")
        opts = self._html_options(func, "matrix", options)
        return self._report_call(func, report_html.matrix, data, **opts)

    def _html_table(self, rows=None, options=None) -> str:
        func = "opensak.html.table"
        rows = self._row_list(func, rows)
        opts = self._html_options(func, "table", options)
        columns, link = opts.get("columns"), opts.get("link")
        if link is not None and not (isinstance(link, dict)
                                     and all(isinstance(v, str) for v in link.values())):
            raise MacroError(f'{func}: link must be a table, e.g. {{ code = "url" }}')
        if columns is None:
            data = [_from_lua(r) for r in rows]
        else:
            names = [c if isinstance(c, str) else (c[0] if isinstance(c, list) and c else None)
                     for c in (columns if isinstance(columns, list) else [])]
            data = self._fields(rows, [*names, *(link or {}).values()])
        if "bars" in opts and not isinstance(opts["bars"], list):
            raise MacroError(f'{func}: bars must be a list of field names, e.g. {{ "count" }}')
        return self._report_call(func, report_html.table, data, **opts)

    @staticmethod
    def _html_escape(value=None) -> str:
        if _is_lua_table(value) or _is_lua_function(value):
            raise MacroError("opensak.html.escape expects a string, number, boolean or nil")
        return report_html.escape(value)

    def _html_tiles(self, items=None) -> str:
        func = "opensak.html.tiles"
        data = _from_lua(items)
        return self._report_call(func, report_html.tiles, [] if data == {} else data)

    def _html_columns(self, *parts) -> str:
        func = "opensak.html.columns"
        if len(parts) == 1 and _is_lua_table(parts[0]):
            parts = tuple(_from_lua(parts[0]) or [])
        return self._report_call(func, report_html.columns, list(parts))

    def _html_page(self, spec=None) -> str:
        func = "opensak.html.page"
        if not _is_lua_table(spec):
            raise MacroError(f'{func} expects a table, e.g. {func}{{ title = "Report", body }}')
        # The array part of the table is the body: html.page{ title = "x", part1, part2 }.
        parts = [spec[i] for i in range(1, len(spec) + 1)]
        named = {k: v for k, v in spec.items() if not isinstance(k, int) or isinstance(k, bool)}
        unknown = set(named) - set(HTML_OPTIONS["page"])
        if unknown:
            raise MacroError(
                f"{func}: unknown key(s) {sorted(map(str, unknown))}; "
                f"valid: {', '.join(sorted(HTML_OPTIONS['page']))}"
            )
        opts: dict[str, Any] = {str(k): _from_lua(v) for k, v in named.items()}
        body = opts.pop("body", None)
        if body is not None:
            parts.extend(body if isinstance(body, list) else [body])
        return self._report_call(func, report_html.page, parts, **opts)

    def _read_text(self, path=None, options=None) -> str:
        func = "opensak.read_text"
        if not isinstance(path, str) or not path.strip():
            raise MacroError(f"{func} expects a file path")
        opts = {} if options is None else _from_lua(options)
        if not isinstance(opts, dict) or set(opts) - {"encoding"}:
            raise MacroError(f'{func}: options must be a table, e.g. {{ encoding = "cp1252" }}')
        names = ", ".join(f'"{e}"' for e in READ_TEXT_ENCODINGS)
        encoding = opts.get("encoding", "utf-8")
        try:
            codec = codecs.lookup(encoding).name if isinstance(encoding, str) else None
        except LookupError:
            codec = None
        # "latin1", "UTF8", "windows-1252", ... name the same codecs.
        if codec not in {codecs.lookup(e).name for e in READ_TEXT_ENCODINGS}:
            raise MacroError(f"{func}: encoding must be one of {names}, got {encoding!r}")
        file = self._readable_file(path.strip())
        try:
            if file.is_dir():
                raise MacroError(f"{func}: {file} is a folder — use opensak.list_dir()")
            if file.stat().st_size > MAX_READ_BYTES:
                raise MacroError(
                    f"{func}: {file.name} is larger than "
                    f"{MAX_READ_BYTES // (1024 * 1024)} MB"
                )
            data = file.read_bytes()
        except FileNotFoundError:
            raise MacroError(f"{func}: file not found: {file}") from None
        except OSError as exc:
            raise MacroError(f"cannot read {file}: {exc}") from None
        try:
            text = data.decode("utf-8-sig" if codec == "utf-8" else codec)
        except UnicodeDecodeError as exc:
            raise MacroError(
                f"{func}: {file.name} is not UTF-8 text (byte {exc.start}); "
                f'for a file from an older Windows program try {{ encoding = "cp1252" }}'
            ) from None
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def _exists(self, func: str, path, folder: bool) -> bool:
        if not isinstance(path, str) or not path.strip():
            raise MacroError(f"{func} expects a {'folder' if folder else 'file'} path")
        # Only asks whether it exists, so a database file (e.g. a GSAK
        # sqlite.db3) can be checked; reading it is still refused.
        target = self._readable_file(path.strip(), allow_database=True)
        return target.is_dir() if folder else target.is_file()

    def _list_dir(self, lua, folder=None, pattern=None):
        func = "opensak.list_dir"
        if not isinstance(folder, str) or not folder.strip():
            raise MacroError(f"{func} expects a folder path")
        if pattern is not None and (
            not isinstance(pattern, str) or not pattern or "/" in pattern or "\\" in pattern
        ):
            raise MacroError(f'{func}: pattern must be a file name pattern such as "*.gpx"')
        path = Path(folder.strip()).expanduser()
        if not path.is_absolute():
            path = (self._base_dir or macros_dir()) / path
        try:
            resolved = resolve_path(path)
        except (OSError, RuntimeError) as exc:
            raise MacroError(f"cannot resolve {path}: {exc}") from None
        # Listing a folder reads its entries: the permission (or the user's
        # approval) is for the folder itself, as for a file in it.
        self._check_access(resolved / "_", write=False)
        if not resolved.is_dir():
            raise MacroError(f"{func}: folder not found: {resolved}")
        wanted = pattern.lower() if pattern else None
        entries = []
        try:
            with os.scandir(resolved) as scan:
                for entry in scan:
                    if wanted and not fnmatch.fnmatchcase(entry.name.lower(), wanted):
                        continue
                    try:
                        st = entry.stat()
                    except OSError:
                        continue    # a broken link, or gone since the scan
                    if len(entries) == MAX_DIR_ENTRIES:
                        raise MacroError(
                            f"{func}: {resolved} has more than {MAX_DIR_ENTRIES} "
                            'entries — pass a pattern such as "*.gpx"'
                        )
                    is_dir = entry.is_dir()
                    entries.append({
                        "name": entry.name,
                        "path": str(resolved / entry.name),
                        "dir": is_dir,
                        "size": 0 if is_dir else st.st_size,
                        "modified": int(st.st_mtime),
                    })
        except OSError as exc:
            raise MacroError(f"cannot read {resolved}: {exc}") from None
        entries.sort(key=lambda e: (e["name"].lower(), e["name"]))
        return lua.table_from([lua.table_from(e) for e in entries])

    # Copying, moving and deleting files (GSAK's FileCopy / FileErase): only
    # through safe_copy() / safe_delete(), with write permission on the
    # target folder and only the file types of FILE_SUFFIXES.

    def _file_arg(self, func: str, path: Any, what: str) -> Path:
        """*path* of a file function, resolved against the macro's folder."""
        if not isinstance(path, str) or not path.strip():
            raise MacroError(f"{func} expects a {what} path")
        file = Path(path.strip()).expanduser()
        if not file.is_absolute():
            file = (self._base_dir or macros_dir()) / file
        return file

    def _writable(self, func: str, file: Path, suffixes: Optional[tuple[str, ...]],
                  kind: str = "write") -> list[FolderPermission]:
        """Check that the running macro may write (or delete) *file*,
        asking the user to approve the folder; the folder list to pass to
        safe_write() and friends. A file type or location that is never
        allowed is refused before the user is asked."""
        try:
            reason = write_denied_reason(resolve_path(file), suffixes)
        except (OSError, RuntimeError) as exc:
            raise MacroError(f"cannot resolve {file}: {exc}") from None
        if reason is not None:
            raise MacroError(f"{func}: macros may not {kind} {file} — {reason}")
        target = self._check_access(file, write=True)
        permissions = self._run_permissions
        if (target, True) in self._picked:
            permissions = with_grant(permissions, target.parent, True)
        return permissions

    def _copy_file(self, func: str, move: bool, source=None, target=None, options=None):
        src = self._file_arg(func, source, "source file")
        dest = self._file_arg(func, target, "target file or folder")
        opts = {} if options is None else _from_lua(options)
        if not isinstance(opts, dict) or set(opts) - {"if_exists"}:
            raise MacroError(f'{func}: options must be a table, e.g. {{ if_exists = "skip" }}')
        if_exists = opts.get("if_exists", "overwrite")
        if if_exists not in COPY_IF_EXISTS:
            raise MacroError(f"{func}: if_exists must be one of {', '.join(COPY_IF_EXISTS)}, "
                             f"got {if_exists!r}")
        src_resolved = self._check_access(src, write=False)
        if not src_resolved.is_file():
            raise MacroError(f"{func}: file not found: {src_resolved}")
        # Moving deletes the source, so it needs write permission as well —
        # checked before anything is copied.
        src_permissions = (self._writable(func, src, FILE_SUFFIXES, "move") if move else None)
        if target.strip().endswith(("/", "\\")) or dest.is_dir():
            dest = dest / src_resolved.name
        permissions = self._writable(func, dest, FILE_SUFFIXES)
        if if_exists == "skip" and dest.exists():
            return None
        try:
            final = safe_copy(src_resolved, dest, permissions, FILE_SUFFIXES, MAX_COPY_BYTES)
            self._written.add(final)
        except FolderAccessDenied as exc:
            raise MacroError(f"{func}: {exc}") from None
        except OSError as exc:
            raise MacroError(f"{func}: cannot copy {src_resolved} to {dest}: {exc}") from None
        if move:
            try:
                safe_delete(src, src_permissions, FILE_SUFFIXES)
            except (FolderAccessDenied, OSError) as exc:
                raise MacroError(f"{func}: copied to {final}, but cannot delete "
                                 f"{src_resolved}: {exc}") from None
        return str(final)

    def _delete_path(self, func: str, folder: bool, path=None) -> bool:
        file = self._file_arg(func, path, "folder" if folder else "file")
        kind = "delete the folder" if folder else "delete"
        permissions = self._writable(func, file, None if folder else FILE_SUFFIXES, kind)
        try:
            return safe_delete(file, permissions, FILE_SUFFIXES, folder=folder)
        except FolderAccessDenied as exc:
            raise MacroError(f"{func}: {exc}") from None
        except OSError as exc:
            if folder and file.is_dir() and any(file.iterdir()):
                raise MacroError(f"{func}: {file} is not empty") from None
            raise MacroError(f"{func}: cannot delete {file}: {exc}") from None

    def _create_folder(self, path=None) -> str:
        func = "opensak.create_folder"
        folder = self._file_arg(func, path, "folder")
        permissions = self._writable(func, folder, None, "create")
        try:
            target = check_access(folder, write=True, permissions=permissions)
            if target.exists() and not target.is_dir():
                raise MacroError(f"{func}: {target} exists and is not a folder")
            target.mkdir(parents=True, exist_ok=True)
        except FolderAccessDenied as exc:
            raise MacroError(f"{func}: {exc}") from None
        except OSError as exc:
            raise MacroError(f"{func}: cannot create {folder}: {exc}") from None
        return str(target)

    def _file_info(self, lua, path=None):
        func = "opensak.file_info"
        # Only looks at the entry, like file_exists(), so a database file
        # can be checked too; reading it is still refused.
        target = self._check_access(self._file_arg(func, path, "file or folder"),
                                    write=False, allow_database=True)
        try:
            st = target.stat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise MacroError(f"{func}: cannot read {target}: {exc}") from None
        is_dir = target.is_dir()
        return lua.table_from({
            "name": target.name,
            "path": str(target),
            "dir": is_dir,
            "size": 0 if is_dir else st.st_size,
            "modified": int(st.st_mtime),
        })

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
        return str(self._pick_file(chosen, save))

    def _pick_file(self, chosen: Path, save: bool) -> Path:
        """A file the user picked: usable for the rest of this run (read,
        or written with *save*) unless it is protected."""
        try:
            target = resolve_path(chosen)
        except (OSError, RuntimeError) as exc:
            raise MacroError(f"cannot resolve {chosen}: {exc}") from None
        reason = protected_reason(target)
        if reason is not None:
            kind = "write" if save else "read"
            raise MacroError(f"macros may not {kind} {target} — {reason}")
        self._picked.add((target, save))
        return target

    def _pick_folder(self, chosen: Path, func: str) -> Path:
        """A folder the user picked: read and write permission on it for
        the rest of this run, unless it is protected or a filesystem root."""
        try:
            target = resolve_path(chosen)
        except (OSError, RuntimeError) as exc:
            raise MacroError(f"{func}: cannot resolve {chosen}: {exc}") from None
        reason = protected_reason(target)
        if reason is not None:
            raise MacroError(f"{func}: macros may not use {target} — {reason}")
        # approvable_folder() of a file in it is None exactly for a root.
        if approvable_folder(target / "_") is None:
            raise MacroError(f"{func}: macros may not use the whole drive {target}")
        for write in (False, True):
            self._run_permissions = with_grant(self._run_permissions, target, write)
        return target

    def _safe_saver(self, target: Path) -> Callable[[Path, Callable, bool], Any]:
        """The Saver (see opensak.export.file_export) for the files a macro
        export writes: safe_write_stream() with this run's folder list, plus
        the file the user picked if *target* is one. Raises
        FolderAccessDenied."""
        permissions = self._run_permissions
        if (target, True) in self._picked:
            permissions = with_grant(permissions, target.parent, True)

        def save(path: Path, write: Callable, binary: bool) -> Any:
            written, result = safe_write_stream(path, write, permissions, binary=binary)
            self._written.add(written)
            return result

        return save

    def _check_access(self, path: Path, write: bool, allow_database: bool = False) -> Path:
        """check_access() against this run's list. A folder that is merely
        not listed is put to the user (host.approve_folder) instead of
        failing; protected data and roots fail without asking. A file the
        user picked in opensak.choose_file() needs no folder permission.
        *allow_database* only for the macro's own database (macro_db())."""
        try:
            return check_access(path, write=write, permissions=self._run_permissions,
                                allow_database=allow_database)
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
            return check_access(path, write=write, permissions=self._run_permissions,
                                allow_database=allow_database)
        except FolderAccessDenied as exc:
            raise MacroError(str(exc)) from None

    def _run_macro(self, path=None, *args):
        if not isinstance(path, str) or not path.strip():
            raise MacroError("opensak.run_macro expects a macro file")
        if self._macro_depth >= MAX_MACRO_DEPTH:
            raise MacroError(
                f"opensak.run_macro: macros may call each other at most "
                f"{MAX_MACRO_DEPTH} levels deep"
            )
        file = Path(path.strip()).expanduser()
        if not file.suffix:
            file = file.with_suffix(".lua")
        file = self._readable_file(str(file))
        try:
            if file.stat().st_size > MAX_MACRO_BYTES:
                raise MacroError(
                    f"{file.name} is larger than {MAX_MACRO_BYTES // (1024 * 1024)} MB"
                )
            source = file.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            raise MacroError(f"macro not found: {file}") from None
        except (OSError, UnicodeDecodeError) as exc:
            raise MacroError(f"cannot read {file}: {exc}") from None
        fn, error = self._load_macro(source, f"={file.name}")
        if fn is None:
            raise MacroError(str(error))
        # Relative paths in the called macro are its own folder's, and its
        # per-macro state is its own file's.
        previous = self._base_dir, self._macro_path
        caller_dbs = self._macro_db_path, dict(self._attachments)
        self._base_dir, self._macro_path = file.parent, file
        self._macro_depth += 1
        try:
            result = fn(*args)
        except BaseException:
            self._end_called_macro(previous, caller_dbs, failed=True)
            raise
        self._end_called_macro(previous, caller_dbs, failed=False)
        return result

    def _end_called_macro(self, previous, caller_dbs, failed: bool) -> None:
        self._macro_depth -= 1
        self._base_dir, self._macro_path = previous
        # The called macro may have switched `macro` to its own file, and
        # attached or detached databases.
        caller_db, caller_attachments = caller_dbs
        try:
            # Detach first what the caller does not have, which keeps the
            # count within SQLite's limit while the caller's come back.
            self._restore_attachments({
                s: a for s, a in caller_attachments.items() if self._attachments.get(s) == a
            })
            self._attach_macro_db(caller_db)
            self._restore_attachments(caller_attachments)
        except SqlError as exc:
            if not failed:      # else the called macro's error is the one to show
                raise MacroError(f"opensak.run_macro: {exc}") from None

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
    def _coords_parse(text=None, fmt=None):
        func = "opensak.coords.parse"
        if not isinstance(text, str):
            raise MacroError(f"{func} expects a string, got {text!r}")
        if fmt is not None and not isinstance(fmt, str):
            raise MacroError(f"{func}: format must be a string, got {fmt!r}")
        try:
            return parse_any(text, fmt)
        except ValueError:
            raise MacroError(
                f"{func}: unknown format {fmt!r}; valid: {', '.join(INPUT_KEYS)}"
            ) from None

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
                save=self._safe_saver(target),
            )
        except FolderAccessDenied as exc:
            raise MacroError(str(exc)) from None
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
                save=self._safe_saver(output),
            )
        except FolderAccessDenied as exc:
            raise MacroError(str(exc)) from None
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

    # -- User interface (#982) -------------------------------------------------

    def _ui(self, name: str, func: str) -> Callable:
        """The host's UI method *name*; a clear error when the host has no
        user interface (e.g. a headless run)."""
        method = getattr(self._host, name, None)
        if method is None:
            raise MacroError(f"{func}: not available — this run has no user interface")
        return method

    def _message(self, text=None, title=None) -> None:
        func = "opensak.message"
        text = _ui_text(func, "text", text)
        self._ui("message", func)(text, _ui_text(func, "title", title, optional=True))

    def _input(self, prompt=None, default=None, title=None) -> Optional[str]:
        func = "opensak.input"
        prompt = _ui_text(func, "prompt", prompt)
        default = _ui_text(func, "default", default, optional=True)
        title = _ui_text(func, "title", title, optional=True)
        answer = self._ui("input", func)(prompt, default, title)
        return None if answer is None else str(answer)

    def _choose(self, prompt=None, options=None, default=None, title=None):
        func = "opensak.choose"
        prompt = _ui_text(func, "prompt", prompt)
        items = macro_forms.parse_options(_from_lua(options), func) \
            if options is not None else []
        if not items:
            raise MacroError(f'{func} expects options, e.g. {func}("Format", {{"GPX", "GGZ"}})')
        index = -1
        if default is not None:
            index = macro_forms.find_option(items, default)
            if index < 0:
                raise MacroError(f"{func}: the default {helpers.lua_tostring(default)} "
                                 "is not one of the options")
        shown = [macro_forms.format_number(i) if isinstance(i, (int, float)) else i
                 for i in items]
        title = _ui_text(func, "title", title, optional=True)
        picked = self._ui("choose", func)(prompt, shown, index, title)
        if picked is None or not 0 <= picked < len(items):
            return None
        return items[picked]

    def _choose_folder(self, title=None) -> Optional[str]:
        func = "opensak.choose_folder"
        if title is not None and (not isinstance(title, str) or len(title) > MAX_CHOOSE_FILE_TEXT):
            raise MacroError(
                f"{func}: title must be a string of at most {MAX_CHOOSE_FILE_TEXT} characters"
            )
        chosen = self._ui("choose_folder", func)(title or "", self._base_dir or macros_dir())
        if not chosen:
            return None
        return str(self._pick_folder(chosen, func))

    def _form(self, lua, spec=None):
        func = "opensak.form"
        show = self._ui("show_form", func)
        if not _is_lua_table(spec):
            raise MacroError(
                f'{func} expects a table, e.g. {func}{{ title = "Export", fields = {{ … }} }}'
            )
        from opensak.lang import tr
        form = macro_forms.parse_form(
            _from_lua(spec), func, is_function=_is_lua_function,
            ok_label=tr("macro_form_ok"), cancel_label=tr("cancel"),
        )
        self._load_form_images(form, func)
        memory = self._form_memory_file() if form.remember else None
        key = macro_forms.remember_key(form)
        if memory is not None:
            stored = self._load_form_memory(memory).get(key)
            if isinstance(stored, dict):
                macro_forms.apply_remembered(form, stored, func)

        session = _FormSession(self, lua, form)
        result = show(form, session.dispatch)
        session.raise_error()
        if result is None:
            return None
        values, closer = result
        if memory is not None:
            keep = macro_forms.remembered_ids(form)
            data = self._load_form_memory(memory)
            data[key] = {k: v for k, v in values.items() if k in keep and v is not None}
            macro_settings.save_settings(memory, data, func)
        return _to_lua(lua, values), closer

    def _load_form_images(self, form: macro_forms.Form, func: str) -> None:
        """Read the pictures of image fields (permission-checked) into
        props["image"], so the host gets bytes, never a path."""
        for f in form.walk():
            if f.type != "image":
                continue
            if f.get("path"):
                path = self._readable_file(f.get("path"))
                try:
                    if path.stat().st_size > MAX_IMAGE_BYTES:
                        raise MacroError(f"{func}: {f.where}: {path.name} is larger than "
                                         f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB")
                    f.props["image"] = path.read_bytes()
                except OSError as exc:
                    raise MacroError(f"{func}: {f.where}: cannot read {path}: {exc}") from None
            elif f.get("data"):
                try:
                    data = base64.b64decode(f.get("data"))
                except (binascii.Error, ValueError):
                    raise MacroError(f"{func}: {f.where}: data is not valid base64") from None
                if len(data) > MAX_IMAGE_BYTES:
                    raise MacroError(f"{func}: {f.where}: the picture is too large")
                f.props["image"] = data

    def _form_memory_file(self) -> Optional[Path]:
        """Where remember = true keeps this macro's form values; None for
        an unsaved macro (it then simply remembers nothing)."""
        if self._macro_path is None:
            return None
        if self._settings_dir is not None:
            folder, macros = self._settings_dir, None
        else:
            folder, macros = macro_settings.default_settings_dir(), macros_dir()
        return macro_settings.settings_file(folder / FORM_MEMORY_DIRNAME, self._macro_path, macros)

    @staticmethod
    def _load_form_memory(path: Path) -> dict:
        try:
            data = macro_settings.load_settings(path, "opensak.form")
        except MacroError:
            return {}  # damaged: start from the defaults; replaced on OK
        return data if isinstance(data, dict) else {}

    def _browse(self, controller: macro_forms.FormController, field: macro_forms.Field) -> None:
        """The Browse button of a file or folder field: OpenSAK's dialog,
        and the same access as opensak.choose_file() / choose_folder()."""
        current = str(controller.value(field) or "").strip()
        start = self._base_dir or macros_dir()
        if current:
            folder = Path(current).expanduser()
            folder = folder if field.type == "folder" else folder.parent
            if folder.is_dir():
                start = folder
        if field.type == "folder":
            chosen = self._ui("choose_folder", "opensak.form")(field.label, start)
            if chosen:
                controller.set_value(field, str(self._pick_folder(chosen, "opensak.form")))
            return
        save = field.get("mode") == "save"
        chosen = self._host.choose_file(field.label, field.get("filter", ""), save, start)
        if chosen:
            controller.set_value(field, str(self._pick_file(chosen, save)))

    def _progress(self, lua, title=None, total=None):
        func = "opensak.progress"
        title = _ui_text(func, "title", title)
        count = 0
        if total is not None:
            number = _number(total)
            if number is None or number < 0 or number != int(number):
                raise MacroError(f"{func}: total must be a whole number >= 0, got {total!r}")
            count = int(number)
        window = self._ui("progress", func)(title, count)
        self._progress_windows.append(window)
        closed = False

        def update(this=None, value=None, text=None) -> bool:
            if not _is_lua_table(this):
                raise MacroError("p:update(i, text) must be called with a colon")
            if closed:
                raise MacroError("p:update: the progress window is closed")
            step = None
            if value is not None:
                number = _number(value)
                if number is None:
                    raise MacroError(f"p:update: expects a number, got {value!r}")
                step = int(number)
            return bool(window.update(step, _ui_text("p:update", "text", text, optional=True)))

        def close(this=None) -> None:
            nonlocal closed
            if not closed:
                closed = True
                window.close()
                if window in self._progress_windows:
                    self._progress_windows.remove(window)

        return lua.table_from({"update": self._api_function(update),
                               "close": self._api_function(close)})

    def _close_progress_windows(self) -> None:
        windows, self._progress_windows = self._progress_windows, []
        for window in windows:
            window.close()

    def _select(self, code=None) -> bool:
        func = "opensak.select"
        return bool(self._ui("select_cache", func)(_gc_code(code, func)))

    def _refresh(self) -> None:
        self._ui("refresh", "opensak.refresh")()

    # -- Saved settings (#983) -------------------------------------------------

    def _settings_file(self, func: str) -> Path:
        if self._macro_path is None:
            raise MacroError(f"{func}: save the macro first — settings are kept per macro file")
        if self._settings_dir is not None:
            folder, macros = self._settings_dir, None
        else:
            folder, macros = macro_settings.default_settings_dir(), macros_dir()
        return macro_settings.settings_file(folder, self._macro_path, macros)

    def _settings_load(self, lua):
        func = "opensak.settings.load"
        data = macro_settings.load_settings(self._settings_file(func), func)
        return lua.table_from(data, recursive=True)

    def _settings_save(self, describe, value=None) -> None:
        func = "opensak.settings.save"
        from lupa.lua54 import lua_type
        if lua_type(value) != "table":
            raise MacroError(f"{func} expects a table, got {helpers.lua_tostring(value)}")
        data = macro_settings.to_json(value, describe, func)
        macro_settings.save_settings(self._settings_file(func), data, func)

    def _settings_clear(self) -> None:
        func = "opensak.settings.clear"
        macro_settings.clear_settings(self._settings_file(func), func)

    def _load_poi_settings(self, name: str) -> PoiExportSettings:
        for path in PoiExportProfile.list_profiles(self._poi_settings_dir):
            try:
                profile = PoiExportProfile.load(path)
            except Exception:
                continue
            if profile.name == name:
                return profile.settings
        raise MacroError(f"no saved POI export setting named {name!r}")

    def _export_poi(self, lua, name=None, folder=None):
        if not isinstance(name, str) or not name.strip():
            raise MacroError("opensak.export_poi expects the name of a saved POI export setting")
        if folder is not None and (not isinstance(folder, str) or not folder.strip()):
            raise MacroError("opensak.export_poi: folder must be a non-empty string")
        settings = self._load_poi_settings(name)
        if folder is None and not settings.folder.strip():
            raise MacroError(
                f"POI export setting {name!r} has no folder — choose one in the "
                "POI export dialog and save the setting again, or pass a folder"
            )
        caches = select_for_export(self._host.filtered_caches())
        if not caches:
            return None
        folder = Path((folder or settings.folder).strip()).expanduser()
        if not folder.is_absolute():
            folder = (self._base_dir or macros_dir()) / folder
        # Every file goes into this folder, so one check covers them all.
        folder = self._check_access(folder / "poi.gpi", write=True).parent
        icon = None
        if settings.icon.strip():
            icon = self._check_access(Path(settings.icon.strip()).expanduser(), write=False)

        def should_write(target: Path) -> bool:
            if settings.if_exists == "skip":
                return False
            if settings.if_exists == "ask":
                from opensak.lang import tr

                return self._host.confirm(tr("file_export_overwrite_msg", path=str(target)))
            return True

        try:
            written = write_poi_export(
                caches, settings, folder,
                database=self._host.database_name(),
                filter_name=self._host.filter_name(),
                center_name=self._host.center_name(),
                should_write=should_write,
                icon_path=icon,
                save=self._safe_saver(folder / "poi.gpi"),
            )
        except FolderAccessDenied as exc:
            raise MacroError(str(exc)) from None
        except OSError as exc:
            raise MacroError(f"cannot write POI file: {exc}") from None
        except ValueError as exc:   # unreadable icon
            raise MacroError(str(exc)) from None
        if not written:
            return None
        return lua.table_from([str(path) for path, _ in written]), sum(n for _, n in written)

    # -- Running ---------------------------------------------------------------

    def run(
        self, source: str, chunk_name: str = "macro", base_dir: Optional[Path] = None,
        macro_path: Optional[Path] = None,
    ) -> int:
        """Execute *source* and return its exit code: the one passed to
        opensak.exit(), 0 otherwise. Raises MacroError on any failure, and
        MacroBusy (without running anything) while another macro runs.

        *base_dir* (usually the macro file's folder) is where relative paths
        given to opensak.read_csv() are looked up; the macros folder
        otherwise. *macro_path* is the macro's file, which opensak.settings
        keys its saved settings by; without it the macro has none.
        """
        if not _RUN_LOCK.acquire(blocking=False):
            raise MacroBusy("another macro is still running")
        try:
            with _c_locale():
                return self._run(source, chunk_name, base_dir, macro_path)
        finally:
            _RUN_LOCK.release()

    def _run(
        self, source: str, chunk_name: str, base_dir: Optional[Path],
        macro_path: Optional[Path] = None,
    ) -> int:
        self._base_dir = base_dir
        self._macro_path = macro_path
        self._chunk_name = chunk_name
        self._macro_db_path = None
        self._attachments = {}
        self._run_permissions = (
            list(self._folder_permissions)
            if self._folder_permissions is not None
            else load_permissions()
        )
        self._denied = set()
        self._write_denied = set()
        self._deleted = {}
        self._tx_deleted = []
        self._picked = set()
        self._written = set()
        self._opened = 0
        self._cancelled.clear()
        self._exit_code = None
        self._slept = 0.0
        self._polygons = {}
        self._macro_depth = 0
        try:
            from lupa.lua54 import LuaError, LuaMemoryError, LuaRuntime, lua_type
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
        self._stop, self._where, make_loader = lua.execute(
            _SANDBOX_SETUP, self._instruction_limit
        )

        g = lua.globals()
        g.print = self._wrap(self._lua_print)
        # "coords.parse" → opensak.coords.parse
        api: dict[str, Any] = {}
        for func in API:
            *namespaces, name = func.name.split(".")
            table = api
            if (func.write or func.beta) and not flags.lua_macro_write:
                continue
            for ns in namespaces:
                table = table.setdefault(ns, {})
            bound = func.bind(self, lua)
            # A Lua function (opensak.transaction) must not be called through
            # Python: errors passing through lose their value.
            table[name] = bound if lua_type(bound) == "function" else self._api_function(bound)
        g.opensak = lua.table_from(api, recursive=True)
        self._load_macro = make_loader()

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
            if self._map_data:
                self._map_data.close()
            self._map_data = None
            # Closing the connection detaches every database.
            self._close_sql_write()
            self._attachments = {}
            self._report_deleted()
            self._close_other_databases()
            self._load_macro = None
            self._close_progress_windows()
            self._host.end_macro()

    def _api_function(self, func: Callable) -> Any:
        """*func* as a function for Lua: MacroError becomes a Lua error
        reported at the line of the macro that called it."""
        return self._wrap(func)

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


class _FormSession:
    """One opensak.form{} call: turns the dialog's events into calls of the
    macro's callbacks, which get a handle (a Lua table of methods) to read
    and change the open form.

    The callbacks run inside Qt's event loop, where an exception must not
    escape. The first error therefore closes the form and is raised again
    by raise_error() once the dialog has returned.
    """

    def __init__(self, runtime: MacroRuntime, lua, form: macro_forms.Form):
        self._rt = runtime
        self._lua = lua
        self._form = form
        self._by_id = form.by_id()
        self._error: Optional[BaseException] = None
        self._handle = None
        self._controller: Optional[macro_forms.FormController] = None

    def raise_error(self) -> None:
        exc = self._error
        if exc is None:
            return
        if isinstance(exc, MacroError):
            raise exc
        from lupa.lua54 import LuaError
        if isinstance(exc, LuaError):
            raise MacroError(str(exc)) from exc
        raise MacroError(f"{type(exc).__name__}: {exc}") from exc

    def dispatch(
        self, controller: macro_forms.FormController, event: str,
        field: Optional[macro_forms.Field],
    ) -> Optional[str]:
        if self._error is not None:
            return None
        try:
            return self._dispatch(controller, event, field)
        except Exception as exc:  # LuaError, MacroError, … — see the class docstring
            self._error = exc
            controller.close_form(None)
            return None

    def _dispatch(self, controller, event: str, field) -> Optional[str]:
        form = self._form
        if event == "browse":
            self._rt._browse(controller, field)
            return None
        handle = self._handle_for(controller)
        if event == "open":
            if form.on_open is not None:
                form.on_open(handle)
        elif event == "change":
            callback = field.get("on_change")
            if callback is not None:
                callback(handle, _to_lua(self._lua, controller.value(field)), field.id)
        elif event == "click":
            callback = field.get("on_click")
            if callback is not None:
                callback(handle, field.id)
        elif event == "validate" and form.validate is not None:
            answer = form.validate(handle, field.id if field is not None else None)
            if answer is None or answer is True:
                return None
            if answer is False:
                from opensak.lang import tr
                return tr("macro_form_invalid")
            if isinstance(answer, (str, int, float)):
                return _ui_text("opensak.form: validate", "the message", answer)
            raise MacroError("opensak.form: validate must return nil or a message")
        return None

    def values(self, controller) -> dict[str, Any]:
        return {f.id: controller.value(f) for f in self._form.value_fields() if f.id}

    def _handle_for(self, controller):
        if self._handle is not None and self._controller is controller:
            return self._handle
        func = "opensak.form"

        def field(this: Any, fid: Any, method: str) -> macro_forms.Field:
            if not _is_lua_table(this):
                raise MacroError(f"{func}: call f:{method}(…) with a colon")
            found = self._by_id.get(fid) if isinstance(fid, str) else None
            if found is None:
                raise MacroError(f"{func}: f:{method}: no field with id {helpers.lua_tostring(fid)}")
            return found

        def value_field(this, fid, method) -> macro_forms.Field:
            found = field(this, fid, method)
            if not found.has_value:
                raise MacroError(f"{func}: f:{method}: {fid!r} is a {found.type} without a value")
            return found

        def flag(method: str, value: Any) -> bool:
            if not isinstance(value, bool):
                raise MacroError(f"{func}: f:{method} expects true or false, got "
                                 f"{helpers.lua_tostring(value)}")
            return value

        def get(this=None, fid=None):
            return _to_lua(self._lua, controller.value(value_field(this, fid, "get")))

        def set_(this=None, fid=None, value=None):
            f = value_field(this, fid, "set")
            controller.set_value(f, macro_forms.coerce_value(f, _from_lua(value), f"{func}: f:set"))

        def values(this=None):
            if not _is_lua_table(this):
                raise MacroError(f"{func}: call f:values() with a colon")
            return _to_lua(self._lua, self.values(controller))

        def set_options(this=None, fid=None, options=None):
            f = field(this, fid, "set_options")
            if f.type not in macro_forms.OPTION_TYPES:
                raise MacroError(f"{func}: f:set_options: {fid!r} is a {f.type} without options")
            f.options = macro_forms.parse_options(_from_lua(options), f"{func}: f:set_options")
            controller.set_options(f)

        def set_label(this=None, fid=None, text=None):
            f = field(this, fid, "set_label")
            f.label = _ui_text(f"{func}: f:set_label", "text", text)
            controller.set_label(f)

        def set_enabled(this=None, fid=None, on=None):
            controller.set_enabled(field(this, fid, "set_enabled"), flag("set_enabled", on))

        def set_visible(this=None, fid=None, on=None):
            controller.set_visible(field(this, fid, "set_visible"), flag("set_visible", on))

        def set_title(this=None, text=None):
            if not _is_lua_table(this):
                raise MacroError(f"{func}: call f:set_title(…) with a colon")
            controller.set_title(_ui_text(f"{func}: f:set_title", "text", text))

        def focus(this=None, fid=None):
            controller.focus(field(this, fid, "focus"))

        def close(this=None, closer=None):
            if not _is_lua_table(this):
                raise MacroError(f"{func}: call f:close(…) with a colon")
            if closer is not None and not isinstance(closer, str):
                raise MacroError(f"{func}: f:close expects an id string, got "
                                 f"{helpers.lua_tostring(closer)}")
            controller.close_form(closer or "ok")

        def cancel(this=None):
            if not _is_lua_table(this):
                raise MacroError(f"{func}: call f:cancel() with a colon")
            controller.close_form(None)

        methods = {
            "get": get, "set": set_, "values": values, "set_options": set_options,
            "set_label": set_label, "set_enabled": set_enabled, "set_visible": set_visible,
            "set_title": set_title, "focus": focus, "close": close, "cancel": cancel,
        }
        self._handle = self._lua.table_from(
            {name: self._rt._api_function(fn) for name, fn in methods.items()}
        )
        self._controller = controller
        return self._handle


# ── Lua API registry ─────────────────────────────────────────────────────────
#
# Single source of truth for the `opensak` table. To add a function: add an
# ApiFunction here (bump API_VERSION and use it as `since` if the release
# already shipped the current version; for a changed function add the new
# version to `changed` instead). Don't commit the regenerated docs and
# Lua Language Server stub — the Macro API docs workflow does that after the
# merge to beta. Regenerate them locally for editor completion with
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
    # (API version, what changed) for changes after `since`
    changed: tuple[tuple[int, str], ...] = ()
    # Changes the active database: only registered with the lua-macro-write
    # feature flag (beta builds), see utils/flags.py.
    write: bool = False
    # Only registered with that flag too, but changes nothing (opensak.attach)
    beta: bool = False

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


# (list, insert, update, delete) examples of the functions _child_api() makes
_CHILD_EXAMPLES: dict[str, tuple[str, str, str, str]] = {
    "log": (
        'for _, l in ipairs(opensak.logs("GC12345") or {}) do\n'
        '    print(l.date, l.type, l.finder)\nend',
        'local id = opensak.insert_log("GC12345", { type = "Write note", date = "2026-10-11",\n'
        '                                          finder = "me", text = "Final solved" })',
        'for _, l in ipairs(opensak.logs("GC12345")) do\n'
        '    if l.type == "Write note" then opensak.update_log(l.id, { text = "checked" }) end\nend',
        'for _, l in ipairs(opensak.logs("GC12345")) do\n'
        '    if l.type == "Write note" and l.finder == "me" then opensak.delete_log(l.id) end\nend',
    ),
    "waypoint": (
        'for _, w in ipairs(opensak.waypoints("GC12345") or {}) do\n'
        '    print(w.prefix, w.type, w.lat, w.lon)\nend',
        'opensak.insert_waypoint("GC12345", { prefix = "FN", type = "Final Location",\n'
        '                                     name = "Final", lat = 47.36872, lon = 8.54093 })',
        'for _, w in ipairs(opensak.waypoints("GC12345")) do\n'
        '    if w.prefix == "PK" then opensak.update_waypoint(w.id, { comment = "free parking" }) end\nend',
        'for _, w in ipairs(opensak.waypoints("GC12345")) do\n'
        '    if w.prefix == "FN" then opensak.delete_waypoint(w.id) end\nend',
    ),
    "attribute": (
        'for _, a in ipairs(opensak.attributes("GC12345") or {}) do\n'
        '    print(a.attribute_id, a.name, a.on)\nend',
        'opensak.insert_attribute("GC12345", { attribute_id = 1, name = "Dogs", on = false })',
        'for _, a in ipairs(opensak.attributes("GC12345")) do\n'
        '    if a.attribute_id == 1 then opensak.update_attribute(a.id, { on = true }) end\nend',
        'for _, a in ipairs(opensak.attributes("GC12345")) do\n'
        '    if a.attribute_id == 1 then opensak.delete_attribute(a.id) end\nend',
    ),
    "trackable": (
        'for _, t in ipairs(opensak.trackables("GC12345") or {}) do print(t.ref, t.name) end',
        'opensak.insert_trackable("GC12345", { ref = "TB1234", name = "My travel bug" })',
        'for _, t in ipairs(opensak.trackables("GC12345")) do\n'
        '    if t.ref == "TB1234" then opensak.update_trackable(t.id, { name = "Renamed" }) end\nend',
        'for _, t in ipairs(opensak.trackables("GC12345")) do\n'
        '    if t.ref == "TB1234" then opensak.delete_trackable(t.id) end\nend',
    ),
}


def _child_api(t: ChildTable) -> tuple[ApiFunction, ...]:
    """opensak.<plural>(), insert_<singular>(), update_<singular>() and
    delete_<singular>()."""
    list_ex, insert_ex, update_ex, delete_ex = _CHILD_EXAMPLES[t.singular]
    required = ", ".join(f"`{n}`" for n in t.required)
    derived = {
        "logs": " OpenSAK updates the cache's log counts and dates.",
        "waypoints": " OpenSAK updates the cache's waypoint count.",
        "trackables": " OpenSAK updates the cache's trackable count.",
    }.get(t.kind or "", "")
    return (
        ApiFunction(
            name=t.plural,
            description=f"The {t.plural} of a cache, each with its `id` for "
                        f"opensak.update_{t.singular}() and the fields listed "
                        "under Logs, waypoints, attributes and trackables. "
                        "Missing data is nil.",
            example=list_ex,
            since=3,
            bind=lambda rt, lua: lambda *a: rt._children(lua, t, *a),
            params=(_CODE, _READ_OPTIONS),
            returns=(f"{t.class_name}[]?", "nil if the cache is not in the database."),
        ),
        ApiFunction(
            name=f"insert_{t.singular}",
            description=f"Add a {t.singular} to a cache in the active database"
                        + (f"; {required} required" if required else "")
                        + "." + derived
                        + " Needs the user's permission like opensak.update().",
            example=insert_ex,
            since=3,
            bind=lambda rt, lua: lambda *a: rt._insert_child(t, *a),
            params=(_CODE, Param("fields", f"{t.class_name}Insert", f"The new {t.singular}'s fields.")),
            returns=("integer?", f"The new {t.singular}'s id; nil if the cache is not in the database."),
            write=True,
        ),
        ApiFunction(
            name=f"update_{t.singular}",
            description=f"Change fields of a {t.singular} in the active "
                        "database; only the given fields change, and `false` "
                        "clears a field that is not required or a boolean. Needs "
                        "the user's permission like opensak.update().",
            example=update_ex,
            since=3,
            bind=lambda rt, lua: lambda *a: rt._update_child(t, *a),
            params=(
                Param("id", "integer", f"The {t.singular}'s id from opensak.{t.plural}()."),
                Param("fields", f"{t.class_name}Update", "The fields to change."),
            ),
            returns=("boolean", f"false if there is no {t.singular} with this id."),
            write=True,
        ),
        ApiFunction(
            name=f"delete_{t.singular}",
            description=f"Delete a {t.singular} from the active database."
                        + derived
                        + " Needs the user's permission like opensak.update().",
            example=delete_ex,
            since=3,
            bind=lambda rt, lua: lambda *a: rt._delete_child(t, *a),
            params=(Param("id", "integer", f"The {t.singular}'s id from opensak.{t.plural}()."),),
            returns=("boolean", f"false if there is no {t.singular} with this id."),
            write=True,
        ),
    )


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
        changed=((3, "added the keys `last_user_update` and `macro_flag`; `owned` "
                     "uses the `owned` cache field"),),
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
                    + ", ".join(f"`{k}`" for k in SORT_KEYS) + ". "
                    "`macro_sort` has no column in the cache list, so no "
                    "column header shows the sort; it compares as text, so "
                    'pad numbers ("0042").',
        example='opensak.sort("difficulty", "desc")',
        since=2,
        changed=((3, "added `macro_sort`"),),
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
        name="save_filter",
        description="The active filter (with its name, and any quick filter "
                    "or search box) as a string, to put back later with "
                    "opensak.restore_filter() — e.g. to leave the user's "
                    "filter as it was after the macro filtered for its own "
                    "work. Like GSAK's SaveFilter(). The sort order is not "
                    "included. The string can be stored with "
                    "opensak.settings.save(); don't build or change it.",
        example='local saved = opensak.save_filter()\n'
                'opensak.filter{ found = false }\n'
                '-- ... work on the unfound caches ...\n'
                'opensak.restore_filter(saved)',
        since=3,
        bind=lambda rt, lua: rt._save_filter,
        returns=("string", "The saved filter; when no filter is active, "
                           "restoring it clears the filter."),
    ),
    ApiFunction(
        name="restore_filter",
        description="Apply a filter saved with opensak.save_filter(), like "
                    "GSAK's RestoreFilter(). As with opensak.filter{}, the "
                    "view is left unchanged when nothing matches. A filter "
                    "saved while none was active clears the filter.",
        example='local saved = opensak.save_filter()\n'
                'opensak.filter{ found = false }\n'
                'opensak.restore_filter(saved)',
        since=3,
        bind=lambda rt, lua: rt._restore_filter,
        params=(Param("saved", "string", "A string from opensak.save_filter()."),),
        returns=("integer", "Number of matching caches (0 = view unchanged, "
                            "unless the saved filter was empty)."),
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
        changed=((3, "added the fields `owned`, `macro_flag`, `macro_sort`, `symbol`, "
                     "`corrected_source` and `last_user_update`"),),
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
                    "unchanged. `all = true`: every cache of the database, "
                    "sorted by name, whatever the active filter shows. "
                    "`database` reads another database, without "
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
        changed=((3, "added `all` and the fields `owned`, `macro_flag`, `macro_sort`, "
                     "`symbol`, `corrected_source` and `last_user_update`"),),
        bind=lambda rt, lua: lambda *a: rt._caches(lua, *a),
        params=(Param("spec", "opensak.CachesSpec",
                      "Filter keys, `all`, `database` and/or `fields`; nothing = the "
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
                    "are allowed, except on TEMP tables: a macro "
                    "may create, fill, change and drop its own TEMP tables, "
                    "views and indexes (`CREATE TEMP TABLE ...`) and use them "
                    "in any query, opensak.sql_write() included. They are "
                    "never stored in the database and are gone when the macro "
                    "ends or calls opensak.switch_database(); changes to them "
                    "inside opensak.transaction() are undone with it. A TEMP "
                    "table may not take the name of a table of the database. "
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
        changed=((3, "TEMP tables, views and indexes"),),
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
                    "opensak.sql(). The macro's TEMP tables and views are "
                    "included.",
        example='print(table.concat(opensak.tables(), ", "))',
        since=2,
        bind=lambda rt, lua: lambda *a: rt._tables(lua, *a),
        params=(_READ_OPTIONS,),
        returns=("string[]", "Table and view names, sorted."),
        changed=((3, "lists the macro's TEMP tables and views, the tables "
                     "of opensak.macro_db() as `macro.<name>` and those of "
                     "opensak.attach() as `<schema>.<name>`"),),
    ),
    ApiFunction(
        name="columns",
        description="The columns of a table or view of the active database "
                    "(or the one named by the `database` option).",
        example='for _, c in ipairs(opensak.columns("caches")) do print(c.name, c.type) end',
        since=2,
        bind=lambda rt, lua: lambda *a: rt._columns(lua, *a),
        params=(Param("table", "string", "Table or view name."), _READ_OPTIONS),
        returns=("{name: string, type: string, required: boolean}[]",
                 "Column names and SQL types, in table order; `required`: NOT "
                 "NULL without a default, so an INSERT must give it."),
        changed=((3, "added `required`"),),
    ),
    ApiFunction(
        name="macro_db",
        description="Attach the macro's own SQLite database — a file named "
                    "after the macro file with the extension .db3, in the "
                    "macro's folder, created if missing — to the SQL "
                    "functions as schema `macro`. In it the macro has full "
                    "access through opensak.sql(), sql_each() and sql_write(): "
                    "CREATE, DROP, ALTER, INSERT, UPDATE and DELETE on "
                    "`macro.<name>` tables, indexes, views and triggers, and "
                    "`PRAGMA macro.user_version`. Its data stays between runs. "
                    "One statement can read and write the active database and "
                    "the macro database together, e.g. `INSERT INTO macro.seen "
                    "SELECT gc_code FROM caches`; the rules for the active "
                    "database stay the same. Unqualified names mean the active "
                    "database first, so create with `macro.` in front. "
                    "opensak.tables() lists its tables as `macro.<name>`. "
                    "The macro's folder must be writable for macros, like any "
                    "file a macro writes (Settings → Folder permissions; the "
                    "macros folder is by default); otherwise OpenSAK asks the "
                    "user to allow it, and a denial makes the call fail. The "
                    "first call creates the file, even if nothing is written "
                    "to it, and prints its path in the macro output. OpenSAK "
                    "remembers the file, and its backups include it in "
                    "`macro_databases/`, wherever it is. If the "
                    "folder cannot be written the call fails; a read-only "
                    "file can be read but not changed. Unlike opensak.update() "
                    "it needs no approval to change databases (see Changing "
                    "caches). Changes inside opensak.transaction() "
                    "are kept or undone together with those of the active "
                    "database. The SQL statement ATTACH itself stays refused. "
                    "Cannot be called inside opensak.transaction(); stays "
                    "attached after opensak.switch_database(). The macro must "
                    "be saved, as the file is named after it. In a macro "
                    "started with opensak.run_macro() it attaches the called "
                    "macro's own file instead; the caller's file is attached "
                    "again when the called macro returns.",
        example='opensak.macro_db()\n'
                'opensak.sql("CREATE TABLE IF NOT EXISTS macro.seen (code TEXT PRIMARY KEY, at TEXT)")\n'
                'opensak.sql("INSERT OR IGNORE INTO macro.seen SELECT gc_code, date(\'now\') FROM caches WHERE found = 1")\n'
                'for _, r in ipairs(opensak.sql("SELECT c.gc_code, s.at FROM caches c "\n'
                '        .. "JOIN macro.seen s ON s.code = c.gc_code LIMIT 5")) do\n'
                '    print(r.gc_code, r.at)\nend',
        since=3,
        bind=lambda rt, lua: rt._macro_db,
        returns=("string", "The path of the macro database file."),
    ),
    ApiFunction(
        name="attach",
        description="Attach another OpenSAK database read-only to the SQL "
                    "functions, under a schema name, so one opensak.sql() or "
                    "sql_write() statement can read it together with the "
                    "active database and opensak.macro_db(): "
                    "`SELECT … FROM caches c JOIN found.caches f ON "
                    "f.gc_code = c.gc_code`. Like GSAK's SQL `ATTACH`, which "
                    "stays refused. Nothing can write to an attached "
                    "database: the file is opened read-only, and SQL that "
                    "would change it is refused. Without `schema`, the name "
                    "is made from the database name: lower case, `_` for "
                    "anything but letters and digits (\"Found 2020\" → "
                    "`found_2020`). The schema name must be letters, digits "
                    "and `_`; `main`, `temp`, `macro`, `companion` and names "
                    "starting with `opensak_` are reserved. The active "
                    "database cannot be attached, and neither can a database "
                    "twice. Attaching the same database under the same name "
                    "again does nothing. SQLite allows 10 attached databases "
                    "at once, opensak.macro_db() included. The database is "
                    "not updated to the current schema, so one last opened "
                    "by an older OpenSAK version may lack columns; "
                    "`opensak.columns(\"<schema>.caches\")` shows what is "
                    "there. Each attach prints a line in the macro output. "
                    "Cannot be called inside opensak.transaction(). Stays "
                    "attached after opensak.switch_database(), unless the "
                    "database becomes the active one. Everything is "
                    "detached when the macro ends. A macro started with "
                    "opensak.run_macro() sees its caller's attached "
                    "databases; what it attaches or detaches is undone when "
                    "it returns. See Several databases in one query.",
        example='local found = opensak.attach("Found 2020", "found")\n'
                'for _, r in ipairs(opensak.sql("SELECT c.gc_code, c.name FROM caches c "\n'
                '        .. "JOIN found.caches f ON f.gc_code = c.gc_code")) do\n'
                '    print(r.gc_code, r.name)\nend',
        since=3,
        bind=lambda rt, lua: rt._attach,
        params=(
            Param("name", "string", "Database name, as in opensak.databases()."),
            Param("schema", "string", "Schema name to use in SQL; default: "
                  "made from the database name.", optional=True),
        ),
        returns=("string", "The schema name."),
        beta=True,
    ),
    ApiFunction(
        name="detach",
        description="Detach a database attached with opensak.attach(). "
                    "Optional: everything is detached when the macro ends. "
                    "Cannot be called inside opensak.transaction().",
        example='opensak.detach("found")',
        since=3,
        bind=lambda rt, lua: rt._detach,
        params=(Param("schema", "string", "The schema name opensak.attach() returned."),),
        beta=True,
    ),
    ApiFunction(
        name="attached",
        description="The databases attached with opensak.attach(), in the "
                    "order they were attached. `opensak` is always true: "
                    "only OpenSAK databases can be attached so far.",
        example='for _, a in ipairs(opensak.attached()) do print(a.schema, a.name) end',
        since=3,
        bind=lambda rt, lua: lambda: rt._attached(lua),
        returns=("{schema: string, name: string, path: string, opensak: boolean}[]",
                 "One table per attached database."),
        beta=True,
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
                    "already exists. The macro needs permission to change "
                    "databases.",
        example='local name = opensak.create_database("CH_Zurich")',
        since=2,
        bind=lambda rt, lua: rt._create_database,
        params=(Param("name", "string", "Name of the new database."),),
        returns=("string", "The name actually used (surrounding spaces removed)."),
    ),
    ApiFunction(
        name="center",
        description="The active centre point, which distances and bearings "
                    "are measured from. `name` is the saved centre point's "
                    "name, the label of a centre set from coordinates or a "
                    "cache, or \"\" when none was chosen; `saved` tells "
                    "whether it is one of opensak.centers().",
        example='local c = opensak.center()\n'
                'print(c.name, opensak.coords.format(c.lat, c.lon))',
        since=3,
        bind=lambda rt, lua: lambda: rt._center(lua),
        returns=("{name: string, lat: number, lon: number, saved: boolean}",
                 "The active centre point."),
    ),
    ApiFunction(
        name="centers",
        description="The saved centre points (Settings → User locations, "
                    "with ★ Home from your geocaching.com home location "
                    "first), as in the centre drop-down. "
                    "`filter{ near = { point = name, km = … } }` filters by one.",
        example='for _, p in ipairs(opensak.centers()) do\n'
                '    print(p.active and "*" or " ", p.name)\nend',
        since=3,
        bind=lambda rt, lua: lambda: rt._centers(lua),
        returns=("{name: string, lat: number, lon: number, active: boolean}[]",
                 "One table per saved centre point."),
    ),
    ApiFunction(
        name="set_center",
        description="Change the active centre point, like the centre "
                    "drop-down in the toolbar (GSAK's Centre): a saved "
                    "centre point by name, or coordinates with an optional "
                    "name to show. Coordinates are not saved as a user "
                    "location. Distance and bearing of every cache in the "
                    "active database are recalculated and stored, so the "
                    "macro needs permission to change the database, and it "
                    "cannot be used inside opensak.transaction(). The "
                    "centre stays when the macro ends.",
        example='opensak.set_center("★ Home")\n'
                'local c = opensak.cache("GC12345")\n'
                'opensak.set_center(c.lat, c.lon, c.code)',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._set_center(lua, *a),
        params=(Param("name", "string", "A saved centre point (see opensak.centers())."),),
        overloads=(
            (_LAT, _LON, Param("name", "string", "Name to show (default: the coordinates).",
                               optional=True)),
            (_COORDS, Param("name", "string", "Name to show (default: the coordinates).",
                            optional=True)),
        ),
        returns=("{name: string, lat: number, lon: number, saved: boolean}",
                 "The new centre point, as opensak.center() returns it."),
        write=True,
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
                    "active database. The macro needs permission to change "
                    "databases.",
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
        name="import_gsak",
        description="Import GSAK databases without any question (like File → "
                    "Import GSAK database, with the answers given up front) "
                    "and return what was done. *source* is a GSAK backup "
                    ".zip, a GSAK folder (the install / data folder with "
                    "`gsak.db3` and `data/<name>/sqlite.db3`, its `data` "
                    "folder, or one database's folder), a single `.db3` "
                    "cache database (just that database), or `gsak.db3` "
                    "(only user locations and saved filters). A relative "
                    "path is resolved against the macro file's folder, and "
                    "environment variables such as `%APPDATA%` are expanded; the "
                    "folder needs read permission. Each GSAK database goes "
                    "into the OpenSAK database of the same name, created if "
                    "missing, unless `target` says otherwise. For an "
                    "OpenSAK database that exists, `if_exists` decides: "
                    "`\"skip\"` (default), `\"merge\"` (new caches added, "
                    "existing ones updated, locked caches kept) or "
                    "`\"overwrite\"` (emptied first). `filters` and "
                    "`locations` also import the saved filters and user "
                    "locations of `gsak.db3`; `true` skips the ones whose "
                    "name exists when `if_exists` is `\"skip\"` and "
                    "replaces them otherwise. Embedded note images are "
                    "replaced by placeholders without asking. A database "
                    "that fails is reported and the others are still "
                    "imported. The summary is printed (unless `quiet`) and "
                    "written to the log file.",
        example='local r = opensak.import_gsak("C:/Backups/GSAKBackup.zip", {\n'
                '  databases = {"AllCH", "Holidays"}, if_exists = "merge",\n'
                '  locations = true })\n'
                'for _, db in ipairs(r.databases) do\n'
                '  print(db.gsak, db.target, db.action, db.created or 0, db.updated or 0)\n'
                'end\n'
                'if not r.ok then opensak.exit(1) end',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._import_gsak(lua, *a),
        params=(
            Param("source", "string", "GSAK backup .zip, GSAK folder or .db3 file."),
            Param("options", "opensak.GsakImportOptions",
                  "databases, exclude, target, if_exists, create, filters, "
                  "locations, quiet.", optional=True),
        ),
        returns=("opensak.GsakImportReport", "What was imported, per database."),
        write=True,
    ),
    ApiFunction(
        name="import_gpx",
        description="Import GPX files and Pocket Query zips without any "
                    "question (like File → Import GPX / PQ zip, then Import) "
                    "and return what was done. *source* is the name of an "
                    "import setting saved in that dialog — its files, "
                    "folders and target database are used — or else the "
                    "path of a `.gpx` / `.zip` file or of a folder. A "
                    "folder imports every GPX / zip file directly in it "
                    "(not in subfolders), looked up at each run, so the "
                    "folder Pocket Queries are downloaded to can be "
                    "imported every night. A GPX file's waypoints file "
                    "next to it is imported with it. A relative path is "
                    "resolved against the macro file's folder, and "
                    "environment variables such as `%USERPROFILE%` are "
                    "expanded; the files need read permission. The caches "
                    "go into `database` if given, else the setting's "
                    "database, else the active one. Files and folders of a "
                    "setting that no longer exist are left out and listed "
                    "in `missing`; a file that fails is reported and the "
                    "others are still imported. Cancel stops after the "
                    "current file. Unlike the dialog, country and state "
                    "are not looked up afterwards (reverse geocoding). The "
                    "summary is printed (unless `quiet`) and written to "
                    "the log file.",
        example='local r = opensak.import_gpx("Nightly PQs")\n'
                '-- or a folder: opensak.import_gpx("%USERPROFILE%/Downloads/PQ", { database = "PQ" })\n'
                'print(r.created .. " new, " .. r.updated .. " updated")\n'
                'if not r.ok then opensak.exit(1) end',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._import_gpx(lua, *a),
        params=(
            Param("source", "string",
                  "Name of a saved import setting, or a .gpx / .zip file or a folder."),
            Param("options", "opensak.GpxImportOptions", "database, quiet.", optional=True),
        ),
        returns=("opensak.GpxImportReport", "What was imported, per file."),
        write=True,
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
        name="update",
        description="Change fields of a cache in the active database. The "
                    "keys are cache field names (see Changing caches); only "
                    "the given fields change. `false` clears a field that is "
                    "not a boolean. Fields OpenSAK maintains itself, such as "
                    "`code`, `distance` or `log_count`, cannot be written. "
                    "Locked caches can be changed too: the lock only protects "
                    "from imports. "
                    "Before a macro's first change, OpenSAK asks the user to "
                    "allow that macro (see Changing caches).",
        example='opensak.update("GC12345", { user_flag = true, user_data = { [2] = "solved" } })\n'
                'for c in opensak.caches{ found = true, fields = {"color"} } do\n'
                '    if not c.color then opensak.update(c.code, { color = "#00AA00" }) end\nend',
        since=3,
        bind=lambda rt, lua: rt._update,
        params=(_CODE, Param("fields", "opensak.CacheUpdate", "The fields to change.")),
        returns=("boolean", "false if the cache is not in the database."),
        write=True,
    ),
    ApiFunction(
        name="macro_flag",
        description="Set, clear or swap the macro flag (field `macro_flag`) of "
                    "many caches at once, like GSAK's MACROFLAG: the caches of "
                    "the active filter, the given GC code(s), or `{ all = true }` "
                    "for every cache of the active database. The macro flag is "
                    "the macros' own, separate from the user flag; filter with "
                    "`filter{ macro_flag = true }`. Needs the user's permission "
                    "like opensak.update().",
        example='opensak.macro_flag("clear", { all = true })\n'
                'for c in opensak.caches{ fields = {"difficulty"} } do\n'
                '    if c.difficulty and c.difficulty >= 4 then opensak.macro_flag("set", c.code) end\n'
                "end\n"
                "opensak.filter{ macro_flag = true }",
        since=3,
        bind=lambda rt, lua: rt._macro_flag,
        params=(Param("action", '"set"|"clear"|"swap"', "What to do with the flag."),
                Param("caches", "string|string[]|{all: true}",
                      "A GC code, a list of codes or { all = true }; default: the "
                      "caches of the active filter.", optional=True)),
        returns=("integer", "The number of caches whose flag changed."),
        write=True,
    ),
    ApiFunction(
        name="insert",
        description="Add a new cache to the active database. `code`, `name`, "
                    "`type`, `lat` and `lon` are required; any other writable "
                    "field may be given too (see Changing caches). Fails if "
                    "the code is already in the database. Needs the user's "
                    "permission like opensak.update().",
        example='opensak.insert{ code = "GC12345", name = "My bonus", type = "Unknown",\n'
                '                lat = 47.36872, lon = 8.54093, user_flag = true }',
        since=3,
        bind=lambda rt, lua: rt._insert,
        params=(Param("fields", "opensak.CacheInsert", "The new cache's fields."),),
        returns=("string", "The cache code as stored (upper case)."),
        write=True,
    ),
    ApiFunction(
        name="delete",
        description="Delete caches from the active database, each with its "
                    "logs, waypoints, attributes, trackables and user note, "
                    "like Waypoint → Delete cache. Takes one GC code, or a list of "
                    "them, e.g. opensak.codes() for the caches of the active "
                    "filter. Deleted caches cannot be restored from inside "
                    "OpenSAK (only from a backup); at the end of the run the "
                    "macro output says how many caches were deleted. The "
                    "cache list and map drop them after the macro. Locked "
                    "caches are deleted too: the lock only protects from "
                    "imports. Needs the user's permission like opensak.update().",
        example='for c in opensak.caches{ archived = true, fields = {"code"} } do\n'
                '    opensak.delete(c.code)\nend\n'
                '-- all caches of the active filter at once:\n'
                'print(opensak.delete(opensak.codes()) .. " caches deleted")',
        since=3,
        bind=lambda rt, lua: rt._delete,
        params=(_CODE,),
        overloads=((Param("codes", "string[]", "GC codes."),),),
        returns=("boolean|integer", "For one code: false if the cache is not in the "
                                    "database. For a list: the number of caches deleted."),
        write=True,
    ),
    *_child_api(LOG),
    *_child_api(WAYPOINT),
    *_child_api(ATTRIBUTE),
    *_child_api(TRACKABLE),
    ApiFunction(
        name="sql_write",
        description="Run one INSERT, UPDATE or DELETE statement (SQLite) against the "
                    "active database — the power tool for bulk work, changing "
                    "many rows with one statement. For a single cache or row, "
                    "prefer `opensak.update()`, `insert{}`, `delete()`, "
                    "`insert_<x>()`, `update_<x>()` and `delete_<x>()`, which "
                    "check each field and need no "
                    "knowledge of the schema (see Changing caches). Only the cache tables can be changed ("
                    + ", ".join(f"`{t}`" for t in sorted(WRITABLE_TABLES)) +
                    "), and an UPDATE may not set the "
                    "keys or the columns OpenSAK maintains itself (see "
                    "Changing caches). Deleting a cache also deletes its "
                    "logs, waypoints, attributes, trackables and user note, "
                    "as opensak.delete() does. Locked caches are changed and "
                    "deleted like any other (add `AND locked = 0` to leave "
                    "them alone). REPLACE (`INSERT OR REPLACE`, "
                    "`REPLACE INTO`, …) is refused, as it would delete those too; "
                    "to update or insert, use `INSERT … ON CONFLICT (gc_code) "
                    "DO UPDATE SET …`. OpenSAK recalculates distances, counts "
                    "and log dates of the caches the statement touched — inside "
                    "opensak.transaction() once at its end (see opensak.resync()). An "
                    "INSERT only needs the real data: the columns "
                    "opensak.columns() marks as `required`, e.g. `gc_code`, "
                    "`name`, `cache_type`, `latitude` and `longitude` for a "
                    "cache (see Changing caches); every other column gets its "
                    "default. Each "
                    "statement is committed on its own (or with the "
                    "surrounding opensak.transaction()), or not at all on an "
                    "error. BEGIN, COMMIT and the like are refused; use "
                    "opensak.transaction(). Needs the user's permission like "
                    "opensak.update().",
        example='local n = opensak.sql_write(\n'
                '  "UPDATE caches SET user_data_1 = ? WHERE country = ? AND found = 0",\n'
                '  { "todo", "Switzerland" })\n'
                'print(n .. " caches marked")\n'
                '-- a found log: only cache_id and log_type are required\n'
                'opensak.sql_write([[\n'
                '  INSERT INTO logs (cache_id, log_type, log_date, finder, text)\n'
                '  SELECT id, \'Found it\', ?, ?, ? FROM caches WHERE gc_code = ?]],\n'
                '  { "2026-10-10 14:30:00.000000", "myUsername", "TFTC", "GC12345" })\n'
                '-- keep only the 5 newest logs of each cache\n'
                'opensak.sql_write([[DELETE FROM logs WHERE id NOT IN (\n'
                '  SELECT l2.id FROM logs l2 WHERE l2.cache_id = logs.cache_id\n'
                '  ORDER BY l2.log_date DESC LIMIT 5)]])',
        since=3,
        bind=lambda rt, lua: rt._sql_write,
        params=(
            Param("query", "string", "One INSERT, UPDATE or DELETE statement."),
            Param("params", "table", "As for opensak.sql().", optional=True),
        ),
        returns=("integer", "Number of rows inserted, updated or deleted (without "
                            "the rows deleted along with a cache)."),
        write=True,
    ),
    ApiFunction(
        name="transaction",
        description="Run *fn* as one transaction: every change it makes to the "
                    "active database (opensak.update, insert, delete, "
                    "sql_write, set_corrected, clear_corrected and the "
                    "functions for logs, waypoints, ...) is kept when *fn* returns, "
                    "and all of it is undone if *fn* raises an error, which "
                    "opensak.transaction() then raises again. Many changes in "
                    "one transaction are also faster than the same changes "
                    "committed one by one. Inside *fn*, opensak.cache, "
                    "caches, sql and the like see the changes already made, "
                    "except the columns OpenSAK maintains (counts, log dates, "
                    "distance), which are recalculated once before the commit "
                    "or when opensak.resync() is called; "
                    "everything else in OpenSAK, exports included, sees them "
                    "only afterwards. A transaction inside another one is "
                    "undone on its own on an error, and kept with the outer "
                    "one otherwise. opensak.switch_database, move_caches and "
                    "copy_caches cannot be used inside. Needs the user's "
                    "permission like opensak.update(). A macro that ends "
                    "inside a transaction (e.g. cancelled) keeps none of it.",
        example='local n = opensak.transaction(function()\n'
                '    local n = 0\n'
                '    for c in opensak.caches{ found = true, fields = {"code"} } do\n'
                '        opensak.update(c.code, { user_flag = true })\n'
                '        n = n + 1\n'
                '    end\n'
                '    return n\n'
                'end)\n'
                'print(n .. " caches flagged")\n'
                '-- error() inside the function undoes everything it changed:\n'
                'local ok, err = pcall(opensak.transaction, function()\n'
                '    opensak.update("GC12345", { user_flag = true })\n'
                '    error("changed my mind")\n'
                'end)',
        since=3,
        bind=lambda rt, lua: lua.execute(
            _TRANSACTION_LUA, rt._wrap(rt._tx_begin), rt._wrap(rt._tx_end)
        ),
        params=(Param("fn", "fun()", "The function to run."),),
        returns=("any...", "What *fn* returns."),
        write=True,
    ),
    ApiFunction(
        name="in_transaction",
        description="Whether the macro is inside opensak.transaction().",
        example='if not opensak.in_transaction() then print("each change is committed on its own") end',
        since=3,
        bind=lambda rt, lua: rt._in_transaction,
        returns=("boolean", "true inside opensak.transaction()."),
        write=True,
    ),
    ApiFunction(
        name="resync",
        description="Recalculate now the columns OpenSAK maintains itself "
                    "(distance and bearing, log, waypoint and trackable counts, "
                    "log dates) for the caches changed so far in the open "
                    "opensak.transaction(). Inside a transaction they are "
                    "recalculated only once, just before the commit, which "
                    "makes many writes much faster; until then opensak.cache, "
                    "sql and the like still see the old values. Call resync() "
                    "when a later read inside the transaction depends on "
                    "them. Outside a transaction it does nothing, as every "
                    "write is recalculated before its own commit.",
        example='opensak.transaction(function()\n'
                '    for i = 1, 3 do opensak.insert_log("GC12345", { type = "Write note" }) end\n'
                '    opensak.resync()\n'
                '    print(opensak.cache("GC12345").log_count)\n'
                'end)',
        since=3,
        bind=lambda rt, lua: rt._resync,
        returns=("integer", "Number of caches recalculated (0 outside a transaction)."),
        write=True,
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
        name="write_text",
        description="Write *text* to a file as UTF-8 and return the file "
                    "written. A relative path is resolved against the macro "
                    "file's folder. The folder needs write permission (the "
                    "OpenSAK temp folder, opensak.temp_dir(), has it by "
                    "default); for an unapproved one the user is asked first. "
                    "Only these file types can be written: "
                    + " ".join(WRITE_TEXT_SUFFIXES) + ". An existing file is "
                    "replaced, or extended with `append = true`. At most "
                    f"{MAX_WRITE_BYTES // (1024 * 1024)} MB.",
        example='local path = opensak.write_text(opensak.temp_dir() .. "/report.html",\n'
                '  "<h1>" .. opensak.count() .. " caches</h1>")\n'
                'print("written: " .. path)',
        since=3,
        bind=lambda rt, lua: rt._write_text,
        params=(
            Param("path", "string", "The file to write."),
            Param("text", "string", "The content."),
            Param("options", "{append?: boolean}",
                  "`append`: add *text* at the end of an existing file.",
                  optional=True),
        ),
        returns=("string", "The file written (full path)."),
    ),
    ApiFunction(
        name="open",
        description="Open a file in its default app, e.g. an HTML report in "
                    "the browser, so a report macro can end by showing its "
                    "result. A relative path is resolved against the macro "
                    "file's folder. A file this run wrote (opensak.write_text, "
                    "opensak.export_*, opensak.copy_file / move_file) or one "
                    "in opensak.temp_dir() opens directly; for any other file "
                    "the user is asked first. Only these file types: "
                    + " ".join(OPEN_SUFFIXES) + " — never a program, script or "
                    "shortcut — and no URLs. At most "
                    f"{MAX_OPENS_PER_RUN} files per run. Needs the user "
                    "interface.",
        example='local path = opensak.write_text(opensak.temp_dir() .. "/report.html",\n'
                '  opensak.html.page{ title = "Report", opensak.html.table(rows) })\n'
                "opensak.open(path)",
        since=3,
        bind=lambda rt, lua: rt._open,
        params=(Param("path", "string", "The file to open."),),
    ),
    ApiFunction(
        name="read_text",
        description="Read a whole text file, of any file type. A relative "
                    "path is resolved against the macro file's folder. The "
                    "folder needs read permission; for an unapproved one the "
                    "user is asked first. UTF-8 unless `encoding` says "
                    "otherwise (" + ", ".join(f'`"{e}"`' for e in READ_TEXT_ENCODINGS)
                    + "); a UTF-8 byte order mark is removed and line endings "
                    "become `\"\\n\"`. At most "
                    f"{MAX_READ_BYTES // (1024 * 1024)} MB.",
        example='local text = opensak.read_text("notes.txt")\n'
                'for line in text:gmatch("[^\\n]+") do\n'
                "    print(line)\nend",
        since=3,
        bind=lambda rt, lua: rt._read_text,
        params=(
            Param("path", "string", "The file to read."),
            Param("options", "{encoding?: string}",
                  '`encoding`: e.g. `"cp1252"` for a file from an older Windows program.',
                  optional=True),
        ),
        returns=("string", "The file's text."),
    ),
    ApiFunction(
        name="file_exists",
        description="Whether a file exists (false for a folder). A relative "
                    "path is resolved against the macro file's folder. The "
                    "folder needs read permission, like opensak.read_text().",
        example='if not opensak.file_exists(opensak.temp_dir() .. "/report.html") then\n'
                '    print("no report yet")\nend',
        since=3,
        bind=lambda rt, lua: lambda path=None: rt._exists("opensak.file_exists", path, False),
        params=(Param("path", "string", "The file."),),
        returns=("boolean", "true if the file exists."),
    ),
    ApiFunction(
        name="folder_exists",
        description="Whether a folder exists. A relative path is resolved "
                    "against the macro file's folder. The folder containing "
                    "it needs read permission.",
        example='if opensak.folder_exists("C:/GPX") then print("found") end',
        since=3,
        bind=lambda rt, lua: lambda path=None: rt._exists("opensak.folder_exists", path, True),
        params=(Param("path", "string", "The folder."),),
        returns=("boolean", "true if the folder exists."),
    ),
    ApiFunction(
        name="list_dir",
        description="The files and folders in a folder (not in its "
                    "subfolders), sorted by name. A relative path is resolved "
                    "against the macro file's folder. The folder needs read "
                    "permission; for an unapproved one the user is asked "
                    "first. `pattern` keeps the names that match it, ignoring "
                    "case: `*` stands for any text, `?` for one character. At "
                    f"most {MAX_DIR_ENTRIES:,} entries. Each entry has `name`, "
                    "`path` (full path), `dir` (true for a folder), `size` "
                    "(bytes, 0 for a folder) and `modified` (seconds since the "
                    "epoch, like `os.time()`).",
        example='for _, f in ipairs(opensak.list_dir("C:/GPX", "*.gpx")) do\n'
                '    print(f.name, f.size, os.date("%Y-%m-%d", f.modified))\nend',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._list_dir(lua, *a),
        params=(
            Param("folder", "string", "The folder."),
            Param("pattern", "string", 'A file name pattern, e.g. "*.gpx".', optional=True),
        ),
        returns=("{name: string, path: string, dir: boolean, size: integer, modified: integer}[]",
                 "One table per entry."),
    ),
    ApiFunction(
        name="file_info",
        description="Size and modification time of one file or folder, as "
                    "in opensak.list_dir(); nil if it does not exist. A "
                    "relative path is resolved against the macro file's "
                    "folder. The folder needs read permission.",
        example='local f = opensak.file_info("C:/GPX/pq.zip")\n'
                'if f and os.time() - f.modified < 86400 then print("downloaded today") end',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._file_info(lua, *a),
        params=(Param("path", "string", "The file or folder."),),
        returns=("{name: string, path: string, dir: boolean, size: integer, modified: integer}?",
                 "nil if there is nothing at *path*."),
    ),
    ApiFunction(
        name="copy_file",
        description="Copy a file (GSAK's FileCopy) and return the file "
                    "written. *target* is a file, or a folder (existing, or "
                    "ending in `/`) to copy into under the same name. "
                    "Relative paths are resolved against the macro file's "
                    "folder. The source's folder needs read permission and "
                    "the target's folder write permission; for an unapproved "
                    "one the user is asked first. Only these file types can "
                    "be written: " + " ".join(FILE_SUFFIXES) + " — no "
                    "programs or scripts. An existing target is replaced, "
                    "the same way opensak.write_text() replaces a file, unless "
                    '`if_exists = "skip"`. The modification time is kept. At '
                    f"most {MAX_COPY_BYTES // (1024 * 1024 * 1024)} GB.",
        example='local backup = opensak.copy_file("C:/GPX/pq.zip", opensak.temp_dir() .. "/")\n'
                'print("copied to " .. backup)',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._copy_file("opensak.copy_file", False, *a),
        params=(
            Param("source", "string", "The file to copy."),
            Param("target", "string", "The new file, or the folder to copy into."),
            Param("options", '{if_exists?: "overwrite"|"skip"}',
                  '`if_exists`: `"overwrite"` (default) or `"skip"`.', optional=True),
        ),
        returns=("string?", 'The file written (full path); nil if skipped.'),
    ),
    ApiFunction(
        name="move_file",
        description="Move or rename a file: like opensak.copy_file(), then "
                    "the source is deleted, so its folder needs write "
                    "permission too and it must be one of the file types "
                    "listed there. Nothing is copied when the source may not "
                    "be deleted.",
        example='for _, f in ipairs(opensak.list_dir("C:/Downloads", "*.zip")) do\n'
                '    opensak.move_file(f.path, "C:/GPX/done/")\nend',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._copy_file("opensak.move_file", True, *a),
        params=(
            Param("source", "string", "The file to move."),
            Param("target", "string", "The new file, or the folder to move into."),
            Param("options", '{if_exists?: "overwrite"|"skip"}',
                  '`if_exists`: `"overwrite"` (default) or `"skip"`.', optional=True),
        ),
        returns=("string?", "The new file (full path); nil if skipped (the "
                            "source is then kept)."),
    ),
    ApiFunction(
        name="delete_file",
        description="Delete a file (GSAK's FileErase). A relative path is "
                    "resolved against the macro file's folder. Its folder "
                    "needs write permission; for an unapproved one the user "
                    "is asked first. Only the file types opensak.copy_file() "
                    "writes can be deleted, never a folder, a link or "
                    "OpenSAK's own files.",
        example='for _, f in ipairs(opensak.list_dir(opensak.temp_dir(), "*.gpx")) do\n'
                '    opensak.delete_file(f.path)\nend',
        since=3,
        bind=lambda rt, lua: lambda path=None: rt._delete_path("opensak.delete_file", False, path),
        params=(Param("path", "string", "The file."),),
        returns=("boolean", "false if the file did not exist."),
    ),
    ApiFunction(
        name="create_folder",
        description="Create a folder, with any missing folders above it, and "
                    "return it. A relative path is resolved against the "
                    "macro file's folder. The folder it goes into needs "
                    "write permission; for an unapproved one the user is "
                    "asked first. A folder that exists already is fine.",
        example='local out = opensak.create_folder(opensak.temp_dir() .. "/" .. os.date("%Y-%m-%d"))',
        since=3,
        bind=lambda rt, lua: rt._create_folder,
        params=(Param("path", "string", "The folder."),),
        returns=("string", "The folder (full path)."),
    ),
    ApiFunction(
        name="delete_folder",
        description="Delete an empty folder; a folder with anything in it is "
                    "an error, so delete its files first. The folder it is "
                    "in needs write permission. A folder listed in Settings "
                    "→ Folder permissions is never deleted.",
        example='opensak.delete_folder(opensak.temp_dir() .. "/old")',
        since=3,
        bind=lambda rt, lua: lambda path=None: rt._delete_path("opensak.delete_folder", True, path),
        params=(Param("path", "string", "The folder."),),
        returns=("boolean", "false if the folder did not exist."),
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
        name="export_poi",
        description="Export the caches of the active filter and/or their child "
                    "waypoints as Garmin POI files (.gpi) with a saved POI "
                    "export setting (File → Export Garmin POI: folder, file "
                    "name, if the file exists, which points, name/description/"
                    "extra templates, category, proximity alert, icon, max. "
                    "points). The file name variables are filled in as in "
                    "the dialog. With \"one file per waypoint type\" a file "
                    "is written for the caches and one for every waypoint "
                    "type. The files go into *folder* if given, else into "
                    "the setting's folder, which needs write permission "
                    "(Settings → Folder permissions); for an unapproved one "
                    "the user is asked first. An existing file is skipped "
                    "when the setting says skip (or ask, and the user "
                    "answers No).",
        example='local files, n = opensak.export_poi("Garmin POI")\n'
                'if files then print(n .. " POIs → " .. table.concat(files, ", ")) end',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._export_poi(lua, *a),
        params=(
            Param("setting", "string", "Name of the saved POI export setting."),
            Param("folder", "string", "Folder to write to instead of the "
                  "setting's folder, e.g. the device's Garmin/POI folder.",
                  optional=True),
        ),
        returns=("string[]?, integer?",
                 "The files written and the number of POIs in them; nil if "
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
        name="choose_folder",
        description="Let the user pick a folder in a folder dialog. The macro "
                    "may then read and write files in it for the rest of this "
                    "run, without a folder permission. OpenSAK's own folders "
                    "and whole drives cannot be picked.",
        example='local folder = opensak.choose_folder("Export to")\n'
                'if not folder then return end      -- cancelled\n'
                'opensak.export_gpx{ path = folder .. "/{filter}.gpx" }',
        since=3,
        bind=lambda rt, lua: rt._choose_folder,
        params=(Param("title", "string", "Dialog title.", optional=True),),
        returns=("string?", "Full path of the folder, or nil if cancelled."),
    ),
    ApiFunction(
        name="message",
        description="Show a message with an OK button and wait until the user "
                    "closes it.",
        example='opensak.message("Done: 12 caches updated")',
        since=3,
        bind=lambda rt, lua: rt._message,
        params=(
            Param("text", "string", "The message."),
            Param("title", "string", "Window title.", optional=True),
        ),
    ),
    ApiFunction(
        name="input",
        description="Ask for one line of text.",
        example='local name = opensak.input("Name of the new database", "CH_Zurich")\n'
                'if not name then return end      -- cancelled',
        since=3,
        bind=lambda rt, lua: rt._input,
        params=(
            Param("prompt", "string", "The question."),
            Param("default", "string", "Text the field starts with.", optional=True),
            Param("title", "string", "Window title.", optional=True),
        ),
        returns=("string?", "The text entered, or nil if cancelled."),
    ),
    ApiFunction(
        name="choose",
        description="Let the user pick one item of a list.",
        example='local fmt = opensak.choose("Export format", {"GPX", "GGZ", "KML"}, "GPX")\n'
                'if not fmt then return end      -- cancelled',
        since=3,
        bind=lambda rt, lua: rt._choose,
        params=(
            Param("prompt", "string", "The question."),
            Param("options", "(string|number)[]", "The items."),
            Param("default", "string|number", "The item selected at first.", optional=True),
            Param("title", "string", "Window title.", optional=True),
        ),
        returns=("string|number?", "The item picked, or nil if cancelled."),
    ),
    ApiFunction(
        name="form",
        description="Show a dialog built from a table (see Forms) and wait "
                    "until it is closed. Fields with a value come back in a "
                    "table keyed by their `id`. Callbacks such as `on_change` "
                    "run while the form stays open and can change it through "
                    "the handle they get. Without `buttons` the form has OK "
                    "and Cancel. Tools → Form designer in the macro window "
                    "builds forms visually and imports GSAK forms.",
        example='local v, button = opensak.form{\n'
                '  title = "Export settings",\n'
                '  remember = true,\n'
                '  fields = {\n'
                '    { type = "folder",   id = "folder",  label = "Output folder", required = true },\n'
                '    { type = "number",   id = "maxdist", label = "Max distance", default = 25,\n'
                '      min = 0, suffix = " km" },\n'
                '    { type = "checkbox", id = "unfound", label = "Unfound only", default = true },\n'
                '    { type = "choice",   id = "fmt",     label = "Format", options = {"GPX", "GGZ"},\n'
                '      on_change = function(f, value)\n'
                '        f:set_enabled("unfound", value == "GPX")\n'
                '      end },\n'
                '  },\n'
                '}\n'
                'if not v then return end      -- cancelled\n'
                'print(v.folder, v.maxdist, v.unfound, v.fmt)',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._form(lua, *a),
        params=(Param("spec", "opensak.FormSpec", "The form."),),
        returns=("table<string, any>?, string?",
                 "The values by field id and the id of the button (or "
                 "`exit_on_change` field, or \"timeout\") that closed the "
                 "form; nil if it was cancelled."),
    ),
    ApiFunction(
        name="progress",
        description="Open a progress window with a Cancel button, for long "
                    "loops. `p:update(i [, text])` moves the bar to *i* of "
                    "*total* and returns false once the user has pressed "
                    "Cancel; `p:close()` closes the window (also done when "
                    "the macro ends). Without *total* the bar just shows "
                    "that work is going on.",
        example='local codes = opensak.codes()\n'
                'local p = opensak.progress("Checking caches", #codes)\n'
                'for i, code in ipairs(codes) do\n'
                '    if not p:update(i, code) then break end      -- Cancel pressed\n'
                '    -- … work on the cache …\n'
                'end\n'
                'p:close()',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._progress(lua, *a),
        params=(
            Param("title", "string", "What is being done."),
            Param("total", "integer", "Number of steps.", optional=True),
        ),
        returns=("opensak.Progress", "The window."),
    ),
    ApiFunction(
        name="select",
        description="Select a cache in the cache list and show it in the "
                    "detail panel and on the map, like a click on its row.",
        example='if not opensak.select("GC12345") then print("not in the current list") end',
        since=3,
        bind=lambda rt, lua: rt._select,
        params=(_CODE,),
        returns=("boolean", "false if the cache is not among the caches of the active filter."),
    ),
    ApiFunction(
        name="refresh",
        description="Redraw the cache list, detail panel and map now. Changes "
                    "a macro makes are otherwise shown when it ends.",
        example="opensak.set_corrected(\"GC12345\", 47.36872, 8.54093)\n"
                "opensak.refresh()",
        since=3,
        bind=lambda rt, lua: rt._refresh,
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
        name="cache_types",
        description="All cache types OpenSAK knows, with GSAK's one-letter "
                    "code where GSAK has one, e.g. for a form's choice list "
                    "or `filter{ type = … }`.",
        example='for _, t in ipairs(opensak.cache_types()) do print(t.code or "-", t.name) end',
        since=3,
        bind=lambda rt, lua: lambda: _cache_types(lua),
        returns=("{name: string, code: string?}[]", "One table per cache type."),
    ),
    ApiFunction(
        name="cache_type",
        description="Convert a cache type between GSAK's one-letter code and "
                    "its name, like GSAK's CacheType(): *value* is a letter "
                    '("T"), a full name ("Traditional Cache") or a short one '
                    '("Traditional"), in any case.',
        example='local name, code = opensak.cache_type("U")  -- "Unknown Cache", "U"\n'
                'print(select(2, opensak.cache_type("multi-cache")))  -- M',
        since=3,
        bind=lambda rt, lua: _cache_type,
        params=(Param("value", "string", "A GSAK letter or a cache type name."),),
        returns=("string?, string?", "The cache type and its GSAK letter (nil if "
                                     "it has none); nil if the type is unknown."),
    ),
    ApiFunction(
        name="waypoint_types",
        description="The child waypoint types, as in the waypoint dialog, each "
                    "with its usual prefix — e.g. for "
                    "opensak.insert_waypoint().",
        example='for _, t in ipairs(opensak.waypoint_types()) do print(t.prefix, t.name) end',
        since=3,
        bind=lambda rt, lua: lambda: _waypoint_types(lua),
        returns=("{name: string, prefix: string}[]", "One table per waypoint type."),
    ),
    ApiFunction(
        name="waypoint_type",
        description="The waypoint type for a type name (any case) or a "
                    'waypoint prefix ("PK", "S1", "F"), with its usual prefix '
                    "(GSAK's ChildType).",
        example='local name, prefix = opensak.waypoint_type("PK")  -- "Parking Area", "PK"',
        since=3,
        bind=lambda rt, lua: _waypoint_type,
        params=(Param("value", "string", "A waypoint type or prefix."),),
        returns=("string?, string?", "The waypoint type and its usual prefix; nil "
                                     "if it is unknown."),
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
        name="run_macro",
        description="Run another macro and wait until it has finished, e.g. to "
                    "chain several macros from one control macro. A relative "
                    "path is looked up in the folder of the calling macro, and "
                    "`.lua` is added when the name has no extension. The "
                    "called macro gets its own global variables; values are "
                    "passed as extra arguments (read them with `...`) and "
                    "come back as its return values. An error in it stops "
                    "the calling macro too, unless caught with `pcall`. "
                    "Macros run this way share the run's limits, sleep time "
                    "and folder approvals; they may call each other at most "
                    f"{MAX_MACRO_DEPTH} levels deep.",
        example='opensak.run_macro("refresh_corrected")\n'
                'local count = opensak.run_macro("lib/count_unfound.lua", "Zürich")\n'
                '-- lib/count_unfound.lua:\n'
                '--   local area = ...\n'
                '--   return opensak.filter{ found = false, state = area }',
        since=3,
        bind=lambda rt, lua: rt._run_macro,
        params=(
            Param("file", "string", "The macro file."),
            Param("...", "any", "Passed to the macro as `...`."),
        ),
        returns=("any...", "What the macro returns."),
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
        description="Parse a coordinate string. Without `fmt` the format is "
                    "detected: latitude/longitude (DMM, DMS, decimal degrees), "
                    "UTM, MGRS, British grid letters (\"TQ 30268 79642\"), "
                    "Plus Codes, Maidenhead locators, geohashes (5+ characters "
                    "mixing letters and digits) and the Swiss, Dutch, Swedish "
                    "and German grids as number pairs, which are only accepted "
                    "inside their country. Pass `fmt` to read one format only — "
                    "needed for numeric British grid positions, short geohashes "
                    "and to rule out a wrong guess. Formats: " + ", ".join(
                        f"`\"{k}\"`" for k in INPUT_KEYS) + " (`\"latlon\"` "
                    "covers DD, DMM and DMS; every name `opensak.coords.format` "
                    "takes works too).",
        example='local lat, lon = opensak.coords.parse("N47 22.123 E008 32.456")\n'
                'if not lat then error("not a coordinate") end\n'
                'local lat2, lon2, fmt = opensak.coords.parse("32T MT 65339 46242")\n'
                'print(fmt)  -- mgrs\n'
                'print(opensak.coords.parse("530268 179642", "osgb"))',
        since=2,
        bind=lambda rt, lua: rt._coords_parse,
        params=(
            Param("text", "string", "The coordinates."),
            Param("fmt", "string", "Read only this format; detect it if omitted.",
                  optional=True),
        ),
        returns=("number?, number?, string?", "Latitude, longitude and the "
                 "format that was read, or nil if the text cannot be parsed."),
        changed=((3, "added `fmt`, the third return value and the grid, "
                     "code and locator formats"),),
    ),
    ApiFunction(
        name="coords.format",
        description="Format coordinates. Formats: `\"dmm\"` (default), `\"dms\"`, "
                    "`\"dd\"`, `\"utm\"`, `\"mgrs\"`, `\"ch1903\"` (Swiss LV03), "
                    "`\"ch1903+\"` (Swiss LV95), `\"rd\"` (Dutch RD), `\"osgb\"` "
                    "(British National Grid), `\"sweref99\"` (SWEREF 99 TM), "
                    "`\"gk\"` (German Gauss-Krüger), `\"olc\"` (Plus Code), "
                    "`\"geohash\"` and `\"maidenhead\"`. Aliases: `\"lv03\"`, "
                    "`\"lv95\"`, `\"bng\"`, `\"sweref\"`, `\"gauss-krueger\"`, "
                    "`\"pluscode\"`, `\"qth\"`. The national grids are only "
                    "meaningful in and around their country; `\"osgb\"` and "
                    "`\"utm\"` raise an error where they are not defined.",
        example='print(opensak.coords.format(47.36872, 8.54093, "utm"))\n'
                'print(opensak.coords.format("N47 22.123 E008 32.456", "ch1903"))\n'
                'print(opensak.coords.format(51.50072, -0.12462, "osgb"))',
        since=2,
        bind=lambda rt, lua: rt._coords_format,
        params=(
            _LAT, _LON,
            Param("fmt", "string", 'Output format, "dmm" if omitted.', optional=True),
        ),
        overloads=((_COORDS, Param("fmt", "string", "Output format.", optional=True)),),
        returns=("string", 'E.g. "N47 22.123  E008 32.456" or "32T E 465123 N 5247123".'),

        changed=((3, "added `\"mgrs\"`, `\"rd\"`, `\"osgb\"`, `\"sweref99\"`, "
                     "`\"gk\"`, `\"olc\"`, `\"geohash\"`, `\"maidenhead\"` "
                     "and the aliases"),),
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
    ApiFunction(
        name="re.escape",
        description="Escape every character that has a meaning in a regular "
                    "expression, so the text matches literally (GSAK's "
                    "RegExEscape), e.g. to search for a cache name in a pattern.",
        example='local pattern = "^" .. opensak.re.escape("Bonus (1/3)") .. "$"\n'
                'print(opensak.re.match("Bonus (1/3)", pattern))  -- true',
        since=3,
        bind=lambda rt, lua: helpers.re_escape,
        params=(_TEXT,),
        returns=("string", "The escaped text."),
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
    ApiFunction(
        name="text.upper",
        description="Upper case for all letters, not only ASCII: "
                    "`string.upper` and `s:upper()` leave ä, é, ø … as they "
                    "are. ß becomes SS.",  # i18n: ignore — API docs example letters
        example='print(opensak.text.upper("zürich"))  -- ZÜRICH; ("zürich"):upper() gives ZüRICH',
        since=3,
        bind=lambda rt, lua: helpers.upper,
        params=(_TEXT,),
        returns=("string", "The text in upper case."),
    ),
    ApiFunction(
        name="text.lower",
        description="Lower case for all letters, not only ASCII (see "
                    "opensak.text.upper()).",
        example='if opensak.text.lower(c.country) == "österreich" then print(c.code) end',
        since=3,
        bind=lambda rt, lua: helpers.lower,
        params=(_TEXT,),
        returns=("string", "The text in lower case."),
    ),
    ApiFunction(
        name="text.trim",
        description="Remove white space at the start and end, including "
                    "line breaks and Unicode spaces such as the no-break "
                    "space that `%s` in a Lua pattern does not match "
                    "(GSAK's Alltrim).",
        example='local name = opensak.text.trim("  Zürich\\u{00A0}")  -- "Zürich"',
        since=3,
        bind=lambda rt, lua: helpers.trim,
        params=(_TEXT,),
        returns=("string", "The trimmed text."),
    ),
    ApiFunction(
        name="text.sub",
        description="Like `string.sub`, but counting characters instead of "
                    "bytes, so it never cuts an ü or é in half (GSAK's Left, "
                    "Right and SubStr). Positions start at 1; negative ones "
                    "count from the end; both ends are included. "
                    "`utf8.len(s)` gives the number of characters.",
        example='local s = "Zürich Nord"\n'
                'print(opensak.text.sub(s, 1, 6))  -- Zürich (Left)\n'
                'print(opensak.text.sub(s, -4))    -- Nord (Right)',
        since=3,
        bind=lambda rt, lua: helpers.sub,
        params=(
            _TEXT,
            Param("i", "integer", "First character (default 1).", optional=True),
            Param("j", "integer", "Last character (default -1, the end).", optional=True),
        ),
        returns=("string", "The characters from i to j."),
    ),
    ApiFunction(
        name="text.html_decode",
        description="Decode HTML entities such as `&amp;`, `&auml;` and "
                    "`&#228;` and keep everything else, tags included "
                    "(GSAK's EntityDecode). opensak.text.html_to_text() "
                    "also removes the tags.",
        example='print(opensak.text.html_decode("Z&uuml;rich &amp; Bern"))  -- Zürich & Bern',
        since=3,
        bind=lambda rt, lua: helpers.html_decode,
        params=(_TEXT,),
        returns=("string", "The decoded text."),
    ),
    ApiFunction(
        name="text.csv_split",
        description="Split one CSV line into its fields (GSAK's CsvGet): a "
                    "field in double quotes may contain the separator, and "
                    "`\"\"` inside quotes is one quote. For a whole file use "
                    "opensak.read_csv().",
        example='local f = opensak.text.csv_split([[GC12345,"Bonus, final",N47 22.123]])\n'
                'print(f[2])  -- Bonus, final',
        since=3,
        bind=lambda rt, lua: lambda *a: lua.table_from(helpers.csv_split(*a)),
        params=(
            Param("line", "string", "One line of CSV."),
            Param("sep", "string", 'Field separator, one character (default ",").',
                  optional=True),
        ),
        returns=("string[]", "The fields."),
    ),
    ApiFunction(
        name="text.url_encode",
        description="Percent-encode text for a URL, e.g. a search term in a "
                    "link: everything except letters, digits and `-._~` "
                    "becomes `%XX` of its UTF-8 bytes (GSAK's HttpEncode).",
        example='local url = "https://www.geocaching.com/play/search?q=" .. '
                'opensak.text.url_encode("Zürich & Bern")',
        since=3,
        bind=lambda rt, lua: helpers.url_encode,
        params=(_TEXT,),
        returns=("string", "The encoded text."),
    ),
    ApiFunction(
        name="text.url_decode",
        description="Decode `%XX` sequences, and `+` as a space as in a "
                    "query string.",
        example='print(opensak.text.url_decode("Z%C3%BCrich+Nord"))  -- Zürich Nord',
        since=3,
        bind=lambda rt, lua: helpers.url_decode,
        params=(_TEXT,),
        returns=("string", "The decoded text."),
    ),
    ApiFunction(
        name="text.base64_encode",
        description="Base64-encode text (GSAK's MimeEncode).",
        example='print(opensak.text.base64_encode("Zürich"))  -- WsO8cmljaA==',
        since=3,
        bind=lambda rt, lua: helpers.base64_encode,
        params=(_TEXT,),
        returns=("string", "The Base64 text."),
    ),
    ApiFunction(
        name="text.base64_decode",
        description="Decode Base64 (GSAK's MimeDecode); white space and line "
                    "breaks in it are ignored. Invalid Base64 is an error.",
        example='print(opensak.text.base64_decode("WsO8cmljaA=="))  -- Zürich',
        since=3,
        bind=lambda rt, lua: helpers.base64_decode,
        params=(Param("text", "string", "The Base64 text."),),
        returns=("string", "The decoded text."),
    ),
    ApiFunction(
        name="text.gc_to_id",
        description="The number behind a geocaching.com code (GSAK's "
                    "GsCodeToNum): up to GCFFFF the code is hexadecimal, from "
                    "GCG000 on it is base 31. Works for TB, GL and other "
                    "two-letter prefixes too; any case.",
        example='print(opensak.text.gc_to_id("GC12345"))  -- 574995\n'
                'print(opensak.text.gc_to_id("GCG000"))   -- 65536',
        since=3,
        bind=lambda rt, lua: helpers.gc_to_id,
        params=(Param("code", "string", 'A code such as "GC12345".'),),
        returns=("integer?", "The number; nil if *code* is not a geocaching.com code."),
    ),
    ApiFunction(
        name="text.id_to_gc",
        description="The geocaching.com code for a number (GSAK's "
                    "NumToGsCode), the reverse of opensak.text.gc_to_id().",
        example='print(opensak.text.id_to_gc(574995))      -- GC12345\n'
                'print(opensak.text.id_to_gc(1, "TB"))     -- TB1',
        since=3,
        bind=lambda rt, lua: helpers.id_to_gc,
        params=(
            Param("id", "integer", "The number, 0 or more."),
            Param("prefix", "string", 'Two letters instead of "GC", e.g. "TB".',
                  optional=True),
        ),
        returns=("string", "The code."),
    ),

    # -- opensak.chart --------------------------------------------------------
    ApiFunction(
        name="chart.bar",
        description="A bar chart of *rows* as an SVG string, for an HTML "
                    "page (opensak.html.page, opensak.write_text) or a .svg "
                    "file. Each row is a "
                    "table; `x` names its category field (default \"key\") "
                    "and `y` its value field (default \"count\"), nil "
                    "counts as 0. `horizontal = true` draws the bars from "
                    "the left, for long category names. Text and grid lines "
                    "take the colour of the surrounding page; the bars use "
                    "the CSS variable --chart-color, else `color`. Each bar "
                    "shows its value as tooltip; `labels` also writes it on "
                    f"the bar (default: up to {charts.MAX_VALUE_LABELS} bars). "
                    "With a list of fields as `y`, the bars are stacked, one "
                    "segment per field, with a legend (`names` instead of the "
                    f"field names; at most {len(charts.SERIES_COLORS)}); a "
                    "matrix from opensak.stats.pivot gives one stacked bar per "
                    "matrix row, e.g. finds per year and cache type. "
                    f"At most {charts.MAX_POINTS} rows.",
        example="local rows = opensak.sql([[SELECT strftime('%Y', found_date) AS year,\n"
                "  COUNT(*) AS finds FROM caches WHERE found GROUP BY year ORDER BY year]])\n"
                'local svg = opensak.chart.bar(rows, { x = "year", y = "finds", title = "Finds per year" })\n'
                'opensak.write_text(opensak.temp_dir() .. "/finds.svg", svg)\n'
                '-- stacked: finds per year and type\n'
                'local by_type = opensak.stats.pivot(finds, "year", "type")\n'
                'local svg2 = opensak.chart.bar(by_type, { title = "Finds per year and type" })',
        since=3,
        bind=lambda rt, lua: functools.partial(rt._chart, "bar"),
        params=(
            Param("rows", "table[]|opensak.Matrix",
                  "The rows, e.g. the result of opensak.sql(), or a matrix."),
            Param("options", "{x?: string, y?: string|string[], title?: string, width?: number, "
                  "height?: number, color?: string, horizontal?: boolean, labels?: boolean, "
                  "names?: string[]}",
                  "Fields to draw, title, size in pixels (default 640 × 280), "
                  "colour (name or \"#rrggbb\").", optional=True),
        ),
        returns=("string", "The chart as SVG."),
    ),
    ApiFunction(
        name="chart.line",
        description="A line chart of *rows* in their order as an SVG string, "
                    "like opensak.chart.bar: `x` the label on the x axis "
                    "(evenly spaced, e.g. months), `y` the value. `area = "
                    "true` fills below the line. The value of the last point "
                    "is written next to it unless `labels = false`. Each "
                    "point shows its value as tooltip.",
        example="local rows, total = {}, 0\n"
                "for _, r in ipairs(opensak.sql([[SELECT strftime('%Y-%m', found_date) AS month,\n"
                "    COUNT(*) AS n FROM caches WHERE found GROUP BY month ORDER BY month]])) do\n"
                "  total = total + r.n\n"
                "  rows[#rows + 1] = { key = r.month, count = total }\n"
                "end\n"
                'local svg = opensak.chart.line(rows, { title = "Total finds", area = true })',
        since=3,
        bind=lambda rt, lua: functools.partial(rt._chart, "line"),
        params=(
            Param("rows", "table[]", "The rows, in the order to draw."),
            Param("options", "{x?: string, y?: string, title?: string, width?: number, "
                  "height?: number, color?: string, area?: boolean, labels?: boolean}",
                  "As for opensak.chart.bar, plus `area`.", optional=True),
        ),
        returns=("string", "The chart as SVG."),
    ),
    ApiFunction(
        name="chart.heatmap",
        description="A grid chart of a matrix (see opensak.stats.pivot) as an "
                    "SVG string: one cell per row × column value, the darker "
                    "the larger, e.g. finds per difficulty × terrain. "
                    "`labels = false` leaves the numbers out of the cells; "
                    "`row_title` and `col_title` name the two fields.",
        example='local dt = opensak.stats.pivot(finds, "difficulty", "terrain",\n'
                '  { rows = {1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5}, cols = {1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5} })\n'
                'local svg = opensak.chart.heatmap(dt, { row_title = "D", col_title = "T" })',
        since=3,
        bind=lambda rt, lua: functools.partial(rt._chart, "heatmap"),
        params=(
            Param("matrix", "opensak.Matrix", "The result of opensak.stats.pivot()."),
            Param("options", "{title?: string, width?: number, color?: string, "
                  "labels?: boolean, row_title?: string, col_title?: string}",
                  "As for opensak.chart.bar, plus the field titles.", optional=True),
        ),
        returns=("string", "The chart as SVG."),
    ),
    ApiFunction(
        name="chart.rose",
        description="A rose (polar area) chart of *rows* as an SVG string: one "
                    "sector per row, in row order clockwise from the top, e.g. "
                    "finds per compass direction (N, NE, E, …). The area of a "
                    "sector grows with its value; `x` names the label field, "
                    "`y` the value (as for opensak.chart.bar).",
        example='local DIRS = { "N", "NE", "E", "SE", "S", "SW", "W", "NW" }\n'
                'for _, c in ipairs(finds) do\n'
                '  c.dir = DIRS[math.floor((opensak.coords.bearing(HOME_LAT, HOME_LON, c.lat, c.lon) + 22.5) / 45) % 8 + 1]\n'
                'end\n'
                'local rows = {}\n'
                'for _, g in ipairs(opensak.stats.group(finds, "dir")) do rows[g.key] = g.count end\n'
                'local sectors = {}\n'
                'for i, d in ipairs(DIRS) do sectors[i] = { key = d, count = rows[d] or 0 } end\n'
                'local svg = opensak.chart.rose(sectors, { title = "Finds by direction" })',
        since=3,
        bind=lambda rt, lua: functools.partial(rt._chart, "rose"),
        params=(
            Param("rows", "table[]", "One row per sector, clockwise from north."),
            Param("options", "{x?: string, y?: string, title?: string, width?: number, "
                  "color?: string, labels?: boolean}",
                  "As for opensak.chart.bar; `width` is also the height.", optional=True),
        ),
        returns=("string", "The chart as SVG."),
    ),
    ApiFunction(
        name="chart.map",
        description="A map of countries, states or counties as an SVG string, "
                    "each region coloured by a value — e.g. finds per country "
                    "from opensak.stats.group. `key` names the field with the "
                    "region name (default \"key\"), `value` the number "
                    "(default \"count\"), `layer` the kind of region: "
                    "\"country\" (default), \"state\" or \"county\". `within` "
                    "(a country name or a list) limits a state or county map "
                    "to those countries; without it the countries the regions "
                    "are in are shown. `extent = \"all\"` shows every region of "
                    "the layer instead of zooming to the ones with a value. "
                    "Names are matched ignoring case, accents and punctuation; "
                    "those that match no region are listed below the map. The "
                    "outlines are those of Update location (offline, no "
                    "internet needed; county outlines are downloaded per "
                    "country on first use, like there). Without the boundary "
                    "data the map is a short note instead.",
        example='local countries = opensak.stats.group(finds, "country")\n'
                'local map = opensak.chart.map(countries, { title = "Finds per country" })\n'
                'local cantons = opensak.stats.group(finds, "state")\n'
                'local map2 = opensak.chart.map(cantons, { layer = "state", within = "Switzerland" })',
        since=3,
        bind=lambda rt, lua: rt._chart_map,
        params=(
            Param("rows", "table[]", "One row per region with its value."),
            Param("options", "{key?: string, value?: string, layer?: string, "
                  "within?: string|string[], title?: string, width?: number, "
                  "height?: number, color?: string, extent?: string, legend?: boolean}",
                  "See above. The height follows the shape of the area unless given.",
                  optional=True),
        ),
        returns=("string", "The map as SVG."),
    ),
    ApiFunction(
        name="chart.points",
        description="A dot map as an SVG string: one dot per row at its "
                    "coordinates (fields `lat` and `lon`), over the outlines of "
                    "`layer` (\"country\", \"state\" or \"county\"), zoomed to "
                    "the dots or to the `within` countries. `label` names the "
                    f"field shown as tooltip. At most {report_maps.MAX_MAP_POINTS} "
                    "rows; dots on the same pixel are drawn once.",
        example='local svg = opensak.chart.points(finds, { label = "name", title = "My finds" })',
        since=3,
        bind=lambda rt, lua: rt._chart_points,
        params=(
            Param("rows", "table[]", "The rows, e.g. caches from opensak.caches()."),
            Param("options", "{lat?: string, lon?: string, label?: string, layer?: string, "
                  "within?: string|string[], title?: string, width?: number, "
                  "height?: number, color?: string, radius?: number}",
                  "See above; `radius` of the dots in pixels (default 2.5).", optional=True),
        ),
        returns=("string", "The map as SVG."),
    ),

    # -- opensak.geo ----------------------------------------------------------
    ApiFunction(
        name="geo.outlines",
        description="The outlines of countries, states or counties (`layer`) "
                    "as a GeoJSON FeatureCollection (a table), e.g. for a "
                    "map drawn by the macro itself or for another program. "
                    "Like opensak.chart.map, `rows` puts the "
                    "value (field `value`, default \"count\") of each row into "
                    "the properties of the region its field `key` (default "
                    "\"key\") names — matched ignoring case, accents and "
                    "punctuation — `within` limits the countries and `extent "
                    "= \"all\"` shows all regions instead of zooming to the ones "
                    "with a value. `around` (rows with `lat`/`lon`) zooms to "
                    "those points instead, `box` (`{min_lon, min_lat, max_lon, "
                    "max_lat}`, e.g. the `bbox` of another collection) to an "
                    "area. The outlines are clipped to the area and simplified "
                    "for a map about `detail` pixels wide (default 800). Every "
                    "feature has `properties.name` (and `country` for states "
                    "and counties). The second value lists the names that match "
                    "no region. nil if the boundary data is not installed.",
        example='local outlines, unmatched = opensak.geo.outlines("country",\n'
                '  { rows = opensak.stats.group(finds, "country") })\n'
                'if outlines then\n'
                '  for _, f in ipairs(outlines.features) do\n'
                '    print(f.properties.name, f.properties.count or 0)\n'
                '  end\n'
                'end',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._geo_outlines(lua, *a),
        params=(
            Param("layer", "string", '"country" (default), "state" or "county".', optional=True),
            Param("options", "{within?: string|string[], rows?: table[], key?: string, "
                  "value?: string, extent?: string, around?: table[], lat?: string, "
                  "lon?: string, box?: number[], detail?: number}", "See above.", optional=True),
        ),
        returns=("table?, string[]?", "The FeatureCollection and the names without a region."),
    ),
    ApiFunction(
        name="geo.regions",
        description="The regions of a layer (\"country\", \"state\" or "
                    "\"county\") in the boundary data used by Update location "
                    "and opensak.chart.map, one row per name: `{name =}`, for "
                    "states and counties also `country`. `within` limits them "
                    "to one country or a list. With `rows` (e.g. finds per "
                    "state from opensak.stats.group) the rows are matched to "
                    "the regions like opensak.chart.map does — the field `key` "
                    "(default \"key\") names the region, ignoring case, "
                    "accents and punctuation — and a region named by rows gets "
                    "the sum of their field `value` (default \"count\"); the "
                    "second value then lists the names that match no region. "
                    "With it a macro can say \"18 of 26 cantons\". nil if the "
                    "boundary data is not installed.",
        example='local cantons = opensak.geo.regions("state", { within = "Switzerland" })\n'
                'if cantons then print(#cantons .. " cantons") end\n'
                '-- the cantons you have found a cache in\n'
                'local all, unmatched = opensak.geo.regions("state",\n'
                '  { within = "Switzerland", rows = opensak.stats.group(finds, "state") })\n'
                'local n = 0\n'
                'for _, r in ipairs(all or {}) do if r.count then n = n + 1 end end\n'
                'print(n .. " of " .. #(all or {}) .. " cantons")',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._geo_regions(lua, *a),
        params=(
            Param("layer", "string", '"country" (default), "state" or "county".', optional=True),
            Param("options", "{within?: string|string[], rows?: table[], key?: string, "
                  "value?: string}", "See above.", optional=True),
        ),
        returns=("{name: string, country?: string}[]?, string[]?",
                 "The regions, sorted (with `rows`: and the names that match none)."),
    ),

    # -- opensak.stats --------------------------------------------------------
    ApiFunction(
        name="stats.group",
        description="Count *rows* per value of the field *by* and return one "
                    "row per group: `{key =, count =}`, sorted by key. Rows "
                    "can come from opensak.caches(), opensak.sql() or "
                    "opensak.read_csv(); rows whose *by* is nil are left "
                    "out. Options: `period` groups a date field (\"YYYY-MM-DD\" "
                    "or os.time() seconds) by \"day\", \"week\" (\"2026-W41\"), "
                    "\"month\" (\"2026-10\"), \"year\" or \"weekday\" "
                    "(\"Mon\"…\"Sun\", sorted Monday first). `count_distinct "
                    "= field` adds `distinct`, the number of different values "
                    "of that field per group; `sum = field` adds `sum`. `sort "
                    "= \"count\"` (or \"sum\") puts the largest group first. "
                    "`top = n` keeps n groups and puts the rest into one "
                    "group named `other` (default \"Other\").",
        example='local finds = {}\n'
                'for c in opensak.caches{ found = true, fields = {"found_date", "type"} } do\n'
                '  finds[#finds + 1] = c\n'
                'end\n'
                'for _, g in ipairs(opensak.stats.group(finds, "found_date",\n'
                '    { period = "year", count_distinct = "found_date" })) do\n'
                '  print(g.key, g.count .. " finds", g.distinct .. " cache days")\n'
                'end\n'
                'local types = opensak.stats.group(finds, "type", { sort = "count", top = 8 })',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._stats_group(lua, *a),
        params=(
            Param("rows", "table[]", "The rows."),
            Param("by", "string", "The field to group by."),
            Param("options", "{period?: string, count_distinct?: string, sum?: string, "
                  "sort?: string, top?: integer, other?: string}",
                  "See above.", optional=True),
        ),
        returns=("{key: any, count: integer, distinct?: integer, sum?: number}[]",
                 "One row per group."),
    ),
    ApiFunction(
        name="stats.fill",
        description="*rows* sorted by the period in field `key` (default "
                    "\"key\"), with a row added for each missing \"day\", "
                    "\"month\" (default) or \"year\" between the first and "
                    "the last row, or between `from` and `to`. Number fields "
                    "of an added row are 0. Use it before a chart or table "
                    "over time, so months without finds still take their "
                    "place.",
        example='local months = opensak.stats.fill(\n'
                '  opensak.stats.group(finds, "found_date", { period = "month" }))',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._stats_fill(lua, *a),
        params=(
            Param("rows", "table[]", "The rows, e.g. from opensak.stats.group."),
            Param("options", "{key?: string, period?: string, from?: string, to?: string}",
                  'Field with the period, its length, first and last period '
                  '(e.g. "2020-01").', optional=True),
        ),
        returns=("table[]", "New rows."),
    ),
    ApiFunction(
        name="stats.running",
        description="Copies of *rows* with the running total of *field* "
                    "(default \"count\") in *into* (default \"total\"), e.g. "
                    "the total number of finds at the end of each month.",
        example='local total = opensak.stats.running(months)   -- months[i].total\n'
                'print("finds at the end: " .. total[#total].total)',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._stats_running(lua, *a),
        params=(
            Param("rows", "table[]", "The rows, in order."),
            Param("field", "string", "The number field to add up.", optional=True),
            Param("into", "string", "The field for the total.", optional=True),
        ),
        returns=("table[]", "New rows."),
    ),
    ApiFunction(
        name="stats.pivot",
        description="Count *rows* per pair of values of two fields, e.g. "
                    "difficulty × terrain. Returns `{rows =, cols =, cells "
                    "=, max =, total =, filled =}`: `rows` and `cols` are the "
                    "values of the two fields (sorted), `cells[i][j]` the "
                    "count for rows[i] and cols[j], `filled` the number of "
                    "cells that are not 0. The options `rows` and `cols` give "
                    "the values and their order instead (other values are "
                    "left out); `sum = field` adds up that field instead of "
                    "counting. For opensak.html.matrix.",
        example='local dt = opensak.stats.pivot(finds, "difficulty", "terrain")\n'
                'print(dt.filled .. " of 81 D/T combinations")',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._stats_pivot(lua, *a),
        params=(
            Param("rows", "table[]", "The rows."),
            Param("row_field", "string", "Field for the rows of the matrix."),
            Param("col_field", "string", "Field for the columns."),
            Param("options", "{rows?: any[], cols?: any[], sum?: string}",
                  "See above.", optional=True),
        ),
        returns=("opensak.Matrix", "The matrix."),
    ),
    ApiFunction(
        name="stats.every",
        description="Every *step*-th row of *rows*, e.g. every 1000th find "
                    "(sort the rows first, see opensak.stats.sort): copies "
                    "with the row number in `n`. The first and the last row "
                    "are included unless `first = false` / `last = false`. "
                    "With `date = field`, `days` holds the days since the "
                    "previous row picked.",
        example='local milestones = opensak.stats.every(finds, 1000, { date = "found_date" })\n'
                'for _, m in ipairs(milestones) do print(m.n, m.found_date, m.code, m.days) end',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._stats_every(lua, *a),
        params=(
            Param("rows", "table[]", "The rows, in order."),
            Param("step", "integer", "Pick every step-th row."),
            Param("options", "{first?: boolean, last?: boolean, date?: string}",
                  "See above.", optional=True),
        ),
        returns=("table[]", "Copies of the picked rows."),
    ),
    ApiFunction(
        name="stats.summary",
        description="Key figures of *field* over *rows* (nil values left "
                    "out): `count`, `distinct`, `min` and `max` (also for "
                    "dates and text), and for numbers `sum`, `mean` and "
                    "`median`.",
        example='local f = opensak.stats.summary(finds, "found_date")\n'
                'print(f.count .. " finds on " .. f.distinct .. " days, first " .. f.min)\n'
                'print("mean difficulty", opensak.stats.summary(finds, "difficulty").mean)',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._stats_summary(lua, *a),
        params=(
            Param("rows", "table[]", "The rows."),
            Param("field", "string", "The field."),
        ),
        returns=("{count: integer, distinct: integer, min?: any, max?: any, sum?: number, "
                 "mean?: number, median?: number}", "The figures."),
    ),
    ApiFunction(
        name="stats.sort",
        description="A new list of *rows* sorted by the fields in turn; "
                    "\"-field\" sorts largest first. nil values come last. "
                    "Faster than table.sort with a compare function, and "
                    "stable: rows with equal fields keep their order.",
        example='finds = opensak.stats.sort(finds, "found_date", "code")\n'
                'local best = opensak.stats.sort(caches, "-favorite_points", "name")',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._stats_sort(lua, *a),
        params=(
            Param("rows", "table[]", "The rows."),
            Param("...", "string", "The fields to sort by."),
        ),
        returns=("table[]", "The same rows in the new order."),
    ),
    ApiFunction(
        name="stats.streaks",
        description="Runs of days in a date field: `days` (the number of "
                    "different days), `longest` (most days in a row with at "
                    "least one row, e.g. your longest caching streak), `gap` "
                    "(most days in a row without, between the first and the "
                    "last day) and `last` (the run that ends on the last day). "
                    "Each run is `{days =, first =, last =}`; `gap` is nil if "
                    "there is none.",
        example='local s = opensak.stats.streaks(finds, "found_date")\n'
                'print("longest streak: " .. s.longest.days .. " days, from " .. s.longest.first)',
        since=3,
        bind=lambda rt, lua: lambda *a: rt._stats_streaks(lua, *a),
        params=(
            Param("rows", "table[]", "The rows."),
            Param("field", "string", "The date field."),
        ),
        returns=("{days: integer, longest?: opensak.Run, gap?: opensak.Run, last?: opensak.Run}",
                 "The runs."),
    ),

    # -- opensak.html ---------------------------------------------------------
    ApiFunction(
        name="html.escape",
        description="*value* as text for HTML: `& < > \" '` are escaped, nil "
                    "becomes \"\", 3.0 becomes \"3\".",
        example='local cell = "<td>" .. opensak.html.escape(c.name) .. "</td>"',
        since=3,
        bind=lambda rt, lua: rt._html_escape,
        params=(Param("value", "any", "A string, number, boolean or nil."),),
        returns=("string", "Escaped text."),
    ),
    ApiFunction(
        name="html.table",
        description="*rows* as an HTML table (values escaped), e.g. the rows "
                    "of opensak.sql() (GSAK's SqlToHtml). `columns` lists the "
                    "fields to show, each a field name or `{field, heading}`; "
                    "without it all fields are shown, sorted by name — a Lua "
                    "table has no field order, so pass `columns` to keep the "
                    "order of the query. Number columns are right-aligned. "
                    "`link = { code = \"url\" }` turns the code cells into "
                    "links to the row's url field (http/https only). `bars = "
                    "{\"count\"}` adds a bar next to each value of those "
                    "number fields, scaled to the largest (in `color`, "
                    "\"#rrggbb\"). The bars use inline styles only, so they "
                    "also show without the stylesheet of opensak.html.page.",
        example='local rows = opensak.sql(\n'
                '  "SELECT country, COUNT(*) AS n FROM caches GROUP BY country ORDER BY n DESC")\n'
                'local html = opensak.html.table(rows, {\n'
                '  columns = { {"country", "Country"}, {"n", "Caches"} }, bars = { "n" } })',
        since=3,
        bind=lambda rt, lua: rt._html_table,
        params=(
            Param("rows", "table[]", "The rows."),
            Param("options", "{columns?: (string|string[])[], link?: table<string, string>, "
                  "caption?: string, bars?: string[], color?: string}", "See above.",
                  optional=True),
        ),
        returns=("string", "HTML."),
    ),
    ApiFunction(
        name="html.matrix",
        description="A matrix (from opensak.stats.pivot) as an HTML table: "
                    "the values of the two fields as headings, each cell "
                    "shaded by its value. `percent = true` shows each cell as "
                    "a share of all, `totals = true` adds a total row and "
                    "column, `shade = false` leaves the colours out; "
                    "`row_title` and `col_title` name the fields, `color` "
                    "(\"#rrggbb\") the shading. Inline styles only, so the "
                    "shading also shows without the stylesheet of "
                    "opensak.html.page.",
        example='local dt = opensak.stats.pivot(finds, "difficulty", "terrain")\n'
                'local html = opensak.html.matrix(dt, { row_title = "D", col_title = "T", totals = true })',
        since=3,
        bind=lambda rt, lua: rt._html_matrix,
        params=(
            Param("matrix", "opensak.Matrix", "The matrix."),
            Param("options", "{shade?: boolean, percent?: boolean, totals?: boolean, "
                  "row_title?: string, col_title?: string, color?: string}",
                  "See above.", optional=True),
        ),
        returns=("string", "HTML."),
    ),
    ApiFunction(
        name="html.tiles",
        description="Key figures as a row of tiles: a list of `{label =, "
                    "value =}`.",
        example='local found = opensak.sql("SELECT COUNT(*) AS n FROM caches WHERE found = 1")[1].n\n'
                'local html = opensak.html.tiles{\n'
                '  { label = "caches", value = opensak.count() },\n'
                '  { label = "found", value = found },\n'
                '}',
        since=3,
        bind=lambda rt, lua: rt._html_tiles,
        params=(Param("items", "{label: string, value: any}[]", "The tiles."),),
        returns=("string", "HTML."),
    ),
    ApiFunction(
        name="html.columns",
        description="Put HTML parts (e.g. two tables) side by side; on a "
                    "narrow window they go one below the other.",
        example='local html = opensak.html.columns(table1, table2)',
        since=3,
        bind=lambda rt, lua: rt._html_columns,
        params=(Param("...", "string", "The HTML parts (or one list of them)."),),
        returns=("string", "HTML."),
    ),
    ApiFunction(
        name="html.page",
        description="A complete HTML page for opensak.write_text: `title` "
                    "(also the heading), `subtitle`, the body — the strings "
                    "in the list part of the table, and/or `body` — and "
                    "`footer`. A built-in stylesheet with a light and a dark "
                    "theme styles headings (`<h2>`), tables, tiles and "
                    "columns; `css` is added after it. The body is put in as "
                    "it is: build it from the html.* functions, or escape "
                    "your own text with html.escape.",
        example='local rows = opensak.sql("SELECT code, name, difficulty FROM caches LIMIT 50")\n'
                'local page = opensak.html.page{\n'
                '  title = "Caches", subtitle = opensak.database(),\n'
                '  opensak.html.tiles{ { label = "caches", value = opensak.count() } },\n'
                '  "<h2>First 50</h2>",\n'
                '  opensak.html.table(rows, { columns = { "code", "name", "difficulty" } }),\n'
                '}\n'
                'opensak.write_text(opensak.temp_dir() .. "/caches.html", page)',
        since=3,
        bind=lambda rt, lua: rt._html_page,
        params=(Param("spec", "{title?: string, subtitle?: string, css?: string, "
                      "lang?: string, footer?: string, body?: string|string[], "
                      "[integer]: string}", "See above."),),
        returns=("string", "The page."),
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
    ApiFunction(
        name="date.diff",
        description="Whole days from date *a* to date *b* (negative if *b* "
                    "is earlier). Dates are \"YYYY-MM-DD\" strings (a time "
                    "after it is ignored) or seconds since the epoch.",
        example='print(opensak.date.diff("2026-01-01", "2026-10-06"))   -- 278',
        since=3,
        bind=lambda rt, lua: rt._date_diff,
        params=(
            Param("a", "string|number", "The first date."),
            Param("b", "string|number", "The second date."),
        ),
        returns=("integer", "Days."),
    ),
    ApiFunction(
        name="date.period",
        description="The period a date falls in, named as opensak.stats.group "
                    "names it: \"day\" (\"2026-10-06\"), \"week\" "
                    "(\"2026-W41\"), \"month\" (\"2026-10\"), \"year\" "
                    "(\"2026\") or \"weekday\" (\"Tue\"). For a field of your "
                    "own to group or pivot by, e.g. the year × month table of "
                    "your finds.",
        example='for _, c in ipairs(finds) do\n'
                '  c.year = opensak.date.period(c.found_date, "year")\n'
                '  c.month = c.found_date:sub(6, 7)\n'
                'end\n'
                'local grid = opensak.stats.pivot(finds, "year", "month")',
        since=3,
        bind=lambda rt, lua: rt._date_period,
        params=(
            Param("date", "string|number", '"YYYY-MM-DD" (a time after it is ignored) '
                  "or seconds since the epoch."),
            Param("period", "string", '"day", "week", "month", "year" or "weekday".'),
        ),
        returns=("string", "The period."),
    ),

    # -- opensak.settings -----------------------------------------------------
    ApiFunction(
        name="settings.load",
        description="The settings this macro saved with opensak.settings.save(), "
                    "or an empty table on the first run. Each macro has its own "
                    "settings, kept per macro file in the `settings` folder of the "
                    "macros folder; no other macro can read them. A renamed macro "
                    "starts with empty settings, and an unsaved macro has none.",
        example='local s = opensak.settings.load()\n'
                's.maxdist = s.maxdist or 25',
        since=3,
        bind=lambda rt, lua: lambda: rt._settings_load(lua),
        returns=("table", "The saved settings; empty if there are none."),
    ),
    ApiFunction(
        name="settings.save",
        description="Save settings for the next run of this macro, replacing "
                    "the saved ones. Strings, numbers, booleans and nested tables "
                    "can be saved; a table needs string keys, or 1, 2, 3, … for a "
                    "list. Functions and cyclic tables are refused, and the "
                    f"settings may take up at most "
                    f"{macro_settings.MAX_SETTINGS_BYTES // (1024 * 1024)} MB.",
        example='local s = opensak.settings.load()\n'
                's.maxdist = 30\n'
                's.types = {"Traditional", "Multi"}\n'
                'opensak.settings.save(s)',
        since=3,
        bind=lambda rt, lua: functools.partial(
            rt._settings_save,
            # type() and a table's address, captured before the macro could
            # replace them: lupa gives no stable Python identity for tables.
            lua.eval('(function(type, fmt) return function(v) local t = type(v) '
                     'if t == "table" then return t, fmt("%p", v) end return t end '
                     'end)(type, string.format)'),
        ),
        params=(Param("settings", "table", "The settings to save."),),
    ),
    ApiFunction(
        name="settings.clear",
        description="Remove this macro's saved settings, so the next "
                    "opensak.settings.load() returns an empty table.",
        example="opensak.settings.clear()",
        since=3,
        bind=lambda rt, lua: rt._settings_clear,
    ),
)

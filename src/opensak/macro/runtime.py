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
import csv
import functools
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
from opensak.coord_formats import INPUT_KEYS, parse_any
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
    CacheWriteError,
    insert_cache,
    parse_values,
    update_cache,
)
from opensak.macro.child_write import (
    ATTRIBUTE,
    LOG,
    TRACKABLE,
    WAYPOINT,
    ChildTable,
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
    WRITABLE_TABLES,
    SqlError,
    WritableDatabase,
    connect_read_only,
)
from opensak.utils import flags
from opensak.utils.constants import ATTRIBUTES, CACHE_TYPES

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

    def approve_database_write(self, name: str, path: Path) -> WriteApproval:
        """Ask the user whether macros may change the database *name* (file
        *path*): never, until OpenSAK closes, or always.

        Asked before a macro's first write to a database that is not
        approved yet. Must be OpenSAK's own dialog. Anything but SESSION or
        ALWAYS counts as DENY.
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

    def refresh(self) -> None:
        """Apply the refreshes deferred so far and redraw the grid now."""


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
        self._run_permissions: list[FolderPermission] = []
        # (folder, write) the user denied during this run — not asked again
        self._denied: set[tuple[Path, bool]] = set()
        # (file, write) picked by the user in opensak.choose_file() — usable
        # for the rest of this run only, whatever the folder list says
        self._picked: set[tuple[Path, bool]] = set()
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
        # The active database's connection for opensak.sql*() and
        # transaction(), opened on first use and closed after the run (or on
        # switch_database), which drops the macro's TEMP tables.
        self._sql_write_db: Optional[WritableDatabase] = None
        # Database paths the user refused to let this run change
        self._write_denied: set[Path] = set()
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
    # after the user approved writing to it (db_access.py).

    def _require_write(self, func: str) -> None:
        """Raise unless the user lets macros change the active database —
        asking (host.approve_database_write) if that was not decided yet.
        A refusal holds for the rest of the run.

        Without the lua-macro-write flag only set_corrected/clear_corrected
        get here (the other writing functions are not registered); they
        change the database without asking, as before the write API."""
        if not flags.lua_macro_write:
            return
        path = Path(get_engine().url.database or "")
        if db_access.is_approved(path):
            return
        name = self._host.database_name()
        if path not in self._write_denied:
            answer = self._host.approve_database_write(name, path)
            if answer in (WriteApproval.SESSION, WriteApproval.ALWAYS):
                db_access.approve(path, answer)
                return
            self._write_denied.add(path)
        raise MacroError(f"{func}: the user did not allow macros to change database {name!r}")

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
        if isinstance(row_id, float) and row_id.is_integer():
            row_id = int(row_id)
        if isinstance(row_id, bool) or not isinstance(row_id, int):
            raise MacroError(
                f"{func} expects the {table.singular}'s id from opensak.{table.plural}(), got {row_id!r}"
            )
        parsed = self._child_values(func, table, values, insert=False)
        self._require_write(func)
        refresh = not self._in_transaction()
        code = self._write_child(
            func, lambda s: update_child(s, table, row_id, parsed, refresh=refresh))
        if code is None:
            return False
        self._host.caches_changed([code], False)
        return True

    def _writable_db(self) -> WritableDatabase:
        if self._sql_write_db is None:
            db = WritableDatabase(Path(get_engine().url.database or ""))
            if self._macro_db_path is not None:
                # Attached again after opensak.switch_database().
                try:
                    db.attach_macro_db(self._macro_db_path)
                except SqlError as exc:
                    raise MacroError(str(exc)) from None
            self._sql_write_db = db
        return self._sql_write_db

    # The macro's own database: <macro file name>.db3 next to the macro file,
    # attached to the active database's SQL connection as `macro`.

    def _macro_db(self) -> str:
        func = "opensak.macro_db"
        if self._base_dir is None:
            raise MacroError(f"{func}: save the macro first — its database is named after the file")
        path = (self._base_dir / f"{Path(self._chunk_name).stem}.db3").resolve()
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
            self._writable_db().attach_macro_db(path)
        except SqlError as exc:
            raise MacroError(f"{func}: {exc}") from None
        if created:
            # Shown in the macro output, so a new file never appears silently.
            self._output(f"{func}: created {path}")
        self._macro_db_path = path
        return str(path)

    def _sql_write(self, query=None, params=None) -> int:
        query, params = _sql_args("opensak.sql_write", query, params)
        self._require_write("opensak.sql_write")
        try:
            result = self._writable_db().execute(query, params)
        except SqlError as exc:
            raise MacroError(str(exc)) from None
        if result.codes:
            self._host.caches_changed(result.codes, result.added)
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

    def _tx_end(self, commit=None) -> None:
        try:
            self._writable_db().end(bool(commit))
        except SqlError as exc:
            raise MacroError(f"opensak.transaction: {exc}") from None

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
            with self._sql_write_db.session() as session:
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
        try:
            db = get_db_manager().new_database(name)
        except ValueError as exc:
            raise MacroError(f"opensak.create_database: {exc}") from None
        self._host.database_list_changed()
        return db.name

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
        self._base_dir, self._macro_path = file.parent, file
        self._macro_depth += 1
        try:
            return fn(*args)
        finally:
            self._macro_depth -= 1
            self._base_dir, self._macro_path = previous

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
            )
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
        self._run_permissions = (
            list(self._folder_permissions)
            if self._folder_permissions is not None
            else load_permissions()
        )
        self._denied = set()
        self._write_denied = set()
        self._picked = set()
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
            if func.write and not flags.lua_macro_write:
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
            self._close_sql_write()
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


# (list, insert, update) examples of the functions _child_api() makes
_CHILD_EXAMPLES: dict[str, tuple[str, str, str]] = {
    "log": (
        'for _, l in ipairs(opensak.logs("GC12345") or {}) do\n'
        '    print(l.date, l.type, l.finder)\nend',
        'local id = opensak.insert_log("GC12345", { type = "Write note", date = "2026-10-11",\n'
        '                                          finder = "me", text = "Final solved" })',
        'for _, l in ipairs(opensak.logs("GC12345")) do\n'
        '    if l.type == "Write note" then opensak.update_log(l.id, { text = "checked" }) end\nend',
    ),
    "waypoint": (
        'for _, w in ipairs(opensak.waypoints("GC12345") or {}) do\n'
        '    print(w.prefix, w.type, w.lat, w.lon)\nend',
        'opensak.insert_waypoint("GC12345", { prefix = "FN", type = "Final Location",\n'
        '                                     name = "Final", lat = 47.36872, lon = 8.54093 })',
        'for _, w in ipairs(opensak.waypoints("GC12345")) do\n'
        '    if w.prefix == "PK" then opensak.update_waypoint(w.id, { comment = "free parking" }) end\nend',
    ),
    "attribute": (
        'for _, a in ipairs(opensak.attributes("GC12345") or {}) do\n'
        '    print(a.attribute_id, a.name, a.on)\nend',
        'opensak.insert_attribute("GC12345", { attribute_id = 1, name = "Dogs", on = false })',
        'for _, a in ipairs(opensak.attributes("GC12345")) do\n'
        '    if a.attribute_id == 1 then opensak.update_attribute(a.id, { on = true }) end\nend',
    ),
    "trackable": (
        'for _, t in ipairs(opensak.trackables("GC12345") or {}) do print(t.ref, t.name) end',
        'opensak.insert_trackable("GC12345", { ref = "TB1234", name = "My travel bug" })',
        'for _, t in ipairs(opensak.trackables("GC12345")) do\n'
        '    if t.ref == "TB1234" then opensak.update_trackable(t.id, { name = "Renamed" }) end\nend',
    ),
}


def _child_api(t: ChildTable) -> tuple[ApiFunction, ...]:
    """opensak.<plural>(), insert_<singular>() and update_<singular>()."""
    list_ex, insert_ex, update_ex = _CHILD_EXAMPLES[t.singular]
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
        changed=((3, "lists the macro's TEMP tables and views, and the "
                     "tables of opensak.macro_db() as `macro.<name>`"),),
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
                    "to it, and prints its path in the macro output. If the "
                    "folder cannot be written the call fails; a read-only "
                    "file can be read but not changed. Unlike opensak.update() "
                    "it does not ask about the active database. Changes inside opensak.transaction() "
                    "are kept or undone together with those of the active "
                    "database. The SQL statement ATTACH itself stays refused. "
                    "Cannot be called inside opensak.transaction(); stays "
                    "attached after opensak.switch_database(). The macro must "
                    "be saved, as the file is named after it.",
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
        name="update",
        description="Change fields of a cache in the active database. The "
                    "keys are cache field names (see Changing caches); only "
                    "the given fields change. `false` clears a field that is "
                    "not a boolean. Fields OpenSAK maintains itself, such as "
                    "`code`, `distance` or `log_count`, cannot be written. "
                    "Before the first change to a database, OpenSAK asks the "
                    "user to allow it (see Changing caches).",
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
    *_child_api(LOG),
    *_child_api(WAYPOINT),
    *_child_api(ATTRIBUTE),
    *_child_api(TRACKABLE),
    ApiFunction(
        name="sql_write",
        description="Run one INSERT or UPDATE statement (SQLite) against the "
                    "active database — the power tool for bulk work, changing "
                    "many rows with one statement. For a single cache or row, "
                    "prefer `opensak.update()`, `insert{}`, `insert_<x>()` and "
                    "`update_<x>()`, which check each field and need no "
                    "knowledge of the schema (see Changing caches). Only the cache tables can be changed ("
                    + ", ".join(f"`{t}`" for t in sorted(WRITABLE_TABLES)) +
                    "); nothing can be deleted, and an UPDATE may not set the "
                    "keys or the columns OpenSAK maintains itself (see "
                    "Changing caches). OpenSAK recalculates distances, counts "
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
                '  { "2026-10-10 14:30:00.000000", "myUsername", "TFTC", "GC12345" })',
        since=3,
        bind=lambda rt, lua: rt._sql_write,
        params=(
            Param("query", "string", "One INSERT or UPDATE statement."),
            Param("params", "table", "As for opensak.sql().", optional=True),
        ),
        returns=("integer", "Number of rows inserted or updated."),
        write=True,
    ),
    ApiFunction(
        name="transaction",
        description="Run *fn* as one transaction: every change it makes to the "
                    "active database (opensak.update, insert, sql_write, "
                    "set_corrected, clear_corrected) is kept when *fn* returns, "
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

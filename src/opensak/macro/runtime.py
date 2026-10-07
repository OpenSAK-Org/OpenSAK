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
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from opensak.filters.engine import (
    AvailableFilter,
    CacheTypeFilter,
    ContainerFilter,
    CountryFilter,
    CountyFilter,
    DifficultyFilter,
    FilterProfile,
    FilterSet,
    FoundFilter,
    GcCodeFilter,
    NameFilter,
    NotFoundFilter,
    OwnerFilter,
    StateFilter,
    TerrainFilter,
    WhereClauseFilter,
    apply_filters_auto,
)
from opensak import __version__, geodesy
from opensak.coords import parse_coords
from opensak.db.database import get_engine
from opensak.export.file_export import select_for_export, write_export_file
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
from opensak.macro.sql import MAX_ROWS, QUERY_TIMEOUT_S, ReadOnlyDatabase, SqlError
from opensak.utils.constants import CACHE_TYPES

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

_TEXT_FILTERS = {
    "name": NameFilter,
    "code": GcCodeFilter,
    "owner": OwnerFilter,
    "country": CountryFilter,
    "state": StateFilter,
    "county": CountyFilter,
}
FILTER_KEYS = sorted(
    {
        "type",
        "container",
        "difficulty",
        "terrain",
        "found",
        "available",
        "where",
        "label",
    }
    | set(_TEXT_FILTERS)
)


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
    FilterKeyDoc(tuple(_TEXT_FILTERS), "string", '"text"',
                 '"Contains" match on that field.'),
    FilterKeyDoc(("where",), "string", '"SQL WHERE clause"',
                 "Raw clause against the caches table."),
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
_SANDBOX_SETUP = """
local limit = ...
local sethook, co_create, co_resume = debug.sethook, coroutine.create, coroutine.resume
local pack, unpack = table.pack, table.unpack
local main = coroutine.running()
local step = math.min(limit, 1000)
local used, tripped = 0, false
local function hook()
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
  sethook(co, hook, "", tripped and 1 or step)
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

    def database_name(self) -> str:
        """Name of the active database — the {database} variable."""

    def center_name(self) -> str:
        """Name of the active centre point ("" = none) — the {center}
        variable."""

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


def build_filterset(spec: dict) -> tuple[FilterSet, str]:
    """Translate the table passed to opensak.filter{} into a FilterSet.

    Returns (filterset, label). Raises MacroError for unknown keys or values.
    """
    unknown = set(spec) - set(FILTER_KEYS)
    if unknown:
        raise MacroError(
            f"unknown filter key(s) {sorted(unknown)}; valid keys: {', '.join(FILTER_KEYS)}"
        )

    fs = FilterSet(mode="AND")
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
    for key, cls in _TEXT_FILTERS.items():
        if key in spec:
            fs.add(cls(str(spec[key])))
    if "where" in spec:
        fs.add(WhereClauseFilter(str(spec["where"])))

    if len(fs) == 0:
        raise MacroError("opensak.filter{} needs at least one criterion")
    return fs, str(spec.get("label") or "Macro")


# ── Corrected coordinates / CSV ──────────────────────────────────────────────


def _gc_code(code: Any, func: str) -> str:
    if not isinstance(code, str) or not code.strip():
        raise MacroError(f"{func} expects a GC code as first argument, got {code!r}")
    return code.strip().upper()


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
        profiles_dir: Optional[Path] = None,
        export_settings_dir: Optional[Path] = None,
        instruction_limit: int = DEFAULT_INSTRUCTION_LIMIT,
        memory_limit: int = DEFAULT_MEMORY_LIMIT,
        folder_permissions: Optional[list[FolderPermission]] = None,
    ):
        """*folder_permissions* limits which folders file functions may
        touch; None means the list saved in Settings, read at every run so
        changes apply without reopening the macro window. Folders the user
        approves during a run are added to a copy of the list for that run."""
        self._host = host
        self._output = output or print
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
        self._slept = 0.0
        self._polygons: dict[Path, LineShape] = {}
        self._boundaries: Optional[tuple[Any, Any]] = None  # (store, resolver)
        # Opened by the first opensak.sql*() call of a run, closed after it.
        self._sql_db: Optional[ReadOnlyDatabase] = None

    def cancel(self) -> None:
        """Ask the running macro to stop. Safe to call from another thread;
        takes effect at the next opensak.sleep()."""
        self._cancelled.set()

    # -- API functions exposed to Lua -----------------------------------------

    def _filter(self, spec=None) -> int:
        if spec is None or not hasattr(spec, "items"):
            raise MacroError(
                "opensak.filter expects a table, e.g. opensak.filter{ found = false }"
            )
        fs, label = build_filterset(dict(spec.items()))
        return self._host.apply_filter(fs, label)

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

    def _sql(self) -> ReadOnlyDatabase:
        if self._sql_db is None:
            self._sql_db = ReadOnlyDatabase(Path(get_engine().url.database or ""))
        return self._sql_db

    def _query(self, lua, query=None, params=None):
        query, params = _sql_args("opensak.sql", query, params)
        try:
            rows = self._sql().query(query, params)
        except SqlError as exc:
            raise MacroError(str(exc)) from None
        return lua.table_from([lua.table_from(r) for r in rows])

    def _query_each(self, lua, query=None, params=None):
        query, params = _sql_args("opensak.sql_each", query, params)
        rows = self._sql().iterate(query, params)

        def step(*_):
            try:
                row = next(rows, None)
            except SqlError as exc:
                raise MacroError(str(exc)) from None
            return None if row is None else lua.table_from(row)

        # The query runs on the first step, so errors surface in the loop.
        return self._wrap(step)

    def _tables(self, lua):
        try:
            return lua.table_from(self._sql().tables())
        except SqlError as exc:
            raise MacroError(str(exc)) from None

    def _columns(self, lua, table=None):
        if not isinstance(table, str) or not table.strip():
            raise MacroError("opensak.columns expects a table name")
        try:
            columns = self._sql().columns(table)
        except SqlError as exc:
            raise MacroError(str(exc)) from None
        return lua.table_from([lua.table_from(c) for c in columns])

    # Cache access: plain-table snapshots (cache_data.py), straight from the
    # database, so they are current even right after a write.

    def _active_codes(self) -> list[str]:
        return [c.gc_code for c in self._host.filtered_caches()]

    def _cache(self, lua, code=None):
        gc_code = _gc_code(code, "opensak.cache")
        with get_session() as session:
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
        if spec:
            filterset, _label = build_filterset(spec)
            with get_session() as session:
                codes = [c.gc_code for c in apply_filters_auto(session, filterset)]
        else:
            codes = self._active_codes()
        records = iter_records(get_session, codes, fields)

        def step(*_):
            record = next(records, None)
            return None if record is None else lua.table_from(record, recursive=True)

        return step

    def _current(self, lua):
        code = self._host.current_code()
        return self._cache(lua, code) if code else None

    def _description(self, lua, code=None):
        gc_code = _gc_code(code, "opensak.description")
        with get_session() as session:
            description = load_description(session, gc_code)
        return lua.table_from(description) if description else None

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

    def _polygon(self, value: Any) -> LineShape:
        func = "opensak.coords.inside"
        if isinstance(value, str):
            file = self._readable_file(value)
            if file in self._polygons:
                return self._polygons[file]
            try:
                points = read_points_file(file)
            except Exception as exc:  # OSError, ParseError, ValueError
                raise MacroError(f"{func}: cannot read {file}: {exc}") from None
        elif hasattr(value, "values"):
            file = None
            points = polygon_points(value)
        else:
            raise MacroError(f"{func} expects a polygon file path or a table of points")
        if len(points) < 3:
            raise MacroError(f"{func}: a polygon needs at least 3 points, got {len(points)}")
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

    # -- Running ---------------------------------------------------------------

    def run(
        self, source: str, chunk_name: str = "macro", base_dir: Optional[Path] = None
    ) -> None:
        """Execute *source*. Raises MacroError on any failure.

        *base_dir* (usually the macro file's folder) is where relative paths
        given to opensak.read_csv() are looked up; the macros folder
        otherwise.
        """
        self._base_dir = base_dir
        self._run_permissions = (
            list(self._folder_permissions)
            if self._folder_permissions is not None
            else load_permissions()
        )
        self._denied = set()
        self._picked = set()
        self._cancelled.clear()
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
        lua.execute(_SANDBOX_SETUP, self._instruction_limit)

        g = lua.globals()
        g.print = self._lua_print
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
        except MacroError:
            raise
        except LuaMemoryError as exc:
            raise MacroError(
                f"macro aborted: memory limit reached ({self._memory_limit // (1024 * 1024)} MB)"
            ) from exc
        except LuaError as exc:
            raise MacroError(str(exc)) from exc
        except Exception as exc:
            # A Python exception raised inside a callback (e.g. the
            # attribute_filter) propagates as itself, not as a LuaError.
            raise MacroError(f"{type(exc).__name__}: {exc}") from exc
        finally:
            if self._boundaries is not None:
                self._boundaries[0].close()
                self._boundaries = None
            if self._sql_db is not None:
                self._sql_db.close()
                self._sql_db = None
            self._host.end_macro()

    @staticmethod
    def _deny_attribute(obj, attr_name, is_setting):
        raise AttributeError("access to Python objects is not allowed in macros")

    @staticmethod
    def _wrap(func: Callable) -> Callable:
        """Turn MacroError into a clean Lua error message (no Python traceback)."""
        from lupa.lua54 import LuaError

        def call(*args):
            try:
                return func(*args)
            except MacroError as exc:
                raise LuaError(str(exc)) from None

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
API_VERSION = 2


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
        description="Build a filter from the given keys (see Filter keys; all "
                    "combined with AND) and apply it. Usually called with "
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
                'if c and c.corrected then print(c.name, c.corrected.lat, c.corrected.lon) end',
        since=2,
        bind=lambda rt, lua: lambda *a: rt._cache(lua, *a),
        params=(_CODE,),
        returns=("opensak.Cache?", "The cache, or nil if it is not in the database."),
    ),
    ApiFunction(
        name="caches",
        description="Iterate over caches, one table per cache (see Cache "
                    "fields), in a generic `for`. Without arguments: the "
                    "caches of the active filter, in grid order. With filter "
                    "keys (see Filter keys): the caches matching them, sorted "
                    "by name; the view and the active filter stay "
                    "unchanged. `fields` limits the fields loaded (`code` is "
                    "always included), which makes loops over many caches "
                    "faster. Caches are loaded in chunks, so large databases "
                    "do not hit the memory limit.",
        example="for c in opensak.caches() do print(c.code, c.name) end\n"
                'for c in opensak.caches{ found = true, country = "Switzerland",\n'
                '                         fields = {"difficulty", "terrain"} } do\n'
                "    print(c.code, c.difficulty, c.terrain)\nend",
        since=2,
        bind=lambda rt, lua: lambda *a: rt._caches(lua, *a),
        params=(Param("spec", "opensak.CachesSpec",
                      "Filter keys and/or `fields`; nothing = the active filter.",
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
        params=(_CODE,),
        returns=("{short: string?, long: string?, html: boolean}?",
                 "Short and long description and whether they are HTML; "
                 "nil if the cache is not in the database."),
    ),
    ApiFunction(
        name="sql",
        description="Run a read-only SQL query (SQLite) against the active "
                    "database and return all rows. Only reading statements "
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
                  "placeholders ({ name = v }).",
                  optional=True),
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
        ),
        returns=("fun(): table<string, any>?", "Iterator for a generic `for`."),
    ),
    ApiFunction(
        name="tables",
        description="The tables and views of the active database, for use "
                    "with opensak.sql().",
        example='print(table.concat(opensak.tables(), ", "))',
        since=2,
        bind=lambda rt, lua: lambda: rt._tables(lua),
        returns=("string[]", "Table and view names, sorted."),
    ),
    ApiFunction(
        name="columns",
        description="The columns of a table or view of the active database.",
        example='for _, c in ipairs(opensak.columns("caches")) do print(c.name, c.type) end',
        since=2,
        bind=lambda rt, lua: lambda *a: rt._columns(lua, *a),
        params=(Param("table", "string", "Table or view name."),),
        returns=("{name: string, type: string}[]", "Column names and SQL types, in table order."),
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

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
  * Planned regex functions should use a linear-time engine (google-re2) or
    the `regex` module with its timeout= argument, not Python's `re`.
  * A worker thread keeps the GUI responsive and lets a Cancel button
    abandon the run, but cannot stop a call that is already running; only a
    subprocess can be terminated hard. This belongs to the threading decision.

"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
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
)
from opensak.coords import parse_coords
from opensak.macro.permissions import (
    FolderAccessDenied,
    FolderPermission,
    check_access,
    macros_dir,
    temp_dir,
)
from opensak.utils.constants import CACHE_TYPES

# A runaway `while true do end` would freeze the GUI thread, so the script is
# aborted after this many Lua VM instructions.
DEFAULT_INSTRUCTION_LIMIT = 50_000_000
# Upper bound for the Lua heap, so e.g. string.rep("x", 1e10) cannot exhaust RAM.
DEFAULT_MEMORY_LIMIT = 256 * 1024 * 1024
# opensak.read_csv() refuses larger files — it is meant for small lists
# (solved puzzles, corrections), not for bulk imports.
MAX_CSV_BYTES = 10 * 1024 * 1024

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


class MacroError(Exception):
    """A macro failed — Lua syntax/runtime error or a bad API call."""


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

    def confirm(self, message: str) -> bool:
        """Ask the user a Yes/No question; True on Yes."""

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
        instruction_limit: int = DEFAULT_INSTRUCTION_LIMIT,
        memory_limit: int = DEFAULT_MEMORY_LIMIT,
        folder_permissions: Optional[list[FolderPermission]] = None,
    ):
        """*folder_permissions* limits which folders file functions may
        touch; None means the list saved in Settings, read at every run so
        changes apply without reopening the macro window."""
        self._host = host
        self._output = output or print
        self._profiles_dir = profiles_dir
        self._instruction_limit = instruction_limit
        self._memory_limit = memory_limit
        self._folder_permissions = folder_permissions
        self._base_dir: Optional[Path] = None

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

    def _confirm(self, message=None) -> bool:
        if not isinstance(message, str) or not message.strip():
            raise MacroError("opensak.confirm expects a message")
        return bool(self._host.confirm(message))

    def _read_csv(self, lua, path=None, sep=None):
        if not isinstance(path, str) or not path.strip():
            raise MacroError("opensak.read_csv expects a file path")
        if sep is not None and not isinstance(sep, str):
            raise MacroError("opensak.read_csv: separator must be a string")
        file = Path(path).expanduser()
        if not file.is_absolute():
            file = (self._base_dir or macros_dir()) / file
        try:
            file = check_access(file, write=False, permissions=self._folder_permissions)
        except FolderAccessDenied as exc:
            raise MacroError(str(exc)) from None
        rows = read_csv_rows(file, sep)
        return lua.table_from([lua.table_from(r) for r in rows])

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
        g.opensak = lua.table_from(
            {func.name: self._wrap(func.bind(self, lua)) for func in API}
        )

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
        self._output("\t".join(_lua_tostring(a) for a in args))


# ── Lua API registry ─────────────────────────────────────────────────────────
#
# Single source of truth for the `opensak` table. To add a function: add an
# ApiFunction here (bump API_VERSION and use it as `since` if the release
# already shipped the current version), then regenerate the docs and the
# Lua Language Server stub with
#   python scripts/generate_macro_api_docs.py

# Raised whenever functions are added or changed in a released build, so
# macros can check opensak.api_version() before using newer functions.
API_VERSION = 1


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
                    "macro file's folder. The file must lie in a folder with "
                    "read permission (Settings → Folder permissions) and may "
                    f"be at most {MAX_CSV_BYTES // (1024 * 1024)} MB.",
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
        name="confirm",
        description="Ask the user a Yes/No question.",
        example='if not opensak.confirm("Update 12 caches?") then return end',
        since=1,
        bind=lambda rt, lua: rt._confirm,
        params=(Param("message", "string", "The question."),),
        returns=("boolean", "true on Yes."),
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
)


def _lua_tostring(value: Any) -> str:
    if value is None:
        return "nil"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)

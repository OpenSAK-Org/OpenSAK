"""
src/opensak/macro/runtime.py — Lua macro runtime (proof of concept).

Runs a user's Lua script in a sandboxed interpreter and exposes a small
`opensak` API table to it. The runtime is kept free of Qt: everything that
touches the main window goes through a MacroHost, so the runtime can be
unit-tested with a fake host.

Lua API (POC):

    opensak.filter{ ... }            -- build a filter and apply it; returns
                                     -- the number of matching caches
                                     -- (0 = nothing matched, view unchanged)
    opensak.filter_profile("Name")   -- apply a saved filter profile; returns count
    opensak.clear_filter()           -- show all caches again
    opensak.count()                  -- caches currently shown in the list
    opensak.profiles()               -- list of saved filter profile names
    print(...)                       -- write to the macro output pane

Keys understood by opensak.filter{} (all combined with AND):

    type       = "Traditional" | {"Traditional", "Multi-cache", ...}
    container  = "Small" | {"Micro", "Small", ...}
    difficulty = 2 | {1, 2.5}         -- exact value or {min, max}
    terrain    = 2 | {1, 2.5}
    found      = true | false
    available  = true                 -- only available (not disabled/archived)
    name, code, owner, country, state, county = "text"   -- "contains" match
    where      = "SQL WHERE clause"   -- raw clause against the caches table
    label      = "Shown in the toolbar" (optional)

Example:

    local n = opensak.filter{ type = "Traditional", difficulty = {1, 2}, found = false }
    print("Easy unfound traditionals: " .. n)
"""

from __future__ import annotations

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
from opensak.utils.constants import CACHE_TYPES

# A runaway `while true do end` would freeze the GUI thread, so the script is
# aborted after this many Lua VM instructions.
DEFAULT_INSTRUCTION_LIMIT = 50_000_000

_TEXT_FILTERS = {
    "name": NameFilter,
    "code": GcCodeFilter,
    "owner": OwnerFilter,
    "country": CountryFilter,
    "state": StateFilter,
    "county": CountyFilter,
}
FILTER_KEYS = sorted(
    {"type", "container", "difficulty", "terrain", "found", "available", "where", "label"}
    | set(_TEXT_FILTERS)
)

# Globals removed from the sandbox: file/process access, loading other code,
# and the lupa bridge back into Python.
_SANDBOX_SETUP = """
local limit = ...
debug.sethook(function() error("macro aborted: instruction limit reached", 2) end, "", limit)
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
        """Number of caches currently shown."""


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
        fs.add(CacheTypeFilter([_resolve_cache_type(t) for t in _as_list(spec["type"])]))
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
    ):
        self._host = host
        self._output = output or print
        self._profiles_dir = profiles_dir
        self._instruction_limit = instruction_limit

    # -- API functions exposed to Lua -----------------------------------------

    def _filter(self, spec=None) -> int:
        if spec is None or not hasattr(spec, "items"):
            raise MacroError("opensak.filter expects a table, e.g. opensak.filter{ found = false }")
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

    # -- Running ---------------------------------------------------------------

    def run(self, source: str, chunk_name: str = "macro") -> None:
        """Execute *source*. Raises MacroError on any failure."""
        try:
            from lupa.lua54 import LuaError, LuaRuntime
        except ImportError as exc:
            raise MacroError(
                "Lua support is not installed — run: pip install \"lupa>=2.0,<3\""
            ) from exc

        lua = LuaRuntime(
            register_eval=False,
            register_builtins=False,
            unpack_returned_tuples=True,
            # No attribute access on Python objects from Lua at all: the
            # script only gets the plain functions in the opensak table.
            attribute_filter=self._deny_attribute,
        )
        lua.execute(_SANDBOX_SETUP, self._instruction_limit)

        g = lua.globals()
        g.print = self._lua_print
        g.opensak = lua.table_from({
            "filter": self._wrap(self._filter),
            "filter_profile": self._wrap(self._filter_profile),
            "clear_filter": self._wrap(self._host.clear_filter),
            "count": self._wrap(self._host.cache_count),
            "profiles": self._wrap(lambda: lua.table_from(self._profile_names())),
        })

        try:
            fn = lua.compile(source, name=f"={chunk_name}")
            fn()
        except MacroError:
            raise
        except LuaError as exc:
            raise MacroError(str(exc)) from exc
        except Exception as exc:
            # A Python exception raised inside a callback (e.g. the
            # attribute_filter) propagates as itself, not as a LuaError.
            raise MacroError(f"{type(exc).__name__}: {exc}") from exc

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

"""
src/opensak/macro/api_baseline.py — the macro API as released, to catch
breaking changes (#1185).

From API 4 (v1.22.0) the macro API only grows within an API version.
Renaming or removing a function, parameter, cache field or header key needs
a new API version and a deprecation period (deprecation.py). snapshot()
records what the API has — each entry with the API version it was
deprecated in, and the feature flag it is only available with — and
check() compares the API with such a snapshot taken at the last stable
release (BASELINE_PATH, written by scripts/update_macro_api_baseline.py).

check() accepts:
  * additions, and marking something as deprecated;
  * removing something deprecated in an API version that has been in a
    stable release, once a later stable release has come out too
    (removal_allowed(), runtime.API_STABLE) — a function then has to be in
    runtime.REMOVED, for the clear error;
  * removing or changing anything only available with a feature flag
    (beta builds).
Anything else that is in the baseline and gone now is reported, renames
included (a rename is a removal plus an addition).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

BASELINE_PATH = Path("tests/fixtures/macro_api_baseline.json")


def _entry(deprecated: Optional[tuple[int, str]], flag: Optional[str] = None) -> dict[str, Any]:
    """{"deprecated": API version, "flag": feature flag}, each left out
    when there is none."""
    entry: dict[str, Any] = {}
    if deprecated:
        entry["deprecated"] = deprecated[0]
    if flag:
        entry["flag"] = flag
    return entry


def snapshot() -> dict[str, Any]:
    """The macro API as plain data (JSON-ready, sorted)."""
    from opensak.macro.cache_data import CACHE_FIELDS, CUSTOM_FIELD
    from opensak.macro.metadata import HEADER_KEYS
    from opensak.macro.runtime import API, API_VERSION

    functions: dict[str, Any] = {}
    for func in API:
        entry = _entry(func.deprecated, func.flag)
        entry["params"] = {p.name: _entry(p.deprecated)
                           for form in (func.params, *func.overloads) for p in form}
        functions[func.name] = entry
    fields = {f.name: _entry(f.deprecated) for f in CACHE_FIELDS}
    # Its values are read from the companion database (companion-db flag)
    fields[CUSTOM_FIELD.name] = _entry(CUSTOM_FIELD.deprecated, "companion-db")
    return {
        "api_version": API_VERSION,
        "functions": functions,
        "cache_fields": fields,
        "header_keys": {k.name: _entry(k.deprecated) for k in HEADER_KEYS},
    }


def to_json(data: dict[str, Any]) -> str:
    """*data* as the baseline file's text (deterministic)."""
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _release(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def removal_allowed(deprecated: Optional[int],
                    stable: Optional[dict[int, str]] = None) -> bool:
    """Whether something deprecated in API version *deprecated* (None: not
    deprecated) may be removed: that version has been in a stable release
    and a later stable release has come out since. *stable* is
    runtime.API_STABLE by default."""
    if stable is None:
        from opensak.macro import runtime
        stable = runtime.API_STABLE
    if deprecated is None or deprecated not in stable:
        return False
    released = _release(stable[deprecated])
    return any(_release(r) > released for r in stable.values())


def check(baseline: dict[str, Any], current: Optional[dict[str, Any]] = None,
          stable: Optional[dict[int, str]] = None,
          removed: Optional[set[str]] = None) -> list[str]:
    """What *current* (snapshot() by default) breaks of *baseline*, one
    message each; empty if nothing. *stable* is runtime.API_STABLE and
    *removed* the names in runtime.REMOVED by default."""
    if current is None:
        current = snapshot()
    if removed is None:
        from opensak.macro.runtime import REMOVED
        removed = {r.name for r in REMOVED}
    problems: list[str] = []

    def gone(what: str, old: dict[str, Any], new: Optional[dict[str, Any]]) -> bool:
        """Report *what* if it was in a stable build and is no longer
        (removed, or now only available with a feature flag), unless that
        is allowed. True if it is no longer there."""
        if old.get("flag"):
            return False                # beta-only in the baseline
        if new is not None and not new.get("flag"):
            return False
        if not removal_allowed(old.get("deprecated"), stable):
            how = (f"it was deprecated in API {old['deprecated']}, which is not "
                   "followed by another stable release yet" if old.get("deprecated")
                   else "it was not deprecated first")
            problems.append(f"{what} was removed or renamed, but {how}")
        return True

    for name, old in baseline.get("functions", {}).items():
        new = current["functions"].get(name)
        if gone(f"opensak.{name}", old, new):
            if (new is None and name not in removed
                    and removal_allowed(old.get("deprecated"), stable)):
                problems.append(f"opensak.{name} was removed: add it to runtime.REMOVED")
            continue
        if old.get("flag") or new is None:
            continue
        for param, old_param in old.get("params", {}).items():
            gone(f"opensak.{name} parameter {param}", old_param,
                 new["params"].get(param))
    for kind, label in (("cache_fields", "cache field"), ("header_keys", "header key")):
        for name, old in baseline.get(kind, {}).items():
            gone(f"{label} {name}", old, current[kind].get(name))
    return problems


def load_baseline(root: Path = Path(".")) -> dict[str, Any]:
    """The committed baseline."""
    return json.loads((root / BASELINE_PATH).read_text(encoding="utf-8"))

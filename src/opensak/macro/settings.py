"""
src/opensak/macro/settings.py — per-macro saved settings (#983).

opensak.settings.load()/save()/clear() give each macro a private JSON file,
so its user options survive to the next run (GSAK's MacSettings). The files
live in the settings/ folder of the macros folder, keyed by the macro file:
<macros>/foo.lua keeps its settings in <macros>/settings/foo.json, and
<macros>/examples/foo.lua in <macros>/settings/examples/foo.json. A macro
outside the macros folder is keyed by its file name alone. A renamed macro
starts with empty settings.

A macro can only reach its own file, and only through this API: the file
functions refuse the settings folder (permissions.protected_reason), even
though the rest of the macros folder is readable by default. An unsaved
macro has no settings.

Values are what maps cleanly to JSON: strings, numbers, booleans and nested
tables. A table is stored as a list when its keys are exactly 1..n, as an
object when all keys are strings; anything else is rejected with an error
naming the offending key path.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable, Optional

from opensak.macro import helpers
from opensak.macro.errors import MacroError

# The folder inside the macros folder.
SETTINGS_DIRNAME = "settings"
# Upper bound for one macro's settings file, so a macro cannot fill the disk.
MAX_SETTINGS_BYTES = 1024 * 1024
# Deeper nesting is almost certainly a mistake (and stops runaway recursion).
MAX_DEPTH = 100


def default_settings_dir() -> Path:
    """<macros folder>/settings — not created here."""
    from opensak.macro.permissions import macros_dir

    return macros_dir() / SETTINGS_DIRNAME


def settings_file(folder: Path, macro: Path, macros: Optional[Path] = None) -> Path:
    """The settings file in *folder* of the macro file *macro*. Inside the
    *macros* folder its subfolders are kept, so examples/foo.lua and foo.lua
    do not share settings."""
    macro = Path(macro)
    parts: tuple[str, ...] = ()
    if macros is not None:
        try:
            parts = macro.resolve().relative_to(Path(macros).resolve()).parent.parts
        except (ValueError, OSError):
            parts = ()
    names = [helpers.normalize_name(p) for p in parts]
    return folder.joinpath(*names, f"{helpers.normalize_name(macro.stem or macro.name)}.json")


def _dumps(data: Any) -> str:
    # The same format settings_store._atomic_write() writes.
    return json.dumps(data, indent=2, ensure_ascii=False)


def to_json(value: Any, describe: Callable[[Any], Any], func: str) -> Any:
    """Convert the Lua *value* to JSON-compatible Python data.

    *describe* is a Lua function returning type(v) and, for a table, its
    address. The address detects cyclic tables: lupa wraps the same table
    in a new Python object on every access, so Python identity cannot be
    used.
    """

    def convert(v: Any, path: str, active: set[str]) -> Any:
        where = f" at {path}" if path else ""
        if isinstance(v, (bool, str, int)):
            return v
        if isinstance(v, float):
            if not math.isfinite(v):
                raise MacroError(f"{func}: cannot save {v}{where} — only finite numbers")
            return v
        if isinstance(v, bytes):
            raise MacroError(f"{func}: the text{where} is not valid UTF-8")
        described = describe(v)
        kind, key = described if isinstance(described, tuple) else (described, None)
        if kind != "table":
            # The opensak functions are Python callables, userdata to Lua.
            shown = "function" if kind == "userdata" and callable(v) else kind
            raise MacroError(f"{func}: cannot save a {shown}{where}")
        if len(active) >= MAX_DEPTH:
            raise MacroError(f"{func}: tables nested more than {MAX_DEPTH} levels deep{where}")
        if key in active:
            raise MacroError(f"{func}: cannot save a cyclic table{where}")
        active.add(key)
        try:
            items = list(v.items())
            keys = [k for k, _ in items]
            if items and all(isinstance(k, int) and not isinstance(k, bool) for k in keys):
                if sorted(keys) != list(range(1, len(keys) + 1)):
                    raise MacroError(
                        f"{func}: cannot save a list with gaps{where} — its keys "
                        "must be 1, 2, 3, …"
                    )
                by_index = dict(items)
                return [convert(by_index[i], f"{path}[{i}]", active)
                        for i in range(1, len(keys) + 1)]
            result = {}
            for k, item in items:
                if not isinstance(k, str):
                    shown = helpers.lua_tostring(k)
                    raise MacroError(
                        f"{func}: cannot save the key {shown}{where} — use string "
                        "keys, or 1, 2, 3, … for a list"
                    )
                result[k] = convert(item, f"{path}.{k}" if path else k, active)
            return result
        finally:
            active.discard(key)

    return convert(value, "", set())


def load_settings(path: Path, func: str) -> Any:
    """The saved settings, or {} when there are none yet."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise MacroError(f"{func}: cannot read {path}: {exc}") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise MacroError(
            f"{func}: the saved settings in {path} are damaged — "
            "opensak.settings.clear() removes them"
        ) from None
    if not isinstance(data, (dict, list)):
        raise MacroError(f"{func}: the saved settings in {path} are not a table")
    return data


def save_settings(path: Path, data: Any, func: str) -> None:
    """Write *data* atomically (temp file + rename)."""
    from opensak.settings_store import _atomic_write

    size = len(_dumps(data).encode("utf-8"))
    if size > MAX_SETTINGS_BYTES:
        raise MacroError(
            f"{func}: the settings are too large ({size // 1024} KB, at most "
            f"{MAX_SETTINGS_BYTES // 1024} KB)"
        )
    try:
        _atomic_write(path, data)
    except OSError as exc:
        raise MacroError(f"{func}: cannot write {path}: {exc}") from None


def clear_settings(path: Path, func: str) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise MacroError(f"{func}: cannot remove {path}: {exc}") from None

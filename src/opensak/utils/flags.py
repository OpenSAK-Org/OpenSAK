"""
src/opensak/utils/flags.py — Feature flags for in-development functionality.

Flags are read from *features.json* at the project root.  That file is never
included in PyInstaller bundles, so release builds fall back to the built-in
_RELEASE_DEFAULTS below.  Developers edit features.json locally to override them.

CLI overrides (highest priority, useful for one-off testing):

    python run.py --feature reverse-geocoding=true
    python run.py --feature reverse-geocoding=true --feature other-flag=false

Usage::

    from opensak.utils import flags

    if flags.reverse_geocoding:
        ...  # new feature path
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from opensak import __version__

# src/opensak/utils/ → src/opensak/ → src/ → project root
_FEATURES_FILE: Path = Path(__file__).parent.parent.parent.parent / "features.json"


def _is_prerelease(version: str) -> bool:
    """True for semver pre-release versions such as "1.21.0-beta.1"."""
    return "-" in version


_RELEASE_DEFAULTS: dict[str, bool] = {
    "reverse-geocoding": True,
    "map-popout": True,
    # Lua macros (#938): on in beta builds only, so the POC cannot reach a
    # stable release before the API (#938 step 4) is settled.
    "lua-macros": _is_prerelease(__version__),
}


def _parse_argv() -> dict[str, bool]:
    """Extract --feature name=value overrides from sys.argv."""
    overrides: dict[str, bool] = {}
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--feature" and i + 1 < len(args):
            pair, i = args[i + 1], i + 2
        elif arg.startswith("--feature="):
            pair, i = arg[len("--feature="):], i + 1
        else:
            i += 1
            continue
        if "=" in pair:
            name, _, raw = pair.partition("=")
            if name in _RELEASE_DEFAULTS:
                overrides[name] = raw.strip().lower() not in ("0", "false", "no", "")
    return overrides


def _load() -> dict[str, bool]:
    merged = dict(_RELEASE_DEFAULTS)
    if _FEATURES_FILE.exists():
        try:
            data = json.loads(_FEATURES_FILE.read_text(encoding="utf-8"))
            merged.update({k: bool(v) for k, v in data.items() if k in _RELEASE_DEFAULTS})
        except (json.JSONDecodeError, OSError):
            pass
    merged.update(_parse_argv())
    return merged


_flags = _load()

# ── Public flag attributes ────────────────────────────────────────────────────

reverse_geocoding: bool    = _flags["reverse-geocoding"]
map_popout: bool           = _flags["map-popout"]
lua_macros: bool           = _flags["lua-macros"]

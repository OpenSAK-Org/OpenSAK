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


def _release_defaults(prerelease: bool) -> dict[str, bool]:
    """The built-in flag values of a beta (*prerelease*) or stable build."""
    return {
        "reverse-geocoding": True,
        "map-popout": True,
        # Lua macros (#938): stable since v1.22.0, macro API 4 (#1190).
        "lua-macros": True,
        # Macros changing the active database (opensak.update, insert,
        # sql_write, transaction, ...): stable since v1.22.0 (#1190).
        "lua-macro-write": True,
        # Companion database per cache database (#1034), which custom fields
        # (#1028) live in: stable since v1.22.0 (#1190).
        "companion-db": True,
        # Macros contacting web sites (opensak.get_url, #1079): beta-only for
        # one more release cycle (v1.22.0 plan, D1), so it does not hold up
        # the stable macro release.
        "lua-macro-net": prerelease,
    }


_RELEASE_DEFAULTS: dict[str, bool] = _release_defaults(_is_prerelease(__version__))

# The flags that are off in a stable release, whatever this build is: what
# they gate is beta-only (e.g. for macro.metadata.beta_only_names()).
BETA_ONLY: frozenset[str] = frozenset(
    name for name, on in _release_defaults(False).items() if not on)


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
lua_macro_write: bool      = _flags["lua-macro-write"]
companion_db: bool         = _flags["companion-db"]
lua_macro_net: bool        = _flags["lua-macro-net"]

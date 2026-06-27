"""
src/opensak/utils/flags.py — Feature flags for in-development functionality.

Flags are read from *features.json* at the project root.  That file is never
included in PyInstaller bundles, so release builds always see all flags as
False.  Developers edit features.json locally to turn features on.

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

# src/opensak/utils/ → src/opensak/ → src/ → project root
_FEATURES_FILE: Path = Path(__file__).parent.parent.parent.parent / "features.json"

_RELEASE_DEFAULTS: dict[str, bool] = {
    "update-location": False,
    "reverse-geocoding": False,
    "distance-computation": False,
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

update_location: bool      = _flags["update-location"]
reverse_geocoding: bool    = _flags["reverse-geocoding"]
distance_computation: bool = _flags["distance-computation"]

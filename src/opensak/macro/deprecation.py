"""
src/opensak/macro/deprecation.py — the texts of macro API deprecations
(#1185).

From API 4 (v1.22.0) the macro API only grows within an API version.
Renaming or removing a function, parameter, cache field or header key needs
a new API version and a deprecation period of at least one stable release:
the entry gets `deprecated=(version, instead)` in its registry
(runtime.ApiFunction / Param, cache_data.CacheField, metadata.HEADER_KEYS),
and once it is removed, a function goes to runtime.REMOVED.

These are the texts the runtime prints, the Macro manager shows and
metadata.find_deprecated() reports, so they read the same everywhere. No
OpenSAK imports: metadata.py, which the OpenSAK-Macros repository uses on
its own, imports this module.
"""

from __future__ import annotations

from typing import Optional

# (API version it was deprecated in, what to use instead, e.g.
# "opensak.new_name()"); it is removed no earlier than one stable release
# later (runtime.API_STABLE).
Deprecated = tuple[int, str]


def deprecated_message(name: str, deprecated: Deprecated) -> str:
    """E.g. "opensak.old_name is deprecated since API 5 and will be removed
    in a later version; use opensak.new_name() instead"."""
    version, instead = deprecated
    return (f"{name} is deprecated since API {version} and will be removed in a "
            f"later version; use {instead} instead")


def removed_message(name: str, version: int, instead: str) -> str:
    """E.g. "opensak.old_name was removed in API 6; use opensak.new_name()
    instead"."""
    return f"{name} was removed in API {version}; use {instead} instead"


def deprecated_note(deprecated: Optional[Deprecated]) -> str:
    """E.g. "Deprecated in API version 5: use `opensak.new_name()`." for
    the docs; "" if not deprecated."""
    if deprecated is None:
        return ""
    version, instead = deprecated
    return f"Deprecated in API version {version}: use `{instead}`."

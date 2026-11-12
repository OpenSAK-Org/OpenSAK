"""
src/opensak/export/cache_text.py — per-cache names and descriptions for an
export, set by a Lua macro (opensak.export_gpx's rename / description /
first_log functions).

The writers (GPX, GGZ, LOC, KML) fall back to the cache's own name and to
their usual description for every cache without an entry, so an empty
ExportText changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional


@dataclass(frozen=True)
class ExportText:
    # gc_code → name written instead of the cache name
    names: Mapping[str, str] = field(default_factory=dict)
    # gc_code → waypoint description (GPX <desc>, LOC label, KML pop-up)
    descriptions: Mapping[str, str] = field(default_factory=dict)
    # gc_code → text of the extra-info first log (GPX/GGZ, #454)
    first_logs: Mapping[str, str] = field(default_factory=dict)

    def renamed(self, cache) -> bool:
        """True if the macro gave *cache* a name of its own."""
        return (cache.gc_code or "") in self.names

    def first_log(self, cache) -> Optional[str]:
        """The macro's extra-info log text, or None for the export's
        template."""
        return self.first_logs.get(cache.gc_code or "")

    def name(self, cache) -> str:
        return self.names.get(cache.gc_code or "", cache.name or "")

    def description(self, cache) -> Optional[str]:
        """The macro's description, or None for the writer's default."""
        return self.descriptions.get(cache.gc_code or "")


NO_TEXT = ExportText()

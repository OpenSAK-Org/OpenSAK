"""
src/opensak/export/file_export.py — write caches to a GPX, LOC, GGZ or KML
file.

Qt-free, so the file export dialog (on a worker thread) and Lua macros
(opensak.export_file, opensak.export_gpx) share the same export logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from opensak.export.cache_text import NO_TEXT, ExportText

# What write_export_file() writes; the export dialog and its saved settings
# offer the first three.
EXPORT_FORMATS = ("gpx", "loc", "ggz", "kml")


def select_for_export(caches: list, max_records: int = 0) -> list:
    """The caches an export writes: only those with coordinates, at most
    *max_records* of them (0 = all)."""
    caches = [c for c in caches if c.latitude is not None]
    return caches[:max_records] if max_records else caches


def write_export_file(
    caches: list,
    output_path: Path,
    fmt: str,
    use_corrected: bool = True,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    text: ExportText = NO_TEXT,
    attributes: bool = True,
    child_waypoints: bool = True,
) -> int:
    """Write *caches* to *output_path* in *fmt* (one of EXPORT_FORMATS).

    The caches are reloaded with everything the export needs first (see
    reload_caches_full). *text* replaces names and descriptions per cache;
    *attributes* (GPX/GGZ) and *child_waypoints* (GPX/GGZ/KML) can be left
    out. Returns the number of caches written.
    """
    from opensak.gps.garmin import generate_gpx, generate_loc, generate_ggz
    from opensak.db.database import reload_caches_full

    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"unknown export format {fmt!r}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    caches = reload_caches_full(caches)

    if fmt == "gpx":
        content = generate_gpx(caches, output_path.stem, progress_cb=progress_cb,
                               use_corrected=use_corrected, text=text,
                               attributes=attributes, child_waypoints=child_waypoints)
        output_path.write_text(content, encoding="utf-8")
    elif fmt == "loc":
        content = generate_loc(caches, progress_cb=progress_cb,
                               use_corrected=use_corrected, text=text)
        output_path.write_text(content, encoding="utf-8")
    elif fmt == "kml":
        from opensak.export.kml import export_kml
        export_kml(caches, output_path, include_waypoints=child_waypoints,
                   progress_cb=progress_cb, use_corrected=use_corrected, text=text)
    else:
        data = generate_ggz(caches, output_path.stem, progress_cb=progress_cb,
                            use_corrected=use_corrected, text=text,
                            attributes=attributes, child_waypoints=child_waypoints)
        output_path.write_bytes(data)

    return len([c for c in caches if c.latitude is not None])


def active_database_name() -> str:
    """Name of the active database, or "" when there is none."""
    try:
        from opensak.db.manager import get_db_manager
        active = get_db_manager().active
        return active.name if active else ""
    except Exception:
        return ""

"""
src/opensak/export/file_export.py — write caches to a GPX, LOC, GGZ or KML
file.

Qt-free, so the file export dialog (on a worker thread) and Lua macros
(opensak.export_file, opensak.export_gpx) share the same export logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import IO, Any, Callable, Optional

from opensak.export.cache_text import NO_TEXT, ExportText
from opensak.export.gpx_content import DEFAULT_CONTENT, GpxContent

# What write_export_file() writes; the export dialog and its saved settings
# offer the first three.
EXPORT_FORMATS = ("gpx", "loc", "ggz", "kml")

# Saves an export file: save(path, write, binary) calls write(f) with f
# open on the file (binary, or UTF-8 text with the platform's newlines) and
# returns what it returns. The default, save_file(), is used by the export
# dialogs; Lua macros pass one that goes through
# opensak.macro.permissions.safe_write_stream().
Saver = Callable[[Path, Callable[[IO[Any]], Any], bool], Any]


def save_file(path: Path, write: Callable[[IO[Any]], Any], binary: bool = False) -> Any:
    """The default Saver: create the folder, then write_export()."""
    from opensak.gps.garmin import write_export

    path.parent.mkdir(parents=True, exist_ok=True)
    return write_export(path, write, binary)


def wpt_counts(caches: list, fmt: str = "gpx",
               content: GpxContent = DEFAULT_CONTENT) -> list[int]:
    """The number of <wpt> an export in *fmt* writes for each of *caches*
    (#775): the cache itself, unless *content* writes children only, plus
    the child waypoints *content* writes. LOC and KML count the caches
    only. Database caches are counted in the database (export_child_counts),
    other objects, such as test stand-ins, from their attributes."""
    from opensak.db.database import export_child_counts, is_db_cache

    content = content.for_format(fmt)
    own = 0 if content.children_only else 1
    if fmt not in ("gpx", "ggz") or not content.writes_children():
        return [own for _ in caches]
    db_counts = export_child_counts(
        [c.id for c in caches if is_db_cache(c)], content.children,
        corrected_final_wp=content.corrected_final_wp,
    )
    return [
        own + (db_counts.get(c.id, 0) if is_db_cache(c) else len(content.child_waypoints(c)))
        for c in caches
    ]


def select_for_export(
    caches: list, max_records: int = 0, max_counts: str = "caches",
    fmt: str = "gpx", content: GpxContent = DEFAULT_CONTENT,
    counts: Optional[list[int]] = None,
) -> list:
    """The caches an export writes: only those with coordinates, at most
    *max_records* of them (0 = all), in the given order.

    #775: with *max_counts* "waypoints" the limit counts every <wpt> of the
    file instead (wpt_counts(), or *counts* when the caller already has
    them for the caches with coordinates): a cache is taken with all its
    children or not at all, and the selection ends at the first cache that
    no longer fits, as GSAK's "max waypoints" does.
    """
    caches = [c for c in caches if c.latitude is not None]
    if not max_records:
        return caches
    if max_counts != "waypoints":
        return caches[:max_records]
    if counts is None:
        counts = wpt_counts(caches, fmt, content)
    selected = []
    used = 0
    for cache, n in zip(caches, counts):
        if used + n > max_records:
            break
        selected.append(cache)
        used += n
    return selected


def write_export_file(
    caches: list,
    output_path: Path,
    fmt: str,
    use_corrected: bool = True,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    text: ExportText = NO_TEXT,
    attributes: bool = True,
    child_waypoints: bool = True,
    save: Saver = save_file,
    content: GpxContent = DEFAULT_CONTENT,
) -> int:
    """Write *caches* to *output_path* in *fmt* (one of EXPORT_FORMATS)
    with *save* (see Saver).

    The caches are reloaded with everything the export needs first (see
    reload_caches_full). *text* replaces names and descriptions per cache;
    *attributes* (GPX/GGZ) and *child_waypoints* (GPX/GGZ/KML) can be left
    out. *content* holds the #775 options for GPX and GGZ (see
    GpxContent.for_format()). Returns the number of caches written.

    #1075/#1078: GPX, LOC, GGZ and KML are streamed — the caches are reloaded a chunk
    at a time (iter_caches_full) and written one by one — so exporting
    100,000+ caches no longer holds them all, or the whole document, in
    memory. They are written to a temporary file next to *output_path* that
    replaces it only once complete, so a failed export never leaves a
    truncated file behind.
    """
    from opensak.gps.garmin import gpx_bounds, write_gpx, write_loc, write_ggz
    from opensak.db.database import iter_caches_full

    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"unknown export format {fmt!r}")

    caches = list(caches)
    full = iter_caches_full(caches)
    content = content.for_format(fmt)

    if fmt == "kml":
        # #1078: KML is streamed the same way.
        from opensak.export.kml import export_kml
        export_kml(full, output_path, include_waypoints=child_waypoints,
                   progress_cb=progress_cb, use_corrected=use_corrected, text=text,
                   save=save, total=len(caches), count=len(caches))
        return len([c for c in caches if c.latitude is not None])

    def _write(out) -> int:
        if fmt == "loc":
            return write_loc(out, full, progress_cb=progress_cb,
                             use_corrected=use_corrected, text=text, total=len(caches))
        # #700: <bounds> comes before the first cache, so it is computed
        # from the selection up front, not from the streamed caches.
        bounds = gpx_bounds(caches, use_corrected, child_waypoints, content)
        if fmt == "gpx":
            return write_gpx(out, full, output_path.stem, progress_cb=progress_cb,
                             use_corrected=use_corrected, text=text,
                             attributes=attributes, child_waypoints=child_waypoints,
                             total=len(caches), bounds=bounds, content=content)
        return write_ggz(out, full, output_path.stem, progress_cb=progress_cb,
                         use_corrected=use_corrected, text=text,
                         attributes=attributes, child_waypoints=child_waypoints,
                         total=len(caches), bounds=bounds, content=content)

    return save(output_path, _write, fmt == "ggz")


def active_database_name() -> str:
    """Name of the active database, or "" when there is none."""
    try:
        from opensak.db.manager import get_db_manager
        active = get_db_manager().active
        return active.name if active else ""
    except Exception:
        return ""

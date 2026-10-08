"""
src/opensak/export/file_export.py — write caches to a GPX, LOC or GGZ file.

Qt-free, so the file export dialog (on a worker thread) and Lua macros
(opensak.export_file) share the same export logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional


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
) -> int:
    """Write *caches* to *output_path* in *fmt* ("gpx" | "loc" | "ggz").

    The caches are reloaded with everything the export needs first (see
    reload_caches_full). Returns the number of caches written.
    """
    from opensak.gps.garmin import generate_gpx, generate_loc, generate_ggz
    from opensak.db.database import reload_caches_full

    if fmt not in ("gpx", "loc", "ggz"):
        raise ValueError(f"unknown export format {fmt!r}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    caches = reload_caches_full(caches)

    if fmt == "gpx":
        content = generate_gpx(caches, output_path.stem, progress_cb=progress_cb,
                               use_corrected=use_corrected)
        output_path.write_text(content, encoding="utf-8")
    elif fmt == "loc":
        content = generate_loc(caches, progress_cb=progress_cb,
                               use_corrected=use_corrected)
        output_path.write_text(content, encoding="utf-8")
    else:
        data = generate_ggz(caches, output_path.stem, progress_cb=progress_cb,
                            use_corrected=use_corrected)
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

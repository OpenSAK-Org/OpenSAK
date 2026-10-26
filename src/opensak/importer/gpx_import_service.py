"""
src/opensak/importer/gpx_import_service.py — GPX / PQ zip import without
questions.

What the GPX import dialog and the main window do for a GPX import, as one
call that never asks anything: import each .gpx (with its companion
waypoints file) and PQ .zip into one database, then the steps that follow a
GUI import — fill in missing country / state / county (when reverse
geocoding is on) and bring the distances up to date. It returns a report
instead of showing one.

The shared GPX import service of the headless command-line import (#937),
next to gsak_import_service for GSAK. The per-file import is
gpx_import_settings.import_file() and the location step is
opensak.geo.reverse_geocode, which the dialog uses as well. Qt-free:
nothing here needs a QApplication, and nothing here may open a dialog.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Optional

from opensak.importer.gpx_import_settings import (
    FOLDER_IMPORT_SUFFIXES,
    companion_wpts,
    folder_import_files,
    import_file,
)

if TYPE_CHECKING:
    from opensak.geo.reverse_geocode import UpdateLocationResult
    from opensak.importer import ImportResult

logger = logging.getLogger(__name__)


class GpxImportError(Exception):
    """The import cannot start (a source is missing or not a .gpx / .zip
    file). Nothing has been changed."""


# ── Sources ──────────────────────────────────────────────────────────────────

def collect_files(paths: Iterable[Path], recursive: bool = False) -> list[Path]:
    """The files to import for *paths*, each once, in the order given.

    A file must be a .gpx or .zip. A folder adds every .gpx / .zip directly
    in it, as a folder entry of the import dialog does (companion waypoint
    files left out, see folder_import_files); with *recursive* its
    subfolders too, each folder sorted by name. Raises GpxImportError for a
    path that is missing or of another type.
    """
    files: list[Path] = []

    def add(path: Path) -> None:
        if path not in files:
            files.append(path)

    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            folders = [path]
            if recursive:
                folders += sorted(p for p in path.rglob("*") if p.is_dir())
            for folder in folders:
                for f in folder_import_files(folder):
                    add(f)
        elif path.is_file():
            if path.suffix.lower() not in FOLDER_IMPORT_SUFFIXES:
                raise GpxImportError(f"{path} is not a .gpx or .zip file")
            add(path)
        else:
            raise GpxImportError(f"{path} does not exist")
    return files


# ── Report ──────────────────────────────────────────────────────────────────

@dataclass
class FileReport:
    """What happened to one file."""

    path: Path
    result: Optional[ImportResult] = None
    error: str = ""                    # the file could not be imported at all

    @property
    def ok(self) -> bool:
        return not self.error and self.result is not None and not self.result.errors

    @property
    def companion(self) -> Optional[Path]:
        """The waypoints file imported with a .gpx, if any."""
        if self.path.suffix.lower() != ".gpx":
            return None
        return companion_wpts(self.path)


@dataclass
class GpxImportReport:
    database: Path
    files: list[FileReport] = field(default_factory=list)
    locations: Optional[UpdateLocationResult] = None
    locations_error: str = ""
    cancelled: bool = False

    def total(self, attr: str) -> int:
        return sum(getattr(f.result, attr) for f in self.files if f.result)

    @property
    def changed(self) -> bool:
        return bool(self.total("created") or self.total("updated"))

    @property
    def errors(self) -> int:
        """Files that failed, caches that failed, a failed location update."""
        count = sum(1 for f in self.files if f.error)
        count += sum(len(f.result.errors) for f in self.files if f.result)
        if self.locations is not None:
            count += self.locations.errors
        return count + bool(self.locations_error)

    @property
    def ok(self) -> bool:
        return not self.cancelled and self.errors == 0

    def summary_lines(self) -> list[str]:
        lines = [f"GPX import into {self.database}"]
        for f in self.files:
            if f.error:
                lines.append(f"  {f.path.name}: failed ({f.error})")
                continue
            r = f.result
            if r is None:
                lines.append(f"  {f.path.name}: not imported (cancelled)")
                continue
            lines.append(f"  {f.path.name}: {r.created} new, {r.updated} updated, "
                         f"{r.skipped} skipped, {r.waypoints} waypoints, "
                         f"{len(r.errors)} errors")
            lines.extend(f"    - {e}" for e in r.errors[:5])
        if not self.files:
            lines.append("  no .gpx or .zip files to import")
        else:
            lines.append(f"  Total: {self.total('created')} new, {self.total('updated')} "
                         f"updated, {self.total('skipped')} skipped, "
                         f"{self.total('waypoints')} waypoints")
        if self.locations is not None:
            loc = self.locations
            lines.append(f"  Locations: {loc.updated} updated, {loc.errors} errors")
        elif self.locations_error:
            lines.append(f"  Locations failed: {self.locations_error}")
        if self.cancelled:
            lines.append("  Cancelled")
        return lines


# ── Running ─────────────────────────────────────────────────────────────────

def run_gpx_import(
    files: list[Path],
    db_path: Path,
    *,
    geocode: Optional[bool] = None,
    progress_cb: Optional[Callable[[str, int], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> GpxImportReport:
    """Import *files* (see collect_files) into the database at *db_path*.

    *geocode*: fill in missing locations afterwards; None = when the
    reverse-geocoding feature is on, as the dialog does. *progress_cb(name,
    caches)* reports progress within a file; *cancel_cb()* returning True
    stops before the next file. One failing file is reported and the run
    goes on with the next.
    """
    from opensak.db.database import session_on

    report = GpxImportReport(db_path)
    for f in files:
        if cancel_cb is not None and cancel_cb():
            report.cancelled = True
            break
        rep = FileReport(f)
        report.files.append(rep)

        def on_progress(count: int, name: str = f.name) -> None:
            if progress_cb is not None:
                progress_cb(name, count)

        try:
            with session_on(db_path) as session:
                rep.result = import_file(f, session, progress_cb=on_progress)
        except Exception as exc:
            logger.warning("GPX import of %s failed", f, exc_info=True)
            rep.error = str(exc) or type(exc).__name__

    if report.changed:
        after_import(report, geocode=geocode)

    for line in report.summary_lines():
        logger.info("%s", line)
    return report


def after_import(report: GpxImportReport, geocode: Optional[bool] = None) -> None:
    """The steps that follow a GPX import in the GUI, on the database
    imported into: missing locations, then distances."""
    if geocode is None:
        from opensak.utils import flags
        geocode = flags.reverse_geocoding
    if geocode:
        from opensak.geo.reverse_geocode import reverse_geocode, rows_missing_location
        try:
            rows = rows_missing_location(report.database)
            if rows:
                report.locations = reverse_geocode(rows, report.database)
        except Exception as exc:
            logger.warning("GPX import: location update failed", exc_info=True)
            report.locations_error = str(exc) or type(exc).__name__
    recalculate_distances_for(report.database)


def recalculate_distances_for(db_path: Path) -> None:
    """Recalculate *db_path*'s distances from its own home point, as the
    main window does after a GPX import — always, since the cheap
    up-to-date check can't see changed coordinates of existing caches. A
    failure is only logged: the next refresh in the GUI checks again."""
    try:
        from opensak.db.database import recalculate_distances
        from opensak.gui.settings import get_settings
        lat, lon = get_settings().home_for_db_file(db_path)
        if lat and lon:
            recalculate_distances(lat, lon, db_path=db_path)
    except Exception:
        logger.warning("GPX import: could not update distances for %s",
                       db_path, exc_info=True)

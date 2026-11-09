"""
src/opensak/importer/gpx_import_settings.py — named, saveable settings for
the GPX / PQ zip import dialog.

Stored like the file export settings (see
opensak.export.file_export_settings), as JSON files in
<app data>/import_settings/, with the settings of the most recent import
kept as "… last used". from_dict() falls back to the default for every
missing or invalid key, so older settings keep loading when options are
added.

Also the parts of an import the dialog and opensak.import_gpx() in Lua
macros share: which files a settings' folders and files cover, and
importing one GPX / zip file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from opensak.export.file_export_settings import FileExportProfile

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from opensak.importer import ImportResult

# File types a folder entry imports.
FOLDER_IMPORT_SUFFIXES = (".gpx", ".zip")


@dataclass
class GpxImportSettings:
    """All options of the GPX import dialog."""

    database: str = ""        # path of the target database ("" = active one)
    files: list[str] = field(default_factory=list)   # files to import
    # Folders to import every GPX / zip file of (not subfolders); the files
    # are looked up at each import, so new ones are picked up.
    folders: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "database": self.database,
            "files": list(self.files),
            "folders": list(self.folders),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GpxImportSettings":
        defaults = cls()
        database = data.get("database", defaults.database)
        if not isinstance(database, str):
            database = defaults.database
        return cls(
            database=database,
            files=cls._str_list(data.get("files")),
            folders=cls._str_list(data.get("folders")),
        )

    @staticmethod
    def _str_list(value) -> list[str]:
        """The non-empty strings of a saved list ([] for anything else)."""
        if not isinstance(value, list):
            return []
        return [v for v in value if isinstance(v, str) and v]


class GpxImportProfile(FileExportProfile):
    """A named GpxImportSettings, saved as JSON."""

    settings_class = GpxImportSettings
    dir_name = "import_settings"


def folder_import_files(folder: Path) -> list[Path]:
    """Return the files a folder entry imports: every GPX / zip file
    directly in *folder* (not in subfolders), sorted by name. Companion
    waypoint files are left out when a main cache GPX is there — the
    import picks them up next to that file.
    """
    from opensak.importer import _is_companion_gpx

    try:
        files = sorted(
            f for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in FOLDER_IMPORT_SUFFIXES
        )
    except OSError:
        return []
    gpx = [f for f in files if f.suffix.lower() == ".gpx"]
    companions = {f for f in gpx if _is_companion_gpx(f)}
    if companions and any(f not in companions for f in gpx):
        files = [f for f in files if f not in companions]
    return files


def companion_wpts(gpx_path: Path) -> Optional[Path]:
    """The waypoints file next to *gpx_path*, found by its content, not by
    a file name convention; None if there is none."""
    from opensak.importer import _is_companion_gpx

    try:
        return next(
            (
                f for f in sorted(gpx_path.parent.glob("*.gpx"))
                if f != gpx_path and _is_companion_gpx(f)
            ),
            None,
        )
    except Exception:
        return None


def import_file(
    path: Path,
    session: "Session | None" = None,
    progress_cb: Optional[Callable[[int], None]] = None,
) -> "ImportResult":
    """Import one GPX file (with its companion waypoints file) or PQ zip
    into the database of *session* (default: the active one). Raises
    ValueError for another file type."""
    from opensak.importer import import_gpx, import_zip
    from opensak.utils.utils import ImportType, get_import_type

    if get_import_type(path) == ImportType.GPX:
        return import_gpx(path, session, wpts_path=companion_wpts(path),
                          progress_cb=progress_cb)
    return import_zip(path, session, progress_cb=progress_cb)

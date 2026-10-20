"""
src/opensak/importer/gpx_import_settings.py — named, saveable settings for
the GPX / PQ zip import dialog.

Stored like the file export settings (see
opensak.export.file_export_settings), as JSON files in
<app data>/import_settings/, with the settings of the most recent import
kept as "… last used". from_dict() falls back to the default for every
missing or invalid key, so older settings keep loading when options are
added.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from opensak.export.file_export_settings import FileExportProfile


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

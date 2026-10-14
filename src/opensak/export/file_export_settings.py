"""
src/opensak/export/file_export_settings.py — named, saveable settings for
the GPX/LOC/GGZ file export dialog.

Stored as JSON files in <app data>/export_settings/, one file per named
setting — the same model as FilterProfile (filters/engine.py), so the
settings are independent of the active database. The settings used for the
most recent export are kept in a reserved file and offered as
"… last used" in the dialog.

Forward compatibility: FileExportSettings.from_dict() falls back to the
default for every missing or invalid key, so settings saved by an older
version keep loading after new export options are added. New options only
need a field with a default, plus a line in to_dict()/from_dict().
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

EXPORT_FORMATS = ("gpx", "loc", "ggz")

# What the export does when the target file already exists.
IF_EXISTS_CHOICES = ("overwrite", "ask", "skip")

# Variables a file name template may contain, in the order they are listed
# to the user. Names are matched case-insensitively.
FILE_NAME_VARIABLES = (
    "database", "filter", "center", "date", "time", "datetime",
    "year", "month", "day", "hour", "minute", "second",
    "format", "count",
)

DEFAULT_FILE_NAME = "opensak_export"

_VARIABLE_RE = re.compile(r"\{(\w+)\}")
# Characters that are not allowed in a file name on Windows (and "/" on any
# platform), plus control characters.
_INVALID_FILE_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Reserved file for the settings used by the most recent export. It is left
# out of list_profiles(), and profile_path() steers a user-chosen name away
# from it.
_LAST_USED_STEM = "__last_used__"


@dataclass
class FileExportSettings:
    """All options of the file export dialog."""

    fmt: str = "gpx"          # "gpx" | "loc" | "ggz"
    output_path: str = ""     # last exported file ("" = not set)
    use_corrected_coords: bool = True   # False = always export original coords
    max_records: int = 0      # max caches to export (0 = all)
    # File name template without extension — fixed text and/or variables
    # such as {database} or {date} (see expand_file_name). "" =
    # DEFAULT_FILE_NAME.
    file_name: str = ""
    folder: str = ""          # folder the export is written to ("" = ask)
    if_exists: str = "ask"    # "overwrite" | "ask" | "skip"

    def to_dict(self) -> dict:
        return {
            "fmt": self.fmt,
            "output_path": self.output_path,
            "use_corrected_coords": self.use_corrected_coords,
            "max_records": self.max_records,
            "file_name": self.file_name,
            "folder": self.folder,
            "if_exists": self.if_exists,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FileExportSettings":
        defaults = cls()
        fmt = data.get("fmt", defaults.fmt)
        if fmt not in EXPORT_FORMATS:
            fmt = defaults.fmt
        output_path = data.get("output_path", defaults.output_path)
        if not isinstance(output_path, str):
            output_path = defaults.output_path
        use_corrected = data.get("use_corrected_coords", defaults.use_corrected_coords)
        if not isinstance(use_corrected, bool):
            use_corrected = defaults.use_corrected_coords
        max_records = data.get("max_records", defaults.max_records)
        # bool is an int subclass — reject it explicitly.
        if (not isinstance(max_records, int) or isinstance(max_records, bool)
                or max_records < 0):
            max_records = defaults.max_records
        file_name = data.get("file_name", defaults.file_name)
        if not isinstance(file_name, str):
            file_name = defaults.file_name
        folder = data.get("folder")
        if not isinstance(folder, str):
            # Settings from before the folder option: keep exporting to the
            # folder of the last exported file.
            folder = str(Path(output_path).parent) if output_path else defaults.folder
        if_exists = data.get("if_exists", defaults.if_exists)
        if if_exists not in IF_EXISTS_CHOICES:
            if_exists = defaults.if_exists
        return cls(
            fmt=fmt,
            output_path=output_path,
            use_corrected_coords=use_corrected,
            max_records=max_records,
            file_name=file_name,
            folder=folder,
            if_exists=if_exists,
        )


def expand_file_name(
    template: str,
    *,
    database: str = "",
    filter_name: str = "",
    center_name: str = "",
    fmt: str = "gpx",
    count: int = 0,
    now: Optional[datetime] = None,
) -> str:
    """Return the file name (without extension) for a file name template.

    *template* is fixed text and/or variables in braces, e.g.
    ``"{database}_{date}"``. Supported variables are FILE_NAME_VARIABLES;
    unknown ones are kept as typed. Characters that are not allowed in a
    file name are replaced with "_". A trailing ".<fmt>" is dropped, since
    the extension is added by the export. Falls back to DEFAULT_FILE_NAME
    when nothing usable is left.
    """
    if now is None:
        now = datetime.now()
    values = {
        "database": database,
        "filter": filter_name,
        "center": center_name,
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H-%M-%S"),
        "datetime": now.strftime("%Y-%m-%d_%H-%M-%S"),
        "year": now.strftime("%Y"),
        "month": now.strftime("%m"),
        "day": now.strftime("%d"),
        "hour": now.strftime("%H"),
        "minute": now.strftime("%M"),
        "second": now.strftime("%S"),
        "format": fmt,
        "count": str(count),
    }

    def _sub(match: re.Match) -> str:
        return values.get(match.group(1).lower(), match.group(0))

    name = _VARIABLE_RE.sub(_sub, template.strip())
    name = _INVALID_FILE_NAME_CHARS.sub("_", name)
    if name.lower().endswith(f".{fmt}"):
        name = name[: -len(fmt) - 1]
    # Windows drops trailing dots and spaces from file names.
    name = name.strip().rstrip(". ")
    return name or DEFAULT_FILE_NAME


class FileExportProfile:
    """A named FileExportSettings, saved as JSON."""

    def __init__(self, name: str, settings: FileExportSettings):
        self.name = name
        self.settings = settings

    @staticmethod
    def default_dir() -> Path:
        from opensak.config import get_app_data_dir
        return get_app_data_dir() / "export_settings"

    @classmethod
    def profile_path(cls, name: str, profiles_dir: Optional[Path] = None) -> Path:
        """Return the JSON path a profile called *name* is stored at."""
        if profiles_dir is None:
            profiles_dir = cls.default_dir()
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)
        if safe_name == _LAST_USED_STEM:
            safe_name += "_"   # never clobber the reserved "last used" file
        return profiles_dir / f"{safe_name}.json"

    def save(self, profiles_dir: Optional[Path] = None) -> Path:
        """Save this profile to disk as JSON. Returns the saved file path."""
        path = self.profile_path(self.name, profiles_dir)
        self._write(path)
        return path

    def _write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"name": self.name, "settings": self.settings.to_dict()}
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "FileExportProfile":
        """Load a profile from a JSON file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            name=data["name"],
            settings=FileExportSettings.from_dict(data.get("settings", {})),
        )

    @classmethod
    def list_profiles(cls, profiles_dir: Optional[Path] = None) -> list[Path]:
        """Return the paths of all user-saved profiles (not "last used")."""
        if profiles_dir is None:
            profiles_dir = cls.default_dir()
        if not profiles_dir.exists():
            return []
        return sorted(
            p for p in profiles_dir.glob("*.json") if p.stem != _LAST_USED_STEM
        )

    # ── "Last used" ───────────────────────────────────────────────────────────

    @classmethod
    def last_used_path(cls, profiles_dir: Optional[Path] = None) -> Path:
        if profiles_dir is None:
            profiles_dir = cls.default_dir()
        return profiles_dir / f"{_LAST_USED_STEM}.json"

    @classmethod
    def load_last_used(cls, profiles_dir: Optional[Path] = None) -> FileExportSettings:
        """Return the settings of the most recent export, or the defaults."""
        path = cls.last_used_path(profiles_dir)
        try:
            return cls.load(path).settings
        except Exception:
            return FileExportSettings()

    @classmethod
    def save_last_used(
        cls, settings: FileExportSettings, profiles_dir: Optional[Path] = None
    ) -> None:
        cls(_LAST_USED_STEM, settings)._write(cls.last_used_path(profiles_dir))

    def __repr__(self) -> str:
        return f"<FileExportProfile {self.name!r}>"

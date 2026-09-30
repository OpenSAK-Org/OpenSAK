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
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

EXPORT_FORMATS = ("gpx", "loc", "ggz")

# Reserved file for the settings used by the most recent export. It is left
# out of list_profiles(), and profile_path() steers a user-chosen name away
# from it.
_LAST_USED_STEM = "__last_used__"


@dataclass
class FileExportSettings:
    """All options of the file export dialog."""

    fmt: str = "gpx"          # "gpx" | "loc" | "ggz"
    output_path: str = ""     # last chosen output file ("" = not set)

    def to_dict(self) -> dict:
        return {
            "fmt": self.fmt,
            "output_path": self.output_path,
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
        return cls(fmt=fmt, output_path=output_path)


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

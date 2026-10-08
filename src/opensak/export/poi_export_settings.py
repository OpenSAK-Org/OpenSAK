"""
src/opensak/export/poi_export_settings.py — named, saveable settings for
the Garmin POI (GPI) export.

Stored like the file export settings (see file_export_settings), as JSON
files in <app data>/poi_export_settings/, with the settings of the most
recent export kept as "… last used". from_dict() falls back to the
default for every missing or invalid key, so older settings keep loading
when options are added.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from opensak.export.file_export_settings import IF_EXISTS_CHOICES, FileExportProfile

# Which child waypoints are exported, by their flag.
WAYPOINT_FLAG_CHOICES = ("all", "flagged", "unflagged")

# Where the "Extra" text goes in the POI: the phone number or the address
# line (both are shown on the device's POI details page).
EXTRA_FIELD_CHOICES = ("phone", "address")

# Units of the proximity alert distance, with their size in meters.
PROXIMITY_UNITS = {"m": 1.0, "km": 1000.0, "ft": 0.3048, "yd": 0.9144, "mi": 1609.344}

DEFAULT_CATEGORY = "OpenSAK"


@dataclass
class PoiExportSettings:
    """All options of the POI export dialog."""

    folder: str = ""              # folder the export is written to ("" = ask)
    # File name template without extension, with the file export's
    # variables ({database}, {date} …; "" = DEFAULT_FILE_NAME).
    file_name: str = ""
    if_exists: str = "ask"        # "overwrite" | "ask" | "skip"

    use_corrected_coords: bool = True
    include_waypoints: bool = True     # also export the child waypoints
    waypoints_only: bool = False       # export only child waypoints, no caches
    waypoint_flag: str = "all"         # "all" | "flagged" | "unflagged"
    # One file per waypoint type ("<name> - Parking Area.gpi" …) next to
    # the file with the caches; the category of each is its type.
    split_by_type: bool = False
    max_points: int = 0                # max POIs to export (0 = all)

    # POI texts — templates with the variables in poi_export.POI_VARIABLES.
    name: str = "{smart}"
    smart_length: int = 20             # length {smart} shortens names to
    description: str = "{name} by {by} ({dif}/{ter})"
    extra: str = "{hint}"
    extra_field: str = "phone"         # "phone" | "address"

    category: str = DEFAULT_CATEGORY
    proximity: float = 0.0             # proximity alert distance (0 = none)
    proximity_unit: str = "m"          # key of PROXIMITY_UNITS
    icon: str = ""                     # image file for the POI icon ("" = none)

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: dict) -> "PoiExportSettings":
        defaults = cls()
        values = {}
        for f in fields(cls):
            default = getattr(defaults, f.name)
            value = data.get(f.name, default)
            if isinstance(default, bool):
                ok = isinstance(value, bool)
            elif isinstance(default, int):
                # bool is an int subclass — reject it explicitly.
                ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
            elif isinstance(default, float):
                ok = (isinstance(value, (int, float)) and not isinstance(value, bool)
                      and value >= 0)
                if ok:
                    value = float(value)
            else:
                ok = isinstance(value, str)
            values[f.name] = value if ok else default
        for name, choices in (
            ("if_exists", IF_EXISTS_CHOICES),
            ("waypoint_flag", WAYPOINT_FLAG_CHOICES),
            ("extra_field", EXTRA_FIELD_CHOICES),
            ("proximity_unit", PROXIMITY_UNITS),
        ):
            if values[name] not in choices:
                values[name] = getattr(defaults, name)
        if values["smart_length"] < 1:
            values["smart_length"] = defaults.smart_length
        return cls(**values)

    def proximity_meters(self) -> int:
        """The proximity alert distance in whole meters (0 = none)."""
        return int(round(self.proximity * PROXIMITY_UNITS[self.proximity_unit]))


class PoiExportProfile(FileExportProfile):
    """A named PoiExportSettings, saved as JSON."""

    settings_class = PoiExportSettings
    dir_name = "poi_export_settings"

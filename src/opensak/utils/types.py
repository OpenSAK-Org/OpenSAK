"""
src/opensak/utils/types.py — Core enumerations and type aliases.

Defines shared Enums and TypeAliases used across the codebase so that
function signatures are self-documenting and the type checker can enforce
valid values at call sites (instead of accepting any string).
"""

from enum import Enum, IntEnum, StrEnum, auto
from typing import TypeAlias


# ── Import / log enums ────────────────────────────────────────────────────────

class ImportType(Enum):
    """Supported import file formats."""
    GPX = auto()
    ZIP = auto()


class LogType(IntEnum):
    """Groundspeak cache log types, mapped to their integer IDs."""
    FOUND = 2
    DNF = 3
    NOTE = 4
    ARCHIVE = 5


# ── Coordinate types ──────────────────────────────────────────────────────────

class CoordFormat(StrEnum):
    """
    Supported coordinate display formats.

    Using StrEnum means values still serialise/compare as plain strings
    ("dd", "dmm", "dms"), so existing persisted settings keep working.
    """
    DD  = "dd"   # 55.78750, 12.41667
    DMM = "dmm"  # N55 47.250 E012 25.000
    DMS = "dms"  # N55° 47' 15" E012° 25' 00"


class DateFormat(StrEnum):
    """Supported date display formats for the cache grid."""
    LOCALE = "locale"  # OS locale short date (e.g. 6/21/25 or 21.06.2026)
    DMY    = "dmy"     # dd.mm.yyyy
    MDY    = "mdy"     # mm/dd/yyyy
    YMD    = "ymd"     # yyyy-mm-dd


class TextSize(StrEnum):
    """Text and icon sizes for UI elements (issue #286/#288/#290)."""
    SMALL  = "small"   # Kompakt: 6/11/9 pt
    MEDIUM = "medium"  # Standard: 7/13/10 pt (default)
    LARGE  = "large"   # Stor: 9/15/12 pt


# Font sizes (pt) for each TextSize level. Maps to:
#   icon_pt (type icon in cache grid)
#   label_pt (info label in detail panel)
#   secondary_pt (corrected coords, hints label)
TEXT_SIZE_MAP = {
    TextSize.SMALL:  {"icon": 6, "label": 11, "secondary": 9},
    TextSize.MEDIUM: {"icon": 7, "label": 13, "secondary": 10},
    TextSize.LARGE:  {"icon": 9, "label": 15, "secondary": 12},
}


# (lat, lon) in decimal degrees — WGS-84.
Coordinate: TypeAlias = tuple[float, float]

# Geocaching.com cache code (e.g. "GC12ABC"). Aliased for intent, not runtime safety.
GcCode: TypeAlias = str

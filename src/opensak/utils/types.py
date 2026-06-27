"""
src/opensak/utils/types.py — Core enumerations and type aliases.

Defines shared Enums and TypeAliases used across the codebase so that
function signatures are self-documenting and the type checker can enforce
valid values at call sites (instead of accepting any string).
"""

import re
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
    LOCALE = "locale"  # OS locale short date (e.g. 06/23/2026 or 23.06.2026)
    DMY    = "dmy"     # dd.mm.yyyy
    MDY    = "mdy"     # mm/dd/yyyy
    YMD    = "ymd"     # yyyy-mm-dd


def norm_locale_date_fmt(raw: str) -> str:
    """Normalise a Qt locale short-date format string.

    Ensures zero-padded fields and a 4-digit year while preserving the
    locale's field order and separator characters.
    """
    fmt = re.sub(r'(?<!d)d(?!d)', 'dd', raw)
    fmt = re.sub(r'(?<!M)M(?!M)', 'MM', fmt)
    fmt = re.sub(r'(?<!y)yy(?!y)', 'yyyy', fmt)
    return fmt


class TextSize(StrEnum):
    """Text and icon sizes for UI elements (issue #286/#288/#290/#375)."""
    SMALL  = "small"   # Kompakt: 5/9/8/8 pt, 20 px rows
    MEDIUM = "medium"  # Standard: 7/13/10/10 pt, 24 px rows (default)
    LARGE  = "large"   # Stor: 10/18/14/13 pt, 30 px rows


# Font sizes (pt) for each TextSize level. Maps to:
#   icon_pt       — type icon in cache grid (SizeBarDelegate)
#   label_pt      — info label in detail panel
#   secondary_pt  — corrected coords, hints label
#   grid_pt       — cell text in the cache grid
#   row_height    — vertical section size (px) in the cache grid
TEXT_SIZE_MAP = {
    TextSize.SMALL:  {"icon": 5,  "label": 9,  "secondary": 8,  "grid": 8,  "row_height": 20},
    TextSize.MEDIUM: {"icon": 7,  "label": 13, "secondary": 10, "grid": 10, "row_height": 24},
    TextSize.LARGE:  {"icon": 10, "label": 18, "secondary": 14, "grid": 13, "row_height": 30},
}


# (lat, lon) in decimal degrees — WGS-84.
Coordinate: TypeAlias = tuple[float, float]

# Geocaching.com cache code (e.g. "GC12ABC"). Aliased for intent, not runtime safety.
GcCode: TypeAlias = str

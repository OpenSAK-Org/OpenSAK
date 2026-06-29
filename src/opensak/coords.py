"""
src/opensak/coords.py — Coordinate format conversion utilities.

Supported formats:
  DD   — Decimal Degrees:          55.78750, 12.41667
  DMM  — Degrees Decimal Minutes:  N55 47.250 E012 25.000
  DMS  — Degrees Minutes Seconds:  N55° 47' 15" E012° 25' 00"

Parse also accepts the geocaching.com copy-paste format:
  N 34° 58.088' E 034° 03.281'   (DMM with degree sign and apostrophe)
  N 34° 58.088 E 034° 03.281     (DMM with degree sign, no apostrophe)
"""

from __future__ import annotations

from opensak.utils.types import Coordinate, CoordFormat

# ── Public aliases (for backwards-compatible API) ────────────────────────────
FORMAT_DD  = CoordFormat.DD
FORMAT_DMM = CoordFormat.DMM
FORMAT_DMS = CoordFormat.DMS

FORMATS = {
    CoordFormat.DMM: "DMM  —  N55 47.250 E012 25.000",
    CoordFormat.DMS: "DMS  —  N55° 47' 15\" E012° 25' 00\"",
    CoordFormat.DD:  "DD   —  55.78750, 12.41667",
}


def _dd_to_dmm(lat: float, lon: float) -> str:
    """Convert decimal degrees to DMM string (geocaching standard)."""
    lat_h = "N" if lat >= 0 else "S"
    lon_h = "E" if lon >= 0 else "W"
    lat_abs = abs(lat)
    lon_abs = abs(lon)
    lat_deg = int(lat_abs)
    lon_deg = int(lon_abs)
    lat_min = (lat_abs - lat_deg) * 60
    lon_min = (lon_abs - lon_deg) * 60
    return f"{lat_h}{lat_deg:02d} {lat_min:06.3f}  {lon_h}{lon_deg:03d} {lon_min:06.3f}"


def _dd_to_dms(lat: float, lon: float) -> str:
    """Convert decimal degrees to DMS string."""
    lat_h = "N" if lat >= 0 else "S"
    lon_h = "E" if lon >= 0 else "W"
    lat_abs = abs(lat)
    lon_abs = abs(lon)
    lat_deg = int(lat_abs)
    lon_deg = int(lon_abs)
    lat_min = int((lat_abs - lat_deg) * 60)
    lon_min = int((lon_abs - lon_deg) * 60)
    lat_sec = (lat_abs - lat_deg - lat_min / 60) * 3600
    lon_sec = (lon_abs - lon_deg - lon_min / 60) * 3600
    return (
        f"{lat_h}{lat_deg:02d}° {lat_min:02d}' {lat_sec:05.2f}\"  "
        f"{lon_h}{lon_deg:03d}° {lon_min:02d}' {lon_sec:05.2f}\""
    )


def _dd_to_dd(lat: float, lon: float) -> str:
    """Format decimal degrees."""
    return f"{lat:.5f}, {lon:.5f}"


def format_coords(lat: float, lon: float, fmt: CoordFormat) -> str:
    """Return a coordinate string in the requested format."""
    if fmt == CoordFormat.DMS:
        return _dd_to_dms(lat, lon)
    if fmt == CoordFormat.DD:
        return _dd_to_dd(lat, lon)
    return _dd_to_dmm(lat, lon)   # default: DMM


# ── Single-axis formatters (used by table columns) ───────────────────────────

def format_lat(lat: float, fmt: CoordFormat) -> str:
    """Format only the latitude part in the requested format.

    Used by the cache list's Latitude column so the value matches the
    user's chosen coordinate format (DD / DMM / DMS).
    """
    h = "N" if lat >= 0 else "S"
    a = abs(lat)
    if fmt == CoordFormat.DD:
        return f"{lat:.6f}"
    if fmt == CoordFormat.DMS:
        deg = int(a)
        m = int((a - deg) * 60)
        s = (a - deg - m / 60) * 3600
        return f"{h}{deg:02d}° {m:02d}' {s:05.2f}\""
    # default: DMM (geocaching standard)
    deg = int(a)
    return f"{h}{deg:02d} {(a - deg) * 60:06.3f}"


def format_lon(lon: float, fmt: CoordFormat) -> str:
    """Format only the longitude part in the requested format.

    Used by the cache list's Longitude column so the value matches the
    user's chosen coordinate format (DD / DMM / DMS).
    """
    h = "E" if lon >= 0 else "W"
    a = abs(lon)
    if fmt == CoordFormat.DD:
        return f"{lon:.6f}"
    if fmt == CoordFormat.DMS:
        deg = int(a)
        m = int((a - deg) * 60)
        s = (a - deg - m / 60) * 3600
        return f"{h}{deg:03d}° {m:02d}' {s:05.2f}\""
    # default: DMM (geocaching standard)
    deg = int(a)
    return f"{h}{deg:03d} {(a - deg) * 60:06.3f}"


# ── Parsing ───────────────────────────────────────────────────────────────────

def _valid_range(lat: float, lon: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def parse_coords(text: str) -> Coordinate | None:
    """
    Try to parse a coordinate string in any supported format.
    Returns (lat, lon) as decimal degrees, or None if parsing fails or
    the values fall outside valid geographic ranges.

    Accepted formats
    ----------------
    DD  :  55.78750, 12.41667
    DMM :  N55 47.250 E012 25.000
    DMM°:  N 34° 58.088' E 034° 03.281'   (med apostrof)
    DMM°:  N 34° 58.088 E 034° 03.281     (uden apostrof — fixes #59)
    DMM°:  N38° 33.502 W90° 22.774        (uden mellemrum efter hemisphere)
    DMS :  N55° 47' 15.00" E012° 25' 00.00"
    """
    import re
    text = text.strip()

    # ── DD: "55.78750, 12.41667" or "55.78750 12.41667" ──────────────────────
    m = re.match(
        r'^([+-]?\d+\.\d+)[,\s]+([+-]?\d+\.\d+)$', text
    )
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        return (lat, lon) if _valid_range(lat, lon) else None

    # ── DMM°: "N 34° 58.088' E 034° 03.281'" (geocaching.com format) ─────────
    # Grads-tegn efter grader, apostrof efter minutter er valgfri (fixes #59)
    m = re.match(
        r'^([NSns])\s*(\d{1,3})\s*°?\s*(\d+(?:\.\d+)?)\s*[\'′]?\s*'
        r'([EWew])\s*(\d{1,3})\s*°?\s*(\d+(?:\.\d+)?)\s*[\'′]?\s*$',
        text
    )
    if m:
        lat_h, lat_d, lat_m, lon_h, lon_d, lon_m = m.groups()
        if float(lat_m) >= 60.0 or float(lon_m) >= 60.0:
            return None
        lat = int(lat_d) + float(lat_m) / 60
        lon = int(lon_d) + float(lon_m) / 60
        if lat_h.upper() == "S":
            lat = -lat
        if lon_h.upper() == "W":
            lon = -lon
        return (lat, lon) if _valid_range(lat, lon) else None

    # Plain DMM "N55 47.250 E012 25.000" is already matched by the DMM° branch
    # above (the degree sign and apostrophe are optional there), so no separate
    # branch is needed.

    # ── DMS: "N55° 47' 15.00" E012° 25' 00.00"" ──────────────────────────────
    m = re.match(
        r'^([NSns])\s*(\d{1,3})[°\s]\s*(\d{1,2})[\'′\s]\s*(\d+(?:\.\d+)?)["\s]*'
        r'\s+([EWew])\s*(\d{1,3})[°\s]\s*(\d{1,2})[\'′\s]\s*(\d+(?:\.\d+)?)["\s]*$',
        text
    )
    if m:
        lat_h, lat_d, lat_m, lat_s, lon_h, lon_d, lon_m, lon_s = m.groups()
        if int(lat_m) >= 60 or int(lon_m) >= 60:
            return None
        if float(lat_s) >= 60.0 or float(lon_s) >= 60.0:
            return None
        lat = int(lat_d) + int(lat_m) / 60 + float(lat_s) / 3600
        lon = int(lon_d) + int(lon_m) / 60 + float(lon_s) / 3600
        if lat_h.upper() == "S":
            lat = -lat
        if lon_h.upper() == "W":
            lon = -lon
        return (lat, lon) if _valid_range(lat, lon) else None

    return None

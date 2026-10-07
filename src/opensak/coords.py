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


def _split_dm(abs_val: float) -> tuple[int, float]:
    """Split |degrees| into (whole_degrees, minutes) with minutes rounded to
    3 decimals, carrying into degrees if the rounding pushes minutes to
    60.000 (issue #751 — e.g. 59.999999° must round to 60° 00.000', not
    59° 60.000'; rounding directly in an f-string's :06.3f never checks
    for this overflow).
    """
    deg = int(abs_val)
    minutes = round((abs_val - deg) * 60, 3)
    if minutes >= 60.0:
        minutes -= 60.0
        deg += 1
    return deg, minutes


def _split_dms(abs_val: float) -> tuple[int, int, float]:
    """Split |degrees| into (whole_degrees, whole_minutes, seconds), with
    seconds rounded to 2 decimals and a two-level carry: seconds rounding
    to 60.00 carries into minutes, and minutes then reaching 60 carries
    into degrees (issue #751 — same rounding-overflow bug as _split_dm,
    but seconds can cascade into minutes *and* degrees).
    """
    deg = int(abs_val)
    total_min = (abs_val - deg) * 60
    minutes = int(total_min)
    seconds = round((total_min - minutes) * 60, 2)
    if seconds >= 60.0:
        seconds -= 60.0
        minutes += 1
    if minutes >= 60:
        minutes -= 60
        deg += 1
    return deg, minutes, seconds


def _dd_to_dmm(lat: float, lon: float) -> str:
    """Convert decimal degrees to DMM string (geocaching standard)."""
    lat_h = "N" if lat >= 0 else "S"
    lon_h = "E" if lon >= 0 else "W"
    lat_deg, lat_min = _split_dm(abs(lat))
    lon_deg, lon_min = _split_dm(abs(lon))
    return f"{lat_h}{lat_deg:02d} {lat_min:06.3f}  {lon_h}{lon_deg:03d} {lon_min:06.3f}"


def _dd_to_dms(lat: float, lon: float) -> str:
    """Convert decimal degrees to DMS string."""
    lat_h = "N" if lat >= 0 else "S"
    lon_h = "E" if lon >= 0 else "W"
    lat_deg, lat_min, lat_sec = _split_dms(abs(lat))
    lon_deg, lon_min, lon_sec = _split_dms(abs(lon))
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
        deg, m, s = _split_dms(a)
        return f"{h}{deg:02d}° {m:02d}' {s:05.2f}\""
    # default: DMM (geocaching standard)
    deg, dm_min = _split_dm(a)
    return f"{h}{deg:02d} {dm_min:06.3f}"


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
        deg, m, s = _split_dms(a)
        return f"{h}{deg:03d}° {m:02d}' {s:05.2f}\""
    # default: DMM (geocaching standard)
    deg, dm_min = _split_dm(a)
    return f"{h}{deg:03d} {dm_min:06.3f}"


# ── Parsing ───────────────────────────────────────────────────────────────────

def _valid_range(lat: float, lon: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


# Minute and seconds marks accepted by parse_coords() (#765). Word turns
# ' and " into ’ and ” when coordinates are typed or pasted there.
_MIN_MARK = "['′’]"
_SEC_MARK = "(?:[\"″”]|'')"


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

    Minutes may be marked ' (apostrophe), ′ (prime) or ’ (the right single
    quotation mark Word substitutes), seconds " (quotation mark), ″ (double
    prime), ” or '' (two apostrophes). Issue #765: DD takes one comma or
    whitespace between latitude and longitude, and DMS one seconds mark,
    right after the number.
    """
    import re
    text = text.strip()

    # ── DD: "55.78750, 12.41667" or "55.78750 12.41667" ──────────────────────
    # Issue #765: exactly one comma (spaces around it allowed) or whitespace —
    # not "56.789 ,, ,  ,,,  12.345".
    m = re.match(
        r'^([+-]?\d+\.\d+)(?:\s*,\s*|\s+)([+-]?\d+\.\d+)$', text
    )
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        return (lat, lon) if _valid_range(lat, lon) else None

    # ── DD with hemisphere letters: "N 59.99999 E 12.99999" ──────────────────
    # Issue #751: without this branch, a plain decimal-degree value written
    # with an N/S/E/W hemisphere letter instead of a +/- sign (no separate
    # minutes component at all) fell through to the DMM° branch below.
    # There, regex backtracking let the degrees group (\d{1,3}, normally
    # greedy) give back digits to the minutes group whenever keeping them
    # would leave an unmatchable "." right after — so "59.99999" silently
    # got reinterpreted as degrees=5, minutes=9.99999 instead of being
    # rejected or recognised as decimal degrees, producing wildly wrong
    # coordinates (5.16667° instead of 59.99999°) with no error shown.
    # Checked before the DMM° branch since it's the more specific/exact
    # match for this exact input shape.
    m = re.match(
        r'^([NSns])\s*(\d{1,3}(?:\.\d+)?)\s+([EWew])\s*(\d{1,3}(?:\.\d+)?)\s*$',
        text
    )
    if m:
        lat_h, lat_val, lon_h, lon_val = m.groups()
        lat = float(lat_val)
        lon = float(lon_val)
        if lat_h.upper() == "S":
            lat = -lat
        if lon_h.upper() == "W":
            lon = -lon
        return (lat, lon) if _valid_range(lat, lon) else None

    # ── DMM°: "N 34° 58.088' E 034° 03.281'" (geocaching.com format) ─────────
    # Grads-tegn efter grader, apostrof efter minutter er valgfri (fixes #59)
    m = re.match(
        rf'^([NSns])\s*(\d{{1,3}})\s*°?\s*(\d+(?:\.\d+)?)\s*{_MIN_MARK}?\s*'
        rf'([EWew])\s*(\d{{1,3}})\s*°?\s*(\d+(?:\.\d+)?)\s*{_MIN_MARK}?\s*$',
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
    # Issue #765: at most one seconds mark, right after the number (the old
    # pattern took any number of quotes and spaces), and ″ ” '' as well as ".
    m = re.match(
        rf'^([NSns])\s*(\d{{1,3}})[°\s]\s*(\d{{1,2}})(?:{_MIN_MARK}|\s)\s*'
        rf'(\d+(?:\.\d+)?)(?:{_SEC_MARK}\s*|\s+)'
        rf'([EWew])\s*(\d{{1,3}})[°\s]\s*(\d{{1,2}})(?:{_MIN_MARK}|\s)\s*'
        rf'(\d+(?:\.\d+)?){_SEC_MARK}?$',
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

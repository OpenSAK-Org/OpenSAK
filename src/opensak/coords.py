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

# Issue #767: one coordinate (latitude or longitude) of a hemisphere format.
# The hemisphere letter may come before or after the value, but exactly once
# (checked in _hemisphere_match), and a value never has a sign of its own.
_LAT_H = "[NSns]"
_LON_H = "[EWew]"
# Decimal degrees with an optional degree sign: "59.99999", "35.123°".
_DD_VALUE = r"(\d{1,3}(?:\.\d+)?)\s*°?"
# Degrees and decimal minutes: "55 47.250", "34° 58.088'". Degrees and
# minutes must be split by a degree sign or whitespace, so the regex can't
# hand digits of the degrees to the minutes (the #751 trap).
_DMM_VALUE = rf"(\d{{1,3}})(?:\s*°\s*|\s+)(\d+(?:\.\d+)?)\s*{_MIN_MARK}?"
# Degrees, minutes and seconds: "55° 47' 15.00\"", "32 22 16.56".
_DMS_VALUE = (
    rf"(\d{{1,3}})[°\s]\s*(\d{{1,2}})(?:{_MIN_MARK}|\s)\s*"
    rf"(\d+(?:\.\d+)?){_SEC_MARK}?"
)


def _coordinate_pattern(hemisphere: str, value: str) -> str:
    return rf"(?:({hemisphere})\s*)?{value}(?:\s*({hemisphere}))?"


def _hemisphere_match(
    text: str, value: str,
) -> tuple[str, tuple[str, ...], str, tuple[str, ...]] | None:
    """
    Match *text* as latitude and longitude, each written with *value* and one
    hemisphere letter before or after it, separated by whitespace and/or one
    comma. Returns (lat letter, lat value groups, lon letter, lon value
    groups) — letters upper-case — or None.
    """
    import re
    m = re.match(
        rf"^{_coordinate_pattern(_LAT_H, value)}(\s*,?\s*)"
        rf"{_coordinate_pattern(_LON_H, value)}$",
        text,
    )
    if not m:
        return None
    groups = m.groups()
    n = (len(groups) - 1) // 2           # leading letter, values, trailing letter
    lat_g, lon_g = groups[:n], groups[n + 1:]
    lat_h = _single_hemisphere(lat_g[0], lat_g[-1])
    lon_h = _single_hemisphere(lon_g[0], lon_g[-1])
    if lat_h is None or lon_h is None:
        return None
    # Two numbers with nothing between them ("…32.5115.8…") can't be told
    # apart: something (a letter, a comma, whitespace, a mark) must come
    # between the latitude's last number and the longitude's first. Groups
    # are 1-based: lat's last value is group n - 1, lon's first n + 3.
    if m.end(n - 1) == m.start(n + 3):
        return None
    return lat_h, lat_g[1:-1], lon_h, lon_g[1:-1]


def _single_hemisphere(before: str | None, after: str | None) -> str | None:
    """The coordinate's hemisphere letter, or None unless there is exactly one."""
    if (before is None) == (after is None):
        return None
    return (before or after or "").upper()


def _signed(value: float, hemisphere: str) -> float:
    return -value if hemisphere in ("S", "W") else value


def parse_coords(text: str) -> Coordinate | None:
    """
    Try to parse a coordinate string in any supported format.
    Returns (lat, lon) as decimal degrees, or None if parsing fails or
    the values fall outside valid geographic ranges.

    Accepted formats
    ----------------
    DD  :  55.78750, 12.41667
    DD  :  N 59.99999 E 12.99999          (hemisphere letters)
    DMM :  N55 47.250 E012 25.000
    DMM°:  N 34° 58.088' E 034° 03.281'   (med apostrof)
    DMM°:  N 34° 58.088 E 034° 03.281     (uden apostrof — fixes #59)
    DMM°:  N38° 33.502 W90° 22.774        (uden mellemrum efter hemisphere)
    DMS :  N55° 47' 15.00" E012° 25' 00.00"

    In the hemisphere formats each coordinate has its letter (N/S, E/W)
    either before or after the value — "32.371267S 115.827467E",
    "32° 22.276 S 115° 49.648 E", "S 35.123° 86.543° W" (#767) — but exactly
    once, and never together with a minus sign.

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
    # minutes component at all) fell through to the DMM° branch below and
    # was silently misread as degrees=5, minutes=9.99999. Checked before the
    # DMM branch since it's the more specific match for this input shape.
    hit = _hemisphere_match(text, _DD_VALUE)
    if hit:
        lat_h, (lat_v,), lon_h, (lon_v,) = hit
        lat = _signed(float(lat_v), lat_h)
        lon = _signed(float(lon_v), lon_h)
        return (lat, lon) if _valid_range(lat, lon) else None

    # ── DMM / DMM°: "N55 47.250 E012 25.000", "N 34° 58.088' E 034° 03.281'" ─
    # Grads-tegn efter grader, apostrof efter minutter er valgfri (fixes #59)
    hit = _hemisphere_match(text, _DMM_VALUE)
    if hit:
        lat_h, (lat_d, lat_m), lon_h, (lon_d, lon_m) = hit
        if float(lat_m) >= 60.0 or float(lon_m) >= 60.0:
            return None
        lat = _signed(int(lat_d) + float(lat_m) / 60, lat_h)
        lon = _signed(int(lon_d) + float(lon_m) / 60, lon_h)
        return (lat, lon) if _valid_range(lat, lon) else None

    # ── DMS: "N55° 47' 15.00" E012° 25' 00.00"" ──────────────────────────────
    # Issue #765: at most one seconds mark, right after the number.
    hit = _hemisphere_match(text, _DMS_VALUE)
    if hit:
        lat_h, (lat_d, lat_m, lat_s), lon_h, (lon_d, lon_m, lon_s) = hit
        if int(lat_m) >= 60 or int(lon_m) >= 60:
            return None
        if float(lat_s) >= 60.0 or float(lon_s) >= 60.0:
            return None
        lat = _signed(int(lat_d) + int(lat_m) / 60 + float(lat_s) / 3600, lat_h)
        lon = _signed(int(lon_d) + int(lon_m) / 60 + float(lon_s) / 3600, lon_h)
        return (lat, lon) if _valid_range(lat, lon) else None

    return None

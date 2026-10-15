"""
src/opensak/coord_formats.py — Every coordinate format OpenSAK can read and
write, for the Coordinate Converter and the Lua macro API.

opensak.coords handles the latitude/longitude formats (DD, DMM, DMS) the rest
of the application uses; this module adds the grid and code formats on top:

  utm         32T E 465340 N 5246242
  mgrs        32T MT 65339 46242
  ch1903      683259 / 247015             (Swiss LV03; parse also takes LV95)
  ch1903+     2683259 / 1247015           (Swiss LV95)
  rd          121290 / 487362             (Dutch Rijksdriehoek)
  osgb        TQ 30268 79642              (British National Grid)
  sweref99    N 6580743 E 674572          (Sweden)
  gk          R 4468513 H 5333350         (German Gauss-Krüger)
  olc         8FVC9G9R+F9M                (Open Location Code / Plus Code)
  geohash     u0qj6r5qf
  maidenhead  JN47gi48                    (QTH locator)

parse_any() reads any of them. Without a format it guesses: the latitude /
longitude formats first, then the formats with letters, then bare number
pairs by their value ranges. A national grid only accepts positions that
fall inside its country (AREAS), which tells most bare number pairs apart;
numeric British grid references are only read with fmt="osgb".
"""

from __future__ import annotations

import re
from typing import Callable, NamedTuple, Optional

from opensak import geodesy
from opensak.coords import format_coords, parse_coords
from opensak.utils.types import Coordinate, CoordFormat


class Area(NamedTuple):
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    def contains(self, lat: float, lon: float) -> bool:
        return self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max


class CoordSystem(NamedTuple):
    key: str
    label: str
    format: Callable[[float, float], str]
    parse: Callable[[str], Optional[Coordinate]]
    area: Optional[Area] = None


# Where the national grids are meaningful (generous bounding boxes).
AREAS = {
    "ch1903": Area(45.5, 48.0, 5.5, 11.0),
    "rd": Area(50.5, 54.0, 2.5, 7.5),
    "osgb": Area(49.0, 61.5, -9.0, 2.5),
    "sweref99": Area(54.5, 69.5, 10.0, 24.5),
    "gk": Area(47.0, 55.5, 5.5, 15.5),
}


def _inside(key: str, point: Coordinate) -> Optional[Coordinate]:
    return point if AREAS[key].contains(*point) else None


# ── Number pairs: "683144 / 248115", "E 2683144, N 1248115", "N 6580822 E 674032"

_NUM = r"(\d+(?:\.\d+)?)"
_LABEL = r"(?:([A-Za-z]{1,2})\s*[:=]?\s*)?"
_PAIR_RE = re.compile(
    rf"^{_LABEL}{_NUM}\s*m?\s*(?:[,/;]\s*|\s+){_LABEL}{_NUM}\s*m?$"
)


def _number_pair(text: str, north_labels: frozenset[str] = frozenset({"N", "H", "HW"}),
                 ) -> Optional[tuple[float, float, bool]]:
    """(first, second, north_first) of two numbers with optional axis
    labels. north_first is True when the first label is one of
    *north_labels* (the label names differ between grids)."""
    m = _PAIR_RE.match(text.strip())
    if not m:
        return None
    label1, a, _label2, b = m.groups()
    return float(a), float(b), (label1 or "").upper() in north_labels


# ── Latitude / longitude (DD, DMM, DMS) ─────────────────────────────────────

def _parse_latlon(text: str) -> Optional[Coordinate]:
    point = parse_coords(text)
    if point is None and "," in text:
        point = parse_coords(text.replace(",", " "))
    return point


# ── UTM ──────────────────────────────────────────────────────────────────────

_BANDS = "CDEFGHJKLMNPQRSTUVWX"
_UTM_RE = re.compile(
    rf"^(\d{{1,2}})\s*([{_BANDS}])\s*,?\s*(?:E\s*)?{_NUM}\s*m?\s*[,/;]?\s*"
    rf"(?:N\s*)?{_NUM}\s*m?$",
    re.IGNORECASE,
)


def _parse_utm(text: str) -> Optional[Coordinate]:
    m = _UTM_RE.match(text.strip())
    if not m:
        return None
    zone, band = int(m.group(1)), m.group(2).upper()
    easting, northing = float(m.group(3)), float(m.group(4))
    if not (1 <= zone <= 60 and 100_000 <= easting <= 900_000 and northing <= 10_000_000):
        return None
    point = geodesy.from_utm(zone, band >= "N", easting, northing)
    # "32N 465339 5246242" may mean hemisphere N (or S), not band N/S.
    if band in "NS" and abs(_BANDS.index(geodesy.utm_band(point[0])) - _BANDS.index(band)) > 1:
        point = geodesy.from_utm(zone, band == "N", easting, northing)
    return point if -80.5 <= point[0] <= 84.5 else None


# ── MGRS ─────────────────────────────────────────────────────────────────────

_MGRS_COLS = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")
_MGRS_ROWS = "ABCDEFGHJKLMNPQRSTUV"
_MGRS_RE = re.compile(
    rf"^(\d{{1,2}})\s*([{_BANDS}])\s*([A-HJ-NP-Z])\s*([A-HJ-NP-V])\s*(\d*)\s*(\d*)$",
    re.IGNORECASE,
)


def format_mgrs(lat: float, lon: float) -> str:
    """E.g. "32T MT 65339 46242" (1 m precision)."""
    zone, band, easting, northing = geodesy.to_utm(lat, lon)
    col = _MGRS_COLS[(zone - 1) % 3][int(easting // 100_000) - 1]
    row_i = int(northing // 100_000) % 20
    if zone % 2 == 0:
        row_i = (row_i + 5) % 20
    return (f"{zone}{band} {col}{_MGRS_ROWS[row_i]} "
            f"{int(easting % 100_000):05d} {int(northing % 100_000):05d}")


def _parse_mgrs(text: str) -> Optional[Coordinate]:
    m = _MGRS_RE.match(text.strip())
    if not m:
        return None
    zone, band = int(m.group(1)), m.group(2).upper()
    col, row = m.group(3).upper(), m.group(4).upper()
    digits_e, digits_n = m.group(5), m.group(6)
    if not digits_n:                          # "6533946242" in one block
        half = len(digits_e) // 2
        digits_e, digits_n = digits_e[:half], digits_e[half:]
    if len(digits_e) != len(digits_n) or len(digits_e) > 5 or not 1 <= zone <= 60:
        return None
    cols = _MGRS_COLS[(zone - 1) % 3]
    if col not in cols:
        return None
    scale = 10 ** (5 - len(digits_e))
    e100k = (cols.index(col) + 1) * 100_000
    row_i = _MGRS_ROWS.index(row)
    if zone % 2 == 0:
        row_i = (row_i - 5) % 20
    n100k = row_i * 100_000
    # Centre of the referenced cell, so formatting it again (which truncates)
    # gives back the same digits.
    easting = e100k + int(digits_e or 0) * scale + scale / 2
    northing = n100k + int(digits_n or 0) * scale + scale / 2
    # The row letters repeat every 2000 km: pick the repeat that lands in
    # the latitude band.
    band_lat = -80 + 8 * _BANDS.index(band)
    band_north = geodesy.to_utm(band_lat, zone * 6 - 183, zone)[3]
    band_north = band_north // 100_000 * 100_000
    while northing < band_north:
        northing += 2_000_000
    return geodesy.from_utm(zone, band >= "N", easting, northing)


# ── Swiss grid ───────────────────────────────────────────────────────────────

def _parse_ch1903(text: str) -> Optional[Coordinate]:
    # Swiss order is east (y) first; x is north. Unlike the other grids the
    # numbers are not sorted: "121290 / 487362" is a Dutch RD position,
    # and the same numbers swapped would land near Geneva.
    pair = _number_pair(text, frozenset({"N", "X"}))
    if pair is None:
        return None
    a, b, north_first = pair
    east, north = (b, a) if north_first else (a, b)
    if east < north:
        return None
    lv95 = east >= 1_000_000
    if lv95 != (north >= 1_000_000):
        return None
    return _inside("ch1903", geodesy.from_ch1903(east, north))


# ── Dutch grid ───────────────────────────────────────────────────────────────

def format_rd(lat: float, lon: float) -> str:
    x, y = geodesy.to_rd(lat, lon)
    return f"{x:.0f} / {y:.0f}"


def _parse_rd(text: str) -> Optional[Coordinate]:
    pair = _number_pair(text)
    if pair is None:
        return None
    x, y = sorted(pair[:2])                          # Y is always larger
    if not (0 <= x <= 300_000 and 280_000 <= y <= 640_000):
        return None
    return _inside("rd", geodesy.from_rd(x, y))


# ── British National Grid ────────────────────────────────────────────────────

_OSGB_LETTERS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"   # no I
_OSGB_RE = re.compile(r"^([HJNOST])([A-HJ-Z])\s*(\d*)\s*(\d*)$", re.IGNORECASE)


def format_osgb(lat: float, lon: float) -> str:
    """E.g. "TQ 30268 79642" (1 m precision)."""
    easting, northing = geodesy.to_osgb(lat, lon)
    if not (0 <= easting < 700_000 and 0 <= northing < 1_300_000):
        raise ValueError("outside the British National Grid")
    e100k, n100k = int(easting // 100_000), int(northing // 100_000)
    l1 = (19 - n100k) - (19 - n100k) % 5 + (e100k + 10) // 5
    l2 = (19 - n100k) * 5 % 25 + e100k % 5
    return (f"{_OSGB_LETTERS[l1]}{_OSGB_LETTERS[l2]} "
            f"{int(easting % 100_000):05d} {int(northing % 100_000):05d}")


def _parse_osgb_letters(text: str) -> Optional[Coordinate]:
    m = _OSGB_RE.match(text.strip())
    if not m:
        return None
    l1 = _OSGB_LETTERS.index(m.group(1).upper())
    l2 = _OSGB_LETTERS.index(m.group(2).upper())
    digits_e, digits_n = m.group(3), m.group(4)
    if not digits_n:
        half = len(digits_e) // 2
        digits_e, digits_n = digits_e[:half], digits_e[half:]
    if len(digits_e) != len(digits_n) or len(digits_e) > 5:
        return None
    scale = 10 ** (5 - len(digits_e))
    e100k = (l1 - 2) % 5 * 5 + l2 % 5
    n100k = 19 - l1 // 5 * 5 - l2 // 5
    easting = e100k * 100_000 + int(digits_e or 0) * scale + scale / 2
    northing = n100k * 100_000 + int(digits_n or 0) * scale + scale / 2
    return _inside("osgb", geodesy.from_osgb(easting, northing))


def _parse_osgb(text: str) -> Optional[Coordinate]:
    """Grid letters, or plain easting / northing in metres."""
    point = _parse_osgb_letters(text)
    if point is not None:
        return point
    pair = _number_pair(text)
    if pair is None:
        return None
    a, b, north_first = pair
    easting, northing = (b, a) if north_first else (a, b)
    if not (0 <= easting < 700_000 and 0 <= northing < 1_300_000):
        return None
    return _inside("osgb", geodesy.from_osgb(easting, northing))


# ── SWEREF 99 TM ─────────────────────────────────────────────────────────────

def format_sweref99(lat: float, lon: float) -> str:
    northing, easting = geodesy.to_sweref99(lat, lon)
    return f"N {northing:.0f} E {easting:.0f}"


def _parse_sweref99(text: str) -> Optional[Coordinate]:
    pair = _number_pair(text)
    if pair is None:
        return None
    easting, northing = sorted(pair[:2])            # N is always larger
    if not (6_000_000 <= northing <= 7_800_000 and 150_000 <= easting <= 1_000_000):
        return None
    return _inside("sweref99", geodesy.from_sweref99(northing, easting))


# ── Gauss-Krüger ─────────────────────────────────────────────────────────────

def format_gk(lat: float, lon: float) -> str:
    rechts, hoch = geodesy.to_gauss_krueger(lat, lon)
    return f"R {rechts:.0f} H {hoch:.0f}"


def _parse_gk(text: str) -> Optional[Coordinate]:
    pair = _number_pair(text)
    if pair is None:
        return None
    a, b, north_first = pair
    rechts, hoch = (b, a) if north_first else (a, b)
    if not (1_000_000 <= rechts < 10_000_000 and 5_000_000 <= hoch <= 6_300_000):
        return None
    return _inside("gk", geodesy.from_gauss_krueger(rechts, hoch))


# ── Open Location Code (Plus Codes) ──────────────────────────────────────────

_OLC_ALPHABET = "23456789CFGHJMPQRVWX"
_OLC_LENGTH = 11                       # 10 digits ≈ 14 m, 11 ≈ 3 m
_OLC_RE = re.compile(r"^[23456789CFGHJMPQRVWX]{8}\+(?:[23456789CFGHJMPQRVWX]{2,7})?$")


def format_olc(lat: float, lon: float) -> str:
    """E.g. "8FVC9G9R+F9M" (3 × 3 m cell)."""
    lat = min(max(lat, -90.0), 90.0)
    lon = (lon + 180.0) % 360.0 - 180.0
    lat_val = int(round((lat + 90) * 25_000_000, 6))
    lon_val = int(round((lon + 180) * 8_192_000, 6))
    lat_val = min(lat_val, 180 * 25_000_000 - 1)
    code = ""
    for _ in range(5):                 # grid section, 5 × 4 cells
        code = _OLC_ALPHABET[(lat_val % 5) * 4 + lon_val % 4] + code
        lat_val //= 5
        lon_val //= 4
    for _ in range(5):                 # pair section, 20 × 20 cells
        code = _OLC_ALPHABET[lat_val % 20] + _OLC_ALPHABET[lon_val % 20] + code
        lat_val //= 20
        lon_val //= 20
    code = code[:8] + "+" + code[8:]
    return code[:_OLC_LENGTH + 1]


def _parse_olc(text: str) -> Optional[Coordinate]:
    """Centre of a full Plus Code. Short codes ("9G8F+6X Zürich") need a
    reference location and are not accepted."""
    code = text.strip().upper()
    if not _OLC_RE.match(code):
        return None
    digits = code.replace("+", "")
    if _OLC_ALPHABET.index(digits[0]) > 8 or _OLC_ALPHABET.index(digits[1]) > 17:
        return None
    lat, lon, size = -90.0, -180.0, 20.0
    pair_digits = digits[:10]
    if len(pair_digits) % 2:
        return None
    for i in range(0, len(pair_digits), 2):
        lat += _OLC_ALPHABET.index(pair_digits[i]) * size
        lon += _OLC_ALPHABET.index(pair_digits[i + 1]) * size
        if i + 2 < len(pair_digits):
            size /= 20
    lat_size = lon_size = size
    for ch in digits[10:]:
        lat_size /= 5
        lon_size /= 4
        idx = _OLC_ALPHABET.index(ch)
        lat += idx // 4 * lat_size
        lon += idx % 4 * lon_size
    return min(lat + lat_size / 2, 90.0), lon + lon_size / 2


# ── Geohash ──────────────────────────────────────────────────────────────────

_GEOHASH_ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"
_GEOHASH_LENGTH = 9                    # ± 2.4 m


def format_geohash(lat: float, lon: float) -> str:
    """E.g. "u0qj6r5qf"."""
    lat_lo, lat_hi, lon_lo, lon_hi = -90.0, 90.0, -180.0, 180.0
    bits = []
    for i in range(_GEOHASH_LENGTH * 5):
        if i % 2 == 0:
            mid = (lon_lo + lon_hi) / 2
            bits.append(lon >= mid)
            lon_lo, lon_hi = (mid, lon_hi) if lon >= mid else (lon_lo, mid)
        else:
            mid = (lat_lo + lat_hi) / 2
            bits.append(lat >= mid)
            lat_lo, lat_hi = (mid, lat_hi) if lat >= mid else (lat_lo, mid)
    return "".join(
        _GEOHASH_ALPHABET[int("".join("1" if b else "0" for b in bits[i:i + 5]), 2)]
        for i in range(0, len(bits), 5)
    )


def _parse_geohash(text: str) -> Optional[Coordinate]:
    code = text.strip().lower()
    if not 1 <= len(code) <= 12 or any(ch not in _GEOHASH_ALPHABET for ch in code):
        return None
    lat_lo, lat_hi, lon_lo, lon_hi = -90.0, 90.0, -180.0, 180.0
    even = True
    for ch in code:
        value = _GEOHASH_ALPHABET.index(ch)
        for shift in range(4, -1, -1):
            bit = (value >> shift) & 1
            if even:
                mid = (lon_lo + lon_hi) / 2
                lon_lo, lon_hi = (mid, lon_hi) if bit else (lon_lo, mid)
            else:
                mid = (lat_lo + lat_hi) / 2
                lat_lo, lat_hi = (mid, lat_hi) if bit else (lat_lo, mid)
            even = not even
    return (lat_lo + lat_hi) / 2, (lon_lo + lon_hi) / 2


def _detect_geohash(text: str) -> Optional[Coordinate]:
    """Geohash for auto-detection: words such as "berg" are valid geohashes
    too, so ask for 5+ characters mixing letters and digits."""
    code = text.strip()
    if len(code) < 5 or not any(c.isdigit() for c in code) or not any(c.isalpha() for c in code):
        return None
    return _parse_geohash(code)


# ── Maidenhead locator ───────────────────────────────────────────────────────

_MAIDENHEAD_RE = re.compile(
    r"^([A-R]{2})(\d{2})(?:([A-X]{2})(?:(\d{2})([A-X]{2})?)?)?$", re.IGNORECASE
)


def format_maidenhead(lat: float, lon: float) -> str:
    """E.g. "JN47gi48" (extended square, about 1 × 0.5 km)."""
    lon = min(max(lon + 180, 0.0), 359.999999)
    lat = min(max(lat + 90, 0.0), 179.999999)
    a, lon = divmod(lon, 20)
    b, lat = divmod(lat, 10)
    c, lon = divmod(lon, 2)
    d, lat = divmod(lat, 1)
    e, lon = divmod(lon, 2 / 24)
    f, lat = divmod(lat, 1 / 24)
    g = lon // (2 / 240)
    h = lat // (1 / 240)
    return (chr(65 + int(a)) + chr(65 + int(b)) + f"{int(c)}{int(d)}"
            + chr(97 + int(e)) + chr(97 + int(f)) + f"{int(g)}{int(h)}")


def _parse_maidenhead(text: str) -> Optional[Coordinate]:
    m = _MAIDENHEAD_RE.match(text.strip())
    if not m:
        return None
    field, square, sub, ext, ext2 = m.groups()
    field = field.upper()
    lon: float = (ord(field[0]) - 65) * 20 - 180 + int(square[0]) * 2
    lat: float = (ord(field[1]) - 65) * 10 - 90 + int(square[1])
    w, h = 2.0, 1.0
    if sub:
        sub = sub.lower()
        w, h = w / 24, h / 24
        lon += (ord(sub[0]) - 97) * w
        lat += (ord(sub[1]) - 97) * h
    if ext:
        w, h = w / 10, h / 10
        lon += int(ext[0]) * w
        lat += int(ext[1]) * h
    if ext2:
        ext2 = ext2.lower()
        w, h = w / 24, h / 24
        lon += (ord(ext2[0]) - 97) * w
        lat += (ord(ext2[1]) - 97) * h
    return lat + h / 2, lon + w / 2


# ── Registry ─────────────────────────────────────────────────────────────────

def _ch_format(lv95: bool) -> Callable[[float, float], str]:
    return lambda lat, lon: geodesy.format_ch1903(lat, lon, lv95=lv95)


# Output formats, in the converter's order.
SYSTEMS: dict[str, CoordSystem] = {s.key: s for s in (
    CoordSystem("dmm", "DMM", lambda la, lo: format_coords(la, lo, CoordFormat.DMM), _parse_latlon),
    CoordSystem("dms", "DMS", lambda la, lo: format_coords(la, lo, CoordFormat.DMS), _parse_latlon),
    CoordSystem("dd", "DD", lambda la, lo: format_coords(la, lo, CoordFormat.DD), _parse_latlon),
    CoordSystem("utm", "UTM", geodesy.format_utm, _parse_utm),
    CoordSystem("mgrs", "MGRS", format_mgrs, _parse_mgrs),
    CoordSystem("ch1903", "CH1903 (LV03)", _ch_format(False), _parse_ch1903, AREAS["ch1903"]),
    CoordSystem("ch1903+", "CH1903+ (LV95)", _ch_format(True), _parse_ch1903, AREAS["ch1903"]),
    CoordSystem("rd", "RD (NL)", format_rd, _parse_rd, AREAS["rd"]),
    CoordSystem("osgb", "OSGB (UK)", format_osgb, _parse_osgb, AREAS["osgb"]),
    CoordSystem("sweref99", "SWEREF 99 TM", format_sweref99, _parse_sweref99, AREAS["sweref99"]),
    CoordSystem("gk", "Gauss-Krüger", format_gk, _parse_gk, AREAS["gk"]),
    CoordSystem("olc", "Plus Code", format_olc, _parse_olc),
    CoordSystem("geohash", "Geohash", format_geohash, _parse_geohash),
    CoordSystem("maidenhead", "Maidenhead", format_maidenhead, _parse_maidenhead),
)}

# Input formats the converter offers (DD/DMM/DMS share one parser, and so
# do LV03/LV95).
INPUT_KEYS = ("latlon", "utm", "mgrs", "ch1903", "rd", "osgb", "sweref99",
              "gk", "olc", "geohash", "maidenhead")

_ALIASES = {
    "lv03": "ch1903", "lv95": "ch1903+", "bng": "osgb", "sweref": "sweref99",
    "gauss-krueger": "gk", "gauss-kruger": "gk", "pluscode": "olc",
    "plus code": "olc", "qth": "maidenhead",
}

# Auto-detection order: unambiguous shapes first, bare number pairs last.
_DETECT: tuple[tuple[str, Callable[[str], Optional[Coordinate]]], ...] = (
    ("latlon", _parse_latlon),
    ("utm", _parse_utm),
    ("mgrs", _parse_mgrs),
    ("maidenhead", _parse_maidenhead),     # "NN17" is a locator, not OSGB
    ("osgb", _parse_osgb_letters),
    ("olc", _parse_olc),
    ("geohash", _detect_geohash),
    ("ch1903", _parse_ch1903),
    ("rd", _parse_rd),
    ("sweref99", _parse_sweref99),
    ("gk", _parse_gk),
)


def normalize_key(name: str) -> Optional[str]:
    """The registry key for a format name or alias (case-insensitive), or
    None. "latlon" (input only) stands for DD, DMM and DMS."""
    key = name.strip().lower()
    key = _ALIASES.get(key, key)
    return key if key in SYSTEMS or key == "latlon" else None


def format_as(lat: float, lon: float, key: str) -> str:
    """*lat*, *lon* in format *key* (a registry key or alias). Raises
    ValueError for an unknown format or a point the format cannot show."""
    norm = normalize_key(key)
    if norm is None or norm == "latlon":
        raise ValueError(f"unknown coordinate format {key!r}")
    return SYSTEMS[norm].format(lat, lon)


def in_area(key: str, lat: float, lon: float) -> bool:
    """False when *key* is a national grid and the point is outside it."""
    area = SYSTEMS[key].area
    return area is None or area.contains(lat, lon)


def parse_any(text: str, fmt: Optional[str] = None) -> Optional[tuple[float, float, str]]:
    """(lat, lon, detected format key) of *text*, or None.

    With *fmt* (a registry key or alias) only that format is tried;
    otherwise the format is detected. The detected key is "latlon" for
    DD, DMM and DMS.
    """
    text = text.strip()
    if not text:
        return None
    parsers = _DETECT
    if fmt is not None:
        key = normalize_key(fmt)
        if key is None:
            raise ValueError(f"unknown coordinate format {fmt!r}")
        if key in ("dd", "dmm", "dms"):
            key = "latlon"
        elif key == "ch1903+":
            key = "ch1903"
        parsers = ((key, _parse_latlon if key == "latlon" else SYSTEMS[key].parse),)
    for key, parse in parsers:
        try:
            point = parse(text)
        except (ValueError, ZeroDivisionError, OverflowError):
            point = None
        if point is not None and -90.0 <= point[0] <= 90.0 and -180.0 <= point[1] <= 180.0:
            return point[0], point[1], key
    return None

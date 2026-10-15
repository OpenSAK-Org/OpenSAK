"""
src/opensak/geodesy.py — Coordinate math without Qt.

  distance_km / bearing / project / midpoint
      Great-circle math on a sphere with the WGS-84 mean radius — the same
      formulas as the Distance & bearing, Projection and Midpoint dialogs.
  to_utm / from_utm / format_utm
      WGS-84 ↔ UTM (Krüger series, sub-millimetre accuracy inside a zone).
  to_ch1903 / from_ch1903 / format_ch1903
      WGS-84 ↔ Swiss grid LV03 (CH1903) or LV95 (CH1903+), using swisstopo's
      approximate formulas (about 1 m accuracy in and around Switzerland).
  to_rd / from_rd
      WGS-84 ↔ Dutch RD grid (approximation, about 1 m in the Netherlands).
  to_osgb / from_osgb
      WGS-84 ↔ British National Grid (OSGB36 via a Helmert shift, ~5 m).
  to_sweref99 / from_sweref99
      WGS-84 ↔ SWEREF 99 TM (Sweden).
  to_gauss_krueger / from_gauss_krueger
      WGS-84 ↔ German Gauss-Krüger (DHDN via a Helmert shift, a few metres).

String formats built on these live in opensak.coord_formats.
"""

from __future__ import annotations

import math
from typing import NamedTuple

from opensak.utils.constants import EARTH_RADIUS_M

_EARTH_RADIUS_KM = EARTH_RADIUS_M / 1000


# ── Great circle ─────────────────────────────────────────────────────────────

def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres (haversine)."""
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    dlat = lat2r - lat1r
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2 in degrees (0 = North, clockwise)."""
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _normalize_lon(lon: float) -> float:
    return (lon + 540) % 360 - 180


def project(lat: float, lon: float, bearing_deg: float, dist_km: float) -> tuple[float, float]:
    """The point *dist_km* away from (lat, lon) in direction *bearing_deg*."""
    latr, lonr = math.radians(lat), math.radians(lon)
    brng = math.radians(bearing_deg)
    d_r = dist_km / _EARTH_RADIUS_KM
    lat2 = math.asin(
        math.sin(latr) * math.cos(d_r) + math.cos(latr) * math.sin(d_r) * math.cos(brng)
    )
    lon2 = lonr + math.atan2(
        math.sin(brng) * math.sin(d_r) * math.cos(latr),
        math.cos(d_r) - math.sin(latr) * math.sin(lat2),
    )
    return math.degrees(lat2), _normalize_lon(math.degrees(lon2))


def midpoint(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    """The point halfway along the great circle between two points."""
    lat1r, lon1r = math.radians(lat1), math.radians(lon1)
    lat2r, lon2r = math.radians(lat2), math.radians(lon2)
    x = (math.cos(lat1r) * math.cos(lon1r) + math.cos(lat2r) * math.cos(lon2r)) / 2
    y = (math.cos(lat1r) * math.sin(lon1r) + math.cos(lat2r) * math.sin(lon2r)) / 2
    z = (math.sin(lat1r) + math.sin(lat2r)) / 2
    lat_m = math.atan2(z, math.hypot(x, y))
    return math.degrees(lat_m), math.degrees(math.atan2(y, x))


# ── Ellipsoids and datums ────────────────────────────────────────────────────

class Ellipsoid(NamedTuple):
    a: float    # semi-major axis in metres
    f: float    # flattening


WGS84 = Ellipsoid(6_378_137.0, 1 / 298.257223563)
GRS80 = Ellipsoid(6_378_137.0, 1 / 298.257222101)
AIRY_1830 = Ellipsoid(6_377_563.396, 1 / 299.3249646)
BESSEL_1841 = Ellipsoid(6_377_397.155, 1 / 299.1528128)


class Helmert(NamedTuple):
    """Seven-parameter shift from WGS-84 to a local datum (position-vector
    convention): translations in metres, rotations in arcseconds, scale in
    ppm."""
    tx: float
    ty: float
    tz: float
    rx: float
    ry: float
    rz: float
    s: float


# WGS-84 → OSGB36 (Ordnance Survey, "A guide to coordinate systems in Great
# Britain"; about 5 m accuracy) and WGS-84 → DHDN/Potsdam (EPSG:1777
# inverted; a few metres in Germany).
_TO_OSGB36 = Helmert(-446.448, 125.157, -542.060, -0.1502, -0.2470, -0.8421, 20.4894)
_TO_DHDN = Helmert(-598.1, -73.7, -418.2, -0.202, -0.045, 2.455, -6.7)


def _to_ecef(lat: float, lon: float, ell: Ellipsoid) -> tuple[float, float, float]:
    phi, lam = math.radians(lat), math.radians(lon)
    e2 = ell.f * (2 - ell.f)
    nu = ell.a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    return (nu * math.cos(phi) * math.cos(lam),
            nu * math.cos(phi) * math.sin(lam),
            (1 - e2) * nu * math.sin(phi))


def _from_ecef(x: float, y: float, z: float, ell: Ellipsoid) -> tuple[float, float]:
    e2 = ell.f * (2 - ell.f)
    p = math.hypot(x, y)
    phi = math.atan2(z, p * (1 - e2))
    for _ in range(10):
        nu = ell.a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
        phi = math.atan2(z + e2 * nu * math.sin(phi), p)
    return math.degrees(phi), math.degrees(math.atan2(y, x))


def _shift_datum(lat: float, lon: float, src: Ellipsoid, dst: Ellipsoid,
                 h: Helmert, inverse: bool = False) -> tuple[float, float]:
    """Move a point from *src* to *dst* with *h* — or with its inverse,
    which for rotations this small is the negated parameter set."""
    sign = -1.0 if inverse else 1.0
    x, y, z = _to_ecef(lat, lon, src)
    sec = math.pi / (180 * 3600)
    rx, ry, rz = (sign * r * sec for r in (h.rx, h.ry, h.rz))
    s = 1 + sign * h.s * 1e-6
    x2 = sign * h.tx + s * x - rz * y + ry * z
    y2 = sign * h.ty + rz * x + s * y - rx * z
    z2 = sign * h.tz - ry * x + rx * y + s * z
    return _from_ecef(x2, y2, z2, dst)


# ── Transverse Mercator (Krüger series, sub-millimetre inside a zone) ────────

class _TM(NamedTuple):
    ell: Ellipsoid
    lon0: float             # central meridian in degrees
    k0: float               # scale factor on the central meridian
    false_e: float
    false_n: float
    lat0: float = 0.0       # latitude of the true origin in degrees


def _tm_series(ell: Ellipsoid):
    n = ell.f / (2 - ell.f)
    big_a = ell.a / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64)
    alpha = (
        n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16 + 41 * n ** 4 / 180,
        13 * n ** 2 / 48 - 3 * n ** 3 / 5 + 557 * n ** 4 / 1440,
        61 * n ** 3 / 240 - 103 * n ** 4 / 140,
        49561 * n ** 4 / 161280,
    )
    beta = (
        n / 2 - 2 * n ** 2 / 3 + 37 * n ** 3 / 96 - n ** 4 / 360,
        n ** 2 / 48 + n ** 3 / 15 - 437 * n ** 4 / 1440,
        17 * n ** 3 / 480 - 37 * n ** 4 / 840,
        4397 * n ** 4 / 161280,
    )
    delta = (
        2 * n - 2 * n ** 2 / 3 - 2 * n ** 3 + 116 * n ** 4 / 45,
        7 * n ** 2 / 3 - 8 * n ** 3 / 5 - 227 * n ** 4 / 45,
        56 * n ** 3 / 15 - 136 * n ** 4 / 35,
        4279 * n ** 4 / 630,
    )
    return big_a, alpha, beta, delta


def _meridian_arc(lat0: float, ell: Ellipsoid) -> float:
    """Unscaled meridian distance from the equator to *lat0*."""
    if lat0 == 0.0:
        return 0.0
    big_a, alpha, _, _ = _tm_series(ell)
    e = math.sqrt(ell.f * (2 - ell.f))
    phi = math.radians(lat0)
    xi = math.atan(math.sinh(math.atanh(math.sin(phi)) - e * math.atanh(e * math.sin(phi))))
    return big_a * (xi + sum(a * math.sin(2 * j * xi) for j, a in enumerate(alpha, 1)))


def _tm_forward(lat: float, lon: float, tm: _TM) -> tuple[float, float]:
    """(easting, northing) of a point given on *tm*'s ellipsoid."""
    big_a, alpha, _, _ = _tm_series(tm.ell)
    e = math.sqrt(tm.ell.f * (2 - tm.ell.f))
    phi = math.radians(lat)
    lam = math.radians(_normalize_lon(lon - tm.lon0))
    t = math.sinh(math.atanh(math.sin(phi)) - e * math.atanh(e * math.sin(phi)))
    xi = math.atan2(t, math.cos(lam))
    eta = math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    x = eta + sum(a * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
                  for j, a in enumerate(alpha, 1))
    y = xi + sum(a * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
                 for j, a in enumerate(alpha, 1))
    easting = tm.false_e + tm.k0 * big_a * x
    northing = tm.false_n + tm.k0 * (big_a * y - _meridian_arc(tm.lat0, tm.ell))
    return easting, northing


def _tm_inverse(easting: float, northing: float, tm: _TM) -> tuple[float, float]:
    """(lat, lon) on *tm*'s ellipsoid of a grid position."""
    big_a, _, beta, delta = _tm_series(tm.ell)
    xi = ((northing - tm.false_n) / tm.k0 + _meridian_arc(tm.lat0, tm.ell)) / big_a
    eta = (easting - tm.false_e) / (tm.k0 * big_a)
    xi1 = xi - sum(b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
                   for j, b in enumerate(beta, 1))
    eta1 = eta - sum(b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
                     for j, b in enumerate(beta, 1))
    chi = math.asin(max(-1.0, min(1.0, math.sin(xi1) / math.cosh(eta1))))
    phi = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(delta, 1))
    lam = math.atan2(math.sinh(eta1), math.cos(xi1))
    return math.degrees(phi), _normalize_lon(tm.lon0 + math.degrees(lam))


# ── UTM ──────────────────────────────────────────────────────────────────────

_UTM_K0 = 0.9996
_UTM_BANDS = "CDEFGHJKLMNPQRSTUVWX"


def _utm_zone(lat: float, lon: float) -> int:
    zone = int((lon + 180) // 6) + 1
    zone = min(zone, 60)
    # Exceptions around Norway and Svalbard
    if 56 <= lat < 64 and 3 <= lon < 12:
        return 32
    if 72 <= lat < 84 and lon >= 0:
        if lon < 9:
            return 31
        if lon < 21:
            return 33
        if lon < 33:
            return 35
        if lon < 42:
            return 37
    return zone


def _utm_projection(zone: int, northern: bool) -> _TM:
    return _TM(WGS84, zone * 6 - 183, _UTM_K0, 500_000.0,
               0.0 if northern else 10_000_000.0)


def utm_band(lat: float) -> str:
    """The UTM latitude band letter (C–X) of *lat*."""
    return _UTM_BANDS[max(0, min(int((lat + 80) // 8), len(_UTM_BANDS) - 1))]


def to_utm(lat: float, lon: float, zone: int | None = None) -> tuple[int, str, float, float]:
    """(zone, latitude band, easting, northing) in metres.

    Raises ValueError outside 80°S–84°N, where UTM is not defined (polar
    areas use UPS instead). *zone* forces a zone instead of the standard one.
    """
    if not -80.0 <= lat <= 84.0:
        raise ValueError("UTM is only defined between 80°S and 84°N")
    zone = zone or _utm_zone(lat, lon)
    easting, northing = _tm_forward(lat, lon, _utm_projection(zone, lat >= 0))
    return zone, utm_band(lat), easting, northing


def from_utm(zone: int, northern: bool, easting: float, northing: float) -> tuple[float, float]:
    """(lat, lon) of a UTM position. Raises ValueError for an invalid zone."""
    if not 1 <= zone <= 60:
        raise ValueError(f"UTM zone must be 1–60, got {zone}")
    return _tm_inverse(easting, northing, _utm_projection(zone, northern))


def format_utm(lat: float, lon: float) -> str:
    """E.g. "32T E 465123 N 5247123"."""
    zone, band, easting, northing = to_utm(lat, lon)
    return f"{zone}{band} E {easting:.0f} N {northing:.0f}"


# ── Swiss grid ───────────────────────────────────────────────────────────────

def to_ch1903(lat: float, lon: float, lv95: bool = False) -> tuple[float, float]:
    """(east, north) in metres — LV03 (y, x), or LV95 (E, N) with *lv95*."""
    phi = (lat * 3600 - 169_028.66) / 10_000
    lam = (lon * 3600 - 26_782.5) / 10_000
    east = (600_072.37 + 211_455.93 * lam - 10_938.51 * lam * phi
            - 0.36 * lam * phi ** 2 - 44.54 * lam ** 3)
    north = (200_147.07 + 308_807.95 * phi + 3_745.25 * lam ** 2
             + 76.63 * phi ** 2 - 194.56 * lam ** 2 * phi + 119.79 * phi ** 3)
    if lv95:
        east += 2_000_000.0
        north += 1_000_000.0
    return east, north


def from_ch1903(east: float, north: float) -> tuple[float, float]:
    """(lat, lon) of a Swiss grid position — LV95 when *east* has seven
    digits, LV03 otherwise (swisstopo's approximate formulas, about 1 m)."""
    if east >= 1_000_000:
        east -= 2_000_000.0
        north -= 1_000_000.0
    y = (east - 600_000) / 1_000_000
    x = (north - 200_000) / 1_000_000
    lam = (2.6779094 + 4.728982 * y + 0.791484 * y * x
           + 0.1306 * y * x ** 2 - 0.0436 * y ** 3)
    phi = (16.9023892 + 3.238272 * x - 0.270978 * y ** 2
           - 0.002528 * x ** 2 - 0.0447 * y ** 2 * x - 0.0140 * x ** 3)
    return phi * 100 / 36, lam * 100 / 36


def format_ch1903(lat: float, lon: float, lv95: bool = False) -> str:
    """E.g. "683144 / 248115" (LV03) or "2683144 / 1248115" (LV95)."""
    east, north = to_ch1903(lat, lon, lv95)
    return f"{east:.0f} / {north:.0f}"


# ── Dutch grid (Rijksdriehoeksstelsel, EPSG:28992) ───────────────────────────
# Schreutelkamp & Strang van Hees' approximation (about 1 m in and around
# the Netherlands). Terms are (power of dφ or dX, power of dλ or dY, coeff).

_RD_LAT0, _RD_LON0 = 52.15517440, 5.38720621
_RD_X = ((0, 1, 190094.945), (1, 1, -11832.228), (2, 1, -114.221),
         (0, 3, -32.391), (1, 0, -0.705), (3, 1, -2.340), (1, 3, -0.608),
         (0, 2, -0.008), (2, 3, 0.148))
_RD_Y = ((1, 0, 309056.544), (0, 2, 3638.893), (2, 0, 73.077),
         (1, 2, -157.984), (3, 0, 59.788), (0, 1, 0.433), (2, 2, -6.439),
         (1, 1, -0.032), (0, 4, 0.092), (1, 4, -0.054))
_RD_LAT = ((0, 1, 3235.65389), (2, 0, -32.58297), (0, 2, -0.24750),
           (2, 1, -0.84978), (0, 3, -0.06550), (2, 2, -0.01709),
           (1, 0, -0.00738), (4, 0, 0.00530), (2, 3, -0.00039),
           (4, 1, 0.00033), (1, 1, -0.00012))
_RD_LON = ((1, 0, 5260.52916), (1, 1, 105.94684), (1, 2, 2.45656),
           (3, 0, -0.81885), (1, 3, 0.05594), (3, 1, -0.05607),
           (0, 1, 0.01199), (3, 2, -0.00256), (1, 4, 0.00128),
           (0, 2, 0.00022), (2, 0, -0.00022), (5, 0, 0.00026))


def _poly(terms, a: float, b: float) -> float:
    return sum(c * a ** p * b ** q for p, q, c in terms)


def to_rd(lat: float, lon: float) -> tuple[float, float]:
    """(X, Y) in metres on the Dutch RD grid."""
    dphi = 0.36 * (lat - _RD_LAT0)
    dlam = 0.36 * (lon - _RD_LON0)
    return 155_000 + _poly(_RD_X, dphi, dlam), 463_000 + _poly(_RD_Y, dphi, dlam)


def from_rd(x: float, y: float) -> tuple[float, float]:
    """(lat, lon) of a Dutch RD position."""
    dx = (x - 155_000) * 1e-5
    dy = (y - 463_000) * 1e-5
    return (_RD_LAT0 + _poly(_RD_LAT, dx, dy) / 3600,
            _RD_LON0 + _poly(_RD_LON, dx, dy) / 3600)


# ── British National Grid (OSGB36, EPSG:27700) ───────────────────────────────

_OSGB = _TM(AIRY_1830, -2.0, 0.9996012717, 400_000.0, -100_000.0, 49.0)


def to_osgb(lat: float, lon: float) -> tuple[float, float]:
    """(easting, northing) in metres on the British National Grid."""
    la, lo = _shift_datum(lat, lon, WGS84, AIRY_1830, _TO_OSGB36)
    return _tm_forward(la, lo, _OSGB)


def from_osgb(easting: float, northing: float) -> tuple[float, float]:
    """(lat, lon) of a British National Grid position."""
    la, lo = _tm_inverse(easting, northing, _OSGB)
    return _shift_datum(la, lo, AIRY_1830, WGS84, _TO_OSGB36, inverse=True)


# ── SWEREF 99 TM (Sweden, EPSG:3006) ─────────────────────────────────────────
# SWEREF 99 is ETRS89, which differs from WGS-84 by well under a metre.

_SWEREF99 = _TM(GRS80, 15.0, 0.9996, 500_000.0, 0.0)


def to_sweref99(lat: float, lon: float) -> tuple[float, float]:
    """(northing, easting) in metres — the Swedish order N, E."""
    easting, northing = _tm_forward(lat, lon, _SWEREF99)
    return northing, easting


def from_sweref99(northing: float, easting: float) -> tuple[float, float]:
    """(lat, lon) of a SWEREF 99 TM position."""
    return _tm_inverse(easting, northing, _SWEREF99)


# ── Gauss-Krüger (Germany, DHDN/Potsdam, 3° zones) ───────────────────────────

def _gk_projection(zone: int) -> _TM:
    return _TM(BESSEL_1841, zone * 3.0, 1.0, zone * 1_000_000 + 500_000.0, 0.0)


def to_gauss_krueger(lat: float, lon: float) -> tuple[float, float]:
    """(Rechtswert, Hochwert) in metres. The zone — central meridian / 3 —
    is the first digit of the Rechtswert."""
    la, lo = _shift_datum(lat, lon, WGS84, BESSEL_1841, _TO_DHDN)
    zone = max(1, min(9, round(lo / 3)))
    return _tm_forward(la, lo, _gk_projection(zone))


def from_gauss_krueger(rechts: float, hoch: float) -> tuple[float, float]:
    """(lat, lon) of a Gauss-Krüger position (zone from the Rechtswert)."""
    zone = int(rechts // 1_000_000)
    if not 1 <= zone <= 9:
        raise ValueError(f"Gauss-Krüger Rechtswert {rechts:.0f} has no zone digit")
    la, lo = _tm_inverse(rechts, hoch, _gk_projection(zone))
    return _shift_datum(la, lo, BESSEL_1841, WGS84, _TO_DHDN, inverse=True)

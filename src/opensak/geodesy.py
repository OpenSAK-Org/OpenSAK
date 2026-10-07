"""
src/opensak/geodesy.py — Coordinate math without Qt.

  distance_km / bearing / project / midpoint
      Great-circle math on a sphere with the WGS-84 mean radius — the same
      formulas as the Distance & bearing, Projection and Midpoint dialogs.
  to_utm / format_utm
      WGS-84 → UTM (Krüger series, sub-metre accuracy inside a zone).
  to_ch1903 / format_ch1903
      WGS-84 → Swiss grid LV03 (CH1903) or LV95 (CH1903+), using swisstopo's
      approximate formulas (about 1 m accuracy in and around Switzerland).
"""

from __future__ import annotations

import math

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


# ── UTM ──────────────────────────────────────────────────────────────────────

_WGS84_A = 6_378_137.0
_WGS84_F = 1 / 298.257223563
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


def to_utm(lat: float, lon: float) -> tuple[int, str, float, float]:
    """(zone, latitude band, easting, northing) in metres.

    Raises ValueError outside 80°S–84°N, where UTM is not defined (polar
    areas use UPS instead).
    """
    if not -80.0 <= lat <= 84.0:
        raise ValueError("UTM is only defined between 80°S and 84°N")
    zone = _utm_zone(lat, lon)
    band = _UTM_BANDS[min(int((lat + 80) // 8), len(_UTM_BANDS) - 1)]

    e2 = _WGS84_F * (2 - _WGS84_F)
    ep2 = e2 / (1 - e2)
    phi = math.radians(lat)
    lam = math.radians(_normalize_lon(lon - ((zone - 1) * 6 - 180 + 3)))
    sin_phi, cos_phi, tan_phi = math.sin(phi), math.cos(phi), math.tan(phi)

    n = _WGS84_A / math.sqrt(1 - e2 * sin_phi ** 2)
    t = tan_phi ** 2
    c = ep2 * cos_phi ** 2
    a = cos_phi * lam
    m = _WGS84_A * (
        (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
        - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
        + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi)
        - (35 * e2 ** 3 / 3072) * math.sin(6 * phi)
    )
    easting = _UTM_K0 * n * (
        a + (1 - t + c) * a ** 3 / 6
        + (5 - 18 * t + t ** 2 + 72 * c - 58 * ep2) * a ** 5 / 120
    ) + 500_000.0
    northing = _UTM_K0 * (m + n * tan_phi * (
        a ** 2 / 2
        + (5 - t + 9 * c + 4 * c ** 2) * a ** 4 / 24
        + (61 - 58 * t + t ** 2 + 600 * c - 330 * ep2) * a ** 6 / 720
    ))
    if lat < 0:
        northing += 10_000_000.0
    return zone, band, easting, northing


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


def format_ch1903(lat: float, lon: float, lv95: bool = False) -> str:
    """E.g. "683144 / 248115" (LV03) or "2683144 / 1248115" (LV95)."""
    east, north = to_ch1903(lat, lon, lv95)
    return f"{east:.0f} / {north:.0f}"

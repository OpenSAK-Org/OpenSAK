"""
src/opensak/db/sql_functions.py — GSAK-compatible geo functions for SQL (#917).

Registered on every connection to a cache database, so Where clauses, macro
SQL and imported GSAK filters can measure between any two points without
changing the centre point:

  g_Distance(lat1, lon1, lat2, lon2 [, units])
      Distance. units: 'K' (km, the default) or 'M' (miles); append 'H' for
      the Vincenty ellipsoid ('KH', 'MH') — as in GSAK.
  g_Bearing2(lat1, lon1, lat2, lon2 [, method])
      Initial bearing in degrees (0–360). method: 'G' (great circle, the
      default) or 'H' / 'V' (Vincenty) — GSAK has no method argument.
  g_Bearing1(lat1, lon1, lat2, lon2 [, method])
      The same as a compass direction: N, NE, E, SE, S, SW, W or NW.
  g_Project(lat, lon, degrees, distance [, units [, calc]])
      The projected point as 'lat;lon' with 6 decimals, as in GSAK. units:
      K (default), M, N (nautical miles), T (metres), Y (yards), F (feet);
      calc: 'G' (great circle, the default) or 'V' (Vincenty).

Coordinates are decimal degrees, as numbers or numeric strings (GSAK stores
them as text). Where GSAK calculates with 0 or crashes, these differ: NULL
in any argument gives NULL, a value that isn't a number or an unknown unit
is an error, and a bearing between two identical points is NULL. The great
circle uses the 6371 km sphere of the rest of OpenSAK (GSAK projects on a
6366.7 km one), and Vincenty iterates to 1e-12 (GSAK to 1e-6, ~20 cm off).
"""

from __future__ import annotations

import logging
import math
import sqlite3
from typing import Any, Callable, Optional

from opensak import geodesy

logger = logging.getLogger(__name__)

# km per unit of g_Project's units argument
_PROJECT_UNITS: dict[str, float] = {
    "K": 1.0, "M": 1.609344, "N": 1.852, "T": 0.001, "Y": 0.0009144, "F": 0.0003048,
}
# km per unit of g_Distance's units argument (GSAK documents only K and M)
_DISTANCE_UNITS: dict[str, float] = {"K": 1.0, "M": 1.609344}


class GeoFunctionError(ValueError):
    """An argument a g_ function cannot use. SQLite only reports "user-defined
    function raised exception", so the message is logged too."""


def _fail(message: str) -> GeoFunctionError:
    logger.warning("SQL %s", message)
    return GeoFunctionError(message)


def _number(func: str, name: str, value: Any) -> Optional[float]:
    """*value* as a float; None for NULL. Numeric strings are accepted (with a
    '.' decimal point), anything else is an error."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.strip())
        except ValueError:
            raise _fail(f"{func}: {name} {value!r} is not a number") from None
    else:
        raise _fail(f"{func}: {name} {value!r} is not a number")
    if not math.isfinite(result):
        raise _fail(f"{func}: {name} {value!r} is not a number")
    return result


def _flag(func: str, value: Any) -> Optional[str]:
    """A flag argument upper-cased and stripped; None for NULL."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise _fail(f"{func}: {value!r} is not a valid option")
    return value.strip().upper()


def _vincenty_method(func: str, value: Any) -> Optional[bool]:
    """g_Bearing's optional method: True for Vincenty, False for the great
    circle, None for NULL."""
    method = _flag(func, value)
    if method is None:
        return None
    if method in ("", "G"):
        return False
    if method in ("H", "V"):
        return True
    raise _fail(f"{func}: unknown method {value!r}; use 'G', 'H' or 'V'")


def g_distance(lat1: Any, lon1: Any, lat2: Any, lon2: Any, units: Any = "K") -> Optional[float]:
    func = "g_Distance"
    flag = _flag(func, units)
    points = [_number(func, n, v) for n, v in
              (("lat1", lat1), ("lon1", lon1), ("lat2", lat2), ("lon2", lon2))]
    if flag is None or None in points:
        return None
    vincenty = flag.endswith("H")
    unit = (flag[:-1] if vincenty else flag) or "K"
    if unit not in _DISTANCE_UNITS:
        raise _fail(f"{func}: unknown units {units!r}; use 'K' or 'M', with 'H' "
                    f"appended for Vincenty")
    la1, lo1, la2, lo2 = (p for p in points if p is not None)
    km = (geodesy.vincenty_inverse(la1, lo1, la2, lo2)[0] if vincenty
          else geodesy.distance_km(la1, lo1, la2, lo2))
    return km / _DISTANCE_UNITS[unit]


def g_bearing2(lat1: Any, lon1: Any, lat2: Any, lon2: Any, method: Any = "G") -> Optional[float]:
    func = "g_Bearing2"
    vincenty = _vincenty_method(func, method)
    points = [_number(func, n, v) for n, v in
              (("lat1", lat1), ("lon1", lon1), ("lat2", lat2), ("lon2", lon2))]
    if vincenty is None or None in points:
        return None
    la1, lo1, la2, lo2 = (p for p in points if p is not None)
    if vincenty:
        km, deg = geodesy.vincenty_inverse(la1, lo1, la2, lo2)
    else:
        km = geodesy.distance_km(la1, lo1, la2, lo2)
        deg = geodesy.bearing(la1, lo1, la2, lo2)
    return None if km == 0.0 else deg


def g_bearing1(lat1: Any, lon1: Any, lat2: Any, lon2: Any, method: Any = "G") -> Optional[str]:
    deg = g_bearing2(lat1, lon1, lat2, lon2, method)
    return None if deg is None else geodesy.bearing_direction(deg)


def g_project(lat: Any, lon: Any, degrees: Any, distance: Any,
              units: Any = "K", calc: Any = "G") -> Optional[str]:
    func = "g_Project"
    unit, method = _flag(func, units), _flag(func, calc)
    values = [_number(func, n, v) for n, v in
              (("latitude", lat), ("longitude", lon), ("degrees", degrees),
               ("distance", distance))]
    if unit is None or method is None or None in values:
        return None
    if unit not in _PROJECT_UNITS:
        raise _fail(f"{func}: unknown units {units!r}; use K, M, N, T, Y or F")
    if method not in ("G", "V"):
        raise _fail(f"{func}: unknown calculation {calc!r}; use 'G' or 'V'")
    la, lo, deg, dist = (v for v in values if v is not None)
    km = dist * _PROJECT_UNITS[unit]
    if method == "V":
        lat2, lon2 = geodesy.vincenty_direct(la, lo, deg, km)
    else:
        lat2, lon2 = geodesy.project(la, lo, deg, km)
    return f"{lat2:.6f};{lon2:.6f}"


# name → (function, the argument counts it takes)
GEO_FUNCTIONS: dict[str, tuple[Callable[..., Any], tuple[int, ...]]] = {
    "g_Distance": (g_distance, (4, 5)),
    "g_Bearing1": (g_bearing1, (4, 5)),
    "g_Bearing2": (g_bearing2, (4, 5)),
    "g_Project": (g_project, (4, 5, 6)),
}


def register_geo_functions(conn: Any) -> None:
    """Make the g_ functions available on *conn* (an sqlite3 connection;
    anything else, such as another DB-API driver, is left alone)."""
    if not isinstance(conn, sqlite3.Connection):
        return
    for name, (fn, arities) in GEO_FUNCTIONS.items():
        for n in arities:
            conn.create_function(name, n, fn, deterministic=True)

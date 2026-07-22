"""
src/opensak/filters/engine.py — Filter & sort engine for OpenSAK.

Usage
-----
    from opensak.filters.engine import FilterSet, SortSpec, apply_filters

    fs = FilterSet()
    fs.add(CacheTypeFilter(["Traditional Cache", "Multi-cache"]))
    fs.add(DifficultyFilter(max_difficulty=3.0))
    fs.add(NotFoundFilter())
    fs.add(DistanceFilter(lat=55.67, lon=12.57, max_km=10.0))

    sort = SortSpec("difficulty", ascending=True)

    with get_session() as s:
        results = apply_filters(s, fs, sort)
"""

from __future__ import annotations

import json
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from opensak.db.models import Cache, UserNote


# ── Helpers ───────────────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in kilometres between two coordinates."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def haversine_km_batch(lat0: float, lon0: float, lats, lons):
    """Great-circle distance (km) from (lat0, lon0) to each (lats[i], lons[i]).

    Vectorised with numpy when available — turning a per-row Python loop over
    tens of thousands of caches (run on every table refresh) into a single
    array operation. Falls back to a Python list comprehension if numpy is not
    installed, so behaviour is identical either way (within float tolerance).
    Returns a numpy array or a list of floats; callers index/iterate it.
    """
    try:
        import numpy as np
    except ImportError:
        return [_haversine_km(lat0, lon0, la, lo) for la, lo in zip(lats, lons)]

    R = 6371.0
    p0 = math.radians(lat0)
    l0 = math.radians(lon0)
    la = np.radians(np.asarray(lats, dtype=float))
    lo = np.radians(np.asarray(lons, dtype=float))
    dphi = la - p0
    dlam = lo - l0
    a = np.sin(dphi / 2) ** 2 + math.cos(p0) * np.cos(la) * np.sin(dlam / 2) ** 2
    return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))


def _vincenty_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Vincenty WGS84 ellipsoidal distance in kilometres.

    More accurate than Haversine (accounts for the oblate spheroid); the
    difference is up to ~0.3 % on long distances. Falls back to Haversine
    when the formula fails to converge (antipodal points).
    """
    # WGS84 ellipsoid parameters
    a = 6378.137          # semi-major axis (km)
    f = 1 / 298.257223563
    b = a * (1 - f)       # semi-minor axis (km)

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    L = math.radians(lon2 - lon1)

    U1 = math.atan((1 - f) * math.tan(phi1))
    U2 = math.atan((1 - f) * math.tan(phi2))
    sU1, cU1 = math.sin(U1), math.cos(U1)
    sU2, cU2 = math.sin(U2), math.cos(U2)

    lam = L
    for _ in range(100):
        sl = math.sin(lam)
        cl = math.cos(lam)
        sin_sigma = math.sqrt((cU2 * sl) ** 2 + (cU1 * sU2 - sU1 * cU2 * cl) ** 2)
        if sin_sigma == 0.0:
            return 0.0  # coincident points
        cos_sigma = sU1 * sU2 + cU1 * cU2 * cl
        sigma = math.atan2(sin_sigma, cos_sigma)
        sin_alpha = cU1 * cU2 * sl / sin_sigma
        cos2a = 1 - sin_alpha ** 2
        cos2sm = (cos_sigma - 2 * sU1 * sU2 / cos2a) if cos2a else 0.0
        C = f / 16 * cos2a * (4 + f * (4 - 3 * cos2a))
        lam_prev = lam
        lam = L + (1 - C) * f * sin_alpha * (
            sigma + C * sin_sigma * (cos2sm + C * cos_sigma * (-1 + 2 * cos2sm ** 2))
        )
        if abs(lam - lam_prev) < 1e-12:
            break
    else:
        return _haversine_km(lat1, lon1, lat2, lon2)  # non-convergence fallback

    u2 = cos2a * (a ** 2 - b ** 2) / b ** 2
    Av = 1 + u2 / 16384 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)))
    Bv = u2 / 1024 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)))
    ds = Bv * sin_sigma * (
        cos2sm + Bv / 4 * (
            cos_sigma * (-1 + 2 * cos2sm ** 2)
            - Bv / 6 * cos2sm * (-3 + 4 * sin_sigma ** 2) * (-3 + 4 * cos2sm ** 2)
        )
    )
    return b * Av * (sigma - ds)


def vincenty_km_batch(lat0: float, lon0: float, lats, lons):
    """Vincenty WGS84 distance (km) from (lat0, lon0) to each point.

    Vincenty is iterative and does not vectorise cleanly, so this always
    falls back to a Python loop. The cost is still small because this path
    only runs once per centre-point change (not on every table refresh).
    Returns a list of floats.
    """
    return [_vincenty_km(lat0, lon0, la, lo) for la, lo in zip(lats, lons)]


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Scalar distance (km) dispatched by the user's distance_method setting."""
    from opensak.gui.settings import get_settings
    if get_settings().distance_method == "vincenty":
        return _vincenty_km(lat1, lon1, lat2, lon2)
    return _haversine_km(lat1, lon1, lat2, lon2)


def distance_km_batch(lat0: float, lon0: float, lats, lons):
    """Batch distance (km) dispatched by the user's distance_method setting."""
    from opensak.gui.settings import get_settings
    if get_settings().distance_method == "vincenty":
        return vincenty_km_batch(lat0, lon0, lats, lons)
    return haversine_km_batch(lat0, lon0, lats, lons)


# Matches the word "distance" but not substrings like "my_distance".
_DISTANCE_RE = re.compile(r"\bdistance\b")


# ── Base filter ───────────────────────────────────────────────────────────────

class BaseFilter(ABC):
    """Abstract base for all filters."""

    # Human-readable name used for serialisation and display
    filter_type: str = "base"

    # Whether this filter instance should be counted in the "N active"
    # badge shown to the user. Defaults to True for every filter; set to
    # False on a specific instance when it represents baseline app
    # behaviour the user didn't consciously choose (see AvailabilityFilter
    # usage in filter_dialog.py._build_filterset() for the motivating case:
    # hiding archived caches by default). This only affects the display
    # count — the filter still fully participates in matches()/apply_to_query().
    counts_as_filter: bool = True

    # Issue #631: whether a non-None apply_to_query() result is a COMPLETE
    # SQL translation of this filter (default), or merely a pre-narrowing
    # optimization that still requires the Python matches() pass for an
    # exact result (e.g. DistanceFilter's bounding-box pushdown — a
    # conservative superset of the circle, not the circle itself). Only
    # exact (sql_exact=True) filters count towards apply_filters()'s
    # "was the whole filterset fully handled in SQL" check that decides
    # whether the Python matches() re-scan can be skipped. A pre-narrowing
    # filter must set this to False on the class, or results will silently
    # include rows the pushdown query only approximately excluded.
    sql_exact: bool = True

    @abstractmethod
    def matches(self, cache: Cache) -> bool:
        """Return True if *cache* passes this filter."""

    def apply_to_query(self, query):
        """Optionally push this filter into a SQLAlchemy query before .all().

        Return the updated query if SQL-level filtering is possible, or None
        to fall back to Python-level matches(). When this returns a query the
        filter must also return True from matches() to avoid double-filtering
        — unless sql_exact is False, in which case matches() is expected to
        still narrow the SQL pushdown's result further (see sql_exact above).
        """
        return None

    def to_dict(self) -> dict:
        """Serialise filter to a JSON-safe dict."""
        return {"filter_type": self.filter_type}

    @classmethod
    def from_dict(cls, data: dict) -> "BaseFilter":
        """Deserialise from a dict (override in subclasses)."""
        return cls()

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>"


# ── Concrete filters ──────────────────────────────────────────────────────────

class CacheTypeFilter(BaseFilter):
    """Keep only caches whose type is in *types*."""
    filter_type = "cache_type"

    def __init__(self, types: list[str]):
        self.types = [t.strip() for t in types]

    def apply_to_query(self, query):
        return query.filter(Cache.cache_type.in_(self.types))

    def matches(self, cache: Cache) -> bool:
        return cache.cache_type in self.types

    def to_dict(self) -> dict:
        return {"filter_type": self.filter_type, "types": self.types}

    @classmethod
    def from_dict(cls, data: dict) -> "CacheTypeFilter":
        return cls(data["types"])

    def __repr__(self) -> str:
        return f"<CacheTypeFilter types={self.types}>"


class ContainerFilter(BaseFilter):
    """Keep only caches whose container size is in *sizes*."""
    filter_type = "container"

    def __init__(self, sizes: list[str]):
        self.sizes = [s.strip() for s in sizes]

    def apply_to_query(self, query):
        return query.filter(Cache.container.in_(self.sizes))

    def matches(self, cache: Cache) -> bool:
        return cache.container in self.sizes

    def to_dict(self) -> dict:
        return {"filter_type": self.filter_type, "sizes": self.sizes}

    @classmethod
    def from_dict(cls, data: dict) -> "ContainerFilter":
        return cls(data["sizes"])


class DifficultyFilter(BaseFilter):
    """Keep caches within a difficulty range (1.0–5.0)."""
    filter_type = "difficulty"

    def __init__(self, min_difficulty: float = 1.0, max_difficulty: float = 5.0):
        self.min_difficulty = min_difficulty
        self.max_difficulty = max_difficulty

    def apply_to_query(self, query):
        from sqlalchemy import or_
        # Mirror matches(): unknown (NULL) difficulty passes by default.
        return query.filter(or_(
            Cache.difficulty.is_(None),
            Cache.difficulty.between(self.min_difficulty, self.max_difficulty),
        ))

    def matches(self, cache: Cache) -> bool:
        if cache.difficulty is None:
            return True  # unknown difficulty passes by default
        return self.min_difficulty <= cache.difficulty <= self.max_difficulty

    def to_dict(self) -> dict:
        return {
            "filter_type": self.filter_type,
            "min_difficulty": self.min_difficulty,
            "max_difficulty": self.max_difficulty,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DifficultyFilter":
        return cls(data.get("min_difficulty", 1.0), data.get("max_difficulty", 5.0))


class TerrainFilter(BaseFilter):
    """Keep caches within a terrain range (1.0–5.0)."""
    filter_type = "terrain"

    def __init__(self, min_terrain: float = 1.0, max_terrain: float = 5.0):
        self.min_terrain = min_terrain
        self.max_terrain = max_terrain

    def apply_to_query(self, query):
        from sqlalchemy import or_
        # Mirror matches(): unknown (NULL) terrain passes by default.
        return query.filter(or_(
            Cache.terrain.is_(None),
            Cache.terrain.between(self.min_terrain, self.max_terrain),
        ))

    def matches(self, cache: Cache) -> bool:
        if cache.terrain is None:
            return True
        return self.min_terrain <= cache.terrain <= self.max_terrain

    def to_dict(self) -> dict:
        return {
            "filter_type": self.filter_type,
            "min_terrain": self.min_terrain,
            "max_terrain": self.max_terrain,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TerrainFilter":
        return cls(data.get("min_terrain", 1.0), data.get("max_terrain", 5.0))


class FoundFilter(BaseFilter):
    """Keep only caches the user HAS found."""
    filter_type = "found"

    def apply_to_query(self, query):
        # Issue #628: Cache.found.is_(True) compiles to "found IS true", which
        # SQLite's query planner cannot satisfy with ix_caches_found (falls
        # back to a full table scan) even though the functionally identical
        # "found = true" can. == compiles to the latter. Verified directly
        # against SQLite 3.45: EXPLAIN QUERY PLAN shows SCAN for IS true vs
        # SEARCH ... USING INDEX for = true on the same predicate.
        return query.filter(Cache.found == True)  # noqa: E712

    def matches(self, cache: Cache) -> bool:
        return cache.found is True

    @classmethod
    def from_dict(cls, data: dict) -> "FoundFilter":
        return cls()


class NotFoundFilter(BaseFilter):
    """Keep only caches the user has NOT found."""
    filter_type = "not_found"

    def apply_to_query(self, query):
        from sqlalchemy import or_
        # Mirror matches(): `not cache.found` treats NULL as not-found too.
        # See FoundFilter above for why == True/False is used instead of
        # .is_(True/False) — .is_(None) for the NULL leg is unaffected and
        # left as-is (SQLite uses the index fine for IS NULL).
        return query.filter(or_(Cache.found == False, Cache.found.is_(None)))  # noqa: E712

    def matches(self, cache: Cache) -> bool:
        return not cache.found

    @classmethod
    def from_dict(cls, data: dict) -> "NotFoundFilter":
        return cls()


class AvailableFilter(BaseFilter):
    """Keep only caches that are currently available (not archived/disabled)."""
    filter_type = "available"

    def apply_to_query(self, query):
        from sqlalchemy import and_
        # See FoundFilter above for why == True/False is used here instead
        # of .is_(True/False).
        return query.filter(and_(Cache.available == True, Cache.archived == False))  # noqa: E712

    def matches(self, cache: Cache) -> bool:
        return cache.available is True and cache.archived is False

    @classmethod
    def from_dict(cls, data: dict) -> "AvailableFilter":
        return cls()


class ArchivedFilter(BaseFilter):
    """Keep only archived caches."""
    filter_type = "archived"

    def apply_to_query(self, query):
        # See FoundFilter above for why == True is used here instead of
        # .is_(True).
        return query.filter(Cache.archived == True)  # noqa: E712

    def matches(self, cache: Cache) -> bool:
        return cache.archived is True

    @classmethod
    def from_dict(cls, data: dict) -> "ArchivedFilter":
        return cls()


class AvailabilityFilter(BaseFilter):
    """
    Keep caches matching any combination of availability states.

    This is the primary filter used by the filter dialog: the user can
    independently toggle showing available, unavailable (disabled) and
    archived caches.
    """
    filter_type = "availability"

    def __init__(
        self,
        show_avail: bool = True,
        show_unavail: bool = False,
        show_archived: bool = False,
    ):
        self.show_avail    = show_avail
        self.show_unavail  = show_unavail
        self.show_archived = show_archived

    def apply_to_query(self, query):
        from sqlalchemy import and_, false, or_
        # Mirror matches(): archived rows obey show_archived; among non-archived,
        # available rows obey show_avail and the rest obey show_unavail.
        # See FoundFilter above for why == True/False is used here instead
        # of .is_(True/False).
        clauses = []
        if self.show_archived:
            clauses.append(Cache.archived == True)  # noqa: E712
        if self.show_avail:
            clauses.append(and_(Cache.archived == False, Cache.available == True))  # noqa: E712
        if self.show_unavail:
            clauses.append(and_(Cache.archived == False, Cache.available == False))  # noqa: E712
        return query.filter(or_(*clauses) if clauses else false())

    def matches(self, cache: Cache) -> bool:
        if cache.archived:
            return self.show_archived
        if cache.available:
            return self.show_avail
        return self.show_unavail

    def to_dict(self) -> dict:
        return {
            "filter_type":   self.filter_type,
            "show_avail":    self.show_avail,
            "show_unavail":  self.show_unavail,
            "show_archived": self.show_archived,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AvailabilityFilter":
        return cls(
            show_avail    = data.get("show_avail",    True),
            show_unavail  = data.get("show_unavail",  False),
            show_archived = data.get("show_archived", False),
        )


class CountryFilter(BaseFilter):
    """Keep caches whose country contains *text* (case-insensitive)."""
    filter_type = "country"

    def __init__(self, text: str):
        self.text = text.strip()

    def apply_to_query(self, query):
        if not self.text:
            return None  # empty filter — let Python handle (matches() drops NULLs)
        from sqlalchemy import func
        return query.filter(func.lower(Cache.country).like(f"%{self.text.lower()}%"))

    def matches(self, cache: Cache) -> bool:
        if not cache.country:
            return False
        return self.text.lower() in cache.country.lower()

    def to_dict(self) -> dict:
        return {"filter_type": self.filter_type, "text": self.text}

    @classmethod
    def from_dict(cls, data: dict) -> "CountryFilter":
        # Backwards compat: old format used "countries" list
        if "countries" in data:
            return cls(data["countries"][0] if data["countries"] else "")
        return cls(data.get("text", ""))


class StateFilter(BaseFilter):
    """Keep caches whose state/region contains *text* (case-insensitive)."""
    filter_type = "state"

    def __init__(self, text: str):
        self.text = text.strip()

    def apply_to_query(self, query):
        if not self.text:
            return None
        from sqlalchemy import func
        return query.filter(func.lower(Cache.state).like(f"%{self.text.lower()}%"))

    def matches(self, cache: Cache) -> bool:
        if not cache.state:
            return False
        return self.text.lower() in cache.state.lower()

    def to_dict(self) -> dict:
        return {"filter_type": self.filter_type, "text": self.text}

    @classmethod
    def from_dict(cls, data: dict) -> "StateFilter":
        if "states" in data:
            return cls(data["states"][0] if data["states"] else "")
        return cls(data.get("text", ""))


class CountyFilter(BaseFilter):
    """Keep caches whose county contains *text* (case-insensitive)."""
    filter_type = "county"

    def __init__(self, text: str):
        self.text = text.strip()

    def apply_to_query(self, query):
        if not self.text:
            return None
        from sqlalchemy import func
        return query.filter(func.lower(Cache.county).like(f"%{self.text.lower()}%"))

    def matches(self, cache: Cache) -> bool:
        if not cache.county:
            return False
        return self.text.lower() in cache.county.lower()

    def to_dict(self) -> dict:
        return {"filter_type": self.filter_type, "text": self.text}

    @classmethod
    def from_dict(cls, data: dict) -> "CountyFilter":
        if "counties" in data:
            return cls(data["counties"][0] if data["counties"] else "")
        return cls(data.get("text", ""))


class NameFilter(BaseFilter):
    """Keep caches whose name contains *text* (case-insensitive)."""
    filter_type = "name"

    def __init__(self, text: str):
        self.text = text.lower()
        self._sql_applied = False

    def apply_to_query(self, query):
        from sqlalchemy import func
        self._sql_applied = True
        return query.filter(func.lower(Cache.name).like(f"%{self.text}%"))

    def matches(self, cache: Cache) -> bool:
        if self._sql_applied:
            return True
        return self.text in (cache.name or "").lower()

    def to_dict(self) -> dict:
        return {"filter_type": self.filter_type, "text": self.text}

    @classmethod
    def from_dict(cls, data: dict) -> "NameFilter":
        return cls(data["text"])


class GcCodeFilter(BaseFilter):
    """Keep caches whose GC code contains *text* (case-insensitive)."""
    filter_type = "gc_code"

    def __init__(self, text: str):
        self.text = text.upper()
        # When the input already has the "GC" prefix, a prefix match is enough
        # and lets SQLite use the B-tree index on gc_code.  Without the prefix,
        # use a substring match so "BEK" finds "GCBEKKA".
        self._prefix = self.text.startswith("GC")
        self._sql_applied = False

    def apply_to_query(self, query):
        from sqlalchemy import func
        self._sql_applied = True
        pattern = f"{self.text}%" if self._prefix else f"%{self.text}%"
        return query.filter(func.upper(Cache.gc_code).like(pattern))

    def matches(self, cache: Cache) -> bool:
        if self._sql_applied:
            return True
        code = (cache.gc_code or "").upper()
        return code.startswith(self.text) if self._prefix else self.text in code

    def to_dict(self) -> dict:
        return {"filter_type": self.filter_type, "text": self.text}

    @classmethod
    def from_dict(cls, data: dict) -> "GcCodeFilter":
        return cls(data["text"])


class PlacedByFilter(BaseFilter):
    """Keep caches placed by owners whose name contains *text* (case-insensitive)."""
    filter_type = "placed_by"

    def __init__(self, text: str):
        self.text = text.lower()

    def apply_to_query(self, query):
        if not self.text:
            return None  # empty text matches all (incl. NULL) — keep in Python
        from sqlalchemy import func
        return query.filter(func.lower(Cache.placed_by).like(f"%{self.text}%"))

    def matches(self, cache: Cache) -> bool:
        return self.text in (cache.placed_by or "").lower()

    def to_dict(self) -> dict:
        return {"filter_type": self.filter_type, "text": self.text}

    @classmethod
    def from_dict(cls, data: dict) -> "PlacedByFilter":
        return cls(data["text"])


class OwnerFilter(BaseFilter):
    """Keep caches whose owner name contains *text* (case-insensitive)."""
    filter_type = "owner_name"

    def __init__(self, text: str):
        self.text = text.lower()

    def apply_to_query(self, query):
        if not self.text:
            return None  # empty text matches all (incl. NULL) — keep in Python
        from sqlalchemy import func
        return query.filter(func.lower(Cache.owner_name).like(f"%{self.text}%"))

    def matches(self, cache: Cache) -> bool:
        return self.text in (cache.owner_name or "").lower()

    def to_dict(self) -> dict:
        return {"filter_type": self.filter_type, "text": self.text}

    @classmethod
    def from_dict(cls, data: dict) -> "OwnerFilter":
        return cls(data["text"])


class DistanceFilter(BaseFilter):
    """
    Keep caches within *max_km* kilometres of a reference coordinate.
    Optionally also enforce a *min_km* to exclude very nearby caches.
    """
    filter_type = "distance"

    # apply_to_query() below only pushes a bounding-box pre-narrowing (a
    # conservative superset of the max_km circle, and it doesn't account for
    # min_km at all) — matches() is still required for an exact result. See
    # BaseFilter.sql_exact.
    sql_exact = False

    def __init__(
        self,
        lat: float,
        lon: float,
        max_km: float,
        min_km: float = 0.0,
        center_state: Optional[dict] = None,
    ):
        self.lat = lat
        self.lon = lon
        self.max_km = max_km
        self.min_km = min_km
        # Serialized CenterPointPicker selection (issue #511) — e.g.
        # {"kind": "point", "name": "Cabin"} or {"kind": "cache"}. Purely for
        # re-populating the picker's combo box when a saved filter is
        # reloaded into the dialog; matching/query logic below only ever
        # uses lat/lon, which are always a frozen snapshot taken at the
        # moment the filter was built (same as before this field existed).
        # None for filters built before #511 or built without a picker.
        self.center_state = center_state

    def apply_to_query(self, query):
        """Pre-narrow with a lat/lon bounding box that *contains* the circle.

        The box is a conservative superset of the max_km circle, so SQLite can
        discard far-away caches (using the (latitude, longitude) index) before
        any Python object is built, while matches() still applies the exact
        haversine test — results are therefore identical. Skipped (returns None,
        i.e. pure Python) for max_km<=0 or near the poles / antimeridian, where
        a simple box could wrap and wrongly drop matches.
        """
        if self.max_km <= 0 or not (-89.0 < self.lat < 89.0):
            return None
        dlat = self.max_km / 111.0  # ~111 km per degree of latitude
        coslat = math.cos(math.radians(self.lat))
        if coslat <= 1e-6:
            return None
        dlon = self.max_km / (111.0 * coslat)
        if dlon >= 180.0 or self.lon - dlon < -180.0 or self.lon + dlon > 180.0:
            return None  # box would wrap the antimeridian — let Python handle it
        from sqlalchemy import and_
        return query.filter(and_(
            Cache.latitude.between(self.lat - dlat, self.lat + dlat),
            Cache.longitude.between(self.lon - dlon, self.lon + dlon),
        ))

    def matches(self, cache: Cache) -> bool:
        if cache.latitude is None or cache.longitude is None:
            return False
        dist = _haversine_km(self.lat, self.lon, cache.latitude, cache.longitude)
        return self.min_km <= dist <= self.max_km

    def to_dict(self) -> dict:
        return {
            "filter_type": self.filter_type,
            "lat": self.lat,
            "lon": self.lon,
            "max_km": self.max_km,
            "min_km": self.min_km,
            "center_state": self.center_state,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DistanceFilter":
        return cls(
            data["lat"], data["lon"], data["max_km"], data.get("min_km", 0.0),
            data.get("center_state"),
        )


class AttributeFilter(BaseFilter):
    """
    Keep caches that have a specific attribute set to *is_on*.
    Uses the Groundspeak attribute ID.
    """
    filter_type = "attribute"

    def __init__(self, attribute_id: int, is_on: bool = True):
        self.attribute_id = attribute_id
        self.is_on = is_on

    def matches(self, cache: Cache) -> bool:
        for attr in cache.attributes:
            if attr.attribute_id == self.attribute_id and attr.is_on == self.is_on:
                return True
        return False

    def to_dict(self) -> dict:
        return {
            "filter_type": self.filter_type,
            "attribute_id": self.attribute_id,
            "is_on": self.is_on,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AttributeFilter":
        return cls(data["attribute_id"], data.get("is_on", True))


class WhereClauseFilter(BaseFilter):
    """Raw SQL WHERE clause evaluated directly against the SQLite caches table."""
    filter_type = "where_clause"

    def __init__(self, sql: str):
        self.sql = sql.strip()
        self._matching_ids: Optional[set] = None  # populated by apply_filters

    def matches(self, cache: Cache) -> bool:
        if self._matching_ids is None:
            return True  # no pre-run done — pass all
        return cache.id in self._matching_ids

    def to_dict(self) -> dict:
        return {"filter_type": self.filter_type, "sql": self.sql}

    @classmethod
    def from_dict(cls, data: dict) -> "WhereClauseFilter":
        return cls(data.get("sql", ""))


class HasTrackableFilter(BaseFilter):
    """Keep only caches that currently have at least one trackable."""
    filter_type = "has_trackable"

    def matches(self, cache: Cache) -> bool:
        return len(cache.trackables) > 0

    @classmethod
    def from_dict(cls, data: dict) -> "HasTrackableFilter":
        return cls()


class PremiumFilter(BaseFilter):
    """Keep only premium-member caches."""
    filter_type = "premium"

    def apply_to_query(self, query):
        # See FoundFilter above for why == True is used here instead of
        # .is_(True). Note: premium_only has no index today (not in #214's
        # migration list), so this doesn't change the query plan right now —
        # kept consistent so it's already correct if one's added later.
        return query.filter(Cache.premium_only == True)  # noqa: E712

    def matches(self, cache: Cache) -> bool:
        return cache.premium_only is True

    @classmethod
    def from_dict(cls, data: dict) -> "PremiumFilter":
        return cls()


class NonPremiumFilter(BaseFilter):
    """Keep only non-premium caches."""
    filter_type = "non_premium"

    def apply_to_query(self, query):
        # See FoundFilter above for why == False is used here instead of
        # .is_(False).
        return query.filter(Cache.premium_only == False)  # noqa: E712

    def matches(self, cache: Cache) -> bool:
        return cache.premium_only is False

    @classmethod
    def from_dict(cls, data: dict) -> "NonPremiumFilter":
        return cls()


class HasCorrectedFilter(BaseFilter):
    """Keep only caches that have corrected coordinates set."""
    filter_type = "has_corrected"

    def matches(self, cache: Cache) -> bool:
        note = cache.user_note
        return bool(note and note.is_corrected)

    @classmethod
    def from_dict(cls, data: dict) -> "HasCorrectedFilter":
        return cls()


class NoCorrectedFilter(BaseFilter):
    """Keep only caches that do NOT have corrected coordinates set.

    Counterpart to HasCorrectedFilter — mirrors the Premium/NonPremium
    pair. Without this class, unchecking "has corrected" while leaving
    only "no corrected" checked in the filter dialog produced no filter
    at all (bug #274: the Corrected Coordinate flag was silently ignored).
    """
    filter_type = "no_corrected"

    def matches(self, cache: Cache) -> bool:
        note = cache.user_note
        return not bool(note and note.is_corrected)

    @classmethod
    def from_dict(cls, data: dict) -> "NoCorrectedFilter":
        return cls()


class UserFlagFilter(BaseFilter):
    """Keep caches based on user_flag value."""
    filter_type = "user_flag"

    def __init__(self, flagged: bool):
        self.flagged = flagged

    def matches(self, cache: Cache) -> bool:
        return bool(cache.user_flag) == self.flagged

    def to_dict(self) -> dict:
        return {"filter_type": self.filter_type, "flagged": self.flagged}

    @classmethod
    def from_dict(cls, data: dict) -> "UserFlagFilter":
        return cls(flagged=data["flagged"])


class LockedFilter(BaseFilter):
    """Keep caches based on locked value (issue #202)."""
    filter_type = "locked"

    def __init__(self, locked: bool):
        self.locked = locked

    def matches(self, cache: Cache) -> bool:
        return bool(cache.locked) == self.locked

    def to_dict(self) -> dict:
        return {"filter_type": self.filter_type, "locked": self.locked}

    @classmethod
    def from_dict(cls, data: dict) -> "LockedFilter":
        return cls(locked=data["locked"])


class DnfFilter(BaseFilter):
    """Keep caches based on DNF (Did Not Find) flag."""
    filter_type = "dnf"

    def __init__(self, has_dnf: bool):
        self.has_dnf = has_dnf

    def matches(self, cache: Cache) -> bool:
        return bool(cache.dnf) == self.has_dnf

    def to_dict(self) -> dict:
        return {"filter_type": self.filter_type, "has_dnf": self.has_dnf}

    @classmethod
    def from_dict(cls, data: dict) -> "DnfFilter":
        return cls(has_dnf=data["has_dnf"])


class FtfFilter(BaseFilter):
    """Keep caches based on FTF (First to Find) flag."""
    filter_type = "ftf"

    def __init__(self, has_ftf: bool):
        self.has_ftf = has_ftf

    def matches(self, cache: Cache) -> bool:
        return bool(cache.first_to_find) == self.has_ftf

    def to_dict(self) -> dict:
        return {"filter_type": self.filter_type, "has_ftf": self.has_ftf}

    @classmethod
    def from_dict(cls, data: dict) -> "FtfFilter":
        return cls(has_ftf=data["has_ftf"])


class FavoritePointsFilter(BaseFilter):
    """Keep caches with favorite_points within [min_pts, max_pts]."""
    filter_type = "favorite_points"

    def __init__(self, min_pts: int = 0, max_pts: int = 9999):
        self.min_pts = min_pts
        self.max_pts = max_pts

    def matches(self, cache: Cache) -> bool:
        pts = cache.favorite_points or 0
        return self.min_pts <= pts <= self.max_pts

    def to_dict(self) -> dict:
        return {
            "filter_type": self.filter_type,
            "min_pts": self.min_pts,
            "max_pts": self.max_pts,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FavoritePointsFilter":
        return cls(min_pts=data.get("min_pts", 0), max_pts=data.get("max_pts", 9999))


class FoundByMeDateFilter(BaseFilter):
    """Keep caches found by the user within an optional date range."""
    filter_type = "found_by_me_date"

    def __init__(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ):
        self.from_date = from_date
        self.to_date = to_date

    def matches(self, cache: Cache) -> bool:
        if not cache.found:
            return False
        fd = cache.found_date
        if fd is None:
            return True  # found but no date — include
        fd = fd.replace(tzinfo=None)
        if self.from_date and fd < self.from_date:
            return False
        if self.to_date and fd > self.to_date:
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "filter_type": self.filter_type,
            "from_date": self.from_date.isoformat() if self.from_date else None,
            "to_date": self.to_date.isoformat() if self.to_date else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FoundByMeDateFilter":
        return cls(
            from_date=datetime.fromisoformat(data["from_date"]) if data.get("from_date") else None,
            to_date=datetime.fromisoformat(data["to_date"]) if data.get("to_date") else None,
        )


class DnfDateFilter(BaseFilter):
    """Keep caches with a DNF date within an optional date range."""
    filter_type = "dnf_date"

    def __init__(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ):
        self.from_date = from_date
        self.to_date = to_date

    def matches(self, cache: Cache) -> bool:
        if not cache.dnf:
            return False
        dd = cache.dnf_date
        if dd is None:
            return True
        dd = dd.replace(tzinfo=None)
        if self.from_date and dd < self.from_date:
            return False
        if self.to_date and dd > self.to_date:
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "filter_type": self.filter_type,
            "from_date": self.from_date.isoformat() if self.from_date else None,
            "to_date": self.to_date.isoformat() if self.to_date else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DnfDateFilter":
        return cls(
            from_date=datetime.fromisoformat(data["from_date"]) if data.get("from_date") else None,
            to_date=datetime.fromisoformat(data["to_date"]) if data.get("to_date") else None,
        )


class LastLogDateFilter(BaseFilter):
    """Keep caches whose last_log_date falls within an optional date range."""
    filter_type = "last_log_date"

    def __init__(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ):
        self.from_date = from_date
        self.to_date = to_date

    def matches(self, cache: Cache) -> bool:
        ld = cache.last_log_date
        if ld is None:
            return False
        ld = ld.replace(tzinfo=None)
        if self.from_date and ld < self.from_date:
            return False
        if self.to_date and ld > self.to_date:
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "filter_type": self.filter_type,
            "from_date": self.from_date.isoformat() if self.from_date else None,
            "to_date": self.to_date.isoformat() if self.to_date else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LastLogDateFilter":
        return cls(
            from_date=datetime.fromisoformat(data["from_date"]) if data.get("from_date") else None,
            to_date=datetime.fromisoformat(data["to_date"]) if data.get("to_date") else None,
        )


class TextSearchFilter(BaseFilter):
    """Keep caches whose text fields contain *text* (case-insensitive).

    Searches any combination of: short/long description, log texts,
    personal user notes, and the encoded hint.
    """
    filter_type = "text_search"

    def __init__(
        self,
        text: str,
        search_description: bool = True,
        search_logs: bool = True,
        search_notes: bool = True,
        search_hint: bool = False,
    ):
        self.text = text.strip()
        self.search_description = search_description
        self.search_logs = search_logs
        self.search_notes = search_notes
        self.search_hint = search_hint

    def apply_to_query(self, query):
        if not self.text:
            return None
        from sqlalchemy import func, exists, or_
        from opensak.db.models import Log, UserNote

        pattern = f"%{self.text.lower()}%"
        conditions = []
        if self.search_description:
            conditions.append(func.lower(Cache.short_description).like(pattern))
            conditions.append(func.lower(Cache.long_description).like(pattern))
        if self.search_hint:
            conditions.append(func.lower(Cache.encoded_hints).like(pattern))
        if self.search_logs:
            conditions.append(
                exists().where(
                    (Log.cache_id == Cache.id)
                    & func.lower(Log.text).like(pattern)
                )
            )
        if self.search_notes:
            conditions.append(
                exists().where(
                    (UserNote.cache_id == Cache.id)
                    & func.lower(UserNote.note).like(pattern)
                )
            )
        if not conditions:
            return None
        return query.filter(or_(*conditions))

    def matches(self, cache: Cache) -> bool:
        if not self.text:
            return True
        needle = self.text.lower()
        if self.search_description:
            if cache.short_description and needle in cache.short_description.lower():
                return True
            if cache.long_description and needle in cache.long_description.lower():
                return True
        if self.search_hint:
            if cache.encoded_hints and needle in cache.encoded_hints.lower():
                return True
        if self.search_notes:
            if cache.user_note and cache.user_note.note:
                if needle in cache.user_note.note.lower():
                    return True
        if self.search_logs:
            for log in cache.logs:
                if log.text and needle in log.text.lower():
                    return True
        return False

    def to_dict(self) -> dict:
        return {
            "filter_type": self.filter_type,
            "text": self.text,
            "search_description": self.search_description,
            "search_logs": self.search_logs,
            "search_notes": self.search_notes,
            "search_hint": self.search_hint,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TextSearchFilter":
        return cls(
            text=data.get("text", ""),
            search_description=data.get("search_description", True),
            search_logs=data.get("search_logs", True),
            search_notes=data.get("search_notes", True),
            search_hint=data.get("search_hint", False),
        )


# ── Filter registry (for deserialisation) ─────────────────────────────────────

FILTER_REGISTRY: dict[str, type[BaseFilter]] = {
    "cache_type":    CacheTypeFilter,
    "container":     ContainerFilter,
    "difficulty":    DifficultyFilter,
    "terrain":       TerrainFilter,
    "found":         FoundFilter,
    "not_found":     NotFoundFilter,
    "available":     AvailableFilter,
    "archived":      ArchivedFilter,
    "availability":  AvailabilityFilter,
    "country":       CountryFilter,
    "state":         StateFilter,
    "county":        CountyFilter,
    "name":          NameFilter,
    "gc_code":       GcCodeFilter,
    "placed_by":     PlacedByFilter,
    "owner_name":    OwnerFilter,
    "distance":      DistanceFilter,
    "attribute":     AttributeFilter,
    "has_trackable": HasTrackableFilter,
    "has_corrected": HasCorrectedFilter,
    "no_corrected":  NoCorrectedFilter,
    "premium":       PremiumFilter,
    "non_premium":   NonPremiumFilter,
    "where_clause":       WhereClauseFilter,
    "user_flag":          UserFlagFilter,
    "locked":             LockedFilter,
    "dnf":                DnfFilter,
    "ftf":                FtfFilter,
    "favorite_points":    FavoritePointsFilter,
    "found_by_me_date":   FoundByMeDateFilter,
    "dnf_date":           DnfDateFilter,
    "last_log_date":      LastLogDateFilter,
    "text_search":        TextSearchFilter,
}


# ── FilterSet — AND / OR composition ─────────────────────────────────────────

class FilterSet:
    """
    A collection of filters combined with AND or OR logic.

    AND (default): a cache must pass ALL filters to be included.
    OR:            a cache must pass AT LEAST ONE filter.

    FilterSets can be nested for complex expressions:
        FilterSet(AND) containing:
          - CacheTypeFilter(["Traditional"])
          - FilterSet(OR) containing:
              - DifficultyFilter(max=2.0)
              - TerrainFilter(max=2.0)
    """

    def __init__(self, mode: str = "AND"):
        if mode not in ("AND", "OR"):
            raise ValueError(f"mode must be 'AND' or 'OR', got {mode!r}")
        self.mode = mode
        self._filters: list[BaseFilter | FilterSet] = []

    def add(self, f: "BaseFilter | FilterSet") -> "FilterSet":
        """Add a filter or nested FilterSet. Returns self for chaining."""
        self._filters.append(f)
        return self

    def clear(self) -> None:
        self._filters.clear()

    def __len__(self) -> int:
        return len(self._filters)

    def active_count(self) -> int:
        """Count filters for the "N active" UI badge.

        Like __len__, but skips filters flagged with counts_as_filter=False
        (baseline app behaviour the user didn't consciously set, e.g. the
        default "hide archived caches" state — see filter_dialog.py). Nested
        FilterSets are counted recursively.
        """
        total = 0
        for f in self._filters:
            if isinstance(f, FilterSet):
                total += f.active_count()
            elif getattr(f, "counts_as_filter", True):
                total += 1
        return total

    def matches(self, cache: Cache) -> bool:
        if not self._filters:
            return True  # empty filter set = show everything

        if self.mode == "AND":
            return all(f.matches(cache) for f in self._filters)
        else:
            return any(f.matches(cache) for f in self._filters)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "filters": [f.to_dict() for f in self._filters],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FilterSet":
        fs = cls(mode=data.get("mode", "AND"))
        for fdata in data.get("filters", []):
            if "mode" in fdata:
                # Nested FilterSet
                fs.add(FilterSet.from_dict(fdata))
            else:
                ftype = fdata.get("filter_type")
                if ftype in FILTER_REGISTRY:
                    fs.add(FILTER_REGISTRY[ftype].from_dict(fdata))
        return fs

    def __repr__(self) -> str:
        return f"<FilterSet mode={self.mode} filters={self._filters}>"


# ── Sort spec ─────────────────────────────────────────────────────────────────

# Logical container sort: physical sizes first (micro→large), then non-physical
# types (earthcache/lab/virtual), then empty/not-chosen. Mirrors _container_sort_key
# in gui/cache_table.py — both must be kept in sync.
_CONTAINER_PHYSICAL_ORDER = {"micro": 1, "small": 2, "regular": 3, "large": 4}
_NON_PHYSICAL_TYPES = {
    "earthcache": "E", "lab cache": "V",
    "virtual cache": "V", "locationless (reverse) cache": "R",
}
_EMPTY_CONTAINERS = {"", "not chosen"}


def _container_sort_key(c) -> tuple:
    ct = (c.cache_type or "").strip().lower()
    letter = _NON_PHYSICAL_TYPES.get(ct)
    if letter is not None:
        return (2, letter)
    key = (c.container or "").strip().lower()
    if key in _CONTAINER_PHYSICAL_ORDER:
        return (1, _CONTAINER_PHYSICAL_ORDER[key])
    if key in _EMPTY_CONTAINERS:
        return (3, "")
    return (2, "O")


# Valid sort fields and how to extract the sort key from a Cache object
SORT_FIELDS: dict[str, Any] = {
    "name":            lambda c: (c.name or "").lower(),
    "gc_code":         lambda c: c.gc_code or "",
    "cache_type":      lambda c: c.cache_type or "",
    "difficulty":      lambda c: c.difficulty or 0.0,
    "terrain":         lambda c: c.terrain or 0.0,
    "hidden_date":     lambda c: c.hidden_date or 0,
    "country":         lambda c: (c.country or "").lower(),
    "state":           lambda c: (c.state or "").lower(),
    "county":          lambda c: (c.county or "").lower(),
    "placed_by":       lambda c: (c.placed_by or "").lower(),
    "container":       _container_sort_key,
    "found":           lambda c: int(c.found),
    "archived":        lambda c: int(c.archived),
    # Kolonner sorteret i CacheTableModel — accepteres af SortSpec men bruges
    # ikke af apply_filters (sortering sker i Python-laget via model.sort())
    "distance":        lambda c: c.distance or 99999.0,
    "bearing":         lambda c: c.bearing or 0.0,
    "log_count":       lambda c: 0,   # placeholder — model.sort() håndterer det
    "last_log":        lambda c: 0,   # placeholder — model.sort() håndterer det
    "found_date":      lambda c: c.found_date or 0,
    "dnf":             lambda c: int(c.dnf),
    "dnf_date":        lambda c: c.dnf_date or 0,
    "premium_only":    lambda c: int(c.premium_only),
    "favorite_points": lambda c: c.favorite_points or 0,
    "trackables":      lambda c: c.trackable_count or 0,
    "corrected":       lambda c: 0,   # placeholder — model.sort() håndterer det
    "first_to_find":   lambda c: int(c.first_to_find or False),
    "user_flag":       lambda c: int(c.user_flag or False),
    "locked":          lambda c: int(c.locked or False),
    "user_sort":       lambda c: c.user_sort if c.user_sort is not None else 999999,
    "user_data_1":     lambda c: (c.user_data_1 or "").lower(),
    "user_data_2":     lambda c: (c.user_data_2 or "").lower(),
    "user_data_3":     lambda c: (c.user_data_3 or "").lower(),
    "user_data_4":     lambda c: (c.user_data_4 or "").lower(),
}


def _sql_order_expr(field: str):
    """Return a SQLAlchemy ORDER BY expression mirroring SORT_FIELDS[*field*],
    or None if the field must be sorted in Python.

    Only numeric / boolean / date columns are ordered in SQL: the expression
    reproduces the Python key exactly (COALESCE for the ``x or default``
    fallbacks). Text fields are deliberately excluded — SQLite's lower() is
    ASCII-only and would diverge from Python's Unicode str.lower() on accented
    values. Distance is stored in the DB column and ordered in SQL via COALESCE.
    """
    from sqlalchemy import func
    distance_expr = func.coalesce(Cache.distance, 99999.0)
    exprs = {
        # Numeric (mirror "x or 0.0/0/999999")
        "difficulty":      func.coalesce(Cache.difficulty, 0.0),
        "terrain":         func.coalesce(Cache.terrain, 0.0),
        "favorite_points": func.coalesce(Cache.favorite_points, 0),
        "trackables":      func.coalesce(Cache.trackable_count, 0),
        "user_sort":       func.coalesce(Cache.user_sort, 999999),
        # Boolean (mirror int(x) / int(x or False) → 0/1)
        "found":           Cache.found,
        "archived":        Cache.archived,
        "dnf":             Cache.dnf,
        "premium_only":    Cache.premium_only,
        "first_to_find":   func.coalesce(Cache.first_to_find, 0),
        "user_flag":       func.coalesce(Cache.user_flag, 0),
        "locked":          func.coalesce(Cache.locked, 0),
        # Dates — plain column ordering (NULLs first ascending in SQLite, i.e.
        # treated as earliest). This also fixes the latent SORT_FIELDS bug where
        # "x or 0" mixes datetime and int and raises TypeError on mixed NULLs.
        "hidden_date":     Cache.hidden_date,
        "found_date":      Cache.found_date,
        "dnf_date":        Cache.dnf_date,
        # distance: only sortable in SQL when the DB column is populated
        "distance":        distance_expr,
    }
    return exprs.get(field)


@dataclass
class SortSpec:
    """Defines a sort operation on the result list."""
    field: str = "name"
    ascending: bool = True

    def __post_init__(self):
        if self.field not in SORT_FIELDS:
            raise ValueError(
                f"Unknown sort field {self.field!r}. "
                f"Valid fields: {list(SORT_FIELDS.keys())}"
            )

    def to_dict(self) -> dict:
        return {"field": self.field, "ascending": self.ascending}

    @classmethod
    def from_dict(cls, data: dict) -> "SortSpec":
        return cls(field=data.get("field", "name"), ascending=data.get("ascending", True))


# ── Distance annotation helper ────────────────────────────────────────────────

def annotate_distances(
    caches: list[Cache],
    lat: float,
    lon: float,
) -> dict[int, float]:
    """
    Return a dict mapping cache.id → distance_km from (lat, lon).
    Useful for displaying distances in the UI without filtering.
    """
    valid = [c for c in caches if c.latitude is not None and c.longitude is not None]
    if not valid:
        return {}
    dists = haversine_km_batch(
        lat, lon, [c.latitude for c in valid], [c.longitude for c in valid]
    )
    return {c.id: float(dists[i]) for i, c in enumerate(valid)}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _iter_filters(filterset: "FilterSet"):
    """Yield all leaf BaseFilter instances from a FilterSet (recursively)."""
    for f in filterset._filters:
        if isinstance(f, FilterSet):
            yield from _iter_filters(f)
        else:
            yield f


def _sql_pushdown_candidates(filterset: "FilterSet"):
    """Yield leaf filters that may be safely pushed into the SQL WHERE clause.

    Pushing a filter adds an *AND* term to the query, so it is only sound when
    every enclosing FilterSet is AND-mode. We descend through AND FilterSets and
    yield their leaf filters; as soon as an OR FilterSet is reached we stop
    descending into it — that whole subtree must be evaluated in Python by the
    OR FilterSet's matches(), or we would incorrectly turn an OR into an AND.

    Filters whose apply_to_query() returns None (no SQL form, or e.g. an empty
    text filter) simply fall back to Python matches() — that is handled by the
    caller, not here.
    """
    if filterset.mode != "AND":
        return
    for f in filterset._filters:
        if isinstance(f, FilterSet):
            if f.mode == "AND":
                yield from _sql_pushdown_candidates(f)
            # OR subtree: leave entirely to Python matches()
        else:
            yield f


# ── Shared query-preparation helpers ────────────────────────────────────────
# Extracted so apply_filters() and apply_filters_lightweight() (#627 beta.9)
# share a single implementation of "which filters can be pushed to SQL, and
# is the whole filterset fully handled that way" — the #631 DistanceFilter
# bug happened because this exact logic is easy to get subtly wrong, so it
# must not be duplicated between the two entry points.

def _prepare_where_clause_filters(
    session: Session,
    filterset: Optional["FilterSet"],
    distance_from: Optional[tuple[float, float]],
) -> None:
    """Pre-populate every WhereClauseFilter's _matching_ids by running its raw
    SQL directly against the database. Must run before any Python-level
    matches() call touches a WhereClauseFilter. Mutates the filter objects
    in place; returns nothing.
    """
    if not filterset:
        return
    from sqlalchemy import text as _sa_text
    _where_filters = [
        _f for _f in _iter_filters(filterset)
        if isinstance(_f, WhereClauseFilter) and _f.sql
    ]
    _dist_udf_ready = False
    if any(_DISTANCE_RE.search(_f.sql) for _f in _where_filters):
        # The "distance" column in the caches table is never persisted — it
        # is always NULL. Register a SQLite UDF so WHERE clauses can use
        # "distance" as haversine distance from the home point. SQL
        # references to "distance" are rewritten to the UDF call below.
        _home_lat, _home_lon, _use_miles = 0.0, 0.0, False
        try:
            from opensak.gui.settings import get_settings as _gs
            _st = _gs()
            _home_lat, _home_lon = _st.home_lat, _st.home_lon
            _use_miles = _st.use_miles
        except Exception:
            pass
        if distance_from:
            _home_lat, _home_lon = distance_from
        _factor = 0.621371 if _use_miles else 1.0
        def _dist_udf(lat, lon, _h=_home_lat, _o=_home_lon, _k=_factor):
            if lat is None or lon is None:
                return None
            return _haversine_km(_h, _o, lat, lon) * _k
        _dbapi = session.connection().connection.dbapi_connection
        assert _dbapi is not None
        _dbapi.create_function("_opensak_dist", 2, _dist_udf)
        _dist_udf_ready = True

    for _f in _where_filters:
        try:
            _sql = (
                _DISTANCE_RE.sub("_opensak_dist(latitude, longitude)", _f.sql)
                if _dist_udf_ready
                else _f.sql
            )
            _result = session.execute(
                _sa_text(f"SELECT id FROM caches WHERE ({_sql})")
            )
            _f._matching_ids = {row[0] for row in _result}
        except Exception:
            _f._matching_ids = set()  # invalid SQL → no matches


def _apply_sql_pushdown(queryable, filterset: Optional["FilterSet"]):
    """Push every filter reachable via _sql_pushdown_candidates() into
    *queryable* — an ORM Query (session.query(Cache)) or a Core Select
    (select(Cache.col1, ...)) both work identically here, since every
    apply_to_query() implementation calls queryable.filter(...), which both
    object types support.

    Returns (queryable, fully_sql_pushed) — see apply_filters()'s docstring
    on fully_sql_pushed (#631) for exactly what that flag means and why
    BaseFilter.sql_exact exists.
    """
    fully_sql_pushed = False
    if filterset:
        _candidates = list(_sql_pushdown_candidates(filterset))
        _total_leaves = sum(1 for _ in _iter_filters(filterset))
        _pushed = 0
        for _f in _candidates:
            updated = _f.apply_to_query(queryable)
            if updated is not None:
                queryable = updated
                if _f.sql_exact:
                    _pushed += 1
        fully_sql_pushed = (
            len(_candidates) == _total_leaves and _pushed == _total_leaves
        )
    return queryable, fully_sql_pushed


@dataclass
class _RelationshipNeeds:
    """Which relationships/deferred fields a filterset actually touches.

    apply_filters() uses this to decide what to joinedload/noload/defer.
    apply_filters_lightweight() uses it to decide whether it can serve the
    request at all — LightweightCache has none of these, so any True flag
    means falling back to the full apply_filters() ORM path.
    """
    attributes: bool
    trackables: bool
    logs: bool
    description: bool
    hint: bool

    @property
    def any(self) -> bool:
        return self.attributes or self.trackables or self.logs or self.description or self.hint


def _filterset_relationship_needs(filterset: Optional["FilterSet"]) -> _RelationshipNeeds:
    needs_attributes = filterset is not None and any(
        isinstance(f, AttributeFilter) for f in _iter_filters(filterset)
    )
    needs_trackables = filterset is not None and any(
        isinstance(f, HasTrackableFilter) for f in _iter_filters(filterset)
    )
    _text_filters = [
        f for f in _iter_filters(filterset)
        if isinstance(f, TextSearchFilter) and f.text
    ] if filterset is not None else []
    needs_description = any(f.search_description for f in _text_filters)
    needs_hint = any(f.search_hint for f in _text_filters)
    needs_logs = any(f.search_logs for f in _text_filters)
    return _RelationshipNeeds(
        attributes=needs_attributes, trackables=needs_trackables,
        logs=needs_logs, description=needs_description, hint=needs_hint,
    )


# ── Main apply function ───────────────────────────────────────────────────────

def apply_filters(
    session: Session,
    filterset: Optional[FilterSet] = None,
    sort: Optional[SortSpec] = None,
    limit: Optional[int] = None,
    distance_from: Optional[tuple[float, float]] = None,
) -> list[Cache]:
    """
    Load caches from DB, apply *filterset*, sort, and return a list.

    Parameters
    ----------
    session      : Active SQLAlchemy session
    filterset    : FilterSet to apply (None = return all)
    sort         : SortSpec (None = sort by name ascending)
    limit        : Maximum number of results to return
    distance_from: Optional (lat, lon) tuple — if given, results are sorted
                   by distance when sort.field == 'distance'

    Returns
    -------
    List of Cache objects that match all filters, in sorted order.
    """
    # Pre-populate WhereClauseFilter matching IDs by running the raw SQL against SQLite.
    # This must happen before the Python-level filter loop below.
    _prepare_where_clause_filters(session, filterset, distance_from)

    # Determine which relationships are actually needed by the active filters.
    # Only joinedload what is required — avoids loading thousands of attribute
    # and trackable rows when the filterset contains only a NameFilter or a
    # simple quick-filter (the common case during live search).
    _needs = _filterset_relationship_needs(filterset)

    from sqlalchemy.orm import defer, joinedload, noload
    _opts: list = [
        joinedload(Cache.attributes) if _needs.attributes else noload(Cache.attributes),
        joinedload(Cache.trackables) if _needs.trackables else noload(Cache.trackables),
        # Logs are loaded via the SQL EXISTS pushdown; avoid a joinedload that
        # would pull all logs for all caches. Python matches() will lazy-load
        # logs only for the already-filtered result set.
        joinedload(Cache.logs)       if _needs.logs        else noload(Cache.logs),
        noload(Cache.waypoints),
        joinedload(Cache.user_note),
    ]
    # Defer the large free-text blobs unless text search needs them.
    if not _needs.description:
        _opts += [defer(Cache.short_description), defer(Cache.long_description)]
    if not _needs.hint:
        _opts.append(defer(Cache.encoded_hints))
    query = session.query(Cache).options(*_opts)

    # Push SQL-capable filters into the query before loading rows.
    # This lets SQLite discard non-matching rows before any Python objects are
    # constructed — critical on large DBs. Only filters reachable through an
    # all-AND path are pushed (see _sql_pushdown_candidates): pushing a filter
    # AND-s it into the WHERE clause, which would be wrong inside an OR set.
    # Anything left out (OR subtrees, relationship filters, apply_to_query()
    # returning None) is still enforced by the Python matches() pass below, so
    # the result is identical — SQL push-down is a pure performance shortcut.
    #
    # Issue #631: when EVERY leaf filter ends up pushed into the WHERE clause,
    # every row query.all() returns already satisfies the filterset — the
    # Python-level `[c for c in all_caches if filterset.matches(c)]` pass
    # further down is then a redundant full re-scan of up to hundreds of
    # thousands of already-hydrated ORM objects. fully_sql_pushed tracks this
    # so that pass can be skipped safely. It requires BOTH that no OR-subtree
    # was left out (candidates covers every leaf in _iter_filters) AND that
    # every candidate's apply_to_query() actually returned a query (some
    # filter types, e.g. WhereClauseFilter/HasTrackableFilter, have no SQL
    # form and always fall back to Python matches() via the default
    # BaseFilter.apply_to_query() returning None).
    query, fully_sql_pushed = _apply_sql_pushdown(query, filterset)

    # Resolve sort early so column-backed fields can be ordered in SQL.
    if sort is None:
        sort = SortSpec("name", ascending=True)

    # Push ORDER BY into SQL for safe (numeric/boolean/date) fields. The
    # Python filter pass below preserves row order, so a SQL-ordered result
    # stays ordered. A trailing Cache.id keeps the order identical to Python's
    # stable sort (ties retain the id-ascending load order).
    sql_sorted = False
    order_expr = _sql_order_expr(sort.field)
    if order_expr is not None:
        direction = order_expr.asc() if sort.ascending else order_expr.desc()
        query = query.order_by(direction, Cache.id.asc())
        sql_sorted = True

    all_caches = query.all()

    # Apply filters (order-preserving — keeps any SQL ORDER BY intact).
    # Issue #631: skip this full Python re-scan when every filter was
    # already pushed into the WHERE clause above — every row in all_caches
    # already satisfies the filterset in that case, so re-checking it here
    # would just be a redundant pass over up to hundreds of thousands of
    # already-hydrated objects.
    if filterset and not fully_sql_pushed:
        results = [c for c in all_caches if filterset.matches(c)]
    else:
        results = list(all_caches)

    # Sort in Python only for fields not handled by SQL.
    if not sql_sorted:
        if sort.field in SORT_FIELDS:
            results.sort(key=SORT_FIELDS[sort.field], reverse=not sort.ascending)

    if limit:
        results = results[:limit]

    return results


# ── Lightweight query path (#627 beta.9-11) ─────────────────────────────────
#
# apply_filters()'s dominant cost at large database sizes is SQLAlchemy ORM
# row hydration via query.all() — NOT SQL execution, and NOT the Python
# matches() pass (#631's isolated benchmark: ~7s of a ~7s call was ORM
# hydration of ~92,000 rows; the Python pass was ~2%). Hydrating a full
# Cache ORM entity costs far more than fetching the same columns as a plain
# row, because of identity-map registration, relationship-lazy-loader setup,
# and instrumented-attribute bookkeeping done for every single object.
#
# apply_filters_lightweight() fetches the same scalar columns via a Core
# select() instead of session.query(Cache) — SQLAlchemy Row objects support
# named attribute access for every selected column but skip all of that ORM
# machinery. Wrapped in LightweightCache so existing display code (table,
# map) can keep using the same attribute names as a full Cache, unchanged.
#
# Deliberately excludes relationship collections (.logs/.attributes/
# .trackables/.waypoints) and the two heavy deferred text fields
# (short_description/long_description) — any filterset that needs those
# transparently falls back to the full apply_filters() ORM path instead of
# returning wrong/incomplete results. This is a fallback, not an error: the
# lightweight path is a pure performance shortcut for the common case
# (table/map display with simple filters), the same relationship the SQL
# push-down in apply_filters() has to its own Python matches() fallback.
#
# mainwindow.py's table and map refresh call apply_filters_auto() (below),
# which always attempts this path — wired in via beta.10 (table) and
# beta.11 (map; needed zero source changes in map_widget.py, confirmed by
# a dedicated compatibility audit and test suite). Was gated behind a
# lightweight-query-path feature flag while beta.9-11 verified it in
# isolation; the flag was removed once both consumers were confirmed
# stable — see apply_filters_auto()'s docstring.

class LightweightUserNote:
    """Minimal stand-in for Cache.user_note — enough for the display code
    that currently does getattr(cache, "user_note", None) then reads
    .is_corrected/.corrected_lat/.corrected_lon (map_widget.py,
    gps/garmin.py's _effective_coords())."""
    __slots__ = ("is_corrected", "corrected_lat", "corrected_lon")

    def __init__(self, is_corrected: bool, corrected_lat: Optional[float], corrected_lon: Optional[float]):
        self.is_corrected = is_corrected
        self.corrected_lat = corrected_lat
        self.corrected_lon = corrected_lon


class LightweightCache:
    """Duck-types as a read-only Cache for display purposes (table/map).

    Wraps a SQLAlchemy Core Row of scalar Cache columns. Every column
    apply_filters_lightweight() selects is reachable by attribute, exactly
    like the corresponding attribute on a real Cache ORM instance — sort
    keys (SORT_FIELDS), filter matches() implementations, and display code
    that only touches scalar fields all work unchanged against this.

    Deliberately does NOT carry .logs/.attributes/.trackables/.waypoints or
    .short_description/.long_description/.encoded_hints — any code that
    touches one of those raises AttributeError. That is the correct failure
    mode: it means that code path needed the full apply_filters() ORM
    result, not a silently wrong or empty value, and apply_filters_lightweight()
    should have fallen back to apply_filters() for that filterset/use case
    instead of returning LightweightCache rows at all.

    Mostly immutable, with one deliberate exception: CacheTableModel.setData()
    (user_flag/locked/first_to_find quick-toggle) persists the change via a
    freshly-queried real Cache ORM object, then also sets the attribute
    directly on whatever object the table row currently holds, purely so the
    UI reflects the change without a full table reload. _MUTABLE_FIELDS
    supports exactly that in-place-update pattern via a small overrides dict
    — every other attribute stays read-only, preserving the AttributeError
    safety net above for anything that was never meant to be writable here.
    """
    __slots__ = ("_row", "user_note", "_overrides")

    _MUTABLE_FIELDS = frozenset({"user_flag", "locked", "first_to_find"})

    def __init__(self, row, user_note: Optional[LightweightUserNote]):
        object.__setattr__(self, "_row", row)
        object.__setattr__(self, "user_note", user_note)
        object.__setattr__(self, "_overrides", {})

    def __getattr__(self, name: str):
        # __getattr__ only fires when normal (slot/instance) lookup fails,
        # so this only ever runs for names delegated to the underlying Row
        # (or an override set via __setattr__ below).
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            return overrides[name]
        try:
            return getattr(self._row, name)
        except AttributeError:
            raise AttributeError(
                f"LightweightCache has no attribute {name!r} — this field "
                "needs the full apply_filters() ORM path (relationship or "
                "deferred text field)."
            ) from None

    def __setattr__(self, name: str, value) -> None:
        if name not in self._MUTABLE_FIELDS:
            raise AttributeError(
                f"LightweightCache is read-only for {name!r}. Only "
                f"{sorted(self._MUTABLE_FIELDS)} can be set in place (matching "
                "CacheTableModel.setData()'s quick-toggle columns) — mutate "
                "the real Cache ORM object for anything else, the same way "
                "setData() already re-fetches one by gc_code to persist."
            )
        self._overrides[name] = value

    def __repr__(self) -> str:
        gc_code = getattr(self._row, "gc_code", "?")
        return f"<LightweightCache {gc_code!r}>"


# Every Cache column apply_filters_lightweight() selects — everything except
# the relationship collections and the three heavy/deferred text fields
# (short_description, long_description, encoded_hints). Kept as an explicit
# list (not introspected from Cache.__table__) so it's obvious at a glance
# exactly what LightweightCache does and doesn't carry.
_LIGHTWEIGHT_COLUMNS = [
    Cache.id, Cache.gc_code, Cache.name, Cache.cache_type, Cache.container,
    Cache.latitude, Cache.longitude, Cache.difficulty, Cache.terrain,
    Cache.placed_by, Cache.owner_name, Cache.owner_id, Cache.hidden_date,
    Cache.last_updated, Cache.available, Cache.archived, Cache.premium_only,
    Cache.short_desc_html, Cache.long_desc_html,
    Cache.country, Cache.state, Cache.county,
    Cache.found, Cache.found_date, Cache.dnf, Cache.dnf_date,
    Cache.first_to_find, Cache.user_flag, Cache.user_sort,
    Cache.user_data_1, Cache.user_data_2, Cache.user_data_3, Cache.user_data_4,
    Cache.distance, Cache.bearing, Cache.favorite_points,
    Cache.gc_note, Cache.url, Cache.elevation, Cache.color, Cache.guid,
    Cache.watch, Cache.gc_cache_id, Cache.find_count,
    Cache.log_count, Cache.trackable_count, Cache.found_log_count,
    Cache.last_log_date, Cache.waypoint_count, Cache.parent_gc_code,
    Cache.locked, Cache.location_source, Cache.location_basis,
    Cache.location_updated, Cache.location_dataset, Cache.imported_at,
    Cache.source_file,
]


def apply_filters_lightweight(
    session: Session,
    filterset: Optional[FilterSet] = None,
    sort: Optional[SortSpec] = None,
    limit: Optional[int] = None,
    distance_from: Optional[tuple[float, float]] = None,
) -> list:
    """Like apply_filters(), but returns LightweightCache rows instead of
    full Cache ORM objects when it safely can — see the module comment
    above for why and when. Falls back to apply_filters() (returning real
    Cache ORM objects, unchanged) whenever the filterset needs a
    relationship or deferred text field this path doesn't carry.

    Callers that only display scalar fields (table, map) can treat the
    return value as "a list of cache-like objects" without caring which
    path served the request — but MUST NOT assume every result is a
    LightweightCache, since a fallback returns real Cache objects instead.
    """
    _needs = _filterset_relationship_needs(filterset)
    if _needs.any:
        return apply_filters(session, filterset, sort, limit, distance_from)

    _prepare_where_clause_filters(session, filterset, distance_from)

    from sqlalchemy import select
    sel = (
        select(*_LIGHTWEIGHT_COLUMNS, UserNote.is_corrected, UserNote.corrected_lat, UserNote.corrected_lon)
        .select_from(Cache)
        .outerjoin(UserNote, UserNote.cache_id == Cache.id)
    )
    sel, fully_sql_pushed = _apply_sql_pushdown(sel, filterset)

    if sort is None:
        sort = SortSpec("name", ascending=True)

    sql_sorted = False
    order_expr = _sql_order_expr(sort.field)
    if order_expr is not None:
        direction = order_expr.asc() if sort.ascending else order_expr.desc()
        sel = sel.order_by(direction, Cache.id.asc())
        sql_sorted = True

    rows = session.execute(sel).all()

    all_caches = []
    for row in rows:
        is_corrected, corrected_lat, corrected_lon = row[-3], row[-2], row[-1]
        note = (
            LightweightUserNote(bool(is_corrected), corrected_lat, corrected_lon)
            if is_corrected is not None else None
        )
        all_caches.append(LightweightCache(row, note))

    if filterset and not fully_sql_pushed:
        # LightweightCache duck-types Cache for every attribute a filter's
        # matches() could touch here — _filterset_relationship_needs()
        # above already guaranteed nothing in this filterset needs a
        # relationship or deferred text field LightweightCache doesn't
        # carry. matches() is typed for Cache specifically since it's the
        # common/default case everywhere else in the codebase.
        results = [c for c in all_caches if filterset.matches(c)]  # type: ignore[arg-type]
    else:
        results = list(all_caches)

    if not sql_sorted:
        if sort.field in SORT_FIELDS:
            results.sort(key=SORT_FIELDS[sort.field], reverse=not sort.ascending)

    if limit:
        results = results[:limit]

    return results


def apply_filters_auto(
    session: Session,
    filterset: Optional[FilterSet] = None,
    sort: Optional[SortSpec] = None,
    limit: Optional[int] = None,
    distance_from: Optional[tuple[float, float]] = None,
) -> list:
    """Preferred entry point for GUI code (table, map) that only needs
    scalar display fields — always the fast path where it safely can be.

    Always calls apply_filters_lightweight(), which itself automatically
    falls back to the full apply_filters() ORM path whenever the filterset
    needs a relationship or deferred text field (see LightweightCache's
    docstring for exactly what that is) — so this is always correct, just
    faster when it safely can be. Callers must still treat the return
    value as "a list of cache-like objects": some entries may be
    LightweightCache, some may be real Cache ORM objects, depending on
    whether a given call needed the fallback. Never assume a specific
    type; only touch attributes documented as present on both.

    #627 beta.9-11: this used to be gated behind a lightweight-query-path
    feature flag while the lightweight path was verified in isolation
    (beta.9), then wired into the table (beta.10) and map (beta.11).
    Both are now confirmed stable — full test suite, e2e suite, and a
    250,000-cache benchmark all green — so the flag has been removed and
    this is unconditional.
    """
    return apply_filters_lightweight(session, filterset, sort, limit, distance_from)


# ── Saved filter profiles ─────────────────────────────────────────────────────

class FilterProfile:
    """
    A named, saveable filter configuration stored as JSON.

    Profiles are saved to ~/.local/share/opensak/filters/
    """

    def __init__(self, name: str, filterset: FilterSet, sort: Optional[SortSpec] = None):
        self.name = name
        self.filterset = filterset
        self.sort = sort or SortSpec()

    def save(self, profiles_dir: Optional[Path] = None) -> Path:
        """Save this profile to disk as JSON. Returns the saved file path."""
        if profiles_dir is None:
            from opensak.config import get_app_data_dir
            profiles_dir = get_app_data_dir() / "filters"
        profiles_dir.mkdir(parents=True, exist_ok=True)

        safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in self.name)
        path = profiles_dir / f"{safe_name}.json"

        data = {
            "name": self.name,
            "filterset": self.filterset.to_dict(),
            "sort": self.sort.to_dict(),
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "FilterProfile":
        """Load a profile from a JSON file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            name=data["name"],
            filterset=FilterSet.from_dict(data["filterset"]),
            sort=SortSpec.from_dict(data.get("sort", {})),
        )

    @classmethod
    def list_profiles(cls, profiles_dir: Optional[Path] = None) -> list[Path]:
        """Return a list of all saved profile paths."""
        if profiles_dir is None:
            from opensak.config import get_app_data_dir
            profiles_dir = get_app_data_dir() / "filters"
        if not profiles_dir.exists():
            return []
        return sorted(profiles_dir.glob("*.json"))

    def __repr__(self) -> str:
        return f"<FilterProfile {self.name!r}>"

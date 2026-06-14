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
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from opensak.db.models import Cache


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


# ── Base filter ───────────────────────────────────────────────────────────────

class BaseFilter(ABC):
    """Abstract base for all filters."""

    # Human-readable name used for serialisation and display
    filter_type: str = "base"

    @abstractmethod
    def matches(self, cache: Cache) -> bool:
        """Return True if *cache* passes this filter."""

    def apply_to_query(self, query):
        """Optionally push this filter into a SQLAlchemy query before .all().

        Return the updated query if SQL-level filtering is possible, or None
        to fall back to Python-level matches(). When this returns a query the
        filter must also return True from matches() to avoid double-filtering.
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
        return query.filter(Cache.found.is_(True))

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
        return query.filter(or_(Cache.found.is_(False), Cache.found.is_(None)))

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
        return query.filter(and_(Cache.available.is_(True), Cache.archived.is_(False)))

    def matches(self, cache: Cache) -> bool:
        return cache.available is True and cache.archived is False

    @classmethod
    def from_dict(cls, data: dict) -> "AvailableFilter":
        return cls()


class ArchivedFilter(BaseFilter):
    """Keep only archived caches."""
    filter_type = "archived"

    def apply_to_query(self, query):
        return query.filter(Cache.archived.is_(True))

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
        clauses = []
        if self.show_archived:
            clauses.append(Cache.archived.is_(True))
        if self.show_avail:
            clauses.append(and_(Cache.archived.is_(False), Cache.available.is_(True)))
        if self.show_unavail:
            clauses.append(and_(Cache.archived.is_(False), Cache.available.is_(False)))
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
        self._sql_applied = False

    def apply_to_query(self, query):
        from sqlalchemy import func
        self._sql_applied = True
        # GC codes are always searched from the start (GC12345) — use a prefix
        # match so SQLite can exploit the existing B-tree index on gc_code.
        return query.filter(func.upper(Cache.gc_code).like(f"{self.text}%"))

    def matches(self, cache: Cache) -> bool:
        if self._sql_applied:
            return True
        return (cache.gc_code or "").upper().startswith(self.text)

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

    def __init__(
        self,
        lat: float,
        lon: float,
        max_km: float,
        min_km: float = 0.0,
    ):
        self.lat = lat
        self.lon = lon
        self.max_km = max_km
        self.min_km = min_km

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
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DistanceFilter":
        return cls(data["lat"], data["lon"], data["max_km"], data.get("min_km", 0.0))


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
        return query.filter(Cache.premium_only.is_(True))

    def matches(self, cache: Cache) -> bool:
        return cache.premium_only is True

    @classmethod
    def from_dict(cls, data: dict) -> "PremiumFilter":
        return cls()


class NonPremiumFilter(BaseFilter):
    """Keep only non-premium caches."""
    filter_type = "non_premium"

    def apply_to_query(self, query):
        return query.filter(Cache.premium_only.is_(False))

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
    "premium":       PremiumFilter,
    "non_premium":   NonPremiumFilter,
    "where_clause":       WhereClauseFilter,
    "user_flag":          UserFlagFilter,
    "dnf":                DnfFilter,
    "ftf":                FtfFilter,
    "favorite_points":    FavoritePointsFilter,
    "found_by_me_date":   FoundByMeDateFilter,
    "dnf_date":           DnfDateFilter,
    "last_log_date":      LastLogDateFilter,
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
    "container":       lambda c: (c.container or "").lower(),
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
    "favorite":        lambda c: int(c.favorite_point),
    "favorite_points": lambda c: c.favorite_points or 0,
    "corrected":       lambda c: 0,   # placeholder — model.sort() håndterer det
    "first_to_find":   lambda c: int(c.first_to_find or False),
    "user_flag":       lambda c: int(c.user_flag or False),
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
    values. Derived / model-only fields (distance, bearing, log_count,
    last_log, corrected, lat/lon) are also excluded and stay in Python.

    The caller must append Cache.id as a final tiebreaker so the SQL order
    matches Python's *stable* sort (ties keep the id-ascending load order).
    """
    from sqlalchemy import func
    return {
        # Numeric (mirror "x or 0.0/0/999999")
        "difficulty":      func.coalesce(Cache.difficulty, 0.0),
        "terrain":         func.coalesce(Cache.terrain, 0.0),
        "favorite_points": func.coalesce(Cache.favorite_points, 0),
        "user_sort":       func.coalesce(Cache.user_sort, 999999),
        # Boolean (mirror int(x) / int(x or False) → 0/1)
        "found":           Cache.found,
        "archived":        Cache.archived,
        "dnf":             Cache.dnf,
        "premium_only":    Cache.premium_only,
        "favorite":        Cache.favorite_point,
        "first_to_find":   func.coalesce(Cache.first_to_find, 0),
        "user_flag":       func.coalesce(Cache.user_flag, 0),
        # Dates — plain column ordering (NULLs first ascending in SQLite, i.e.
        # treated as earliest). This also fixes the latent SORT_FIELDS bug where
        # "x or 0" mixes datetime and int and raises TypeError on mixed NULLs.
        "hidden_date":     Cache.hidden_date,
        "found_date":      Cache.found_date,
        "dnf_date":        Cache.dnf_date,
    }.get(field)


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
    if filterset:
        from sqlalchemy import text as _sa_text
        for _f in _iter_filters(filterset):
            if isinstance(_f, WhereClauseFilter) and _f.sql:
                try:
                    _result = session.execute(
                        _sa_text(f"SELECT id FROM caches WHERE ({_f.sql})")
                    )
                    _f._matching_ids = {row[0] for row in _result}
                except Exception:
                    _f._matching_ids = set()  # invalid SQL → no matches

    # Determine which relationships are actually needed by the active filters.
    # Only joinedload what is required — avoids loading thousands of attribute
    # and trackable rows when the filterset contains only a NameFilter or a
    # simple quick-filter (the common case during live search).
    needs_attributes  = filterset is not None and any(
        isinstance(f, AttributeFilter)    for f in _iter_filters(filterset)
    )
    needs_trackables  = filterset is not None and any(
        isinstance(f, HasTrackableFilter) for f in _iter_filters(filterset)
    )

    from sqlalchemy.orm import defer, joinedload, noload
    query = session.query(Cache).options(
        joinedload(Cache.attributes) if needs_attributes else noload(Cache.attributes),
        joinedload(Cache.trackables) if needs_trackables else noload(Cache.trackables),
        noload(Cache.logs),       # load on-demand when user opens a cache
        noload(Cache.waypoints),  # load on-demand when user opens a cache
        joinedload(Cache.user_note),  # one-to-one, cheap; needed for corrected-coords
        # Defer the large free-text blobs — they dominate per-row size but are
        # never shown in the cache table (only in the detail panel, which loads
        # each cache separately via _load_full_cache()). Deferring them keeps
        # the table refresh light on big databases. A load_only() allow-list was
        # rejected as too fragile: the table model and Python-level filters read
        # a wide, scattered set of scalar columns, and missing one would trigger
        # a lazy SELECT per row (N+1). These three blobs carry ~all the weight.
        defer(Cache.short_description),
        defer(Cache.long_description),
        defer(Cache.encoded_hints),
    )

    # Push SQL-capable filters into the query before loading rows.
    # This lets SQLite discard non-matching rows before any Python objects are
    # constructed — critical on large DBs. Only filters reachable through an
    # all-AND path are pushed (see _sql_pushdown_candidates): pushing a filter
    # AND-s it into the WHERE clause, which would be wrong inside an OR set.
    # Anything left out (OR subtrees, relationship filters, apply_to_query()
    # returning None) is still enforced by the Python matches() pass below, so
    # the result is identical — SQL push-down is a pure performance shortcut.
    if filterset:
        for _f in _sql_pushdown_candidates(filterset):
            updated = _f.apply_to_query(query)
            if updated is not None:
                query = updated

    # Resolve sort early so column-backed fields can be ordered in SQL.
    if sort is None:
        sort = SortSpec("name", ascending=True)

    # Push ORDER BY into SQL for the safe (numeric/boolean/date) fields. The
    # Python filter pass below preserves row order, so a SQL-ordered result
    # stays ordered. A trailing Cache.id keeps the order identical to Python's
    # stable sort (ties retain the id-ascending load order). The distance sort
    # needs a live reference point, so it always stays in Python.
    sql_sorted = False
    if not (sort.field == "distance" and distance_from):
        order_expr = _sql_order_expr(sort.field)
        if order_expr is not None:
            direction = order_expr.asc() if sort.ascending else order_expr.desc()
            query = query.order_by(direction, Cache.id.asc())
            sql_sorted = True

    all_caches = query.all()

    # Apply filters (order-preserving — keeps any SQL ORDER BY intact)
    if filterset:
        results = [c for c in all_caches if filterset.matches(c)]
    else:
        results = list(all_caches)

    # Sort in Python only for fields not handled by SQL above.
    if not sql_sorted:
        if sort.field == "distance" and distance_from:
            lat, lon = distance_from
            results.sort(
                key=lambda c: _haversine_km(lat, lon, c.latitude or 0, c.longitude or 0),
                reverse=not sort.ascending,
            )
        elif sort.field in SORT_FIELDS:
            results.sort(key=SORT_FIELDS[sort.field], reverse=not sort.ascending)

    if limit:
        results = results[:limit]

    return results


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

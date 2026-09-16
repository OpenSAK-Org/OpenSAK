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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from opensak.db.models import Cache, UserNote, Waypoint
from opensak.filters.line_polygon import LP_MIN_POINTS, LP_MODES, LineShape


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


# ── Text match filters ────────────────────────────────────────────────────────
#
# The single-column text filters (cache name, GC code, placed by, owner name,
# country, state, county) share one set of comparison operators. The first
# nine mirror GSAK's text-comparison dropdown one-to-one — Enthält / Enthält
# nicht / Gleich / Ungleich / Leer / Nicht leer / In Liste / RegEx /
# Nicht(RegExp) — under the names scripts/migrate_gsak_filters.py already
# uses for them; starts_with, ends_with and not_in_list are OpenSAK
# additions. Every comparison is case-insensitive, like the plain "contains"
# filters always were, and a NULL column counts as the empty string.

TEXT_OPS: tuple[str, ...] = (
    "contains", "not_contains", "equals", "not_equals",
    "starts_with", "ends_with", "in_list", "not_in_list",
    "empty", "not_empty", "regex", "not_regex",
)

# Operators that compare against nothing — the filter's text is ignored.
TEXT_OPS_VALUELESS = frozenset({"empty", "not_empty"})

# Separates the values of in_list / not_in_list — ";" like GSAK's "In Liste".
TEXT_LIST_SEPARATOR = ";"

# Negated operator → its positive counterpart. A negated operator matches
# exactly when the positive one doesn't.
_TEXT_OP_NEGATIONS = {
    "not_contains": "contains",
    "not_equals":   "equals",
    "not_in_list":  "in_list",
    "not_regex":    "regex",
}

# LIKE pattern per positive operator; {} is the escaped needle.
_TEXT_OP_LIKE = {
    "contains":    "%{}%",
    "starts_with": "{}%",
    "ends_with":   "%{}",
    "equals":      "{}",
    "in_list":     "{}",
}


def _like_pattern(needle: str) -> str:
    """Escape LIKE wildcards in *needle* (for ESCAPE '\\') and turn every
    non-ASCII character into a "_" wildcard — see TextMatchFilter.__init__."""
    return "".join(
        "\\" + ch if ch in "\\%_" else ch if ch.isascii() else "_"
        for ch in needle
    )


class TextMatchFilter(BaseFilter):
    """Keep caches whose *column* matches *text* under operator *op*.

    Subclasses set filter_type and column. *text* is stored as entered
    (stripped), so it round-trips through saved profiles and the filter
    dialog unchanged; matching lower-cases both sides.
    """
    column: str = ""

    def __init__(self, text: str = "", op: str = "contains"):
        if op not in TEXT_OPS:
            raise ValueError(f"op must be one of {TEXT_OPS}, got {op!r}")
        self.text = text.strip()
        self.op = op
        # The operator actually matched with — GcCodeFilter narrows
        # "contains" to "starts_with"; self.op keeps what the user chose.
        self._match_op = op
        self._needle = self.text.lower()
        self._items = [
            item.strip().lower()
            for item in self.text.split(TEXT_LIST_SEPARATOR)
            if item.strip()
        ]
        # A regex that doesn't compile matches nothing; the filter dialog
        # shows regex_error and refuses to apply such a filter.
        self._regex: Optional[re.Pattern[str]] = None
        self.regex_error: Optional[str] = None
        if op in ("regex", "not_regex") and self.text:
            try:
                self._regex = re.compile(self.text, re.IGNORECASE)
            except re.error as exc:
                self.regex_error = str(exc)
        # SQLite's lower() only folds ASCII, while matches() uses Python's
        # Unicode-aware str.lower() ("ZÜRICH" → "zÜrich" vs "zürich"). So a
        # needle with non-ASCII characters is pushed to SQL only as a
        # pre-narrowing LIKE (each such character a "_" wildcard) and
        # matches() makes the exact decision — see BaseFilter.sql_exact.
        self.sql_exact = op in TEXT_OPS_VALUELESS or all(
            n.isascii() for n in self._needles()
        )

    def _needles(self) -> list[str]:
        if self.op in ("in_list", "not_in_list"):
            return self._items
        return [self._needle] if self._needle else []

    def _is_noop(self) -> bool:
        """Nothing to compare against (empty text) — every cache matches."""
        return self.op not in TEXT_OPS_VALUELESS and not self._needles()

    def apply_to_query(self, query):
        if self._is_noop():
            return query
        cond = self.sql_condition(getattr(Cache, self.column))
        return None if cond is None else query.filter(cond)

    def sql_condition(self, col):
        """SQL condition applying this match to column expression *col*, or
        None when it can't be expressed in SQL (see apply_to_query). A no-op
        filter yields true(). Shared with WaypointFilter, which matches the
        same operators against waypoint columns."""
        from sqlalchemy import and_, func, or_, true
        if self._is_noop():
            return true()
        if self.op == "empty":
            return or_(col.is_(None), col == "")
        if self.op == "not_empty":
            return and_(col.is_not(None), col != "")
        positive = _TEXT_OP_NEGATIONS.get(self._match_op, self._match_op)
        negated = positive != self._match_op
        if positive == "regex":
            return None  # SQLite has no REGEXP operator — matches() only
        if not self.sql_exact and (
            negated or any(len(ch.lower()) != 1 for ch in self.text)
        ):
            # A pre-narrowing superset can't be negated, and a character
            # that lower-cases to several (e.g. "İ") breaks the one-"_"-per-
            # character pattern — leave both to matches().
            return None
        lowered = func.lower(col)
        if self.sql_exact and positive == "equals":
            cond = lowered == self._needle
        elif self.sql_exact and positive == "in_list":
            cond = lowered.in_(self._items)
        else:
            cond = or_(*(
                lowered.like(_TEXT_OP_LIKE[positive].format(_like_pattern(n)), escape="\\")
                for n in self._needles()
            ))
        if negated:
            # NOT on a NULL column is NULL (row dropped) in SQL, but NULL is
            # "" for matches() — which a negated operator lets through.
            cond = or_(col.is_(None), ~cond)
        return cond

    def matches(self, cache: Cache) -> bool:
        return self.match_value(getattr(cache, self.column))

    def match_value(self, value: Optional[str]) -> bool:
        """Whether *value* (None counts as "") passes this match."""
        if self._is_noop():
            return True
        value = value or ""
        if self.op == "empty":
            return not value
        if self.op == "not_empty":
            return bool(value)
        positive = _TEXT_OP_NEGATIONS.get(self._match_op, self._match_op)
        if positive == "regex":
            if self._regex is None:
                return False  # invalid pattern
            matched = self._regex.search(value) is not None
        else:
            value = value.lower()
            if positive == "contains":
                matched = self._needle in value
            elif positive == "equals":
                matched = value == self._needle
            elif positive == "starts_with":
                matched = value.startswith(self._needle)
            elif positive == "ends_with":
                matched = value.endswith(self._needle)
            else:  # in_list
                matched = value in self._items
        negated = positive != self._match_op
        return not matched if negated else matched

    def to_dict(self) -> dict:
        return {"filter_type": self.filter_type, "text": self.text, "op": self.op}

    @classmethod
    def from_dict(cls, data: dict) -> "TextMatchFilter":
        # Profiles saved before the operator existed have no "op" — they
        # were always "contains".
        return cls(data.get("text", ""), data.get("op", "contains"))

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} {self.op} {self.text!r}>"


class CountryFilter(TextMatchFilter):
    """Keep caches whose country matches *text* (case-insensitive)."""
    filter_type = "country"
    column = "country"

    @classmethod
    def from_dict(cls, data: dict) -> "TextMatchFilter":
        # Backwards compat: old format used "countries" list
        if "countries" in data:
            return cls(data["countries"][0] if data["countries"] else "")
        return super().from_dict(data)


class StateFilter(TextMatchFilter):
    """Keep caches whose state/region matches *text* (case-insensitive)."""
    filter_type = "state"
    column = "state"

    @classmethod
    def from_dict(cls, data: dict) -> "TextMatchFilter":
        if "states" in data:
            return cls(data["states"][0] if data["states"] else "")
        return super().from_dict(data)


class CountyFilter(TextMatchFilter):
    """Keep caches whose county matches *text* (case-insensitive)."""
    filter_type = "county"
    column = "county"

    @classmethod
    def from_dict(cls, data: dict) -> "TextMatchFilter":
        if "counties" in data:
            return cls(data["counties"][0] if data["counties"] else "")
        return super().from_dict(data)


class NameFilter(TextMatchFilter):
    """Keep caches whose name matches *text* (case-insensitive)."""
    filter_type = "name"
    column = "name"


class GcCodeFilter(TextMatchFilter):
    """Keep caches whose GC code matches *text* (case-insensitive)."""
    filter_type = "gc_code"
    column = "gc_code"

    def __init__(self, text: str = "", op: str = "contains"):
        super().__init__(text, op)
        # "contains" with the "GC" prefix typed is a code entered from its
        # start, so it matches as a prefix; without the prefix it stays a
        # substring match so "BEK" finds "GCBEKKA".
        if op == "contains" and self._needle.startswith("gc"):
            self._match_op = "starts_with"


class PlacedByFilter(TextMatchFilter):
    """Keep caches placed by owners whose name matches *text* (case-insensitive)."""
    filter_type = "placed_by"
    column = "placed_by"


class OwnerFilter(TextMatchFilter):
    """Keep caches whose owner name matches *text* (case-insensitive)."""
    filter_type = "owner_name"
    column = "owner_name"


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


class LinePolygonFilter(BaseFilter):
    """GSAK's line/polygon filter: keep caches along a line, inside a polygon
    or near a set of points (see line_polygon.LineShape) — or, with
    *exclude*, only the caches that don't match.

    Tests each cache's effective_coords() (corrected coordinates when set,
    as on the map). *points* is a frozen snapshot taken when the filter was
    built, like DistanceFilter's centre; *text* is the dialog's point list
    as entered ("W,<code>" lines and comments included), kept only to
    re-populate the dialog.
    """
    filter_type = "line_polygon"

    # apply_to_query() below only pushes a bounding-box pre-narrowing —
    # matches() makes the exact decision. See BaseFilter.sql_exact.
    sql_exact = False

    def __init__(
        self,
        points: list[tuple[float, float]],
        mode: str = "line",
        distance_km: float = 0.0,
        exclude: bool = False,
        text: str = "",
    ):
        if mode not in LP_MODES:
            raise ValueError(f"mode must be one of {LP_MODES}, got {mode!r}")
        self.points = [(float(lat), float(lon)) for lat, lon in points]
        self.mode = mode
        self.distance_km = max(0.0, float(distance_km))
        self.exclude = bool(exclude)
        self.text = text
        self._shape = LineShape(self.points, mode, self.distance_km)

    def apply_to_query(self, query):
        """Pre-narrow to the shape's bounding box (grown by the distance).

        Checked against the raw coordinates OR the corrected ones, since
        matches() uses whichever applies — a puzzle whose final lies on the
        line but whose posted coordinates don't must still come through.
        Nothing is pushed for *exclude* (the complement of a box narrows
        nothing) or when the shape has no box (poles / antimeridian).
        """
        bbox = self._shape.bbox
        if self.exclude or bbox is None:
            return None
        from sqlalchemy import and_, exists, or_
        lat_lo, lat_hi, lon_lo, lon_hi = bbox
        # .correlate(Cache) — see HasCorrectedFilter.apply_to_query().
        corrected_in_box = (
            exists()
            .where(
                UserNote.cache_id == Cache.id,
                UserNote.is_corrected == True,  # noqa: E712
                UserNote.corrected_lat.between(lat_lo, lat_hi),
                UserNote.corrected_lon.between(lon_lo, lon_hi),
            )
            .correlate(Cache)
        )
        return query.filter(or_(
            and_(
                Cache.latitude.between(lat_lo, lat_hi),
                Cache.longitude.between(lon_lo, lon_hi),
            ),
            corrected_in_box,
        ))

    def matches(self, cache: Cache) -> bool:
        lat, lon = effective_coords(cache)
        if lat is None or lon is None:
            return False
        return self._shape.contains(lat, lon) != self.exclude

    def to_dict(self) -> dict:
        # "shape_type", not "mode": FilterSet.from_dict() reads any dict
        # with a "mode" key as a nested FilterSet.
        return {
            "filter_type": self.filter_type,
            "shape_type": self.mode,
            "points": [[lat, lon] for lat, lon in self.points],
            "distance_km": self.distance_km,
            "exclude": self.exclude,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LinePolygonFilter":
        return cls(
            points=data.get("points", []),
            mode=data.get("shape_type", "line"),
            distance_km=data.get("distance_km", 0.0),
            exclude=data.get("exclude", False),
            text=data.get("text", ""),
        )

    def __repr__(self) -> str:
        exclude = " exclude" if self.exclude else ""
        return (f"<LinePolygonFilter {self.mode} points={len(self.points)} "
                f"{self.distance_km} km{exclude}>")


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

    def apply_to_query(self, query):
        # #633: mirrors matches() exactly via a correlated EXISTS — no
        # UserNote row at all, or one with is_corrected falsy, both
        # correctly exclude the cache, same as `bool(note and note.is_corrected)`.
        #
        # .correlate(Cache) is required: apply_filters_lightweight()'s
        # select() already outerjoins UserNote (for corrected-coords
        # display), so without an explicit correlate(), SQLAlchemy's
        # auto-correlation sees UserNote in both the outer query and this
        # subquery and tries to correlate on it too — leaving the subquery
        # with no FROM clause of its own and raising InvalidRequestError.
        # apply_filters()'s plain session.query(Cache) has no such outer
        # UserNote reference, so this only breaks on the lightweight path —
        # caught by testing both, not just the ORM path (see #631's
        # DistanceFilter for why testing only one path isn't enough here).
        from sqlalchemy import exists

        from opensak.db.models import UserNote
        subq = (
            exists()
            .where(UserNote.cache_id == Cache.id, UserNote.is_corrected == True)  # noqa: E712
            .correlate(Cache)
        )
        return query.filter(subq)

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

    def apply_to_query(self, query):
        # #633: NOT EXISTS mirrors `not bool(note and note.is_corrected)` —
        # includes both "no UserNote row" and "UserNote exists but not
        # corrected", same as matches() below. .correlate(Cache) needed —
        # see HasCorrectedFilter above for why.
        from sqlalchemy import exists

        from opensak.db.models import UserNote
        subq = (
            exists()
            .where(UserNote.cache_id == Cache.id, UserNote.is_corrected == True)  # noqa: E712
            .correlate(Cache)
        )
        return query.filter(~subq)

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

    def apply_to_query(self, query):
        # #633: mirror matches()'s `bool(cache.user_flag) == self.flagged` —
        # NULL counts as falsy, same as bool(None). == True/False (not
        # .is_(True/False) — see #628) so the index stays usable.
        from sqlalchemy import or_
        if self.flagged:
            return query.filter(Cache.user_flag == True)  # noqa: E712
        return query.filter(or_(Cache.user_flag == False, Cache.user_flag.is_(None)))  # noqa: E712

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

    def apply_to_query(self, query):
        # #633: same NULL-as-falsy mirror as UserFlagFilter above.
        from sqlalchemy import or_
        if self.locked:
            return query.filter(Cache.locked == True)  # noqa: E712
        return query.filter(or_(Cache.locked == False, Cache.locked.is_(None)))  # noqa: E712

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

    def apply_to_query(self, query):
        # #633: same NULL-as-falsy mirror as UserFlagFilter above.
        from sqlalchemy import or_
        if self.has_dnf:
            return query.filter(Cache.dnf == True)  # noqa: E712
        return query.filter(or_(Cache.dnf == False, Cache.dnf.is_(None)))  # noqa: E712

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

    def apply_to_query(self, query):
        # #633: same NULL-as-falsy mirror as UserFlagFilter above.
        from sqlalchemy import or_
        if self.has_ftf:
            return query.filter(Cache.first_to_find == True)  # noqa: E712
        return query.filter(or_(Cache.first_to_find == False, Cache.first_to_find.is_(None)))  # noqa: E712

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

    def apply_to_query(self, query):
        # #633: mirror matches()'s `cache.favorite_points or 0` (NULL treated
        # as 0) via coalesce.
        from sqlalchemy import func
        return query.filter(func.coalesce(Cache.favorite_points, 0).between(self.min_pts, self.max_pts))

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

    def apply_to_query(self, query):
        # #633: mirrors matches() exactly — found must be true; if a date
        # range is given, a NULL found_date still matches (found but no
        # date — include), same as the `if fd is None: return True` branch
        # below. With no range given, any found=True row matches
        # regardless of found_date, same as matches() falling through to
        # `return True` when both from_date/to_date are falsy.
        from sqlalchemy import and_, or_
        q = query.filter(Cache.found == True)  # noqa: E712
        range_conditions = []
        if self.from_date:
            range_conditions.append(Cache.found_date >= self.from_date)
        if self.to_date:
            range_conditions.append(Cache.found_date <= self.to_date)
        if range_conditions:
            q = q.filter(or_(Cache.found_date.is_(None), and_(*range_conditions)))
        return q

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

    def apply_to_query(self, query):
        # #633: same pattern as FoundByMeDateFilter above, for dnf/dnf_date.
        from sqlalchemy import and_, or_
        q = query.filter(Cache.dnf == True)  # noqa: E712
        range_conditions = []
        if self.from_date:
            range_conditions.append(Cache.dnf_date >= self.from_date)
        if self.to_date:
            range_conditions.append(Cache.dnf_date <= self.to_date)
        if range_conditions:
            q = q.filter(or_(Cache.dnf_date.is_(None), and_(*range_conditions)))
        return q

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

    def apply_to_query(self, query):
        # #633: mirrors matches() exactly — unlike FoundByMeDateFilter/
        # DnfDateFilter above, a NULL last_log_date EXCLUDES the cache here
        # (matches() returns False for ld is None, not True), so no
        # NULL-passthrough branch — just require non-NULL plus the range.
        from sqlalchemy import and_
        conditions = [Cache.last_log_date.is_not(None)]
        if self.from_date:
            conditions.append(Cache.last_log_date >= self.from_date)
        if self.to_date:
            conditions.append(Cache.last_log_date <= self.to_date)
        return query.filter(and_(*conditions))

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


class HiddenDateFilter(BaseFilter):
    """Keep caches whose hidden_date falls within an optional date range.

    #857: this used to be defined inline inside filter_dialog.py's _apply(),
    with no filter_registry entry and a to_dict() that dropped from_date/
    to_date entirely. That meant _load_filterset() had no branch to restore
    it from (checkboxes/dates reset on reopen) and saved filter profiles
    lost the dates on reload. Promoted to a proper class here, mirroring
    LastLogDateFilter's NULL-exclusion behaviour (a cache with no
    hidden_date does not match a hidden-date range).
    """
    filter_type = "hidden_date_range"

    def __init__(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ):
        self.from_date = from_date
        self.to_date = to_date

    def apply_to_query(self, query):
        from sqlalchemy import and_
        conditions = [Cache.hidden_date.is_not(None)]
        if self.from_date:
            conditions.append(Cache.hidden_date >= self.from_date)
        if self.to_date:
            conditions.append(Cache.hidden_date <= self.to_date)
        return query.filter(and_(*conditions))

    def matches(self, cache: Cache) -> bool:
        hd = cache.hidden_date
        if hd is None:
            return False
        hd = hd.replace(tzinfo=None)
        if self.from_date and hd < self.from_date:
            return False
        if self.to_date and hd > self.to_date:
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "filter_type": self.filter_type,
            "from_date": self.from_date.isoformat() if self.from_date else None,
            "to_date": self.to_date.isoformat() if self.to_date else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HiddenDateFilter":
        return cls(
            from_date=datetime.fromisoformat(data["from_date"]) if data.get("from_date") else None,
            to_date=datetime.fromisoformat(data["to_date"]) if data.get("to_date") else None,
        )


# ── GSAK-style date filter ────────────────────────────────────────────────────

# Filterable date fields: filter key -> Cache attribute. The keys are the
# cache table's column IDs (changed_date/creation_date display
# last_updated/imported_at there as well).
DATE_FILTER_FIELDS: dict[str, str] = {
    "last_found_date": "last_found_date",
    "hidden_date":     "hidden_date",
    "found_date":      "found_date",
    "dnf_date":        "dnf_date",
    "creation_date":   "imported_at",
    "last_gpx_update": "last_gpx_update",
    "last_log_date":   "last_log_date",
    "changed_date":    "last_updated",
}
# Fields whose DateFilter bounds may carry a time of day (to the minute) —
# timestamps OpenSAK records itself, where the time is meaningful.
DATETIME_FILTER_FIELDS = ("creation_date", "last_gpx_update", "changed_date")
DATE_OPS = ("on_or_before", "on_or_after", "equal", "between",
            "during", "not_during", "compare")
DATE_UNITS = ("days", "weeks", "months", "years")
DATE_COMPARE_OPS = ("equal", "older", "older_or_equal", "newer",
                    "newer_or_equal", "within", "outside")

# filter_type of the older from/to-only date filters -> the field they cover.
LEGACY_DATE_FILTER_FIELDS: dict[str, str] = {
    "hidden_date_range": "hidden_date",
    "found_by_me_date":  "found_date",
    "dnf_date":          "dnf_date",
    "last_log_date":     "last_log_date",
}


def _to_date(value) -> Optional[date]:
    """Calendar date of a datetime (tz dropped, like the other date filters)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).date()
    return value


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    """Parse a saved date; accepts both 'YYYY-MM-DD' and full ISO datetimes."""
    return datetime.fromisoformat(value).date() if value else None


def _parse_iso_bound(value: Optional[str]) -> Optional[date]:
    """Parse a saved DateFilter bound: a date for 'YYYY-MM-DD', a datetime
    when the string carries a time."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if "T" in value or " " in value else parsed.date()


def _to_bound(value, with_time: bool) -> Optional[date]:
    """A DateFilter bound: a naive datetime truncated to the minute when
    *with_time* and *value* has a time, otherwise its calendar date."""
    if with_time and isinstance(value, datetime):
        return value.replace(tzinfo=None, second=0, microsecond=0)
    return _to_date(value)


def _day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)


def _bound_start(b: date) -> datetime:
    """First instant covered by bound *b* (a date or a to-the-minute datetime)."""
    return b if isinstance(b, datetime) else _day_start(b)


def _bound_end(b: date) -> Optional[datetime]:
    """First instant after bound *b* (None if that overflows)."""
    try:
        if isinstance(b, datetime):
            return b + timedelta(minutes=1)
        return _day_start(b + timedelta(days=1))
    except OverflowError:
        return None


def _months_back(d: date, months: int) -> date:
    """*d* moved back *months* calendar months, clamped to the month's last day."""
    import calendar
    year, month0 = divmod(d.year * 12 + d.month - 1 - months, 12)
    month = month0 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _shift_back(d: date, amount: int, unit: str) -> date:
    """*d* moved back *amount* days/weeks/months/years (date.min on underflow)."""
    try:
        if unit == "weeks":
            return d - timedelta(weeks=amount)
        if unit == "months":
            return _months_back(d, amount)
        if unit == "years":
            return _months_back(d, amount * 12)
        return d - timedelta(days=amount)
    except (ValueError, OverflowError):
        return date.min


def _today() -> date:
    """Reference day for the relative "during the last N …" operators."""
    return date.today()


def _compare_diff(op: str, diff, days: int, absolute=abs):
    """Evaluate a DateFilter compare op on *diff* = this date - other date (in
    days). Works on ints and on SQL expressions (pass absolute=func.abs)."""
    if op == "equal":
        return diff == 0
    if op == "older":
        return diff < 0
    if op == "older_or_equal":
        return diff <= 0
    if op == "newer":
        return diff > 0
    if op == "newer_or_equal":
        return diff >= 0
    if op == "within":
        return absolute(diff) <= days
    return absolute(diff) > days  # outside


def _date_op_range(
    op: str, date1: Optional[date], date2: Optional[date], amount: int, unit: str,
) -> tuple[Optional[date], Optional[date]]:
    """Inclusive (lo, hi) bounds of a DateFilter operator other than compare."""
    if op == "on_or_before":
        return None, date1
    if op == "on_or_after":
        return date1, None
    if op == "equal":
        return date1, date1
    if op == "between":
        assert date1 is not None and date2 is not None  # enforced by callers
        return min(date1, date2, key=_bound_start), max(date1, date2, key=_bound_start)
    today = _today()  # during / not_during
    return _shift_back(today, amount, unit), today


def _date_range_sql(col, op: str, lo: Optional[date], hi: Optional[date]):
    """SQL form of _date_in_range() on datetime column *col*. Compares the raw
    column against day (or minute) boundaries, so an index on it stays usable."""
    from sqlalchemy import and_, not_, or_
    conditions = [col.is_not(None)]
    if lo is not None:
        conditions.append(col >= _bound_start(lo))
    end = _bound_end(hi) if hi is not None else None
    if end is not None:
        conditions.append(col < end)
    inside = and_(*conditions)
    if op == "not_during":
        return or_(col.is_(None), not_(inside))
    return inside


def _date_in_range(value, op: str, lo: Optional[date], hi: Optional[date]) -> bool:
    """Whether *value* (a date or datetime) passes a range operator: inside
    [lo, hi], or — for not_during — outside it or missing. Date bounds cover
    whole days, datetime bounds whole minutes."""
    if isinstance(value, datetime):
        value = value.replace(tzinfo=None)
    elif value is not None:
        value = _day_start(value)
    end = _bound_end(hi) if hi is not None else None
    inside = (
        value is not None
        and (lo is None or value >= _bound_start(lo))
        and (end is None or value < end)
    )
    return not inside if op == "not_during" else inside


class DateFilter(BaseFilter):
    """GSAK-style filter on one of the cache's date fields (DATE_FILTER_FIELDS).

    Operators — all compare calendar dates, ignoring the time of day:
      on_or_before / on_or_after / equal   relative to *date1*
                   (for DATETIME_FILTER_FIELDS, *date1*/*date2* may be
                   datetimes: the bound is then that minute instead of a day)
      between      *date1*..*date2* inclusive (in either order)
      during       within the last *amount* *unit*s, up to and including today
      not_during   the complement of "during": also matches caches without a
                   date, so "last found not during the last 2 years" keeps
                   never-found caches
      compare      against *other_field* of the same cache using *compare_op*;
                   "within"/"outside" take *compare_days*
    Apart from not_during, a cache without a date never matches.

    Supersedes the from/to-only HiddenDateFilter/FoundByMeDateFilter/
    DnfDateFilter/LastLogDateFilter, which stay registered so filter profiles
    saved before this still load; from_legacy() converts them.
    """
    filter_type = "date"

    def __init__(
        self,
        field: str,
        op: str,
        date1: Optional[date] = None,
        date2: Optional[date] = None,
        amount: int = 1,
        unit: str = "days",
        other_field: str = "hidden_date",
        compare_op: str = "equal",
        compare_days: int = 0,
    ):
        if field not in DATE_FILTER_FIELDS:
            raise ValueError(f"Unknown date field {field!r}")
        if op not in DATE_OPS:
            raise ValueError(f"Unknown date operator {op!r}")
        if unit not in DATE_UNITS:
            raise ValueError(f"Unknown date unit {unit!r}")
        if other_field not in DATE_FILTER_FIELDS:
            raise ValueError(f"Unknown date field {other_field!r}")
        if compare_op not in DATE_COMPARE_OPS:
            raise ValueError(f"Unknown date compare operator {compare_op!r}")
        self.field = field
        self.op = op
        with_time = field in DATETIME_FILTER_FIELDS
        self.date1 = _to_bound(date1, with_time)
        self.date2 = _to_bound(date2, with_time)
        if op in ("on_or_before", "on_or_after", "equal", "between") and self.date1 is None:
            raise ValueError(f"Date operator {op!r} needs date1")
        if op == "between" and self.date2 is None:
            raise ValueError("Date operator 'between' needs date2")
        self.amount = max(0, int(amount))
        self.unit = unit
        self.other_field = other_field
        self.compare_op = compare_op
        self.compare_days = max(0, int(compare_days))

    def _range(self) -> tuple[Optional[date], Optional[date]]:
        """Inclusive (lo, hi) bounds for every op except compare."""
        return _date_op_range(self.op, self.date1, self.date2, self.amount, self.unit)

    def apply_to_query(self, query):
        # Mirrors matches() exactly. Range bounds compare the raw column
        # against day boundaries (index-friendly); compare uses SQLite's
        # date()/julianday() so both sides are reduced to calendar dates.
        from sqlalchemy import func
        col = getattr(Cache, DATE_FILTER_FIELDS[self.field])
        if self.op == "compare":
            other = getattr(Cache, DATE_FILTER_FIELDS[self.other_field])
            diff = func.julianday(func.date(col)) - func.julianday(func.date(other))
            return query.filter(
                col.is_not(None), other.is_not(None),
                _compare_diff(self.compare_op, diff, self.compare_days, func.abs),
            )
        return query.filter(_date_range_sql(col, self.op, *self._range()))

    def matches(self, cache: Cache) -> bool:
        raw = getattr(cache, DATE_FILTER_FIELDS[self.field], None)
        value = _to_date(raw)
        if self.op == "compare":
            other = _to_date(getattr(cache, DATE_FILTER_FIELDS[self.other_field], None))
            if value is None or other is None:
                return False
            return _compare_diff(self.compare_op, (value - other).days, self.compare_days)
        return _date_in_range(raw, self.op, *self._range())

    def to_dict(self) -> dict:
        return {
            "filter_type": self.filter_type,
            "field": self.field,
            "op": self.op,
            "date1": self.date1.isoformat() if self.date1 else None,
            "date2": self.date2.isoformat() if self.date2 else None,
            "amount": self.amount,
            "unit": self.unit,
            "other_field": self.other_field,
            "compare_op": self.compare_op,
            "compare_days": self.compare_days,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DateFilter":
        return cls(
            field=data["field"],
            op=data["op"],
            date1=_parse_iso_bound(data.get("date1")),
            date2=_parse_iso_bound(data.get("date2")),
            amount=data.get("amount", 1),
            unit=data.get("unit", "days"),
            other_field=data.get("other_field", "hidden_date"),
            compare_op=data.get("compare_op", "equal"),
            compare_days=data.get("compare_days", 0),
        )

    @classmethod
    def from_legacy(cls, legacy: BaseFilter) -> Optional["DateFilter"]:
        """Equivalent DateFilter for an older from/to range filter
        (LEGACY_DATE_FILTER_FIELDS), or None if it has no date bounds.

        Not an exact match for FoundByMeDateFilter/DnfDateFilter, which also
        let found/DNF caches without a date through — DateFilter never
        matches a missing date."""
        field = LEGACY_DATE_FILTER_FIELDS.get(legacy.filter_type)
        from_date = getattr(legacy, "from_date", None)
        to_date = getattr(legacy, "to_date", None)
        if field is None or not (from_date or to_date):
            return None
        if from_date and to_date:
            return cls(field, "between", date1=from_date, date2=to_date)
        if from_date:
            return cls(field, "on_or_after", date1=from_date)
        return cls(field, "on_or_before", date1=to_date)

    def __repr__(self) -> str:
        return f"<DateFilter {self.to_dict()}>"


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


# ── Child waypoint filter ─────────────────────────────────────────────────────

# Text criteria of WaypointFilter, in GSAK's order: code, type, name, comment.
WAYPOINT_TEXT_FIELDS = ("code", "wp_type", "name", "comment")
# DateFilter's operators, minus compare (a waypoint has only the one date).
WAYPOINT_DATE_OPS = tuple(op for op in DATE_OPS if op != "compare")
WAYPOINT_COUNT_OPS = ("any", "equal", "at_least", "at_most", "between")


def _waypoint_text_column(field_name: str):
    from sqlalchemy import func
    if field_name == "code":
        return func.coalesce(Waypoint.wp_code, Waypoint.prefix)
    return getattr(Waypoint, field_name)


def _waypoint_text_value(wp, field_name: str) -> Optional[str]:
    if field_name == "code":
        return wp.wp_code or wp.prefix
    return getattr(wp, field_name)


class WaypointFilter(BaseFilter):
    """Keep caches by their child waypoints (GSAK's "Child waypoints" tab).

    A waypoint qualifies when it passes every criterion that is set: text
    matches on code/type/name/comment (same operators as TextMatchFilter), a
    date operator on wp_date (DateFilter's, except compare) and whether the
    user created it. The cache matches when the number of qualifying
    waypoints passes the count operator — "any" meaning at least one, so
    "count equal 0" together with e.g. type "Parking Area" finds caches
    without a parking waypoint. Code is the GSAK waypoint code (wp_code),
    falling back to the two-letter prefix for GPX imports, which have none.

    apply_filters() calls prepare() first, which counts the qualifying
    waypoints per cache in one query; matches() then only looks up the
    count, so it also works for LightweightCache rows. Without prepare()
    (e.g. on in-memory objects) matches() walks cache.waypoints instead.
    """
    filter_type = "waypoint"

    def __init__(
        self,
        texts: Optional[dict[str, tuple[str, str]]] = None,
        date_op: Optional[str] = None,
        date1: Optional[date] = None,
        date2: Optional[date] = None,
        date_amount: int = 1,
        date_unit: str = "days",
        by_user: Optional[bool] = None,
        count_op: str = "any",
        count1: int = 0,
        count2: int = 0,
    ):
        # texts: field -> (text, op); a match with nothing to compare is dropped.
        self.texts: dict[str, TextMatchFilter] = {}
        for field_name, (text, op) in (texts or {}).items():
            if field_name not in WAYPOINT_TEXT_FIELDS:
                raise ValueError(f"Unknown waypoint field {field_name!r}")
            match = TextMatchFilter(text, op)
            if not match._is_noop():
                self.texts[field_name] = match
        if date_op is not None and date_op not in WAYPOINT_DATE_OPS:
            raise ValueError(f"Unknown waypoint date operator {date_op!r}")
        if date_unit not in DATE_UNITS:
            raise ValueError(f"Unknown date unit {date_unit!r}")
        if count_op not in WAYPOINT_COUNT_OPS:
            raise ValueError(f"Unknown waypoint count operator {count_op!r}")
        self.date_op = date_op
        self.date1 = _to_date(date1)
        self.date2 = _to_date(date2)
        if date_op in ("on_or_before", "on_or_after", "equal", "between") and self.date1 is None:
            raise ValueError(f"Date operator {date_op!r} needs date1")
        if date_op == "between" and self.date2 is None:
            raise ValueError("Date operator 'between' needs date2")
        self.date_amount = max(0, int(date_amount))
        self.date_unit = date_unit
        self.by_user = by_user
        self.count_op = count_op
        self.count1 = max(0, int(count1))
        self.count2 = max(0, int(count2))
        # cache id -> qualifying waypoint count, filled by prepare()
        self._counts: Optional[dict[int, int]] = None

    @property
    def regex_error(self) -> Optional[str]:
        """First invalid regular expression among the text criteria, if any."""
        return next((m.regex_error for m in self.texts.values() if m.regex_error), None)

    def has_waypoint_criteria(self) -> bool:
        return bool(self.texts) or self.date_op is not None or self.by_user is not None

    def is_noop(self) -> bool:
        return self.count_op == "any" and not self.has_waypoint_criteria()

    # ── Per-waypoint criteria ────────────────────────────────────────────────

    def _date_range(self) -> tuple[Optional[date], Optional[date]]:
        assert self.date_op is not None
        return _date_op_range(self.date_op, self.date1, self.date2,
                              self.date_amount, self.date_unit)

    def waypoint_matches(self, wp) -> bool:
        """Whether waypoint *wp* (ORM object or row with the same fields) qualifies."""
        if self.by_user is not None and bool(wp.created_by_user) != self.by_user:
            return False
        if self.date_op is not None and not _date_in_range(
                _to_date(wp.wp_date), self.date_op, *self._date_range()):
            return False
        return all(
            match.match_value(_waypoint_text_value(wp, field_name))
            for field_name, match in self.texts.items()
        )

    def _sql_waypoint_conditions(self) -> tuple[list, bool]:
        """SQL conditions on the waypoints table plus whether they are exact.
        Inexact ones (regex, non-ASCII text) only pre-narrow the rows, and
        waypoint_matches() has to decide."""
        conditions: list = []
        exact = True
        if self.by_user is not None:
            conditions.append(Waypoint.created_by_user == self.by_user)
        if self.date_op is not None:
            conditions.append(_date_range_sql(Waypoint.wp_date, self.date_op, *self._date_range()))
        for field_name, match in self.texts.items():
            cond = match.sql_condition(_waypoint_text_column(field_name))
            if cond is None or not match.sql_exact:
                exact = False
            if cond is not None:
                conditions.append(cond)
        return conditions, exact

    # ── Count ────────────────────────────────────────────────────────────────

    def _count_bounds(self) -> tuple[int, Optional[int]]:
        """Inclusive (lo, hi) bounds on the qualifying waypoint count."""
        if self.count_op == "equal":
            return self.count1, self.count1
        if self.count_op == "at_least":
            return self.count1, None
        if self.count_op == "at_most":
            return 0, self.count1
        if self.count_op == "between":
            return min(self.count1, self.count2), max(self.count1, self.count2)
        return (1 if self.has_waypoint_criteria() else 0), None  # any

    def _count_ok(self, count: int) -> bool:
        lo, hi = self._count_bounds()
        return count >= lo and (hi is None or count <= hi)

    # ── BaseFilter ───────────────────────────────────────────────────────────

    def prepare(self, session: Session) -> None:
        """Count every cache's qualifying waypoints (see the class docstring)."""
        from sqlalchemy import func, select
        conditions, exact = self._sql_waypoint_conditions()
        if exact:
            rows = session.execute(
                select(Waypoint.cache_id, func.count(Waypoint.id))
                .where(*conditions)
                .group_by(Waypoint.cache_id)
            )
            self._counts = {cache_id: count for cache_id, count in rows}
            return
        counts: dict[int, int] = {}
        rows = session.execute(
            select(
                Waypoint.cache_id, Waypoint.wp_code, Waypoint.prefix,
                Waypoint.wp_type, Waypoint.name, Waypoint.comment,
                Waypoint.wp_date, Waypoint.created_by_user,
            ).where(*conditions)
        )
        for row in rows:
            if self.waypoint_matches(row):
                counts[row.cache_id] = counts.get(row.cache_id, 0) + 1
        self._counts = counts

    def apply_to_query(self, query):
        if self.is_noop():
            return query
        conditions, exact = self._sql_waypoint_conditions()
        if not exact:
            return None  # matches() decides, from prepare()'s counts
        from sqlalchemy import exists, func, select
        lo, hi = self._count_bounds()
        if lo == 1 and hi is None:
            # "at least one" — EXISTS stops at the first qualifying waypoint.
            return query.filter(
                exists().where(Waypoint.cache_id == Cache.id, *conditions).correlate(Cache)
            )
        count = (
            select(func.count(Waypoint.id))
            .where(Waypoint.cache_id == Cache.id, *conditions)
            .correlate(Cache)
            .scalar_subquery()
        )
        if lo > 0:
            query = query.filter(count >= lo)
        if hi is not None:
            query = query.filter(count <= hi)
        return query

    def matches(self, cache: Cache) -> bool:
        if self.is_noop():
            return True
        if self._counts is not None:
            count = self._counts.get(cache.id, 0)
        else:
            count = sum(1 for wp in cache.waypoints if self.waypoint_matches(wp))
        return self._count_ok(count)

    def to_dict(self) -> dict:
        return {
            "filter_type": self.filter_type,
            "texts": {f: {"text": m.text, "op": m.op} for f, m in self.texts.items()},
            "date_op": self.date_op,
            "date1": self.date1.isoformat() if self.date1 else None,
            "date2": self.date2.isoformat() if self.date2 else None,
            "date_amount": self.date_amount,
            "date_unit": self.date_unit,
            "by_user": self.by_user,
            "count_op": self.count_op,
            "count1": self.count1,
            "count2": self.count2,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WaypointFilter":
        return cls(
            texts={
                f: (spec.get("text", ""), spec.get("op", "contains"))
                for f, spec in (data.get("texts") or {}).items()
            },
            date_op=data.get("date_op"),
            date1=_parse_iso_date(data.get("date1")),
            date2=_parse_iso_date(data.get("date2")),
            date_amount=data.get("date_amount", 1),
            date_unit=data.get("date_unit", "days"),
            by_user=data.get("by_user"),
            count_op=data.get("count_op", "any"),
            count1=data.get("count1", 0),
            count2=data.get("count2", 0),
        )

    def __repr__(self) -> str:
        return f"<WaypointFilter {self.to_dict()}>"


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
    "line_polygon":  LinePolygonFilter,
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
    "hidden_date_range":  HiddenDateFilter,
    "date":               DateFilter,
    "text_search":        TextSearchFilter,
    "waypoint":           WaypointFilter,
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
    # ── Issue #658: additional GSAK-compatible columns — placeholders, same
    # reasoning as log_count/last_log/corrected above: real sorting happens
    # in CacheTableModel.sort() when the user clicks the column header.
    # These entries exist only so SortSpec(field) validates and a
    # remembered/restored sort on one of these columns doesn't crash at
    # startup (#658 follow-up — caught in Allan's manual test after the
    # first delivery: SortSpec's own field whitelist is separate from
    # CacheTableModel.sort()'s dispatch and had been missed).
    "gc_cache_id":     lambda c: 0,
    "changed_date":    lambda c: 0,
    "creation_date":   lambda c: 0,
    "elevation":       lambda c: 0,
    "find_count":      lambda c: 0,
    "gc_note":         lambda c: 0,
    "guid":            lambda c: 0,
    "hints":           lambda c: 0,
    "notes":           lambda c: 0,
    "owner_id":        lambda c: 0,
    "owner_name":      lambda c: 0,
    "source":          lambda c: 0,
    "url":             lambda c: 0,
    "watch":           lambda c: 0,
    # ── Issue #716: last_found_date, last_gpx_update, last_four_logs ────
    "last_found_date": lambda c: 0,
    "last_gpx_update": lambda c: 0,
    "last_four_logs":  lambda c: 0,
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
    SQL directly against the database, and every WaypointFilter's waypoint
    counts (WaypointFilter.prepare()). Must run before any Python-level
    matches() call touches one of those filters. Mutates the filter objects
    in place; returns nothing.
    """
    if not filterset:
        return
    for _f in _iter_filters(filterset):
        if isinstance(_f, WaypointFilter):
            _f.prepare(session)
    from sqlalchemy import text as _sa_text
    _where_filters = [
        _f for _f in _iter_filters(filterset)
        if isinstance(_f, WhereClauseFilter) and _f.sql
    ]
    _dist_udf_ready = False
    if any(_DISTANCE_RE.search(_f.sql) for _f in _where_filters):
        _register_distance_udf(session, distance_from)
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


def _register_distance_udf(
    session: Session,
    distance_from: Optional[tuple[float, float]],
) -> None:
    """The "distance" column in the caches table is never persisted — it is
    always NULL. Register a SQLite UDF so WHERE clauses can use "distance"
    as haversine distance from the home point (or *distance_from*). SQL
    references to "distance" are rewritten to the UDF call by the callers.
    """
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


def validate_where_sql(session: Session, sql: str) -> Optional[str]:
    """Return an error message if *sql* is not a valid WHERE clause for
    WhereClauseFilter, or None if it is valid. Rewrites "distance" exactly
    like _prepare_where_clause_filters() does, so what validates here is
    what actually runs. Shared by the Set Filter dialog's Where tab and the
    toolbar's quick Where box (#558).
    """
    from sqlalchemy import text as _sa_text
    try:
        if _DISTANCE_RE.search(sql):
            _register_distance_udf(session, None)
            sql = _DISTANCE_RE.sub("_opensak_dist(latitude, longitude)", sql)
        session.execute(_sa_text(f"SELECT 1 FROM caches WHERE ({sql}) LIMIT 0"))
        return None
    except Exception as exc:
        return str(exc)


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
    notes: bool

    @property
    def any(self) -> bool:
        return (
            self.attributes or self.trackables or self.logs
            or self.description or self.hint or self.notes
        )


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
    # Issue #752: search_notes was never checked here, so a TextSearchFilter
    # scoped to *only* User Notes (search_description/search_logs/
    # search_hint all False) fell through with every _RelationshipNeeds
    # flag False, and apply_filters_lightweight() then took the fast Core
    # select() path — where TextSearchFilter.apply_to_query()'s notes
    # exists() subquery raises an auto-correlation InvalidRequestError,
    # since that subquery was written for (and only ever tested against)
    # apply_filters()'s ORM Query, which correlates automatically in a way
    # Core select() doesn't. Treating notes like description/logs/hint here
    # routes notes-only searches to the full ORM path instead, which
    # already handles the same exists() subquery correctly.
    needs_notes = any(f.search_notes for f in _text_filters)
    return _RelationshipNeeds(
        attributes=needs_attributes, trackables=needs_trackables,
        logs=needs_logs, description=needs_description, hint=needs_hint,
        notes=needs_notes,
    )


# ── Main apply function ───────────────────────────────────────────────────────

def apply_filters(
    session: Session,
    filterset: Optional[FilterSet] = None,
    sort: Optional[SortSpec] = None,
    limit: Optional[int] = None,
    distance_from: Optional[tuple[float, float]] = None,
    columns: Optional[frozenset[str]] = None,
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
    columns      : Issue #658 — column IDs currently visible in the caller's
                   grid, if any. Only "hints" is looked at here (Notes is
                   already always loaded via the joinedload(Cache.user_note)
                   below, regardless of columns) — used to decide whether to
                   undefer Cache.encoded_hints even when no *filter* needs
                   it, so the "Hints" column doesn't lazy-load one row at a
                   time.

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
    needs_hint_column = columns is not None and "hints" in columns

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
    if not _needs.hint and not needs_hint_column:
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


# Every Cache column apply_filters_lightweight() selects — everything except
# the relationship collections and the three heavy/deferred text fields
# (short_description, long_description, encoded_hints). Kept as an explicit
# list (not introspected from Cache.__table__) so it's obvious at a glance
# exactly what LightweightCache does and doesn't carry. Defined here, before
# LightweightCache, because its __slots__ is built from this list.
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
    # Issue #716
    Cache.last_found_date, Cache.last_gpx_update, Cache.last_four_logs,
]


# Fields CacheTableModel.load() touches unconditionally, for every single
# row, via _update_distances() — not just for currently-visible rows the
# way data() is (Qt only calls data() for rows actually on screen, so that
# path stays fine with lazy delegation). Promoted to real __slots__ entries,
# set once at construction, so this specific hot loop gets direct attribute
# access instead of __getattr__ dispatch. Everything else stays lazily
# delegated to the underlying Row — see LightweightCache's docstring for why
# eagerly copying *every* selected column (not just these) turned out to be
# a net loss, not a win.
_LIGHTWEIGHT_EAGER_FIELDS = ("id", "distance", "bearing")


class LightweightCache:
    """Duck-types as a read-only Cache for display purposes (table/map).

    Wraps a SQLAlchemy Core Row of scalar Cache columns. Every column
    apply_filters_lightweight() selects is reachable by attribute, exactly
    like the corresponding attribute on a real Cache ORM instance — sort
    keys (SORT_FIELDS), filter matches() implementations, and display code
    that only touches scalar fields all work unchanged against this.

    Performance note — two things were tried and measured before landing
    on this design:
      1. Lazy delegation for every field via __getattr__ (the original
         version). Simple, but every single attribute access pays
         Python-level __getattr__ dispatch overhead — including
         .id/.distance/.bearing, which CacheTableModel._update_distances()
         touches on every one of hundreds of thousands of rows during
         table load. Measured: that overhead alone was ~0.33s of a ~0.53s
         table-load call at 100,000 rows, eating most of this path's own
         speed advantage over the full ORM route.
      2. Eagerly copying *every* selected column into its own __slots__
         entry at construction time (fixes #1, but overcorrects). Measured:
         this made apply_filters_lightweight()'s own fetch time roughly
         equal to apply_filters()'s full ORM hydration — the eager-copy
         loop (52 getattr+setattr pairs per row, for every row, whether or
         not most of those fields are ever read) cost about as much as the
         ORM hydration it was meant to avoid, erasing the fetch-side win
         that's the whole point of this function.
      3. This version: eagerly copy ONLY _LIGHTWEIGHT_EAGER_FIELDS above —
         the handful of fields touched unconditionally on every row during
         table load — and leave everything else lazily delegated. Qt only
         calls data() for currently-visible rows (view virtualization), so
         the remaining ~49 fields staying lazy doesn't cost anything at
         scale; fetch time stays fast because construction only eagerly
         copies 3 fields, not 52; table load's hot loop stays fast because
         those 3 fields don't pay __getattr__ dispatch.

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
    __slots__ = _LIGHTWEIGHT_EAGER_FIELDS + ("_row", "user_note", "_overrides")

    _MUTABLE_FIELDS = frozenset({"user_flag", "locked", "first_to_find"})

    def __init__(self, row, user_note: Optional[LightweightUserNote]):
        for name in _LIGHTWEIGHT_EAGER_FIELDS:
            object.__setattr__(self, name, getattr(row, name))
        object.__setattr__(self, "_row", row)
        object.__setattr__(self, "user_note", user_note)
        object.__setattr__(self, "_overrides", {})

    def __getattr__(self, name: str):
        # __getattr__ only fires when normal (slot/instance) lookup fails —
        # so this never runs for the eager fields above, only for anything
        # delegated to the underlying Row (or an override set via
        # __setattr__ below).
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


def apply_filters_lightweight(
    session: Session,
    filterset: Optional[FilterSet] = None,
    sort: Optional[SortSpec] = None,
    limit: Optional[int] = None,
    distance_from: Optional[tuple[float, float]] = None,
    push_limit: bool = False,
    columns: Optional[frozenset[str]] = None,
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

    push_limit (#639, default False — no behavior change for existing
    callers): when True, pushes `limit` into the SQL query itself
    (LIMIT after ORDER BY) instead of fetching every filtered row and
    slicing in Python. Only takes effect when it's actually safe — the
    whole filterset must be handled in SQL (no relationship filters, no
    OR-subtree left to Python, see fully_sql_pushed) AND the sort field
    must be SQL-sortable (sql_sorted) — a SQL LIMIT applied before a
    Python-only sort or Python-only filter pass would silently return the
    wrong N rows. Falls back to the existing Python-slice behavior
    whenever those conditions aren't met, same as if push_limit were
    False — always correct, just not always as fast. Measured directly
    (#639, 100,000-cache database, distance-sorted, no filter): Python
    slice ~3.0s regardless of limit size (500 through 5000 all fetch and
    construct every row before slicing); SQL LIMIT 0.31s-0.56s, correctly
    scaling with the requested limit.

    columns (#658): column IDs currently visible in the caller's grid, if
    any. LightweightCache/LightweightUserNote deliberately don't carry
    encoded_hints or the full UserNote.note text (see LightweightCache's
    docstring), so a visible "hints" or "notes" column needs the full
    apply_filters() ORM path — same fallback mechanism already used for
    filtersets that touch those fields via _filterset_relationship_needs().
    """
    _needs = _filterset_relationship_needs(filterset)
    needs_hint_column = columns is not None and "hints" in columns
    needs_notes_column = columns is not None and "notes" in columns
    if _needs.any or needs_hint_column or needs_notes_column:
        return apply_filters(session, filterset, sort, limit, distance_from, columns=columns)

    _prepare_where_clause_filters(session, filterset, distance_from)

    from sqlalchemy import select
    sel = (
        select(*_LIGHTWEIGHT_COLUMNS, UserNote.is_corrected, UserNote.corrected_lat, UserNote.corrected_lon)
        .select_from(Cache)
        .outerjoin(UserNote, UserNote.cache_id == Cache.id)
    )
    sel, fully_sql_pushed = _apply_sql_pushdown(sel, filterset)
    # A filterset of None trivially has nothing left for Python to check —
    # same nuance the final results-selection block below already relies on
    # via `if filterset and not fully_sql_pushed`.
    filter_fully_handled_in_sql = (not filterset) or fully_sql_pushed

    if sort is None:
        sort = SortSpec("name", ascending=True)

    sql_sorted = False
    order_expr = _sql_order_expr(sort.field)
    if order_expr is not None:
        direction = order_expr.asc() if sort.ascending else order_expr.desc()
        sel = sel.order_by(direction, Cache.id.asc())
        sql_sorted = True

    limit_pushed = False
    if push_limit and limit and filter_fully_handled_in_sql and sql_sorted:
        sel = sel.limit(limit)
        limit_pushed = True

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

    if limit and not limit_pushed:
        results = results[:limit]

    return results


def apply_filters_auto(
    session: Session,
    filterset: Optional[FilterSet] = None,
    sort: Optional[SortSpec] = None,
    limit: Optional[int] = None,
    distance_from: Optional[tuple[float, float]] = None,
    push_limit: bool = False,
    columns: Optional[frozenset[str]] = None,
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

    push_limit (#639): see apply_filters_lightweight()'s docstring —
    passed straight through, default False (no behavior change unless a
    caller opts in).

    columns (#658): see apply_filters_lightweight()'s docstring — passed
    straight through, default None (no behavior change unless a caller
    opts in).
    """
    return apply_filters_lightweight(session, filterset, sort, limit, distance_from, push_limit, columns)


# Issue #748: how far, in km, a cache's corrected coordinates are assumed
# to be able to diverge from its raw ones — used only to widen
# get_nearby_caches()'s SQL pre-filter net so a real-world puzzle/multi's
# corrected final still gets a chance to be picked up even when its posted
# coordinates fall outside radius_km. A fixed constant (not proportional to
# database size or radius_km), chosen generously above any realistic
# correction distance while staying cheap for the bounding-box query.
_CORRECTION_MARGIN_KM = 50.0


def effective_coords(cache) -> tuple[Optional[float], Optional[float]]:
    """Return the coordinates that should be used for distance/map-position
    purposes: a cache's corrected coordinates when set, else its original
    latitude/longitude.

    Issue #748: the split-screen map plots a cache at its *corrected*
    coordinates when set (map_widget.py's loadCaches() JS —
    `c.corrected ? c.clat : c.lat`), but get_nearby_caches() below used to
    compute radius membership purely from raw latitude/longitude via
    DistanceFilter. A cache whose corrected coordinates sit far from its
    raw ones (the normal case for a puzzle/multi's final vs. posted
    coordinates) could pass the raw-coordinate radius check yet render
    outside the drawn circle, or vice versa. This mirrors garmin.py's
    private _effective_coords() (kept separate here to avoid a gps->
    filters import) so filtering/sorting always agrees with what's
    actually plotted.
    """
    note = getattr(cache, "user_note", None)
    if note and getattr(note, "is_corrected", False):
        lat, lon = note.corrected_lat, note.corrected_lon
        if lat is not None and lon is not None:
            return lat, lon
    return cache.latitude, cache.longitude


def lookup_code_coords(session: Session, code: str) -> Optional[tuple[float, float]]:
    """Coordinates for a GSAK-style "W,<code>" point of the line/polygon filter.

    A cache code gives that cache's effective_coords() (corrected when set);
    otherwise a waypoint is looked up by its own code — wp_code (GSAK
    imports), else prefix + the parent cache's code without "GC" (PK12345
    for a GC12345 parking waypoint, as GPX files name them). None for an
    unknown code or a waypoint without coordinates.
    """
    code = code.strip().upper()
    if not code:
        return None
    cache = session.query(Cache).filter(Cache.gc_code == code).first()
    if cache is not None:
        lat, lon = effective_coords(cache)
        return (lat, lon) if lat is not None and lon is not None else None
    from sqlalchemy import func
    has_coords = (Waypoint.latitude.is_not(None), Waypoint.longitude.is_not(None))
    wp = (
        session.query(Waypoint)
        .filter(func.upper(Waypoint.wp_code) == code, *has_coords)
        .first()
    )
    if wp is None and len(code) > 2:
        wp = (
            session.query(Waypoint)
            .join(Cache, Waypoint.cache_id == Cache.id)
            .filter(
                func.upper(Waypoint.prefix) == code[:2],
                Cache.gc_code == "GC" + code[2:],
                *has_coords,
            )
            .first()
        )
    if wp is None or wp.latitude is None or wp.longitude is None:
        return None
    return wp.latitude, wp.longitude


def user_flagged_codes(session: Session) -> list[str]:
    """GC codes of every cache with the user flag set, in user sort order
    (then by code) — for the line/polygon filter's "Add flagged" button."""
    from sqlalchemy import func
    rows = (
        session.query(Cache.gc_code)
        .filter(Cache.user_flag == True)  # noqa: E712
        .order_by(func.coalesce(Cache.user_sort, 999999), Cache.gc_code)
        .all()
    )
    return [row[0] for row in rows]


def get_nearby_caches(
    session: Session,
    lat: float,
    lon: float,
    radius_km: float,
    max_caches: int,
    filterset: Optional["FilterSet"] = None,
) -> tuple[list, int]:
    """Issue #718: the selected cache plus its neighbours within *radius_km*,
    sorted nearest-first, capped to *max_caches*. Used by the split-screen
    map so it always shows the selected cache in correct local context,
    independent of the overview map's own map_max_caches cap and current
    sort order (which is what #718 was about: a cache outside that cap's
    dataset previously had no map at all, or the wrong one).

    Returns (caches, total) — caches is capped to max_caches, total is the
    full count found within radius_km (for a "showing nearest X of Y"
    label; see settings_dialog's map_nearby_* settings).

    filterset (#743): when given, ANDed together with the DistanceFilter so
    the split-screen "nearby" view respects whatever filter is currently
    active on the main list/overview map — previously this function always
    queried distance alone, so selecting a cache while a filter was active
    made the split-screen map silently revert to showing every nearby
    cache regardless of the filter ("map not staying filtered").

    Deliberately does NOT use SortSpec("distance", ...) — that sort field
    always orders by the persisted Cache.distance column, which is
    distance from the *home point* (see recalculate_distances()), not
    from (lat, lon) here. distance_from only rewrites WhereClauseFilter's
    raw-SQL UDF (_prepare_where_clause_filters), it does not change what
    "distance" means for sorting. So results are fetched unsorted at the
    SQL level and sorted in Python against (lat, lon) instead — cheap here
    because DistanceFilter's SQL bounding-box (apply_to_query) already
    keeps the fetched set geographically bounded regardless of database
    size, same box used by the overview map's DistanceFilter today.

    Issue #748: radius membership and sort order are computed from each
    candidate's effective_coords() (corrected coordinates when set),
    matching what map_widget.py actually plots — not DistanceFilter's own
    raw-coordinate match, which is only used here as a first-pass SQL
    net widened by _CORRECTION_MARGIN_KM (below) specifically to still
    catch caches whose corrected coordinates pull them into radius_km
    despite raw coordinates sitting further out — corrected coordinates
    for real-world puzzle/multi caches are essentially never further from
    their posted coordinates than that margin, so this keeps the net
    cheap (a fixed extra width, not proportional to database size) while
    covering the practical range of corrections.
    """
    if lat is None or lon is None:
        return [], 0

    fs = FilterSet()
    fs.add(DistanceFilter(lat=lat, lon=lon, max_km=radius_km + _CORRECTION_MARGIN_KM))
    if filterset is not None and len(filterset) > 0:
        fs.add(filterset)

    candidates = apply_filters_auto(session, fs)
    if not candidates:
        return [], 0

    eff = [effective_coords(c) for c in candidates]
    dists = distance_km_batch(
        lat, lon,
        [ec[0] for ec in eff],
        [ec[1] for ec in eff],
    )
    within_radius = [
        (c, d) for c, d, (elat, elon) in zip(candidates, dists, eff)
        if elat is not None and elon is not None and d <= radius_km
    ]
    total = len(within_radius)
    if not within_radius:
        return [], 0

    ordered = sorted(within_radius, key=lambda pair: pair[1])
    nearby = [c for c, _ in ordered[:max_caches]]
    return nearby, total


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

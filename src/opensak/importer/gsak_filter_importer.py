"""
src/opensak/importer/gsak_filter_importer.py — import saved GSAK filters as
OpenSAK filter profiles.

GSAK stores every saved filter as one row in ``gsak.db3``::

    TranslateFilters(Type, Description, Data)
        Type        'FI' for a filter
        Description the filter's name as shown in GSAK
        Data        the whole filter dialog, serialised (see _parse_blob)

OpenSAK stores one JSON file per filter profile (see
``opensak.filters.engine.FilterProfile``). This module converts the former
into the latter, and every criterion it touches ends up in exactly one of
three places:

  * a **native filter** — the criterion is expressible in the Set Filter
    dialog, so it becomes a real filter object and shows up in the tabs;
  * **executable SQL** in the Where tab — OpenSAK stores the data but has no
    GUI filter for it (watch list, elevation, user_data_1-4, …);
  * an **SQL comment** in the Where tab — nothing in OpenSAK can express the
    criterion, so it is written as a ``--`` line that documents what GSAK
    filtered on. Nothing about it runs; it is there so the filter can be
    finished by hand later.

The first two count as migrated, the third does not, which is what
``Conversion.coverage`` reports and what the import dialog totals up into its
migration-coverage statistic.

Confidence: decodings marked "ASSUMED" were inferred from real filter data,
not from GSAK documentation. Each assumption that actually affected a written
profile is listed in that profile's ``_gsak.notes`` block.

Supersedes the former ``scripts/migrate_gsak_filters.py``.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from opensak.filters.engine import (
    DATE_UNITS,
    LOG_SCOPE_CHOICES,
    AttributeFilter,
    AvailabilityFilter,
    CacheTypeFilter,
    ContainerFilter,
    CountryFilter,
    CountyFilter,
    DateFilter,
    DifficultyFilter,
    DistanceFilter,
    DnfFilter,
    FavoritePointsFilter,
    FilterProfile,
    FilterSet,
    FoundFilter,
    FtfFilter,
    GcCodeFilter,
    HasCorrectedFilter,
    HasTrackableFilter,
    LinePolygonFilter,
    LockedFilter,
    LogFilter,
    NameFilter,
    NoCorrectedFilter,
    NonPremiumFilter,
    NotFoundFilter,
    OwnerFilter,
    PlacedByFilter,
    PremiumFilter,
    StateFilter,
    TerrainFilter,
    TextSearchFilter,
    UserFlagFilter,
    WaypointFilter,
    WhereClauseFilter,
)

# ── GSAK → OpenSAK vocabularies ──────────────────────────────────────────────

# GSAK filter-dialog cache-type labels → OpenSAK cache_type string, routed
# through GSAK's one-letter codes so this stays consistent with
# GSAK_CACHE_TYPE_MAP in opensak/importer/gsak_importer.py (the authoritative
# mapping). None = GSAK has the type but OpenSAK has no equivalent.
GSAK_TYPE_LABEL_TO_OSAK: dict[str, Optional[str]] = {
    "Traditional":     "Traditional Cache",
    "Multi":           "Multi-cache",
    "Mystery":         "Unknown Cache",
    "Letterbox":       "Letterbox Hybrid",
    "Wherigo":         "Wherigo Cache",
    "Earth":           "Earthcache",
    "Virtual":         "Virtual Cache",
    "Webcam":          "Webcam Cache",
    "Event":           "Event Cache",
    "CITO":            "Cache In Trash Out Event",
    "Mega Event":      "Mega-Event Cache",
    "Giga Event":      "Giga-Event Cache",
    "L&F Event":       "Community Celebration Event",
    "Groundspeak HQ":  "Geocaching HQ Cache",
    "Block Party":     "Geocaching HQ Block Party",
    "Maze Exhibit":    "GPS Adventures Maze",
    "Project APE":     "Project A.P.E. Cache",
    "Lab Cache":       "Lab Cache",
    "Locationless":    "Locationless (Reverse) Cache",
    "Benchmark":       "Benchmark",   # not in CACHE_TYPES, but the GSAK importer
    "Other":           "Other",       # writes both of these verbatim
    "Waymark":         None,          # no OpenSAK equivalent
    "L&F Celebration": None,          # GSAK code D — deliberately unmapped
}

# GSAK container checkbox key → OpenSAK CONTAINER_SIZES value.
# Virtual/Unknown are GSAK placeholders for "no physical container" and both
# collapse to "Not chosen" on import (GSAK_CONTAINER_MAP in gsak_importer.py),
# so selecting either in GSAK cannot be reproduced exactly.
GSAK_CONTAINER_KEYS: dict[str, str] = {
    "cbxMicro":     "Micro",
    "cbxSmall":     "Small",
    "cbxRegular":   "Regular",
    "cbxLarge":     "Large",
    "cbxOther":     "Other",
    "cbxNotChosen": "Not chosen",
    "cbxVirtual":   "Not chosen",   # collapses — see above
    "chkUnknown":   "Not chosen",   # collapses — see above
}
_COLLAPSED_CONTAINERS = {"cbxVirtual", "chkUnknown"}

# Text-comparison combo (cbxDesc/Geocache-Name, cbxOwnerName/Owner-Name,
# cbxCountry/Land, …). Item order read off the GSAK dialog (German UI):
# Enthält, Enthält nicht, Gleich, Ungleich, Leer, Nicht leer, In Liste,
# RegEx, Nicht(RegExp) — which is exactly the operator list OpenSAK's
# TextMatchFilter offers under the same names, so the index maps straight
# onto a TEXT_OPS entry.
#
# Note there is no "off" entry: index 0 ("contains") is the default, and a
# text criterion counts as active whenever its edit box is non-empty — or,
# for empty/not-empty, on the operator alone.
TEXT_OP: dict[int, str] = {
    0: "contains",
    1: "not_contains",
    2: "equals",
    3: "not_equals",
    4: "empty",
    5: "not_empty",
    6: "in_list",
    7: "regex",
    8: "not_regex",
}
# Operators that need no value in the edit box.
TEXT_OP_VALUELESS = {"empty", "not_empty"}
# Operators SQLite cannot run: OpenSAK registers no REGEXP function on its
# connection, so a where_clause can never carry one. (The GUI's text filters
# do regex in Python, so a native filter still handles these.)
TEXT_OP_NO_SQL = {"regex", "not_regex"}

# Numeric-comparison combo (cbxFavorite/Favoritenpunkte, cbxLogCount, …):
# Beliebig, Kleiner gleich, Größer gleich, Gleich, Zwischen (Inklusive).
NUM_OP: dict[int, Optional[str]] = {
    0: None,            # "Beliebig" = criterion off
    1: "at_most",
    2: "at_least",
    3: "equals",
    4: "between",
}
# The same operators under the names OpenSAK's log/waypoint count rows use.
NUM_OP_TO_COUNT_OP: dict[str, str] = {
    "at_most": "at_most",
    "at_least": "at_least",
    "equals": "equal",
    "between": "between",
}

# Difficulty/Terrain use the same operators minus "Beliebig" — the dialog
# opens on "Kleiner gleich" with the value 5.0, which is how "no D/T
# restriction" is stored. ASSUMED order (the dialog only ever shows the
# collapsed combo); a criterion is therefore only written out when it
# actually narrows the 1.0–5.0 range.
DT_OP: dict[int, str] = {
    0: "at_most",
    1: "at_least",
    2: "equals",
    3: "between",
}

# Date combos (cbxFound/Letztes Funddatum, cbxDNFDate/DNF-Datum, …):
# Am oder vor, Am oder nach, Gleich, Zwischen (Inklusive), Während,
# Nicht während, Verglichen mit, Beliebig — mapped onto DateFilter's own
# operator names (DATE_OPS), which were modelled on this very list.
DATE_OP: dict[int, Optional[str]] = {
    0: "on_or_before",
    1: "on_or_after",
    2: "equal",
    3: "between",
    4: "during",          # rolling window
    5: "not_during",      # rolling window
    6: "compare",         # two date columns — GSAK records only one of them
    7: None,              # "Beliebig" = criterion off
}

# The "Logs" and "Unterwegspunkte" tabs have their own date combos (cbxLogDate,
# cbxcDate) whose stored value is 6 in every saved filter — including filters
# whose date boxes clearly are not meant to restrict anything ("Noch nie
# gefunden", "Letzte 2 DNF"), and including ones that do use the rest of the
# tab. There is nothing for a log/waypoint date to be "compared with", so
# ASSUMED: these two combos simply lack the "Verglichen mit" entry, which puts
# their "Beliebig" at index 6 instead of 7.
DATE_OP_NO_COMPARE: dict[int, Optional[str]] = {
    0: "on_or_before",
    1: "on_or_after",
    2: "equal",
    3: "between",
    4: "during",
    5: "not_during",
    6: None,          # "Beliebig" = criterion off
}

# "Mein Funddatum" (cbxUserFound) does NOT use the list above: its stored value
# is 0 in every saved filter while every other date combo in the same blob
# holds 7 ("Beliebig"), and filters that store 0 also tick both "Gefunden" and
# "Nicht gefunden" — which would contradict any my-found-date restriction. So 0
# is this combo's "off" state and the date boxes beside it are stale UI values.
# The remaining indices are unknown and are reported rather than guessed.
DATE_OP_MY_FOUND: dict[int, Optional[str]] = {0: None}

# GSAK's rolling-window row reads "Während den letzten [n] [unit]": the edit
# box holds n, the combo beside it the unit. ASSUMED (every real filter leaves
# the row empty): that combo lists days / weeks / months / years — OpenSAK's
# DATE_UNITS, in the same order.
DURING_UNITS: tuple[str, ...] = DATE_UNITS

# GSAK's child-waypoint type dropdown (cbxCtype2) stores an index, not a name.
# ASSUMED from real filters: the list holds the six GPX waypoint types in
# alphabetical order — "Parking", a filter that selects caches having a parking
# waypoint, stores index 1, and the "…ToCorrect" filters, which look for
# mystery caches with no final yet, store index 0 together with "waypoint
# count = 0".
WP_TYPE_BY_INDEX: tuple[str, ...] = (
    "Final Location",
    "Parking Area",
    "Physical Stage",
    "Reference Point",
    "Trailhead",
    "Virtual Stage",
)

# GSAK compass-quadrant checkbox → the bearing range it covers, in degrees
# clockwise from north. The eight 45° sectors are centred on their compass
# point, so N spans 337.5°–22.5° and wraps around 0.
_QUADRANTS: dict[str, tuple[float, float]] = {
    "cbxN":  (337.5, 22.5),
    "cbxNe": (22.5, 67.5),   "cbxNE": (22.5, 67.5),
    "cbxE":  (67.5, 112.5),
    "cbxSE": (112.5, 157.5),
    "cbxS":  (157.5, 202.5),
    "cbxSW": (202.5, 247.5),
    "cbxW":  (247.5, 292.5),
    "cbxNW": (292.5, 337.5),
}


def _dt_value(index: int) -> float:
    """GSAK D/T value combo index → rating: index 0 = 1.0 … index 8 = 5.0."""
    return 1.0 + 0.5 * index


def _escape_sql(value: str) -> str:
    return value.replace("'", "''")


def _wrap(text: str, width: int) -> list[str]:
    """Break *text* into lines of at most *width* characters, on spaces.

    A word longer than *width* (a URL, a long SQL expression) is left whole
    rather than cut, so nothing that might be uncommented later gets broken
    in the middle.
    """
    words = " ".join(text.split()).split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# ── GSAK where-clause → OpenSAK SQL ──────────────────────────────────────────

# Column renames, applied case-insensitively on word boundaries. OpenSAK's
# WhereClauseFilter runs "SELECT id FROM caches WHERE (<sql>)", so scalar
# subqueries against the related tables are legal.
WHERE_COLUMN_MAP: dict[str, str] = {
    # ── Caches table ────────────────────────────────────────────────────────
    "code":          "gc_code",
    "cachetype":     "cache_type",
    "placedby":      "placed_by",
    "ownername":     "owner_name",
    "ownerid":       "owner_id",
    "placeddate":    "hidden_date",
    "favpoints":     "favorite_points",
    "userflag":      "user_flag",
    "usersort":      "user_sort",
    "userdata":      "user_data_1",
    "user2":         "user_data_2",
    "user3":         "user_data_3",
    "user4":         "user_data_4",
    "userdate":      "found_date",
    "founddate":     "found_date",
    "foundbymedate": "found_date",
    "lastfounddate": "last_found_date",
    "dnfdate":       "dnf_date",
    "ftf":           "first_to_find",
    "lock":          "locked",
    "ispremium":     "premium_only",
    "pmonly":        "premium_only",
    "cacheid":       "gc_cache_id",
    "gcnote":        "gc_note",
    "lastgpxdate":   "last_gpx_update",
    "changed":       "last_updated",
    "created":       "imported_at",
    "lastlogdate":   "last_log_date",
    "longdescription":  "long_description",
    "shortdescription": "short_description",
    "hints":         "encoded_hints",
    "tbcount":       "trackable_count",
    "numlogs":       "log_count",
    "foundcount":    "found_log_count",
    # GSAK keeps these on the cache row itself (empty string / 0 when unset);
    # OpenSAK keeps them in user_notes and simply has no row — hence coalesce,
    # so "= ''" style GSAK tests keep working.
    "hascorrected":  "coalesce((SELECT is_corrected FROM user_notes WHERE user_notes.cache_id = caches.id), 0)",
    "usernote":      "coalesce((SELECT note FROM user_notes WHERE user_notes.cache_id = caches.id), '')",
    "userlat":       "(SELECT corrected_lat FROM user_notes WHERE user_notes.cache_id = caches.id)",
    "userlon":       "(SELECT corrected_lon FROM user_notes WHERE user_notes.cache_id = caches.id)",
    "lastuserdate":  "(SELECT updated_at FROM user_notes WHERE user_notes.cache_id = caches.id)",
    # Identical names, listed so they are not reported as unknown identifiers.
    "name":          "name",
    "container":     "container",
    "difficulty":    "difficulty",
    "terrain":       "terrain",
    "latitude":      "latitude",
    "longitude":     "longitude",
    "latoriginal":   "latitude",
    "lonoriginal":   "longitude",
    "elevation":     "elevation",
    "country":       "country",
    "state":         "state",
    "county":        "county",
    "found":         "found",
    "dnf":           "dnf",
    "archived":      "archived",
    "watch":         "watch",
    "color":         "color",
    "guid":          "guid",
    "bearing":       "bearing",
    "distance":      "distance",   # OpenSAK rewrites this to its haversine UDF
    "url":           "url",
}

# GSAK sub-table columns → their OpenSAK counterparts.
#
# GSAK links its child tables to the parent cache by GC CODE (Logs.lParent,
# Waypoints.cParent, Attributes.aCode, Corrected.kCode); OpenSAK links them by
# the numeric caches.id. A rename on its own would therefore produce SQL that
# runs but matches nothing, so every rename below is followed by
# _relink_code_joins(), which rewrites GSAK's code-based joins into id-based
# ones.
WHERE_SUBTABLE_MAP: dict[str, str] = {
    # LOGS
    "lparent":   "cache_id",
    "llogid":    "log_id",
    "ltype":     "log_type",
    "ldate":     "log_date",
    "lby":       "finder",
    "lownerid":  "finder_id",
    "ltext":     "text",
    "lencoded":  "text_encoded",
    # WAYPOINTS
    "cparent":   "cache_id",
    "ccode":     "wp_code",
    "cprefix":   "prefix",
    "cname":     "name",
    "ctype":     "wp_type",
    "cdate":     "wp_date",
    "clat":      "latitude",
    "clon":      "longitude",
    "ccomment":  "comment",
    "cbyuser":   "created_by_user",
    "cflag":     "wp_flag",
    "curl":      "url",
    # ATTRIBUTES
    "acode":     "cache_id",
    "aid":       "attribute_id",
    "ainc":      "is_on",
    # CORRECTED → user_notes (see WHERE_TABLE_MAP)
    "kcode":     "cache_id",
    "kafterlat": "corrected_lat",
    "kafterlon": "corrected_lon",
}

# GSAK table names → OpenSAK table names. Logs/Waypoints/Attributes keep their
# names; only Corrected moves.
WHERE_TABLE_MAP: dict[str, str] = {
    "corrected": "user_notes",
}

# GSAK columns whose OpenSAK counterpart carries the opposite meaning.
WHERE_INVERTED: dict[str, str] = {
    "tempdisabled": "(NOT available)",
}

# Mappings that are close but not exact. They still translate (the clause stays
# "verified"), but the difference is recorded in the profile's _gsak.notes.
WHERE_SOFT_NOTES: dict[str, str] = {
    "lastgpxdate": "GSAK's LastGPXDate (last GPX/API refresh) became OpenSAK's last_gpx_update — "
                   "the timestamp of the last import pass that touched the cache, which is the "
                   "same idea but is stamped locally, not taken from the source file",
    "changed": "GSAK's Changed (the GC.com-side listing change date) became OpenSAK's "
               "last_updated, which the GSAK importer fills from exactly that column",
    "created": "GSAK's Created (record-creation date) became OpenSAK's imported_at, which is "
               "when THIS OpenSAK database first saw the cache — not when GSAK first saw it",
    "foundbymedate": "GSAK's FoundByMeDate became OpenSAK's found_date",
    "lastuserdate": "GSAK's LastUserDate became a sub-select on OpenSAK's user_notes.updated_at "
                    "(NULL for caches that have no personal note or corrected coordinates)",
    "userdata": "GSAK's UserData became OpenSAK's user_data_1",
    "tbcount": "GSAK's tbCount became OpenSAK's trackable_count (filled in on import)",
    "numlogs": "GSAK's NumLogs became OpenSAK's cached log_count column",
    "foundcount": "GSAK's FoundCount is a 0/1 \"found by me\" flag; it became OpenSAK's "
                  "found_log_count (how many of the user's own found-type logs the cache has), "
                  "which is >= 1 for every cache GSAK would have counted",
    "hascorrected": "GSAK's HasCorrected became a sub-select on OpenSAK's user_notes.is_corrected",
    "usernote": "GSAK's UserNote became a sub-select on OpenSAK's user_notes.note",
    "latitude": "GSAK's Latitude/Longitude hold the CORRECTED position for a solved cache; "
                "OpenSAK's caches.latitude/longitude always hold the posted position and keep "
                "the correction in user_notes.corrected_lat/corrected_lon. Wrap the column in "
                "coalesce((SELECT corrected_lat FROM user_notes WHERE user_notes.cache_id = "
                "caches.id), latitude) if the clause meant the corrected position",
    "kafterlat": "GSAK's Corrected table became OpenSAK's user_notes (kCode → cache_id, "
                 "kAfterLat/kAfterLon → corrected_lat/corrected_lon)",
    "clat": "GSAK's child-waypoint coordinates (cLat/cLon) became OpenSAK's "
            "waypoints.latitude/longitude, which are NULL — not 0.0 — when unknown",
}
WHERE_SOFT_NOTES["latoriginal"] = (
    "GSAK's LatOriginal/LonOriginal (the posted position of a solved cache) became OpenSAK's "
    "caches.latitude/longitude, which always hold the posted position — the correction lives "
    "in user_notes.corrected_lat/corrected_lon"
)
WHERE_SOFT_NOTES["lonoriginal"] = WHERE_SOFT_NOTES["latoriginal"]
WHERE_SOFT_NOTES["longitude"] = WHERE_SOFT_NOTES["latitude"]
WHERE_SOFT_NOTES["kafterlon"] = WHERE_SOFT_NOTES["kafterlat"]
WHERE_SOFT_NOTES["kcode"] = WHERE_SOFT_NOTES["kafterlat"]
WHERE_SOFT_NOTES["clon"] = WHERE_SOFT_NOTES["clat"]

# GSAK identifiers with no mechanical translation — each one makes the
# translated clause "unverified", which keeps it out of the executable SQL and
# puts it in the Where tab as a comment instead.
WHERE_UNMAPPABLE: dict[str, str] = {
    "isowner":   "GSAK's isOwner has no OpenSAK column — set your geocaching.com account name "
                 "in Settings to have it translated to an owner_name test",
    "ltime":     "GSAK's logs.lTime is a separate time-of-day column; OpenSAK folds date and "
                 "time into logs.log_date, so compare against that instead",
    "kbeforelat": "GSAK's Corrected.kBeforeLat/kBeforeLon (the posted position of a solved "
                  "cache) became OpenSAK's caches.latitude/longitude — they are not columns of "
                  "user_notes, so this sub-select needs a join back to caches",
    "cachememo": "GSAK's CacheMemo table is split across OpenSAK's caches / user_notes columns",
    "cachesall": "GSAK's CachesAll view (all databases at once) has no OpenSAK counterpart",
    "custom":    "GSAK's Custom/CustomLocal tables (user-defined columns) have no OpenSAK "
                 "counterpart — only user_data_1-4 exist",
    "customlocal": "GSAK's Custom/CustomLocal tables (user-defined columns) have no OpenSAK "
                   "counterpart — only user_data_1-4 exist",
    "travelbugs": "GSAK's CacheMemo.TravelBugs text maps to OpenSAK's trackables table / "
                  "trackable_count",
    "favperc":   "GSAK's FavPerc (favourite percentage) is not stored by OpenSAK",
    "labid":     "GSAK's LabId is not stored by OpenSAK",
    "symbol":    "GSAK's Symbol (GPX symbol name) is not stored by OpenSAK",
}
WHERE_UNMAPPABLE["kbeforelon"] = WHERE_UNMAPPABLE["kbeforelat"]

# GSAK Status ('A' active, 'T' temporarily disabled, 'X' archived) → the
# equivalent test on OpenSAK's archived / available booleans. Verified 1:1
# against the importer (gsak_importer.py: _STATUS_ARCHIVED / _STATUS_AVAILABLE).
_STATUS_SQL: dict[str, str] = {
    "A": "(available AND NOT archived)",
    "T": "(NOT available AND NOT archived)",
    "X": "archived",
}
_STATUS_CMP_RE = re.compile(r"\bstatus\b\s*(=|<>|!=)\s*'([ATX])'", re.IGNORECASE)

# GSAK macro globals and macro functions (%g_… / $g_… / g_Distance(…)) that can
# appear in a saved where clause and only resolve while a GSAK macro is running.
# GSAK's kBeforeLat/kAfterLon and friends are NOT listed here: inside a where
# clause they are columns of the Corrected table, and are mapped as such.
_MACRO_GLOBAL_RE = re.compile(r"^g_", re.IGNORECASE)

# GSAK one-letter cache-type codes as used inside where clauses.
GSAK_CODE_TO_OSAK: dict[str, Optional[str]] = {
    "T": "Traditional Cache",   "M": "Multi-cache",
    "U": "Unknown Cache",       "B": "Letterbox Hybrid",
    "W": "Webcam Cache",        "V": "Virtual Cache",
    "E": "Event Cache",         "C": "Cache In Trash Out Event",
    "R": "Earthcache",          "I": "Wherigo Cache",
    "L": "Locationless (Reverse) Cache",
    "O": "Other",               "G": "Benchmark",
    "Q": "Lab Cache",           "A": "Project A.P.E. Cache",
    "H": "Geocaching HQ Cache", "J": "Giga-Event Cache",
    "P": "Geocaching HQ Block Party",
    "X": "GPS Adventures Maze", "Z": "Mega-Event Cache",
    "F": "Community Celebration Event",
    "D": None, "Y": None,
}

# OpenSAK columns (and sub-selects) that hold numbers. GSAK's saved SQL often
# quotes boolean/integer literals ("UserFlag = '1'"); against a plain column
# SQLite's INTEGER affinity still coerces that, but against an expression —
# such as the coalesce() sub-selects above — it does not: "SELECT 0 = '0'"
# is false. So quoted numeric literals are unquoted after translation.
_NUMERIC_COLUMNS = {
    "found", "archived", "available", "premium_only", "user_flag", "locked",
    "dnf", "first_to_find", "trackable_count", "favorite_points", "user_sort",
    "difficulty", "terrain", "latitude", "longitude", "elevation",
    "is_corrected", "corrected_lat", "corrected_lon", "attribute_id", "is_on",
    "watch", "log_count", "found_log_count", "waypoint_count", "find_count",
    "bearing", "distance", "created_by_user", "wp_flag", "text_encoded",
}
_NUM_EXPR = r"(?:coalesce\((?:[^()']|\([^()]*\))*\)|[A-Za-z_][A-Za-z_0-9]*)"
_QUOTED_NUM_RE = re.compile(
    rf"(?P<expr>{_NUM_EXPR})\s*(?P<op>=|<>|!=|>=|<=|>|<)\s*'(?P<val>-?\d+(?:\.\d+)?)'"
)
_QUOTED_NUM_REV_RE = re.compile(
    rf"'(?P<val>-?\d+(?:\.\d+)?)'\s*(?P<op>=|<>|!=|>=|<=|>|<)\s*(?P<expr>{_NUM_EXPR})"
)


def _unquote_numeric_literals(sql: str, notes: list[str]) -> str:
    """Drop the quotes around numeric literals compared against numeric columns."""

    def _fix(m: re.Match, reverse: bool = False) -> str:
        expr = m.group("expr")
        if not any(col in expr.lower() for col in _NUMERIC_COLUMNS):
            return m.group(0)
        notes.append(
            "where: GSAK compared a numeric column against a quoted literal "
            "(e.g. \"= '1'\"); the quotes were removed so SQLite compares numbers"
        )
        val, op = m.group("val"), m.group("op")
        return f"{val} {op} {expr}" if reverse else f"{expr} {op} {val}"

    sql = _QUOTED_NUM_RE.sub(_fix, sql)
    return _QUOTED_NUM_REV_RE.sub(lambda m: _fix(m, reverse=True), sql)


_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
# Identifiers are matched together with any table qualifier ("c.hascorrected",
# not "c" and "hascorrected" separately): several GSAK columns map to a
# correlated sub-select rather than a plain column name, and pasting one of
# those in after a "c." would produce invalid SQL.
_IDENT_RE = re.compile(
    r"\b(?:(?P<qual>[A-Za-z_][A-Za-z_0-9]*)\s*\.\s*)?(?P<word>[A-Za-z_][A-Za-z_0-9]*)\b")
_CACHETYPE_IN_RE = re.compile(r"\bcachetype\b\s+(not\s+)?in\s*\(([^)]*)\)", re.IGNORECASE)
# GSAK's Latitude/Longitude hold the CORRECTED position of a solved cache and
# LatOriginal/LonOriginal the posted one; OpenSAK's caches.latitude/longitude
# are the posted position and the correction lives in user_notes. Both GSAK
# columns therefore translate to the same OpenSAK column, so a clause that
# compares the two collapses into a tautology and has to be flagged.
_GSAK_ORIGINAL_POS_RE = re.compile(r"\b(?:latoriginal|lonoriginal)\b", re.IGNORECASE)
_GSAK_CORRECTED_POS_RE = re.compile(r"\b(?:latitude|longitude)\b", re.IGNORECASE)
_CACHETYPE_CMP_RE = re.compile(r"\bcachetype\b\s*(=|<>|!=)\s*'([A-Za-z])'", re.IGNORECASE)

# SQL keywords, functions and OpenSAK table names that are never column names.
_SQL_WORDS = {
    "select", "from", "where", "and", "or", "not", "in", "is", "null", "like",
    "between", "exists", "as", "on", "join", "left", "right", "full", "cross",
    "natural", "using", "inner", "outer", "group",
    "by", "order", "having", "union", "intersect", "except", "with",
    "recursive", "values", "all", "distinct", "case", "when", "then",
    "else", "end", "asc", "desc", "limit", "offset", "date", "datetime", "now",
    "localtime", "utc", "day", "days", "month", "months", "year", "years",
    "hour", "minute", "second", "weekday", "start", "of",
    "julianday", "unixepoch", "strftime", "count", "sum", "total", "min",
    "max", "avg", "length", "instr", "iif", "printf", "format", "typeof",
    "group_concat", "glob", "escape", "random", "hex", "ltrim", "rtrim",
    "lower", "upper", "substr", "substring", "trim", "replace", "cast",
    "int", "integer", "real", "text", "decimal", "numeric", "float", "double",
    "boolean", "char", "varchar", "blob", "collate", "nocase", "binary",
    "coalesce", "ifnull", "nullif", "abs", "round", "true",
    "false", "caches", "logs", "attributes", "user_notes", "waypoints",
    "trackables",
}

# Every OpenSAK column name (plus its table names), so an identifier that is
# ALREADY an OpenSAK column — because a rewrite earlier in the pipeline
# produced it, or because the GSAK clause happened to use the same word — is
# not reported as unrecognised.
_OSAK_COLUMNS: set[str] = (
    set(WHERE_COLUMN_MAP.values())
    | set(WHERE_SUBTABLE_MAP.values())
    | {
        "id", "cache_id", "gc_code", "cache_type", "available", "archived",
        "short_desc_html", "long_desc_html", "found_date", "dnf_date",
        "first_to_find", "user_data_1", "user_data_2", "user_data_3",
        "user_data_4", "favorite_points", "gc_note", "guid", "watch",
        "gc_cache_id", "find_count", "log_count", "trackable_count",
        "found_log_count", "last_log_date", "waypoint_count",
        "parent_gc_code", "locked", "location_source", "location_basis",
        "location_updated", "location_dataset", "imported_at", "source_file",
        "last_found_date", "last_gpx_update", "last_four_logs", "last_updated",
        "is_corrected", "corrected_lat", "corrected_lon", "updated_at", "note",
        "wp_type", "wp_code", "wp_date", "wp_flag", "prefix", "comment",
        "description", "created_by_user", "log_id", "log_type", "log_date",
        "finder", "finder_id", "text_encoded", "logged_by_owner",
        "tracking_code", "ref", "attribute_id", "is_on",
    }
) - {v for v in WHERE_COLUMN_MAP.values() if not v.isidentifier()}


def _split_sql_literals(sql: str) -> list[tuple[bool, str]]:
    """Split *sql* into (is_string_literal, chunk) parts so identifier
    rewriting never touches the inside of a quoted string."""
    parts: list[tuple[bool, str]] = []
    pos = 0
    for m in _STRING_LITERAL_RE.finditer(sql):
        if m.start() > pos:
            parts.append((False, sql[pos:m.start()]))
        parts.append((True, m.group(0)))
        pos = m.end()
    if pos < len(sql):
        parts.append((False, sql[pos:]))
    return parts


_UNMAPPED_TYPE_NOTE = (
    "where: GSAK cache-type code {codes} has no OpenSAK cache type — the GSAK importer files "
    "such caches under 'Unknown Cache', where they are indistinguishable from real mystery "
    "caches, so the code was dropped from the type list rather than left as raw GSAK SQL"
)


def _expand_cachetype_codes(sql: str, warnings: list[str], notes: list[str]) -> str:
    """Rewrite GSAK's single-letter cachetype comparisons to OpenSAK names.

    Codes with no OpenSAK cache type (GSAK's D / Y) are dropped from the list
    and recorded as a note rather than a warning: leaving the raw GSAK letter
    in place would produce SQL that silently matches nothing, and the importer
    collapses those types into "Unknown Cache" anyway, so no translation can
    single them out.
    """

    def _names(codes: str) -> Optional[str]:
        out: list[str] = []
        dropped: list[str] = []
        for raw in codes.split(","):
            code = raw.strip().strip("'").upper()
            if not code:
                continue
            name = GSAK_CODE_TO_OSAK.get(code)
            if name is None:
                dropped.append(code)
                continue
            quoted = "'" + name.replace("'", "''") + "'"
            if quoted not in out:
                out.append(quoted)
        if dropped:
            notes.append(_UNMAPPED_TYPE_NOTE.format(
                codes=", ".join(f"'{c}'" for c in sorted(set(dropped)))))
        return ", ".join(out) if out else None

    def _sub_in(m: re.Match) -> str:
        names = _names(m.group(2))
        if names is None:
            # Every listed code was unmappable: "in (…)" can never be true and
            # "not in (…)" is always true.
            warnings.append(
                "where: a cachetype list held only GSAK codes with no OpenSAK equivalent — "
                "the comparison was left unchanged and needs a manual decision"
            )
            return m.group(0)
        return f"cache_type {'not ' if m.group(1) else ''}in ({names})"

    def _sub_cmp(m: re.Match) -> str:
        code, op = m.group(2).upper(), m.group(1)
        name = GSAK_CODE_TO_OSAK.get(code)
        if name is None:
            # No OpenSAK cache type can stand for this code, so the equality can
            # never hold (and its negation always does).
            notes.append(_UNMAPPED_TYPE_NOTE.format(codes=f"'{code}'"))
            return "1 = 0" if op == "=" else "1 = 1"
        return f"cache_type {op} '{name}'"

    sql = _CACHETYPE_IN_RE.sub(_sub_in, sql)
    return _CACHETYPE_CMP_RE.sub(_sub_cmp, sql)


def _expand_status(sql: str, notes: list[str]) -> str:
    """Rewrite GSAK's Status = 'A'/'T'/'X' tests onto archived / available."""

    def _sub(m: re.Match) -> str:
        expr = _STATUS_SQL[m.group(2).upper()]
        notes.append(
            "where: GSAK's Status ('A' active / 'T' temporarily disabled / 'X' archived) was "
            "rewritten onto OpenSAK's archived / available booleans, which the GSAK importer "
            "derives from exactly that column"
        )
        return expr if m.group(1) == "=" else f"NOT {expr}"

    return _STATUS_CMP_RE.sub(_sub, sql)


_ALIAS_RE = re.compile(
    r"\b(?:from|join)\s+[A-Za-z_][A-Za-z_0-9]*\s+(?:as\s+)?([A-Za-z_][A-Za-z_0-9]*)",
    re.IGNORECASE,
)


# ── GSAK code-joins → OpenSAK id-joins ───────────────────────────────────────
#
# Applied after the identifier renames, so these patterns already talk about
# OpenSAK column names (gc_code / cache_id) rather than GSAK's.
_JOIN_CODE_TO_ID_RE = re.compile(
    r"\b(?P<a>[A-Za-z_][A-Za-z_0-9]*)\.gc_code\b\s*=\s*"
    r"(?P<b>[A-Za-z_][A-Za-z_0-9]*\.)?cache_id\b", re.IGNORECASE)
_JOIN_ID_TO_CODE_RE = re.compile(
    r"\b(?P<b>[A-Za-z_][A-Za-z_0-9]*\.)?cache_id\b\s*=\s*"
    r"(?P<a>[A-Za-z_][A-Za-z_0-9]*)\.gc_code\b", re.IGNORECASE)
_IN_SUBSELECT_RE = re.compile(
    r"(?P<qual>\b[A-Za-z_][A-Za-z_0-9]*\.)?\bgc_code\b\s+(?P<neg>not\s+)?in\s*\(",
    re.IGNORECASE)

_RELINK_NOTE = (
    "where: GSAK links its Logs/Waypoints/Attributes/Corrected rows to the parent cache by GC "
    "code (lParent/cParent/aCode/kCode); OpenSAK links them by the numeric caches.id, so the "
    "code-based joins and \"code in (select …)\" tests were rewritten onto id"
)


def _closing_paren(sql: str, open_idx: int) -> int:
    """Index of the ``)`` matching the ``(`` at *open_idx*, or -1.

    Skips over single-quoted string literals so a bracket inside one is not
    mistaken for structure.
    """
    depth = 0
    i = open_idx
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            i += 1
            while i < len(sql):
                if sql[i] == "'":
                    if i + 1 < len(sql) and sql[i + 1] == "'":
                        i += 2
                        continue
                    break
                i += 1
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _selects_cache_id(subselect: str) -> bool:
    """True when *subselect* is a SELECT whose result column is a cache_id."""
    body = subselect.strip()
    if not re.match(r"(?is)^select\b", body):
        return False
    # Only the select list matters — a cache_id in the WHERE part is a join
    # predicate, not the value the outer "in" is compared against.
    m = re.search(r"(?is)\bfrom\b", body)
    select_list = body[6:m.start()] if m else body[6:]
    return re.search(r"\bcache_id\b", select_list, re.IGNORECASE) is not None


def _relink_code_joins(sql: str, notes: list[str]) -> str:
    """Turn GSAK's GC-code joins into OpenSAK's numeric-id joins."""
    hit = False

    def _sub_join(m: re.Match) -> str:
        nonlocal hit
        hit = True
        return f"{m.group('a')}.id = {m.group('b') or ''}cache_id"

    def _sub_join_rev(m: re.Match) -> str:
        nonlocal hit
        hit = True
        return f"{m.group('b') or ''}cache_id = {m.group('a')}.id"

    sql = _JOIN_CODE_TO_ID_RE.sub(_sub_join, sql)
    sql = _JOIN_ID_TO_CODE_RE.sub(_sub_join_rev, sql)

    # "code [not] in (select lParent …)" → "id [not] in (select cache_id …)".
    out: list[str] = []
    pos = 0
    while True:
        m = _IN_SUBSELECT_RE.search(sql, pos)
        if not m:
            break
        open_idx = m.end() - 1
        close_idx = _closing_paren(sql, open_idx)
        if close_idx < 0 or not _selects_cache_id(sql[open_idx + 1:close_idx]):
            out.append(sql[pos:m.end()])
            pos = m.end()
            continue
        hit = True
        out.append(sql[pos:m.start()])
        out.append(f"{m.group('qual') or ''}id {m.group('neg') or ''}in (")
        pos = m.end()
    out.append(sql[pos:])
    sql = "".join(out)

    if hit:
        notes.append(_RELINK_NOTE)
    return sql


def _table_aliases(sql: str) -> set[str]:
    """Collect table aliases (``from caches c``, ``join logs l``) plus every
    identifier used as a qualifier (``c.name``) so they are not mistaken for
    column names."""
    aliases = {m.group(1).lower() for m in _ALIAS_RE.finditer(sql)}
    aliases -= _SQL_WORDS
    aliases |= {m.group(1).lower()
                for m in re.finditer(r"\b([A-Za-z_][A-Za-z_0-9]*)\s*\.", sql)}
    return aliases


def translate_where(sql: str, custom_columns: Optional[list[str]] = None,
                    me: Optional[str] = None
                    ) -> tuple[str, list[str], list[str], bool]:
    """Best-effort translation of a GSAK where clause into OpenSAK SQL.

    *custom_columns* are the user-defined GSAK column names of the same filter
    (from its ``*custom*`` section); they are reported by name rather than as
    anonymous unknown identifiers. *me* is the user's geocaching.com account
    name, used to translate GSAK's ``isOwner``.

    Returns (translated_sql, warnings, notes, verified). ``verified`` is False
    when anything was left untranslated; ``notes`` are inexact-but-usable
    mappings that do not block verification.
    """
    warnings: list[str] = []
    notes: list[str] = []
    sql = _expand_cachetype_codes(sql, warnings, notes)
    sql = _expand_status(sql, notes)
    aliases = _table_aliases(sql)
    custom = {name.lower() for name in (custom_columns or [])}

    out: list[str] = []
    for is_literal, chunk in _split_sql_literals(sql):
        if is_literal:
            out.append(chunk)
            continue

        def _sub_ident(m: re.Match) -> str:
            qual, word = m.group("qual"), m.group("word")
            prefix = f"{qual}." if qual else ""

            def _keep(mapped: str) -> str:
                """Re-attach the table qualifier — unless the mapping is an
                expression rather than a bare column name, in which case the
                qualifier cannot survive."""
                if mapped.isidentifier():
                    return prefix + mapped
                if qual:
                    notes.append(
                        f"where: '{prefix}{word}' maps to a sub-select on OpenSAK's user_notes, "
                        f"which cannot carry the '{qual}' table qualifier — the sub-select is "
                        f"correlated to the outer caches row instead, so check the logic if "
                        f"'{qual}' was not that outer caches table"
                    )
                return mapped

            low = word.lower()
            if low in _SQL_WORDS or low in aliases:
                return prefix + word
            if low in custom:
                warnings.append(
                    f"where: '{word}' is a user-defined GSAK column — OpenSAK has no custom "
                    f"columns (only user_data_1-4)"
                )
                return prefix + word
            if _MACRO_GLOBAL_RE.match(word):
                warnings.append(
                    f"where: '{word}' is a GSAK macro variable resolved at run time — "
                    f"substitute a literal value"
                )
                return prefix + word
            if low == "isowner":
                if me:
                    notes.append(
                        f"where: GSAK's isOwner was translated to an owner_name test against "
                        f"your account name ({me})"
                    )
                    return f"lower(coalesce(owner_name, '')) = '{_escape_sql(me.lower())}'"
                warnings.append(f"where: {WHERE_UNMAPPABLE['isowner']}")
                return prefix + word
            if low in WHERE_INVERTED:
                warnings.append(
                    f"where: '{word}' → {WHERE_INVERTED[low]} — inverted meaning, verify the logic"
                )
                return WHERE_INVERTED[low]
            if low in WHERE_UNMAPPABLE:
                warnings.append(f"where: {WHERE_UNMAPPABLE[low]}")
                return prefix + word
            mapped = (WHERE_COLUMN_MAP.get(low)
                      or WHERE_SUBTABLE_MAP.get(low)
                      or WHERE_TABLE_MAP.get(low))
            if mapped is not None:
                if low in WHERE_SOFT_NOTES:
                    notes.append("where: " + WHERE_SOFT_NOTES[low])
                return _keep(mapped)
            if low in _OSAK_COLUMNS:
                return prefix + word   # already an OpenSAK column
            # Anything still unrecognised is left alone and reported.
            warnings.append(f"where: unrecognised identifier '{word}' left unchanged")
            return prefix + word

        out.append(_IDENT_RE.sub(_sub_ident, chunk))

    if _GSAK_ORIGINAL_POS_RE.search(sql) and _GSAK_CORRECTED_POS_RE.search(
            _GSAK_ORIGINAL_POS_RE.sub("", sql)):
        warnings.append(
            "where: the clause compares GSAK's Latitude/Longitude (the CORRECTED position of a "
            "solved cache) against LatOriginal/LonOriginal (the posted one). OpenSAK's "
            "caches.latitude/longitude ARE the posted position and keep the correction in "
            "user_notes, so both GSAK columns translate to the same OpenSAK column and the "
            "comparison collapses into a tautology — rewrite the corrected side as "
            "coalesce((SELECT corrected_lat FROM user_notes WHERE user_notes.cache_id = "
            "caches.id), latitude)"
        )

    sql = _relink_code_joins("".join(out), notes)
    sql = _unquote_numeric_literals(sql, notes)
    return sql, _dedupe(warnings), _dedupe(notes), not warnings


# ── GSAK blob parsing ────────────────────────────────────────────────────────

class GsakFilter:
    """One parsed GSAK filter blob."""

    def __init__(self, name: str, raw: str):
        self.name = name
        self.raw = raw
        self.kv: dict[str, str] = {}
        self.custom: list[str] = []   # *custom* section, one line per criterion
        self.where: str = ""          # *where* section, verbatim GSAK SQL

    def text(self, key: str) -> str:
        return self.kv.get(key, "").strip()

    def flag(self, key: str, default: Optional[bool] = None) -> Optional[bool]:
        v = self.kv.get(key)
        if v is None:
            return default
        return v.strip().lower() == "true"

    def num(self, key: str) -> Optional[int]:
        v = self.text(key)
        if not v:
            return None
        try:
            return int(float(v))
        except ValueError:
            return None


def parse_filter_blob(name: str, data: str) -> GsakFilter:
    """Parse a TranslateFilters.Data blob.

    Layout (CRLF separated):
        key=value lines, one per dialog control
        [ArcFilter=~lat,lon~lat,lon…]        polygon / arc selection
        [<Cache type label>=True|False]      one line per cache type
        [*custom*]                           user-defined column criteria follow
        [*where*=<SQL>]                      raw SQL, runs to the end of the blob
    """
    f = GsakFilter(name, data)
    body, sep, where = data.partition("*where*=")
    if sep:
        f.where = where.strip()

    in_custom = False
    for line in body.replace("\r\n", "\n").split("\n"):
        if line.strip() == "*custom*":
            in_custom = True
            continue
        if not line.strip():
            continue
        if in_custom:
            f.custom.append(line.strip())
            continue
        key, eq, value = line.partition("=")
        if eq:
            f.kv[key.strip()] = value
    return f


def delphi_date(serial: str) -> Optional[datetime]:
    """Convert a Delphi TDateTime serial (days since 1899-12-30) to a datetime."""
    try:
        value = float(serial.strip())
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return datetime(1899, 12, 30) + timedelta(days=value)


# ── Coverage-tracked conversion ──────────────────────────────────────────────

NATIVE = "native"    # became a filter the Set Filter dialog shows
SQL = "sql"          # became executable SQL in the Where tab
COMMENT = "comment"  # became a -- comment in the Where tab; nothing runs

MIGRATED_STATUSES = (NATIVE, SQL)


@dataclass
class Criterion:
    """One GSAK criterion and where it ended up."""
    label: str
    status: str
    detail: str = ""


@dataclass
class Options:
    """Everything convert() needs beyond the filter itself."""
    center: Optional[tuple[float, float]] = None   # (lat, lon) for distances
    miles: bool = False                            # GSAK's distances are miles
    me: Optional[str] = None                       # geocaching.com account name


class Conversion:
    """The result of converting one GSAK filter, with its coverage."""

    def __init__(self, name: str):
        self.name = name
        self.criteria: list[Criterion] = []
        self.filters: list[Any] = []                  # native filter objects
        self.sql_parts: list[tuple[str, str]] = []    # (label, sql)
        # (label, reason, extra body lines) — what where_text() writes out
        self.comment_lines: list[tuple[str, str, list[str]]] = []
        self.notes: list[str] = []                    # assumptions worth keeping

    @property
    def comments(self) -> list[tuple[str, str]]:
        """The commented-out criteria as (label, reason) pairs."""
        return [(label, reason) for label, reason, _ in self.comment_lines]

    # ── Recording ────────────────────────────────────────────────────────────

    def native(self, label: str, *filters: Any) -> None:
        """A criterion the Set Filter dialog can show natively."""
        self.filters.extend(filters)
        self.criteria.append(Criterion(label, NATIVE))

    def sql(self, label: str, sql_text: str) -> None:
        """A criterion OpenSAK stores but has no GUI filter for."""
        self.sql_parts.append((label, " ".join(sql_text.split())))
        self.criteria.append(Criterion(label, SQL))

    def comment(self, label: str, reason: str,
                lines: Optional[list[str]] = None) -> None:
        """A criterion nothing in OpenSAK can express — documented only.

        *lines* are extra body lines (warnings, the SQL as far as it could be
        translated) written under the reason, one ``--`` line each.
        """
        reason = " ".join(reason.split())
        self.comment_lines.append((label, reason, list(lines or [])))
        self.criteria.append(Criterion(label, COMMENT, reason))

    def note(self, text: str) -> None:
        self.notes.append(" ".join(text.split()))

    # ── Coverage ─────────────────────────────────────────────────────────────

    @property
    def total(self) -> int:
        return len(self.criteria)

    @property
    def migrated(self) -> int:
        return sum(1 for c in self.criteria if c.status in MIGRATED_STATUSES)

    @property
    def native_count(self) -> int:
        return sum(1 for c in self.criteria if c.status == NATIVE)

    @property
    def not_migrated(self) -> int:
        return sum(1 for c in self.criteria if c.status == COMMENT)

    @property
    def coverage(self) -> float:
        """Share of this filter's criteria that actually run, 0.0–1.0.

        A filter with no criteria at all (GSAK's "show everything") is fully
        migrated by definition — there was nothing to lose.
        """
        if not self.criteria:
            return 1.0
        return self.migrated / self.total

    # ── Output ───────────────────────────────────────────────────────────────

    def where_text(self) -> str:
        """The Where tab's content: the comments first, then the SQL.

        Order matters. OpenSAK runs the clause as
        ``SELECT id FROM caches WHERE (<sql>)`` and WhereClauseFilter strips
        the text, so a trailing ``--`` comment would swallow the closing
        bracket. Every comment therefore goes above the SQL it belongs to, and
        the last line is always executable.
        """
        lines: list[str] = []
        if self.comment_lines:
            # The name comes from GSAK's database, so it is folded onto one
            # line before it goes into a -- comment.
            lines.append(
                f'-- NOT MIGRATED from the GSAK filter "{" ".join(self.name.split())}" '
                f'({len(self.comment_lines)} condition(s)).'
            )
            lines.append("-- Rebuild these by hand, then delete the comment.")
            for label, reason, extra in self.comment_lines:
                for i, chunk in enumerate(_wrap(f"{label}: {reason}", 96)):
                    lines.append(f"--   {chunk}" if i == 0 else f"--     {chunk}")
                for extra_line in extra:
                    for chunk in _wrap(extra_line, 96):
                        lines.append(f"--     {chunk}")
        if self.sql_parts:
            for i, (label, sql_text) in enumerate(self.sql_parts):
                for chunk in _wrap(label, 96):
                    lines.append(f"-- {chunk}")
                lines.append(f"{'AND ' if i else ''}({sql_text})")
        elif self.comment_lines:
            # Comments only — the clause still has to be valid SQL, so it ends
            # on a condition that changes nothing.
            lines.append("-- (nothing above runs — this tab only documents what was lost)")
            lines.append("1 = 1")
        return "\n".join(lines)

    def build_filterset(self) -> FilterSet:
        fs = FilterSet(mode="AND")
        for f in self.filters:
            fs.add(f)
        where = self.where_text()
        if where:
            fs.add(WhereClauseFilter(where))
        return fs

    def gsak_block(self, source_db: Path) -> dict[str, Any]:
        """Provenance for the profile's ``_gsak`` key. OpenSAK ignores unknown
        top-level keys on load, so this travels with the file harmlessly."""
        block: dict[str, Any] = {
            "note": ("Imported from GSAK. OpenSAK ignores this block; it records where each "
                     "GSAK criterion ended up."),
            "source_database": str(source_db),
            "source_filter": self.name,
            "imported_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "coverage_percent": round(self.coverage * 100, 1),
            "criteria": [
                {"label": c.label, "status": c.status}
                for c in self.criteria
            ],
        }
        if self.notes:
            block["notes"] = _dedupe(self.notes)
        return block


# ── Small shared helpers ─────────────────────────────────────────────────────

def _tri_flag(gf: GsakFilter, yes_key: str, no_key: str) -> Optional[bool]:
    """GSAK yes/no checkbox pairs: both ticked = no restriction (None),
    only yes = True, only no = False."""
    yes, no = gf.flag(yes_key), gf.flag(no_key)
    if yes is None or no is None:
        return None
    if yes and not no:
        return True
    if no and not yes:
        return False
    return None


def _sql_text_test(column: str, op: str, value: str) -> Optional[str]:
    """A WHERE fragment for one text comparison, or None when SQLite cannot
    run it (the regex operators — OpenSAK registers no REGEXP function)."""
    col = f"lower(coalesce({column}, ''))"
    needle = _escape_sql(value.lower())
    return {
        "contains":     f"{col} like '%{needle}%'",
        "not_contains": f"{col} not like '%{needle}%'",
        "equals":       f"{col} = '{needle}'",
        "not_equals":   f"{col} <> '{needle}'",
        "starts_with":  f"{col} like '{needle}%'",
        "ends_with":    f"{col} like '%{needle}'",
        "empty":        f"{col} = ''",
        "not_empty":    f"{col} <> ''",
        "in_list":      f"{col} in ("
                        + ", ".join(f"'{_escape_sql(v.strip().lower())}'"
                                    for v in value.split(";") if v.strip())
                        + ")",
    }.get(op)


def _wildcard_op(pattern: str) -> tuple[str, str]:
    """GSAK's wildcard finder box (``*`` / ``?``) → an OpenSAK text operator.

    The common shapes become the plain operators; anything else becomes a
    regular expression, which OpenSAK's text filters evaluate in Python.
    """
    core = pattern.strip()
    if "?" not in core and core.count("*") == 2 and core.startswith("*") and core.endswith("*"):
        return core[1:-1], "contains"
    if "?" not in core and core.count("*") == 1:
        if core.endswith("*"):
            return core[:-1], "starts_with"
        if core.startswith("*"):
            return core[1:], "ends_with"
    if "*" not in core and "?" not in core:
        return core, "equals"
    escaped = re.escape(core).replace(r"\*", ".*").replace(r"\?", ".")
    return f"^{escaped}$", "regex"


@dataclass
class DateSpec:
    """A decoded GSAK date criterion, in OpenSAK's own vocabulary."""
    op: str
    date1: Optional[datetime] = None
    date2: Optional[datetime] = None
    amount: int = 1
    unit: str = "days"

    def ordered(self) -> "DateSpec":
        """"Between" with the dates the wrong way round still means a range."""
        if self.op == "between" and self.date1 and self.date2 and self.date2 < self.date1:
            return DateSpec("between", self.date2, self.date1, self.amount, self.unit)
        return self


def _read_during(gf: GsakFilter, edt_key: str, cbx_key: str) -> Optional[tuple[int, str]]:
    """GSAK's "Während den letzten [n] [unit]" row → (amount, DATE_UNITS name).

    ASSUMED unit list — see DURING_UNITS. None when the amount box is empty,
    which leaves nothing to reconstruct the window from.
    """
    raw = gf.text(edt_key)
    m = re.match(r"^\s*(\d+)", raw or "")
    if not m:
        return None
    index = gf.num(cbx_key) or 0
    unit = DURING_UNITS[index] if 0 <= index < len(DURING_UNITS) else "days"
    return int(m.group(1)), unit


def _read_date(gf: GsakFilter, c: Conversion, label: str, op_key: str,
               from_key: str, to_key: str, during_edt: str, during_cbx: str,
               ops: dict[int, Optional[str]]) -> Optional[DateSpec]:
    """Decode one GSAK date criterion, or record why it could not be."""
    raw_op = gf.num(op_key)
    if raw_op is None:
        return None
    if raw_op not in ops:
        start, end = delphi_date(gf.text(from_key)), delphi_date(gf.text(to_key))
        c.comment(label, (
            f"GSAK date operator index {raw_op} is not decoded for this field "
            f"(the dates beside it were "
            f"{start.date().isoformat() if start else 'empty'} … "
            f"{end.date().isoformat() if end else 'empty'})"
        ))
        return None
    op = ops[raw_op]
    if op is None:
        return None                      # "Beliebig" — criterion off
    if op == "compare":
        c.comment(label, (
            "GSAK compared this date against another date column "
            "(\"Verglichen mit\") and the saved filter does not record which one, "
            "so there is nothing to translate. OpenSAK's date row has the same "
            "\"Compared with\" operator — pick the other field there by hand"
        ))
        return None
    if op in ("during", "not_during"):
        during = _read_during(gf, during_edt, during_cbx)
        if during is None:
            c.comment(label, (
                f"GSAK used a rolling window (\"{op}\") but stores no window size, "
                f"so the number of days/weeks/months could not be recovered"
            ))
            return None
        amount, unit = during
        c.note(f"{label}: the rolling window's unit combo was read as "
               f"{' / '.join(DURING_UNITS)} (assumed order)")
        return DateSpec(op, amount=amount, unit=unit)

    start, end = delphi_date(gf.text(from_key)), delphi_date(gf.text(to_key))
    if op == "between":
        if start is None or end is None:
            return None                  # operator set but no usable dates
        return DateSpec("between", start, end).ordered()
    if start is None:
        return None
    return DateSpec(op, start)


def _date_sql(column: str, spec: DateSpec) -> str:
    """WHERE fragment for a date criterion on a column with no GUI filter.

    Compared with date() on both sides so a stored timestamp's time-of-day
    never excludes a day the GSAK filter would have included.
    """
    col = f"date({column})"

    def _d(value: Optional[datetime]) -> str:
        return f"'{value.date().isoformat()}'" if value else "null"

    if spec.op in ("during", "not_during"):
        unit = spec.unit.rstrip("s")
        window = f"{col} >= date('now', 'localtime', '-{spec.amount} {unit}')"
        return window if spec.op == "during" else f"({column} IS NULL OR NOT ({window}))"
    if spec.op == "on_or_before":
        return f"{col} <= {_d(spec.date1)}"
    if spec.op == "on_or_after":
        return f"{col} >= {_d(spec.date1)}"
    if spec.op == "equal":
        return f"{col} = {_d(spec.date1)}"
    return f"{col} between {_d(spec.date1)} and {_d(spec.date2)}"


def _num_bounds(gf: GsakFilter, c: Conversion, label: str, op_key: str,
                v1_key: str, v2_key: Optional[str]
                ) -> Optional[tuple[str, int, int]]:
    """Decode a GSAK numeric criterion into (op, value1, value2).

    *op* is one of NUM_OP's names; None means the criterion is off or could
    not be read (in which case the reason has been recorded).
    """
    raw_op = gf.num(op_key) or 0
    op = NUM_OP.get(raw_op)
    if op is None:
        if raw_op != 0:
            c.comment(label, f"unknown GSAK comparison index {raw_op}")
        return None
    v1 = gf.num(v1_key)
    if v1 is None:
        return None
    v2 = gf.num(v2_key) if v2_key else None
    if op == "between":
        if v2 is None:
            c.comment(label, "GSAK's \"Zwischen\" without a second value")
            return None
        return "between", min(v1, v2), max(v1, v2)
    return op, v1, v1


def _num_sql(column: str, op: str, v1: int, v2: int) -> str:
    if op == "at_most":
        return f"{column} <= {v1}"
    if op == "at_least":
        return f"{column} >= {v1}"
    if op == "equals":
        return f"{column} = {v1}"
    return f"{column} between {v1} and {v2}"


# ── Conversion — one function per GSAK tab ───────────────────────────────────

def convert(gf: GsakFilter, opts: Optional[Options] = None) -> Conversion:
    """Translate one parsed GSAK filter into OpenSAK filters + coverage."""
    opts = opts or Options()
    c = Conversion(gf.name)

    _convert_cache_types(gf, c)
    _convert_containers(gf, c)
    _convert_dt(gf, c)
    _convert_found(gf, c)
    _convert_availability(gf, c)
    _convert_text_fields(gf, c)
    _convert_favorites(gf, c)
    _convert_distance(gf, c, opts)
    _convert_flags(gf, c)
    _convert_text_search(gf, c)
    _convert_dates(gf, c)
    _convert_attributes(gf, c)
    _convert_area(gf, c, opts)
    _convert_custom(gf, c)
    _convert_log_tab(gf, c)
    _convert_waypoint_tab(gf, c)
    _convert_misc(gf, c)
    _convert_where(gf, c, opts)
    return c


def _convert_cache_types(gf: GsakFilter, c: Conversion) -> None:
    present = [k for k in GSAK_TYPE_LABEL_TO_OSAK if k in gf.kv]
    if not present:
        return
    selected = [k for k in present if gf.flag(k)]
    if len(selected) == len(present):
        return   # everything ticked = no restriction
    osak: list[str] = []
    dropped: list[str] = []
    for label in selected:
        mapped = GSAK_TYPE_LABEL_TO_OSAK[label]
        if mapped is None:
            dropped.append(label)
        elif mapped not in osak:
            osak.append(mapped)
    if osak:
        c.native("Cache types", CacheTypeFilter(osak))
    if dropped:
        c.comment("Cache types " + ", ".join(f"\"{d}\"" for d in dropped), (
            "GSAK has these cache types but OpenSAK does not — the GSAK importer files such "
            "caches under \"Unknown Cache\", where nothing tells them apart from real mystery "
            "caches"
        ))
    elif not osak and selected:
        c.comment("Cache types", "none of the selected GSAK types exist in OpenSAK")


def _convert_containers(gf: GsakFilter, c: Conversion) -> None:
    present = [k for k in GSAK_CONTAINER_KEYS if k in gf.kv]
    if not present:
        return
    selected = [k for k in present if gf.flag(k)]
    if len(selected) == len(present):
        return
    sizes: list[str] = []
    for key in selected:
        size = GSAK_CONTAINER_KEYS[key]
        if size not in sizes:
            sizes.append(size)
        if key in _COLLAPSED_CONTAINERS:
            c.note(f"Container: GSAK's \"{key}\" has no OpenSAK equivalent and was mapped to "
                   f"\"Not chosen\", the same way the GSAK cache importer maps it")
    if sizes:
        c.native("Container sizes", ContainerFilter(sizes))


def _convert_dt(gf: GsakFilter, c: Conversion) -> None:
    for label, op_key, v1_key, v2_key, cls, kwargs_names in (
        ("Difficulty", "cbxDifficulty", "cbxDif", "cbxDif2",
         DifficultyFilter, ("min_difficulty", "max_difficulty")),
        ("Terrain", "cbxTerrain", "cbxTer", "cbxTer2",
         TerrainFilter, ("min_terrain", "max_terrain")),
    ):
        raw_op = gf.num(op_key)
        i1 = gf.num(v1_key)
        if raw_op is None or i1 is None:
            continue
        op = DT_OP.get(raw_op)
        if op is None:
            c.comment(label, f"unknown GSAK comparison index {raw_op}")
            continue
        v1 = _dt_value(i1)
        i2 = gf.num(v2_key)
        v2 = _dt_value(i2) if i2 is not None else v1
        if op == "at_most":
            lo, hi = 1.0, v1
        elif op == "at_least":
            lo, hi = v1, 5.0
        elif op == "equals":
            lo, hi = v1, v1
        else:   # between (inclusive)
            lo, hi = min(v1, v2), max(v1, v2)
        if (lo, hi) == (1.0, 5.0):
            continue   # the dialog's reset state ("Kleiner gleich 5.0") = no restriction
        c.native(label, cls(**dict(zip(kwargs_names, (lo, hi)))))
        c.note(f"{label}: GSAK's operator index {raw_op} was read as \"{op}\" → range "
               f"{lo}-{hi} (assumed order: 0 = at most, 1 = at least, 2 = equal, 3 = between)")


def _convert_found(gf: GsakFilter, c: Conversion) -> None:
    found, notfound = gf.flag("chkFound"), gf.flag("chkNotFound")
    if found is None or notfound is None:
        return
    if found and not notfound:
        c.native("Found by me", FoundFilter())
    elif notfound and not found:
        c.native("Not found by me", NotFoundFilter())
    elif not found and not notfound:
        c.sql("Found by me", "1 = 0")
        c.note("Found: GSAK had BOTH \"found\" and \"not found\" unticked, which matches "
               "nothing at all. That became the constant 1 = 0 — delete it if the GSAK "
               "filter was simply misconfigured")


def _convert_availability(gf: GsakFilter, c: Conversion) -> None:
    avail = gf.flag("chkAvailable")
    if avail is None:
        return
    c.native("Availability", AvailabilityFilter(
        show_avail=bool(avail),
        show_unavail=bool(gf.flag("chkTempUnavailable", False)),
        show_archived=bool(gf.flag("chkArchivedOnly", False)),
    ))


def _text_criterion(gf: GsakFilter, c: Conversion, op_key: str, val_key: str,
                    label: str, cls: Optional[type] = None,
                    column: Optional[str] = None) -> None:
    """One GSAK text criterion → a native text filter, or SQL on *column*.

    *cls* is the native TextMatchFilter subclass when OpenSAK's GUI has a row
    for the field (which supports every GSAK operator, regex included);
    otherwise *column* names the OpenSAK column a where_clause can test.
    """
    raw_op = gf.num(op_key) or 0
    op = TEXT_OP.get(raw_op)
    value = gf.text(val_key)
    if op is None:
        if value:
            c.comment(label, f"unknown GSAK comparison index {raw_op} for the value "
                             f"\"{value}\"")
        return
    if not value and op not in TEXT_OP_VALUELESS:
        return   # empty edit box = criterion not set

    if cls is not None:
        c.native(label, cls(value, op))
        return

    assert column is not None
    if op in TEXT_OP_NO_SQL:
        c.comment(label, (
            f"GSAK matched \"{value}\" with a regular expression. OpenSAK has no GUI filter "
            f"for this field, and its SQLite connection registers no REGEXP operator, so "
            f"neither route can run it"
        ))
        return
    sql = _sql_text_test(column, op, value)
    if sql is None:
        c.comment(label, f"GSAK comparison \"{op}\" could not be expressed in SQL")
        return
    c.sql(label, sql)


def _convert_text_fields(gf: GsakFilter, c: Conversion) -> None:
    # Fields the Set Filter dialog has a row for — every GSAK operator, the
    # two regex ones included, is available there.
    for op_key, val_key, label, cls in (
        ("cbxDesc",      "edtDesc",      "Geocache name", NameFilter),
        ("cbxCode",      "edtCode",      "GC code",       GcCodeFilter),
        ("cbxOwnerName", "edtOwnerName", "Owner",         OwnerFilter),
        ("cbxPlacedBy",  "edtPlacedBy",  "Placed by",     PlacedByFilter),
        ("cbxCountry",   "edtCountry",   "Country",       CountryFilter),
        ("cbxState",     "edtState",     "State",         StateFilter),
        ("cbxCounty",    "edtCounty",    "County",        CountyFilter),
    ):
        _text_criterion(gf, c, op_key, val_key, label, cls=cls)

    # GSAK's four free-text user fields map 1:1 onto OpenSAK's user_data_1-4
    # columns, but OpenSAK has no filter row for them — hence SQL.
    for n, (op_key, val_key) in enumerate(
        (("cbxUserData", "edtUserData"), ("cbxUser2", "EdtUser2"),
         ("cbxUser3", "edtUser3"), ("cbxUser4", "edtUser4")), start=1
    ):
        _text_criterion(gf, c, op_key, val_key, f"User data {n}",
                        column=f"user_data_{n}")


def _convert_favorites(gf: GsakFilter, c: Conversion) -> None:
    bounds = _num_bounds(gf, c, "Favourite points", "cbxFavorite",
                         "edtFavorite", "edtFavorite2")
    if bounds is None:
        return
    op, v1, v2 = bounds
    if op == "at_most":
        lo, hi = 0, v1
    elif op == "at_least":
        lo, hi = v1, 9999
    elif op == "equals":
        lo, hi = v1, v1
    else:
        lo, hi = v1, v2
    c.native("Favourite points", FavoritePointsFilter(min_pts=lo, max_pts=hi))


def _convert_distance(gf: GsakFilter, c: Conversion, opts: Options) -> None:
    bounds_op = NUM_OP.get(gf.num("cbxDistance") or 0)
    if bounds_op is None:
        return
    v1, v2 = gf.text("edtDistance"), gf.text("edtDistance2")
    if not v1:
        return
    unit = "miles" if opts.miles else "km"
    factor = 1.609344 if opts.miles else 1.0
    try:
        d1 = float(v1.replace(",", ".")) * factor
        d2 = float(v2.replace(",", ".")) * factor if v2 else None
    except ValueError:
        c.comment("Distance", f"could not read the radius \"{v1}\"/\"{v2}\"")
        return

    if bounds_op == "at_most":
        lo_km, hi_km = 0.0, d1
    elif bounds_op == "at_least":
        lo_km, hi_km = d1, None
    elif bounds_op == "equals":
        lo_km, hi_km = d1, d1
    else:
        if d2 is None:
            c.comment("Distance", "GSAK's \"Zwischen\" without a second value")
            return
        lo_km, hi_km = min(d1, d2), max(d1, d2)

    if hi_km is None:
        # Open-ended ("at least X") — the GUI's distance row always has an
        # upper bound, so this goes to SQL on the `distance` pseudo-column,
        # which OpenSAK rewrites to a haversine call against the ACTIVE centre
        # point (closer to GSAK, which never froze a centre either).
        c.sql("Distance", f"distance >= {round(lo_km / factor, 3)}")
        c.note(f"Distance: \"at least {v1} {unit}\" has no upper bound, so it became SQL on "
               f"the `distance` column, which OpenSAK measures from the active centre point "
               f"in your own unit — rescale the number if your unit is not {unit}")
        return

    if opts.center is None:
        c.sql("Distance", f"distance between {round(lo_km / factor, 3)} "
                          f"and {round(hi_km / factor, 3)}")
        c.note(f"Distance: no centre point was available, so the radius became SQL on the "
               f"`distance` column, which OpenSAK measures from the active centre point in "
               f"your own unit — rescale the numbers if your unit is not {unit}")
        return

    lat, lon = opts.center
    c.native("Distance", DistanceFilter(
        lat=round(lat, 6), lon=round(lon, 6),
        max_km=round(hi_km, 3), min_km=round(lo_km, 3),
        center_state={"kind": "home"},
    ))
    c.note(f"Distance: GSAK stores only the radius ({v1} {unit}) and measures it from "
           f"whatever centre point is active at the time. OpenSAK's distance filter freezes "
           f"a coordinate instead, so your home point ({lat}, {lon}) was taken as the centre")


def _convert_flags(gf: GsakFilter, c: Conversion) -> None:
    corrected = _tri_flag(gf, "chkCorrectYes", "chkCorrectNo")
    if corrected is True:
        c.native("Corrected coordinates", HasCorrectedFilter())
    elif corrected is False:
        c.native("Corrected coordinates", NoCorrectedFilter())

    dnf = _tri_flag(gf, "chkDNFYes", "chkDNFNo")
    if dnf is not None:
        c.native("DNF", DnfFilter(has_dnf=dnf))

    ftf = _tri_flag(gf, "chkFtfyes", "chkFtfNo")
    if ftf is not None:
        c.native("FTF", FtfFilter(has_ftf=ftf))

    premium = _tri_flag(gf, "chkPoYes", "chkPoNo")
    if premium is True:
        c.native("Premium only", PremiumFilter())
    elif premium is False:
        c.native("Premium only", NonPremiumFilter())

    locked = _tri_flag(gf, "chkLockYes", "chkLockNo")
    if locked is not None:
        c.native("Locked", LockedFilter(locked=locked))

    flagged = _tri_flag(gf, "chkUserFlag1", "chkUserFlag2")
    if flagged is not None:
        c.native("User flag", UserFlagFilter(flagged=flagged))

    trackables = _tri_flag(gf, "cbxBugs", "chkBugNo")
    if trackables is True:
        c.native("Has trackables", HasTrackableFilter())
    elif trackables is False:
        c.sql("Has trackables", "coalesce(trackable_count, 0) = 0")
        c.note("Trackables: GSAK filtered to caches WITHOUT trackables; OpenSAK's GUI only "
               "offers \"has trackables\", so the negative became SQL on trackable_count")

    note = _tri_flag(gf, "chkNoteYes", "chkNoteNo")
    if note is not None:
        op = "<>" if note else "="
        c.sql("User note", f"coalesce((SELECT note FROM user_notes "
                           f"WHERE user_notes.cache_id = caches.id), '') {op} ''")

    # OpenSAK does store the watch list (caches.watch, filled from GSAK's own
    # Watch column by gsak_importer.py) but has no GUI filter for it.
    watch = _tri_flag(gf, "chkWatchYes", "chkWatchNo")
    if watch is not None:
        c.sql("Watch list", f"coalesce(watch, 0) = {1 if watch else 0}")


def _convert_text_search(gf: GsakFilter, c: Conversion) -> None:
    text = gf.text("edtFull")
    if not text:
        return
    # "Wo suchen": Überall (RbtFullAll) searches everything, in which case the
    # individual Logs / Notizen / Beschreibung boxes are ignored by GSAK — they
    # are only read for "Nur ausgewählte" (rbtFullSelect).
    search_all = bool(gf.flag("RbtFullAll", False))
    c.native("Text search", TextSearchFilter(
        text=text,
        search_description=search_all or bool(gf.flag("chkFullDes", False)),
        search_logs=search_all or bool(gf.flag("chkFullLogs", False)),
        search_notes=search_all or bool(gf.flag("chkFullNotes", False)),
        search_hint=False,
    ))
    if search_all:
        c.note("Text search: GSAK searched \"everywhere\", which became description + logs + "
               "notes (OpenSAK's hint search stays off)")
    if gf.flag("chkRegEx", False):
        c.comment("Text search (regular expression)", (
            f"GSAK searched for \"{text}\" as a regular expression. OpenSAK's full-text "
            f"search is a plain substring match, so it was migrated as one — widen or "
            f"narrow it by hand if the expression mattered"
        ))
    if gf.flag("chkFullHighlight", False):
        c.note("Text search: GSAK's \"highlight matches\" option has no OpenSAK equivalent")


# GSAK date criterion → (op key, from key, to key, during keys, label, the
# OpenSAK DateFilter field or None, the SQL column to fall back on, operator
# table, extra note).
_DATE_FIELDS: tuple[tuple[str, str, str, str, str, str, Optional[str], str,
                          dict[int, Optional[str]], str], ...] = (
    ("cbxUserFound", "edtDateMeF", "edtDateMeT", "edtFbmDuring", "cbxFbmDuring",
     "My found date", "found_date", "found_date", DATE_OP_MY_FOUND, ""),
    ("cbxDNFDate", "edtDNFDateF", "edtDNFDateT", "edtDNFDateDuring", "cbxDNFDateDuring",
     "DNF date", "dnf_date", "dnf_date", DATE_OP, ""),
    ("cbxLastLog", "edtLastLogF", "edtLastLogT", "edtLastLogDuring", "cbxLastLogDuring",
     "Last log date", "last_log_date", "last_log_date", DATE_OP, ""),
    ("cbxFound", "edtDateF", "edtDateF2", "edtFoundDuring", "cbxFoundDuring",
     "Last found date", "last_found_date", "last_found_date", DATE_OP, ""),
    ("cbxPlaced", "edtDateP", "edtDateP2", "edtPlacedDuring", "cbxPlacedDuring",
     "Hidden date", "hidden_date", "hidden_date", DATE_OP, ""),
    ("cbxCreate", "edtCreateF", "edtCreateT", "edtCreatedDuring", "cbxCreatedDuring",
     "Record created date", "creation_date", "imported_at", DATE_OP,
     "Record created date: OpenSAK's creation date is when THIS database first saw the "
     "cache, not when GSAK first saw it"),
    ("cbxLastUpdate", "edtLastUpdateF", "edtLastUpdateT", "edtLastUpdateDuring",
     "cbxLastUpdateDuring", "Last GPX update", "last_gpx_update", "last_gpx_update", DATE_OP,
     "Last GPX update: OpenSAK stamps this locally on every import pass, where GSAK's "
     "LastGPXDate came from the source file"),
    ("cbxLastUser", "edtLastUserF", "edtLastUserT", "edtLastUserDuring", "cbxLastUserDuring",
     "Last user update", None,
     "(SELECT updated_at FROM user_notes WHERE user_notes.cache_id = caches.id)", DATE_OP,
     "Last user update: a cache with no personal note and no corrected coordinates has no "
     "user_notes row at all, so it can never match"),
    ("cbxChange", "edtChangeF", "edtChangeT", "edtChangeDuring", "cbxChangeDuring",
     "Last changed date", "changed_date", "last_updated", DATE_OP, ""),
)


def _convert_dates(gf: GsakFilter, c: Conversion) -> None:
    for (op_key, from_key, to_key, during_edt, during_cbx,
         label, osak_field, column, ops, extra) in _DATE_FIELDS:
        spec = _read_date(gf, c, label, op_key, from_key, to_key,
                          during_edt, during_cbx, ops)
        if spec is None:
            continue
        if extra:
            c.note(extra)
        if osak_field is not None:
            c.native(label, DateFilter(
                field=osak_field, op=spec.op,
                date1=spec.date1, date2=spec.date2,
                amount=spec.amount, unit=spec.unit,
            ))
        else:
            c.sql(label, _date_sql(column, spec))


def _convert_attributes(gf: GsakFilter, c: Conversion) -> None:
    natives: list[AttributeFilter] = []
    absent: list[int] = []
    for key, value in gf.kv.items():
        m = re.fullmatch(r"chkAtt(\d+)_([012])", key)
        if not m or value.strip().lower() != "true":
            continue
        attr_id, state = int(m.group(1)), m.group(2)
        if state == "0":
            natives.append(AttributeFilter(attr_id, is_on=True))
        elif state == "1":
            natives.append(AttributeFilter(attr_id, is_on=False))
        else:
            absent.append(attr_id)     # GSAK's "Keines" — not present at all
    if natives:
        c.note("Attributes: GSAK's three checkbox columns are Ja / Nein / Keines → "
               "\"is set\", \"is not set\", and \"not present at all\"")
        if gf.flag("rbtAttAny", False) and len(natives) > 1:
            group = FilterSet(mode="OR")
            for f in natives:
                group.add(f)
            c.native("Attributes (any of)", group)
        else:
            c.native("Attributes", *natives)
    for attr_id in absent:
        c.sql(f"Attribute {attr_id} not present",
              f"NOT EXISTS (SELECT 1 FROM attributes a WHERE a.cache_id = caches.id "
              f"AND a.attribute_id = {attr_id})")


def _convert_area(gf: GsakFilter, c: Conversion, opts: Options) -> None:
    """GSAK's polygon / arc / point area selection → the Line/Polygon filter."""
    arc = gf.text("ArcFilter")
    if not arc:
        return
    if gf.flag("rbtPoly", False):
        mode, kind = "polygon", "polygon"
    elif gf.flag("rbtArc", False):
        mode, kind = "line", "arc"
    elif gf.flag("rbtPoint", False):
        mode, kind = "points", "point"
    else:
        mode, kind = "polygon", "area"

    points: list[tuple[float, float]] = []
    for point in arc.split("~"):
        point = point.strip()
        if not point:
            continue
        try:
            lat_s, lon_s = point.split(",")
            points.append((float(lat_s), float(lon_s)))
        except ValueError:
            continue
    if not points:
        c.comment(f"Area selection ({kind})",
                  f"no usable coordinates in the GSAK definition ({arc})")
        return
    # GSAK repeats the first vertex to close a polygon; OpenSAK closes it itself.
    if mode == "polygon" and len(points) > 1 and points[0] == points[-1]:
        points.pop()

    try:
        radius = float((gf.text("edtArcDistance") or "0").replace(",", "."))
    except ValueError:
        radius = 0.0
    distance_km = radius * (1.609344 if opts.miles else 1.0)

    label = f"Area selection ({kind})"
    try:
        c.native(label, LinePolygonFilter(
            points=points, mode=mode, distance_km=distance_km,
            exclude=bool(gf.flag("chkArcExclude", False)),
        ))
    except ValueError as exc:
        c.comment(label, f"{exc} — GSAK's definition was {arc}")


def _convert_custom(gf: GsakFilter, c: Conversion) -> None:
    """User-defined GSAK columns (the Custom / CustomLocal tables).

    OpenSAK has no custom-column storage at all — the GSAK cache importer
    deliberately skips the Custom table — so there is not even a column for
    SQL to point at. The raw GSAK criterion is kept so it can be rebuilt on
    one of user_data_1-4 once the data is moved there.
    """
    for line in gf.custom:
        parts = line.split(";")
        name = parts[0] if parts else line
        c.comment(f"User-defined GSAK column \"{name}\"", (
            f"OpenSAK stores no custom columns (only user_data_1-4), and the GSAK cache "
            f"import does not carry the Custom table over, so there is no column to test. "
            f"The GSAK criterion was: {line}"
        ))


def _convert_log_tab(gf: GsakFilter, c: Conversion) -> None:
    """GSAK's Logs tab → OpenSAK's native log filter, which was modelled on it."""
    if "chkLogFound" not in gf.kv and "cbxLogCount" not in gf.kv:
        return

    described: list[str] = []

    # Which log types qualify ("Lt<name>=True", one key per ticked type). The
    # names are the ones OpenSAK stores in logs.log_type — the GSAK cache
    # importer copies Logs.lType through verbatim.
    types = sorted(k[2:] for k, v in gf.kv.items()
                   if k.startswith("Lt") and v.strip().lower() == "true")
    if types:
        described.append("types " + ", ".join(types))

    # Which logs are searched at all ("Zu durchsuchende Logs").
    categories: Optional[list[str]] = None
    if "chkLogSearchFound" in gf.kv:
        chosen = [
            name for key, name in (("chkLogSearchFound", "found"),
                                   ("chkLogSearchNotFound", "not_found"),
                                   ("chkLogSearchNote", "other"))
            if gf.flag(key, False)
        ]
        if chosen and len(chosen) < 3:
            categories = chosen
            described.append("only " + "/".join(chosen) + " logs")
        elif not chosen:
            # All three unticked. Every real filter has at least one ticked, so
            # what GSAK means by this is untested; searching every log (the
            # OpenSAK default) is the reading that cannot lose caches.
            c.note("Logs: GSAK had none of the three log groups (found / not found / notes) "
                   "ticked, which was read as \"search every log\"")

    # How many of the newest logs are searched.
    last_n = 0
    scope_index = gf.num("cbxLogsToSearch") or 0
    if scope_index:
        if scope_index < len(LOG_SCOPE_CHOICES):
            last_n = LOG_SCOPE_CHOICES[scope_index]
            described.append(f"in the last {last_n} log(s)")
            c.note("Logs: GSAK's \"logs to search\" dropdown was read through OpenSAK's own "
                   "list of the same choices (0/all, 1, 2, …, 10, 15, 20, 30, 40, 50, 100)")
        else:
            c.comment("Logs: number of logs to search",
                      f"GSAK's dropdown index {scope_index} is outside the list of choices "
                      f"OpenSAK offers, so every log is searched instead")

    # Who wrote them.
    finder_text, finder_op, finder_by_id = "", "contains", False
    finder = gf.text("edtGeoName")
    if finder:
        if gf.flag("rbtId", False):
            finder_text, finder_op, finder_by_id = finder, "equals", True
            described.append(f"by GC user id {finder}")
        elif gf.flag("rbtRegex", False):
            finder_text, finder_op = finder, "regex"
            described.append(f"by a finder matching /{finder}/")
        elif gf.flag("rbtWild", False):
            finder_text, finder_op = _wildcard_op(finder)
            described.append(f"by a finder matching \"{finder}\"")
            c.note("Logs: GSAK's */? wildcards on the finder box were translated into "
                   "OpenSAK's text operators (or a regular expression when the pattern "
                   "needed one)")
        else:
            finder_text, finder_op = finder, "equals"
            described.append(f"by {finder}")

    # When they were written.
    spec = _read_date(gf, c, "Log date", "cbxLogDate", "edtLogDateF", "edtLogDateT",
                      "edtLogDateDuring", "cbxLogDateDuring", DATE_OP_NO_COMPARE)
    if spec is not None:
        described.append(f"dated {spec.op.replace('_', ' ')}")
        c.note("Logs: the log-date combo has no \"compared with\" entry, so its \"any\" sits "
               "at index 6 rather than 7")

    # How many have to qualify.
    count_op, count1, count2 = "any", 0, 0
    bounds = _num_bounds(gf, c, "Log count", "cbxLogCount", "edtLogFrom", "edtLogTo")
    if bounds is not None:
        op, v1, v2 = bounds
        count_op, count1, count2 = NUM_OP_TO_COUNT_OP[op], v1, v2
        described.insert(0, f"count {count_op.replace('_', ' ')} "
                            f"{v1}{f'..{v2}' if count_op == 'between' else ''}")

    if gf.num("cbxLogInclude"):
        c.comment("Logs: \"logs to include\" selector",
                  f"GSAK stores index {gf.num('cbxLogInclude')} here and its meaning could "
                  f"not be established, so it was left out of the log filter")

    log_filter = LogFilter(
        date_op=spec.op if spec else None,
        date1=spec.date1 if spec else None,
        date2=spec.date2 if spec else None,
        date_amount=spec.amount if spec else 1,
        date_unit=spec.unit if spec else "days",
        categories=categories,
        last_n=last_n,
        types=types,
        finder_text=finder_text,
        finder_op=finder_op,
        finder_by_id=finder_by_id,
        count_op=count_op,
        count1=count1,
        count2=count2,
    )
    if log_filter.is_noop():
        return
    c.native(f"Logs ({'; '.join(described)})" if described else "Logs", log_filter)


def _convert_waypoint_tab(gf: GsakFilter, c: Conversion) -> None:
    """GSAK's Unterwegspunkte tab → OpenSAK's native waypoint filter.

    Both tabs work the same way: the criteria describe one child waypoint, and
    the count operator says how many such waypoints the cache needs. The
    general tab's "has child waypoints: yes/no" tick pair belongs to the same
    filter, so it is folded in here.
    """
    has_tab = "cbxcCount" in gf.kv or "cbxctype" in gf.kv
    child = _tri_flag(gf, "chkChildYes", "chkChildNo")
    if not has_tab and child is None:
        return

    described: list[str] = []
    texts: dict[str, tuple[str, str]] = {}

    def _text(op_key: str, val_key: str, field: str, label: str) -> None:
        raw_op = gf.num(op_key) or 0
        op = TEXT_OP.get(raw_op)
        value = gf.text(val_key)
        if op is None:
            if value:
                c.comment(f"Waypoint {label}",
                          f"unknown GSAK comparison index {raw_op} for the value \"{value}\"")
            return
        if not value and op not in TEXT_OP_VALUELESS:
            return
        texts[field] = (value, op)
        described.append(f"{label} {op.replace('_', ' ')} \"{value}\"")

    _text("cbxcCode", "edtcCode", "code", "code")
    _text("cbxctype", "edtctype", "wp_type", "type")
    _text("cbxcDescription", "edtcDescription", "name", "name")
    _text("cbxcComments", "edtcComments", "comment", "comment")

    # The tab's waypoint-type dropdown, which stores an index rather than a
    # name (see WP_TYPE_BY_INDEX). The free-text type row above wins when both
    # are set, since that one is unambiguous.
    type_index = gf.num("cbxCtype2")
    if type_index is not None and "wp_type" not in texts:
        if 0 <= type_index < len(WP_TYPE_BY_INDEX):
            wp_type = WP_TYPE_BY_INDEX[type_index]
            texts["wp_type"] = (wp_type, "equals")
            described.append(f"type {wp_type}")
            c.note(f"Waypoints: GSAK stores its waypoint-type dropdown as a number "
                   f"({type_index}); it was read against the six GPX waypoint types in "
                   f"alphabetical order, which makes it \"{wp_type}\"")
        else:
            c.comment("Waypoint type",
                      f"GSAK's waypoint-type dropdown holds index {type_index}, which is "
                      f"outside the list of types OpenSAK knows")

    spec = _read_date(gf, c, "Waypoint date", "cbxcDate", "edtcDate1", "edtcDate2",
                      "edtcDateDuring", "cbxcDateDuring", DATE_OP_NO_COMPARE)
    if spec is not None:
        described.append(f"dated {spec.op.replace('_', ' ')}")
        c.note("Waypoints: the waypoint-date combo has no \"compared with\" entry, so its "
               "\"any\" sits at index 6 rather than 7")

    by_user = _tri_flag(gf, "chkcByUserYes", "chkcByUserNo")
    if by_user is not None:
        described.append("created by me" if by_user else "not created by me")

    count_op, count1, count2 = "any", 0, 0
    bounds = _num_bounds(gf, c, "Waypoint count", "cbxcCount", "edtcCount1", "edtcCount2")
    if bounds is not None:
        op, v1, v2 = bounds
        count_op, count1, count2 = NUM_OP_TO_COUNT_OP[op], v1, v2
        described.insert(0, f"count {count_op.replace('_', ' ')} "
                            f"{v1}{f'..{v2}' if count_op == 'between' else ''}")
    elif child is not None:
        # "Has child waypoints: yes / no" from the general tab.
        count_op, count1 = ("at_least", 1) if child else ("equal", 0)
        described.insert(0, "at least one" if child else "none")

    if gf.flag("chkcSetFlag", False) or gf.flag("chkcClearFlag", False):
        c.note("Waypoints: the tab's set/clear-flag boxes are a GSAK side effect, not a "
               "filter criterion, so there was nothing to migrate")

    wp_filter = WaypointFilter(
        texts=texts,
        date_op=spec.op if spec else None,
        date1=spec.date1 if spec else None,
        date2=spec.date2 if spec else None,
        date_amount=spec.amount if spec else 1,
        date_unit=spec.unit if spec else "days",
        by_user=by_user,
        count_op=count_op,
        count1=count1,
        count2=count2,
    )
    if wp_filter.is_noop():
        return
    c.native(f"Waypoints ({'; '.join(described)})" if described else "Waypoints", wp_filter)


def _convert_misc(gf: GsakFilter, c: Conversion) -> None:
    # Numeric criteria OpenSAK stores as a column but has no filter row for.
    for label, op_key, v1_key, v2_key, column, extra in (
        ("Elevation", "cbxElevation", "edtElevation", "edtElevation2", "elevation", ""),
        ("User sort", "cbxUsort", "edtUsort", "edtUsort2", "user_sort", ""),
        # GSAK's FoundCount turned out to be a 0/1 "found by me" flag rather
        # than a community find count (verified by the cache importer against a
        # real 12,600-cache database), so found_log_count — how many of your
        # own found-type logs the cache carries — is the honest counterpart.
        ("My found count", "cbxFoundCount", "EdtFoundCount", None, "found_log_count",
         "My found count: GSAK's FoundCount is really a 0/1 found-by-me flag, while "
         "OpenSAK's found_log_count counts your own found-type logs, so it is >= 1 for "
         "exactly the caches GSAK counted as 1"),
        # OpenSAK persists a bearing per cache, recomputed whenever the centre
        # point changes (db/database.py::recalculate_distances).
        ("Bearing", "cbxDegrees", "edtDegrees", None, "bearing",
         "Bearing: measured from the ACTIVE centre point, and only as fresh as the last "
         "distance recalculation"),
    ):
        bounds = _num_bounds(gf, c, label, op_key, v1_key, v2_key)
        if bounds is None:
            continue
        c.sql(label, _num_sql(column, *bounds))
        if extra:
            c.note(extra)

    _text_criterion(gf, c, "cbxSource", "edtSource", "Source", column="source_file")
    _trackable_name_criterion(gf, c)

    if gf.text("edtSymbol"):
        c.comment("Symbol name", (
            f"GSAK matched the GPX symbol name (\"{gf.text('edtSymbol')}\"), which OpenSAK "
            f"does not store"
        ))

    _convert_quadrants(gf, c)

    if gf.flag("chkReverse", False):
        c.comment("Reverse filter", (
            "GSAK's \"reverse filter\" inverts the whole result. OpenSAK cannot negate a "
            "filter set, so everything above matches the NON-inverted criteria"
        ))


def _convert_quadrants(gf: GsakFilter, c: Conversion) -> None:
    """Compass-quadrant tick boxes → a bearing range in SQL."""
    present = [k for k in _QUADRANTS if k in gf.kv]
    if not present:
        return
    selected = [k for k in present if gf.flag(k)]
    if len(selected) == len(present) or not selected:
        return   # all ticked (or none recorded) = no restriction
    tests: list[str] = []
    for key in selected:
        lo, hi = _QUADRANTS[key]
        tests.append(f"(bearing >= {lo} OR bearing < {hi})" if lo > hi
                     else f"(bearing >= {lo} AND bearing < {hi})")
    c.sql("Compass quadrants (" + ", ".join(k[3:] for k in selected) + ")",
          "bearing IS NOT NULL AND (" + " OR ".join(tests) + ")")
    c.note("Compass quadrants: assumed to be the eight 45° sectors centred on their compass "
           "point (N = 337.5°-22.5°), measured from the ACTIVE centre point")


def _trackable_name_criterion(gf: GsakFilter, c: Conversion) -> None:
    """GSAK's TB/coin name box → an EXISTS on OpenSAK's trackables table."""
    raw_op = gf.num("cbxTbugName") or 0
    op = TEXT_OP.get(raw_op)
    value = gf.text("edtTbugName")
    if op is None:
        if value:
            c.comment("TB/coin name",
                      f"unknown GSAK comparison index {raw_op} for the value \"{value}\"")
        return
    if not value and op not in TEXT_OP_VALUELESS:
        return
    if op in TEXT_OP_NO_SQL:
        c.comment("TB/coin name", (
            f"GSAK matched \"{value}\" with a regular expression; OpenSAK keeps trackables in "
            f"their own table and its SQLite connection registers no REGEXP operator"
        ))
        return
    test = _sql_text_test("t.name", op, value)
    if test is None:
        c.comment("TB/coin name", f"GSAK comparison \"{op}\" could not be expressed in SQL")
        return
    c.sql("TB/coin name",
          f"EXISTS (SELECT 1 FROM trackables t WHERE t.cache_id = caches.id AND {test})")
    c.note("TB/coin name: GSAK matched the cache's travel-bug list as one text field; "
           "OpenSAK keeps trackables in their own table, so the test looks for any "
           "trackable whose name matches")


def _convert_where(gf: GsakFilter, c: Conversion, opts: Options) -> None:
    """GSAK's own where clause.

    A clause that translates cleanly becomes executable SQL. One that does not
    becomes a comment holding the translation so far plus every warning —
    never live SQL, because OpenSAK treats a where_clause whose SQL fails as
    "matches nothing", which would silently empty the whole filter.
    """
    if not gf.where:
        return
    custom_columns = [line.split(";")[0] for line in gf.custom if line.split(";")[0]]
    sql, warnings, notes, verified = translate_where(gf.where, custom_columns, opts.me)
    for note in notes:
        c.note(note)
    if verified:
        c.sql("GSAK where clause", sql)
        return
    body = ["Why:"]
    body += [f"- {w[len('where: '):] if w.startswith('where: ') else w}" for w in warnings]
    body.append("Translated as far as it goes — fix it up and uncomment it:")
    body += [f"| {line}" for line in _wrap(sql, 88)]
    c.comment("GSAK where clause",
              "the SQL could not be translated completely, so it was left inactive "
              "(OpenSAK treats a where clause that fails to run as matching nothing, "
              "which would silently empty this filter)",
              lines=body)


# ── Reading gsak.db3 ─────────────────────────────────────────────────────────

class GsakFilterSourceError(Exception):
    """The chosen file is not a GSAK settings database with saved filters."""


def find_gsak_filter_db(path: Path) -> Path:
    """Locate the ``gsak.db3`` holding the saved filters.

    Saved filters live in GSAK's settings database (``gsak.db3``), not in a
    cache database (``sqlite.db3``): a .zip is searched for the former, a
    plain file is taken as given. Raises GsakFilterSourceError when a zip
    holds no gsak.db3.
    """
    path = Path(path)
    if path.suffix.lower() != ".zip":
        return path

    import tempfile
    import zipfile

    extract_dir = Path(tempfile.mkdtemp(prefix="gsak_filters_"))
    with zipfile.ZipFile(path) as zf:
        zf.extractall(extract_dir)
    matches = list(extract_dir.rglob("gsak.db3"))
    if not matches:
        raise GsakFilterSourceError(f"No gsak.db3 file found inside {path.name}")
    return matches[0]


def load_gsak_filters(db_path: Path) -> list[tuple[str, str]]:
    """Return [(name, data)] for every saved filter in gsak.db3 (read-only)."""
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
            rows = conn.execute(
                "SELECT Description, Data FROM TranslateFilters WHERE Type = 'FI'"
            ).fetchall()
    except sqlite3.Error as exc:
        raise GsakFilterSourceError(str(exc)) from exc
    return sorted(
        ((str(name), str(data or "")) for name, data in rows if name),
        key=lambda row: row[0].lower(),
    )


def read_gsak_center(gsak_ini: Path) -> tuple[Optional[tuple[float, float]], bool]:
    """Return ((lat, lon) or None, use_miles) read from gsak.ini.

    The centre point lives in the [LastCenter] section; rbtMiles.Checked says
    whether GSAK's distances are miles rather than kilometres.
    """
    gsak_ini = Path(gsak_ini)
    if not gsak_ini.is_file():
        return None, False
    lat = lon = None
    miles = False
    section = ""
    for line in gsak_ini.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].lower()
            continue
        key, eq, value = line.partition("=")
        if not eq:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "rbtmiles.checked":
            miles = value.lower() == "true"
        elif section == "lastcenter":
            try:
                if key == "lat":
                    lat = float(value)
                elif key == "lon":
                    lon = float(value)
            except ValueError:
                pass
    return ((lat, lon) if lat is not None and lon is not None else None), miles


def default_options(db_path: Optional[Path] = None) -> Options:
    """Build conversion options from OpenSAK's settings plus GSAK's own ini.

    The centre point for distance filters is OpenSAK's home point (the one its
    distance filter measures from); GSAK's unit and the account name behind
    ``isOwner`` come from gsak.ini and OpenSAK's settings respectively.
    """
    center: Optional[tuple[float, float]] = None
    me: Optional[str] = None
    try:
        from opensak.gui.settings import get_settings
        settings = get_settings()
        center = (settings.home_lat, settings.home_lon)
        me = (settings.gc_username or "").strip() or None
    except Exception:
        pass
    miles = False
    if db_path is not None:
        _, miles = read_gsak_center(Path(db_path).with_name("gsak.ini"))
    return Options(center=center, miles=miles, me=me)


# ── Writing profiles ─────────────────────────────────────────────────────────

def safe_filename(name: str) -> str:
    """Mirror FilterProfile.save()'s sanitisation so a later save from inside
    OpenSAK lands on the same file."""
    return "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in name)


def build_profile_data(c: Conversion, source_db: Path) -> dict[str, Any]:
    """The JSON a converted filter is written as."""
    profile = FilterProfile(c.name, c.build_filterset())
    return {
        "name": profile.name,
        "filterset": profile.filterset.to_dict(),
        "sort": profile.sort.to_dict(),
        "_gsak": c.gsak_block(source_db),
    }


@dataclass
class FilterImportEntry:
    """What happened to one GSAK filter."""
    name: str
    conversion: Optional[Conversion] = None
    path: Optional[Path] = None
    written: bool = False
    skipped_existing: bool = False
    error: Optional[str] = None

    @property
    def coverage(self) -> float:
        return self.conversion.coverage if self.conversion else 0.0


@dataclass
class GsakFilterImportResult:
    """Everything the dialog reports after an import."""
    source: Optional[Path] = None
    entries: list[FilterImportEntry] = dc_field(default_factory=list)

    # ── Counts ───────────────────────────────────────────────────────────────

    @property
    def written(self) -> int:
        return sum(1 for e in self.entries if e.written)

    @property
    def skipped(self) -> int:
        return sum(1 for e in self.entries if e.skipped_existing)

    @property
    def failed(self) -> int:
        return sum(1 for e in self.entries if e.error)

    @property
    def _converted(self) -> list[Conversion]:
        return [e.conversion for e in self.entries if e.written and e.conversion]

    # ── Migration coverage ───────────────────────────────────────────────────

    @property
    def total_criteria(self) -> int:
        return sum(c.total for c in self._converted)

    @property
    def migrated_criteria(self) -> int:
        return sum(c.migrated for c in self._converted)

    @property
    def native_criteria(self) -> int:
        return sum(c.native_count for c in self._converted)

    @property
    def sql_criteria(self) -> int:
        return self.migrated_criteria - self.native_criteria

    @property
    def commented_criteria(self) -> int:
        return self.total_criteria - self.migrated_criteria

    @property
    def coverage(self) -> float:
        """Share of all imported criteria that actually run, 0.0-1.0.

        Counted over criteria rather than over filters, so one filter with
        twenty conditions weighs more than one with two — which is what makes
        "100% = everything migrated, 0% = everything sits in comments" true of
        the whole import and not just of the average filter.
        """
        if not self.total_criteria:
            return 1.0
        return self.migrated_criteria / self.total_criteria

    @property
    def fully_migrated(self) -> int:
        return sum(1 for c in self._converted if c.coverage >= 1.0)

    @property
    def partially_migrated(self) -> int:
        return sum(1 for c in self._converted if 0.0 < c.coverage < 1.0)

    @property
    def not_migrated(self) -> int:
        return sum(1 for c in self._converted if c.coverage <= 0.0)


def import_gsak_filters(
    db_path: Path,
    out_dir: Optional[Path] = None,
    names: Optional[list[str]] = None,
    overwrite: bool = False,
    opts: Optional[Options] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> GsakFilterImportResult:
    """Convert saved GSAK filters into OpenSAK filter profiles on disk.

    *names* limits the import to those GSAK filters (all of them when None),
    *overwrite* decides what happens to a profile file that already exists,
    and *out_dir* defaults to OpenSAK's own filter-profile folder.
    """
    db_path = Path(db_path)
    if out_dir is None:
        from opensak.config import get_app_data_dir
        out_dir = get_app_data_dir() / "filters"
    out_dir = Path(out_dir)
    opts = opts or default_options(db_path)

    rows = load_gsak_filters(db_path)
    if names is not None:
        wanted = {n for n in names}
        rows = [r for r in rows if r[0] in wanted]

    out_dir.mkdir(parents=True, exist_ok=True)
    result = GsakFilterImportResult(source=db_path)
    used: set[str] = set()
    total = len(rows)

    for index, (name, data) in enumerate(rows, start=1):
        entry = FilterImportEntry(name=name)
        result.entries.append(entry)
        try:
            conversion = convert(parse_filter_blob(name, data), opts)
            entry.conversion = conversion

            stem = safe_filename(name)
            if stem in used:
                n = 2
                while f"{stem}_{n}" in used:
                    n += 1
                stem = f"{stem}_{n}"
            used.add(stem)
            path = out_dir / f"{stem}.json"
            entry.path = path

            if path.exists() and not overwrite:
                entry.skipped_existing = True
            else:
                path.write_text(
                    json.dumps(build_profile_data(conversion, db_path),
                               indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                entry.written = True
        except Exception as exc:     # one bad filter must not stop the rest
            entry.error = f"{type(exc).__name__}: {exc}"
        if progress_cb is not None:
            progress_cb(index, total)

    return result

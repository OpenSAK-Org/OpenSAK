"""
src/opensak/macro/db_schema.py — the database tables macros may rely on.

From API 4 the database schema is part of the macro API for reading
(decision D3 in #1196): the tables and columns listed in TABLES keep their
names and value formats (FORMATS) within an API version, and are only
added to. A structural change keeps the old names as a compatibility view.
Writing with opensak.sql_write(), the opensak_* tables, indexes and the
columns in INTERNAL are not covered.

api_docs.py renders this as "Database tables for macros" in api.md, and
api_baseline.py records it, so a documented column can't be removed or
renamed without a deprecation. test_macro_db_schema_1202.py checks it
against the real schema: every column listed exists after all migrations,
with the storage its format says, and every column of these tables is
either listed here or in INTERNAL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from opensak.macro.deprecation import Deprecated
from opensak.utils.constants import CACHE_TYPES, CONTAINER_SIZES, LOG_TYPES


@dataclass(frozen=True)
class Format:
    """How the values of a column are stored."""

    label: str          # shown in the Format column of the docs
    description: str    # shown in the table of value formats
    affinity: str       # SQLite type affinity of the column (INTEGER, REAL, TEXT, NUMERIC)


def _quoted(values) -> str:
    return ", ".join(f'"{v}"' for v in values)


# Every format a documented column has, in documentation order.
FORMATS: dict[str, Format] = {
    "key": Format(
        "integer key",
        "A row's key (`id`) or the key of the cache it belongs to (`cache_id` = "
        "`caches.id`). Only valid within one database: a cache copied or moved to "
        "another database gets a new `id`, so match caches across databases by "
        "`gc_code`.",
        "INTEGER"),
    "integer": Format("integer", "A whole number.", "INTEGER"),
    "text": Format("text", "Text (UTF-8).", "TEXT"),
    "boolean": Format(
        "0 / 1",
        "`1` for yes, `0` for no: `WHERE found = 1`, `SUM(found)`. Not `true`/`false` "
        "(opensak.cache() turns them into Lua booleans, opensak.sql() does not).",
        "NUMERIC"),
    "datetime": Format(
        "date and time",
        'Text `"YYYY-MM-DD HH:MM:SS.ffffff"`, e.g. `"2024-05-01 00:00:00.000000"`, '
        "so it sorts and compares as text: `hidden_date >= '2024-01-01'`. "
        "`substr(x, 1, 10)` or `date(x)` gives the date. Dates from a listing or a "
        "log (hidden, found, log dates) have the time the import file gives, often "
        "00:00:00; the times OpenSAK records itself (imports, changes) are UTC.",
        "NUMERIC"),
    "degrees": Format(
        "decimal degrees",
        "A latitude (−90 … 90, south negative) or longitude (−180 … 180, west "
        "negative) in decimal degrees, WGS84.",
        "REAL"),
    "rating": Format("1 – 5", "Difficulty or terrain: 1.0 to 5.0 in steps of 0.5.", "REAL"),
    "km": Format("km", "A distance in kilometres.", "REAL"),
    "metres": Format("metres", "A height in metres above sea level.", "REAL"),
    "bearing": Format("degrees 0 – 360", "A compass bearing in degrees, 0 = north, 90 = east.",
                      "REAL"),
    "cache_type": Format(
        "cache type",
        f"One of {_quoted(CACHE_TYPES)}.",
        "TEXT"),
    "container": Format(
        "container",
        f"One of {_quoted(CONTAINER_SIZES)}.",
        "TEXT"),
    "log_type": Format(
        "log type",
        f"geocaching.com's log type: {_quoted(LOG_TYPES)}. A log imported from "
        "elsewhere may have another type.",
        "TEXT"),
}


@dataclass(frozen=True)
class Column:
    """A column macros may read."""

    name: str
    format: str                 # key of FORMATS
    description: str
    # (API version, what to use instead) once the column is deprecated
    deprecated: Optional[Deprecated] = None


@dataclass(frozen=True)
class Table:
    """A table of the database, with the columns macros may read."""

    name: str
    description: str
    columns: tuple[Column, ...]


def _c(name: str, format: str, description: str) -> Column:
    return Column(name, format, description)


_ID = _c("id", "key", "The row's key.")
_CACHE_ID = _c("cache_id", "key", "The cache it belongs to (`caches.id`).")

TABLES: tuple[Table, ...] = (
    Table("caches", "One row per cache (and per custom waypoint, see `parent_gc_code`).", (
        _c("id", "key", "The cache's key, used by `cache_id` in the other tables."),
        _c("gc_code", "text", 'GC code, e.g. "GC12345"; unique in the database.'),
        _c("name", "text", "Cache name."),
        _c("cache_type", "cache_type", "Cache type."),
        _c("container", "container", "Container size; NULL if not known."),
        _c("latitude", "degrees", "Posted latitude."),
        _c("longitude", "degrees", "Posted longitude."),
        _c("difficulty", "rating", "Difficulty."),
        _c("terrain", "rating", "Terrain."),
        _c("placed_by", "text", "Placed-by name as shown on the listing."),
        _c("owner_name", "text", "Owner's geocaching.com name."),
        _c("owner_id", "text", "Owner's geocaching.com member id."),
        _c("hidden_date", "datetime", "Hidden (placed) date."),
        _c("last_updated", "datetime", "When the listing last changed, as the import file says."),
        _c("available", "boolean", "1 = not disabled."),
        _c("archived", "boolean", "1 = archived."),
        _c("premium_only", "boolean", "1 = premium-member only."),
        _c("short_description", "text", "Short description."),
        _c("short_desc_html", "boolean", "1 = the short description is HTML."),
        _c("long_description", "text", "Long description."),
        _c("long_desc_html", "boolean", "1 = the long description is HTML."),
        _c("encoded_hints", "text", "Hint text as imported."),
        _c("country", "text", "Country."),
        _c("state", "text", "State / region."),
        _c("county", "text", "County."),
        _c("found", "boolean", "1 = found by you."),
        _c("found_date", "datetime", "Your find date."),
        _c("dnf", "boolean", "1 = your latest log is a Didn't find it."),
        _c("dnf_date", "datetime",
           "Date of a Didn't find it log: your own from GSAK (DNFDate), the "
           "latest by anyone from a GPX file."),
        _c("first_to_find", "boolean", "1 = you were first to find."),
        _c("user_flag", "boolean", "User flag."),
        _c("user_sort", "integer", "User sort value."),
        _c("user_data_1", "text", "User data field 1 (GSAK's UserData)."),
        _c("user_data_2", "text", "User data field 2."),
        _c("user_data_3", "text", "User data field 3."),
        _c("user_data_4", "text", "User data field 4."),
        _c("distance", "km", "Distance from the active centre point; NULL until calculated."),
        _c("bearing", "bearing", "Bearing from the active centre point; NULL until calculated."),
        _c("favorite_points", "integer", "Favourite points; NULL until known."),
        _c("find_count", "integer", "Number of Found it logs by anyone."),
        _c("gc_note", "text", "Personal note from geocaching.com."),
        _c("url", "text", "Listing URL."),
        _c("elevation", "metres", "Elevation; NULL if not known."),
        _c("color", "text", 'Colour tag, e.g. "#FF5733".'),
        _c("guid", "text", "geocaching.com listing GUID."),
        _c("watch", "boolean", "1 = on the watchlist."),
        _c("gc_cache_id", "text", "geocaching.com's numeric cache id, as text."),
        _c("log_count", "integer", "Number of logs stored."),
        _c("trackable_count", "integer", "Number of trackables in the cache."),
        _c("found_log_count", "integer",
           "Number of your own Found it (Attended, Webcam Photo Taken) logs."),
        _c("waypoint_count", "integer", "Number of additional waypoints."),
        _c("last_log_date", "datetime", "Date of the latest log."),
        _c("last_found_date", "datetime", "Date of the latest Found it log by anyone."),
        _c("parent_gc_code", "text",
           "For a custom waypoint: the GC code of the cache it belongs to; NULL for caches."),
        _c("locked", "boolean", "1 = locked against import changes."),
        _c("imported_at", "datetime", "When the cache was first imported (UTC)."),
        _c("last_gpx_update", "datetime", "When an import last touched the cache (UTC)."),
        _c("last_user_update", "datetime",
           "When a user field (user flag, user sort, user data, lock, note or correction) "
           "last changed (UTC)."),
        _c("macro_flag", "boolean", "Flag for macros' own use (GSAK's MacroFlag)."),
        _c("macro_sort", "text", "Text for macros' own use (GSAK's MacroSort)."),
        _c("is_owner", "boolean", "1 = owned by your geocaching.com account in Settings."),
        _c("symbol", "text", 'GPX waypoint symbol, e.g. "Geocache Found".'),
    )),
    Table("logs", "The logs of the caches.", (
        _ID,
        _CACHE_ID,
        _c("log_id", "text", "geocaching.com's log id; unique."),
        _c("log_type", "log_type", "Log type."),
        _c("log_date", "datetime", "Log date."),
        _c("finder", "text", "Name of the logger."),
        _c("finder_id", "text", "geocaching.com member id of the logger."),
        _c("text", "text", "Log text."),
        _c("text_encoded", "boolean", "1 = the log text is encoded (ROT13)."),
        _c("latitude", "degrees", "Coordinates of the log (e.g. Earthcache logs); usually NULL."),
        _c("longitude", "degrees", "Coordinates of the log; usually NULL."),
        _c("logged_by_owner", "boolean", "1 = logged by the cache owner."),
    )),
    Table("waypoints", "The additional waypoints of the caches (parking, stages, final, …).", (
        _ID,
        _CACHE_ID,
        _c("prefix", "text", 'Waypoint prefix, e.g. "PK", "S1".'),
        _c("wp_type", "text", 'Waypoint type, e.g. "Parking Area", "Final Location".'),
        _c("name", "text", "Name."),
        _c("description", "text", "Description."),
        _c("comment", "text", "Comment."),
        _c("latitude", "degrees", "Latitude; NULL if not known yet."),
        _c("longitude", "degrees", "Longitude; NULL if not known yet."),
        _c("parent_gc_code", "text", "GC code of the cache."),
        _c("wp_code", "text", "Waypoint code, unique within the cache."),
        _c("url", "text", "URL."),
        _c("wp_date", "datetime", "Date."),
        _c("created_by_user", "boolean", "1 = added in OpenSAK (or by a macro), not imported."),
        _c("wp_flag", "boolean", "User flag of the waypoint."),
    )),
    Table("attributes", "The attributes of the caches.", (
        _ID,
        _CACHE_ID,
        _c("attribute_id", "integer", "Groundspeak attribute id, e.g. 1 = dogs; unique within the cache."),
        _c("name", "text", 'Name, e.g. "Dogs allowed".'),
        _c("is_on", "boolean", "1: yes, 0: no (e.g. no dogs)."),
    )),
    Table("trackables", "The trackables in the caches.", (
        _ID,
        _CACHE_ID,
        _c("tracking_code", "text", "Tracking code."),
        _c("name", "text", "Name."),
        _c("ref", "text", 'Reference, e.g. "TB1234".'),
    )),
    Table("user_notes", "Your note and corrected coordinates: at most one row per cache.", (
        _ID,
        _c("cache_id", "key", "The cache it belongs to (`caches.id`); unique."),
        _c("note", "text", "Your local note."),
        _c("corrected_lat", "degrees", "Corrected latitude."),
        _c("corrected_lon", "degrees", "Corrected longitude."),
        _c("is_corrected", "boolean",
           "1 = the cache has corrected coordinates: test this, not the "
           "coordinates, as opensak.cache() does."),
        _c("corrected_source", "text",
           'Who corrected the coordinates: "user", "macro" or "import" (from GSAK); '
           "NULL if not known."),
        _c("updated_at", "datetime", "When the row last changed (UTC)."),
    )),
)

# Columns of these tables that are left out on purpose: OpenSAK's own
# bookkeeping, which may change in any version. A new column goes either
# into TABLES (macros may rely on it) or here.
INTERNAL: dict[str, frozenset[str]] = {
    "caches": frozenset({
        "location_source", "location_basis", "location_updated", "location_dataset",
        "source_file", "last_four_logs",
    }),
}


def table(name: str) -> Optional[Table]:
    return next((t for t in TABLES if t.name == name), None)

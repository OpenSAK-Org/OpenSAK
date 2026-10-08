"""
src/opensak/macro/cache_data.py — cache records as plain data for Lua macros.

Macros never see live ORM objects (the runtime's attribute_filter denies all
access to Python objects); they get snapshots built from CACHE_FIELDS. The
API field names are stable and separate from the database schema, so a
schema migration does not break macros: `code`, `lat`, `lon`, `owner`
instead of `gc_code`, `latitude`, `longitude`, `owner_name`.

Records are loaded with a column query in chunks, never as full Cache
objects, so iterating a 50k-cache database stays cheap — and cheaper still
when a macro asks for a few fields only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Iterator, Optional

from sqlalchemy import select

from opensak.db.models import Cache, UserNote

# Codes per query — well below SQLite's bound-parameter limit.
CHUNK_SIZE = 500


def _date(value: Optional[datetime]) -> Optional[str]:
    return value.strftime("%Y-%m-%d") if value else None


def _bool(value: Any) -> bool:
    return bool(value)


def _user_data(*values: Optional[str]) -> list[str]:
    return [v or "" for v in values]


def _corrected(is_corrected, lat, lon) -> Optional[dict[str, float]]:
    if is_corrected and lat is not None and lon is not None:
        return {"lat": lat, "lon": lon}
    return None


@dataclass(frozen=True)
class CacheField:
    """One field of the cache table a macro receives."""

    name: str
    # Lua Language Server type of the value (nil is always possible unless
    # the type says boolean/integer and the column is NOT NULL)
    type: str
    description: str
    columns: tuple
    # columns' values → the Lua value; default: the single column as is
    convert: Optional[Callable[..., Any]] = None

    def value(self, row: Any) -> Any:
        values = [row[_label(c)] for c in self.columns]
        if self.convert is None:
            return values[0]
        return self.convert(*values)


def _label(column) -> str:
    return f"{column.class_.__tablename__}_{column.key}"


# Every key of the table returned by opensak.cache() / opensak.caches(), in
# documentation order.
CACHE_FIELDS: tuple[CacheField, ...] = (
    CacheField("code", "string", 'GC code, e.g. "GC12345".', (Cache.gc_code,)),
    CacheField("name", "string", "Cache name.", (Cache.name,)),
    CacheField("type", "string", 'Cache type, e.g. "Traditional Cache".', (Cache.cache_type,)),
    CacheField("container", "string?", 'Container size, e.g. "Small".', (Cache.container,)),
    CacheField("lat", "number", "Posted latitude (decimal degrees).", (Cache.latitude,)),
    CacheField("lon", "number", "Posted longitude (decimal degrees).", (Cache.longitude,)),
    CacheField("difficulty", "number?", "Difficulty 1–5.", (Cache.difficulty,)),
    CacheField("terrain", "number?", "Terrain 1–5.", (Cache.terrain,)),
    CacheField("owner", "string?", "Owner name.", (Cache.owner_name,)),
    CacheField("placed_by", "string?", "Placed-by name as shown on the listing.",
               (Cache.placed_by,)),
    CacheField("hidden", "string?", 'Hidden date, "YYYY-MM-DD".', (Cache.hidden_date,), _date),
    CacheField("found", "boolean", "Found by you.", (Cache.found,), _bool),
    CacheField("found_date", "string?", 'Your find date, "YYYY-MM-DD".',
               (Cache.found_date,), _date),
    CacheField("dnf", "boolean", "Your latest log is a Didn't find it.", (Cache.dnf,), _bool),
    CacheField("dnf_date", "string?", 'Date of your DNF, "YYYY-MM-DD".',
               (Cache.dnf_date,), _date),
    CacheField("ftf", "boolean", "You were first to find.", (Cache.first_to_find,), _bool),
    CacheField("available", "boolean", "Not disabled.", (Cache.available,), _bool),
    CacheField("archived", "boolean", "Archived.", (Cache.archived,), _bool),
    CacheField("premium", "boolean", "Premium-member only.", (Cache.premium_only,), _bool),
    CacheField("country", "string?", "Country.", (Cache.country,)),
    CacheField("state", "string?", "State / region.", (Cache.state,)),
    CacheField("county", "string?", "County.", (Cache.county,)),
    CacheField("distance", "number?", "Distance from the active centre point in km.",
               (Cache.distance,)),
    CacheField("bearing", "number?", "Bearing from the active centre point in degrees.",
               (Cache.bearing,)),
    CacheField("elevation", "number?", "Elevation in metres.", (Cache.elevation,)),
    CacheField("favorite_points", "integer?", "Favourite points (nil until known).",
               (Cache.favorite_points,)),
    CacheField("find_count", "integer?", "Number of Found it logs by anyone.",
               (Cache.find_count,)),
    CacheField("user_flag", "boolean", "User flag.", (Cache.user_flag,), _bool),
    CacheField("user_sort", "integer?", "User sort value.", (Cache.user_sort,)),
    CacheField("user_data", "string[]", 'User data fields 1–4; "" when empty.',
               (Cache.user_data_1, Cache.user_data_2, Cache.user_data_3, Cache.user_data_4),
               _user_data),
    CacheField("color", "string?", 'Colour tag, e.g. "#FF5733".', (Cache.color,)),
    CacheField("locked", "boolean", "Locked against import changes.", (Cache.locked,), _bool),
    CacheField("watch", "boolean", "On the watchlist.", (Cache.watch,), _bool),
    CacheField("note", "string?", "Your local note.", (UserNote.note,)),
    CacheField("gc_note", "string?", "Personal note from geocaching.com.", (Cache.gc_note,)),
    CacheField("hint", "string?", "Hint text as imported.", (Cache.encoded_hints,)),
    CacheField("url", "string?", "Listing URL.", (Cache.url,)),
    CacheField("corrected", "{lat: number, lon: number}?",
               "Corrected coordinates; nil if the cache is not solved.",
               (UserNote.is_corrected, UserNote.corrected_lat, UserNote.corrected_lon),
               _corrected),
    CacheField("waypoint_count", "integer", "Number of additional waypoints.",
               (Cache.waypoint_count,)),
    CacheField("log_count", "integer", "Number of logs stored.", (Cache.log_count,)),
    CacheField("trackable_count", "integer", "Number of trackables in the cache.",
               (Cache.trackable_count,)),
    CacheField("last_log_date", "string?", 'Date of the latest log, "YYYY-MM-DD".',
               (Cache.last_log_date,), _date),
)

_BY_NAME = {f.name: f for f in CACHE_FIELDS}
FIELD_NAMES = tuple(_BY_NAME)


class UnknownField(ValueError):
    """A requested field name is not in CACHE_FIELDS."""


def resolve_fields(names: Optional[Iterable[str]]) -> tuple[CacheField, ...]:
    """The fields to load: all for None, else *names* plus `code` (always
    included so a record can be identified)."""
    if names is None:
        return CACHE_FIELDS
    wanted = {str(n) for n in names}
    unknown = wanted - set(_BY_NAME)
    if unknown:
        raise UnknownField(
            f"unknown cache field(s) {sorted(unknown)}; valid fields: {', '.join(FIELD_NAMES)}"
        )
    wanted.add("code")
    return tuple(f for f in CACHE_FIELDS if f.name in wanted)


def _query(fields: tuple[CacheField, ...]):
    columns = {_label(Cache.gc_code): Cache.gc_code}
    for f in fields:
        for c in f.columns:
            columns.setdefault(_label(c), c)
    stmt = select(*(c.label(name) for name, c in columns.items())).select_from(Cache)
    if any(c.class_ is UserNote for c in columns.values()):
        stmt = stmt.outerjoin(UserNote, UserNote.cache_id == Cache.id)
    return stmt


def load_records(
    session, codes: list[str], fields: tuple[CacheField, ...] = CACHE_FIELDS
) -> list[dict[str, Any]]:
    """Records for *codes*, in that order; codes not in the database are
    left out."""
    stmt = _query(fields)
    by_code: dict[str, dict[str, Any]] = {}
    for start in range(0, len(codes), CHUNK_SIZE):
        chunk = codes[start:start + CHUNK_SIZE]
        for row in session.execute(stmt.where(Cache.gc_code.in_(chunk))).mappings():
            by_code[row[_label(Cache.gc_code)]] = {f.name: f.value(row) for f in fields}
    return [by_code[c] for c in codes if c in by_code]


def iter_records(
    session_factory, codes: list[str], fields: tuple[CacheField, ...] = CACHE_FIELDS
) -> Iterator[dict[str, Any]]:
    """Like load_records(), but loads one chunk at a time — each with its
    own short session, so a macro loop never holds a session open."""
    for start in range(0, len(codes), CHUNK_SIZE):
        with session_factory() as session:
            records = load_records(session, codes[start:start + CHUNK_SIZE], fields)
        yield from records


def load_description(session, code: str) -> Optional[dict[str, Any]]:
    """{short, long, html} of one cache, or None if it is not in the database."""
    row = session.execute(
        select(
            Cache.short_description, Cache.short_desc_html,
            Cache.long_description, Cache.long_desc_html,
        ).where(Cache.gc_code == code)
    ).first()
    if row is None:
        return None
    short, short_html, long, long_html = row
    return {
        "short": short,
        "long": long,
        "html": bool(long_html if long else short_html),
    }

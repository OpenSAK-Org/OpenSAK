"""
src/opensak/macro/cache_write.py — changing cache data from Lua macros.

opensak.update() and opensak.insert() take the same field names as the
cache table a macro reads (cache_data.CACHE_FIELDS), so a macro can read a
cache, change a few keys and write them back. Only the fields in
WRITABLE_FIELDS can be written. The others are either the cache's identity
(`code`) or maintained by OpenSAK itself: distance and bearing follow from
the coordinates and the centre point, the counts and log dates from the
logs, waypoints and trackables, and last_gpx_update from imports. Writing
one of them is an error rather than being ignored, so a macro never thinks
it changed something it did not.

opensak.sql_write() (sql.py, WritableDatabase) enforces the same rules on
the database columns: PROTECTED_COLUMNS can never be set by an UPDATE, and
after each statement refresh_derived() recalculates the derived columns of
every cache the statement touched, which also overwrites whatever an
INSERT put into them.

A Lua table cannot hold nil, so `false` clears a field that is not a
boolean (e.g. `{ container = false }`); for text fields "" does too.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

from opensak.db.models import Cache, UserNote
from opensak.utils.constants import CACHE_TYPES, FOUND_LOG_TYPES

# Rows per IN (...) list — well below SQLite's bound-parameter limit.
CHUNK_SIZE = 500


class CacheWriteError(ValueError):
    """A value or field name a macro passed cannot be written."""


# ── Value parsers: Lua value → value for the column ─────────────────────────


def _clear(value: Any) -> bool:
    """`false` (or "" for text) means: set the field to nil."""
    return value is False


def _text(name: str, value: Any, required: bool = False) -> Optional[str]:
    if not required and (_clear(value) or value == ""):
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise CacheWriteError(
            f"{name} must be a non-empty string" if required
            else f"{name} must be a string, or false to clear it"
        )
    return value.strip() if required else value


def _bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise CacheWriteError(f"{name} must be true or false, got {value!r}")
    return value


def _float(name: str, value: Any, lo: float, hi: float, required: bool = False) -> Optional[float]:
    if not required and _clear(value):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            pass
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
        raise CacheWriteError(f"{name} must be a number" + ("" if required else ", or false to clear it"))
    if not lo <= value <= hi:
        raise CacheWriteError(f"{name} must be between {lo:g} and {hi:g}, got {value:g}")
    return float(value)


def _rating(name: str, value: Any) -> Optional[float]:
    rating = _float(name, value, 1.0, 5.0)
    if rating is not None and rating * 2 != int(rating * 2):
        raise CacheWriteError(f"{name} must be in steps of 0.5, got {rating:g}")
    return rating


def _int(name: str, value: Any) -> Optional[int]:
    if _clear(value):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CacheWriteError(f"{name} must be a whole number >= 0, or false to clear it")
    return value


def _date(name: str, value: Any) -> Optional[datetime]:
    if _clear(value) or value == "":
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip())
        except ValueError:
            pass
    raise CacheWriteError(
        f'{name} must be a date "YYYY-MM-DD" (or "YYYY-MM-DDTHH:MM:SS"), '
        f"or false to clear it, got {value!r}"
    )


def _cache_type(name: str, value: Any) -> str:
    """Accept both "Traditional Cache" and the short "Traditional"."""
    if isinstance(value, str):
        wanted = value.strip().lower()
        for full in CACHE_TYPES:
            if full.lower() in (wanted, f"{wanted} cache"):
                return full
    raise CacheWriteError(f"unknown cache type {value!r}")


_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _color(name: str, value: Any) -> Optional[str]:
    if _clear(value) or value == "":
        return None
    if not isinstance(value, str) or not _COLOR_RE.match(value.strip()):
        raise CacheWriteError(f'{name} must be a colour "#RRGGBB", or false to clear it')
    return value.strip().upper()


def _user_data(name: str, value: Any) -> dict[int, Optional[str]]:
    """{ [1] = "a", [3] = "c" } → {1: "a", 3: "c"}; the other slots stay."""
    if not isinstance(value, dict) or not value:
        raise CacheWriteError(f'{name} must be a table, e.g. {{ "a", "b" }} or {{ [3] = "c" }}')
    result: dict[int, Optional[str]] = {}
    for key, item in value.items():
        if isinstance(key, float) and key.is_integer():
            key = int(key)
        if not isinstance(key, int) or not 1 <= key <= 4:
            raise CacheWriteError(f"{name} has the slots 1 to 4, got {key!r}")
        result[key] = _text(f"{name}[{key}]", item)
    return result


def _corrected(name: str, value: Any) -> Optional[tuple[float, float]]:
    """{ lat = .., lon = .. } or a coordinate string → (lat, lon); false → None."""
    if _clear(value):
        return None
    if isinstance(value, str):
        from opensak.coords import parse_coords

        parsed = parse_coords(value)
        if parsed is None:
            raise CacheWriteError(f"{name}: cannot parse coordinates {value!r}")
        lat, lon = parsed
    elif isinstance(value, dict) and set(value) == {"lat", "lon"}:
        lat, lon = value["lat"], value["lon"]
    else:
        raise CacheWriteError(
            f'{name} must be {{ lat = .., lon = .. }}, a coordinate string, or false to clear it'
        )
    return (
        _float(f"{name}.lat", lat, -90.0, 90.0, required=True),
        _float(f"{name}.lon", lon, -180.0, 180.0, required=True),
    )


# ── Writable fields ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WritableField:
    """A cache field macros may write, with the Lua type it accepts."""

    name: str
    type: str
    parse: Callable[[str, Any], Any]
    # Cache attribute the parsed value goes to (None: handled in _apply)
    attribute: Optional[str] = None


def _required_text(name: str, value: Any) -> str:
    return _text(name, value, required=True)  # type: ignore[return-value]


WRITABLE_FIELDS: tuple[WritableField, ...] = (
    WritableField("name", "string", _required_text, "name"),
    WritableField("type", "string", _cache_type, "cache_type"),
    WritableField("container", "string|false", _text, "container"),
    WritableField("lat", "number", lambda n, v: _float(n, v, -90.0, 90.0, required=True), "latitude"),
    WritableField("lon", "number", lambda n, v: _float(n, v, -180.0, 180.0, required=True), "longitude"),
    WritableField("difficulty", "number|false", _rating, "difficulty"),
    WritableField("terrain", "number|false", _rating, "terrain"),
    WritableField("owner", "string|false", _text, "owner_name"),
    WritableField("placed_by", "string|false", _text, "placed_by"),
    WritableField("hidden", "string|false", _date, "hidden_date"),
    WritableField("found", "boolean", _bool, "found"),
    WritableField("found_date", "string|false", _date, "found_date"),
    WritableField("dnf", "boolean", _bool, "dnf"),
    WritableField("dnf_date", "string|false", _date, "dnf_date"),
    WritableField("ftf", "boolean", _bool, "first_to_find"),
    WritableField("available", "boolean", _bool, "available"),
    WritableField("archived", "boolean", _bool, "archived"),
    WritableField("premium", "boolean", _bool, "premium_only"),
    WritableField("country", "string|false", _text, "country"),
    WritableField("state", "string|false", _text, "state"),
    WritableField("county", "string|false", _text, "county"),
    WritableField("elevation", "number|false",
                  lambda n, v: _float(n, v, -1000.0, 10000.0), "elevation"),
    WritableField("favorite_points", "integer|false", _int, "favorite_points"),
    WritableField("find_count", "integer|false", _int, "find_count"),
    WritableField("user_flag", "boolean", _bool, "user_flag"),
    WritableField("user_sort", "integer|false", _int, "user_sort"),
    WritableField("user_data", "table<integer, string|false>", _user_data),
    WritableField("color", "string|false", _color, "color"),
    WritableField("locked", "boolean", _bool, "locked"),
    WritableField("watch", "boolean", _bool, "watch"),
    WritableField("note", "string|false", _text),
    WritableField("gc_note", "string|false", _text, "gc_note"),
    WritableField("hint", "string|false", _text, "encoded_hints"),
    WritableField("url", "string|false", _text, "url"),
    WritableField("corrected", "{lat: number, lon: number}|string|false", _corrected),
)

_WRITABLE = {f.name: f for f in WRITABLE_FIELDS}
WRITABLE_NAMES = tuple(_WRITABLE)

# Cache fields a macro reads but can never write, and why.
PROTECTED_FIELDS: dict[str, str] = {
    "code": "it identifies the cache",
    "distance": "OpenSAK calculates it from the coordinates and the centre point",
    "bearing": "OpenSAK calculates it from the coordinates and the centre point",
    "waypoint_count": "OpenSAK counts the waypoints",
    "log_count": "OpenSAK counts the logs",
    "trackable_count": "OpenSAK counts the trackables",
    "last_log_date": "OpenSAK takes it from the logs",
    "last_gpx_update": "it records when an import last touched the cache",
}

# Fields opensak.insert{} needs (besides `code`).
INSERT_REQUIRED = ("name", "type", "lat", "lon")


def parse_values(func: str, values: dict, insert: bool = False) -> dict[str, Any]:
    """Check the fields a macro passed and convert their values.

    *values* is the Lua table as a dict (nested tables as dicts too). For an
    insert, `code` is accepted as well and the INSERT_REQUIRED fields must
    be present. Raises CacheWriteError with *func* in the message.
    """
    if not values:
        raise CacheWriteError(f"{func} expects a table of fields, e.g. {{ user_flag = true }}")
    parsed: dict[str, Any] = {}
    for key, value in values.items():
        if insert and key == "code":
            parsed["code"] = _gc_code(func, value)
            continue
        field = _WRITABLE.get(key) if isinstance(key, str) else None
        if field is None:
            if key in PROTECTED_FIELDS:
                raise CacheWriteError(f"{func}: {key} cannot be changed — {PROTECTED_FIELDS[key]}")
            raise CacheWriteError(
                f"{func}: unknown field {key!r}; writable fields: {', '.join(WRITABLE_NAMES)}"
            )
        try:
            parsed[key] = field.parse(key, value)
        except CacheWriteError as exc:
            raise CacheWriteError(f"{func}: {exc}") from None
    if insert:
        missing = [k for k in ("code", *INSERT_REQUIRED) if k not in parsed]
        if missing:
            raise CacheWriteError(f"{func}: missing field(s) {', '.join(missing)}")
    return parsed


def _gc_code(func: str, code: Any) -> str:
    if not isinstance(code, str) or not code.strip():
        raise CacheWriteError(f"{func}: code must be a cache code, e.g. \"GC12345\"")
    return code.strip().upper()


def _apply(session, cache: Cache, values: dict[str, Any]) -> None:
    for key, value in values.items():
        field = _WRITABLE.get(key)
        if field is None:          # "code" of an insert
            continue
        if field.attribute is not None:
            setattr(cache, field.attribute, value)
        elif key == "user_data":
            for slot, text in value.items():
                setattr(cache, f"user_data_{slot}", text)
        else:                      # note, corrected: in user_notes
            note = cache.user_note
            if note is None:
                note = UserNote(cache_id=cache.id)
                session.add(note)
                cache.user_note = note
            if key == "note":
                note.note = value
            else:
                note.corrected_lat, note.corrected_lon = value or (None, None)
                note.is_corrected = value is not None


def update_cache(session, code: str, values: dict[str, Any]) -> bool:
    """Write the parse_values() result *values* to the cache *code*.
    Returns False if the cache is not in the database."""
    cache = session.query(Cache).filter_by(gc_code=code).first()
    if cache is None:
        return False
    _apply(session, cache, values)
    session.flush()
    if "lat" in values or "lon" in values:
        refresh_derived(_session_executor(session), {cache.id: {"coords"}})
    return True


def insert_cache(session, values: dict[str, Any]) -> str:
    """Add a new cache from the parse_values(insert=True) result *values*;
    returns its code. Raises CacheWriteError if the code exists."""
    code = values["code"]
    if session.query(Cache.id).filter_by(gc_code=code).first() is not None:
        raise CacheWriteError(f"opensak.insert: {code} is already in the database")
    cache = Cache(
        gc_code=code,
        name=values["name"],
        cache_type=values["type"],
        latitude=values["lat"],
        longitude=values["lon"],
        imported_at=_now(),
    )
    session.add(cache)
    session.flush()
    # Not from an import, so no import ever refreshed it. Set after the
    # INSERT, which fills in the column default even for an explicit None.
    cache.last_gpx_update = None
    _apply(session, cache, values)
    session.flush()
    refresh_derived(_session_executor(session), {cache.id: {"coords"}})
    return code


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Derived columns ──────────────────────────────────────────────────────────
#
# Shared by the ORM writes above and by opensak.sql_write(). The SQL is
# written with :name parameters, which both sqlite3 and SQLAlchemy's text()
# understand; *execute* runs one statement and returns its rows.

Executor = Callable[[str, dict], list]


def _session_executor(session) -> Executor:
    from sqlalchemy import text

    def execute(sql: str, params: dict) -> list:
        result = session.execute(text(sql), params)
        return result.fetchall() if result.returns_rows else []

    return execute


def _chunks(ids: Iterable[int]) -> Iterable[tuple[str, dict]]:
    """(":i0, :i1, ...", {"i0": .., "i1": ..}) per CHUNK_SIZE ids."""
    ids = sorted(set(ids))
    for start in range(0, len(ids), CHUNK_SIZE):
        chunk = ids[start:start + CHUNK_SIZE]
        names = [f"i{n}" for n in range(len(chunk))]
        yield ", ".join(f":{n}" for n in names), dict(zip(names, chunk))


def _db_datetime(value: datetime) -> str:
    """The format SQLAlchemy stores DateTime columns in."""
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")


def refresh_derived(execute: Executor, kinds_by_id: dict[int, set[str]]) -> None:
    """Recalculate the derived columns of the given caches.

    *kinds_by_id* maps a cache id to what changed: "coords" (distance and
    bearing), "logs", "waypoints", "trackables", or "insert" (a new row:
    all of them, plus the import columns).
    """
    def ids(kind: str) -> set[int]:
        return {i for i, kinds in kinds_by_id.items() if kind in kinds or "insert" in kinds}

    inserted = {i for i, kinds in kinds_by_id.items() if "insert" in kinds}
    for marks, params in _chunks(inserted):
        execute(
            "UPDATE caches SET gc_code = upper(trim(gc_code)), imported_at = :now, "
            "last_gpx_update = NULL, source_file = NULL, location_source = NULL, "
            "location_basis = NULL, location_updated = NULL, location_dataset = NULL "
            f"WHERE id IN ({marks})",
            {**params, "now": _db_datetime(_now())},
        )
    _refresh_distances(execute, ids("coords"))
    _refresh_logs(execute, ids("logs"))
    for marks, params in _chunks(ids("waypoints")):
        execute(
            "UPDATE caches SET waypoint_count = (SELECT COUNT(*) FROM waypoints "
            f"WHERE waypoints.cache_id = caches.id) WHERE id IN ({marks})",
            params,
        )
        execute(
            "UPDATE waypoints SET parent_gc_code = (SELECT gc_code FROM caches "
            "WHERE caches.id = waypoints.cache_id) "
            f"WHERE cache_id IN ({marks})",
            params,
        )
    for marks, params in _chunks(ids("trackables")):
        execute(
            "UPDATE caches SET trackable_count = (SELECT COUNT(*) FROM trackables "
            f"WHERE trackables.cache_id = caches.id) WHERE id IN ({marks})",
            params,
        )


def _refresh_distances(execute: Executor, ids: set[int]) -> None:
    """Distance and bearing from the active centre point, as
    recalculate_distances() does for all caches."""
    if not ids:
        return
    from opensak import geodesy
    from opensak.filters.engine import distance_km_batch
    from opensak.gui.settings import get_settings

    settings = get_settings()
    lat0, lon0 = settings.home_lat, settings.home_lon
    for marks, params in _chunks(ids):
        rows = execute(
            f"SELECT id, latitude, longitude FROM caches WHERE id IN ({marks}) "
            "AND latitude IS NOT NULL AND longitude IS NOT NULL",
            params,
        )
        if not rows:
            continue
        if lat0 is None or lon0 is None:
            for row in rows:
                execute("UPDATE caches SET distance = NULL, bearing = NULL WHERE id = :id",
                        {"id": row[0]})
            continue
        dists = distance_km_batch(lat0, lon0, [r[1] for r in rows], [r[2] for r in rows])
        for row, dist in zip(rows, dists):
            execute(
                "UPDATE caches SET distance = :d, bearing = :b WHERE id = :id",
                {"d": float(dist), "b": geodesy.bearing(lat0, lon0, row[1], row[2]), "id": row[0]},
            )


_FOUND_TYPES = ", ".join(f"'{t}'" for t in sorted(FOUND_LOG_TYPES))


def _refresh_logs(execute: Executor, ids: set[int]) -> None:
    """The log counts and dates, as the importer derives them."""
    if not ids:
        return
    from opensak.gui.settings import get_settings
    from opensak.utils.utils import count_own_found_logs

    settings = get_settings()
    for marks, params in _chunks(ids):
        execute(
            "UPDATE caches SET "
            "log_count = (SELECT COUNT(*) FROM logs WHERE logs.cache_id = caches.id), "
            "last_log_date = (SELECT MAX(log_date) FROM logs WHERE logs.cache_id = caches.id), "
            "last_found_date = (SELECT MAX(log_date) FROM logs WHERE logs.cache_id = caches.id "
            f"  AND logs.log_type IN ({_FOUND_TYPES})), "
            "last_four_logs = (SELECT group_concat(line, char(10)) FROM ("
            "  SELECT strftime('%Y-%m-%dT%H:%M:%S', log_date) || char(9) || log_type "
            "         || char(9) || COALESCE(finder, '') AS line "
            "  FROM logs WHERE logs.cache_id = caches.id AND log_date IS NOT NULL "
            "  ORDER BY log_date DESC LIMIT 4)) "
            f"WHERE id IN ({marks})",
            params,
        )
        logs: dict[int, list[dict]] = {i: [] for i in params.values()}
        for cache_id, log_type, finder, finder_id in execute(
            f"SELECT cache_id, log_type, finder, finder_id FROM logs WHERE cache_id IN ({marks})",
            params,
        ):
            logs[cache_id].append({"log_type": log_type, "finder": finder or "",
                                   "finder_id": finder_id or ""})
        for cache_id, cache_logs in logs.items():
            execute(
                "UPDATE caches SET found_log_count = :n WHERE id = :id",
                {"n": count_own_found_logs(cache_logs, settings.gc_finder_id,
                                           settings.gc_username),
                 "id": cache_id},
            )

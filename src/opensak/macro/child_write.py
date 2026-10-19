"""
src/opensak/macro/child_write.py — the rows that belong to a cache (logs,
waypoints, attributes, trackables) for Lua macros, without SQL.

Each table is described by a ChildTable: the Lua field names, how a value
is parsed (the parsers of cache_write.py, so `false` clears a field there
too) and the model attribute it goes to. opensak.logs(code) and friends
list the rows of a cache, each with its `id`; opensak.insert_log(code, {..})
adds one and returns its id; opensak.update_log(id, {..}) changes the given
fields of one.

The keys (`id`, the cache a row belongs to) and the columns OpenSAK
maintains (a waypoint's parent_gc_code, a log's gc.com log_id) cannot be
written, as for opensak.sql_write() (sql.PROTECTED_COLUMNS). After a write,
cache_write.refresh_derived() recalculates the cache's counts and log dates
(inside opensak.transaction(): before the commit, or on opensak.resync()).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from opensak.db.models import Attribute, Cache, Log, Trackable, Waypoint
from opensak.macro.cache_write import (
    CacheWriteError,
    _bool,
    _date,
    _float,
    _int,
    _required_text,
    _session_executor,
    _text,
    refresh_derived,
)


@dataclass(frozen=True)
class ChildField:
    """A field of a cache's log, waypoint, ... that macros read and write."""

    name: str
    type: str
    description: str
    attribute: str
    parse: Callable[[str, Any], Any]


@dataclass(frozen=True)
class ChildTable:
    """One table of rows belonging to a cache."""

    singular: str                   # "log": opensak.insert_log, update_log
    plural: str                     # "logs": opensak.logs
    model: Any
    fields: tuple[ChildField, ...]
    required: tuple[str, ...]       # fields opensak.insert_<singular> needs
    kind: Optional[str]             # what refresh_derived() recalculates
    order: tuple                    # order of opensak.<plural>()
    # Model attributes set on every row a macro inserts
    insert_defaults: tuple[tuple[str, Any], ...] = ()

    @property
    def class_name(self) -> str:
        """Lua Language Server class of a row, e.g. "opensak.Log"."""
        return f"opensak.{self.singular.capitalize()}"

    def field(self, name: str) -> Optional[ChildField]:
        return next((f for f in self.fields if f.name == name), None)


def _lat(name: str, value: Any) -> Optional[float]:
    return _float(name, value, -90.0, 90.0)


def _lon(name: str, value: Any) -> Optional[float]:
    return _float(name, value, -180.0, 180.0)


def _attribute_id(name: str, value: Any) -> int:
    parsed = _int(name, value) if value is not False else None
    if parsed is None:
        raise CacheWriteError(f"{name} must be a whole number >= 0")
    return parsed


LOG = ChildTable(
    "log", "logs", Log,
    (
        ChildField("type", "string", 'Log type, e.g. "Found it", "Write note".',
                   "log_type", _required_text),
        ChildField("date", "string?", 'Log date "YYYY-MM-DDTHH:MM:SS" (a date alone is midnight).',
                   "log_date", _date),
        ChildField("finder", "string?", "Name of the logger.", "finder", _text),
        ChildField("finder_id", "string?", "geocaching.com id of the logger.", "finder_id", _text),
        ChildField("text", "string?", "Log text.", "text", _text),
        ChildField("encoded", "boolean", "The log text is encoded (ROT13).", "text_encoded", _bool),
        ChildField("lat", "number?", "Coordinates of the log (e.g. Earthcache logs).", "latitude", _lat),
        ChildField("lon", "number?", "Coordinates of the log.", "longitude", _lon),
        ChildField("by_owner", "boolean", "Logged by the cache owner.", "logged_by_owner", _bool),
    ),
    required=("type",),
    kind="logs",
    order=(Log.log_date.desc(), Log.id),
)

WAYPOINT = ChildTable(
    "waypoint", "waypoints", Waypoint,
    (
        ChildField("prefix", "string", 'Waypoint prefix, e.g. "PK", "S1".', "prefix", _required_text),
        ChildField("type", "string", 'Waypoint type, e.g. "Parking Area", "Final Location".',
                   "wp_type", _required_text),
        ChildField("name", "string?", "Name.", "name", _text),
        ChildField("code", "string?", "Waypoint code, unique within the cache.", "wp_code", _text),
        ChildField("lat", "number?", "Latitude; nil if not known yet.", "latitude", _lat),
        ChildField("lon", "number?", "Longitude; nil if not known yet.", "longitude", _lon),
        ChildField("description", "string?", "Description.", "description", _text),
        ChildField("comment", "string?", "Comment.", "comment", _text),
        ChildField("url", "string?", "URL.", "url", _text),
        ChildField("date", "string?", 'Date "YYYY-MM-DDTHH:MM:SS".', "wp_date", _date),
        ChildField("flag", "boolean", "User flag of the waypoint.", "wp_flag", _bool),
    ),
    required=("prefix", "type"),
    kind="waypoints",
    order=(Waypoint.prefix, Waypoint.id),
    insert_defaults=(("created_by_user", True),),
)

ATTRIBUTE = ChildTable(
    "attribute", "attributes", Attribute,
    (
        ChildField("attribute_id", "integer", "Groundspeak attribute id, unique within the cache.",
                   "attribute_id", _attribute_id),
        ChildField("name", "string?", 'Name, e.g. "Dogs allowed".', "name", _text),
        ChildField("on", "boolean", "true: yes, false: no (e.g. no dogs).", "is_on", _bool),
    ),
    required=("attribute_id",),
    kind=None,
    order=(Attribute.attribute_id, Attribute.id),
    insert_defaults=(("is_on", True),),
)

TRACKABLE = ChildTable(
    "trackable", "trackables", Trackable,
    (
        ChildField("ref", "string?", 'Reference, e.g. "TB1234".', "ref", _text),
        ChildField("name", "string?", "Name.", "name", _text),
        ChildField("tracking_code", "string?", "Tracking code.", "tracking_code", _text),
    ),
    required=(),
    kind="trackables",
    order=(Trackable.id,),
)

CHILD_TABLES: tuple[ChildTable, ...] = (LOG, WAYPOINT, ATTRIBUTE, TRACKABLE)


def parse_child_values(
    func: str, table: ChildTable, values: dict, insert: bool = False
) -> dict[str, Any]:
    """Check the fields a macro passed and convert their values to model
    attributes. Raises CacheWriteError with *func* in the message."""
    if not values:
        example = f"{table.fields[0].name} = .."
        raise CacheWriteError(f"{func} expects a table of fields, e.g. {{ {example} }}")
    parsed: dict[str, Any] = {}
    for key, value in values.items():
        field = table.field(key) if isinstance(key, str) else None
        if field is None:
            names = ", ".join(f.name for f in table.fields)
            if key == "id":
                raise CacheWriteError(f"{func}: id cannot be changed — it identifies the {table.singular}")
            raise CacheWriteError(f"{func}: unknown field {key!r}; writable fields: {names}")
        try:
            parsed[field.attribute] = field.parse(key, value)
        except CacheWriteError as exc:
            raise CacheWriteError(f"{func}: {exc}") from None
    if insert:
        missing = [k for k in table.required if table.field(k).attribute not in parsed]  # type: ignore[union-attr]
        if missing:
            raise CacheWriteError(f"{func}: missing field(s) {', '.join(missing)}")
    return parsed


def _lua_value(value: Any) -> Any:
    return value.strftime("%Y-%m-%dT%H:%M:%S") if isinstance(value, datetime) else value


def list_children(session, table: ChildTable, code: str) -> Optional[list[dict]]:
    """The rows of the cache *code* as dicts with `id` and the field names
    (NULL = missing); None if the cache is not in the database."""
    cache_id = session.query(Cache.id).filter_by(gc_code=code).scalar()
    if cache_id is None:
        return None
    rows = session.query(table.model).filter_by(cache_id=cache_id).order_by(*table.order)
    result = []
    for row in rows:
        record = {"id": row.id}
        for field in table.fields:
            value = _lua_value(getattr(row, field.attribute))
            if value is not None:
                record[field.name] = value
        result.append(record)
    return result


def _refresh(session, table: ChildTable, cache_id: int) -> None:
    if table.kind is not None:
        session.flush()
        refresh_derived(_session_executor(session), {cache_id: {table.kind}})


def insert_child(
    session, table: ChildTable, code: str, values: dict[str, Any], refresh: bool = True
) -> Optional[int]:
    """Add a row from the parse_child_values(insert=True) result *values*
    to the cache *code*; returns its id, None if the cache is not in the
    database. *refresh* as for cache_write.update_cache()."""
    cache_id = session.query(Cache.id).filter_by(gc_code=code).scalar()
    if cache_id is None:
        return None
    row = table.model(cache_id=cache_id, **dict(table.insert_defaults))
    for attribute, value in values.items():
        setattr(row, attribute, value)
    session.add(row)
    session.flush()
    if refresh:
        _refresh(session, table, cache_id)
    return row.id


def update_child(
    session, table: ChildTable, row_id: int, values: dict[str, Any], refresh: bool = True
) -> Optional[str]:
    """Write the parse_child_values() result *values* to the row *row_id*;
    returns the GC code of its cache, None if there is no such row.
    *refresh* as for cache_write.update_cache()."""
    row = session.get(table.model, row_id)
    if row is None:
        return None
    for attribute, value in values.items():
        setattr(row, attribute, value)
    session.flush()
    if refresh:
        _refresh(session, table, row.cache_id)
    return session.query(Cache.gc_code).filter_by(id=row.cache_id).scalar()

"""
src/opensak/db/custom_fields.py — custom fields (#1028) in the companion database.

Custom fields are user-defined columns per cache, as in GSAK. They live in
the companion database (db/companion.py, #1034), owned by OpenSAK:

    custom_fields   one row per field definition (kind 'free')
    custom_values   gc_code PRIMARY KEY + one real column per field
                    (kind 'cache': rows follow their caches by GC code)

Design: docs/architecture/gsak-fields.md and plans/companion-database.md.

Fields
------
* A field is matched by its ``field_id`` (a UUID), never by its name, so a
  rename keeps the values.
* **Local** fields belong to one database. **Global** fields are defined
  once in the app settings (GLOBAL_KEY, the master) and copied into each
  database's ``custom_fields`` (``scope = 'global'``), so a database stays
  readable on its own (backup, copy, another computer).
* A database gets its companion only when something is stored in it.
  Global fields don't create one: until a database stores a value, the
  view below shows their default.
* Columns are nullable and have no default. No row is needed for an
  untouched cache: the ``caches_all`` view shows the field's default for
  a missing row or a NULL, so a GSAK clause like ``Angel = 0`` also
  matches caches nobody touched. Changing a default is therefore only a
  change of the definition.
* ``expression`` keeps GSAK's ``fexpression`` as it was. In GSAK it is a
  display transform of the stored value (``nullif(GCFinds,-1)``): the cache
  list and the detail panel show it (display_expression(), load_values()),
  filters and sorting use the stored value, as in GSAK.

Applying definitions
--------------------
apply_definitions() runs when a database is opened (db/database.py) and
after every change made here. It brings the database's copy in step with
the global master and makes sure every field has its column:

* a field without a column gets one (ADD COLUMN);
* a global field renamed in the master is renamed here (RENAME COLUMN);
* a global field deleted from the master (GLOBAL_DELETED_KEY) is marked
  orphaned; its column and values stay;
* a changed type is destructive and is only reported (ApplyResult.pending);
  change_type() converts the column once the user agreed.

Columns are never dropped automatically: drop_field() does that on request.

The caches_all view
-------------------
SQLite doesn't let a view in one file refer to another, so ``caches_all``
is a TEMP view, made per connection by ensure_view() (called on every
checkout of the engines' connections): ``caches`` plus one column per
field, ``COALESCE(value, default)``. It is rebuilt when the definitions
changed (changed()).

Values are written on the companion's own connection (write_values());
values_model() maps ``custom_values`` for SQLAlchemy sessions whose
connections have the companion attached.

Display (#1028 step 4)
----------------------
load_values() reads the fields of many caches at once from ``caches_all``
(the cache list, the detail panel), each with its display value: the
field's expression where it has one that works (display_expression()), else
the stored value. Expressions only ever run inside a SELECT, with an
authorizer that allows nothing but reading (read_only()).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from opensak.db import companion
from opensak.db.companion import COMPANION_SCHEMA, CompanionError

logger = logging.getLogger(__name__)

FIELDS_TABLE = "custom_fields"
VALUES_TABLE = "custom_values"
VIEW = "caches_all"
CODE_COLUMN = "gc_code"

TYPE_STRING = "string"
TYPE_INTEGER = "integer"
TYPE_REAL = "real"
TYPE_DATE = "date"
TYPE_BOOLEAN = "boolean"
TYPES = (TYPE_STRING, TYPE_INTEGER, TYPE_REAL, TYPE_DATE, TYPE_BOOLEAN)

SCOPE_LOCAL = "local"
SCOPE_GLOBAL = "global"
ORIGIN_OPENSAK = "opensak"
ORIGIN_GSAK = "gsak"

# App settings (settings_store): the master definitions of the global
# fields, and the ids of global fields deleted from it.
GLOBAL_KEY = "custom_fields.global"
GLOBAL_DELETED_KEY = "custom_fields.global_deleted"

# Suffix added to a name that can't be used as it is (rename_for_import()).
COLLISION_SUFFIX = "_custom"
MAX_NAME_LENGTH = 64
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_SQL_TYPES = {
    TYPE_STRING: "TEXT",
    TYPE_INTEGER: "INTEGER",
    TYPE_REAL: "REAL",
    TYPE_DATE: "TEXT",
    TYPE_BOOLEAN: "INTEGER",
}
# Type names accepted from outside (GSAK's ftype, #126's "Decimal"), lower case.
_TYPE_ALIASES = {
    "string": TYPE_STRING, "text": TYPE_STRING,
    "integer": TYPE_INTEGER, "int": TYPE_INTEGER,
    "real": TYPE_REAL, "decimal": TYPE_REAL, "float": TYPE_REAL, "number": TYPE_REAL,
    "date": TYPE_DATE,
    "boolean": TYPE_BOOLEAN, "bool": TYPE_BOOLEAN,
}
_TRUE = {"1", "true", "yes", "y", "on", "-1"}
_FALSE = {"0", "false", "no", "n", "off", ""}

# SQLite's keywords (https://sqlite.org/lang_keywords.html). Names are always
# quoted here, but GSAK filters and macros write them unquoted.
SQL_KEYWORDS = frozenset("""
ABORT ACTION ADD AFTER ALL ALTER ALWAYS ANALYZE AND AS ASC ATTACH AUTOINCREMENT
BEFORE BEGIN BETWEEN BY CASCADE CASE CAST CHECK COLLATE COLUMN COMMIT CONFLICT
CONSTRAINT CREATE CROSS CURRENT CURRENT_DATE CURRENT_TIME CURRENT_TIMESTAMP
DATABASE DEFAULT DEFERRABLE DEFERRED DELETE DESC DETACH DISTINCT DO DROP EACH
ELSE END ESCAPE EXCEPT EXCLUDE EXCLUSIVE EXISTS EXPLAIN FAIL FILTER FIRST
FOLLOWING FOR FOREIGN FROM FULL GENERATED GLOB GROUP GROUPS HAVING IF IGNORE
IMMEDIATE IN INDEX INDEXED INITIALLY INNER INSERT INSTEAD INTERSECT INTO IS
ISNULL JOIN KEY LAST LEFT LIKE LIMIT MATCH MATERIALIZED NATURAL NO NOT NOTHING
NOTNULL NULL NULLS OF OFFSET ON OR ORDER OTHERS OUTER OVER PARTITION PLAN
PRAGMA PRECEDING PRIMARY QUERY RAISE RANGE RECURSIVE REFERENCES REGEXP REINDEX
RELEASE RENAME REPLACE RESTRICT RETURNING RIGHT ROLLBACK ROW ROWS SAVEPOINT
SELECT SET TABLE TEMP TEMPORARY THEN TIES TO TRANSACTION TRIGGER UNBOUNDED
UNION UNIQUE UPDATE USING VACUUM VALUES VIEW VIRTUAL WHEN WHERE WINDOW WITH
WITHOUT
""".split())

_FIELDS_DDL = f"""CREATE TABLE IF NOT EXISTS {{schema}}.{FIELDS_TABLE} (
    field_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    type TEXT NOT NULL CHECK (type IN ({", ".join(f"'{t}'" for t in TYPES)})),
    position INTEGER NOT NULL DEFAULT 0,
    default_value TEXT,
    expression TEXT,
    control TEXT,
    height INTEGER,
    scope TEXT NOT NULL CHECK (scope IN ('{SCOPE_LOCAL}', '{SCOPE_GLOBAL}')),
    origin TEXT NOT NULL DEFAULT '{ORIGIN_OPENSAK}',
    orphaned INTEGER NOT NULL DEFAULT 0
)"""
_VALUES_DDL = f"""CREATE TABLE IF NOT EXISTS {{schema}}.{VALUES_TABLE} (
    {CODE_COLUMN} TEXT PRIMARY KEY
)"""
_FIELD_COLUMNS = ("field_id", "name", "type", "position", "default_value", "expression",
                  "control", "height", "scope", "origin", "orphaned")

# Rows per executemany() in write_values().
_BATCH = 500


class CustomFieldError(Exception):
    """A field can't be defined or changed as asked."""


@dataclass(frozen=True)
class Field:
    """One custom field definition."""

    field_id: str
    name: str
    type: str
    position: int = 0
    default: Optional[str] = None       # as text, as GSAK stores it
    expression: Optional[str] = None    # GSAK's fexpression, for display
    control: Optional[str] = None       # kept for GSAK round trips
    height: Optional[int] = None
    scope: str = SCOPE_LOCAL
    origin: str = ORIGIN_OPENSAK
    orphaned: bool = False

    @property
    def default_value(self) -> Any:
        """The default as a value of the field's type (None if unusable)."""
        value, _ok = convert(self.type, self.default)
        if value is None and self.type in (TYPE_INTEGER, TYPE_BOOLEAN):
            return 0
        if value is None and self.type == TYPE_REAL:
            return 0.0
        if value is None and self.type in (TYPE_STRING, TYPE_DATE):
            return ""
        return value

    def to_dict(self) -> dict[str, Any]:
        """For the global master in the app settings."""
        return {
            "field_id": self.field_id, "name": self.name, "type": self.type,
            "position": self.position, "default": self.default,
            "expression": self.expression, "control": self.control,
            "height": self.height, "origin": self.origin,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Field":
        return cls(
            field_id=str(data["field_id"]), name=str(data["name"]),
            type=normalize_type(data.get("type")) or TYPE_STRING,
            position=int(data.get("position") or 0), default=data.get("default"),
            expression=data.get("expression") or None, control=data.get("control") or None,
            height=data.get("height"), scope=SCOPE_GLOBAL,
            origin=data.get("origin") or ORIGIN_OPENSAK,
        )


@dataclass(frozen=True)
class PendingChange:
    """A change of the global master that needs the user's agreement."""

    field_id: str
    name: str
    old_type: str
    new_type: str


@dataclass
class ApplyResult:
    """What apply_definitions() did to a database."""

    added: list[str] = field(default_factory=list)          # columns added
    renamed: list[tuple[str, str]] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)       # fields marked orphaned
    pending: list[PendingChange] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)      # what couldn't be applied

    @property
    def changed(self) -> bool:
        return bool(self.added or self.renamed or self.orphaned)


# ── Types and names ───────────────────────────────────────────────────────────

def normalize_type(value: Any) -> Optional[str]:
    """One of TYPES for a type name (GSAK's ``ftype`` too), None if unknown."""
    if value is None:
        return None
    return _TYPE_ALIASES.get(str(value).strip().lower())


def sql_type(type_: str) -> str:
    """The SQLite column type of a field type."""
    return _SQL_TYPES[type_]


def convert(type_: str, value: Any) -> tuple[Any, bool]:
    """
    *value* as a value of the field type *type_*. Returns (value, ok); when
    it can't be converted, ok is False and the value is returned unchanged
    (SQLite keeps it, like GSAK would). None stays None.
    """
    if value is None:
        return None, True
    if type_ == TYPE_BOOLEAN:
        if isinstance(value, bool):
            return int(value), True
        if isinstance(value, (int, float)):
            return int(value != 0), True
        text = str(value).strip().lower()
        if text in _TRUE:
            return 1, True
        if text in _FALSE:
            return 0, True
        return value, False
    if type_ == TYPE_INTEGER:
        if isinstance(value, bool):
            return int(value), True
        if isinstance(value, int):
            return value, True
        try:
            number = float(str(value).strip())
        except ValueError:
            return (None, True) if str(value).strip() == "" else (value, False)
        return (int(number), True) if number.is_integer() else (value, False)
    if type_ == TYPE_REAL:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value), True
        text = str(value).strip()
        if text == "":
            return None, True
        try:
            return float(text), True
        except ValueError:
            return value, False
    return str(value), True


def reserved_names() -> set[str]:
    """Lower-case names a field can't have: the columns of ``caches`` (they
    share the caches_all view) and SQL keywords."""
    from opensak.db.models import Cache
    names = {str(c.name).lower() for c in Cache.__table__.columns}
    names.update(k.lower() for k in SQL_KEYWORDS)
    return names


def check_name(name: str, taken: Iterable[str] = ()) -> None:
    """Raise CustomFieldError if *name* can't be a field name (also when
    it is one of *taken*, compared case-insensitively)."""
    if not _NAME_RE.match(name or ""):
        raise CustomFieldError(
            f"{name!r} is not a valid field name (letters, digits and _, "
            "not starting with a digit)")
    if len(name) > MAX_NAME_LENGTH:
        raise CustomFieldError(f"{name!r} is longer than {MAX_NAME_LENGTH} characters")
    lower = name.lower()
    if lower in reserved_names() or lower.startswith(("sqlite_", "opensak_")):
        raise CustomFieldError(f"{name!r} is reserved (a cache column or an SQL keyword)")
    if lower in {t.lower() for t in taken}:
        raise CustomFieldError(f"a field named {name!r} exists already")


def rename_for_import(name: str, taken: Iterable[str] = ()) -> str:
    """
    A usable field name for *name*: invalid characters become ``_``, and a
    name that is reserved (check_name()) or in *taken* gets COLLISION_SUFFIX
    (``Name`` → ``Name_custom``, then ``Name_custom2`` …). Returns *name*
    itself when it can be used as it is.
    """
    taken_lower = {t.lower() for t in taken}
    clean = re.sub(r"[^A-Za-z0-9_]", "_", str(name or "").strip()) or "field"
    if clean[0].isdigit():
        clean = "_" + clean
    clean = clean[:MAX_NAME_LENGTH - len(COLLISION_SUFFIX) - 3]

    def usable(candidate: str) -> bool:
        try:
            check_name(candidate)
        except CustomFieldError:
            return False
        return candidate.lower() not in taken_lower

    if usable(clean):
        return clean
    candidate = clean + COLLISION_SUFFIX
    number = 2
    while not usable(candidate):
        candidate = f"{clean}{COLLISION_SUFFIX}{number}"
        number += 1
    return candidate


def new_field_id() -> str:
    return str(uuid.uuid4())


# ── Global master (app settings) ──────────────────────────────────────────────

def global_fields() -> list[Field]:
    """The global fields defined in the app settings, by position."""
    from opensak.settings_store import get_store
    raw = get_store().get(GLOBAL_KEY) or []
    fields: list[Field] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            fields.append(Field.from_dict(item))
        except (KeyError, TypeError, ValueError):
            logger.warning("custom fields: ignoring a broken global definition: %r", item)
    return sorted(fields, key=lambda f: (f.position, f.name.lower()))


def deleted_global_ids() -> set[str]:
    from opensak.settings_store import get_store
    raw = get_store().get(GLOBAL_DELETED_KEY) or []
    return {str(x) for x in raw} if isinstance(raw, list) else set()


def _save_globals(fields: list[Field], deleted: Optional[set[str]] = None) -> None:
    from opensak.settings_store import get_store
    updates: dict[str, Any] = {GLOBAL_KEY: [f.to_dict() for f in fields]}
    if deleted is not None:
        updates[GLOBAL_DELETED_KEY] = sorted(deleted)
    get_store().set_many(updates)
    changed()


def define_global(field_: Field) -> Field:
    """Add *field_* to the global master (or replace the one with its id)."""
    field_ = replace(field_, scope=SCOPE_GLOBAL, orphaned=False)
    others = [f for f in global_fields() if f.field_id != field_.field_id]
    check_name(field_.name, (f.name for f in others))
    deleted = deleted_global_ids() - {field_.field_id}
    _save_globals([*others, field_], deleted)
    return field_


# ── Definitions in a database ─────────────────────────────────────────────────

def stored_fields(db_path: Path, include_orphaned: bool = False) -> list[Field]:
    """The definitions stored in the companion of *db_path*, by position."""
    if not companion.exists(db_path):
        return []
    conn = _open(db_path)
    try:
        return _read_fields(conn, "main", include_orphaned)
    finally:
        conn.close()


def fields(db_path: Path) -> list[Field]:
    """
    The fields *db_path* has: those stored in its companion, plus the
    global fields it doesn't store yet (they show their default). Orphaned
    fields are left out. By position.
    """
    stored = stored_fields(db_path)
    return _effective(stored, global_fields(), deleted_global_ids())


def _effective(stored: list[Field], globals_: list[Field], deleted: set[str]) -> list[Field]:
    ids = {f.field_id for f in stored}
    names = {f.name.lower() for f in stored}
    extra = [g for g in globals_
             if g.field_id not in ids and g.field_id not in deleted
             and g.name.lower() not in names]
    return sorted([*stored, *extra], key=lambda f: (f.position, f.name.lower()))


def define_field(
    db_path: Path,
    name: str,
    type_: str,
    *,
    default: Optional[str] = None,
    position: Optional[int] = None,
    expression: Optional[str] = None,
    control: Optional[str] = None,
    height: Optional[int] = None,
    scope: str = SCOPE_LOCAL,
    origin: str = ORIGIN_OPENSAK,
    field_id: Optional[str] = None,
) -> Field:
    """
    Define a field in the database at *db_path* and add its column; a
    global one (*scope*) also goes into the global master. Creates the
    companion if needed. Raises CustomFieldError (bad name, type or a name
    in use) or CompanionError (no usable companion).
    """
    normalized = normalize_type(type_)
    if normalized is None:
        raise CustomFieldError(f"unknown field type {type_!r} (one of {', '.join(TYPES)})")
    if scope not in (SCOPE_LOCAL, SCOPE_GLOBAL):
        raise CustomFieldError(f"unknown scope {scope!r}")
    existing = fields(db_path)
    check_name(name, (f.name for f in existing if f.field_id != field_id))
    new = Field(
        field_id=field_id or new_field_id(), name=name, type=normalized,
        position=position if position is not None else _next_position(existing),
        default=default, expression=expression or None, control=control or None,
        height=height, scope=scope, origin=origin,
    )
    if scope == SCOPE_GLOBAL:
        define_global(new)
    with _companion(db_path) as conn:
        _store_field(conn, new)
    changed(db_path)
    return new


def rename_field(db_path: Path, field_id: str, new_name: str) -> Field:
    """Rename a field and its column; a global field also in the master."""
    current = _field(db_path, field_id)
    check_name(new_name, (f.name for f in fields(db_path) if f.field_id != field_id))
    renamed = replace(current, name=new_name)
    if current.scope == SCOPE_GLOBAL and any(g.field_id == field_id for g in global_fields()):
        define_global(renamed)
    if companion.exists(db_path):
        with _companion(db_path) as conn:
            if _stored(conn, field_id) is not None:
                _rename(conn, current.name, new_name, field_id)
    changed(db_path)
    return renamed


def delete_field(db_path: Path, field_id: str) -> None:
    """
    Delete a field: its definition is marked orphaned, its column and
    values stay until drop_field(). A global field leaves the master too
    (and becomes orphaned in other databases when they are opened).
    """
    current = _field(db_path, field_id)
    if current.scope == SCOPE_GLOBAL:
        _save_globals([g for g in global_fields() if g.field_id != field_id],
                      deleted_global_ids() | {field_id})
    if companion.exists(db_path):
        with _companion(db_path) as conn:
            conn.execute(f"UPDATE {FIELDS_TABLE} SET orphaned = 1 WHERE field_id = ?",
                         (field_id,))
    changed(db_path)


def drop_field(db_path: Path, field_id: str) -> None:
    """Remove a field's definition, column and values for good (destructive:
    the caller has asked the user). Global fields: only in this database."""
    stored = {f.field_id: f for f in stored_fields(db_path, include_orphaned=True)}
    current = stored.get(field_id)
    if current is None:
        raise CustomFieldError(f"no stored field with id {field_id}")
    with _companion(db_path) as conn:
        if current.name.lower() in _columns(conn):
            conn.execute(f"ALTER TABLE {VALUES_TABLE} DROP COLUMN {_q(current.name)}")
        conn.execute(f"DELETE FROM {FIELDS_TABLE} WHERE field_id = ?", (field_id,))
    changed(db_path)


def change_type(db_path: Path, field_id: str, new_type: str) -> Field:
    """
    Change a field's type and convert its stored values (destructive: values
    that don't convert become NULL, i.e. the default). The caller has asked
    the user. A global field changes in the master too.
    """
    normalized = normalize_type(new_type)
    if normalized is None:
        raise CustomFieldError(f"unknown field type {new_type!r}")
    current = _field(db_path, field_id)
    if current.scope == SCOPE_GLOBAL and any(g.field_id == field_id for g in global_fields()):
        define_global(replace(current, type=normalized))
    if companion.exists(db_path):
        with _companion(db_path) as conn:
            stored = _stored(conn, field_id)
            if stored is not None:
                _convert_column(conn, stored.name, normalized)
                conn.execute(f"UPDATE {FIELDS_TABLE} SET type = ? WHERE field_id = ?",
                             (normalized, field_id))
    changed(db_path)
    return replace(current, type=normalized)


def update_definition(db_path: Path, field_id: str, **values: Any) -> Field:
    """Change what doesn't touch the column: default, position, expression,
    control, height. A global field changes in the master too."""
    allowed = {"default", "position", "expression", "control", "height"}
    unknown = set(values) - allowed
    if unknown:
        raise CustomFieldError(f"can't change {', '.join(sorted(unknown))} this way")
    current = _field(db_path, field_id)
    updated = replace(current, **values)
    if current.scope == SCOPE_GLOBAL and any(g.field_id == field_id for g in global_fields()):
        define_global(updated)
    if companion.exists(db_path):
        with _companion(db_path) as conn:
            if _stored(conn, field_id) is not None:
                _update_row(conn, updated)
    changed(db_path)
    return updated


def apply_definitions(db_path: Path) -> ApplyResult:
    """
    Bring the custom fields of *db_path* in step with the global master and
    make sure every field has its column (see the module docstring). Only
    for a database whose companion exists and is usable; never creates one.
    Raises sqlite3.Error if the companion can't be written.
    """
    result = ApplyResult()
    if not companion.exists(db_path) or companion.status(db_path) is not companion.Status.OK:
        return result
    conn = _open(db_path)
    try:
        if not _has_table(conn, FIELDS_TABLE):
            # Nothing stored; global fields alone don't make a database
            # store them (lazy).
            return result
        conn.execute("BEGIN IMMEDIATE")
        try:
            _apply(conn, result)
        except BaseException:
            conn.rollback()
            raise
        conn.commit()
    finally:
        conn.close()
    if result.changed:
        changed(db_path)
    for change in result.pending:
        logger.info("custom fields: %s: field %s is %s here but %s in the global "
                    "definitions — not converted without confirmation",
                    db_path, change.name, change.old_type, change.new_type)
    for conflict in result.conflicts:
        logger.warning("custom fields: %s: %s", db_path, conflict)
    return result


def _apply(conn: sqlite3.Connection, result: ApplyResult) -> None:
    stored = {f.field_id: f for f in _read_fields(conn, "main", include_orphaned=True)}
    deleted = deleted_global_ids()
    for master in global_fields():
        local = stored.get(master.field_id)
        if local is None or local.orphaned:
            continue    # stored only once a value is (lazy), or deleted here
        if local.name != master.name:
            clash = next((f for f in stored.values()
                          if f.field_id != local.field_id and f.name.lower() == master.name.lower()), None)
            if clash is not None:
                result.conflicts.append(
                    f"global field {local.name} was renamed to {master.name}, "
                    f"but a field of that name exists here — not renamed")
            else:
                _rename(conn, local.name, master.name, local.field_id)
                result.renamed.append((local.name, master.name))
                local = replace(local, name=master.name)
        if local.type != master.type:
            result.pending.append(PendingChange(local.field_id, local.name, local.type, master.type))
        synced = replace(local, default=master.default, position=master.position,
                         expression=master.expression, control=master.control,
                         height=master.height)
        if synced != local:
            _update_row(conn, synced)
        stored[local.field_id] = synced
    for f in stored.values():
        if f.scope == SCOPE_GLOBAL and not f.orphaned and f.field_id in deleted:
            conn.execute(f"UPDATE {FIELDS_TABLE} SET orphaned = 1 WHERE field_id = ?", (f.field_id,))
            result.orphaned.append(f.name)
    _ensure_values_table(conn)
    columns = _columns(conn)
    for f in stored.values():
        if f.orphaned or f.field_id in deleted or f.name.lower() in columns:
            continue
        _add_column(conn, f)
        result.added.append(f.name)


def on_open(db_path: Path) -> None:
    """apply_definitions() when a database is opened. Never raises."""
    try:
        apply_definitions(db_path)
    except (sqlite3.Error, OSError) as exc:
        logger.warning("custom fields: could not apply the definitions to %s: %s", db_path, exc)


# ── Values ────────────────────────────────────────────────────────────────────

def write_values(db_path: Path, rows: Iterable[tuple[str, dict[str, Any]]]) -> int:
    """
    Write custom values: *rows* are (gc_code, {field name: value}). Only the
    named fields of a cache change (an upsert); each field must be one of
    fields(). A global field the database doesn't store yet is stored now.
    Values are stored as given. Runs in one transaction on the companion's
    own connection: call it after the caches are committed (main database
    first). Returns the rows written.
    """
    known = {f.name.lower(): f for f in fields(db_path)}
    used: dict[str, Field] = {}
    written = 0
    by_columns: dict[tuple[str, ...], list[tuple[Any, ...]]] = {}
    for code, values in rows:
        if not code:
            continue
        names: list[str] = []
        for name in values:
            f = known.get(name.lower())
            if f is None:
                raise CustomFieldError(f"no field named {name!r} in {db_path}")
            used[f.field_id] = f
            names.append(f.name)
        key = tuple(names)
        by_columns.setdefault(key, []).append((code, *values.values()))
    if not by_columns:
        return 0
    with _companion(db_path) as conn:
        stored_ids = {f.field_id for f in _read_fields(conn, "main", include_orphaned=False)}
        for f in used.values():
            if f.field_id not in stored_ids:
                _store_field(conn, f)
                changed(db_path)
        for group, params in by_columns.items():
            cols = ", ".join(_q(n) for n in group)
            marks = ", ".join("?" * (len(group) + 1))
            if group:
                update = "DO UPDATE SET " + ", ".join(f"{_q(n)} = excluded.{_q(n)}" for n in group)
            else:
                update = "DO NOTHING"
            sql = (f"INSERT INTO {VALUES_TABLE} ({CODE_COLUMN}{', ' if group else ''}{cols}) "
                   f"VALUES ({marks}) ON CONFLICT({CODE_COLUMN}) {update}")
            for start in range(0, len(params), _BATCH):
                conn.executemany(sql, params[start:start + _BATCH])
            written += len(params)
    return written


def read_values(db_path: Path, codes: Optional[Iterable[str]] = None) -> dict[str, dict[str, Any]]:
    """
    The values of *codes* (all caches if None) as {gc_code: {name: value}},
    with each field's default where nothing is stored. Only caches with a
    stored row are listed when *codes* is None.
    """
    effective = fields(db_path)
    stored: dict[str, dict[str, Any]] = {}
    if companion.exists(db_path):
        conn = _open(db_path)
        try:
            if _has_table(conn, VALUES_TABLE):
                columns = _columns(conn)
                present = [f for f in effective if f.name.lower() in columns]
                select = ", ".join([CODE_COLUMN, *(_q(f.name) for f in present)])
                wanted = None if codes is None else list(dict.fromkeys(codes))
                chunks = [None] if wanted is None else [
                    wanted[i:i + _BATCH] for i in range(0, len(wanted), _BATCH)]
                for chunk in chunks:
                    sql = f"SELECT {select} FROM {VALUES_TABLE}"
                    params: list[Any] = []
                    if chunk is not None:
                        sql += f" WHERE {CODE_COLUMN} IN ({', '.join('?' * len(chunk))})"
                        params = list(chunk)
                    for row in conn.execute(sql, params):
                        stored[row[0]] = {f.name: v for f, v in zip(present, row[1:])}
        finally:
            conn.close()
    result: dict[str, dict[str, Any]] = {}
    for code in (stored if codes is None else dict.fromkeys(codes)):
        values = stored.get(code, {})
        result[code] = {
            f.name: values[f.name] if values.get(f.name) is not None else f.default_value
            for f in effective
        }
    return result


# ── Copying definitions (move/copy of caches) ─────────────────────────────────

def copy_definitions(conn: sqlite3.Connection, source_schema: str) -> list[str]:
    """
    Make sure the companion open on *conn* (the target, as "main") has the
    fields of the companion attached as *source_schema*, before rows of
    ``custom_values`` are copied (companion.copy_rows()). A field is matched
    by id, then by name; a missing one is created with its column. A field
    of the same name but another type keeps the target's definition (the
    values are copied as they are). Runs in the caller's transaction.
    Returns the names of the fields created.
    """
    if not _has_table(conn, FIELDS_TABLE, source_schema):
        return []
    source = _read_fields(conn, source_schema, include_orphaned=False)
    if not source:
        return []
    _ensure_tables(conn)
    target = _read_fields(conn, "main", include_orphaned=True)
    ids = {f.field_id for f in target}
    names = {f.name.lower(): f for f in target}
    created: list[str] = []
    for f in source:
        if f.field_id in ids:
            continue
        same = names.get(f.name.lower())
        if same is not None:
            if same.type != f.type:
                logger.warning("custom fields: field %s is %s in the source but %s in "
                               "the target — the target's definition is kept",
                               f.name, f.type, same.type)
            continue
        _insert_row(conn, f)
        created.append(f.name)
        names[f.name.lower()] = f
    columns = _columns(conn)
    for f in _read_fields(conn, "main", include_orphaned=False):
        if f.name.lower() not in columns:
            _add_column(conn, f)
    if created:
        logger.info("custom fields: created %s in the target database", ", ".join(created))
    return created


# ── The caches_all view ───────────────────────────────────────────────────────

_gen_lock = threading.Lock()
_generation: dict[str, int] = {}
_global_generation = 0


def changed(db_path: Optional[Path] = None) -> None:
    """The definitions of *db_path* (None: the global master, so every
    database) changed: views and models are rebuilt on next use."""
    global _global_generation
    with _gen_lock:
        if db_path is None:
            _global_generation += 1
        else:
            key = companion.status_key(db_path)
            _generation[key] = _generation.get(key, 0) + 1


def generation(db_path: Path, key: Optional[str] = None) -> tuple[int, int]:
    """A value that changes whenever the fields of *db_path* may have."""
    key = key or companion.status_key(db_path)
    with _gen_lock:
        return _global_generation, _generation.get(key, 0)


def view_sql(fields_: list[Field], stored_columns: Optional[set[str]]) -> str:
    """
    The CREATE statement of the caches_all TEMP view for *fields_*.
    *stored_columns* are the lower-case columns of the attached
    custom_values (None: not attached, every field shows its default).
    """
    select = ["c.*"]
    for f in fields_:
        default = _literal(f.default_value)
        if stored_columns is not None and f.name.lower() in stored_columns:
            select.append(f"COALESCE(cv.{_q(f.name)}, {default}) AS {_q(f.name)}")
        else:
            select.append(f"{default} AS {_q(f.name)}")
    source = "main.caches AS c"
    if stored_columns is not None:
        source += (f" LEFT JOIN {COMPANION_SCHEMA}.{VALUES_TABLE} AS cv "
                   f"ON cv.{CODE_COLUMN} = c.gc_code")
    return f"CREATE TEMP VIEW {VIEW} AS SELECT {', '.join(select)} FROM {source}"


def ensure_view(dbapi_connection: Any, db_path: Path, info: dict, attached: bool) -> None:
    """
    (Re)create the caches_all TEMP view on *dbapi_connection* when the
    fields changed since it was made there. *info* is the connection's
    record info (remembers the generation); *attached*: the companion is
    attached as COMPANION_SCHEMA. Never raises: without the view the
    connection works as before.
    """
    key = companion.status_key(db_path)
    stamp = (generation(db_path, key), attached)
    if info.get("caches_all") == stamp:
        return
    cursor = dbapi_connection.cursor()
    try:
        stored: list[Field] = []
        columns: Optional[set[str]] = None
        if attached and _has_table(dbapi_connection, FIELDS_TABLE, COMPANION_SCHEMA):
            stored = _read_fields(dbapi_connection, COMPANION_SCHEMA, include_orphaned=False)
            columns = (_columns(dbapi_connection, COMPANION_SCHEMA)
                       if _has_table(dbapi_connection, VALUES_TABLE, COMPANION_SCHEMA) else None)
        effective = _effective(stored, global_fields(), deleted_global_ids())
        cursor.execute(f"DROP VIEW IF EXISTS temp.{VIEW}")
        cursor.execute(view_sql(effective, columns))
        info["caches_all"] = stamp
    except sqlite3.Error as exc:
        logger.warning("custom fields: caches_all view not created for %s: %s", db_path, exc)
    finally:
        cursor.close()


def drop_view(connection: Any) -> None:
    """Drop the caches_all view of the SQLAlchemy *connection* (before a
    rebuild of ``caches``); ensure_view() makes it again on the next
    checkout."""
    connection.exec_driver_sql(f"DROP VIEW IF EXISTS temp.{VIEW}")
    connection.info.pop("caches_all", None)


# ── Display: the cache list and the detail panel ──────────────────────────────

# What SQL may do while an expression runs (read_only()).
_READ_ACTIONS = frozenset({
    sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
})


@contextmanager
def read_only(dbapi_connection: Any) -> Iterator[None]:
    """Let *dbapi_connection* (an sqlite3 connection without an authorizer
    of its own) only read while the block runs, so a field's expression
    can't change anything."""
    def authorizer(action: int, *_args: Any) -> int:
        return sqlite3.SQLITE_OK if action in _READ_ACTIONS else sqlite3.SQLITE_DENY

    dbapi_connection.set_authorizer(authorizer)
    try:
        yield
    finally:
        dbapi_connection.set_authorizer(None)


_display_sql: dict[tuple[str, str, str], Optional[str]] = {}


def display_expression(field_: Field, names: Iterable[str]) -> Optional[str]:
    """
    The SQL the cache list shows for *field_* instead of its stored value,
    or None. A GSAK expression goes through the GSAK filter translator
    (*names*: the database's field names, which it may use like columns);
    one that can't be translated is None, so the stored value is shown.
    Not checked against a database here: load_values() does that.
    """
    expr = (field_.expression or "").strip()
    if not expr or ";" in expr:
        return None
    if field_.origin != ORIGIN_GSAK:
        return expr
    names = list(names)
    key = (field_.field_id, expr, ",".join(sorted(n.lower() for n in names)))
    if key not in _display_sql:
        from opensak.importer.gsak_filter_importer import translate_where
        sql, warnings, _notes, verified = translate_where(expr, known_fields=names)
        _display_sql[key] = sql if verified else None
        if not verified:
            logger.info("custom fields: expression of %s not used for display (%s): %s",
                        field_.name, "; ".join(warnings), expr)
    return _display_sql[key]


@dataclass
class Values:
    """Custom values of many caches, from load_values()."""

    fields: tuple[Field, ...]
    # gc_code → the stored values of `fields`, then their display values
    rows: dict[str, tuple[Any, ...]]
    # lower-case names of the fields whose display value is an expression's
    expressions: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        self._index = {f.name.lower(): i for i, f in enumerate(self.fields)}

    def field(self, name: str) -> Optional[Field]:
        i = self._index.get(name.lower())
        return None if i is None else self.fields[i]

    def by_expression(self, name: str) -> bool:
        """Whether *name*'s display value comes from its expression."""
        return name.lower() in self.expressions

    def merged(self, other: "Values") -> "Values":
        """These values with *other*'s rows on top (same fields)."""
        if other.fields != self.fields:
            return other
        return Values(self.fields, {**self.rows, **other.rows}, self.expressions)

    def raw(self, code: str, name: str) -> Any:
        """The stored value (the default for an untouched cache)."""
        i = self._index.get(name.lower())
        row = self.rows.get(code)
        return None if i is None or row is None else row[i]

    def display(self, code: str, name: str) -> Any:
        """What to show: the expression's result, or the stored value."""
        i = self._index.get(name.lower())
        row = self.rows.get(code)
        return None if i is None or row is None else row[len(self.fields) + i]


EMPTY_VALUES = Values((), {})

# (database, field id, expression) whose display expression failed once.
_failed_display: set[tuple[str, str, str]] = set()


def load_values(
    dbapi_connection: Any,
    db_path: Path,
    names: Optional[Iterable[str]] = None,
    codes: Optional[Iterable[str]] = None,
) -> Values:
    """
    The custom fields *names* (all fields of *db_path* if None) of the
    caches *codes* (all if None), read from the caches_all view on
    *dbapi_connection* — a connection of the database engines
    (db/database.py), which have the view. Each with its display value
    (display_expression(); the stored value when the expression fails
    here). Fields the view lacks are left out; without the view the result
    is empty. Read-only (read_only()).
    """
    try:
        view_columns = {str(r[1]).lower() for r in
                        dbapi_connection.execute(f"PRAGMA temp.table_info({VIEW})")}
    except sqlite3.Error:
        return EMPTY_VALUES
    every = fields(db_path)
    if names is None:
        wanted = [f for f in every if f.name.lower() in view_columns]
    else:
        by_name = {f.name.lower(): f for f in every}
        wanted = [by_name[n.lower()] for n in dict.fromkeys(names)
                  if n.lower() in by_name and n.lower() in view_columns]
    if not wanted:
        return Values((), {})
    all_names = [f.name for f in every]
    key = companion.status_key(db_path)
    displays: list[Optional[str]] = []
    with read_only(dbapi_connection):
        for f in wanted:
            expr = display_expression(f, all_names)
            stamp = (key, f.field_id, expr or "")
            if expr is not None and stamp not in _failed_display:
                try:
                    dbapi_connection.execute(f"SELECT ({expr}) FROM {VIEW} LIMIT 0")
                except sqlite3.Error as exc:
                    _failed_display.add(stamp)
                    logger.warning("custom fields: display expression of %s fails, the "
                                   "stored value is shown: %s (%s)", f.name, exc, expr)
            displays.append(None if expr is None or stamp in _failed_display else expr)
        select = [CODE_COLUMN, *(_q(f.name) for f in wanted),
                  *(f"({e})" for e in displays if e is not None)]
        sql = f"SELECT {', '.join(select)} FROM {VIEW}"
        n = len(wanted)
        rows: dict[str, tuple[Any, ...]] = {}

        def add(cursor: Any) -> None:
            for row in cursor:
                raw = tuple(row[1:n + 1])
                shown = iter(row[n + 1:])
                rows[row[0]] = raw + tuple(
                    next(shown) if e is not None else raw[i] for i, e in enumerate(displays))

        if codes is None:
            add(dbapi_connection.execute(sql))
        else:
            wanted_codes = list(dict.fromkeys(codes))
            for start in range(0, len(wanted_codes), _BATCH):
                chunk = wanted_codes[start:start + _BATCH]
                add(dbapi_connection.execute(
                    f"{sql} WHERE {CODE_COLUMN} IN ({', '.join('?' * len(chunk))})", chunk))
    expressions = frozenset(f.name.lower() for f, e in zip(wanted, displays) if e is not None)
    return Values(tuple(wanted), rows, expressions)


def check_expression(dbapi_connection: Any, db_path: Path, field_: Field) -> Optional[str]:
    """
    Why *field_*'s display expression can't be shown, or None when it can
    (or there is none): it must translate (display_expression()) and run
    in a read-only SELECT on the caches_all view of *dbapi_connection* (a
    connection of the database engines). *field_* may be new or renamed;
    it is checked as if its column existed already.
    """
    expr = (field_.expression or "").strip()
    if not expr:
        return None
    if ";" in expr:
        return "the expression must be one expression, without ;"
    names = [f.name for f in fields(db_path) if f.field_id != field_.field_id]
    sql = display_expression(field_, [*names, field_.name])
    if sql is None:
        return "the GSAK expression can't be translated into OpenSAK SQL"
    try:
        columns = {str(r[1]).lower() for r in
                   dbapi_connection.execute(f"PRAGMA temp.table_info({VIEW})")}
    except sqlite3.Error as exc:
        return str(exc)
    if not columns:
        return "the custom fields are not available on this connection"
    source = VIEW
    if field_.name.lower() not in columns:
        source = f"(SELECT *, NULL AS {_q(field_.name)} FROM {VIEW})"
    try:
        with read_only(dbapi_connection):
            dbapi_connection.execute(f"SELECT ({sql}) FROM {source} LIMIT 0")
    except sqlite3.Error as exc:
        return str(exc)
    return None


def check_active_expression(field_: Field) -> Optional[str]:
    """check_expression() for the active database."""
    from opensak.db.database import get_engine, get_session
    db_path = Path(get_engine().url.database or "")
    with get_session() as session:
        dbapi = session.connection().connection.dbapi_connection
        return check_expression(dbapi, db_path, field_)


def active_values(names: Optional[Iterable[str]] = None,
                  codes: Optional[Iterable[str]] = None) -> Values:
    """load_values() for the active database, on a connection of its engine."""
    from opensak.db.database import get_engine, get_session
    db_path = Path(get_engine().url.database or "")
    with get_session() as session:
        dbapi = session.connection().connection.dbapi_connection
        return load_values(dbapi, db_path, names, codes)


def parse_value(field_: Field, value: Any) -> Any:
    """
    *value* (typed in the GUI or passed by a macro) as the value to store
    for *field_*: 0/1 for a boolean, a number, "YYYY-MM-DD" (or an ISO
    date and time) for a date, text. None, and "" for every type but text,
    mean "no value" (the default shows). Raises CustomFieldError if it
    doesn't fit the type.
    """
    if value is None or (value == "" and field_.type != TYPE_STRING):
        return None
    if field_.type == TYPE_DATE:
        from datetime import date, datetime
        if isinstance(value, datetime):
            return value.isoformat(timespec="seconds")
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, str):
            text = value.strip()
            try:
                datetime.fromisoformat(text)
            except ValueError:
                pass
            else:
                return text
        raise CustomFieldError(f'{field_.name} must be a date "YYYY-MM-DD", got {value!r}')
    if field_.type == TYPE_STRING and not isinstance(value, str):
        raise CustomFieldError(f"{field_.name} must be text, got {value!r}")
    if field_.type in (TYPE_INTEGER, TYPE_REAL) and isinstance(value, bool):
        raise CustomFieldError(f"{field_.name} must be a number, got {value!r}")
    converted, ok = convert(field_.type, value)
    if not ok:
        kind = {TYPE_BOOLEAN: "true or false", TYPE_INTEGER: "a whole number",
                TYPE_REAL: "a number"}.get(field_.type, field_.type)
        raise CustomFieldError(f"{field_.name} must be {kind}, got {value!r}")
    return converted


# ── Runtime-built SQLAlchemy model ────────────────────────────────────────────

@dataclass(frozen=True)
class ValuesModel:
    """``custom_values`` mapped for SQLAlchemy, built from the definitions."""

    table: Any          # sqlalchemy Table (schema COMPANION_SCHEMA)
    cls: type           # mapped class, one attribute per stored field
    fields: tuple[Field, ...]


_models: dict[str, tuple[tuple[int, int], Optional[ValuesModel]]] = {}


def values_model(db_path: Path) -> Optional[ValuesModel]:
    """
    The ``custom_values`` model of *db_path*: an own MetaData (create_all()
    never touches it), table in the COMPANION_SCHEMA schema, one attribute
    per stored field. Cached until the definitions change. None while the
    database stores no fields. Usable in sessions on connections that
    attach the companion (the engines of db/database.py).
    """
    key = companion.status_key(db_path)
    stamp = generation(db_path, key)
    cached = _models.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    model = _build_model(db_path)
    _models[key] = (stamp, model)
    return model


def _build_model(db_path: Path) -> Optional[ValuesModel]:
    from sqlalchemy import Boolean, Column, Float, Integer, MetaData, String, Table
    from sqlalchemy.orm import registry

    if not companion.exists(db_path):
        return None
    conn = _open(db_path)
    try:
        if not _has_table(conn, VALUES_TABLE):
            return None
        columns = _columns(conn)
        stored = [f for f in _read_fields(conn, "main", include_orphaned=False)
                  if f.name.lower() in columns]
    finally:
        conn.close()
    types: dict[str, Any] = {TYPE_STRING: String, TYPE_INTEGER: Integer, TYPE_REAL: Float,
                             TYPE_DATE: String, TYPE_BOOLEAN: Boolean}
    metadata = MetaData()
    table = Table(
        VALUES_TABLE, metadata,
        Column(CODE_COLUMN, String, primary_key=True),
        *(Column(f.name, types[f.type], key=f.name) for f in stored),
        schema=COMPANION_SCHEMA,
    )
    cls = type("CustomValues", (), {"__doc__": f"custom_values of {Path(db_path).name}"})
    registry().map_imperatively(cls, table)
    return ValuesModel(table=table, cls=cls, fields=tuple(stored))


# ── Helpers ───────────────────────────────────────────────────────────────────

class _companion:
    """The companion of *db_path* (created if missing) on its own
    connection, with the custom-field tables, in one transaction."""

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> sqlite3.Connection:
        companion.create(self._db_path)
        self._conn = _open(self._db_path)
        self._conn.execute("BEGIN IMMEDIATE")
        _ensure_tables(self._conn)
        return self._conn

    def __exit__(self, exc_type: Any, *_exc: object) -> None:
        assert self._conn is not None
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(companion.companion_path(db_path)), timeout=30,
                           isolation_level=None)
    return conn


def _ensure_tables(conn: sqlite3.Connection, schema: str = "main") -> None:
    conn.execute(_FIELDS_DDL.format(schema=_q(schema)))
    _ensure_values_table(conn, schema)
    s = _q(schema)
    for name, kind, code_column, description in (
        (FIELDS_TABLE, companion.KIND_FREE, None, "Custom field definitions"),
        (VALUES_TABLE, companion.KIND_CACHE, CODE_COLUMN, "Custom field values"),
    ):
        conn.execute(
            f"INSERT OR IGNORE INTO {s}.{companion.REGISTRY_TABLE} (name, owner, owner_name, "
            "kind, code_column, description, created_at, created_by_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, companion.OWNER_OPENSAK, "OpenSAK", kind, code_column, description,
             companion._now(), companion._version()),
        )


def _ensure_values_table(conn: sqlite3.Connection, schema: str = "main") -> None:
    conn.execute(_VALUES_DDL.format(schema=_q(schema)))


def _has_table(conn: Any, name: str, schema: str = "main") -> bool:
    try:
        return conn.execute(
            f"SELECT 1 FROM {_q(schema)}.sqlite_master WHERE type = 'table' "
            "AND name = ? COLLATE NOCASE", (name,)
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _columns(conn: Any, schema: str = "main") -> set[str]:
    return {str(row[1]).lower()
            for row in conn.execute(f"PRAGMA {_q(schema)}.table_info({VALUES_TABLE})")}


def _read_fields(conn: Any, schema: str, include_orphaned: bool) -> list[Field]:
    if not _has_table(conn, FIELDS_TABLE, schema):
        return []
    sql = f"SELECT {', '.join(_FIELD_COLUMNS)} FROM {_q(schema)}.{FIELDS_TABLE}"
    if not include_orphaned:
        sql += " WHERE orphaned = 0"
    sql += " ORDER BY position, name"
    return [
        Field(field_id=r[0], name=r[1], type=r[2], position=r[3] or 0, default=r[4],
              expression=r[5], control=r[6], height=r[7], scope=r[8], origin=r[9],
              orphaned=bool(r[10]))
        for r in conn.execute(sql)
    ]


def _stored(conn: sqlite3.Connection, field_id: str) -> Optional[Field]:
    return next((f for f in _read_fields(conn, "main", include_orphaned=True)
                 if f.field_id == field_id), None)


def _field(db_path: Path, field_id: str) -> Field:
    """A field of *db_path* (stored, orphaned or only global) by id."""
    for f in [*stored_fields(db_path, include_orphaned=True), *global_fields()]:
        if f.field_id == field_id:
            return f
    raise CustomFieldError(f"no field with id {field_id}")


def _store_field(conn: sqlite3.Connection, f: Field) -> None:
    existing = _stored(conn, f.field_id)
    if existing is None:
        _insert_row(conn, f)
    else:
        if existing.name != f.name:
            _rename(conn, existing.name, f.name, f.field_id)
        _update_row(conn, replace(f, orphaned=False))
        conn.execute(f"UPDATE {FIELDS_TABLE} SET orphaned = 0 WHERE field_id = ?", (f.field_id,))
    if f.name.lower() not in _columns(conn):
        _add_column(conn, f)


def _insert_row(conn: sqlite3.Connection, f: Field) -> None:
    conn.execute(
        f"INSERT INTO {FIELDS_TABLE} ({', '.join(_FIELD_COLUMNS)}) "
        f"VALUES ({', '.join('?' * len(_FIELD_COLUMNS))})",
        (f.field_id, f.name, f.type, f.position, f.default, f.expression, f.control,
         f.height, f.scope, f.origin, int(f.orphaned)),
    )


def _update_row(conn: sqlite3.Connection, f: Field) -> None:
    conn.execute(
        f"UPDATE {FIELDS_TABLE} SET position = ?, default_value = ?, expression = ?, "
        "control = ?, height = ? WHERE field_id = ?",
        (f.position, f.default, f.expression, f.control, f.height, f.field_id),
    )


def _add_column(conn: sqlite3.Connection, f: Field) -> None:
    conn.execute(f"ALTER TABLE {VALUES_TABLE} ADD COLUMN {_q(f.name)} {sql_type(f.type)}")


def _rename(conn: sqlite3.Connection, old: str, new: str, field_id: str) -> None:
    if old.lower() in _columns(conn):
        if old.lower() == new.lower():
            # Only the case changes: SQLite needs a detour.
            temp = f"{old}__renaming"
            conn.execute(f"ALTER TABLE {VALUES_TABLE} RENAME COLUMN {_q(old)} TO {_q(temp)}")
            old = temp
        conn.execute(f"ALTER TABLE {VALUES_TABLE} RENAME COLUMN {_q(old)} TO {_q(new)}")
    conn.execute(f"UPDATE {FIELDS_TABLE} SET name = ? WHERE field_id = ?", (new, field_id))


def _convert_column(conn: sqlite3.Connection, name: str, new_type: str) -> None:
    if name.lower() not in _columns(conn):
        return
    temp = f"{name}__converting"
    conn.execute(f"ALTER TABLE {VALUES_TABLE} ADD COLUMN {_q(temp)} {sql_type(new_type)}")
    rows = conn.execute(f"SELECT {CODE_COLUMN}, {_q(name)} FROM {VALUES_TABLE}").fetchall()
    converted = []
    for code, value in rows:
        new, ok = convert(new_type, value)
        converted.append((new if ok else None, code))
    conn.executemany(f"UPDATE {VALUES_TABLE} SET {_q(temp)} = ? WHERE {CODE_COLUMN} = ?",
                     converted)
    conn.execute(f"ALTER TABLE {VALUES_TABLE} DROP COLUMN {_q(name)}")
    conn.execute(f"ALTER TABLE {VALUES_TABLE} RENAME COLUMN {_q(temp)} TO {_q(name)}")


def _next_position(existing: list[Field]) -> int:
    return max((f.position for f in existing), default=0) + 10


def _literal(value: Any) -> str:
    """*value* as an SQL literal (for the view, which can't take parameters)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


__all__ = [
    "ApplyResult", "CompanionError", "CustomFieldError", "EMPTY_VALUES", "Field",
    "PendingChange", "Values", "ValuesModel", "active_values", "apply_definitions",
    "change_type", "check_active_expression", "check_expression", "parse_value",
    "changed", "check_name", "convert", "copy_definitions", "define_field",
    "define_global", "delete_field", "display_expression", "drop_field",
    "ensure_view", "fields", "global_fields", "load_values", "normalize_type",
    "read_only", "read_values", "rename_field", "rename_for_import",
    "stored_fields", "update_definition", "values_model", "view_sql", "write_values",
]

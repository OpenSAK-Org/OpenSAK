"""
src/opensak/importer/gsak_custom_fields.py — GSAK custom fields (#1028 step 3).

GSAK keeps custom fields in three places:

* ``Custom`` (in the database): one row per cache (``cCode``) and one real
  column per field — local *and* global fields;
* ``CustomLocal`` (in the database): the definitions of the local fields;
* ``CustomGlobal`` / ``CustomDefs`` (in ``gsak.db3``, GSAK's settings
  database): the definitions of the global fields.

They become OpenSAK custom fields (db/custom_fields.py) in the target
database's companion: local fields as local fields, global ones as
OpenSAK global fields. Without ``gsak.db3`` (a single database imported on
its own) the global fields are imported as local fields, defined from
their column, and the result says so. Names that clash with a cache column
or an SQL keyword get ``_custom`` appended.

A field that exists already (same name) is reused; when its type differs,
the existing definition is kept and the values are converted where they
can be. Values that can't be converted are stored as they are, as GSAK
would. ``fexpression`` is kept as the field's expression, not evaluated.

Runs after the caches were imported and committed (main database first,
companion second); only caches that are in the target database get values.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from opensak.db import custom_fields
from opensak.db.custom_fields import (
    ORIGIN_GSAK, SCOPE_GLOBAL, SCOPE_LOCAL, TYPE_STRING, Field,
)

logger = logging.getLogger(__name__)

# GSAK's settings database, with the global definitions.
GSAK_SETTINGS = "gsak.db3"
_CHUNK = 500

# SQLite column affinity → field type, for fields without a definition.
_AFFINITY_TYPES = {"INTEGER": "integer", "INT": "integer", "REAL": "real",
                   "FLOAT": "real", "DOUBLE": "real", "TEXT": "string"}


@dataclass
class CustomFieldImportResult:
    """What the import of a GSAK database's custom fields did."""

    created: list[str] = field(default_factory=list)       # fields defined now
    reused: list[str] = field(default_factory=list)        # fields that existed
    global_fields: list[str] = field(default_factory=list) # imported as global
    renamed: list[tuple[str, str]] = field(default_factory=list)   # GSAK → OpenSAK
    values: int = 0                                        # caches with values written
    unconverted: int = 0                                   # values stored as they were
    messages: list[str] = field(default_factory=list)

    @property
    def fields(self) -> int:
        return len(self.created) + len(self.reused)


@dataclass(frozen=True)
class _GsakDefinition:
    name: str
    type: str
    position: int
    default: Optional[str]
    control: Optional[str]
    height: Optional[int]
    expression: Optional[str]
    scope: str


def import_custom_fields(
    gsak_conn: sqlite3.Connection,
    target_db: Path,
    codes: Iterable[str],
    settings_db: Optional[Path] = None,
) -> CustomFieldImportResult:
    """
    Import the custom fields of the GSAK database open on *gsak_conn* into
    the OpenSAK database at *target_db*, with the values of the caches
    *codes*. *settings_db* is GSAK's ``gsak.db3`` (global definitions), if
    available. Raises sqlite3.Error, CompanionError or CustomFieldError when
    the target can't be written; the caller reports it.
    """
    result = CustomFieldImportResult()
    if not _has_table(gsak_conn, "Custom"):
        return result
    columns = {
        str(row[1]): (str(row[2] or "").upper(), row[4])
        for row in gsak_conn.execute("PRAGMA table_info(Custom)")
        if str(row[1]).lower() != "ccode"
    }
    local = _definitions(gsak_conn, "CustomLocal", SCOPE_LOCAL)
    global_ = _global_definitions(settings_db, result)
    definitions = _resolve(columns, local, global_, settings_db is not None, result)
    if not definitions:
        return result

    target_fields = _target_fields(target_db, definitions, result)
    present = [name for name in columns if name.lower() in target_fields]
    if not present:
        return result
    wanted = _cache_codes(target_db, codes)
    if not wanted:
        return result

    select = ", ".join(['"cCode"', *(_q(name) for name in present)])
    rows: list[tuple[str, dict[str, Any]]] = []
    for start in range(0, len(wanted), _CHUNK):
        chunk = wanted[start:start + _CHUNK]
        for row in gsak_conn.execute(
            f"SELECT {select} FROM Custom WHERE cCode IN ({', '.join('?' * len(chunk))})", chunk
        ):
            values: dict[str, Any] = {}
            for name, raw in zip(present, row[1:]):
                target = target_fields[name.lower()]
                value, ok = custom_fields.convert(target.type, raw)
                if not ok:
                    result.unconverted += 1
                values[target.name] = value
            rows.append((row[0], values))
    result.values = custom_fields.write_values(target_db, rows)
    if result.unconverted:
        result.messages.append(
            f"{result.unconverted} custom field value(s) didn't fit the field's type "
            "and were stored as they were")
    return result


def _resolve(
    columns: dict[str, tuple[str, Any]],
    local: dict[str, _GsakDefinition],
    global_: dict[str, _GsakDefinition],
    have_settings: bool,
    result: CustomFieldImportResult,
) -> list[_GsakDefinition]:
    """One definition per column of Custom, plus local definitions without
    a column (GSAK has them, so they are kept)."""
    resolved: list[_GsakDefinition] = []
    from_column: list[str] = []
    for name, (affinity, default) in columns.items():
        found = local.get(name.lower()) or global_.get(name.lower())
        if found is None:
            from_column.append(name)
            found = _GsakDefinition(
                name=name, type=_AFFINITY_TYPES.get(affinity, TYPE_STRING),
                position=0, default=_unquote(default), control=None, height=None,
                expression=None, scope=SCOPE_LOCAL,
            )
        resolved.append(_with_name(found, name))
    known = {name.lower() for name in columns}
    resolved.extend(d for key, d in local.items() if key not in known)
    if from_column:
        if have_settings:
            result.messages.append(
                "no GSAK definition for custom field(s) " + ", ".join(from_column)
                + ": defined as local fields from their column")
        else:
            result.messages.append(
                f"{GSAK_SETTINGS} not available: custom field(s) " + ", ".join(from_column)
                + " imported as local fields (in GSAK they are probably global)")
    return resolved


def _with_name(definition: _GsakDefinition, name: str) -> _GsakDefinition:
    """*definition* with the column's spelling of its name."""
    if definition.name == name:
        return definition
    return _GsakDefinition(name, definition.type, definition.position, definition.default,
                           definition.control, definition.height, definition.expression,
                           definition.scope)


def _target_fields(
    target_db: Path, definitions: list[_GsakDefinition], result: CustomFieldImportResult,
) -> dict[str, Field]:
    """Define what's missing in the target; returns {GSAK name (lower): field}."""
    mapped: dict[str, Field] = {}
    for d in definitions:
        existing = {f.name.lower(): f for f in custom_fields.fields(target_db)}
        if d.scope == SCOPE_GLOBAL:
            existing.update({f.name.lower(): f for f in custom_fields.global_fields()})
        name = custom_fields.rename_for_import(d.name)
        if name != d.name:
            result.renamed.append((d.name, name))
            result.messages.append(f"custom field {d.name} renamed to {name} "
                                   "(the name is used by OpenSAK)")
        current = existing.get(name.lower())
        if current is not None:
            if current.type != d.type:
                result.messages.append(
                    f"custom field {name} is {current.type} in OpenSAK but {d.type} in "
                    f"GSAK: kept as {current.type}, values converted where possible")
            mapped[d.name.lower()] = current
            result.reused.append(current.name)
            continue
        taken = set(existing)
        if d.scope == SCOPE_LOCAL:
            taken.update(f.name.lower() for f in custom_fields.global_fields())
        if name.lower() in taken:
            name = custom_fields.rename_for_import(name, taken)
            result.renamed.append((d.name, name))
            result.messages.append(f"custom field {d.name} renamed to {name} "
                                   "(a global field has that name)")
        created = custom_fields.define_field(
            target_db, name, d.type, default=d.default, position=d.position,
            expression=d.expression, control=d.control, height=d.height,
            scope=d.scope, origin=ORIGIN_GSAK,
        )
        mapped[d.name.lower()] = created
        result.created.append(created.name)
        if d.scope == SCOPE_GLOBAL:
            result.global_fields.append(created.name)
    return mapped



def _definitions(conn: sqlite3.Connection, table: str, scope: str) -> dict[str, _GsakDefinition]:
    if not _has_table(conn, table):
        return {}
    found: dict[str, _GsakDefinition] = {}
    for fname, ftype, fsequence, fdefault, fcontrol, fheight, fexpression in conn.execute(
        f"SELECT fname, ftype, fsequence, fdefault, fcontrol, fheight, fexpression FROM {table}"
    ):
        if not fname:
            continue
        type_ = custom_fields.normalize_type(ftype)
        if type_ is None:
            logger.warning("GSAK custom field %s has the unknown type %r: imported as string",
                           fname, ftype)
            type_ = TYPE_STRING
        found[str(fname).lower()] = _GsakDefinition(
            name=str(fname), type=type_, position=_int(fsequence) or 0,
            default=None if fdefault is None else str(fdefault),
            control=fcontrol or None, height=_int(fheight),
            expression=(str(fexpression).strip() or None) if fexpression else None,
            scope=scope,
        )
    return found


def _global_definitions(settings_db: Optional[Path],
                        result: CustomFieldImportResult) -> dict[str, _GsakDefinition]:
    """CustomGlobal from gsak.db3. CustomDefs only says which fields are
    global; every field in CustomGlobal is."""
    if settings_db is None:
        return {}
    try:
        conn = sqlite3.connect(f"{Path(settings_db).resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        result.messages.append(f"{GSAK_SETTINGS} could not be read ({exc}): "
                               "global custom fields imported as local fields")
        return {}
    try:
        return _definitions(conn, "CustomGlobal", SCOPE_GLOBAL)
    except sqlite3.Error as exc:
        result.messages.append(f"{GSAK_SETTINGS} could not be read ({exc}): "
                               "global custom fields imported as local fields")
        return {}
    finally:
        conn.close()


def _cache_codes(target_db: Path, codes: Iterable[str]) -> list[str]:
    """Those of *codes* that are caches in the target database."""
    wanted = list(dict.fromkeys(c for c in codes if c))
    conn = sqlite3.connect(str(target_db), timeout=30)
    try:
        present: list[str] = []
        for start in range(0, len(wanted), _CHUNK):
            chunk = wanted[start:start + _CHUNK]
            present.extend(r[0] for r in conn.execute(
                f"SELECT gc_code FROM caches WHERE gc_code IN ({', '.join('?' * len(chunk))})",
                chunk))
        return present
    finally:
        conn.close()


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? COLLATE NOCASE", (name,)
    ).fetchone() is not None


def _unquote(default: Any) -> Optional[str]:
    """A column default from PRAGMA table_info ('' or 0 or -1) as text."""
    if default is None:
        return None
    text = str(default).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'

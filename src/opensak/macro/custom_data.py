"""
src/opensak/macro/custom_data.py — custom fields (#1028 step 5) for Lua macros.

* **Read:** the cache records (cache_data.py) have a `custom` table, field
  name → value: a boolean as true/false, numbers as numbers, text and dates
  ("YYYY-MM-DD") as strings — the stored value, or the field's default for
  a cache nobody changed (as GSAK's Custom row has).
* **Write:** opensak.update(code, { custom = { Angel = true } }), with the
  user's write approval like every change of a cache. `false` clears a
  field that is not a boolean (it shows its default again).
* **Define:** opensak.define_custom_field{}, opensak.custom_fields(),
  opensak.delete_custom_field().

The values live in the companion database (db/custom_fields.py), written on
its own connection; inside opensak.transaction() the runtime keeps them
until the outermost transaction commits.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional

from opensak.db import custom_fields
from opensak.db.custom_fields import CustomFieldError, Field
from opensak.macro.cache_write import CacheWriteError

# The keys opensak.define_custom_field{} takes.
DEFINE_KEYS = ("name", "type", "default", "scope", "expression")


def lua_value(field: Field, value: Any) -> Any:
    """A stored value (or default) of *field* as the macro sees it."""
    if value is None:
        return None
    if field.type == custom_fields.TYPE_BOOLEAN:
        return value not in (0, "0", "", False)
    if field.type == custom_fields.TYPE_INTEGER:
        converted, ok = custom_fields.convert(field.type, value)
        return converted if ok else value
    if field.type == custom_fields.TYPE_REAL:
        converted, ok = custom_fields.convert(field.type, value)
        return converted if ok else value
    return str(value)


def record_values(db_path: Optional[Path], codes: Iterable[str]) -> dict[str, dict[str, Any]]:
    """{code: {field name: value}} for the records of *codes* (every field
    of the database, defaults included); {} without custom fields."""
    codes = list(codes)
    if db_path is None or not codes:
        return {}
    fields = {f.name: f for f in custom_fields.fields(db_path)}
    if not fields:
        return {code: {} for code in codes}
    raw = custom_fields.read_values(db_path, codes)
    return {
        code: {name: lua_value(fields[name], v) for name, v in values.items() if name in fields}
        for code, values in raw.items()
    }


def parse_custom(func: str, db_path: Path, values: Any) -> dict[str, Any]:
    """The `custom` table of opensak.update() → {field name: value to
    store}. Raises CacheWriteError for an unknown field or a value that
    doesn't fit its type."""
    if not isinstance(values, dict) or not values:
        raise CacheWriteError(f"{func}: custom must be a table of fields, e.g. "
                              "{ custom = { Angel = true } }")
    by_name = {f.name.lower(): f for f in custom_fields.fields(db_path)}
    parsed: dict[str, Any] = {}
    for key, value in values.items():
        field = by_name.get(key.lower()) if isinstance(key, str) else None
        if field is None:
            known = ", ".join(f.name for f in by_name.values()) or "none"
            raise CacheWriteError(f"{func}: no custom field {key!r} in the active database "
                                  f"(fields: {known}); see opensak.define_custom_field()")
        if value is False and field.type != custom_fields.TYPE_BOOLEAN:
            value = None
        try:
            parsed[field.name] = custom_fields.parse_value(field, value)
        except CustomFieldError as exc:
            raise CacheWriteError(f"{func}: {exc}") from None
    return parsed


def definition(field: Field) -> dict[str, Any]:
    """One field for opensak.custom_fields()."""
    return {
        "name": field.name,
        "type": field.type,
        "default": lua_value(field, field.default_value),
        "scope": field.scope,
        "origin": field.origin,
        "position": field.position,
        "expression": field.expression,
    }


def define(func: str, db_path: Path, spec: dict[str, Any]) -> tuple[Field, bool]:
    """opensak.define_custom_field{}: (the field, whether it was created).
    A field of that name and type already there is returned as it is, so a
    macro can define its fields on every run. Raises CacheWriteError."""
    unknown = set(spec) - set(DEFINE_KEYS)
    if unknown:
        raise CacheWriteError(f"{func}: unknown key(s) {sorted(unknown)}; "
                              f"valid: {', '.join(DEFINE_KEYS)}")
    name, type_ = spec.get("name"), spec.get("type")
    if not isinstance(name, str) or not name.strip():
        raise CacheWriteError(f"{func}: `name` must be the field's name")
    normalized = custom_fields.normalize_type(type_)
    if normalized is None:
        raise CacheWriteError(f"{func}: `type` must be one of {', '.join(custom_fields.TYPES)}, "
                              f"got {type_!r}")
    scope = spec.get("scope", custom_fields.SCOPE_LOCAL)
    if scope not in (custom_fields.SCOPE_LOCAL, custom_fields.SCOPE_GLOBAL):
        raise CacheWriteError(f'{func}: `scope` must be "local" or "global", got {scope!r}')
    name = name.strip()
    existing = next((f for f in custom_fields.fields(db_path)
                     if f.name.lower() == name.lower()), None)
    if existing is not None:
        if existing.type != normalized:
            raise CacheWriteError(f"{func}: the field {existing.name} exists already as "
                                  f"{existing.type}, not {normalized}")
        return existing, False
    probe = Field(field_id="", name=name, type=normalized)
    default = spec.get("default")
    expression = spec.get("expression")
    if expression is not None and not isinstance(expression, str):
        raise CacheWriteError(f"{func}: `expression` must be SQL text")
    expression = (expression or "").strip() or None
    try:
        stored = custom_fields.parse_value(probe, default)
        text = None if stored is None else str(stored)
        if expression is not None:
            error = custom_fields.check_active_expression(
                Field(field_id="", name=name, type=normalized, default=text,
                      expression=expression))
            if error is not None:
                raise CacheWriteError(f"{func}: the display expression doesn't work: {error}")
        field = custom_fields.define_field(db_path, name, normalized, default=text, scope=scope,
                                           expression=expression)
    except (CustomFieldError, custom_fields.CompanionError) as exc:
        raise CacheWriteError(f"{func}: {exc}") from None
    return field, True

"""
src/opensak/gui/custom_field_format.py — how custom field values (#1028) are
shown and sorted in the cache list and the detail panel.

Values come from db/custom_fields.load_values(): the stored value (the
field's default for an untouched cache) and the display value, which is the
result of the field's expression where it has one. A display value from an
expression is shown as it is; otherwise the value is formatted by the
field's type.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from opensak.db.custom_fields import TYPE_BOOLEAN, TYPE_DATE, TYPE_INTEGER, TYPE_REAL, Field
from opensak.gui.date_format import format_date
from opensak.gui.settings import get_settings
from opensak.lang import tr

# Types shown centred, like the built-in number, date and flag columns.
CENTERED_TYPES = frozenset({TYPE_BOOLEAN, TYPE_INTEGER, TYPE_REAL, TYPE_DATE})

# The translation key of each field type (literals, for test_no_unused_keys);
# "Text" and "Date" are the words used elsewhere already.
_TYPE_KEYS = {
    "string":  "container_display_text",
    "integer": "custom_type_integer",
    "real":    "custom_type_real",
    "date":    "filter_wp_date",
    "boolean": "custom_type_boolean",
}


def type_label(type_: str) -> str:
    """A field type as the user reads it."""
    return tr(_TYPE_KEYS.get(type_, "container_display_text"))


def _number(value: Any) -> str:
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return str(int(value))
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def _date(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        try:
            return format_date(datetime.fromisoformat(value.strip()), get_settings().date_format)
        except ValueError:
            return value
    return "" if value is None else str(value)


def format_value(field: Field, value: Any, by_expression: bool, grid: bool = True) -> str:
    """*value* of *field* as text. *by_expression*: *value* is the result
    of the field's expression (shown as it is). In the grid a true boolean
    is a tick and a false one empty, like the other flag columns; elsewhere
    (*grid* False) it reads Yes / No."""
    if value is None:
        return ""
    if by_expression:
        return _number(value) if isinstance(value, float) else str(value)
    if field.type == TYPE_BOOLEAN:
        truthy = value not in (0, "0", "", False)
        if grid:
            return "✓" if truthy else ""
        return tr("yes") if truthy else tr("no")
    if field.type in (TYPE_INTEGER, TYPE_REAL):
        return _number(value)
    if field.type == TYPE_DATE:
        return _date(value)
    return str(value).replace("\n", " ") if grid else str(value)


def sort_key(field: Field, value: Any) -> tuple:
    """Sort key for a stored value of *field*; missing values first."""
    if value is None:
        return (0, 0.0, "")
    if field.type in (TYPE_BOOLEAN, TYPE_INTEGER, TYPE_REAL):
        try:
            return (1, float(value), "")
        except (TypeError, ValueError):
            return (2, 0.0, str(value).lower())
    return (1, 0.0, str(value).lower())

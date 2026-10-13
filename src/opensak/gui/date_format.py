"""
src/opensak/gui/date_format.py — the user's date format, in one place.

Issue #1043: the date format chosen in Settings (#295) was only applied to the
cache grid and the detail panel. The date pickers (filter dialog, Mark as
Found, waypoint dialog) used Qt's default or a hardcoded pattern, so the same
date could look different in two places. Everything that shows a date now
takes its pattern from here.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from PySide6.QtCore import QDate, QLocale
from PySide6.QtWidgets import QDateTimeEdit

from opensak.utils.types import DateFormat, norm_locale_date_fmt

_FIXED_PATTERNS = {
    DateFormat.DMY: "dd.MM.yyyy",
    DateFormat.MDY: "MM/dd/yyyy",
    DateFormat.YMD: "yyyy-MM-dd",
}


def qt_date_pattern(fmt: DateFormat) -> str:
    """
    The Qt display pattern for *fmt*. LOCALE uses the OS short date format,
    zero-padded with a 4-digit year — the same as the cache grid.
    """
    pattern = _FIXED_PATTERNS.get(DateFormat(fmt))
    if pattern is not None:
        return pattern
    return norm_locale_date_fmt(
        QLocale.system().dateFormat(QLocale.FormatType.ShortFormat)
    )


def format_date(d: date, fmt: DateFormat) -> str:
    """*d* (a date or datetime; only the date is shown) in the format *fmt*."""
    qd = QDate(d.year, d.month, d.day)
    return QLocale.system().toString(qd, qt_date_pattern(fmt))


def current_date_pattern() -> str:
    """The Qt pattern for the date format currently chosen in Settings."""
    from opensak.gui.settings import get_settings
    return qt_date_pattern(get_settings().date_format)


def apply_date_format(edit: QDateTimeEdit, pattern: Optional[str] = None) -> None:
    """
    Show *edit* (a QDateEdit or QDateTimeEdit) in the Settings date format,
    or in *pattern* if given. Display only — the value is unaffected.
    """
    edit.setDisplayFormat(pattern if pattern is not None else current_date_pattern())


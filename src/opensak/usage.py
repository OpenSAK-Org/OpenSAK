"""
src/opensak/usage.py — "Using OpenSAK since ..." for Help → About (issue #1050).

GSAK shows how long you have been using it on its splash and About screens;
this is OpenSAK's version of that small bit of fun. The date OpenSAK was
first started is stored once in opensak.json under ``usage.first_started``
(ISO date) and never changes after that.

Existing users get credit for the time before this feature existed: the
first time the date is recorded, it is backdated to the oldest cache import
found in any registered database, if that is earlier than today.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

from opensak.logger import get_logger

_log = get_logger("usage")

FIRST_STARTED_KEY = "usage.first_started"


def first_started() -> Optional[date]:
    """The stored first-start date, or None if not recorded (or unreadable)."""
    from opensak.settings_store import get_store

    return _parse_date(get_store().get(FIRST_STARTED_KEY))


def ensure_first_started(
    db_paths: Iterable[Path], today: Optional[date] = None
) -> date:
    """
    Record the first-start date if it isn't stored yet, and return it.

    Called once at every startup; after the first call it only reads the
    stored value. On that first call the date is backdated to the oldest
    cache import in *db_paths* (see _earliest_import), so someone who has
    used OpenSAK for months doesn't start again at day 1.
    """
    from opensak.settings_store import get_store

    today = today or date.today()
    stored = first_started()
    if stored is not None:
        return stored

    candidates = [today]
    for path in db_paths:
        d = _earliest_import(Path(path))
        if d is not None and d <= today:
            candidates.append(d)
    first = min(candidates)
    get_store().set(FIRST_STARTED_KEY, first.isoformat())
    _log.info("usage: first start recorded as %s", first.isoformat())
    return first


def day_number(first: date, today: Optional[date] = None) -> int:
    """Which day of use *today* is — the first day is day 1."""
    today = today or date.today()
    return max(1, (today - first).days + 1)


def _earliest_import(db_path: Path) -> Optional[date]:
    """
    The import date of the oldest cache in one database, or None.

    Reads the row with the lowest id rather than MIN(imported_at): ids are
    handed out in import order and imported_at is set once when a cache is
    first created, so this is the same answer in practice — and it is one
    primary-key lookup instead of a full scan, which matters on
    multi-gigabyte databases. Any problem (missing file, locked, no caches
    table, odd value) just means "no answer" for this database.
    """
    if not db_path.is_file():
        return None
    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=1)
        try:
            row = con.execute(
                "SELECT imported_at FROM caches ORDER BY id LIMIT 1"
            ).fetchone()
        finally:
            con.close()
    except (sqlite3.Error, OSError, ValueError):
        _log.debug("usage: could not read %s", db_path, exc_info=True)
        return None
    return _parse_date(row[0]) if row else None


def _parse_date(value: object) -> Optional[date]:
    """'YYYY-MM-DD…' (an ISO date or SQLite datetime) as a date, else None."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None

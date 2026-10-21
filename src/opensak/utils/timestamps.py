"""
src/opensak/utils/timestamps.py — UTC/local conversion for recorded timestamps
(issue #1097).

Timestamps OpenSAK records itself (``caches.imported_at``,
``caches.last_gpx_update``, ``caches.last_user_update``,
``user_notes.updated_at``) are stored as naive UTC, so a database keeps the
same meaning when the computer changes time zone or the database is opened
on another machine. They are converted to local time only when shown or
compared with a date the user typed.

Listing-side dates (placed date, the listing's "changed" date, found date,
log dates) are calendar dates, not timestamps, and are never converted.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional, overload


def utc_now() -> datetime:
    """Current time as naive UTC, the form recorded timestamps are stored in."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@overload
def utc_to_local(value: datetime) -> datetime: ...
@overload
def utc_to_local(value: None) -> None: ...
def utc_to_local(value: Optional[datetime]) -> Optional[datetime]:
    """A stored timestamp as naive local time. Naive input is taken as UTC.

    Values that can't be converted (e.g. ``datetime.min``) are returned
    unchanged rather than raising."""
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    try:
        return aware.astimezone().replace(tzinfo=None)
    except (OverflowError, ValueError, OSError):
        return value.replace(tzinfo=None)


@overload
def local_to_utc(value: datetime) -> datetime: ...
@overload
def local_to_utc(value: None) -> None: ...
def local_to_utc(value: Optional[datetime]) -> Optional[datetime]:
    """A local time as naive UTC, for storing or for comparing with stored
    timestamps. Naive input is taken as local time on this computer.

    Values that can't be converted (e.g. ``datetime.min``) are returned
    unchanged rather than raising."""
    if value is None:
        return None
    try:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    except (OverflowError, ValueError, OSError):
        return value.replace(tzinfo=None)


def local_date(value: Optional[datetime]) -> Optional[date]:
    """Calendar date of a stored UTC timestamp in local time."""
    local = utc_to_local(value)
    return local.date() if local is not None else None

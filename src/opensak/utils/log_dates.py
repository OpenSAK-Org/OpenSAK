"""
src/opensak/utils/log_dates.py — the calendar date a geocaching log was
written for (issue #875).

A log on geocaching.com has a date the logger picked, but GPX files (and the
API) carry it as a UTC timestamp. Taking the UTC calendar date puts an
evening find in the Americas on the next day. How the timestamp relates to
the chosen date has changed over the years (from lignumaqua, GSAK forum
topic 37364):

* before 2008, every log was stamped 07:00 Pacific;
* 2008 – May 2017, every log was stamped 19:00 Pacific;
* since May 2017, app logs carry the real time of logging, while website
  logs still get a fixed time;
* since Sep 2026, found logs of users who opted out of data sharing are
  always 12:00 UTC.

The fixed times are not always written the same way: PQs and databases
also show 19:00 and 20:00 UTC (noon Pacific) and midnight for files that
only carry a date. So:

* A timestamp on a whole hour (minutes, seconds and microseconds all zero)
  is taken as a fixed time, not a real one, and its calendar date is used.
  The one fixed time whose UTC date is not the chosen date is 19:00
  Pacific (02:00 or 03:00 UTC the next day); that one is read in Pacific
  time. A real app log on the exact second of a whole hour is rare, and
  even then its date only comes out wrong close to midnight.
* Any other timestamp is a real time. For the user's own logs it is
  converted to the local time zone of this computer, the best guess for
  where they were when they logged. For other finders' logs there is no
  better guess than UTC.

Like GSAK, this is an educated guess, not a certainty: a log written while
travelling, or days after the find, can still come out a day off. The
stored log rows keep the original timestamp; the guess is made when
found_date / dnf_date are derived and when log dates are shown.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Optional


def _nth_sunday(year: int, month: int, n: int) -> date:
    """The *n*-th Sunday of *month* (n = -1 for the last one)."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))
    next_month = date(year + month // 12, month % 12 + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def _pacific(value: datetime) -> datetime:
    """An aware UTC *value* as naive US Pacific wall time, DST included.

    The US rules are written out here rather than taken from zoneinfo,
    which needs the tzdata package on Windows."""
    year = value.year
    if year >= 2007:
        start, end = _nth_sunday(year, 3, 2), _nth_sunday(year, 11, 1)
    else:
        start, end = _nth_sunday(year, 4, 1), _nth_sunday(year, 10, -1)
    # DST runs from 02:00 PST (10:00 UTC) to 02:00 PDT (09:00 UTC).
    dst_start = datetime.combine(start, time(10), timezone.utc)
    dst_end = datetime.combine(end, time(9), timezone.utc)
    offset = -7 if dst_start <= value < dst_end else -8
    return (value + timedelta(hours=offset)).replace(tzinfo=None)


def _as_aware_utc(value: datetime) -> datetime:
    """Stored log dates are UTC; SQLite gives them back naive."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_fixed_time(value: datetime) -> bool:
    """True when *value* is on a whole hour, i.e. one of the fixed times
    geocaching.com gives logs that carry no real time of logging."""
    return value.minute == 0 and value.second == 0 and value.microsecond == 0


def log_calendar_date(value: Optional[datetime], *, own: bool,
                      local_tz: Optional[tzinfo] = None) -> Optional[date]:
    """The date a log with timestamp *value* was most likely written for.

    *own* is True for the current user's own logs, which are read in this
    computer's local time zone (*local_tz*, for tests). Naive *value* is
    taken as UTC."""
    if value is None:
        return None
    utc = _as_aware_utc(value)
    if is_fixed_time(utc):
        pacific = _pacific(utc)
        if pacific.hour == 19:
            return pacific.date()
        return utc.date()
    if own:
        try:
            return utc.astimezone(local_tz).date()
        except (OverflowError, ValueError, OSError):
            return utc.date()
    return utc.date()


def log_calendar_midnight(value: Optional[datetime], *, own: bool,
                          local_tz: Optional[tzinfo] = None) -> Optional[datetime]:
    """log_calendar_date() as a naive midnight datetime — the form found and
    DNF dates are stored in (as the Mark as found dialog and the GSAK
    importer already store them)."""
    d = log_calendar_date(value, own=own, local_tz=local_tz)
    return datetime.combine(d, time()) if d is not None else None


def is_own_log(finder_id: object, finder: Optional[str],
               gc_finder_id: Optional[str], gc_username: Optional[str]) -> bool:
    """True when a log with *finder_id* / *finder* is the current user's,
    matched the same way as found_date and count_own_found_logs()."""
    from opensak.utils.utils import normalize_geocacher_name

    gc_finder_id = (gc_finder_id or "").strip()
    if gc_finder_id and str(finder_id or "").strip() == gc_finder_id:
        return True
    username = normalize_geocacher_name(gc_username)
    return bool(username) and normalize_geocacher_name(finder) == username

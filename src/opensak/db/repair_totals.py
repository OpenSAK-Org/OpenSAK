"""
src/opensak/db/repair_totals.py — the totals checks of the database repair
(#1051, phase 3), used by db/repair.py.

A cache keeps values derived from its logs, waypoints and trackables, so
the grid, the filters and sorting don't count them each time. The
importers and macros keep them up to date; they drift in older databases,
after raw SQL, or after a repair removed rows or cleared log dates.

The totals mean "what is in the database", as every import and macro
already computes them (importer/__init__.py ``_upsert_cache()``,
gsak_importer.py, macro/cache_write.py ``refresh_derived()``):

* ``log_count``: the cache's logs. ``last_log_date``: the latest log date.
  ``last_found_date``: the latest found-type log (FOUND_LOG_TYPES) by
  anyone. ``last_four_logs``: the four latest dated logs, one
  "date<TAB>type<TAB>finder" line each. Logs with the same date may be in
  any order, so the same lines in another order are not a difference.
* ``waypoint_count``, ``trackable_count``: the cache's rows.
* ``found_log_count``: the user's own found-type logs (is_own_log()). Only
  raised, never lowered: a missing log doesn't prove fewer finds (a Pocket
  Query brings only some logs, and #1310 will delete logs).

``find_count`` (the site's total, or GSAK's) is not derived from the logs
and is never touched.

Found: a cache with one of the user's own found logs that isn't marked
found is marked, and gets the found date of the latest such log (as
log_calendar_midnight() makes it, #875) when it has none. ``found`` is never
cleared: Adventure Labs and manual marks have no log behind them. ``dnf`` is
not checked: the importers take it only from GSAK's flag.

The checks of the user's own logs need the account (Settings → GC finder
id / username); without one they are skipped, as the importer does.

Caches and logs are read in one pass ordered by cache id, so memory stays
flat on a large database. On a real database of 152k caches and 732k logs
the stored values matched these rules exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator, Optional

from opensak.utils.constants import FOUND_LOG_TYPES
from opensak.utils.log_dates import is_own_log, log_calendar_midnight

# (finder id, username) from Settings.
Account = tuple[Optional[str], Optional[str]]

# The derived columns this module checks, in the order shown.
COLUMNS = ("log_count", "found_log_count", "last_log_date", "last_found_date",
           "last_four_logs", "waypoint_count", "trackable_count")


@dataclass
class CacheTotals:
    """One cache whose stored values differ from its rows. *changes*:
    column → (stored, recalculated). *mark_found*: it has an own found log
    but isn't marked found; *found_date* then is the date to set (None: keep
    the stored one)."""

    cache_id: int
    gc_code: str
    changes: dict[str, tuple[object, object]] = field(default_factory=dict)
    mark_found: bool = False
    found_date: Optional[str] = None


def has_account(account: Optional[Account]) -> bool:
    return account is not None and bool((account[0] or "").strip() or (account[1] or "").strip())


def differences(conn, account: Optional[Account]) -> Iterator[CacheTotals]:
    """Every cache whose derived values or found mark don't match its rows."""
    own = has_account(account)
    finder_id, username = account if account is not None else (None, None)
    waypoints = dict(conn.execute("SELECT cache_id, count(*) FROM waypoints GROUP BY cache_id"))
    trackables = dict(conn.execute(
        "SELECT cache_id, count(*) FROM trackables GROUP BY cache_id"))
    logs = conn.cursor().execute(
        "SELECT cache_id, log_date, log_type, finder, finder_id FROM logs ORDER BY cache_id")
    pending = logs.fetchone()
    caches = conn.cursor().execute(
        f"SELECT id, gc_code, found, found_date, {', '.join(COLUMNS)} FROM caches ORDER BY id")
    for row in caches:
        cache_id, code, found, found_date = row[:4]
        stored = dict(zip(COLUMNS, row[4:]))
        cache_logs = []
        while pending is not None and pending[0] < cache_id:
            pending = logs.fetchone()          # a log without a cache
        while pending is not None and pending[0] == cache_id:
            cache_logs.append(pending[1:])
            pending = logs.fetchone()
        result = _check(cache_id, code, stored, cache_logs, waypoints.get(cache_id, 0),
                        trackables.get(cache_id, 0), bool(found), found_date,
                        own, finder_id, username)
        if result is not None:
            yield result


def _check(cache_id: int, code: str, stored: dict, logs: list, waypoint_count: int,
           trackable_count: int, found: bool, found_date: Optional[str], own: bool,
           finder_id: Optional[str], username: Optional[str]) -> Optional[CacheTotals]:
    # Log dates as the date checks leave them: converted to the stored
    # format, or None when unreadable or GSAK's 1970 "no date".
    logs = [(log_date(lg[0]), *lg[1:]) for lg in logs]
    dated = [lg for lg in logs if lg[0] is not None]
    calc: dict[str, object] = {
        "log_count": len(logs),
        "last_log_date": max((lg[0] for lg in dated), default=None),
        "last_found_date": max((lg[0] for lg in dated if lg[1] in FOUND_LOG_TYPES),
                               default=None),
        "last_four_logs": four_logs(dated),
        "waypoint_count": waypoint_count,
        "trackable_count": trackable_count,
    }
    own_found = [lg for lg in logs if lg[1] in FOUND_LOG_TYPES
                 and is_own_log(lg[3], lg[2], finder_id, username)] if own else []
    if own and len(own_found) > (stored["found_log_count"] or 0):
        calc["found_log_count"] = len(own_found)
    result = CacheTotals(cache_id, code)
    for column, value in calc.items():
        if column == "last_four_logs" and _same_lines(stored[column], value):
            continue
        if stored[column] != value:
            result.changes[column] = (stored[column], value)
    if own_found and not found:
        result.mark_found = True
        if not found_date:
            latest = max((lg[0] for lg in own_found if lg[0] is not None), default=None)
            result.found_date = _found_day(latest)
    return result if result.changes or result.mark_found else None


_STORED = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}")


def log_date(value: object) -> Optional[str]:
    """A stored log date in the stored format, as the date checks would
    leave it; None for no date."""
    from opensak.db.repair_dates import EPOCH, normalize

    if isinstance(value, str) and _STORED.fullmatch(value) and value != EPOCH:
        return value                        # the common case, kept fast
    if value is None:
        return None
    fixed = normalize(value)
    return fixed if fixed != EPOCH else None


def four_logs(dated: list) -> Optional[str]:
    """last_four_logs as the importer writes it, from (date, type, finder, …)
    rows with a date in the stored format."""
    latest = sorted(dated, key=lambda lg: lg[0], reverse=True)[:4]
    return "\n".join(f"{lg[0][:10]}T{lg[0][11:19]}\t{lg[1]}\t{lg[2] or ''}"
                     for lg in latest) or None


def _same_lines(stored: object, calc: object) -> bool:
    """The same lines, maybe in another order (logs with the same date)."""
    if not isinstance(stored, str) or not isinstance(calc, str):
        return False
    return sorted(stored.split("\n")) == sorted(calc.split("\n"))


def _found_day(stored_date: Optional[str]) -> Optional[str]:
    from opensak.db.repair_dates import to_stored

    if not stored_date:
        return None
    try:
        day = log_calendar_midnight(datetime.fromisoformat(stored_date), own=True)
    except ValueError:
        return None
    return to_stored(day) if day is not None else None


def shown(value: object) -> str:
    """A stored or recalculated value on one line without tabs, so it fits
    a tree cell and the tab-separated "Copy list" (last_four_logs has both)."""
    if value is None:
        return "NULL"
    return str(value).replace("\t", " ").replace("\n", " | ")

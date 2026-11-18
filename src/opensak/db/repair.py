"""
src/opensak/db/repair.py — check the data of a database and fix what can be
fixed safely, like GSAK's Repair/Defrag (#1051).

#1048 checks the *file* (quick_check, integrity_check, foreign_key_check);
this module checks the *data*. ``scan()`` only reads and reports every check
with its number of problems and a few example GC codes. ``repair()`` fixes
the checks the user picked, in one transaction, and leaves everything else
as it is. Some checks are report only: there is no safe automatic fix, so
the user has to look at the caches.

Checks in this phase:

* rows that point to a missing parent row (``foreign_key_check``): logs,
  waypoints, attributes, trackables and user notes of a cache that is gone.
  Fix: delete them. Foreign keys are enforced and cache_delete.py removes
  the child rows first, so these come from older databases, migrations that
  run with foreign keys off, or a crash.
* a waypoint's ``parent_gc_code`` that isn't the code of its own cache.
  Fix: set it, as the importer and macros do.
* the corrected-coordinates flag without corrected coordinates. Filters
  count such a cache as corrected, the map and distances do not. Fix: clear
  the flag. Coordinates "corrected" to the listed ones are fine: they are a
  real user choice (the final is at the listed coordinates).
* rows of the companion database (#1034) whose cache is gone. Fix:
  ``companion.cleanup()``.
* dates (phase 2, details in db/repair_dates.py): values SQLAlchemy can't
  read, which break loading a cache, or that are in another format, which
  sorts and filters wrongly. Fix: convert them to the stored format, or
  clear the ones that can't be read as a date at all. GSAK's "no date",
  1970-01-01, is a check of its own. Fix: clear it.
* report only: coordinates out of range, difficulty or terrain outside
  1–5 or not in 0.5 steps, an empty GC code or name, dates before May 2000
  or in the future, and a custom waypoint's link to its parent cache
  (``caches.parent_gc_code``) that points to a cache that is not in the
  database. Every database is independent, so the parent may be in another
  database or be imported later: the link isn't necessarily wrong, and
  clearing it would lose it.

No duplicate check: the unique constraints on attributes, ``logs.log_id``
and waypoints exist in every database (models since v0.2.0, migrations 2 and
21), and the importers replace a cache's waypoints and match logs by id, so
the NULL gaps of those constraints are not filled twice either.

Fixing a correction flag would fire the last_user_update trigger (#1140);
a repair is not a user edit, so the dates are put back.

The functions open their own sqlite3 connection, like maintenance.py. The
scan also works while the database is open; for repair() the caller
releases the database's connections first.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

from opensak.db import repair_dates
from opensak.db.repair_dates import Detail

logger = logging.getLogger(__name__)

# Affected rows shown in the dialog per check …
MAX_EXAMPLES = 5
# … and kept for "Copy list", so the user can fix report-only problems by
# hand. The count is always complete.
MAX_DETAILS = 1000
# Rows per DELETE … WHERE rowid IN (…), below SQLite's bound-parameter limit.
_CHUNK = 500

# Check ids. Their names are in database_repair_dialog.py.
ORPHANS = "orphans"
PARENT_LINK = "parent_link"
WAYPOINT_PARENT = "waypoint_parent"
CORRECTED_FLAG = "corrected_flag"
COMPANION_ORPHANS = "companion_orphans"
COORDS = "coords"
CORRECTED_COORDS = "corrected_coords"
RATINGS = "ratings"
IDENTITY = "identity"
DATE_FORMAT = "date_format"
DATE_UNREADABLE = "date_unreadable"
DATE_PLACEHOLDER = "date_placeholder"
DATE_RANGE = "date_range"


@dataclass(frozen=True)
class Finding:
    """One check's result. *table* is set for the orphan checks (one finding
    per table). *fixable* False: report only. *details*: the first
    MAX_DETAILS affected rows, ordered by GC code (none for orphans, whose
    cache is gone)."""

    check: str
    count: int
    fixable: bool
    details: tuple[Detail, ...] = ()
    table: Optional[str] = None

    @property
    def examples(self) -> tuple[str, ...]:
        return tuple(d.text for d in self.details[:MAX_EXAMPLES])

    @property
    def key(self) -> str:
        """Identifies the finding to repair(): the check, or check:table."""
        return f"{self.check}:{self.table}" if self.table else self.check


@dataclass(frozen=True)
class ScanResult:
    path: Path
    findings: tuple[Finding, ...] = ()
    # quick_check messages. When set, the file itself is damaged: the data
    # checks did not run, and only a backup can help.
    damaged: tuple[str, ...] = ()

    @property
    def problems(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.count)

    @property
    def fixable(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.problems if f.fixable)


@dataclass
class RepairResult:
    """What repair() changed: finding key → rows fixed."""

    path: Path
    fixed: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.fixed.values())


# ── Report-only checks: (id, WHERE on caches) ─────────────────────────────────

_COORDS_WHERE = (
    "latitude IS NULL OR longitude IS NULL "
    "OR latitude NOT BETWEEN -90 AND 90 OR longitude NOT BETWEEN -180 AND 180"
)


def _rating_bad(col: str) -> str:
    # NULL is fine: custom waypoints have no rating.
    return (f"({col} IS NOT NULL AND ({col} NOT BETWEEN 1 AND 5 "
            f"OR {col} * 2 <> CAST({col} * 2 AS INTEGER)))")


_PARENT_LINK_WHERE = (
    "parent_gc_code IS NOT NULL AND trim(parent_gc_code) <> '' "
    "AND upper(trim(parent_gc_code)) NOT IN (SELECT gc_code FROM caches)"
)

# (id, WHERE on caches, field shown, SQL for the value shown)
_REPORT_CHECKS: tuple[tuple[str, str, str, str], ...] = (
    (PARENT_LINK, _PARENT_LINK_WHERE, "parent_gc_code", "parent_gc_code"),
    (COORDS, _COORDS_WHERE, "latitude, longitude",
     "quote(latitude) || ', ' || quote(longitude)"),
    (RATINGS, f"{_rating_bad('difficulty')} OR {_rating_bad('terrain')}", "difficulty / terrain",
     "quote(difficulty) || ' / ' || quote(terrain)"),
    (IDENTITY, "gc_code IS NULL OR trim(gc_code) = '' OR name IS NULL OR trim(name) = ''",
     "gc_code / name", "quote(gc_code) || ' / ' || quote(name)"),
)
_CORRECTED_VALUE = "quote(n.corrected_lat) || ', ' || quote(n.corrected_lon)"

_CORRECTED_COORDS_WHERE = (
    "n.is_corrected AND n.corrected_lat IS NOT NULL AND n.corrected_lon IS NOT NULL "
    "AND (n.corrected_lat NOT BETWEEN -90 AND 90 OR n.corrected_lon NOT BETWEEN -180 AND 180)"
)

# ── Fixable checks on the main database ───────────────────────────────────────

_WAYPOINT_PARENT_FROM = (
    "FROM waypoints w JOIN caches c ON c.id = w.cache_id "
    "WHERE w.parent_gc_code IS NOT c.gc_code"
)
def _corrected_flag_where(alias: str = "") -> str:
    a = f"{alias}." if alias else ""
    return f"{a}is_corrected AND ({a}corrected_lat IS NULL OR {a}corrected_lon IS NULL)"


# ── Scan ──────────────────────────────────────────────────────────────────────

def scan(db_path: Path, with_companion: bool = False,
         step: Optional[Callable[[str], None]] = None) -> ScanResult:
    """
    Run every check on the database *db_path* (and on its companion when
    *with_companion*). Changes nothing. *step* is called with each check id
    before it runs. Raises sqlite3.Error if the file can't be read.
    """
    db_path = Path(db_path)
    notify = step or (lambda _id: None)
    conn = _connect(db_path)
    try:
        notify("quick_check")
        rows = [str(r[0]) for r in conn.execute("PRAGMA quick_check").fetchall()]
        if rows != ["ok"]:
            return ScanResult(db_path, damaged=tuple(rows))
        findings: list[Finding] = []

        notify(ORPHANS)
        for table, count in sorted(_orphan_rowids_by_table(conn, count_only=True).items()):
            findings.append(Finding(ORPHANS, count, True, table=table))

        notify(WAYPOINT_PARENT)
        findings.append(_finding(conn, WAYPOINT_PARENT, True, _WAYPOINT_PARENT_FROM, "c.gc_code",
                                 "waypoints.parent_gc_code", "w.parent_gc_code"))

        notify(CORRECTED_FLAG)
        findings.append(_note_finding(conn, CORRECTED_FLAG, _corrected_flag_where("n"), True))

        notify(DATE_FORMAT)
        findings += _date_findings(conn)

        if with_companion:
            notify(COMPANION_ORPHANS)
            findings.append(Finding(COMPANION_ORPHANS,
                                    sum(_companion_orphans(db_path).values()), True))

        for check, where, label, value in _REPORT_CHECKS:
            notify(check)
            findings.append(_cache_finding(conn, check, where, False, label, value))
        notify(CORRECTED_COORDS)
        findings.append(_note_finding(conn, CORRECTED_COORDS, _CORRECTED_COORDS_WHERE, False))
        notify(DATE_RANGE)
        count, dates = repair_dates.out_of_range(conn, MAX_DETAILS)
        findings.append(Finding(DATE_RANGE, count, False, tuple(dates)))
    finally:
        conn.close()
    return ScanResult(db_path, tuple(findings))


_DATE_CHECKS = (DATE_FORMAT, DATE_UNREADABLE, DATE_PLACEHOLDER)


def _date_check(bad: repair_dates.BadDate) -> str:
    if bad.fixed == repair_dates.EPOCH:
        return DATE_PLACEHOLDER
    return DATE_FORMAT if bad.fixed is not None else DATE_UNREADABLE


def _all_bad_dates(conn: sqlite3.Connection) -> Iterator[repair_dates.BadDate]:
    yield from repair_dates.bad_dates(conn)
    yield from repair_dates.placeholder_dates(conn)


def _date_findings(conn: sqlite3.Connection) -> list[Finding]:
    counts = {check: 0 for check in _DATE_CHECKS}
    details: dict[str, list[Detail]] = {check: [] for check in _DATE_CHECKS}
    for bad in _all_bad_dates(conn):
        check = _date_check(bad)
        counts[check] += 1
        kept = details[check]
        kept.append(bad.detail)
        if len(kept) > 2 * MAX_DETAILS:     # keep memory flat on a huge log table
            kept.sort()
            del kept[MAX_DETAILS:]
    return [Finding(check, counts[check], True, tuple(sorted(details[check])[:MAX_DETAILS]))
            for check in counts]


def _finding(conn: sqlite3.Connection, check: str, fixable: bool, from_where: str,
             code_sql: str, label: str, value_sql: str) -> Finding:
    """A finding from "FROM … WHERE …" *from_where*, with the GC code and
    value of each affected row from *code_sql* and *value_sql*."""
    count = conn.execute(f"SELECT count(*) {from_where}").fetchone()[0]
    rows = conn.execute(
        f"SELECT {code_sql}, {value_sql} {from_where} ORDER BY 1 LIMIT {MAX_DETAILS}"
    ).fetchall() if count else []
    return Finding(check, count, fixable,
                   tuple(Detail(repair_dates.shown(code), label, repair_dates.shown(value))
                         for code, value in rows))


def _cache_finding(conn: sqlite3.Connection, check: str, where: str, fixable: bool,
                   label: str, value_sql: str) -> Finding:
    return _finding(conn, check, fixable, f"FROM caches WHERE {where}", "gc_code",
                    label, value_sql)


def _note_finding(conn: sqlite3.Connection, check: str, where: str, fixable: bool) -> Finding:
    return _finding(conn, check, fixable,
                    f"FROM user_notes n JOIN caches c ON c.id = n.cache_id WHERE {where}",
                    "c.gc_code", "corrected", _CORRECTED_VALUE)


def _orphan_rowids_by_table(conn: sqlite3.Connection, count_only: bool = False) -> dict:
    """foreign_key_check grouped by table: table → row count (*count_only*)
    or table → list of rowids."""
    found: dict = {}
    for table, rowid, _parent, _fkid in conn.execute("PRAGMA foreign_key_check").fetchall():
        if count_only:
            found[table] = found.get(table, 0) + 1
        elif rowid is not None:
            found.setdefault(table, []).append(rowid)
    if not count_only:
        # A row with two broken keys is listed twice.
        found = {t: sorted(set(ids)) for t, ids in found.items()}
    return found


def _companion_orphans(db_path: Path) -> dict[str, int]:
    from opensak.db import companion

    if not companion.exists(db_path):
        return {}
    result = companion.check(db_path)
    return dict(result.orphans) if result is not None else {}


# ── Repair ────────────────────────────────────────────────────────────────────

def repair(db_path: Path, keys: set[str],
           step: Optional[Callable[[str], None]] = None) -> RepairResult:
    """
    Fix the findings *keys* (Finding.key of fixable findings) in *db_path*.
    The main database's fixes run in one transaction; the companion is
    cleaned up after it is committed. Keys of report-only checks are
    ignored. The caller has released the database's connections. Raises
    sqlite3.Error; then nothing in the main database has changed.
    """
    db_path = Path(db_path)
    notify = step or (lambda _id: None)
    result = RepairResult(db_path)
    conn = _connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        with conn:
            orphan_tables = {k.split(":", 1)[1] for k in keys if k.startswith(f"{ORPHANS}:")}
            if orphan_tables:
                notify(ORPHANS)
                for table, rowids in _orphan_rowids_by_table(conn).items():
                    if table in orphan_tables:
                        result.fixed[f"{ORPHANS}:{table}"] = _delete_rowids(conn, table, rowids)
            if WAYPOINT_PARENT in keys:
                notify(WAYPOINT_PARENT)
                result.fixed[WAYPOINT_PARENT] = conn.execute(
                    "UPDATE waypoints SET parent_gc_code = (SELECT gc_code FROM caches "
                    "WHERE caches.id = waypoints.cache_id) "
                    "WHERE cache_id IN (SELECT id FROM caches) AND parent_gc_code IS NOT "
                    "(SELECT gc_code FROM caches WHERE caches.id = waypoints.cache_id)"
                ).rowcount
            if CORRECTED_FLAG in keys:
                notify(CORRECTED_FLAG)
                result.fixed[CORRECTED_FLAG] = _clear_corrected_flags(conn)
            if keys & set(_DATE_CHECKS):
                notify(DATE_FORMAT)
                result.fixed.update(_fix_dates(conn, keys))
    finally:
        conn.close()
    if COMPANION_ORPHANS in keys:
        from opensak.db import companion

        notify(COMPANION_ORPHANS)
        if companion.exists(db_path):
            result.fixed[COMPANION_ORPHANS] = sum(companion.cleanup(db_path).orphans.values())
    result.fixed = {k: v for k, v in result.fixed.items() if v > 0}
    if result.total:
        logger.info("repair of %s: %s", db_path, result.fixed)
    return result


def _fix_dates(conn: sqlite3.Connection, keys: set[str]) -> dict[str, int]:
    """Convert (DATE_FORMAT) the dates not in the stored format, or clear
    the unreadable ones (DATE_UNREADABLE) and GSAK's 1970-01-01 "no date"
    (DATE_PLACEHOLDER). A NOT NULL column gets now instead of NULL."""
    fixed = {check: 0 for check in _DATE_CHECKS}
    now = repair_dates.now_stored()
    for bad in list(_all_bad_dates(conn)):
        check = _date_check(bad)
        if check not in keys:
            continue
        if check == DATE_FORMAT:
            value: Optional[str] = bad.fixed
        else:
            value = now if bad.column.not_null else None
        conn.execute(
            f"UPDATE {_quote(bad.column.table)} SET {_quote(bad.column.column)} = ? "
            "WHERE rowid = ?", (value, bad.rowid))
        fixed[check] += 1
    return fixed


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _delete_rowids(conn: sqlite3.Connection, table: str, rowids: list[int]) -> int:
    removed = 0
    quoted = _quote(table)
    for start in range(0, len(rowids), _CHUNK):
        chunk = rowids[start:start + _CHUNK]
        marks = ", ".join("?" * len(chunk))
        removed += conn.execute(f"DELETE FROM {quoted} WHERE rowid IN ({marks})", chunk).rowcount
    return removed


def _clear_corrected_flags(conn: sqlite3.Connection) -> int:
    """Clear the flag, keeping the caches' last_user_update: the trigger
    would set it to now, but a repair is not a user edit."""
    kept = conn.execute(
        "SELECT c.id, c.last_user_update FROM caches c JOIN user_notes n ON n.cache_id = c.id "
        f"WHERE {_corrected_flag_where('n')}"
    ).fetchall()
    count = conn.execute(
        f"UPDATE user_notes SET is_corrected = 0, corrected_source = NULL "
        f"WHERE {_corrected_flag_where()}"
    ).rowcount
    conn.executemany("UPDATE caches SET last_user_update = ? WHERE id = ?",
                     [(stamp, cache_id) for cache_id, stamp in kept])
    return count


def _connect(db_path: Path) -> sqlite3.Connection:
    """A connection to the existing file *db_path* — never creates one."""
    if not db_path.is_file():
        raise sqlite3.OperationalError(f"unable to open database file: {db_path}")
    return sqlite3.connect(str(db_path), timeout=30)

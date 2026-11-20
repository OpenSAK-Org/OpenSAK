"""
src/opensak/db/repair.py — check the data of a database and fix what can be
fixed safely, like GSAK's Repair/Defrag (#1051, phase 1).

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
* report only: coordinates out of range, difficulty or terrain outside
  1–5 or not in 0.5 steps, an empty GC code or name, and a custom
  waypoint's link to its parent cache (``caches.parent_gc_code``) that
  points to a cache that is not in the database. Every database is
  independent, so the parent may be in another database or be imported
  later: the link isn't necessarily wrong, and clearing it would lose it.

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
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# GC codes listed per check.
MAX_EXAMPLES = 5
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


@dataclass(frozen=True)
class Finding:
    """One check's result. *table* is set for the orphan checks (one finding
    per table). *fixable* False: report only."""

    check: str
    count: int
    fixable: bool
    examples: tuple[str, ...] = ()
    table: Optional[str] = None

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

_REPORT_CHECKS: tuple[tuple[str, str], ...] = (
    (PARENT_LINK, _PARENT_LINK_WHERE),
    (COORDS, _COORDS_WHERE),
    (RATINGS, f"{_rating_bad('difficulty')} OR {_rating_bad('terrain')}"),
    (IDENTITY, "gc_code IS NULL OR trim(gc_code) = '' OR name IS NULL OR trim(name) = ''"),
)

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
        count = conn.execute(f"SELECT count(*) {_WAYPOINT_PARENT_FROM}").fetchone()[0]
        examples = _codes(conn, f"SELECT DISTINCT c.gc_code {_WAYPOINT_PARENT_FROM} "
                                "ORDER BY c.gc_code") if count else ()
        findings.append(Finding(WAYPOINT_PARENT, count, True, examples))

        notify(CORRECTED_FLAG)
        findings.append(_note_finding(conn, CORRECTED_FLAG, _corrected_flag_where("n"), True))

        if with_companion:
            notify(COMPANION_ORPHANS)
            findings.append(Finding(COMPANION_ORPHANS,
                                    sum(_companion_orphans(db_path).values()), True))

        for check, where in _REPORT_CHECKS:
            notify(check)
            findings.append(_cache_finding(conn, check, where, False))
        notify(CORRECTED_COORDS)
        findings.append(_note_finding(conn, CORRECTED_COORDS, _CORRECTED_COORDS_WHERE, False))
    finally:
        conn.close()
    return ScanResult(db_path, tuple(findings))


def _cache_finding(conn: sqlite3.Connection, check: str, where: str, fixable: bool) -> Finding:
    count = conn.execute(f"SELECT count(*) FROM caches WHERE {where}").fetchone()[0]
    examples = _codes(conn, f"SELECT gc_code FROM caches WHERE {where} ORDER BY gc_code") \
        if count else ()
    return Finding(check, count, fixable, examples)


def _note_finding(conn: sqlite3.Connection, check: str, where: str, fixable: bool) -> Finding:
    base = f"FROM user_notes n JOIN caches c ON c.id = n.cache_id WHERE {where}"
    count = conn.execute(f"SELECT count(*) {base}").fetchone()[0]
    examples = _codes(conn, f"SELECT c.gc_code {base} ORDER BY c.gc_code") if count else ()
    return Finding(check, count, fixable, examples)


def _codes(conn: sqlite3.Connection, sql: str) -> tuple[str, ...]:
    rows = conn.execute(f"{sql} LIMIT {MAX_EXAMPLES}").fetchall()
    # An empty GC code is one of the problems; show it as "''".
    return tuple(str(r[0]) if r[0] not in (None, "") else "''" for r in rows)


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


def _delete_rowids(conn: sqlite3.Connection, table: str, rowids: list[int]) -> int:
    removed = 0
    quoted = '"' + table.replace('"', '""') + '"'
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

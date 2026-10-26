"""
src/opensak/db/maintenance.py — compact a database with VACUUM (#1033, #1048).

Rebuilding tables (migration 26) leaves the old pages on SQLite's free list,
so the file grows. ``vacuum()`` gives that space back, with conditions so it
can never turn a successful migration into a failed one:

- It runs only when it pays off: the free pages must be at least
  ``MIN_RECLAIM_BYTES`` *and* ``MIN_RECLAIM_FRACTION`` of the file. Fresh and
  small databases are skipped.
- It checks free disk space first. VACUUM writes a full temporary copy into
  SQLite's temp folder, and in WAL mode the whole database also passes
  through the WAL next to the database. If either place is short, it skips.
- It never raises. A failed VACUUM leaves a valid (just larger) database; a
  warning is logged and the caller goes on.
- Afterwards ``PRAGMA wal_checkpoint(TRUNCATE)`` shrinks the WAL file again.

The caller must not be inside a transaction (VACUUM fails there) — run it
after the commit and after foreign keys are back on.

``vacuum()`` works on an open sqlite3 connection (the migration's own);
``vacuum_file()`` opens one for a database file, for a "Maintenance" action
in the Database Manager (#1048). That caller must make sure the database is
not open elsewhere first.

The other maintenance functions of #1048 — ``file_info()`` (Info),
``check_file()`` (Check), ``optimize_file()`` (Optimize) and
``checkpoint_file()`` (Checkpoint) — each open their own connection too.
They only read, or change no data, so they also work on the active
database while it is open. The companion database has the same functions
in db/companion.py; the Maintenance dialog runs both.

Optimize also runs on its own (db/database.py ``optimize_database()``):
when a database is opened, closed or switched, after migrations, and after
imports, deletes and moves. Without statistics (``sqlite_stat1``) the query
planner often picks an index on a column with two or three values over the
lat/lon index; on a 1.4 GB database the map query took 517 ms instead of
35 ms (#1048).
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# VACUUM only when at least this much space would come back …
MIN_RECLAIM_BYTES = 50 * 1024 * 1024
# … and it is at least this share of the file.
MIN_RECLAIM_FRACTION = 0.20

# Extra free space required on top of the estimate, so VACUUM never fills a
# disk to the last byte.
_FREE_SPACE_MARGIN = 50 * 1024 * 1024

# Outcomes in VacuumResult.status.
DONE = "done"
NOT_WORTHWHILE = "not_worthwhile"
LOW_DISK_SPACE = "low_disk_space"
IN_TRANSACTION = "in_transaction"
FAILED = "failed"


@dataclass(frozen=True)
class PageStats:
    """Page counts of a database, from its PRAGMAs."""

    page_size: int
    page_count: int
    freelist_count: int

    @property
    def file_bytes(self) -> int:
        return self.page_size * self.page_count

    @property
    def free_bytes(self) -> int:
        """What VACUUM would give back."""
        return self.page_size * self.freelist_count

    @property
    def used_bytes(self) -> int:
        """Roughly the size after VACUUM — what it has to write, twice."""
        return self.file_bytes - self.free_bytes


@dataclass(frozen=True)
class SpaceCheck:
    """Free disk space for a VACUUM; *shortages* lists (folder, needed, free)."""

    needed: int
    shortages: tuple[tuple[Path, int, int], ...]

    @property
    def ok(self) -> bool:
        return not self.shortages


@dataclass(frozen=True)
class VacuumResult:
    """What vacuum() did to *db_path*; sizes include the -wal file."""

    db_path: Path
    status: str
    size_before: int
    size_after: int
    reclaimable: int
    seconds: float = 0.0
    error: Optional[str] = None

    @property
    def ran(self) -> bool:
        return self.status == DONE


def page_stats(conn: sqlite3.Connection) -> PageStats:
    """Page size, page count and free-list count of the open database."""
    return PageStats(
        page_size=conn.execute("PRAGMA page_size").fetchone()[0],
        page_count=conn.execute("PRAGMA page_count").fetchone()[0],
        freelist_count=conn.execute("PRAGMA freelist_count").fetchone()[0],
    )


def is_worthwhile(
    stats: PageStats,
    min_bytes: int = MIN_RECLAIM_BYTES,
    min_fraction: float = MIN_RECLAIM_FRACTION,
) -> bool:
    """True if VACUUM would give back at least *min_bytes* and *min_fraction*
    of the file."""
    if stats.file_bytes <= 0:
        return False
    return (
        stats.free_bytes >= min_bytes
        and stats.free_bytes >= min_fraction * stats.file_bytes
    )


def sqlite_temp_dir() -> Path:
    """
    The folder SQLite writes VACUUM's temporary copy to.

    Same search order as SQLite: on Windows the system temp path (TMP, TEMP,
    USERPROFILE); elsewhere SQLITE_TMPDIR, TMPDIR, /var/tmp, /usr/tmp, /tmp.
    On Linux /tmp may be a small tmpfs, which is why this matters.
    """
    if sys.platform == "win32":
        candidates = [os.environ.get(v) for v in ("TMP", "TEMP", "USERPROFILE")]
    else:
        candidates = [os.environ.get("SQLITE_TMPDIR"), os.environ.get("TMPDIR"),
                      "/var/tmp", "/usr/tmp", "/tmp"]
    for c in candidates:
        if c and os.path.isdir(c) and os.access(c, os.W_OK | os.X_OK):
            return Path(c)
    return Path(tempfile.gettempdir())


def _same_device(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def check_free_space(
    db_path: Path, stats: PageStats, temp_in_memory: bool = False
) -> SpaceCheck:
    """
    Is there room for a VACUUM of *db_path*?

    It writes about ``stats.used_bytes`` to the temp folder and the same
    again into the WAL next to the database; on one disk that is twice.
    """
    need = stats.used_bytes + _FREE_SPACE_MARGIN
    db_dir = Path(db_path).parent
    needs: dict[Path, int] = {db_dir: need}
    if not temp_in_memory:
        tmp = sqlite_temp_dir()
        if _same_device(tmp, db_dir):
            needs[db_dir] += stats.used_bytes
        else:
            needs[tmp] = need
    shortages = []
    for folder, n in needs.items():
        free = shutil.disk_usage(folder).free
        if free < n:
            shortages.append((folder, n, free))
    return SpaceCheck(needed=sum(needs.values()), shortages=tuple(shortages))


def _file_size(db_path: Path) -> int:
    """Size of the database plus its -wal file."""
    total = 0
    for p in (Path(db_path), Path(f"{db_path}-wal")):
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def vacuum(
    conn: sqlite3.Connection,
    db_path: Path,
    *,
    force: bool = False,
    min_bytes: int = MIN_RECLAIM_BYTES,
    min_fraction: float = MIN_RECLAIM_FRACTION,
    progress: Optional[Callable[[], None]] = None,
) -> VacuumResult:
    """
    VACUUM the database open on *conn* (at *db_path*) if it pays off and
    there is room, then truncate the WAL. Never raises.

    *force* skips the pays-off check (an explicit "Maintenance" action), not
    the disk-space check. *progress* is called right before VACUUM starts,
    so a caller can tell the user it may take a while.
    """
    db_path = Path(db_path)
    size_before = _file_size(db_path)
    reclaimable = 0
    try:
        if conn.in_transaction:
            logger.warning("vacuum %s: skipped, a transaction is open", db_path)
            return VacuumResult(db_path, IN_TRANSACTION, size_before, size_before, 0)

        stats = page_stats(conn)
        reclaimable = stats.free_bytes
        if not force and not is_worthwhile(stats, min_bytes, min_fraction):
            logger.info(
                "vacuum %s: skipped, only %d MB of %d MB free",
                db_path, reclaimable // 2**20, stats.file_bytes // 2**20,
            )
            return VacuumResult(db_path, NOT_WORTHWHILE, size_before, size_before, reclaimable)

        # A table rebuild (migration 26) leaves a WAL as large as the rebuilt
        # tables. Fold it into the database first, so that space is free
        # again before the check and VACUUM's own WAL starts from zero.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        temp_in_memory = conn.execute("PRAGMA temp_store").fetchone()[0] == 2
        space = check_free_space(db_path, stats, temp_in_memory)
        if not space.ok:
            for folder, needed, free in space.shortages:
                logger.warning(
                    "vacuum %s: skipped, not enough free disk space in %s "
                    "(%d MB needed, %d MB free)",
                    db_path, folder, needed // 2**20, free // 2**20,
                )
            return VacuumResult(db_path, LOW_DISK_SPACE, size_before, size_before, reclaimable)

        if progress is not None:
            progress()
        logger.info(
            "vacuum %s: starting, %d MB file, %d MB to reclaim",
            db_path, size_before // 2**20, reclaimable // 2**20,
        )
        t0 = time.monotonic()
        conn.execute("VACUUM")
        busy, _log, _done = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            logger.warning("vacuum %s: WAL checkpoint blocked by another connection", db_path)
        seconds = time.monotonic() - t0
    except Exception as e:
        logger.warning("vacuum %s failed; the database stays as it was",
                       db_path, exc_info=True)
        return VacuumResult(db_path, FAILED, size_before, _file_size(db_path),
                            reclaimable, error=str(e))

    size_after = _file_size(db_path)
    logger.info(
        "vacuum %s: done in %.1fs, %d MB -> %d MB",
        db_path, seconds, size_before // 2**20, size_after // 2**20,
    )
    return VacuumResult(db_path, DONE, size_before, size_after, reclaimable, seconds)


def vacuum_file(db_path: Path, **kwargs) -> VacuumResult:
    """
    vacuum() on its own connection to the database file *db_path*.

    For the Database Manager (#1048): the caller must make sure the
    database is not open elsewhere (dispose its engine first). Keyword
    arguments go to vacuum(). Never raises.
    """
    db_path = Path(db_path)
    try:
        conn = sqlite3.connect(db_path, timeout=30)
    except Exception as e:
        logger.warning("vacuum %s: cannot open", db_path, exc_info=True)
        size = _file_size(db_path)
        return VacuumResult(db_path, FAILED, size, size, 0, error=str(e))
    try:
        return vacuum(conn, db_path, **kwargs)
    finally:
        conn.close()


def vacuum_with_companion(
    db_path: Path, **kwargs
) -> tuple[VacuumResult, Optional[VacuumResult]]:
    """
    Compact (#1048) the database *db_path* and then its companion database
    (#1034), each on its own connection and each only when it pays off and
    there is room (or with ``force``). Returns both results; the second is
    None when the database has no companion.

    Same rule as vacuum_file(): the caller makes sure neither file is open
    elsewhere — disposing the database's engine releases the companion as
    well, since it is attached to the same connections. A companion that
    fails or is skipped never changes the database's result. Never raises.
    """
    from opensak.db import companion

    main = vacuum_file(db_path, **kwargs)
    path = companion.companion_path(Path(db_path))
    if not path.is_file():
        return main, None
    return main, vacuum_file(path, **kwargs)


# ── Info, Check, Optimize, Checkpoint (#1048) ─────────────────────────────────

# Compact from the Maintenance dialog: worth it from this much (and share)
# free space. Lower than after a migration, since the user asked for it.
MANUAL_MIN_RECLAIM_BYTES = 10 * 1024 * 1024
MANUAL_MIN_RECLAIM_FRACTION = 0.05

# Row counts shown by Info.
COUNTED_TABLES = ("caches", "logs", "waypoints")


@dataclass(frozen=True)
class FileInfo:
    """Info (#1048) for a database file."""

    path: Path
    file_bytes: int                 # the .db file on disk
    wal_bytes: int
    stats: PageStats
    schema_version: int             # PRAGMA user_version
    sqlite_version: str
    rows: dict[str, int]            # COUNTED_TABLES that exist → row count

    @property
    def free_fraction(self) -> float:
        return self.stats.free_bytes / self.stats.file_bytes if self.stats.file_bytes else 0.0


@dataclass(frozen=True)
class CheckReport:
    """Check (#1048) for a database file. ok when nothing was found."""

    path: Path
    thorough: bool
    problems: tuple[str, ...] = ()          # quick_check / integrity_check messages
    foreign_key_problems: int = 0           # rows foreign_key_check reports

    @property
    def ok(self) -> bool:
        return not (self.problems or self.foreign_key_problems)


def _connect(db_path: Path, timeout: float = 30) -> sqlite3.Connection:
    """A connection to the existing file *db_path* — never creates one."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise sqlite3.OperationalError(f"unable to open database file: {db_path}")
    return sqlite3.connect(str(db_path), timeout=timeout)


def file_info(db_path: Path) -> FileInfo:
    """Info for the database *db_path*. Raises sqlite3.Error if it can't be
    read."""
    db_path = Path(db_path)
    conn = _connect(db_path)
    try:
        stats = page_stats(conn)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        existing = {
            name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        rows = {
            name: conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            for name in COUNTED_TABLES if name in existing
        }
    finally:
        conn.close()
    return FileInfo(
        path=db_path,
        file_bytes=_size_of(db_path),
        wal_bytes=_size_of(Path(f"{db_path}-wal")),
        stats=stats,
        schema_version=version,
        sqlite_version=sqlite3.sqlite_version,
        rows=rows,
    )


def check_file(db_path: Path, thorough: bool = False) -> CheckReport:
    """
    Check the database *db_path*: ``quick_check`` (or ``integrity_check``
    when *thorough*) and ``foreign_key_check``. Changes nothing. Raises
    sqlite3.Error if it can't be read at all.
    """
    db_path = Path(db_path)
    conn = _connect(db_path)
    try:
        rows = [str(r[0]) for r in conn.execute(
            f"PRAGMA {'integrity_check' if thorough else 'quick_check'}").fetchall()]
        problems = () if rows == ["ok"] else tuple(rows)
        if problems:
            # foreign_key_check would read a damaged file; report what we have.
            return CheckReport(db_path, thorough, problems)
        fk = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    finally:
        conn.close()
    return CheckReport(db_path, thorough, foreign_key_problems=fk)


def optimize(conn: sqlite3.Connection) -> None:
    """
    Refresh the query planner's statistics of the database open on *conn*
    (and of every database attached to it). Raises sqlite3.Error.

    ``PRAGMA optimize`` alone only looks at the tables this connection has
    queried, so on a fresh connection it does nothing. SQLite >= 3.46 takes
    0x10000 for "all tables"; it then also creates the statistics of a
    database that never had any. Older versions get an ANALYZE the first
    time, then ``PRAGMA optimize``.

    Both sample at most ``_ANALYSIS_LIMIT`` rows per index (SQLite >= 3.32;
    older ones ignore the pragma). Without it, both read every index in
    full when a database has no statistics yet, even ``optimize=0x10002`` on
    SQLite 3.50: the first open of a GSAK database of many GB would freeze
    the GUI. The limit stays set on *conn*.
    """
    conn.execute(f"PRAGMA analysis_limit={_ANALYSIS_LIMIT}").fetchall()
    if sqlite3.sqlite_version_info >= (3, 46):
        conn.execute("PRAGMA optimize=0x10002").fetchall()
    elif conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_stat1'"
    ).fetchone() is None:
        conn.execute("ANALYZE")
    else:
        conn.execute("PRAGMA optimize").fetchall()
    if conn.in_transaction:
        conn.commit()


# Rows per index optimize() samples — good enough
# statistics for the planner, fast at any database size.
_ANALYSIS_LIMIT = 1000


def optimize_file(db_path: Path, timeout: float = 30) -> bool:
    """optimize() on its own connection to *db_path*. False if it failed
    (logged); never raises. *timeout*: how long to wait for a writer."""
    try:
        conn = _connect(Path(db_path), timeout)
        try:
            optimize(conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("optimize %s failed: %s", db_path, exc)
        return False
    return True


def checkpoint_file(db_path: Path) -> bool:
    """
    ``wal_checkpoint(TRUNCATE)`` on *db_path*: write the WAL back into the
    database and empty the -wal file. False if another connection kept it
    from finishing (still reading) or it failed (logged); never raises.
    """
    try:
        conn = _connect(Path(db_path))
        try:
            busy, _log, _done = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("checkpoint %s failed: %s", db_path, exc)
        return False
    if busy:
        logger.info("checkpoint %s: blocked by another connection", db_path)
    return not busy


def _size_of(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0

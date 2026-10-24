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

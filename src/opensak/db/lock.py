"""
src/opensak/db/lock.py — one OpenSAK process per database (#937).

The databases run in WAL mode with a 30 s busy timeout, so a long headless
import (`opensak import …`) running while OpenSAK has the same database
open would make the GUI's own writes fail, and the GUI would show stale
data. So each database gets a lock file next to it ("<name>.db.lock"):

* the GUI holds it for its active database (DatabaseManager.hold_active_lock);
* a headless run takes it for its target database, and refuses to start
  (or waits, with --wait) while someone else holds it.

It is a real OS lock (QLockFile), not just a marker file: a lock left by a
process that crashed or was killed is detected as stale by its process id
and taken over, so it never blocks later runs. QLockFile needs no
QApplication.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOCK_SUFFIX = ".lock"


def lock_path_for(db_path: Path) -> Path:
    """The lock file of the database at *db_path*."""
    return db_path.with_name(db_path.name + LOCK_SUFFIX)


class DatabaseLock:
    """The lock of one database file. Not reentrant: a second DatabaseLock on
    the same database fails to acquire it, also in the same process."""

    def __init__(self, db_path: Path):
        from PySide6.QtCore import QLockFile

        self.db_path = Path(db_path)
        self.path = lock_path_for(self.db_path)
        self._lock = QLockFile(str(self.path))
        # Held for as long as the database is open, which can be days: never
        # treat it as stale by age — only when its process is gone.
        self._lock.setStaleLockTime(0)

    @property
    def held(self) -> bool:
        return self._lock.isLocked()

    def acquire(self, wait_seconds: float = 0) -> bool:
        """Take the lock, waiting up to *wait_seconds* for its holder to let
        go. False if someone else still holds it; OSError if the lock file
        cannot be created (e.g. a read-only folder)."""
        from PySide6.QtCore import QLockFile

        if self.held:
            return True
        if self._lock.tryLock(max(0, int(wait_seconds * 1000))):
            return True
        if self._lock.error() != QLockFile.LockError.LockFailedError:
            raise OSError(f"cannot create the lock file {self.path} "
                          f"({self._lock.error().name})")
        return False

    def release(self) -> None:
        if self.held:
            self._lock.unlock()

    def holder(self) -> Optional[str]:
        """Who holds the lock, e.g. "process 1234 (opensak) on PC", or None
        when nobody does."""
        pid, host, app = self._lock.getLockInfo()   # (0, "", "") when not locked
        if not pid:
            return None
        return f"process {pid} ({app or '?'}) on {host or '?'}"

    def __enter__(self) -> "DatabaseLock":
        return self

    def __exit__(self, *exc) -> None:
        self.release()

"""
src/opensak/backup/snapshot.py — WAL-safe database snapshot (issue #943).

OpenSAK databases run in WAL mode (see ``_enable_wal_and_fk`` in
``opensak.db.database``). Recently committed changes can live in the
``-wal`` sidecar file until a checkpoint moves them into the main ``.db``
file, so copying only the main file (``shutil.copy2``) can silently miss
them, or produce an inconsistent copy if a write or checkpoint happens
during the copy.

``snapshot_database()`` uses SQLite's online backup API instead, which reads
through SQLite itself and therefore sees the WAL content too. The result is
written to a temporary file next to the target, switched out of WAL mode
(so it is one self-contained file with no ``-wal``/``-shm`` sidecars),
optionally verified with ``PRAGMA integrity_check``, and only then renamed
into place atomically. An interrupted or failed snapshot never leaves a
half-written file at the target path, and never touches an existing file
there.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path
from typing import Callable, Optional

# (pages_done, pages_total) — called after each backup step.
ProgressCallback = Callable[[int, int], None]

# Pages copied per backup step. With the default 4 KiB page size this is
# 4 MiB per step: small enough for useful progress reporting on large
# databases, large enough that the per-step overhead doesn't matter.
DEFAULT_STEP_PAGES = 1024

# Seconds to wait on a locked source database before giving up.
_BUSY_TIMEOUT = 30

_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")


class SnapshotError(Exception):
    """A database snapshot could not be created. The target is untouched."""


def snapshot_database(
    source: Path,
    target: Path,
    progress: Optional[ProgressCallback] = None,
    *,
    step_pages: int = DEFAULT_STEP_PAGES,
    verify: bool = True,
) -> None:
    """
    Write a consistent, self-contained copy of the SQLite database *source*
    to *target*.

    Safe to call while *source* is open and in use (including the active
    OpenSAK database): all committed changes are included, also those still
    in the ``-wal`` file. If another connection writes to *source* while the
    snapshot is running, SQLite restarts the copy so the result stays
    consistent.

    Args:
        source: the database to copy. Must exist and be a valid SQLite file.
        target: where to write the copy. Its folder is created if needed.
            An existing file at *target* is replaced, but only once the new
            snapshot is complete (and verified, if *verify* is True).
        progress: optional callback, called with (pages_done, pages_total)
            after each backup step. It may raise to cancel the snapshot:
            the exception propagates unchanged, and nothing is left behind.
        step_pages: pages to copy per step (must be > 0).
        verify: run ``PRAGMA integrity_check`` on the copy before it is
            moved into place. On very large databases this adds noticeable
            time, which is why it can be switched off.

    Raises:
        SnapshotError: if the snapshot could not be made. Nothing is left
            behind at *target* (or next to it), and an existing file at
            *target* is left as it was.
    """
    source = Path(source)
    target = Path(target)

    if step_pages <= 0:
        raise ValueError("step_pages must be greater than 0")
    if not source.is_file():
        raise SnapshotError(f"Source database not found: {source}")
    if _same_file(source, target):
        raise SnapshotError("Source and target are the same file")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SnapshotError(f"Cannot create target folder: {exc}") from exc

    # Same folder as the target, so the final os.replace() is an atomic
    # rename on the same filesystem. Dot-prefixed so it stays out of the way
    # (hidden on Linux/macOS) while the snapshot is running.
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")

    try:
        _write_snapshot(source, tmp, progress, step_pages, verify)
        os.replace(tmp, target)
    except BaseException as exc:
        # BaseException, not Exception: a KeyboardInterrupt or a cancelled
        # worker thread must not leave a half-written temp file behind either.
        _remove_with_sidecars(tmp)
        if isinstance(exc, SnapshotError):
            raise
        if isinstance(exc, (sqlite3.Error, OSError)):
            raise SnapshotError(str(exc)) from exc
        raise


def _write_snapshot(
    source: Path,
    tmp: Path,
    progress: Optional[ProgressCallback],
    step_pages: int,
    verify: bool,
) -> None:
    """Copy *source* into the new file *tmp* and finalise it."""
    # A plain connection rather than a ``file:...?mode=ro`` URI: URIs are
    # awkward for Windows UNC paths (databases on a NAS), and the existence
    # check in snapshot_database() already guarantees sqlite3.connect()
    # won't create an empty database. query_only guards against any write
    # through this connection.
    src_conn = sqlite3.connect(str(source), timeout=_BUSY_TIMEOUT)
    try:
        src_conn.execute("PRAGMA query_only = ON")
        try:
            # Reading the schema forces SQLite to parse the header, so a
            # file that isn't a database fails here with a clear message
            # rather than somewhere inside the backup.
            src_conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError as exc:
            raise SnapshotError(
                f"Source is not a valid SQLite database: {exc}"
            ) from exc

        dst_conn = sqlite3.connect(str(tmp))
        try:
            src_conn.backup(
                dst_conn,
                pages=step_pages,
                progress=_progress_adapter(progress),
            )
            # The backup copies the source header, including its WAL flag.
            # Switching the copy to rollback-journal mode makes it a single
            # self-contained file; OpenSAK turns WAL back on when it opens
            # the database.
            dst_conn.execute("PRAGMA journal_mode = DELETE").fetchone()
            if verify:
                _check_integrity(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def _check_integrity(conn: sqlite3.Connection) -> None:
    """Raise SnapshotError unless ``PRAGMA integrity_check`` reports ok."""
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    if rows != [("ok",)]:
        problems = "; ".join(str(row[0]) for row in rows[:5])
        raise SnapshotError(f"Integrity check failed on the copy: {problems}")


def _progress_adapter(
    progress: Optional[ProgressCallback],
) -> Optional[Callable[[int, int, int], None]]:
    """Translate sqlite3's (status, remaining, total) into (done, total)."""
    if progress is None:
        return None

    def _adapter(_status: int, remaining: int, total: int) -> None:
        progress(total - remaining, total)

    return _adapter


def _same_file(a: Path, b: Path) -> bool:
    """True if *a* and *b* point to the same file (also via other spellings)."""
    try:
        return b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


def _remove_with_sidecars(path: Path) -> None:
    """Best-effort removal of *path* and any SQLite sidecar files next to it."""
    for suffix in ("", *_SIDECAR_SUFFIXES):
        p = Path(str(path) + suffix)
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass

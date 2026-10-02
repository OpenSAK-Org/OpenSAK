"""
src/opensak/backup/restore.py — restore databases from a backup set (#954).

Part of the backup epic (#942); design: docs/architecture/backup.md.

Restore always *adds*: a restored database becomes a new database in the
list, and nothing that exists is ever overwritten. It keeps its ``db_uuid``
(it is the same database at an earlier point in time), and because its own
settings live inside the file (#659), it opens exactly as it was.

Restoring the settings themselves (opensak.json, column views, icons) is a
separate, opt-in step — sub-issue 6 of #942. Only filter profiles that no
longer exist are added back here, so a restored database finds its
last-used profile.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from opensak.backup.backupset import (
    SETTINGS_DIRNAME,
    BackupSet,
    DatabaseEntry,
)
from opensak.backup.snapshot import SnapshotError, snapshot_database

if TYPE_CHECKING:
    from opensak.db.manager import DatabaseInfo, DatabaseManager

logger = logging.getLogger(__name__)

# (pages_done, pages_total) — as for snapshot_database()
ProgressCallback = Callable[[int, int], None]


class RestoreError(Exception):
    """A database could not be restored. Nothing was added or left behind."""


def is_newer_than_this_version(entry: DatabaseEntry) -> bool:
    """True if the backup's schema is newer than this OpenSAK can open."""
    from opensak.db.database import SCHEMA_VERSION
    return entry.schema_version is not None and entry.schema_version > SCHEMA_VERSION


def restore_database(
    backup_set: BackupSet,
    entry: DatabaseEntry,
    progress: Optional[ProgressCallback] = None,
    *,
    today: Optional[date] = None,
) -> "DatabaseInfo":
    """
    Restore *entry* from *backup_set* as a new database and return it.

    The database is copied into the database folder under a new name
    ("<name> (restored YYYY-MM-DD)", made unique if needed), checked with
    ``PRAGMA integrity_check`` before it is moved into place, and added to
    the database list.

    Raises RestoreError if the database can't be restored — missing from
    the set, from a newer OpenSAK, or damaged. *progress* may raise to
    cancel; the exception propagates unchanged. In every failing case
    nothing is added and no file is left behind.
    """
    from opensak.db.database import SCHEMA_VERSION
    from opensak.db.manager import get_db_manager
    from opensak.lang import tr

    source = _entry_path(backup_set, entry)
    if not source.is_file():
        raise RestoreError(f"{entry.file} is missing from the backup")

    actual_version = _user_version(source)
    if actual_version is None:
        raise RestoreError(f"{entry.name} is not a readable database")
    if actual_version > SCHEMA_VERSION:
        raise RestoreError(
            f"{entry.name} was backed up from OpenSAK "
            f"{backup_set.opensak_version or 'a newer version'}, which uses a "
            f"newer database format; update OpenSAK to restore it"
        )

    manager = get_db_manager()
    today = today or date.today()
    base = tr("restore_db_name", name=entry.name, date=today.isoformat())
    name, target = _free_name_and_path(manager, base)

    try:
        snapshot_database(source, target, progress)   # verify=True: integrity
    except SnapshotError as exc:
        raise RestoreError(f"{entry.name} could not be restored: {exc}") from exc

    try:
        info = manager.add_existing(target, name)
    except BaseException:
        _remove_database_file(target)
        raise
    logger.info("restore: %s from %s → %s", entry.name, backup_set.path.name, target)
    return info


def restore_missing_filter_profiles(backup_set: BackupSet) -> list[str]:
    """
    Copy the backup's filter profiles that no longer exist. Profiles that
    exist today — also ones changed since the backup — are left untouched.
    Returns the names of the profiles that were added.
    """
    from opensak.config import get_app_data_dir

    src_dir = backup_set.path / SETTINGS_DIRNAME / "filters"
    if not src_dir.is_dir():
        return []
    dst_dir = get_app_data_dir() / "filters"
    added: list[str] = []
    for src in sorted(src_dir.glob("*.json")):
        dst = dst_dir / src.name
        if dst.exists():
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        added.append(src.stem)
    if added:
        logger.info("restore: added filter profiles %s", ", ".join(added))
    return added


# ── Helpers ───────────────────────────────────────────────────────────────────

def _entry_path(backup_set: BackupSet, entry: DatabaseEntry) -> Path:
    """The database file inside the set — never anywhere outside it."""
    root = backup_set.path.resolve()
    path = (backup_set.path / entry.file).resolve()
    if not path.is_relative_to(root):
        raise RestoreError(f"Invalid file name in the backup: {entry.file}")
    return path


def _free_name_and_path(manager: "DatabaseManager", base: str) -> tuple[str, Path]:
    """A display name and file path that are both still free."""
    from opensak.db.manager import DatabaseManager

    names = {db.name for db in manager.databases}
    paths = {Path(db.path) for db in manager.databases}
    name, n = base, 1
    while True:
        path = DatabaseManager.default_path_for(name)
        if name not in names and path not in paths and not path.exists():
            return name, path
        n += 1
        name = f"{base} ({n})"


def _user_version(path: Path) -> Optional[int]:
    try:
        conn = sqlite3.connect(str(path), timeout=30)
        try:
            conn.execute("PRAGMA query_only = ON")
            return int(conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _remove_database_file(path: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            Path(str(path) + suffix).unlink(missing_ok=True)
        except OSError:
            pass

"""
src/opensak/backup/premigration.py — one-time backup before a schema
migration (issue #549).

The first time OpenSAK opens a database whose schema is behind
``SCHEMA_VERSION``, ``init_db()`` migrates it in place — a one-way change.
Before that happens, ``backup_before_migration()`` saves a snapshot of the
untouched database, so the old database can be restored by hand if a
migration goes wrong, or if the user wants to go back to an older OpenSAK
build.

Where the copy goes (#988): into ``<backup folder>/pre-migration/`` once the
user has set a backup folder (Welcome Wizard, Settings, or a backup dialog)
and it can be used right now; otherwise into a ``backups`` folder next to the
database, as before. An unavailable backup folder — an external drive that
isn't connected — never blocks a migration, it just falls back.

The backup is best-effort: it never raises and never blocks the migration.
If it can't be made (not enough disk space, no write access, …) a warning
is logged and the migration goes ahead as before.

Backups that were made are queued as notices, so the GUI can tell the user
where the file is (``take_notices()``) instead of it being a silent
background action.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from opensak.backup.snapshot import snapshot_database

logger = logging.getLogger(__name__)

# Folder, next to the database file, where pre-migration backups are kept
# when no backup folder is set.
BACKUP_DIRNAME = "backups"

# Subfolder of the user's backup folder for pre-migration backups (#988).
# Never touched by backup-set rotation, which only handles OpenSAK-backup-*.
BACKUP_FOLDER_SUBDIR = "pre-migration"

# Extra free space required on top of the database's own size, so a backup
# never fills the disk to the last byte right before a migration needs room.
_FREE_SPACE_MARGIN = 50 * 1024 * 1024


@dataclass(frozen=True)
class PreMigrationBackup:
    """A backup made before migrating *db_path* from schema *from_version*."""

    db_path: Path
    backup_path: Path
    from_version: int


_notices: list[PreMigrationBackup] = []
_notices_lock = threading.Lock()  # init_db() can also run off the GUI thread


def backup_path_for(db_path: Path, from_version: int) -> Path:
    """Where the pre-migration backup of *db_path* at *from_version* goes."""
    suffix = db_path.suffix or ".db"
    name = f"{db_path.stem}.pre-migration-schema{from_version}{suffix}"
    return db_path.parent / BACKUP_DIRNAME / name


def backup_folder_path_for(db_path: Path, from_version: int, folder: Path) -> Path:
    """
    Where the pre-migration backup goes inside the backup *folder* (#988).

    One backup folder can hold copies from databases in different places
    that share a file name (two ``Default.db``), so the name carries a short
    id: the database's own ``db_uuid`` when it has one (it survives a move
    and is kept by a restore), else a hash of its path. Both are the same on
    every attempt, so the "already backed up" check keeps working.
    """
    suffix = db_path.suffix or ".db"
    name = (
        f"{db_path.stem}.{_short_id(db_path)}"
        f".pre-migration-schema{from_version}{suffix}"
    )
    return Path(folder) / BACKUP_FOLDER_SUBDIR / name


def pending_schema_version(db_path: Path, schema_version: int) -> Optional[int]:
    """
    Return the schema version of the existing database at *db_path* if it
    is behind *schema_version* (i.e. opening it will migrate it), else None.

    None for: a missing or empty file (a new database — nothing to
    protect), a database without tables, a database already at or past
    *schema_version*, and anything SQLite can't read (init_db() reports
    that itself, with a proper error, right after).
    """
    try:
        if not db_path.is_file() or db_path.stat().st_size == 0:
            return None
        conn = sqlite3.connect(str(db_path), timeout=30)
        try:
            conn.execute("PRAGMA query_only = ON")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            tables = conn.execute(
                "SELECT count(*) FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return None
    if tables == 0 or version >= schema_version:
        return None
    return int(version)


def backup_before_migration(db_path: Path, schema_version: int) -> Optional[Path]:
    """
    Snapshot *db_path* into ``backups/`` if opening it is about to migrate
    its schema. Returns the backup path, or None if no backup was made.

    Never raises: any problem is logged, and the caller carries on with the
    migration exactly as it would without this function.

    If a backup for the same starting schema version already exists — for
    instance from an earlier attempt where the migration then failed part
    way — it is kept as it is: that one is the cleanest copy.
    """
    try:
        db_path = Path(db_path)
        from_version = pending_schema_version(db_path, schema_version)
        if from_version is None:
            return None

        beside = backup_path_for(db_path, from_version)
        folder = _usable_backup_folder()
        in_folder = (
            backup_folder_path_for(db_path, from_version, folder)
            if folder is not None else None
        )
        for existing in (beside, in_folder):
            if existing is not None and existing.exists():
                logger.info(
                    "pre-migration backup: keeping existing %s (schema %s)",
                    existing, from_version,
                )
                return None
        target = in_folder if in_folder is not None else beside

        needed = _database_size(db_path) + _FREE_SPACE_MARGIN
        free = shutil.disk_usage(_nearest_existing(target.parent)).free
        if free < needed:
            logger.warning(
                "pre-migration backup of %s skipped: not enough free disk "
                "space (%d MB needed, %d MB free) — migrating without a backup",
                db_path, needed // 2**20, free // 2**20,
            )
            return None

        logger.info(
            "pre-migration backup: %s is at schema %s (target %s), "
            "saving a copy to %s",
            db_path, from_version, schema_version, target,
        )
        # verify=False: the integrity check would roughly double the time
        # this adds to opening a large database, at a moment where the user
        # is waiting (often on the splash screen). The backup API copies
        # through SQLite itself, so the copy is as sound as the source.
        snapshot_database(db_path, target, verify=False)
    except Exception:
        logger.warning(
            "pre-migration backup of %s failed — migrating without a backup",
            db_path, exc_info=True,
        )
        return None

    with _notices_lock:
        _notices.append(PreMigrationBackup(db_path, target, from_version))
    return target


def take_notices() -> list[PreMigrationBackup]:
    """Return and clear the backups made since the last call."""
    with _notices_lock:
        notices = list(_notices)
        _notices.clear()
    return notices


def clear_notices() -> None:
    """Forget pending notices (used by tests)."""
    with _notices_lock:
        _notices.clear()


def _usable_backup_folder() -> Optional[Path]:
    """
    The backup folder the user has set, if it can take a backup right now;
    else None (#988). Only an explicitly set folder counts: with none set,
    the copy stays next to the database as it always did, rather than
    quietly appearing in Documents.
    """
    try:
        from opensak.backup.backupset import (
            BACKUP_DIR_KEY, BackupError, folder_available, validate_backup_dir,
        )
        from opensak.settings_store import get_store
        value = get_store().get(BACKUP_DIR_KEY)
        if not value:
            return None
        folder = Path(value)
        if not folder_available(folder):
            logger.info(
                "pre-migration backup: backup folder %s not available — "
                "using the folder next to the database", folder,
            )
            return None
        try:
            validate_backup_dir(folder)
        except BackupError:
            return None
        return folder
    except Exception:  # never let the lookup block a migration
        logger.warning("pre-migration backup: could not read the backup folder",
                       exc_info=True)
        return None


def _short_id(db_path: Path) -> str:
    """First 8 characters of the database's db_uuid, else of a path hash."""
    try:
        from opensak.db.db_settings import UUID_KEY, read_file
        value = read_file(db_path).get(UUID_KEY)
        if isinstance(value, str) and len(value) >= 8:
            return value[:8]
    except Exception:  # an old database without the settings table, or unreadable
        pass
    try:
        key = str(db_path.resolve())
    except OSError:
        key = str(db_path.absolute())
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def _nearest_existing(path: Path) -> Path:
    """*path*, or its nearest parent that exists (for a free-space check)."""
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return path


def _database_size(db_path: Path) -> int:
    """Size of *db_path* plus its -wal file, if any."""
    size = db_path.stat().st_size
    wal = Path(str(db_path) + "-wal")
    if wal.exists():
        size += wal.stat().st_size
    return size

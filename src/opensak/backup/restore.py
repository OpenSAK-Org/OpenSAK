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

import contextlib
import logging
import shutil
import sqlite3
import zipfile
from datetime import date
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Callable, Iterator, Optional

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

# Extra free space needed when unpacking from a compressed set (#989).
_EXTRACT_MARGIN = 50 * 1024 * 1024
_EXTRACT_CHUNK = 1024 * 1024


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

    manager = get_db_manager()
    today = today or date.today()
    base = tr("restore_db_name", name=entry.name, date=today.isoformat())
    name, target = _free_name_and_path(manager, base)

    with _source_file(backup_set, entry, target, progress) as (source, copy_progress):
        actual_version = _user_version(source)
        if actual_version is None:
            raise RestoreError(f"{entry.name} is not a readable database")
        if actual_version > SCHEMA_VERSION:
            raise RestoreError(
                f"{entry.name} was backed up from OpenSAK "
                f"{backup_set.opensak_version or 'a newer version'}, which uses a "
                f"newer database format; update OpenSAK to restore it"
            )
        try:
            snapshot_database(source, target, copy_progress)   # verify=True: integrity
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

    dst_dir = get_app_data_dir() / "filters"
    added: list[str] = []

    if backup_set.compressed:
        # #989: the profiles are members settings/filters/<name>.json.
        prefix = f"{SETTINGS_DIRNAME}/filters/"
        with zipfile.ZipFile(backup_set.path) as zf:
            for member in sorted(zf.namelist()):
                rest = member[len(prefix):] if member.startswith(prefix) else ""
                if not rest.endswith(".json") or "/" in rest or "\\" in rest:
                    continue
                dst = dst_dir / rest
                if dst.exists():
                    continue
                dst_dir.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(zf.read(member))
                added.append(dst.stem)
        if added:
            logger.info("restore: added filter profiles %s", ", ".join(added))
        return added

    src_dir = backup_set.path / SETTINGS_DIRNAME / "filters"
    if not src_dir.is_dir():
        return []
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

@contextlib.contextmanager
def _source_file(
    backup_set: BackupSet,
    entry: DatabaseEntry,
    target: Path,
    progress: Optional[ProgressCallback],
) -> Iterator[tuple[Path, Optional[ProgressCallback]]]:
    """
    The database file to restore from, and the progress callback to copy it
    with. For a folder set that is the file inside the set. For a compressed
    set (#989) the database is first unpacked next to *target* — on the disk
    it is restored to — and the unpacked copy is removed afterwards, also on
    failure or cancel; unpacking and copying then each count for half.
    """
    if not backup_set.compressed:
        source = _entry_path(backup_set, entry)
        if not source.is_file():
            raise RestoreError(f"{entry.file} is missing from the backup")
        yield source, progress
        return

    member = _zip_member(entry)
    unpacked = target.with_name(target.name + ".unpacking")
    try:
        with zipfile.ZipFile(backup_set.path) as zf:
            try:
                info = zf.getinfo(member)
            except KeyError as exc:
                raise RestoreError(f"{entry.file} is missing from the backup") from exc
            _check_room_to_unpack(target.parent, info.file_size, entry)
            _unpack(zf, info, unpacked, progress)
        yield unpacked, _second_half(progress)
    except (OSError, zipfile.BadZipFile) as exc:
        raise RestoreError(f"{entry.name} could not be unpacked: {exc}") from exc
    finally:
        _remove_database_file(unpacked)


def _zip_member(entry: DatabaseEntry) -> str:
    """The member name of *entry* — never anything outside the set's own tree."""
    member = PurePosixPath(entry.file)
    if member.is_absolute() or ".." in member.parts or "\\" in entry.file:
        raise RestoreError(f"Invalid file name in the backup: {entry.file}")
    return str(member)


def _check_room_to_unpack(folder: Path, size: int, entry: DatabaseEntry) -> None:
    """Unpacked copy plus the restored database must both fit (#989)."""
    folder.mkdir(parents=True, exist_ok=True)
    needed = 2 * size + _EXTRACT_MARGIN
    free = shutil.disk_usage(folder).free
    if free < needed:
        raise RestoreError(
            f"Not enough free space in {folder} to restore {entry.name}: about "
            f"{needed // 2**20 + 1} MB needed, {free // 2**20} MB free"
        )


def _unpack(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path,
    progress: Optional[ProgressCallback],
) -> None:
    size = info.file_size
    done = 0
    with zf.open(info) as src, dest.open("wb") as dst:
        while True:
            chunk = src.read(_EXTRACT_CHUNK)
            if not chunk:
                break
            dst.write(chunk)
            done += len(chunk)
            if progress is not None:
                progress(done, 2 * size)   # first half of this database


def _second_half(progress: Optional[ProgressCallback]) -> Optional[ProgressCallback]:
    if progress is None:
        return None

    def _report(done: int, pages: int) -> None:
        progress(pages + done, 2 * pages)
    return _report


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

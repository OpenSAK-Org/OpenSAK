"""
src/opensak/backup/macros_restore.py — restore the Lua macros from a backup set (#1137).

Part of the backup epic (#942); design: docs/architecture/backup.md
("Macros"). What a set holds is described in macros_backup.py.

A macros restore works on the user's macros folder as it is now, and never
silently overwrites anything:

1. **Plan** (plan_macro_restore): every file of the set's ``macros/``, and
   every macro database (``macro_databases/``) that belonged in the macros
   folder, is matched with the place it goes in today's macros folder.
   It is *new* (nothing there), *same* (the file there has the same bytes)
   or a *conflict* (something different is there; a macro database that
   exists is always a conflict, since two databases can't be compared
   cheaply). A macro database that lived outside the macros folder is not
   restored: a backup set must never be able to write anywhere on the disk.
   Its file is in the set for copying back by hand.
2. **Restore** (restore_macros): new files are copied, the conflicts the
   user chose to replace are replaced — the current file is first kept as
   ``<name>.<YYYYMMDD-HHMMSS>.bak`` next to it, as "Restore example" does —
   and the others are left alone.
3. **Approvals**: what the user allowed each macro "always" (write, folders,
   web sites; macro.db_access, #1091) is stored in opensak.json. For every
   macro file that now holds the backup's code — restored, or the same
   already — the backup's record is taken over, with its path moved to
   today's macros folder. Records are keyed on the code, so this never
   approves anything other than exactly what was approved when the backup
   was taken. This happens without "Also restore settings", which restores
   everything else in opensak.json (at the next start).

This module has no UI. Messages are technical English; the restore dialog
shows them as they are.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass, field, replace as _replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Optional

from opensak.backup.backupset import SETTINGS_DIRNAME, BackupSet
from opensak.backup.macros_backup import MACROS_DIRNAME, is_sqlite_file, macros_root
from opensak.backup.snapshot import SnapshotError, snapshot_database

logger = logging.getLogger(__name__)

_SETTINGS_FILE = "opensak.json"
_UNPACK_SUFFIX = ".unpacking"
_SQLITE_SUFFIXES = (".db", ".db3", ".sqlite", ".sqlite3")


class MacroRestoreError(Exception):
    """The macros could not be restored at all (e.g. an unreadable set)."""


@dataclass(frozen=True)
class MacroRestoreItem:
    """One file to restore into the macros folder."""

    rel: str                  # relative to the macros folder, "/"-separated
    source: str               # the file inside the set, "/"-separated
    target: Path              # where it goes
    size_bytes: int = 0
    database: bool = False    # a macro database (snapshotted, not copied)


@dataclass
class MacroRestorePlan:
    """What restoring the macros of *backup_set* would do."""

    backup_set: BackupSet
    root: Path                                   # today's macros folder
    new: list[MacroRestoreItem] = field(default_factory=list)
    same: list[MacroRestoreItem] = field(default_factory=list)
    conflicts: list[MacroRestoreItem] = field(default_factory=list)
    # Macro databases that are not restored, with the path they had.
    outside: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.new or self.same or self.conflicts)


@dataclass
class MacroRestoreResult:
    """What restore_macros() did; paths relative to the macros folder."""

    restored: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)   # (rel, reason)
    outside: list[str] = field(default_factory=list)
    approvals: int = 0


def has_macros(backup_set: BackupSet) -> bool:
    """True if *backup_set* has macros (or macro databases) to restore."""
    return backup_set.macros is not None or bool(backup_set.macro_databases)


# ── Plan ──────────────────────────────────────────────────────────────────────

def plan_macro_restore(
    backup_set: BackupSet, root: Optional[Path] = None,
) -> MacroRestorePlan:
    """Match the set's macros with today's macros folder *root* (default:
    the user's). Raises MacroRestoreError if the set can't be read."""
    root = (Path(root) if root is not None else macros_root()).resolve()
    plan = MacroRestorePlan(backup_set, root)
    try:
        members = _macro_members(backup_set)
    except (OSError, zipfile.BadZipFile) as exc:
        raise MacroRestoreError(f"The macros in the backup can't be read: {exc}") from exc

    for rel, size in members:
        target = _target(root, rel)
        if target is None or is_sqlite_file(PurePosixPath(rel).name):
            logger.warning("restore: macros: ignoring %r in the backup", rel)
            continue
        item = MacroRestoreItem(rel, f"{MACROS_DIRNAME}/{rel}", target, size)
        if not os.path.lexists(target):
            plan.new.append(item)
        elif target.is_file() and not target.is_symlink() and _same_content(
            backup_set, item.source, target,
        ):
            plan.same.append(item)
        else:
            plan.conflicts.append(item)

    original_root = _original_root(backup_set, root)
    taken = {os.path.normcase(str(i.target)) for i in plan.new + plan.same + plan.conflicts}
    for entry in backup_set.macro_databases:
        db_rel = _rel_to(original_root, entry.path)
        db_target = _target(root, db_rel) if db_rel is not None else None
        if (
            db_target is None or db_rel is None
            or not PurePosixPath(db_rel).name.lower().endswith(_SQLITE_SUFFIXES)
            or os.path.normcase(str(db_target)) in taken
        ):
            plan.outside.append(entry.path)
            continue
        taken.add(os.path.normcase(str(db_target)))
        item = MacroRestoreItem(db_rel, entry.file, db_target, entry.size_bytes, database=True)
        (plan.conflicts if os.path.lexists(db_target) else plan.new).append(item)

    for items in (plan.new, plan.same, plan.conflicts):
        items.sort(key=lambda i: i.rel.lower())
    return plan


# ── Restore ───────────────────────────────────────────────────────────────────

def restore_macros(
    plan: MacroRestorePlan,
    replace: Iterable[str] = (),
    *,
    now: Optional[datetime] = None,
) -> MacroRestoreResult:
    """
    Carry out *plan*: copy the new files, replace the conflicts whose rel
    path is in *replace* (keeping the current file as a timestamped .bak)
    and leave the other conflicts as they are. Then take over the backup's
    approvals for the macros that now hold its code. A file that fails is
    reported in the result; the others are still restored.
    """
    chosen = set(replace)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    result = MacroRestoreResult(outside=list(plan.outside))
    result.unchanged = [i.rel for i in plan.same]
    in_place: list[MacroRestoreItem] = list(plan.same)

    for item in plan.new:
        if _restore_item(plan, item, None, result):
            result.restored.append(item.rel)
            in_place.append(item)
    for item in plan.conflicts:
        if item.rel not in chosen:
            result.kept.append(item.rel)
            continue
        if _restore_item(plan, item, stamp, result):
            result.replaced.append(item.rel)
            in_place.append(item)

    result.approvals = _restore_approvals(plan, in_place)
    logger.info(
        "restore: macros from %s: %d restored, %d replaced, %d kept, %d unchanged, "
        "%d failed, %d approval(s)",
        plan.backup_set.path.name, len(result.restored), len(result.replaced),
        len(result.kept), len(result.unchanged), len(result.failed), result.approvals,
    )
    return result


def _restore_item(
    plan: MacroRestorePlan, item: MacroRestoreItem, stamp: Optional[str],
    result: MacroRestoreResult,
) -> bool:
    try:
        if stamp is not None:
            _keep_current(item, stamp)
        if item.database:
            _restore_database(plan.backup_set, item)
            from opensak.backup.backupset import remember_macro_database
            remember_macro_database(item.target)
        else:
            _restore_file(plan.backup_set, item)
    except (OSError, SnapshotError, sqlite3.Error, zipfile.BadZipFile,
            MacroRestoreError) as exc:
        logger.warning("restore: macros: %s not restored — %s", item.rel, exc)
        result.failed.append((item.rel, str(exc)))
        return False
    return True


def _keep_current(item: MacroRestoreItem, stamp: str) -> None:
    """Move the current file out of the way as <name>.<stamp>.bak."""
    target = item.target
    if not target.is_file() or target.is_symlink():
        raise MacroRestoreError("something other than a file is in the way")
    bak = target.with_name(f"{target.name}.{stamp}.bak")
    n = 1
    while os.path.lexists(bak):
        n += 1
        bak = target.with_name(f"{target.name}.{stamp}-{n}.bak")
    if item.database:
        # A database is copied through SQLite (it may have a -wal), and then
        # emptied of its -wal so the restored file can take its place.
        snapshot_database(target, bak)
        _checkpoint(target)
    else:
        os.replace(target, bak)


def _checkpoint(path: Path) -> None:
    """Write *path*'s -wal into it and close, so no -wal is left that would
    be applied to the file that replaces it."""
    conn = sqlite3.connect(str(path), timeout=5)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    wal = Path(str(path) + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        raise MacroRestoreError("the database is in use")
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def _restore_file(backup_set: BackupSet, item: MacroRestoreItem) -> None:
    item.target.parent.mkdir(parents=True, exist_ok=True)
    tmp = item.target.with_name(item.target.name + _UNPACK_SUFFIX)
    try:
        if backup_set.compressed:
            with zipfile.ZipFile(backup_set.path) as zf:
                with zf.open(_member(item.source)) as src, tmp.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
            shutil.copy2(_set_path(backup_set, item.source), tmp)
        os.replace(tmp, item.target)
    finally:
        tmp.unlink(missing_ok=True)


def _restore_database(backup_set: BackupSet, item: MacroRestoreItem) -> None:
    """Snapshot the macro database into place (checked with integrity_check)."""
    item.target.parent.mkdir(parents=True, exist_ok=True)
    if not backup_set.compressed:
        snapshot_database(_set_path(backup_set, item.source), item.target)
        return
    unpacked = item.target.with_name(item.target.name + _UNPACK_SUFFIX)
    try:
        with zipfile.ZipFile(backup_set.path) as zf:
            with zf.open(_member(item.source)) as src, unpacked.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        snapshot_database(unpacked, item.target)
    finally:
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(str(unpacked) + suffix).unlink(missing_ok=True)


# ── Approvals ─────────────────────────────────────────────────────────────────

def _restore_approvals(plan: MacroRestorePlan, in_place: list[MacroRestoreItem]) -> int:
    """Take over the backup's approvals of the macros in *in_place*."""
    from opensak.macro.db_access import STORE_KEY, Approval, restore_approvals

    lua = {i.rel: i.target for i in in_place if not i.database}
    if not lua:
        return 0
    try:
        raw = _backup_settings(plan.backup_set).get(STORE_KEY)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        logger.warning("restore: macros: the backup's approvals can't be read — %s", exc)
        return 0
    if not isinstance(raw, list):
        return 0
    original_root = _original_root(plan.backup_set, plan.root)
    records = []
    for data in raw:
        record = Approval.from_dict(data) if isinstance(data, dict) else None
        if record is None:
            continue
        rel = _rel_to(original_root, record.path)
        if rel is None or rel not in lua:
            continue
        records.append(_replace(record, path=str(lua[rel])))
    return restore_approvals(records)


def _backup_settings(backup_set: BackupSet) -> dict[str, Any]:
    """The backup's opensak.json ({} if it has none)."""
    if not backup_set.settings:
        return {}
    member = f"{SETTINGS_DIRNAME}/{_SETTINGS_FILE}"
    if backup_set.compressed:
        with zipfile.ZipFile(backup_set.path) as zf:
            try:
                text = zf.read(member).decode("utf-8")
            except KeyError:
                return {}
    else:
        path = backup_set.path / SETTINGS_DIRNAME / _SETTINGS_FILE
        if not path.is_file():
            return {}
        text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _macro_members(backup_set: BackupSet) -> list[tuple[str, int]]:
    """(path relative to macros/, size) of every file in the set's macros/."""
    if backup_set.macros is None:
        return []
    prefix = f"{MACROS_DIRNAME}/"
    if backup_set.compressed:
        with zipfile.ZipFile(backup_set.path) as zf:
            return sorted(
                (info.filename[len(prefix):], info.file_size)
                for info in zf.infolist()
                if info.filename.startswith(prefix) and not info.is_dir()
                and len(info.filename) > len(prefix)
            )
    folder = backup_set.path / MACROS_DIRNAME
    if not folder.is_dir():
        return []
    found = []
    for path in folder.rglob("*"):
        if path.is_file() and not path.is_symlink():
            found.append((path.relative_to(folder).as_posix(), path.stat().st_size))
    return sorted(found)


def _target(root: Path, rel: str) -> Optional[Path]:
    """Where *rel* goes in the macros folder *root* — None for anything
    that would end up outside it."""
    if not rel or "\\" in rel:
        return None
    parts = PurePosixPath(rel).parts
    if PurePosixPath(rel).is_absolute() or any(p in ("", ".", "..") for p in parts):
        return None
    target = root.joinpath(*parts)
    try:
        resolved = target.parent.resolve() / target.name
    except OSError:
        return None
    if not resolved.is_relative_to(root):
        return None
    return resolved


def _original_root(backup_set: BackupSet, root: Path) -> Path:
    """The macros folder the set was backed up from (today's, for a set
    that doesn't say)."""
    if backup_set.macros is not None and backup_set.macros.path:
        return Path(backup_set.macros.path)
    return root


def _rel_to(original_root: Path, path: str) -> Optional[str]:
    """*path* relative to *original_root*, "/"-separated; None outside it.
    Compared as written and without case where the platform ignores case,
    since the path may come from another machine."""
    root_s = os.path.normcase(os.path.normpath(str(original_root)))
    path_s = os.path.normcase(os.path.normpath(path))
    if not path_s.startswith(root_s.rstrip(os.sep) + os.sep):
        return None
    rel = os.path.normpath(path)[len(os.path.normpath(str(original_root)).rstrip(os.sep)) + 1:]
    return PurePosixPath(*Path(rel).parts).as_posix()


def _member(file: str) -> str:
    member = PurePosixPath(file)
    if member.is_absolute() or ".." in member.parts or "\\" in file:
        raise MacroRestoreError(f"Invalid file name in the backup: {file}")
    return str(member)


def _set_path(backup_set: BackupSet, file: str) -> Path:
    root = backup_set.path.resolve()
    path = (backup_set.path / _member(file)).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise MacroRestoreError(f"{file} is missing from the backup")
    return path


def _same_content(backup_set: BackupSet, source: str, target: Path) -> bool:
    try:
        current = target.read_bytes()
        if backup_set.compressed:
            with zipfile.ZipFile(backup_set.path) as zf:
                info = zf.getinfo(_member(source))
                if info.file_size != len(current):
                    return False
                return zf.read(info) == current
        path = _set_path(backup_set, source)
        return path.stat().st_size == len(current) and path.read_bytes() == current
    except (OSError, KeyError, MacroRestoreError, zipfile.BadZipFile):
        return False


__all__ = [
    "MacroRestoreError", "MacroRestoreItem", "MacroRestorePlan",
    "MacroRestoreResult", "has_macros", "plan_macro_restore", "restore_macros",
]

"""
src/opensak/backup/settings_restore.py — restore the settings from a backup
set (#987, sub-issue 6 of #942).

A settings restore is opt-in and happens in two steps:

1. **Now** (``stage_settings_restore``): the current settings are saved as a
   settings-only *safety* set, so the restore can be undone, and the backup's
   settings are copied into ``<install folder>/settings-restore-pending/``.
2. **At the next start** (``apply_pending_settings_restore``, called by
   app.main() before anything reads the settings): the pending settings are
   applied and the pending folder is removed.

Why not apply them straight away: OpenSAK keeps its settings in memory and
writes the whole of opensak.json whenever one value changes, and other parts
(window layout, column views) write on exit. Anything restored into the
running app could be overwritten before it ever took effect. Applying them
before the next start, when nothing has read them yet, avoids that — and
matches OpenSAK's no-automatic-restart design (the user closes and reopens).

What is restored: ``filters/``, ``column_views/`` and ``icons/`` (replaced by
the backup's copies, where the backup has them) and opensak.json, merged:
everything comes from the backup except the keys in ``_KEEP_PREFIXES``,
which describe this machine or the present — the database list and folder,
the backup settings, window placement, last-used paths, the update state and
OpenSAK's own internal flags. Restoring those would make the databases
restored since vanish again, point at folders that don't exist here, or put
windows off-screen.
"""

from __future__ import annotations

import json
import logging
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from opensak.backup.backupset import (
    KIND_SAFETY,
    SETTINGS_DIRNAME,
    BackupError,
    BackupSet,
    write_backup_set,
)

logger = logging.getLogger(__name__)

PENDING_DIRNAME = "settings-restore-pending"
FAILED_DIRNAME = "settings-restore-failed"
MARKER_NAME = "restore.json"
SETTINGS_FILE = "opensak.json"
SETTINGS_DIRS = ("filters", "column_views", "icons")

# opensak.json keys kept from the current settings, never taken from the
# backup. A key is kept if it starts with one of these.
_KEEP_PREFIXES = (
    "databases.",   # the database list, active database and folder
    "backup.",      # backup folder, on-exit choice, keep count, compression, state
    "window.",      # geometry and splitters belong to this machine's screens
    "paths.",       # last-used folders on this machine
    "updates.",     # skipped version, beta notifications
    "_",            # OpenSAK's internal flags (wizard done, migrations done)
)


class SettingsRestoreError(Exception):
    """The settings could not be restored. Nothing was changed."""


def can_restore_settings(backup_set: BackupSet) -> Optional[str]:
    """None if *backup_set*'s settings can be restored, else the reason why not."""
    if not backup_set.settings:
        return "This backup does not contain settings."
    if _is_newer_than_this_version(backup_set.opensak_version):
        return (
            f"These settings come from OpenSAK {backup_set.opensak_version}, "
            f"which is newer than this version."
        )
    return None


def stage_settings_restore(backup_set: BackupSet) -> BackupSet:
    """
    Save the current settings as a safety set, then put the backup's
    settings in place to be applied at the next start. Returns the safety
    set. Raises SettingsRestoreError if anything fails; a half-made pending
    folder is removed, and an earlier pending restore is left as it was.
    """
    from opensak.settings_store import get_install_dir

    reason = can_restore_settings(backup_set)
    if reason:
        raise SettingsRestoreError(reason)

    try:
        safety = write_backup_set([], KIND_SAFETY, compress=False).backup_set
    except BackupError as exc:
        raise SettingsRestoreError(
            f"Your current settings could not be saved first, so nothing was "
            f"changed: {exc}"
        ) from exc

    install = get_install_dir()
    pending = install / PENDING_DIRNAME
    partial = install / (PENDING_DIRNAME + ".partial")
    try:
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True)
        _copy_settings_out(backup_set, partial)
        (partial / MARKER_NAME).write_text(json.dumps({
            "from_set": backup_set.path.name,
            "safety_set": safety.path.name,
            "staged": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }, indent=2) + "\n", encoding="utf-8")
        shutil.rmtree(pending, ignore_errors=True)   # a newer choice wins
        partial.rename(pending)
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        shutil.rmtree(partial, ignore_errors=True)
        raise SettingsRestoreError(f"The settings could not be prepared: {exc}") from exc

    logger.info(
        "settings restore: staged %s (safety set %s) — applied at next start",
        backup_set.path.name, safety.path.name,
    )
    return safety


def pending_settings_restore() -> bool:
    """True if a settings restore is waiting for the next start."""
    from opensak.settings_store import get_install_dir
    return (get_install_dir() / PENDING_DIRNAME / MARKER_NAME).is_file()


APPLIED = "applied"
FAILED = "failed"


def apply_pending_settings_restore() -> Optional[str]:
    """
    Apply a staged settings restore, if there is one. Called at startup,
    before anything reads the settings. Returns APPLIED, FAILED, or None if
    nothing was waiting. Never raises: on failure the pending folder is kept as
    ``settings-restore-failed`` for inspection, and OpenSAK starts with the
    settings it had — the safety set holds them as well.
    """
    from opensak.settings_store import get_install_dir, get_store

    install = get_install_dir()
    pending = install / PENDING_DIRNAME
    if not (pending / MARKER_NAME).is_file():
        return None

    try:
        store = get_store()
        current_path = store.settings_path()
        current = _read_json(current_path)
        restored = _read_json(pending / SETTINGS_FILE)
        merged = merge_settings(current, restored)

        for name in SETTINGS_DIRS:
            src = pending / name
            if src.is_dir():
                dst = install / name
                shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst)

        from opensak.settings_store import _atomic_write
        _atomic_write(current_path, merged)
        store.reload()   # re-read from disk on next use
        shutil.rmtree(pending, ignore_errors=True)
        logger.info("settings restore: applied (%d keys)", len(merged))
        return APPLIED
    except Exception:
        logger.exception("settings restore: could not be applied")
        failed = install / FAILED_DIRNAME
        shutil.rmtree(failed, ignore_errors=True)
        try:
            pending.rename(failed)
        except OSError:
            shutil.rmtree(pending, ignore_errors=True)
        return FAILED


def merge_settings(current: dict[str, Any], restored: dict[str, Any]) -> dict[str, Any]:
    """
    opensak.json after the restore: the backup's values, except the keys
    that belong to this machine or the present, which keep their current
    values (or stay absent if they are absent now).
    """
    merged = {k: v for k, v in restored.items() if not _kept(k)}
    merged.update({k: v for k, v in current.items() if _kept(k)})
    return merged


# ── Helpers ───────────────────────────────────────────────────────────────────

def _kept(key: str) -> bool:
    return key.startswith(_KEEP_PREFIXES)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} does not hold settings")
    return data


def _copy_settings_out(backup_set: BackupSet, target: Path) -> None:
    """Copy the allow-listed settings of *backup_set* into *target*."""
    allowed = (SETTINGS_FILE, *SETTINGS_DIRS)
    if backup_set.compressed:
        prefix = SETTINGS_DIRNAME + "/"
        with zipfile.ZipFile(backup_set.path) as zf:
            for member in zf.namelist():
                if not member.startswith(prefix) or member.endswith("/"):
                    continue
                rel = PurePosixPath(member[len(prefix):])
                if rel.is_absolute() or ".." in rel.parts or "\\" in member:
                    raise ValueError(f"Invalid file name in the backup: {member}")
                if rel.parts[0] not in allowed:
                    continue
                dest = target.joinpath(*rel.parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(member))
        return

    src_root = backup_set.path / SETTINGS_DIRNAME
    if (src_root / SETTINGS_FILE).is_file():
        shutil.copy2(src_root / SETTINGS_FILE, target / SETTINGS_FILE)
    for name in SETTINGS_DIRS:
        if (src_root / name).is_dir():
            shutil.copytree(src_root / name, target / name)


def _is_newer_than_this_version(version: str) -> bool:
    if not version:
        return False
    from opensak import __version__
    from opensak.updater import _parse_version
    try:
        return _parse_version(version) > _parse_version(__version__)
    except Exception:
        return False

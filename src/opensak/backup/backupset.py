"""
src/opensak/backup/backupset.py — write, list and rotate backup sets (#952).

Part of the backup epic (#942); design: docs/architecture/backup.md.

A *backup set* is one plain folder in the user's backup folder:

    OpenSAK-backup-2026-10-01_1830-auto/
        manifest.json
        databases/      one snapshot (#943) per database
        settings/       opensak.json, filters/, column_views/, icons/

A set is built in ``<name>.partial`` and renamed into place only when it is
complete, manifest last, so a folder with a manifest is always a whole
backup. Credentials (the Geocaching.com token file, keyring passwords) and
machine-specific files (bootstrap.json, logs) are never included.

This module has no UI. Messages in BackupError are technical English; the
dialogs that use this (later sub-issues of #942) translate them for users.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Protocol

from opensak.backup.snapshot import SnapshotError, snapshot_database

logger = logging.getLogger(__name__)

# ── Settings keys and defaults ────────────────────────────────────────────────

BACKUP_DIR_KEY = "backup.dir"
KEEP_AUTO_KEY = "backup.keep_auto"
DEFAULT_KEEP_AUTO = 5
DEFAULT_DIRNAME = "OpenSAK Backups"

# ── Set layout ────────────────────────────────────────────────────────────────

KIND_AUTO = "auto"
KIND_MANUAL = "manual"
_KINDS = (KIND_AUTO, KIND_MANUAL)

SET_PREFIX = "OpenSAK-backup-"
PARTIAL_SUFFIX = ".partial"
MANIFEST_NAME = "manifest.json"
MANIFEST_FORMAT = 1
DATABASES_DIRNAME = "databases"
SETTINGS_DIRNAME = "settings"

# What goes into settings/: opensak.json plus these folders from the install
# folder. Deliberately an allow-list, so credentials and logs can never slip in.
_SETTINGS_FILE = "opensak.json"
_SETTINGS_DIRS = ("filters", "column_views", "icons")

# A set folder OpenSAK made: prefix, timestamp, kind, optional -N suffix.
_SET_NAME_RE = re.compile(
    r"^OpenSAK-backup-\d{4}-\d{2}-\d{2}_\d{4}-(auto|manual)(-\d+)?$"
)

# Free space needed on top of the databases' own size.
_FREE_SPACE_FACTOR = 1.10

# (database name, fraction of that database done, fraction of the whole set)
ProgressCallback = Callable[[str, float, float], None]


class BackupError(Exception):
    """A backup could not be made or read. Nothing half-written is left."""


class NotEnoughSpaceError(BackupError):
    """The backup folder's disk doesn't have room for the backup (#953)."""


class _DatabaseLike(Protocol):
    """What write_backup_set() needs from an entry in the database list."""

    name: str
    path: Path


@dataclass(frozen=True)
class DatabaseEntry:
    """One database inside a backup set, as described by its manifest."""

    name: str
    file: str                       # relative to the set folder
    db_uuid: Optional[str]
    schema_version: Optional[int]
    size_bytes: int


@dataclass(frozen=True)
class BackupSet:
    """A complete backup set on disk."""

    path: Path
    kind: str
    created: datetime               # UTC, timezone-aware
    opensak_version: str
    databases: tuple[DatabaseEntry, ...]
    settings: bool
    format: int


@dataclass(frozen=True)
class BackupResult:
    """What write_backup_set() made, and which databases it had to skip."""

    backup_set: BackupSet
    skipped: tuple[str, ...]        # names of databases whose file is missing


# ── Backup folder ─────────────────────────────────────────────────────────────

def default_backup_dir() -> Path:
    """
    ``<Documents>/OpenSAK Backups``.

    Documents is found from ``Path.home()`` rather than through Qt, the same
    way settings_store does for MSIX installs: QStandardPaths ignores the
    patched home folder in tests, so a test could otherwise write into the
    developer's real Documents folder. On Linux the XDG user-dirs file is
    honoured, so a localized folder such as ~/Dokumenter is used.
    """
    return _documents_dir() / DEFAULT_DIRNAME


def get_backup_dir() -> Path:
    """The configured backup folder, or the default. Not created here."""
    from opensak.settings_store import get_store
    value = get_store().get(BACKUP_DIR_KEY)
    return Path(value) if value else default_backup_dir()


def folder_available(folder: Path) -> bool:
    """
    True when backups can be written to *folder*: it exists, or it is the
    default ``<Documents>/OpenSAK Backups``, which the backup core creates
    on first use (Documents included, if missing). Any other folder was
    picked with Browse and so existed then; if it's gone now it is most
    likely on a drive that isn't connected — creating it would put the
    backups on the wrong disk, or fail on a mount point like /Volumes/USB.

    Moved here from exit_backup_dialog.py in #988, so the pre-migration
    backup (which runs without the GUI) can use it too.
    """
    folder = Path(folder)
    try:
        return folder.is_dir() or folder == default_backup_dir()
    except OSError:
        return False


def set_backup_dir(path: Path) -> None:
    """Validate and store the backup folder (raises BackupError if unsuitable)."""
    from opensak.settings_store import get_store
    path = Path(path)
    validate_backup_dir(path)
    get_store().set(BACKUP_DIR_KEY, str(path))


def validate_backup_dir(
    path: Path,
    *,
    install_dir: Optional[Path] = None,
    db_dir: Optional[Path] = None,
) -> None:
    """
    Raise BackupError if *path* can't hold backups: it must not be (inside)
    the install folder or the database folder. A backup inside what it
    backs up would copy itself, and an uninstall would remove it.

    *install_dir* and *db_dir* default to the folders in use now. The
    Welcome Wizard and Settings pass the folders the user has just chosen
    but not saved yet (#986).
    """
    from opensak.settings_store import get_db_dir, get_install_dir
    candidate = _resolved(Path(path))
    for label, protected in (
        ("install folder", install_dir if install_dir is not None else get_install_dir()),
        ("database folder", db_dir if db_dir is not None else get_db_dir()),
    ):
        root = _resolved(protected)
        if candidate == root or candidate.is_relative_to(root):
            raise BackupError(
                f"The backup folder can't be inside the {label} ({protected})"
            )


def get_keep_auto() -> int:
    """How many automatic sets rotation keeps (at least 1)."""
    from opensak.settings_store import get_store
    try:
        value = int(get_store().get(KEEP_AUTO_KEY, DEFAULT_KEEP_AUTO))
    except (TypeError, ValueError):
        value = DEFAULT_KEEP_AUTO
    return max(1, value)


# ── Writing a set ─────────────────────────────────────────────────────────────

def write_backup_set(
    databases: Iterable[_DatabaseLike],
    kind: str,
    progress: Optional[ProgressCallback] = None,
    *,
    folder: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> BackupResult:
    """
    Back up *databases* and the settings into a new set in *folder*
    (default: the backup folder).

    Databases whose file doesn't exist are skipped and reported in the
    result; the rest of the backup still completes. *progress* may raise to
    cancel: the exception propagates unchanged and nothing is left behind.

    Raises BackupError if the set can't be written (unsuitable folder, not
    enough free space, a database that can't be snapshotted, …).
    """
    if kind not in _KINDS:
        raise ValueError(f"kind must be one of {_KINDS}, not {kind!r}")
    folder = Path(folder) if folder is not None else get_backup_dir()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    validate_backup_dir(folder)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError(f"Cannot create the backup folder: {exc}") from exc

    present: list[tuple[_DatabaseLike, int]] = []   # (database, size in bytes)
    skipped: list[str] = []
    for db in databases:
        if Path(db.path).is_file():
            present.append((db, _database_size(Path(db.path))))
        else:
            skipped.append(db.name)
            logger.warning("backup: database %r skipped — its file is missing", db.name)
    skipped_names = tuple(skipped)

    cleanup_partials(folder)

    total = sum(size for _db, size in present)
    free = shutil.disk_usage(folder).free
    if free < total * _FREE_SPACE_FACTOR:
        raise NotEnoughSpaceError(
            f"Not enough free space in {folder}: about "
            f"{_mb(total * _FREE_SPACE_FACTOR)} MB needed, {_mb(free)} MB free"
        )

    name = _unique_set_name(folder, now, kind)
    partial = folder / (name + PARTIAL_SUFFIX)
    final = folder / name

    try:
        (partial / DATABASES_DIRNAME).mkdir(parents=True)
        entries = _write_databases(present, total, partial, progress)
        settings = _copy_settings(partial / SETTINGS_DIRNAME)
        manifest = _manifest(kind, now, entries, settings)
        _write_json(partial / MANIFEST_NAME, manifest)
        os.replace(partial, final)
    except BaseException as exc:
        # BaseException: a cancelled worker or Ctrl+C must not leave a
        # .partial folder behind either.
        shutil.rmtree(partial, ignore_errors=True)
        if isinstance(exc, BackupError):
            raise
        if isinstance(exc, (SnapshotError, OSError, sqlite3.Error)):
            raise BackupError(str(exc)) from exc
        raise

    logger.info(
        "backup: wrote %s (%d database(s), %d skipped)",
        final, len(entries), len(skipped_names),
    )
    return BackupResult(read_backup_set(final), skipped_names)


def _write_databases(
    present: list[tuple[_DatabaseLike, int]],
    total: int,
    partial: Path,
    progress: Optional[ProgressCallback],
) -> list[DatabaseEntry]:
    from opensak.db.db_settings import ensure_seeded, read_file

    entries: list[DatabaseEntry] = []
    used_names: set[str] = set()
    done_before = 0
    for db, size in present:
        src = Path(db.path)
        file_name = _unique_file_name(src.name, used_names)
        target = partial / DATABASES_DIRNAME / file_name

        # #659: make sure the database's own settings (and db_uuid) are
        # inside the file before it is copied, also for a database that
        # hasn't been opened since the upgrade. Best-effort.
        try:
            ensure_seeded(src, db.name)
        except Exception:
            logger.warning(
                "backup: could not import the settings of %r", db.name,
                exc_info=True,
            )

        def _db_progress(done: int, pages: int, *, _name: str = db.name,
                         _before: int = done_before, _size: int = size) -> None:
            if progress is None:
                return
            fraction = done / pages if pages else 1.0
            overall = (_before + fraction * _size) / total if total else 1.0
            progress(_name, fraction, overall)

        snapshot_database(src, target, _db_progress)
        if progress is not None:
            progress(db.name, 1.0, (done_before + size) / total if total else 1.0)
        done_before += size

        entries.append(DatabaseEntry(
            name=db.name,
            file=f"{DATABASES_DIRNAME}/{file_name}",
            db_uuid=_str_or_none(read_file(target).get("db_uuid")),
            schema_version=_user_version(target),
            size_bytes=target.stat().st_size,
        ))
    return entries


def _copy_settings(target: Path) -> bool:
    """Copy the allow-listed settings into *target*."""
    from opensak.settings_store import get_install_dir, get_store

    install = get_install_dir()
    target.mkdir(parents=True)
    # The file the settings store really uses (written atomically, so it is
    # safe to copy while OpenSAK runs).
    settings_file = get_store().settings_path()
    if settings_file.is_file():
        shutil.copy2(settings_file, target / _SETTINGS_FILE)
    for name in _SETTINGS_DIRS:
        src = install / name
        if src.is_dir():
            shutil.copytree(src, target / name)
    return True


def _manifest(
    kind: str, now: datetime, entries: list[DatabaseEntry], settings: bool,
) -> dict[str, Any]:
    from opensak import __version__
    return {
        "format": MANIFEST_FORMAT,
        "kind": kind,
        "created": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "opensak_version": __version__,
        "databases": [
            {
                "name": e.name,
                "file": e.file,
                "db_uuid": e.db_uuid,
                "schema_version": e.schema_version,
                "size_bytes": e.size_bytes,
            }
            for e in entries
        ],
        "settings": settings,
    }


# ── Reading sets ──────────────────────────────────────────────────────────────

def read_backup_set(path: Path) -> BackupSet:
    """Read the set at *path* from its manifest (raises BackupError if invalid)."""
    path = Path(path)
    manifest_path = path / MANIFEST_NAME
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BackupError(f"Not a complete backup (no manifest): {path}") from exc
    except (OSError, ValueError) as exc:
        raise BackupError(f"Unreadable manifest in {path}: {exc}") from exc

    try:
        fmt = int(data["format"])
        if fmt > MANIFEST_FORMAT:
            raise BackupError(
                f"{path.name} was made by a newer version of OpenSAK "
                f"(backup format {fmt})"
            )
        kind = str(data["kind"])
        if kind not in _KINDS:
            raise BackupError(f"Unknown backup kind {kind!r} in {path}")
        created = datetime.strptime(
            str(data["created"]), "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        databases = tuple(
            DatabaseEntry(
                name=str(d["name"]),
                file=str(d["file"]),
                db_uuid=_str_or_none(d.get("db_uuid")),
                schema_version=(
                    int(d["schema_version"])
                    if d.get("schema_version") is not None else None
                ),
                size_bytes=int(d.get("size_bytes", 0)),
            )
            for d in data["databases"]
        )
        return BackupSet(
            path=path,
            kind=kind,
            created=created,
            opensak_version=str(data.get("opensak_version", "")),
            databases=databases,
            settings=bool(data.get("settings", False)),
            format=fmt,
        )
    except BackupError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupError(f"Invalid manifest in {path}: {exc}") from exc


def list_backup_sets(folder: Optional[Path] = None) -> list[BackupSet]:
    """
    The complete sets in *folder* (default: the backup folder), newest
    first. Partial folders, folders without a manifest and anything else in
    the folder are ignored.
    """
    folder = Path(folder) if folder is not None else get_backup_dir()
    if not folder.is_dir():
        return []
    sets: list[BackupSet] = []
    for child in folder.iterdir():
        if not child.is_dir() or not _SET_NAME_RE.match(child.name):
            continue
        try:
            sets.append(read_backup_set(child))
        except BackupError as exc:
            logger.info("backup: ignoring %s — %s", child.name, exc)
    sets.sort(key=lambda s: (s.created, s.path.name), reverse=True)
    return sets


# ── Rotation and cleanup ──────────────────────────────────────────────────────

def rotate(folder: Optional[Path] = None, keep: Optional[int] = None) -> list[Path]:
    """
    Delete the oldest automatic sets beyond the newest *keep* (default: the
    ``backup.keep_auto`` setting). Manual sets, pre-migration backups and
    anything OpenSAK didn't create are never touched. Leftover partial
    folders are cleaned up too. Returns the deleted paths.
    """
    folder = Path(folder) if folder is not None else get_backup_dir()
    keep = max(1, keep if keep is not None else get_keep_auto())
    deleted = cleanup_partials(folder)
    autos = [s for s in list_backup_sets(folder) if s.kind == KIND_AUTO]
    for old in autos[keep:]:
        if old.path.parent != folder or not _SET_NAME_RE.match(old.path.name):
            continue  # defensive: never delete outside our own set folders
        try:
            shutil.rmtree(old.path)
            deleted.append(old.path)
            logger.info("backup: rotated away %s", old.path.name)
        except OSError:
            logger.warning("backup: could not delete %s", old.path, exc_info=True)
    return deleted


def cleanup_partials(folder: Path) -> list[Path]:
    """Remove ``.partial`` set folders left behind by an interrupted backup."""
    folder = Path(folder)
    removed: list[Path] = []
    if not folder.is_dir():
        return removed
    for child in folder.iterdir():
        if (
            child.is_dir()
            and child.name.endswith(PARTIAL_SUFFIX)
            and _SET_NAME_RE.match(child.name[: -len(PARTIAL_SUFFIX)])
        ):
            shutil.rmtree(child, ignore_errors=True)
            if not child.exists():
                removed.append(child)
                logger.info("backup: removed leftover %s", child.name)
    return removed


# ── Helpers ───────────────────────────────────────────────────────────────────

def _documents_dir() -> Path:
    home = Path.home()
    if sys.platform.startswith("linux"):
        found = _xdg_documents_dir(home)
        if found is not None:
            return found
    return home / "Documents"


def _xdg_documents_dir(home: Path) -> Optional[Path]:
    """XDG_DOCUMENTS_DIR from user-dirs.dirs, if set to something useful."""
    config = os.environ.get("XDG_CONFIG_HOME") or str(home / ".config")
    try:
        text = (Path(config) / "user-dirs.dirs").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("XDG_DOCUMENTS_DIR="):
            continue
        value = line.split("=", 1)[1].strip().strip('"')
        value = value.replace("$HOME", str(home))
        path = Path(value)
        # "$HOME/" alone means "no documents folder" in the XDG spec.
        if path.is_absolute() and path != home:
            return path
    return None


def _unique_set_name(folder: Path, now: datetime, kind: str) -> str:
    base = f"{SET_PREFIX}{now.astimezone():%Y-%m-%d_%H%M}-{kind}"
    name, n = base, 1
    while (folder / name).exists() or (folder / (name + PARTIAL_SUFFIX)).exists():
        n += 1
        name = f"{base}-{n}"
    return name


def _unique_file_name(name: str, used: set[str]) -> str:
    """*name*, or name-2.db, name-3.db… if two databases share a file name."""
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate, n = name, 1
    while candidate.lower() in used:
        n += 1
        candidate = f"{stem}-{n}{suffix}"
    used.add(candidate.lower())
    return candidate


def _database_size(path: Path) -> int:
    size = path.stat().st_size
    wal = Path(str(path) + "-wal")
    if wal.exists():
        size += wal.stat().st_size
    return size


def _user_version(path: Path) -> Optional[int]:
    try:
        conn = sqlite3.connect(str(path))
        try:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def _resolved(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser().absolute()


def _str_or_none(value: Any) -> Optional[str]:
    return str(value) if value is not None else None


def _mb(n: float) -> int:
    return int(n // 2**20) + 1 if n else 0

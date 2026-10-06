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
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Protocol

from opensak.backup.snapshot import SnapshotError, snapshot_database

logger = logging.getLogger(__name__)

# ── Settings keys and defaults ────────────────────────────────────────────────

BACKUP_DIR_KEY = "backup.dir"
KEEP_AUTO_KEY = "backup.keep_auto"
# Issue #989: write new sets as one zip file. Off unless the user turns it on.
COMPRESS_KEY = "backup.compress"
DEFAULT_KEEP_AUTO = 5
DEFAULT_DIRNAME = "OpenSAK Backups"

# ── Set layout ────────────────────────────────────────────────────────────────

KIND_AUTO = "auto"
KIND_MANUAL = "manual"
_KINDS = (KIND_AUTO, KIND_MANUAL)

SET_PREFIX = "OpenSAK-backup-"
PARTIAL_SUFFIX = ".partial"
ZIP_SUFFIX = ".zip"   # a compressed set: <set name>.zip (#989)
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

# Compressed sets (#989): room assumed for the zip, as a fraction of the
# databases' size. Typical databases shrink to about a third; this leaves
# margin for ones that compress worse. Only one database is ever staged
# uncompressed at a time, so that needs room on top of this.
_ZIP_SPACE_FACTOR = 0.6
_ZIP_CHUNK = 1024 * 1024
# Deflate level 1: on a 234 MB test database it took 2.7 s against 11.7 s at
# zlib's default level 6, for a zip about a third larger (53 vs 39 MB) —
# still far smaller than the database. Speed matters most here: the backup
# on exit runs while the user waits for OpenSAK to close.
_ZIP_LEVEL = 1

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
    compressed: bool = False        # one .zip file instead of a folder (#989)


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


def get_compress() -> bool:
    """Whether new backup sets are written as a zip (#989). Off by default."""
    from opensak.settings_store import get_store
    return bool(get_store().get(COMPRESS_KEY, False))


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
    compress: Optional[bool] = None,
) -> BackupResult:
    """
    Back up *databases* and the settings into a new set in *folder*
    (default: the backup folder).

    With *compress* (default: the ``backup.compress`` setting, #989) the set
    is one zip file with the same layout inside, instead of a folder.

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

    if compress is None:
        compress = get_compress()

    total = sum(size for _db, size in present)
    if compress:
        largest = max((size for _db, size in present), default=0)
        needed = largest * _FREE_SPACE_FACTOR + total * _ZIP_SPACE_FACTOR
    else:
        needed = total * _FREE_SPACE_FACTOR
    free = shutil.disk_usage(folder).free
    if free < needed:
        raise NotEnoughSpaceError(
            f"Not enough free space in {folder}: about "
            f"{_mb(needed)} MB needed, {_mb(free)} MB free"
        )

    name = _unique_set_name(folder, now, kind)
    if compress:
        final = _write_compressed_set(
            present, total, folder, name, kind, now, progress,
        )
        logger.info("backup: wrote %s (compressed, %d skipped)", final, len(skipped_names))
        return BackupResult(read_backup_set(final), skipped_names)

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


def _write_compressed_set(
    present: list[tuple[_DatabaseLike, int]],
    total: int,
    folder: Path,
    name: str,
    kind: str,
    now: datetime,
    progress: Optional[ProgressCallback],
) -> Path:
    """
    Write a compressed set (#989): ``<name>.zip`` with the same layout as a
    folder set. Databases are staged one at a time in ``<name>.partial/``
    and removed as soon as they are in the zip, so the extra space needed is
    the largest database, not all of them. The zip is written as
    ``<name>.zip.partial`` and renamed when complete, manifest last — never
    half a backup. On any failure or cancel both are removed.
    """
    staging = folder / (name + PARTIAL_SUFFIX)
    zip_partial = folder / (name + ZIP_SUFFIX + PARTIAL_SUFFIX)
    final = folder / (name + ZIP_SUFFIX)
    try:
        (staging / DATABASES_DIRNAME).mkdir(parents=True)
        with zipfile.ZipFile(
            zip_partial, "w", compression=zipfile.ZIP_DEFLATED,
            compresslevel=_ZIP_LEVEL, allowZip64=True,
        ) as zf:
            def _add_database(target: Path, arcname: str,
                              report: Callable[[float], None]) -> None:
                _zip_file(zf, target, arcname, report)
                target.unlink()

            entries = _write_databases(
                present, total, staging, progress,
                snapshot_share=0.5, after_each=_add_database,
            )
            settings_dir = staging / SETTINGS_DIRNAME
            settings = _copy_settings(settings_dir)
            for path in sorted(p for p in settings_dir.rglob("*") if p.is_file()):
                arcname = f"{SETTINGS_DIRNAME}/{path.relative_to(settings_dir).as_posix()}"
                zf.write(path, arcname)
            manifest = _manifest(kind, now, entries, settings)
            zf.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            )
        os.replace(zip_partial, final)
    except BaseException as exc:
        try:
            zip_partial.unlink(missing_ok=True)
        except OSError:
            pass
        shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, BackupError):
            raise
        if isinstance(exc, (SnapshotError, OSError, sqlite3.Error, zipfile.BadZipFile)):
            raise BackupError(str(exc)) from exc
        raise
    shutil.rmtree(staging, ignore_errors=True)
    return final


def _zip_file(
    zf: zipfile.ZipFile, path: Path, arcname: str, report: Callable[[float], None],
) -> None:
    """Add *path* to *zf* in chunks, reporting progress (which may raise)."""
    info = zipfile.ZipInfo.from_file(path, arcname)
    info.compress_type = zipfile.ZIP_DEFLATED
    # ZipFile.open() takes the level from the ZipInfo, not the ZipFile.
    # Python 3.13 renamed the attribute; 3.12 only has the private name.
    if hasattr(info, "compress_level"):
        info.compress_level = _ZIP_LEVEL
    else:
        info._compresslevel = _ZIP_LEVEL  # type: ignore[attr-defined]
    size = info.file_size
    done = 0
    with path.open("rb") as src, zf.open(info, "w") as dst:
        while True:
            chunk = src.read(_ZIP_CHUNK)
            if not chunk:
                break
            dst.write(chunk)
            done += len(chunk)
            report(done / size if size else 1.0)
    report(1.0)


def _write_databases(
    present: list[tuple[_DatabaseLike, int]],
    total: int,
    partial: Path,
    progress: Optional[ProgressCallback],
    *,
    snapshot_share: float = 1.0,
    after_each: Optional[Callable[[Path, str, Callable[[float], None]], None]] = None,
) -> list[DatabaseEntry]:
    """
    Snapshot each database into *partial*/databases/. *after_each* (used for
    compressed sets) gets each finished copy, its name inside the set, and a
    progress reporter for its own work; the snapshot then counts for
    *snapshot_share* of that database's progress.
    """
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

        def _report(fraction: float, *, _name: str = db.name,
                    _before: int = done_before, _size: int = size) -> None:
            if progress is None:
                return
            overall = (_before + fraction * _size) / total if total else 1.0
            progress(_name, fraction, overall)

        def _db_progress(done: int, pages: int, *, _report=_report) -> None:
            _report((done / pages if pages else 1.0) * snapshot_share)

        snapshot_database(src, target, _db_progress)
        entry = DatabaseEntry(
            name=db.name,
            file=f"{DATABASES_DIRNAME}/{file_name}",
            db_uuid=_str_or_none(read_file(target).get("db_uuid")),
            schema_version=_user_version(target),
            size_bytes=target.stat().st_size,
        )
        if after_each is not None:
            def _after_report(f: float, *, _report=_report) -> None:
                _report(snapshot_share + f * (1 - snapshot_share))

            after_each(target, entry.file, _after_report)
        _report(1.0)
        done_before += size
        entries.append(entry)
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
    compressed = is_compressed_set_file(path)
    try:
        if compressed:
            # #989: read the manifest straight from the zip.
            with zipfile.ZipFile(path) as zf:
                data = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        else:
            data = json.loads((path / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (FileNotFoundError, KeyError) as exc:
        raise BackupError(f"Not a complete backup (no manifest): {path}") from exc
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
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
            compressed=compressed,
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
        if not _is_set(child):
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
        if old.path.parent != folder or not _is_set(old.path):
            continue  # defensive: never delete outside our own set folders
        try:
            if old.compressed:
                old.path.unlink()
            else:
                shutil.rmtree(old.path)
            deleted.append(old.path)
            logger.info("backup: rotated away %s", old.path.name)
        except OSError:
            logger.warning("backup: could not delete %s", old.path, exc_info=True)
    return deleted


def cleanup_partials(folder: Path) -> list[Path]:
    """
    Remove ``.partial`` set folders — and ``.zip.partial`` files of
    compressed sets (#989) — left behind by an interrupted backup.
    """
    folder = Path(folder)
    removed: list[Path] = []
    if not folder.is_dir():
        return removed
    zip_partial = ZIP_SUFFIX + PARTIAL_SUFFIX
    for child in folder.iterdir():
        if (
            child.is_dir()
            and child.name.endswith(PARTIAL_SUFFIX)
            and _SET_NAME_RE.match(child.name[: -len(PARTIAL_SUFFIX)])
        ):
            shutil.rmtree(child, ignore_errors=True)
        elif (
            child.is_file()
            and child.name.endswith(zip_partial)
            and _SET_NAME_RE.match(child.name[: -len(zip_partial)])
        ):
            try:
                child.unlink()
            except OSError:
                pass
        else:
            continue
        if not child.exists():
            removed.append(child)
            logger.info("backup: removed leftover %s", child.name)
    return removed


def is_compressed_set_file(path: Path) -> bool:
    """True if *path* is a compressed set file by its name (#989)."""
    path = Path(path)
    return (
        path.name.endswith(ZIP_SUFFIX)
        and bool(_SET_NAME_RE.match(path.name[: -len(ZIP_SUFFIX)]))
        and path.is_file()
    )


def _is_set(path: Path) -> bool:
    """A set OpenSAK made: a folder, or a compressed set file (#989)."""
    return (path.is_dir() and bool(_SET_NAME_RE.match(path.name))) or (
        is_compressed_set_file(path)
    )


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
    while any(
        (folder / (name + suffix)).exists()
        for suffix in ("", PARTIAL_SUFFIX, ZIP_SUFFIX, ZIP_SUFFIX + PARTIAL_SUFFIX)
    ):
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

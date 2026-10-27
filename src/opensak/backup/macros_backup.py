"""
src/opensak/backup/macros_backup.py — the Lua macros folder in backup sets (#1137).

Part of the backup epic (#942); design: docs/architecture/backup.md
("Macros").

A backup set holds the user's macros folder as ``macros/``, with the same
layout as on disk: the macro files, the data files next to them and
``settings/`` (each macro's saved settings, opensak.settings, #983). Left
out:

* **Unchanged example macros.** ``examples/`` holds copies of the examples
  shipped with OpenSAK; each release brings its own, so a backup never keeps
  an old copy that a restore could put back over a newer one. An example the
  user edited is their work and is kept: a ``.lua`` file directly in
  ``examples/`` is left out only when its code is that of a shipped example
  (examples.shipped_hashes()), another file there only when it is identical
  to the shipped file of the same name.
* **SQLite files** (and their ``-wal``/``-shm``/``-journal``). A macro's own
  database can be open while the backup runs, so it is never copied as a
  plain file: every SQLite file in the macros folder is snapshotted into
  ``macro_databases/`` instead, with the macro_db() files recorded
  elsewhere (backupset.py).
* **Links.** A symbolic link is not followed and not copied.

This module has no UI.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional

logger = logging.getLogger(__name__)

# The folder in a backup set, and in the macros folder the shipped examples' one.
MACROS_DIRNAME = "macros"
EXAMPLES_DIRNAME = "examples"

# The files a SQLite database consists of (the same list permissions.py
# protects from macros' file functions).
_SQLITE_SUFFIXES = (".db", ".db3", ".sqlite", ".sqlite3")
_SQLITE_COMPANIONS = ("-wal", "-shm", "-journal")


@dataclass(frozen=True)
class MacroFile:
    """A file of the macros folder that a backup includes."""

    path: Path        # on disk
    rel: str          # relative to the macros folder, "/"-separated
    size_bytes: int


@dataclass(frozen=True)
class MacrosEntry:
    """The macros folder inside a backup set, as described by its manifest."""

    dir: str          # the folder in the set, "macros"
    path: str         # the macros folder it was backed up from
    files: int
    size_bytes: int


def macros_root() -> Path:
    """The user's macros folder, resolved."""
    from opensak.macro.permissions import macros_dir

    return macros_dir()


def is_sqlite_file(name: str) -> bool:
    """True for a SQLite database file name, or one of its -wal/-shm/-journal."""
    name = name.lower()
    for companion in _SQLITE_COMPANIONS:
        if name.endswith(companion):
            name = name[: -len(companion)]
            break
    return name.endswith(_SQLITE_SUFFIXES)


def files_to_back_up(root: Optional[Path] = None) -> list[MacroFile]:
    """The files of the macros folder *root* (default: the user's) that a
    backup includes, sorted by their relative path. A folder or file that
    can't be read is left out with a warning in the log."""
    root = Path(root) if root is not None else macros_root()
    found: list[MacroFile] = []
    if not root.is_dir():
        return found

    def _warn(exc: OSError) -> None:
        logger.warning("backup: macros: cannot read %s — %s", exc.filename, exc)

    for folder, dirs, files in os.walk(root, onerror=_warn, followlinks=False):
        here = Path(folder)
        dirs[:] = sorted(d for d in dirs if not (here / d).is_symlink())
        for name in sorted(files):
            path = here / name
            if path.is_symlink() or is_sqlite_file(name):
                continue
            rel = path.relative_to(root).as_posix()
            if _is_unchanged_example(path, rel):
                continue
            try:
                size = path.stat().st_size
            except OSError as exc:
                _warn(exc)
                continue
            found.append(MacroFile(path, rel, size))
    return found


def sqlite_files(root: Optional[Path] = None) -> list[Path]:
    """The SQLite database files in the macros folder *root*, so a backup
    snapshots each of them, also one no macro has opened through
    opensak.macro_db() on this machine."""
    root = Path(root) if root is not None else macros_root()
    found: list[Path] = []
    if not root.is_dir():
        return found
    for folder, dirs, files in os.walk(root, followlinks=False):
        here = Path(folder)
        dirs[:] = sorted(d for d in dirs if not (here / d).is_symlink())
        for name in sorted(files):
            path = here / name
            lower = name.lower()
            if (
                lower.endswith(_SQLITE_SUFFIXES)
                and not path.is_symlink()
                and path.is_file()
            ):
                found.append(path)
    return found


def copy_macros(
    files: list[MacroFile], target: Path, root: Optional[Path] = None,
    *, skipped: Optional[list[str]] = None,
) -> Optional[MacrosEntry]:
    """Copy *files* into *target* (the set's macros/ folder). A file that
    can't be read is left out with a warning and added to *skipped*; the
    macros never make a backup fail. None if nothing was copied."""
    root = Path(root) if root is not None else macros_root()
    count = size = 0
    for f in files:
        dest = target.joinpath(*PurePosixPath(f.rel).parts)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f.path, dest)
        except OSError as exc:
            logger.warning("backup: macro file %s skipped — %s", f.path, exc)
            if skipped is not None:
                skipped.append(f.rel)
            continue
        count += 1
        size += dest.stat().st_size
    return _entry(root, count, size)


def zip_macros(
    zf: zipfile.ZipFile, files: list[MacroFile], root: Optional[Path] = None,
    *, skipped: Optional[list[str]] = None,
) -> Optional[MacrosEntry]:
    """Add *files* to the compressed set *zf* under macros/, like
    copy_macros()."""
    root = Path(root) if root is not None else macros_root()
    count = size = 0
    for f in files:
        try:
            zf.write(f.path, f"{MACROS_DIRNAME}/{f.rel}")
        except OSError as exc:
            logger.warning("backup: macro file %s skipped — %s", f.path, exc)
            if skipped is not None:
                skipped.append(f.rel)
            continue
        count += 1
        size += f.size_bytes
    return _entry(root, count, size)


def fingerprint(root: Optional[Path] = None) -> Optional[list]:
    """
    What "back up on exit" compares (#959): ``[files, total size, newest
    mtime_ns, digest of the file names]`` over the files a backup would
    include, or None when there are none — so the state of a user without
    macros stays exactly as it was before macros were backed up.

    Built from the files only, not from the folders' own modification
    times: SQLite creates and removes its -wal/-journal files next to a
    macro's database every time it is used, which changes the folder.
    """
    files = files_to_back_up(root)
    if not files:
        return None
    newest = 0
    for f in files:
        try:
            newest = max(newest, f.path.stat().st_mtime_ns)
        except OSError:
            pass
    names = hashlib.sha256("\n".join(f.rel for f in files).encode("utf-8")).hexdigest()
    return [len(files), sum(f.size_bytes for f in files), newest, names[:16]]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _entry(root: Path, count: int, size: int) -> Optional[MacrosEntry]:
    if not count:
        return None
    return MacrosEntry(dir=MACROS_DIRNAME, path=str(root), files=count, size_bytes=size)


def _is_unchanged_example(path: Path, rel: str) -> bool:
    """True for a file directly in examples/ that is as OpenSAK shipped it."""
    parts = PurePosixPath(rel).parts
    if len(parts) != 2 or parts[0].lower() != EXAMPLES_DIRNAME:
        return False
    from opensak.macro import examples

    try:
        if path.suffix.lower() == ".lua":
            from opensak.macro.db_access import read_macro_source, source_hash

            return source_hash(read_macro_source(path)) in examples.shipped_hashes()
        shipped = examples.bundled_examples_dir() / path.name
        return shipped.is_file() and _same_bytes(path, shipped)
    except (OSError, UnicodeDecodeError):
        return False


def _same_bytes(a: Path, b: Path) -> bool:
    if a.stat().st_size != b.stat().st_size:
        return False
    return a.read_bytes() == b.read_bytes()

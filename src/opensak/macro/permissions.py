"""
src/opensak/macro/permissions.py — folders Lua macros may read or write.

The list lives in opensak.json under "macros.folder_permissions" as
[{"path": ..., "read": bool, "write": bool}, ...] and is edited in
Settings → Folder permissions. Until the user saves a list of their own,
the defaults apply: the system temp folder (read/write) and OpenSAK's
macros folder (read only).

Every path a macro hands to a file function goes through check_access().
Both the requested path and the listed folders are run through
Path.resolve() before comparing, so "../" segments and symlinks (or
Windows junctions) cannot lead out of a permitted folder. When folders are
nested, the most specific entry decides — a sub-folder listed without
rights therefore blocks it even if a parent folder is permitted.

A filesystem root (/, a drive such as C:\\ or a network share such as
\\\\server\\share) is never a permitted folder, since it would open the
whole drive: Settings refuses to add one, and an entry for a root in
opensak.json (edited by hand) is ignored.

OpenSAK's own data can never be read or written by a macro, whatever the
list says (see protected_reason()): opensak.json, bootstrap.json, the
Geocaching.com token, the data and database folders (except the macros
folder inside the data folder) and SQLite files (.db, .db3, .sqlite,
.sqlite3 and their -wal/-shm/-journal files) anywhere.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Iterable, Optional

STORE_KEY = "macros.folder_permissions"


class FolderAccessDenied(PermissionError):
    """A macro tried to read or write outside its permitted folders."""


@dataclass
class FolderPermission:
    path: str
    read: bool = False
    write: bool = False

    def to_dict(self) -> dict:
        return {"path": self.path, "read": self.read, "write": self.write}

    @classmethod
    def from_dict(cls, data: dict) -> "FolderPermission":
        return cls(
            path=str(data.get("path") or ""),
            read=bool(data.get("read")),
            write=bool(data.get("write")),
        )


def resolve_path(path: str | Path) -> Path:
    """Expand ~ and environment variables, make absolute and resolve
    "..", "." and symlinks."""
    return Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()


def is_root_folder(path: str | Path) -> bool:
    """True if *path* is a filesystem root: "/" on Linux/macOS, a drive
    ("C:\\") or a network share ("\\\\server\\share") on Windows.

    The path is resolved first, so e.g. "/tmp/.." counts as the root.
    """
    try:
        resolved = resolve_path(path)
    except (OSError, RuntimeError):
        return False
    return _is_root(resolved)


def _is_root(path: PurePath) -> bool:
    # Only a root is its own parent (for both POSIX and Windows paths).
    return path.parent == path


def temp_dir() -> Path:
    """The system temp folder, resolved (on macOS e.g. /private/var/folders/…/T)."""
    return resolve_path(tempfile.gettempdir())


def macros_dir() -> Path:
    """OpenSAK's macros folder, resolved."""
    from opensak.config import get_macros_dir

    return resolve_path(get_macros_dir())


def default_permissions() -> list[FolderPermission]:
    return [
        FolderPermission(str(temp_dir()), read=True, write=True),
        FolderPermission(str(macros_dir()), read=True, write=False),
    ]


def load_permissions() -> list[FolderPermission]:
    """The saved list, or the defaults if the user never saved one."""
    from opensak.settings_store import get_store

    raw = get_store().get(STORE_KEY)
    if not isinstance(raw, list):
        return default_permissions()
    return [
        perm
        for perm in (
            FolderPermission.from_dict(entry)
            for entry in raw
            if isinstance(entry, dict) and entry.get("path")
        )
        if not is_root_folder(perm.path)
    ]


def save_permissions(permissions: Iterable[FolderPermission]) -> None:
    from opensak.settings_store import get_store

    get_store().set(STORE_KEY, [p.to_dict() for p in permissions])


# ── OpenSAK's own data: never accessible to macros ───────────────────────────

_SQLITE_SUFFIXES = (".db", ".db3", ".sqlite", ".sqlite3")
_SQLITE_COMPANIONS = ("-wal", "-shm", "-journal")


def _is_sqlite_file(path: Path) -> bool:
    name = path.name.lower()
    for companion in _SQLITE_COMPANIONS:
        if name.endswith(companion):
            name = name[: -len(companion)]
            break
    return name.endswith(_SQLITE_SUFFIXES)


def _protected_locations() -> tuple[list[Path], list[Path]]:
    """(folders, files) macros may never touch. Each lookup is guarded, so
    a broken setting cannot switch the protection off for the others."""
    folders: list[Path] = []
    files: list[Path] = []

    def add(target: list[Path], getter) -> None:
        try:
            target.append(resolve_path(getter()))
        except Exception:
            pass

    from opensak import config, settings_store

    add(folders, settings_store.get_install_dir)
    add(folders, settings_store.get_db_dir)
    add(files, lambda: settings_store.get_store().settings_path())
    add(files, settings_store._bootstrap_path)
    add(files, config.get_gc_token_path)
    return folders, files


def protected_reason(target: Path) -> Optional[str]:
    """Why the resolved *target* is off limits to macros, or None.

    Checked before the folder list, so no Settings entry can open
    OpenSAK's settings, credentials or databases to a macro.
    """
    if _is_sqlite_file(target):
        return "it is a database file"
    if target.name.lower().startswith("opensak.json"):
        return "it is OpenSAK's settings file"
    folders, files = _protected_locations()
    if target in files:
        return "it is one of OpenSAK's own files"
    try:
        macros = macros_dir()
    except Exception:
        macros = None
    for folder in folders:
        if not target.is_relative_to(folder):
            continue
        # The macros folder normally lives inside the data folder (which is
        # also the default database folder) and stays usable.
        if (
            macros is not None
            and macros != folder
            and macros.is_relative_to(folder)
            and target.is_relative_to(macros)
        ):
            continue
        return f"it is inside OpenSAK's data folder {folder}"
    return None


def _matching_entry(
    target: Path, permissions: Iterable[FolderPermission]
) -> Optional[FolderPermission]:
    """The most specific listed folder that contains *target*."""
    best: Optional[FolderPermission] = None
    best_depth = -1
    for perm in permissions:
        try:
            folder = resolve_path(perm.path)
        except (OSError, RuntimeError):
            continue
        if _is_root(folder):
            continue
        if target.is_relative_to(folder) and len(folder.parts) > best_depth:
            best, best_depth = perm, len(folder.parts)
    return best


def check_access(
    path: str | Path,
    write: bool = False,
    permissions: Optional[Iterable[FolderPermission]] = None,
) -> Path:
    """Return the resolved *path* if a permitted folder grants the access.

    Raises FolderAccessDenied otherwise. *permissions* defaults to the
    saved list (load_permissions()).
    """
    if permissions is None:
        permissions = load_permissions()
    try:
        target = resolve_path(path)
    except (OSError, RuntimeError) as exc:   # RuntimeError: symlink loop
        raise FolderAccessDenied(f"cannot resolve {path}: {exc}") from None
    kind = "write" if write else "read"
    reason = protected_reason(target)
    if reason is not None:
        raise FolderAccessDenied(f"macros may not {kind} {target} — {reason}")
    entry = _matching_entry(target, permissions)
    allowed = entry is not None and (entry.write if write else entry.read)
    if not allowed:
        raise FolderAccessDenied(
            f"macros may not {kind} {target} — permitted folders are set in "
            "Settings → Folder permissions"
        )
    return target

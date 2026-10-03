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
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
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


def default_permissions() -> list[FolderPermission]:
    from opensak.config import get_macros_dir

    return [
        FolderPermission(str(resolve_path(tempfile.gettempdir())), read=True, write=True),
        FolderPermission(str(resolve_path(get_macros_dir())), read=True, write=False),
    ]


def load_permissions() -> list[FolderPermission]:
    """The saved list, or the defaults if the user never saved one."""
    from opensak.settings_store import get_store

    raw = get_store().get(STORE_KEY)
    if not isinstance(raw, list):
        return default_permissions()
    return [
        FolderPermission.from_dict(entry)
        for entry in raw
        if isinstance(entry, dict) and entry.get("path")
    ]


def save_permissions(permissions: Iterable[FolderPermission]) -> None:
    from opensak.settings_store import get_store

    get_store().set(STORE_KEY, [p.to_dict() for p in permissions])


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
    entry = _matching_entry(target, permissions)
    allowed = entry is not None and (entry.write if write else entry.read)
    if not allowed:
        kind = "write" if write else "read"
        raise FolderAccessDenied(
            f"macros may not {kind} {target} — permitted folders are set in "
            "Settings → Folder permissions"
        )
    return target

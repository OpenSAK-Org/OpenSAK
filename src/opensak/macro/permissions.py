"""
src/opensak/macro/permissions.py — folders Lua macros may read or write.

The list lives in opensak.json under "macros.folder_permissions" as
[{"path": ..., "read": bool, "write": bool}, ...] and is edited in
Settings → Folder permissions. Until the user changes the list, nothing
is stored and the defaults apply: an "opensak" folder inside the system
temp folder (read/write) and OpenSAK's macros folder (read only). Saving a
list equal to the defaults stores nothing either, so a later change of the
defaults reaches everyone who never customised them; a list stored by an
older version that equals the old defaults (the whole temp folder) is
dropped on load for the same reason.

Every path a macro hands to a file function goes through check_access().
Both the requested path and the listed folders are run through
Path.resolve() before comparing, so "../" segments and symlinks (or
Windows junctions) cannot lead out of a permitted folder. When folders are
nested, the most specific entry decides — a sub-folder listed without
rights therefore blocks it even if a parent folder is permitted.

Every file a macro writes goes through safe_write(): it refuses a target
with more than one hard link (resolve() cannot see through those, so a
link in a permitted folder could otherwise lead to e.g. opensak.json) and
writes to a temporary file first, which then replaces the target. The
check is repeated right before the replace.

A filesystem root (/, a drive such as C:\\ or a network share such as
\\\\server\\share) is never a permitted folder, since it would open the
whole drive: Settings refuses to add one, and an entry for a root in
opensak.json (edited by hand) is ignored.

When no listed folder grants an access, check_access() raises
FolderNotApproved, and the runtime asks the user (through OpenSAK's own
dialog, never through the macro) to approve the file's folder for this run
only or permanently. Protected data and roots are never offered.

OpenSAK's own data can never be read or written by a macro, whatever the
list says (see protected_reason()): opensak.json, bootstrap.json, the
Geocaching.com token, the data and database folders (except the macros
folder inside the data folder) and SQLite files (.db, .db3, .sqlite,
.sqlite3 and their -wal/-shm/-journal files) anywhere.
"""

from __future__ import annotations

import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Iterable, Optional

STORE_KEY = "macros.folder_permissions"


class FolderAccessDenied(PermissionError):
    """A macro tried to read or write outside its permitted folders."""


class FolderNotApproved(FolderAccessDenied):
    """Denied only because no listed folder grants the access — unlike a
    protected file, the user may approve it (see approvable_folder())."""

    def __init__(self, message: str, target: Path, write: bool):
        super().__init__(message)
        self.target = target
        self.write = write


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


def system_temp_dir() -> Path:
    """The system temp folder, resolved (on macOS e.g. /private/var/folders/…/T)."""
    return resolve_path(tempfile.gettempdir())


def temp_dir() -> Path:
    """OpenSAK's folder inside the system temp folder (created if needed),
    resolved — the default read/write folder for macros."""
    d = system_temp_dir() / "opensak"
    d.mkdir(parents=True, exist_ok=True)
    return d


def macros_dir() -> Path:
    """OpenSAK's macros folder, resolved."""
    from opensak.config import get_macros_dir

    return resolve_path(get_macros_dir())


def default_permissions() -> list[FolderPermission]:
    return [
        FolderPermission(str(temp_dir()), read=True, write=True),
        FolderPermission(str(macros_dir()), read=True, write=False),
    ]


def _legacy_default_permissions() -> list[FolderPermission]:
    """The defaults before the temp folder was narrowed to temp/opensak.
    Settings stored them on every save, so many opensak.json files hold
    exactly this list without the user ever having chosen it."""
    return [
        FolderPermission(str(system_temp_dir()), read=True, write=True),
        FolderPermission(str(macros_dir()), read=True, write=False),
    ]


def _same_permissions(
    a: Iterable[FolderPermission], b: Iterable[FolderPermission]
) -> bool:
    """Equal lists, comparing folders after resolving them."""
    def key(perms):
        out = []
        for p in perms:
            try:
                path = str(resolve_path(p.path))
            except (OSError, RuntimeError):
                path = p.path
            out.append((path, p.read, p.write))
        return out

    return key(a) == key(b)


def load_permissions() -> list[FolderPermission]:
    """The saved list, or the defaults if the user never saved one."""
    from opensak.settings_store import get_store

    store = get_store()
    raw = store.get(STORE_KEY)
    if not isinstance(raw, list):
        return default_permissions()
    perms = _parse(raw)
    if _same_permissions(perms, _legacy_default_permissions()):
        store.delete(STORE_KEY)
        return default_permissions()
    return perms


def _parse(raw: list) -> list[FolderPermission]:
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
    """Store *permissions*; a list equal to the defaults removes the stored
    list instead, so the defaults keep following future versions."""
    from opensak.settings_store import get_store

    permissions = list(permissions)
    store = get_store()
    if _same_permissions(permissions, default_permissions()):
        if store.get(STORE_KEY) is not None:
            store.delete(STORE_KEY)
        return
    store.set(STORE_KEY, [p.to_dict() for p in permissions])


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
        raise FolderNotApproved(
            f"macros may not {kind} {target} — permitted folders are set in "
            "Settings → Folder permissions",
            target,
            write,
        )
    return target


# ── Approval while a macro runs ──────────────────────────────────────────────

def approvable_folder(target: Path) -> Optional[Path]:
    """The folder the user is asked to approve for the resolved *target*:
    the folder containing it. None if that is a filesystem root, which can
    never be permitted."""
    folder = target.parent
    return None if _is_root(folder) else folder


def with_grant(
    permissions: Iterable[FolderPermission], folder: Path, write: bool
) -> list[FolderPermission]:
    """A copy of *permissions* that also grants read (or write) on *folder*.

    An entry for the same folder is extended instead of duplicated, keeping
    its other right. Otherwise a new entry is added; since *folder* contains
    the requested file, it is then the most specific match for it.
    """
    result: list[FolderPermission] = []
    found = False
    for perm in permissions:
        try:
            same = resolve_path(perm.path) == folder
        except (OSError, RuntimeError):
            same = False
        if same and not found:
            found = True
            perm = FolderPermission(
                perm.path,
                read=perm.read or not write,
                write=perm.write or write,
            )
        result.append(FolderPermission(perm.path, perm.read, perm.write))
    if not found:
        result.append(FolderPermission(str(folder), read=not write, write=write))
    return result


def grant_permanently(folder: Path, write: bool) -> None:
    """Add read (or write) on *folder* to the list saved in Settings."""
    save_permissions(with_grant(load_permissions(), folder, write))


def _refuse_hard_link(target: Path) -> None:
    """Raise if *target* exists with more than one hard link — writing to
    it would change the other names too, which no folder check can see."""
    try:
        st = target.stat()
    except FileNotFoundError:
        return
    if st.st_nlink > 1:
        raise FolderAccessDenied(
            f"macros may not write {target} — the file has more than one "
            "hard link, so writing to it would also change another file"
        )


def safe_write(
    path: str | Path,
    data: bytes | str,
    permissions: Optional[Iterable[FolderPermission]] = None,
    encoding: str = "utf-8",
) -> Path:
    """Write *data* to *path* for a macro and return the resolved path.

    The only way macro API functions may write files:
      * check_access(write=True), so the deny list and folder list apply;
      * a target with more than one hard link is refused;
      * the data goes to a temporary file in the same folder, which then
        replaces the target with os.replace(). The rename swaps the folder
        entry instead of writing into the old file, so a hard link that
        appears after the check no longer reaches the other file, and a
        failed write never leaves a half-written file behind;
      * the access check runs again right before the replace, so a folder
        swapped for a link in the meantime is caught.

    Raises FolderAccessDenied or OSError; the temporary file is removed on
    any failure.
    """
    if permissions is not None:
        permissions = list(permissions)    # iterated twice
    target = check_access(path, write=True, permissions=permissions)
    _refuse_hard_link(target)
    payload = data.encode(encoding) if isinstance(data, str) else data

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o666)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        final = check_access(path, write=True, permissions=permissions)
        if final != target:
            raise FolderAccessDenied(
                f"macros may not write {path} — the path changed while writing "
                f"(now {final})"
            )
        _refuse_hard_link(final)
        try:
            # Keep the permission bits of a file that is overwritten.
            os.chmod(tmp, final.stat().st_mode & 0o7777)
        except FileNotFoundError:
            pass
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return final

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

Every file a macro writes goes through safe_write() (or
safe_write_stream(), for a file written piece by piece, e.g. a large
export): it refuses a target with more than one hard link (resolve()
cannot see through those, so a link in a permitted folder could otherwise
lead to e.g. opensak.json) and
writes to a temporary file first, which then replaces the target. The
check is repeated right before the replace. A copy is such a write
(safe_copy()); a delete (safe_delete()) needs write access too, refuses
a link and repeats the check right before the delete.

A filesystem root (/, a drive such as C:\\ or a network share such as
\\\\server\\share) is never a permitted folder, since it would open the
whole drive: Settings refuses to add one, and an entry for a root in
opensak.json (edited by hand) is ignored.

When no listed folder grants an access, check_access() raises
FolderNotApproved, and the runtime asks the user (through OpenSAK's own
dialog, never through the macro) to approve the file's folder for this run
only or permanently. Protected data and roots are never offered. Both
answers belong to the running macro only: a permanent one is stored with
it (db_access.py, #1091), so the list here holds just the folders the user
listed in Settings, shared by all macros, and those older versions added.

OpenSAK's own data can never be read or written by a macro, whatever the
list says (see protected_reason()): opensak.json, bootstrap.json, the
Geocaching.com token, the data and database folders (except the macros
folder inside the data folder) and SQLite files (.db, .db3, .sqlite,
.sqlite3 and their -wal/-shm/-journal files) anywhere — except the macro's
own database (opensak.macro_db()), which still needs write access to its
folder like any other file. These checks ignore upper/lower case, since
resolve() does not fix the case on macOS, and also compare existing files
and folders by identity (device and inode), so a differently spelled path
to the same place is refused too.

On Windows, check_access() also refuses paths that do not name a plain
file (see windows_path_reason()): device names such as COM3 or NUL (also
"COM3.gpx"), alternate data streams ("a.gpx:x") and the \\\\.\\ and \\\\?\\
prefixes, which resolve() leaves in place and which the folder checks
would therefore not match.
"""

from __future__ import annotations

import os
import re
import secrets
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePath, PureWindowsPath
from typing import IO, Any, Callable, Iterable, Optional, TypeVar

from opensak.utils.file_names import is_reserved_device_name

T = TypeVar("T")

# Windows-only path checks (windows_path_reason()) apply in check_access().
_WINDOWS = sys.platform == "win32"

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


_WINDOWS_VAR = re.compile(r"%([^%/\\\s]+)%")


def expand_path(path: str | Path) -> Path:
    """Expand ~ and environment variables. %NAME% is expanded on every
    platform (os.path.expandvars only does that on Windows), so a path such
    as "%APPDATA%/gsak" works wherever the variable is set; unknown
    variables are left as they are."""
    text = os.path.expandvars(os.path.expanduser(str(path)))
    text = _WINDOWS_VAR.sub(lambda m: os.environ.get(m.group(1), m.group(0)), text)
    return Path(text)


def resolve_path(path: str | Path) -> Path:
    """Expand ~ and environment variables, make absolute and resolve
    "..", "." and symlinks."""
    return expand_path(path).resolve()


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


def _identity(path: Path) -> Optional[tuple[int, int]]:
    """(device, inode) of an existing *path*, or None. A file system that
    reports no inode (0) gives None, so it never matches anything."""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return (st.st_dev, st.st_ino) if st.st_ino else None


class _Location:
    """A resolved path as the deny list compares it: by its case-folded
    spelling, and where it exists, by the identity of the path itself and
    of its parents. macOS (and Windows) file systems ignore case, and
    resolve() does not fix the case on macOS, so "…/OPENSAK/bootstrap.json"
    reaches the same file as "…/opensak/bootstrap.json"."""

    def __init__(self, path: Path):
        self.path = path
        self.folded = _folded(path)
        self._own: Optional[tuple[int, int]] = None
        self._chain: Optional[set[tuple[int, int]]] = None

    def _ids(self) -> set[tuple[int, int]]:
        if self._chain is None:
            self._own = _identity(self.path)
            chain = {self._own} if self._own else set()
            for parent in self.path.parents:
                ident = _identity(parent)
                if ident:
                    chain.add(ident)
            self._chain = chain
        return self._chain

    def is_same(self, other: Path) -> bool:
        if self.folded == _folded(other):
            return True
        self._ids()
        return self._own is not None and self._own == _identity(other)

    def is_inside(self, folder: Path) -> bool:
        """*folder* is this path or one of its parents."""
        if self.folded.is_relative_to(_folded(folder)):
            return True
        ident = _identity(folder)
        return ident is not None and ident in self._ids()


def protected_reason(target: Path, allow_database: bool = False) -> Optional[str]:
    """Why the resolved *target* is off limits to macros, or None.

    Checked before the folder list, so no Settings entry can open
    OpenSAK's settings, credentials or databases to a macro.
    *allow_database*: *target* is the macro's own database
    (opensak.macro_db()), which may be an SQLite file; everything else
    still applies, OpenSAK's data folders included.

    Upper/lower case does not matter, and an existing file or folder is
    also matched by identity (see _Location), so another spelling of a
    protected path, or a hard link to a protected file, is refused too.
    On Linux this may also refuse a folder that differs from OpenSAK's own
    only in case — the safe direction.
    """
    if _is_sqlite_file(target) and not allow_database:
        return "it is a database file"
    if target.name.casefold().startswith("opensak.json"):
        return "it is OpenSAK's settings file"
    location = _Location(target)
    folders, files = _protected_locations()
    if any(location.is_same(f) for f in files):
        return "it is one of OpenSAK's own files"
    try:
        macros = macros_dir()
    except Exception:
        macros = None
    # Each macro reads its own saved settings through opensak.settings only,
    # never another macro's through the file functions (#983).
    from opensak.macro.settings import SETTINGS_DIRNAME
    if macros is not None and location.is_inside(macros / SETTINGS_DIRNAME):
        return "it holds the saved settings of macros — use opensak.settings"
    macros_location = _Location(macros) if macros is not None else None
    for folder in folders:
        if not location.is_inside(folder):
            continue
        # The macros folder normally lives inside the data folder (which is
        # also the default database folder) and stays usable.
        if (
            macros is not None
            and macros_location is not None
            and not macros_location.is_same(folder)
            and macros_location.is_inside(folder)
            and location.is_inside(macros)
        ):
            continue
        return f"it is inside OpenSAK's data folder {folder}"
    return None


# ── Windows: names that are not plain files ─────────────────────────────────

# \\.\ (device namespace) and \\?\ (no normalisation, e.g. \\?\GLOBALROOT),
# with either slash, and the NT object namespace \??\.
_DEVICE_PREFIX = re.compile(r"^(?:[\\/]{2}[?.]|[\\/]\?\?)(?:[\\/]|$)")


def windows_path_reason(path: str | PurePath) -> Optional[str]:
    """Why the Windows *path* does not name a plain file, or None.

    Refused: a \\\\.\\, \\\\?\\ or \\??\\ prefix (resolve() keeps
    "\\\\?\\C:\\…", so no folder check would match it); a ":" after the
    drive, which names an NTFS alternate data stream ("data.db:x"); and a
    device name such as CON, NUL, COM3 or LPT1 in any part of the path,
    also with an extension ("COM3.gpx" writes to the serial port). No macro
    needs any of these.
    """
    text = str(path)
    if _DEVICE_PREFIX.match(text):
        return "Windows device paths (\\\\.\\ or \\\\?\\) are not allowed"
    pure = PureWindowsPath(text)
    if ":" in text[len(pure.drive):]:
        return 'a ":" after the drive names an alternate data stream'
    for part in pure.parts[1 if pure.anchor else 0:]:
        if is_reserved_device_name(part):
            return f'"{part}" is a Windows device name, not a file'
    return None


# ── Writes: allowed file types, never an autostart location ─────────────────

# Shell start-up files in the home folder: a line added there runs at the
# next login or terminal.
_SHELL_RC_FILES = (
    ".bashrc", ".bash_profile", ".bash_login", ".profile",
    ".zshrc", ".zprofile", ".zshenv", ".zlogin",
)


def _autostart_locations() -> tuple[list[Path], list[Path]]:
    """(folders, files) where a written file runs at the next login."""
    home = Path.home()
    folders = [home / ".config" / "autostart", home / "Library" / "LaunchAgents"]
    for var in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(var)
        if base:
            folders.append(
                Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            )
    files = [home / name for name in _SHELL_RC_FILES]
    resolved: tuple[list[Path], list[Path]] = ([], [])
    for source, target in ((folders, resolved[0]), (files, resolved[1])):
        for path in source:
            try:
                target.append(resolve_path(path))
            except (OSError, RuntimeError):
                pass
    return resolved


def _folded(path: Path) -> PurePath:
    # Windows and macOS file names are case-insensitive by default; macOS
    # also treats composed and decomposed accents ("é" / "e" + "´") alike.
    return PurePath(unicodedata.normalize("NFC", str(path)).casefold())


def write_denied_reason(
    target: Path, allowed_suffixes: Optional[Iterable[str]] = None
) -> Optional[str]:
    """Why a macro may not write the resolved *target*, beyond
    protected_reason(), or None: an autostart location, or (with
    *allowed_suffixes*, e.g. {".html", ".txt"}) a file type the writing
    function does not produce."""
    folded = _folded(target)
    folders, files = _autostart_locations()
    if any(folded == _folded(f) for f in files) or any(
        folded.is_relative_to(_folded(f)) for f in folders
    ):
        return "it is an autostart location"
    if allowed_suffixes is not None:
        allowed = sorted({s.lower() for s in allowed_suffixes})
        # A ":" would name an NTFS alternate data stream ("a.html:x.bat").
        if ":" in target.name or target.suffix.lower() not in allowed:
            return f"only these file types can be written: {' '.join(allowed)}"
    return None


def protected_inside(folder: str | Path) -> list[Path]:
    """OpenSAK's own data in *folder* or below it: the data and database
    folders, opensak.json, bootstrap.json, the token and every listed
    database. A file inside one of the listed folders is not repeated.

    Macros can never write these anyway; Settings uses this to warn before
    write access is granted to such a folder (e.g. the home folder).
    """
    try:
        root = resolve_path(folder)
    except (OSError, RuntimeError):
        return []
    folders, files = _protected_locations()
    try:
        from opensak.db.manager import get_db_manager

        for db in get_db_manager().databases:
            try:
                files.append(resolve_path(db.path))
            except (OSError, RuntimeError):
                pass
    except Exception:
        pass
    # Same comparison as protected_reason(), so the warning matches what
    # macros are actually refused.
    inside_folders = sorted({f for f in folders if _Location(f).is_inside(root)})
    inside_files = sorted({
        f for f in files
        if (location := _Location(f)).is_inside(root)
        and not any(location.is_inside(d) for d in inside_folders)
    })
    return inside_folders + inside_files


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


def resolve_macro_path(path: str | Path, kind: str = "use") -> Path:
    """resolve_path() for a path a macro supplied.

    On Windows a path that does not name a plain file is refused (see
    windows_path_reason()), both as given — before resolve(), which would
    open "\\\\.\\COM3" to look at it — and once resolved. Raises
    FolderAccessDenied (*kind*, e.g. "read", goes into its message), also
    when the path cannot be resolved.
    """
    if _WINDOWS:
        reason = windows_path_reason(expand_path(path))
        if reason is not None:
            raise FolderAccessDenied(f"macros may not {kind} {path} — {reason}")
    try:
        target = resolve_path(path)
    except (OSError, RuntimeError, ValueError) as exc:   # RuntimeError: symlink loop
        raise FolderAccessDenied(f"cannot resolve {path}: {exc}") from None
    reason = windows_path_reason(target) if _WINDOWS else None
    if reason is not None:
        raise FolderAccessDenied(f"macros may not {kind} {target} — {reason}")
    return target


def check_access(
    path: str | Path,
    write: bool = False,
    permissions: Optional[Iterable[FolderPermission]] = None,
    allow_database: bool = False,
    allowed_suffixes: Optional[Iterable[str]] = None,
) -> Path:
    """Return the resolved *path* if a permitted folder grants the access.

    Raises FolderAccessDenied otherwise. *permissions* defaults to the
    saved list (load_permissions()); *allow_database* as for
    protected_reason(). A write is also refused for an autostart location,
    and for a suffix not in *allowed_suffixes* (see write_denied_reason()).
    On Windows, a path that does not name a plain file is refused, both as
    given and once resolved (see windows_path_reason()).
    """
    if permissions is None:
        permissions = load_permissions()
    kind = "write" if write else "read"
    target = resolve_macro_path(path, kind)
    reason = protected_reason(target, allow_database)
    if reason is None and write:
        reason = write_denied_reason(target, allowed_suffixes)
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
    """Add read (or write) on *folder* to the list saved in Settings, for
    all macros. (A folder approved while a macro runs is stored with that
    macro instead, see db_access.grant_folder().)"""
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
    allowed_suffixes: Optional[Iterable[str]] = None,
) -> Path:
    """Write *data* to *path* for a macro and return the resolved path
    (see safe_write_stream)."""
    payload = data.encode(encoding) if isinstance(data, str) else data
    return safe_write_stream(path, lambda f: f.write(payload), permissions,
                             allowed_suffixes=allowed_suffixes)[0]


def _refuse_link(path: str | Path, kind: str) -> None:
    """Raise if *path* itself (not a folder above it) is a symbolic link or
    junction: resolve() follows it, so removing the resolved path would
    remove the file it points to, not the link."""
    try:
        unresolved = Path(os.path.abspath(expand_path(path)))
        is_link = unresolved.is_symlink() or (
            hasattr(unresolved, "is_junction") and unresolved.is_junction()
        )
    except OSError:
        is_link = False
    if is_link:
        raise FolderAccessDenied(f"macros may not {kind} {path} — it is a link")


def safe_copy(
    source: Path,
    path: str | Path,
    permissions: Optional[Iterable[FolderPermission]] = None,
    allowed_suffixes: Optional[Iterable[str]] = None,
    max_bytes: Optional[int] = None,
) -> Path:
    """Copy the already read-checked, resolved file *source* to *path* for a
    macro through safe_write_stream() (so every check of a write applies)
    and return the resolved target. The modification time is kept."""
    st = source.stat()
    if max_bytes is not None and st.st_size > max_bytes:
        raise FolderAccessDenied(
            f"macros may not copy {source} — it is larger than "
            f"{max_bytes // (1024 * 1024)} MB"
        )
    # resolve() so that a target naming the source through another path is caught.
    if resolve_path(path) == source:
        raise FolderAccessDenied(f"macros may not copy {source} onto itself")

    def copy(f: IO[Any]) -> None:
        import shutil

        with open(source, "rb") as src:
            shutil.copyfileobj(src, f, 1024 * 1024)

    final = safe_write_stream(path, copy, permissions, allowed_suffixes=allowed_suffixes)[0]
    try:
        os.utime(final, ns=(st.st_atime_ns, st.st_mtime_ns))
    except OSError:
        pass
    return final


def safe_delete(
    path: str | Path,
    permissions: Optional[Iterable[FolderPermission]] = None,
    allowed_suffixes: Optional[Iterable[str]] = None,
    folder: bool = False,
) -> bool:
    """Delete the file (or with *folder*, the empty folder) *path* for a
    macro; False if there is nothing to delete.

    Deleting is writing: check_access(write=True) applies, with the deny
    list, autostart locations and *allowed_suffixes* (files only). A link
    is refused (see _refuse_link), and the check is repeated right before
    the delete, as in safe_write_stream(). Never deletes a folder that is
    not empty. Raises FolderAccessDenied or OSError.
    """
    if permissions is not None:
        permissions = list(permissions)    # iterated twice
    suffixes = None if folder or allowed_suffixes is None else tuple(allowed_suffixes)
    kind = "delete the folder" if folder else "delete"
    _refuse_link(path, kind)
    target = check_access(path, write=True, permissions=permissions,
                          allowed_suffixes=suffixes)
    if not target.exists():
        return False
    if folder != target.is_dir():
        what = "a folder" if target.is_dir() else "not a folder"
        raise FolderAccessDenied(f"macros may not {kind} {target} — it is {what}")
    if folder:
        if _is_root(target):
            raise FolderAccessDenied(f"macros may not {kind} {target} — it is a drive")
        listed = load_permissions() if permissions is None else permissions
        for perm in listed:
            try:
                same = resolve_path(perm.path) == target
            except (OSError, RuntimeError):
                same = False
            if same:
                raise FolderAccessDenied(
                    f"macros may not {kind} {target} — it is a folder in "
                    "Settings → Folder permissions"
                )
    _refuse_link(path, kind)
    final = check_access(path, write=True, permissions=permissions, allowed_suffixes=suffixes)
    if final != target:
        raise FolderAccessDenied(
            f"macros may not {kind} {path} — the path changed (now {final})"
        )
    if folder:
        os.rmdir(final)     # fails for a folder that is not empty
    else:
        os.remove(final)
    return True


def safe_write_stream(
    path: str | Path,
    write: Callable[[IO[Any]], T],
    permissions: Optional[Iterable[FolderPermission]] = None,
    binary: bool = True,
    encoding: str = "utf-8",
    allowed_suffixes: Optional[Iterable[str]] = None,
) -> tuple[Path, T]:
    """Call *write(f)* to write *path* for a macro; return the resolved path
    and what *write* returned. *f* is a binary file, or with
    ``binary=False`` a text file in *encoding* with the platform's newlines.

    The only way macro API functions may write files (safe_write() for data
    already in memory):
      * check_access(write=True), so the deny list and folder list apply
        (and *allowed_suffixes*, the file types the caller produces);
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
    if allowed_suffixes is not None:
        allowed_suffixes = tuple(allowed_suffixes)
    target = check_access(
        path, write=True, permissions=permissions, allowed_suffixes=allowed_suffixes
    )
    _refuse_hard_link(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o666)
    try:
        with (os.fdopen(fd, "wb") if binary else os.fdopen(fd, "w", encoding=encoding)) as f:
            result = write(f)
        final = check_access(
            path, write=True, permissions=permissions,
            allowed_suffixes=allowed_suffixes,
        )
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
    return final, result

"""
src/opensak/macro/db_access.py — what the user allowed an individual Lua
macro: changing OpenSAK's databases (#1034), using folders and contacting
web sites (#1079), keyed on the macro's file and code (#1091).

Reading a database needs no approval. Before a macro's first write
(opensak.update/insert/sql_write, set_corrected, clear_corrected) the
runtime asks the user, through OpenSAK's own dialog, never through the
macro, whether *this macro* may change the OpenSAK databases:

  * Deny — the write fails; not asked again during this run;
  * Until OpenSAK closes — the macro may change databases until the
    application quits (kept in memory only);
  * Always — stored in opensak.json under "macros.approvals" and listed in
    Settings → Folder permissions, where it can be removed again;
  * While I edit it — only offered for a run from an external editor
    (`opensak --run-macro`): until OpenSAK closes, whatever the file holds.

A folder a macro is allowed "always" (permissions.py asks for folders that
no listed folder grants) is stored in the same record, so it belongs to
that macro only. The folders listed in Settings by hand, and the built-in
defaults, stay shared by all macros (permissions.py). A web site allowed
"always" (net_access.py) is stored there too; there is no shared list of
web sites.

An approval belongs to the macro file's path *and* its code: the SHA-256
of the source that runs (source_hash()). A file changed in any other way —
replaced by a downloaded or merged update, edited in another editor — loses
what it was allowed. The first time the changed macro needs any of it, the
user is asked once whether to allow it all again, and told what changed
(MacroChange); a "Permissions:" header cannot stand in for this, since an
update can simply declare nothing new. Declined, the old record is left
alone for the rest of the run, and is replaced by a new one for the new
code as soon as the user allows that something always.

Editing the macro in OpenSAK's macro editor keeps its approvals: the editor
passes the hashes of the versions it loaded, saved or ran (*edited_from*),
so its unsaved text counts as approved, and carry_over() moves the record
to the new code when the editor saves. Only versions the editor had count,
so opening a replaced file and saving it does not approve it.

"While I edit it" is keyed on the path alone, so a macro developer using
their own editor is not asked after every save: the macro's record then
counts whatever the file holds, until OpenSAK closes. It applies only to
runs started from an external editor, never to the Macro manager, OpenSAK's
macro editor or opensak.run_macro().

Moving or renaming the file needs a new approval. Writing covers every
database, but a macro still only ever writes the active one. An unsaved
macro (an editor buffer without a file) can only be approved until
OpenSAK closes; that approval is shared by all unsaved macros, whatever
their code (they can only be typed in OpenSAK's editor), and a folder or
web site can be allowed to it for one run only. A macro started with
opensak.run_macro() needs its own approvals.

Each record also keeps the macro's Version and declared permissions, so a
changed macro can be described. Older versions stored only a list of paths
under "macros.write_always"; it is converted once, approving the files as
they are at that moment. Folders allowed always by older versions are in
the shared list, which is left as it is.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

from opensak.macro.metadata import MacroInfo, added_declarations, parse_header
from opensak.macro.net_access import HostPermission, with_host
from opensak.macro.permissions import FolderPermission, with_grant

STORE_KEY = "macros.approvals"
LEGACY_STORE_KEY = "macros.write_always"

# The key for macros without a file
_UNSAVED = "<unsaved>"


class WriteApproval(Enum):
    """The user's answer when a macro wants to change a database, or when
    a changed macro asks for what it was allowed before."""

    SESSION = "session"   # until OpenSAK closes
    ALWAYS = "always"     # stored in Settings
    EDITING = "editing"   # until OpenSAK closes, whatever the file holds
    DENY = "deny"


@dataclass(frozen=True)
class Approval:
    """What the user allowed the macro file *path* with the code that
    hashes to *sha256*: changing databases (*write*), *folders* and web
    sites (*hosts*), and what its header said then."""

    path: str
    sha256: str
    version: str = ""
    permissions_declared: bool = False
    declared: tuple[str, ...] = ()
    write: bool = True
    folders: tuple[FolderPermission, ...] = ()
    hosts: tuple[HostPermission, ...] = ()

    @property
    def anything(self) -> bool:
        """True if the record allows anything at all."""
        return self.write or bool(self.folders) or bool(self.hosts)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "version": self.version,
            "permissions_declared": self.permissions_declared,
            "declared": list(self.declared),
            "write": self.write,
            "folders": [f.to_dict() for f in self.folders],
            "hosts": [h.to_dict() for h in self.hosts],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Optional["Approval"]:
        path, sha = data.get("path"), data.get("sha256")
        if not (isinstance(path, str) and path and isinstance(sha, str) and sha):
            return None
        declared = data.get("declared")
        folders = data.get("folders")
        hosts = data.get("hosts")
        parsed_hosts = (
            HostPermission.from_dict(h) for h in (hosts if isinstance(hosts, list) else [])
            if isinstance(h, dict)
        )
        return cls(
            path=path,
            sha256=sha,
            version=str(data.get("version") or ""),
            permissions_declared=bool(data.get("permissions_declared")),
            declared=tuple(str(d) for d in declared) if isinstance(declared, list) else (),
            write=bool(data.get("write")),
            folders=tuple(
                FolderPermission.from_dict(f)
                for f in (folders if isinstance(folders, list) else [])
                if isinstance(f, dict) and f.get("path")
            ),
            hosts=tuple(h for h in parsed_hosts if h is not None),
        )


@dataclass(frozen=True)
class MacroChange:
    """How a macro differs from what was approved: the Version before and
    now, and what it declares now that it did not then; *write*,
    *folders* and *hosts* are what it was allowed."""

    old_version: str = ""
    new_version: str = ""
    added: tuple[str, ...] = ()
    write: bool = False
    folders: tuple[FolderPermission, ...] = ()
    hosts: tuple[HostPermission, ...] = ()


# Approvals to write "until OpenSAK closes" (keys from macro_key()), and the
# paths approved "while I edit it", with whether that includes writing.
_session: dict[str, Approval] = {}
_editing: dict[str, bool] = {}


def source_hash(source: str) -> str:
    """The SHA-256 of macro source *source* (a leading BOM left out), as
    the runtime runs it: every entry point reads files with
    read_macro_source(), so newlines are already "\\n"."""
    return hashlib.sha256(source.lstrip("﻿").encode("utf-8")).hexdigest()


def read_macro_source(path: str | Path) -> str:
    """The source of the macro file *path* (UTF-8, a BOM removed). Raises
    OSError or UnicodeDecodeError."""
    return Path(path).read_text(encoding="utf-8-sig")


def macro_key(macro: Optional[str | Path]) -> str:
    """A comparable form of a macro path: resolved, case-folded where the
    filesystem ignores case. None (an unsaved macro) has its own key. Also
    the owner of the tables a macro creates in the companion database."""
    if macro is None:
        return _UNSAVED
    try:
        resolved = Path(macro).expanduser().resolve()
    except (OSError, RuntimeError):
        resolved = Path(macro)
    return os.path.normcase(str(resolved))


def _approval(
    macro: Optional[str | Path], sha: str, info: Optional[MacroInfo], write: bool = True,
    folders: tuple[FolderPermission, ...] = (),
) -> Approval:
    path = _UNSAVED if macro is None else str(Path(macro).resolve())
    record = Approval(path, sha, write=write, folders=folders)
    return record if info is None else _stamped(record, sha, info)


def _stamped(record: Approval, sha: str, info: Optional[MacroInfo]) -> Approval:
    """*record* moved to the code hashing to *sha*, with header *info*."""
    if info is None:
        return replace(record, sha256=sha)
    return replace(record, sha256=sha, version=info.version,
                   permissions_declared=info.permissions_declared, declared=info.declared)


def _migrate(store) -> list[Approval]:
    """Convert the path list of older versions: each file is approved as
    it is now; a path whose file cannot be read is dropped."""
    raw = store.get(LEGACY_STORE_KEY)
    records: list[Approval] = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, str) or not p:
            continue
        try:
            source = read_macro_source(p)
        except (OSError, UnicodeDecodeError):
            continue
        records.append(_approval(p, source_hash(source), parse_header(source, Path(p))))
    return records


def approvals() -> list[Approval]:
    """The macros the user allowed something permanently, as stored."""
    from opensak.settings_store import get_store

    store = get_store()
    if store.get(LEGACY_STORE_KEY) is not None:
        records = _migrate(store)
        existing = store.get(STORE_KEY)
        if not isinstance(existing, list) or not existing:
            _save(records)
        store.delete(LEGACY_STORE_KEY)
    raw = store.get(STORE_KEY)
    if not isinstance(raw, list):
        return []
    parsed = [Approval.from_dict(e) for e in raw if isinstance(e, dict)]
    return [a for a in parsed if a is not None and a.anything]


def _save(records: Iterable[Approval]) -> None:
    from opensak.settings_store import get_store

    store = get_store()
    unique: dict[str, Approval] = {}
    for a in records:
        unique.setdefault(macro_key(a.path), a)
    if unique:
        store.set(STORE_KEY, [a.to_dict() for a in unique.values()])
    elif store.get(STORE_KEY) is not None:
        store.delete(STORE_KEY)


def _put(record: Approval) -> None:
    """Store *record*, replacing the macro's previous one."""
    key = macro_key(record.path)
    records = approvals()
    for i, a in enumerate(records):
        if macro_key(a.path) == key:
            records[i] = record
            break
    else:
        records.append(record)
    _save(records)


def always_approved() -> list[str]:
    """The paths of the macros allowed something permanently."""
    return [a.path for a in approvals()]


def save_always_approved(paths: Iterable[str]) -> None:
    """Keep only the permanent approvals of *paths* (Settings removes
    macros this way; it cannot add any). A macro dropped from the list
    loses everything at once, also what it may until OpenSAK closes."""
    wanted = {macro_key(p) for p in paths}
    current = approvals()
    dropped = {macro_key(a.path) for a in current} - wanted
    for key in dropped:
        _session.pop(key, None)
        _editing.pop(key, None)
    _save(a for a in current if macro_key(a.path) in wanted)


def _matches(approval: Optional[Approval], sha: str, edited_from: Iterable[str]) -> bool:
    return approval is not None and (approval.sha256 == sha or approval.sha256 in edited_from)


def _record(macro: Optional[str | Path]) -> Optional[Approval]:
    if macro is None:
        return None
    key = macro_key(macro)
    return next((a for a in approvals() if macro_key(a.path) == key), None)


def current_approval(
    macro: Optional[str | Path], sha: str, edited_from: Iterable[str] = (),
    editing: bool = False,
) -> Optional[Approval]:
    """The stored record of the macro at *macro* if it applies to the code
    hashing to *sha*: approved for that code, for a version OpenSAK's
    macro editor had (*edited_from*), or — for a run from an external
    editor (*editing*) — "while I edit it"."""
    record = _record(macro)
    if record is None:
        return None
    if _matches(record, sha, set(edited_from)):
        return record
    if editing and macro_key(macro) in _editing:
        return record
    return None


def stale_approval(
    macro: Optional[str | Path], sha: str, edited_from: Iterable[str] = (),
    editing: bool = False,
) -> Optional[Approval]:
    """The stored record of the macro at *macro* if its code has changed
    since (see current_approval)."""
    record = _record(macro)
    if record is None or current_approval(macro, sha, edited_from, editing) is not None:
        return None
    return record


def approved_folders(
    macro: Optional[str | Path], sha: str, edited_from: Iterable[str] = (),
    editing: bool = False,
) -> list[FolderPermission]:
    """The folders allowed always to the macro at *macro* with this code."""
    record = current_approval(macro, sha, edited_from, editing)
    return list(record.folders) if record is not None else []


def approved_hosts(
    macro: Optional[str | Path], sha: str, edited_from: Iterable[str] = (),
    editing: bool = False,
) -> list[HostPermission]:
    """The web sites allowed always to the macro at *macro* with this code."""
    record = current_approval(macro, sha, edited_from, editing)
    return list(record.hosts) if record is not None else []


def is_approved(
    macro: Optional[str | Path], sha: str, edited_from: Iterable[str] = (),
    editing: bool = False,
) -> bool:
    """True if the macro at *macro* (None: an unsaved one) whose source
    hashes to *sha* may change databases without asking.

    *edited_from*: hashes of the versions OpenSAK's macro editor had of
    the text it runs (it is an edit of those). *editing*: the run comes
    from an external editor, so a "while I edit it" approval counts."""
    key = macro_key(macro)
    if macro is None:
        return key in _session      # only typed in OpenSAK's editor
    edited_from = set(edited_from)
    if editing and _editing.get(key):
        return True
    if _matches(_session.get(key), sha, edited_from):
        return True
    record = current_approval(macro, sha, edited_from, editing)
    return record is not None and record.write


def session_approval(macro: Optional[str | Path]) -> Optional[Approval]:
    """The approval to write until OpenSAK closes the macro at *macro*
    had, whatever its code was then; None if it has none."""
    return None if macro is None else _session.get(macro_key(macro))


def change_since(approval: Approval, info: MacroInfo) -> MacroChange:
    """How the macro *info* differs from what *approval* approved."""
    return MacroChange(
        old_version=approval.version,
        new_version=info.version,
        added=added_declarations(approval.declared, approval.permissions_declared, info),
        write=approval.write,
        folders=approval.folders,
        hosts=approval.hosts,
    )


def approve(
    macro: Optional[str | Path], answer: WriteApproval, sha: str,
    info: Optional[MacroInfo] = None, edited_from: Iterable[str] = (),
    editing: bool = False,
) -> None:
    """Record the user's *answer* to whether the macro at *macro*, whose
    source hashes to *sha*, may change databases; *info* is its header.
    DENY records nothing here — the runtime remembers it for the current
    run only. An unsaved macro (None) cannot be approved always; ALWAYS
    then counts as SESSION. *edited_from* and *editing* as for
    current_approval(): the folders of a record that applies are kept."""
    if answer not in (WriteApproval.SESSION, WriteApproval.ALWAYS, WriteApproval.EDITING):
        return
    key = macro_key(macro)
    if answer is WriteApproval.EDITING:
        if macro is not None:
            _editing[key] = True
        return
    _session[key] = _approval(macro, sha, info)
    if answer is WriteApproval.ALWAYS and macro is not None:
        record = current_approval(macro, sha, edited_from, editing)
        if record is not None:
            _put(replace(record, write=True))
        else:
            _put(_approval(macro, sha, info))


def grant_folder(
    macro: str | Path, folder: Path, write: bool, sha: str,
    info: Optional[MacroInfo] = None, edited_from: Iterable[str] = (),
    editing: bool = False,
) -> None:
    """Allow the macro at *macro*, whose source hashes to *sha*, to read
    (or write) *folder* always. Added to its record if that applies to
    this code (see current_approval); otherwise the record is replaced by
    one for this code with just this folder."""
    record = current_approval(macro, sha, edited_from, editing)
    if record is None:
        record = _approval(macro, sha, info, write=False)
    folders = tuple(with_grant(record.folders, folder, write))
    _put(replace(record, folders=folders))


def grant_host(
    macro: str | Path, host: str, local: bool, sha: str,
    info: Optional[MacroInfo] = None, edited_from: Iterable[str] = (),
    editing: bool = False,
) -> None:
    """Allow the macro at *macro*, whose source hashes to *sha*, to contact
    the web site *host* always (with *local*, also on the local network),
    like grant_folder()."""
    record = current_approval(macro, sha, edited_from, editing)
    if record is None:
        record = _approval(macro, sha, info, write=False)
    _put(replace(record, hosts=tuple(with_host(record.hosts, host, local))))


def renew(
    macro: str | Path, answer: WriteApproval, sha: str, info: Optional[MacroInfo] = None,
) -> bool:
    """The user's *answer* when the changed macro at *macro* asked for
    what it was allowed before: ALWAYS moves its record to the code
    hashing to *sha*; EDITING lets the record count until OpenSAK closes,
    whatever the file holds. True if the record applies now."""
    record = _record(macro)
    if record is None:
        return False
    if answer is WriteApproval.ALWAYS:
        _put(_stamped(record, sha, info))
        return True
    if answer is WriteApproval.EDITING:
        key = macro_key(macro)
        _editing[key] = _editing.get(key, False) or record.write
        return True
    return False


def carry_over(
    macro: str | Path, edited_from: Iterable[str], sha: str, info: Optional[MacroInfo] = None,
) -> bool:
    """OpenSAK's macro editor saved new code (hashing to *sha*) to *macro*:
    approvals of one of the versions it had (*edited_from*) move to the
    new code. True if one did."""
    key = macro_key(macro)
    edited_from = set(edited_from)
    moved = False
    session = _session.get(key)
    if session is not None and session.sha256 in edited_from:
        _session[key] = _stamped(session, sha, info)
        moved = True
    record = _record(macro)
    if record is not None and record.sha256 in edited_from:
        _put(_stamped(record, sha, info))
        moved = True
    return moved


def restore_approvals(records: Iterable[Approval]) -> int:
    """A restore from a backup (#1137) put macro files back: store each of
    *records*, the approval the backup had for the file *record.path*,
    replacing the macro's previous one. Only when the file holds exactly
    the code the record approves, and not when the macro already has a
    record for that code (it may allow more than the backup's). The store
    is written once, after all of them. The number stored."""
    stored = approvals()
    index = {macro_key(a.path): i for i, a in enumerate(stored)}
    count = 0
    for record in records:
        try:
            sha = source_hash(read_macro_source(record.path))
        except (OSError, UnicodeDecodeError):
            continue
        if sha != record.sha256 or not record.anything:
            continue
        key = macro_key(record.path)
        i = index.get(key)
        if i is not None and stored[i].sha256 == sha:
            continue
        record = replace(record, path=str(Path(record.path).resolve()))
        if i is None:
            index[key] = len(stored)
            stored.append(record)
        else:
            stored[i] = record
        count += 1
    if count:
        _save(stored)
    return count


def reset_session() -> None:
    """Forget the approvals given until OpenSAK closes (used by tests)."""
    _session.clear()
    _editing.clear()

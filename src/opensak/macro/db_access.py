"""
src/opensak/macro/db_access.py — which Lua macros may change OpenSAK's
databases (#1034, #1091).

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

An approval belongs to the macro file's path *and* its code: the SHA-256
of the source that runs (source_hash()). A file changed in any other way —
replaced by a downloaded or merged update, edited in another editor — is
asked about again, and the question says what changed (WriteChange). A
"Permissions:" header cannot stand in for this, since an update can
simply declare nothing new.

Editing the macro in OpenSAK's macro editor keeps the approval: the editor
passes the hashes of the versions it loaded, saved or ran (*edited_from*),
so its unsaved text counts as approved, and carry_over() moves the
approval to the new code when the editor saves. Only versions the editor
had count, so opening a replaced file and saving it does not approve it.

"While I edit it" is keyed on the path alone, so a macro developer using
their own editor is not asked after every save. It applies only to runs
started from an external editor, never to the Macro manager, OpenSAK's
macro editor or opensak.run_macro().

Moving or renaming the file needs a new approval. It covers every
database, but a macro still only ever writes the active one. An unsaved
macro (an editor buffer without a file) can only be approved until
OpenSAK closes; that approval is shared by all unsaved macros, whatever
their code (they can only be typed in OpenSAK's editor). A macro
started with opensak.run_macro() needs its own approval.

Each record also keeps the macro's Version and declared permissions, so a
changed macro can be described. Older versions stored only a list of paths
under "macros.write_always"; it is converted once, approving the files as
they are at that moment.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

from opensak.macro.metadata import MacroInfo, added_declarations, parse_header

STORE_KEY = "macros.approvals"
LEGACY_STORE_KEY = "macros.write_always"

# The key for macros without a file
_UNSAVED = "<unsaved>"


class WriteApproval(Enum):
    """The user's answer when a macro wants to change a database."""

    SESSION = "session"   # until OpenSAK closes
    ALWAYS = "always"     # stored in Settings
    EDITING = "editing"   # until OpenSAK closes, whatever the file holds
    DENY = "deny"


@dataclass(frozen=True)
class Approval:
    """What the user approved: the macro file *path* with the code that
    hashes to *sha256*, and what its header said then."""

    path: str
    sha256: str
    version: str = ""
    permissions_declared: bool = False
    declared: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "version": self.version,
            "permissions_declared": self.permissions_declared,
            "declared": list(self.declared),
            "write": True,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Optional["Approval"]:
        path, sha = data.get("path"), data.get("sha256")
        if not (isinstance(path, str) and path and isinstance(sha, str) and sha):
            return None
        declared = data.get("declared")
        return cls(
            path=path,
            sha256=sha,
            version=str(data.get("version") or ""),
            permissions_declared=bool(data.get("permissions_declared")),
            declared=tuple(str(d) for d in declared) if isinstance(declared, list) else (),
        )


@dataclass(frozen=True)
class WriteChange:
    """How an approved macro differs from what was approved: the Version
    before and now, and what it declares now that it did not then."""

    old_version: str = ""
    new_version: str = ""
    added: tuple[str, ...] = field(default=())


# Approvals "until OpenSAK closes" (keys from _key()), and the paths
# approved "while I edit it".
_session: dict[str, Approval] = {}
_editing: set[str] = set()


def source_hash(source: str) -> str:
    """The SHA-256 of macro source *source* (a leading BOM left out), as
    the runtime runs it: every entry point reads files with
    read_macro_source(), so newlines are already "\\n"."""
    return hashlib.sha256(source.lstrip("﻿").encode("utf-8")).hexdigest()


def read_macro_source(path: str | Path) -> str:
    """The source of the macro file *path* (UTF-8, a BOM removed). Raises
    OSError or UnicodeDecodeError."""
    return Path(path).read_text(encoding="utf-8-sig")


def _key(macro: Optional[str | Path]) -> str:
    """A comparable form of a macro path: resolved, case-folded where the
    filesystem ignores case. None (an unsaved macro) has its own key."""
    if macro is None:
        return _UNSAVED
    try:
        resolved = Path(macro).expanduser().resolve()
    except (OSError, RuntimeError):
        resolved = Path(macro)
    return os.path.normcase(str(resolved))


def macro_key(macro: Optional[str | Path]) -> str:
    """The key of the macro at *macro*, as approvals store it. Also the
    owner of the tables a macro creates in the companion database."""
    return _key(macro)


def _approval(macro: Optional[str | Path], sha: str, info: Optional[MacroInfo]) -> Approval:
    path = _UNSAVED if macro is None else str(Path(macro).resolve())
    if info is None:
        return Approval(path, sha)
    return Approval(path, sha, info.version, info.permissions_declared, info.declared)


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
    """The macros approved permanently, as stored."""
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
    return [
        a for a in (Approval.from_dict(e) for e in raw if isinstance(e, dict) and e.get("write"))
        if a is not None
    ]


def _save(records: Iterable[Approval]) -> None:
    from opensak.settings_store import get_store

    store = get_store()
    unique: dict[str, Approval] = {}
    for a in records:
        unique.setdefault(_key(a.path), a)
    if unique:
        store.set(STORE_KEY, [a.to_dict() for a in unique.values()])
    elif store.get(STORE_KEY) is not None:
        store.delete(STORE_KEY)


def always_approved() -> list[str]:
    """The paths of the macros approved permanently."""
    return [a.path for a in approvals()]


def save_always_approved(paths: Iterable[str]) -> None:
    """Keep only the permanent approvals of *paths* (Settings removes
    macros this way; it cannot add any). A macro dropped from the list
    loses its approval at once, also the ones until OpenSAK closes."""
    wanted = {_key(p) for p in paths}
    current = approvals()
    dropped = {_key(a.path) for a in current} - wanted
    for key in dropped:
        _session.pop(key, None)
        _editing.discard(key)
    _save(a for a in current if _key(a.path) in wanted)


def _matches(approval: Optional[Approval], sha: str, edited_from: Iterable[str]) -> bool:
    return approval is not None and (approval.sha256 == sha or approval.sha256 in edited_from)


def is_approved(
    macro: Optional[str | Path], sha: str, edited_from: Iterable[str] = (),
    editing: bool = False,
) -> bool:
    """True if the macro at *macro* (None: an unsaved one) whose source
    hashes to *sha* may change databases without asking.

    *edited_from*: hashes of the versions OpenSAK's macro editor had of
    the text it runs (it is an edit of those). *editing*: the run comes
    from an external editor, so a "while I edit it" approval counts."""
    key = _key(macro)
    if macro is None:
        return key in _session      # only typed in OpenSAK's editor
    edited_from = set(edited_from)
    if editing and key in _editing:
        return True
    if _matches(_session.get(key), sha, edited_from):
        return True
    return any(_key(a.path) == key and _matches(a, sha, edited_from) for a in approvals())


def stored_approval(macro: Optional[str | Path]) -> Optional[Approval]:
    """The approval the macro at *macro* had (permanent or until OpenSAK
    closes), whatever its code was then; None if it never had one."""
    if macro is None:
        return None
    key = _key(macro)
    for a in approvals():
        if _key(a.path) == key:
            return a
    return _session.get(key)


def change_since(approval: Approval, info: MacroInfo) -> WriteChange:
    """How the macro *info* differs from what *approval* approved."""
    return WriteChange(
        old_version=approval.version,
        new_version=info.version,
        added=added_declarations(approval.declared, approval.permissions_declared, info),
    )


def approve(
    macro: Optional[str | Path], answer: WriteApproval, sha: str,
    info: Optional[MacroInfo] = None,
) -> None:
    """Record the user's *answer* for the macro at *macro* whose source
    hashes to *sha*; *info* is its header. DENY records nothing here — the
    runtime remembers it for the current run only. An unsaved macro (None)
    cannot be approved always; ALWAYS then counts as SESSION."""
    if answer not in (WriteApproval.SESSION, WriteApproval.ALWAYS, WriteApproval.EDITING):
        return
    key = _key(macro)
    if answer is WriteApproval.EDITING:
        if macro is not None:
            _editing.add(key)
        return
    record = _approval(macro, sha, info)
    _session[key] = record
    if answer is WriteApproval.ALWAYS and macro is not None:
        records = approvals()
        for i, a in enumerate(records):
            if _key(a.path) == key:
                records[i] = record
                break
        else:
            records.append(record)
        _save(records)


def carry_over(
    macro: str | Path, edited_from: Iterable[str], sha: str, info: Optional[MacroInfo] = None,
) -> bool:
    """OpenSAK's macro editor saved new code (hashing to *sha*) to *macro*:
    an approval of one of the versions it had (*edited_from*) moves to the
    new code. True if one did."""
    key = _key(macro)
    edited_from = set(edited_from)
    moved = False
    session = _session.get(key)
    if session is not None and session.sha256 in edited_from:
        _session[key] = _approval(macro, sha, info)
        moved = True
    records = approvals()
    for i, a in enumerate(records):
        if _key(a.path) == key and a.sha256 in edited_from:
            records[i] = _approval(a.path, sha, info)
            _save(records)
            moved = True
            break
    return moved


def reset_session() -> None:
    """Forget the approvals given until OpenSAK closes (used by tests)."""
    _session.clear()
    _editing.clear()

"""
src/opensak/macro/repository.py — macros from the shared OpenSAK-Macros
repository (#1342): its index, which local macros came from it, and
installing, updating and removing them.

The repository publishes ``index.json`` (INDEX_URL) after every push to its
``main`` branch; the format is described in its MAINTAINERS.md. Every
published macro is a folder ``macros/<id>/`` with ``<id>.lua`` and other
files (README, images). For every file the index has its size, SHA-256 and a
download URL pinned to a commit, and for the macro file the same for every
version that was ever published. ``withdrawn[]`` lists removed macros with
the hashes of all their versions.

A local macro counts as one from the index only when

- the Macro manager installed it (an install record, kept in opensak.json
  under "macros.installed"), or
- its file hashes to a published version (or withdrawn one). It is then
  "adopted": a record is written, so it stays known after it is edited.

A name or folder that happens to match a published macro is not enough,
so the user's own macros are never touched. Matching happens locally;
nothing about the local macros is sent anywhere. A macro the user chose to
stop tracking ("macros.untracked") is never matched again until they track
it again.

Installing puts every file of a macro into ``<macros folder>/<id>/``.
Updating moves the files it replaces (and the files the new version no
longer has, if unchanged since they were installed) into the hidden
``.previous/`` folder next to them, which discover_macros() leaves out.
Every download is checked against the size and SHA-256 in the index before
anything is written. Removing never deletes the macro's companion database
(``<name>.db3``) unless asked to: it holds the user's data.

Nothing here runs without the user: there is no automatic installing.
Approvals stay keyed on the code hash (#1091), so an updated macro asks for
its approvals again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Optional

from opensak.macro.metadata import NO_PERMISSIONS, MacroInfo

INDEX_URL = "https://raw.githubusercontent.com/OpenSAK-Org/OpenSAK-Macros/index/index.json"

# The newest index format this OpenSAK understands. Fields are only ever
# added within a schema; a breaking change raises it.
SUPPORTED_SCHEMA = 1

REQUEST_TIMEOUT = 15            # seconds
MAX_INDEX_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024

# Where the downloaded URLs may point: the repository's raw files only.
_ALLOWED_URL = re.compile(r"^https://raw\.githubusercontent\.com/OpenSAK-Org/OpenSAK-Macros/")
# A macro id is its folder name in the repository; it becomes a folder name
# here, so nothing that could leave the macros folder or hide the macro.
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

PREVIOUS_DIR = ".previous"
COMPANION_SUFFIX = ".db3"

INSTALLED_KEY = "macros.installed"
UNTRACKED_KEY = "macros.untracked"
CHECK_AT_STARTUP_KEY = "macros.updates.check_at_startup"
LAST_CHECK_KEY = "macros.updates.last_check"
CHECK_INTERVAL = timedelta(days=1)

# Statuses of a local macro (MacroStatus.state).
LOCAL = ""                      # not from the index (the user's own macro)
UP_TO_DATE = "up-to-date"
UPDATE = "update"
NEEDS_NEWER_API = "needs-api"
WITHDRAWN = "withdrawn"
CHANGED = "changed"
UNTRACKED = "untracked"


class RepositoryError(Exception):
    """The index could not be read or a download failed its check."""


# ── The index ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class IndexFile:
    """A file of a published macro. *name* is its path inside the macro's
    folder ("README.md", "img/shot.png")."""
    name: str
    size: int
    sha256: str
    url: str


@dataclass(frozen=True)
class IndexVersion:
    """A published version of a macro file."""
    version: str
    published: str
    requires_api: Optional[int]
    permissions: tuple[str, ...]
    network: tuple[str, ...]
    size: int
    sha256: str
    url: str


@dataclass(frozen=True)
class IndexMacro:
    id: str
    name: str
    author: str = ""
    version: str = ""
    description: str = ""
    requires_api: Optional[int] = None
    permissions: tuple[str, ...] = ()
    network: tuple[str, ...] = ()
    lua: str = ""                       # the macro file's name in files
    page: str = ""
    files: tuple[IndexFile, ...] = ()
    versions: tuple[IndexVersion, ...] = ()       # newest first
    changelog: tuple[tuple[str, str], ...] = ()   # (version, text), newest first
    updated: str = ""
    error: str = ""

    @property
    def lua_file(self) -> Optional[IndexFile]:
        return next((f for f in self.files if f.name == self.lua), None)

    @property
    def declared(self) -> tuple[str, ...]:
        """Like MacroInfo.declared: the permissions and "network:<host>"."""
        return (tuple(p for p in self.permissions if p != NO_PERMISSIONS)
                + tuple(f"network:{h}" for h in self.network))

    @property
    def permissions_declared(self) -> bool:
        return bool(self.permissions)

    def needs_newer_api(self, api_version: int) -> bool:
        return self.requires_api is not None and self.requires_api > api_version

    def version_of(self, sha256: str) -> Optional[IndexVersion]:
        return next((v for v in self.versions if v.sha256 == sha256), None)

    def changes_since(self, version: str) -> tuple[tuple[str, str], ...]:
        """The changelog entries newer than *version* (all if it is not
        listed)."""
        entries: list[tuple[str, str]] = []
        for entry in self.changelog:
            if entry[0] == version:
                break
            entries.append(entry)
        return tuple(entries)


@dataclass(frozen=True)
class WithdrawnMacro:
    id: str
    name: str
    last_version: str
    withdrawn: str
    hashes: frozenset[str]


@dataclass(frozen=True)
class MacroIndex:
    commit: str = ""
    generated: str = ""
    macros: dict[str, IndexMacro] = field(default_factory=dict)
    withdrawn: dict[str, WithdrawnMacro] = field(default_factory=dict)

    def find_hash(self, sha256: str) -> Optional[tuple[str, str]]:
        """(id, version) of the published or withdrawn macro version whose
        file hashes to *sha256*; None if there is none."""
        for macro in self.macros.values():
            version = macro.version_of(sha256)
            if version is not None:
                return macro.id, version.version
        for gone in self.withdrawn.values():
            if sha256 in gone.hashes:
                return gone.id, gone.last_version
        return None


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _strs(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(v for v in value if isinstance(v, str) and v)


def _sha(value: Any) -> str:
    text = _str(value).lower()
    return text if re.fullmatch(r"[0-9a-f]{64}", text) else ""


def _url(value: Any) -> str:
    text = _str(value)
    return text if _ALLOWED_URL.match(text) else ""


def _file_name(path: str, folder: str) -> str:
    """*path* (in the repository) relative to the macro's *folder*, or ""
    if it is not safely inside it."""
    try:
        rel = PurePosixPath(path).relative_to(PurePosixPath(folder))
    except ValueError:
        return ""
    parts = rel.parts
    if not parts or any(p in ("", ".", "..") or p.startswith(".") or "\\" in p or ":" in p
                        for p in parts):
        return ""
    return rel.as_posix()


def _parse_macro(raw: dict) -> Optional[IndexMacro]:
    macro_id = _str(raw.get("id"))
    if not _SAFE_ID.match(macro_id):
        return None
    folder = _str(raw.get("folder")) or f"macros/{macro_id}"
    files: list[IndexFile] = []
    for f in raw.get("files") or ():
        if not isinstance(f, dict):
            continue
        name, sha, url, size = (_file_name(_str(f.get("path")), folder), _sha(f.get("sha256")),
                                _url(f.get("url")), _int(f.get("size")))
        if name and sha and url and size is not None and 0 <= size <= MAX_FILE_BYTES:
            files.append(IndexFile(name, size, sha, url))
    versions: list[IndexVersion] = []
    for v in raw.get("versions") or ():
        if not isinstance(v, dict):
            continue
        sha = _sha(v.get("sha256"))
        if sha:
            versions.append(IndexVersion(
                _str(v.get("version")), _str(v.get("published")), _int(v.get("requires_api")),
                _strs(v.get("permissions")), _strs(v.get("network")),
                _int(v.get("size")) or 0, sha, _url(v.get("url"))))
    changelog = tuple(
        (_str(c.get("version")), _str(c.get("text")))
        for c in raw.get("changelog") or () if isinstance(c, dict) and _str(c.get("text")))
    return IndexMacro(
        id=macro_id,
        name=_str(raw.get("name")) or macro_id,
        author=_str(raw.get("author")),
        version=_str(raw.get("version")),
        description=_str(raw.get("description")),
        requires_api=_int(raw.get("requires_api")),
        permissions=_strs(raw.get("permissions")),
        network=_strs(raw.get("network")),
        lua=_file_name(_str(raw.get("file")), folder) or f"{macro_id}.lua",
        page=_str(raw.get("page")) if _str(raw.get("page")).startswith("https://github.com/") else "",
        files=tuple(files),
        versions=tuple(versions),
        changelog=changelog,
        updated=_str(raw.get("updated")),
        error=_str(raw.get("error")),
    )


def parse_index(data: Any) -> MacroIndex:
    """The index in *data* (the decoded JSON). Raises RepositoryError for a
    format this OpenSAK does not understand. Entries that are not
    well-formed (or have an id that is not a safe folder name) are left out."""
    if not isinstance(data, dict):
        raise RepositoryError("the macro index is not a JSON object")
    schema = _int(data.get("schema"))
    if schema is None:
        raise RepositoryError("the macro index has no schema version")
    if schema > SUPPORTED_SCHEMA:
        raise RepositoryError(
            f"the macro index has format {schema}; this OpenSAK reads format "
            f"{SUPPORTED_SCHEMA} — update OpenSAK")
    macros: dict[str, IndexMacro] = {}
    for raw in data.get("macros") or ():
        macro = _parse_macro(raw) if isinstance(raw, dict) else None
        if macro is not None and macro.id not in macros:
            macros[macro.id] = macro
    withdrawn: dict[str, WithdrawnMacro] = {}
    for raw in data.get("withdrawn") or ():
        if not isinstance(raw, dict) or not _SAFE_ID.match(_str(raw.get("id"))):
            continue
        hashes = frozenset(
            sha for v in raw.get("versions") or ()
            if isinstance(v, dict) and (sha := _sha(v.get("sha256"))))
        gone = WithdrawnMacro(_str(raw.get("id")), _str(raw.get("name")) or _str(raw.get("id")),
                              _str(raw.get("last_version")), _str(raw.get("withdrawn")), hashes)
        if gone.id not in macros:
            withdrawn[gone.id] = gone
    return MacroIndex(_str(data.get("commit")), _str(data.get("generated")), macros, withdrawn)


def _download(url: str, limit: int, timeout: float = REQUEST_TIMEOUT) -> bytes:
    """GET *url* (at most *limit* bytes). Raises RepositoryError."""
    from opensak import __version__, net

    request = urllib.request.Request(url, headers={"User-Agent": f"OpenSAK/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=net.SSL_CONTEXT) as resp:
            data = resp.read(limit + 1)
    except (OSError, ValueError) as exc:
        net.note_failure(exc)
        raise RepositoryError(str(exc)) from exc
    net.note_success()
    if len(data) > limit:
        raise RepositoryError(f"{url} is larger than {limit} bytes")
    return data


Fetcher = Callable[[str, int], bytes]


# The index fetched last in this session (startup check or Macro manager).
_last_index: Optional[MacroIndex] = None


def fetch_index(fetch: Optional[Fetcher] = None) -> MacroIndex:
    """Download and read the index, and keep it as last_index().
    Raises RepositoryError."""
    global _last_index
    data = (fetch or _download)(INDEX_URL, MAX_INDEX_BYTES)
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RepositoryError(f"the macro index is not valid JSON: {exc}") from exc
    index = parse_index(decoded)
    _last_index = index
    return index


def last_index() -> Optional[MacroIndex]:
    """The index fetched last in this session, None before the first fetch."""
    return _last_index


def forget_index() -> None:
    """Forget last_index() (tests)."""
    global _last_index
    _last_index = None


# ── Install records (opensak.json) ───────────────────────────────────────────

@dataclass(frozen=True)
class InstallRecord:
    """A macro installed from (or matched to) the index. *lua* is the macro
    file, *files* every file installed with it and its SHA-256 then, both
    as store keys (see shortcuts._key)."""
    id: str
    version: str
    lua: str
    files: dict[str, str]
    installed: str

    @property
    def lua_path(self) -> Path:
        from opensak.macro.shortcuts import _path
        return _path(self.lua)

    def to_json(self) -> dict:
        return {"version": self.version, "lua": self.lua, "files": dict(self.files),
                "installed": self.installed}


def _key(path: Path) -> str:
    from opensak.macro.shortcuts import _key as key
    return key(path)


def _path(key: str) -> Path:
    from opensak.macro.shortcuts import _path as path
    return path(key)


def install_records() -> dict[str, InstallRecord]:
    """{id: record} as stored; entries that are not well-formed are left out."""
    from opensak.settings_store import get_store

    raw = get_store().get(INSTALLED_KEY)
    if not isinstance(raw, dict):
        return {}
    records: dict[str, InstallRecord] = {}
    for macro_id, entry in raw.items():
        if not isinstance(macro_id, str) or not isinstance(entry, dict):
            continue
        lua = _str(entry.get("lua"))
        files = entry.get("files")
        if not lua:
            continue
        files = {k: v for k, v in files.items() if isinstance(k, str) and isinstance(v, str)} \
            if isinstance(files, dict) else {}
        records[macro_id] = InstallRecord(macro_id, _str(entry.get("version")), lua, files,
                                          _str(entry.get("installed")))
    return records


def _save_records(records: dict[str, InstallRecord]) -> None:
    from opensak.settings_store import get_store

    if records:
        get_store().set(INSTALLED_KEY, {k: r.to_json() for k, r in records.items()})
    else:
        get_store().delete(INSTALLED_KEY)


def save_record(record: InstallRecord) -> None:
    records = install_records()
    records[record.id] = record
    _save_records(records)


def drop_record(macro_id: str) -> None:
    records = install_records()
    if records.pop(macro_id, None) is not None:
        _save_records(records)


def record_for(path: Path) -> Optional[InstallRecord]:
    """The install record whose macro file is *path*."""
    key = _key(path)
    return next((r for r in install_records().values() if r.lua == key), None)


def untracked() -> set[str]:
    from opensak.settings_store import get_store

    raw = get_store().get(UNTRACKED_KEY)
    return {k for k in raw if isinstance(k, str)} if isinstance(raw, list) else set()


def is_tracked(path: Path) -> bool:
    return _key(path) not in untracked()


def set_tracked(path: Path, tracked: bool) -> None:
    """Stop tracking macro *path* (drop its install record and never match
    it by hash again) or track it again."""
    from opensak.settings_store import get_store

    keys = untracked()
    key = _key(path)
    if tracked:
        keys.discard(key)
    else:
        keys.add(key)
        record = record_for(path)
        if record is not None:
            drop_record(record.id)
    if keys:
        get_store().set(UNTRACKED_KEY, sorted(keys))
    else:
        get_store().delete(UNTRACKED_KEY)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ── Startup check ────────────────────────────────────────────────────────────

def check_at_startup() -> bool:
    """The "Check for macro updates at startup" setting (on by default)."""
    from opensak.settings_store import get_store

    value = get_store().get(CHECK_AT_STARTUP_KEY, True)
    return value if isinstance(value, bool) else str(value).lower() in ("true", "1", "yes")


def set_check_at_startup(on: bool) -> None:
    from opensak.settings_store import get_store
    get_store().set(CHECK_AT_STARTUP_KEY, bool(on))


def startup_check_due(now: Optional[datetime] = None) -> bool:
    """True if the startup check is on and the last check was at least a
    day ago (or never happened, or its time is not readable)."""
    from opensak.settings_store import get_store

    if not check_at_startup():
        return False
    last = _str(get_store().get(LAST_CHECK_KEY))
    try:
        when = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return not (timedelta(0) <= now - when < CHECK_INTERVAL)


def note_checked(now: Optional[datetime] = None) -> None:
    from opensak.settings_store import get_store

    when = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    get_store().set(LAST_CHECK_KEY, when.isoformat().replace("+00:00", "Z"))


# ── Matching local macros ────────────────────────────────────────────────────

def file_hashes(data: bytes) -> set[str]:
    """SHA-256 of *data* and of the same text with the other line endings
    and with or without a BOM, so a copy saved by an editor that changed
    those still matches the published bytes."""
    bom = b"\xef\xbb\xbf"
    body = data[len(bom):] if data.startswith(bom) else data
    lf = body.replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    variants = {data, body, lf, crlf, bom + lf, bom + crlf}
    return {hashlib.sha256(v).hexdigest() for v in variants}


def _sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class MacroStatus:
    """Where a local macro stands against the index. *version* is the
    installed version as published (or as last recorded when changed
    locally); *macro* the index entry (None for withdrawn and local ones)."""
    state: str
    id: str = ""
    version: str = ""
    macro: Optional[IndexMacro] = None
    withdrawn: Optional[WithdrawnMacro] = None
    record: Optional[InstallRecord] = None

    @property
    def latest(self) -> str:
        return self.macro.version if self.macro else ""

    @property
    def can_update(self) -> bool:
        return self.state in (UPDATE, CHANGED) and self.macro is not None \
            and self.macro.lua_file is not None

    @property
    def from_index(self) -> bool:
        return self.state not in (LOCAL, UNTRACKED)


def match_macro(path: Path, index: MacroIndex, api_version: int,
                records: Optional[dict[str, InstallRecord]] = None,
                untracked_keys: Optional[set[str]] = None) -> MacroStatus:
    """The status of the local macro file *path* (see the module docstring
    for what counts as a macro from the index)."""
    key = _key(path)
    if key in (untracked() if untracked_keys is None else untracked_keys):
        return MacroStatus(UNTRACKED)
    records = install_records() if records is None else records
    record = next((r for r in records.values() if r.lua == key), None)
    try:
        hashes = file_hashes(Path(path).read_bytes())
    except OSError:
        hashes = set()
    found = next((f for sha in hashes if (f := index.find_hash(sha)) is not None), None)
    if record is not None:
        macro_id = record.id
    elif found is not None:
        macro_id = found[0]
    else:
        return MacroStatus(LOCAL)
    macro = index.macros.get(macro_id)
    gone = index.withdrawn.get(macro_id)
    if gone is not None:
        return MacroStatus(WITHDRAWN, macro_id, found[1] if found else record.version if record
                           else "", withdrawn=gone, record=record)
    if macro is None:
        # Known once, now neither published nor withdrawn (e.g. an index
        # from another repository state): nothing to offer.
        return MacroStatus(LOCAL, macro_id, record=record)
    version = next((v for v in macro.versions if v.sha256 in hashes), None)
    if version is None:
        return MacroStatus(CHANGED, macro_id, record.version if record else "", macro,
                           record=record)
    lua = macro.lua_file
    if lua is not None and lua.sha256 in hashes:
        return MacroStatus(UP_TO_DATE, macro_id, version.version, macro, record=record)
    if macro.needs_newer_api(api_version):
        return MacroStatus(NEEDS_NEWER_API, macro_id, version.version, macro, record=record)
    if lua is None:
        return MacroStatus(UP_TO_DATE, macro_id, version.version, macro, record=record)
    return MacroStatus(UPDATE, macro_id, version.version, macro, record=record)


def match_macros(macros: Iterable[MacroInfo], index: MacroIndex, api_version: int,
                 adopt: bool = True) -> dict[Path, MacroStatus]:
    """match_macro() for each of *macros*. With *adopt*, a macro matched by
    its hash alone gets an install record (with just its macro file), so
    it stays known when the user edits it."""
    records = install_records()
    keys = untracked()
    result: dict[Path, MacroStatus] = {}
    for info in macros:
        status = match_macro(info.path, index, api_version, records, keys)
        if adopt and status.from_index and status.record is None and status.id \
                and status.id not in records:
            try:
                sha = _sha_of(info.path)
            except OSError:
                sha = ""
            record = InstallRecord(status.id, status.version, _key(info.path),
                                   {_key(info.path): sha} if sha else {}, _now())
            records[status.id] = record
            save_record(record)
            status = MacroStatus(status.state, status.id, status.version, status.macro,
                                 status.withdrawn, record)
        result[info.path] = status
    return result


# ── Install, update, remove ─────────────────────────────────────────────────

def download_files(macro: IndexMacro, only_lua: bool = False,
                   fetch: Optional[Fetcher] = None) -> dict[str, bytes]:
    """{name: content} of the files of *macro* (just the macro file with
    *only_lua*), each checked against its size and SHA-256. Raises
    RepositoryError before returning anything if one fails."""
    fetch = fetch or _download
    files = [f for f in macro.files if not only_lua or f.name == macro.lua]
    if not any(f.name == macro.lua for f in files):
        raise RepositoryError(f"the index lists no macro file for {macro.id}")
    contents: dict[str, bytes] = {}
    for f in files:
        data = fetch(f.url, MAX_FILE_BYTES)
        if len(data) != f.size or hashlib.sha256(data).hexdigest() != f.sha256:
            raise RepositoryError(
                f"{f.name} of {macro.id} does not match the index (size or SHA-256)")
        contents[f.name] = data
    return contents


def install_folder(macro_id: str) -> Path:
    from opensak.macro.permissions import macros_dir
    return macros_dir() / macro_id


def _write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".opensak-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _move_to_previous(folder: Path, files: Iterable[Path]) -> None:
    """Move *files* (inside *folder*) to <folder>/.previous/, replacing
    what an earlier update left there."""
    files = [f for f in files if f.is_file()]
    if not files:
        return
    previous = folder / PREVIOUS_DIR
    if previous.exists():
        shutil.rmtree(previous)
    for f in files:
        target = previous / f.relative_to(folder)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(target))


def existing_files(macro: IndexMacro, folder: Path) -> list[Path]:
    """The files of *macro* that already exist in *folder*, i.e. what an
    install there would replace."""
    return [folder / f.name for f in macro.files if (folder / f.name).exists()]


def install(macro: IndexMacro, contents: dict[str, bytes], folder: Optional[Path] = None,
            record: Optional[InstallRecord] = None,
            lua_target: Optional[Path] = None) -> InstallRecord:
    """Write the downloaded *contents* (download_files()) of *macro* into
    *folder* (``<macros folder>/<id>/`` by default) and store the install
    record. The macro file goes to *lua_target* if given (an update of a
    copy the user renamed). Files it replaces, and files of the old
    *record* that the new version does not have and that are unchanged
    since installed, go to ``.previous/`` first."""
    folder = Path(folder) if folder is not None else install_folder(macro.id)
    folder.mkdir(parents=True, exist_ok=True)
    targets = {name: folder / name for name in contents}
    if lua_target is not None:
        targets[macro.lua] = Path(lua_target)
    for target in targets.values():
        if folder.resolve() not in target.resolve().parents:
            raise RepositoryError(f"{target} is outside {folder}")
    stale: list[Path] = []
    if record is not None:
        new = {_key(t) for t in targets.values()}
        for key, sha in record.files.items():
            old = _path(key)
            if key in new or not old.is_file() or folder.resolve() not in old.resolve().parents:
                continue
            try:
                if _sha_of(old) == sha:
                    stale.append(old)
            except OSError:
                pass
    _move_to_previous(folder, [t for t in targets.values() if t.exists()] + stale)
    for name, data in contents.items():
        _write_atomic(targets[name], data)
    lua = targets[macro.lua]
    new_record = InstallRecord(
        macro.id, macro.version, _key(lua),
        {_key(t): hashlib.sha256(contents[n]).hexdigest() for n, t in targets.items()}, _now())
    keys = untracked()
    if _key(lua) in keys:
        set_tracked(lua, True)
    save_record(new_record)
    return new_record


def companion_db(lua: Path) -> Path:
    """The companion database of macro file *lua* (opensak.macro_db())."""
    return lua.with_suffix(COMPANION_SUFFIX)


def data_files(lua: Path) -> list[Path]:
    """The data of macro file *lua* that exists: its companion database,
    its saved settings and its remembered form values."""
    from opensak.macro.settings import saved_files

    data = companion_db(lua)
    return ([data] if data.exists() else []) + saved_files(lua)


@dataclass(frozen=True)
class RemoveResult:
    removed: tuple[Path, ...]
    kept: tuple[Path, ...]          # installed files changed since (left behind)
    kept_data: tuple[Path, ...] = ()    # data_files() left behind


def remove(lua: Path, delete_data: bool = False) -> RemoveResult:
    """Remove the macro *lua*: the files of its install record (just the
    macro file without one), its ``.previous/`` folder if it has its own
    folder, and with *delete_data* its data_files(). Files changed since
    installed are removed too when they are the macro file itself (the
    user asked to remove the macro) but kept otherwise. The record is
    dropped and the folder deleted if nothing else is left in it."""
    from opensak.macro.settings import remove_saved_file

    lua = Path(lua)
    record = record_for(lua)
    files = {lua: ""}
    if record is not None:
        files.update({_path(k): v for k, v in record.files.items()})
    data = data_files(lua)          # before the macro file is gone
    removed: list[Path] = []
    kept: list[Path] = []
    for path, sha in files.items():
        if not path.is_file():
            continue
        try:
            same = path == lua or not sha or _sha_of(path) == sha
        except OSError:
            same = False
        if same:
            path.unlink()
            removed.append(path)
        else:
            kept.append(path)
    if delete_data:
        for path in data:
            if path == companion_db(lua):
                path.unlink()
            else:
                remove_saved_file(path)
            removed.append(path)
    from opensak.macro.permissions import macros_dir

    folder = lua.parent
    own_folder = folder.resolve() != macros_dir().resolve()
    if record is not None:
        drop_record(record.id)
        previous = folder / PREVIOUS_DIR
        if own_folder and folder.name == record.id and previous.is_dir():
            shutil.rmtree(previous)
    if own_folder:
        _remove_empty(folder)       # only folders with nothing left in them
    set_tracked(lua, True)          # forget a "stop tracking" for the path
    return RemoveResult(tuple(removed), tuple(kept), () if delete_data else tuple(data))


def _remove_empty(folder: Path) -> None:
    for sub in sorted((p for p in folder.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        try:
            sub.rmdir()
        except OSError:
            pass
    try:
        folder.rmdir()
    except OSError:
        pass


def update_folder(status: MacroStatus, lua: Path) -> tuple[Path, bool]:
    """Where an update of the local macro *lua* goes, and whether only its
    macro file is updated: all files when the macro has its own folder
    (named after its id), just the macro file when it was copied by hand
    among other macros."""
    folder = lua.parent
    from opensak.macro.permissions import macros_dir
    own = folder.name == status.id and folder.resolve() != macros_dir().resolve()
    return folder, not own


def updates(statuses: dict[Path, MacroStatus]) -> dict[Path, MacroStatus]:
    """The macros that have an update to install."""
    return {p: s for p, s in statuses.items() if s.state == UPDATE}


def withdrawn_macros(statuses: dict[Path, MacroStatus]) -> dict[Path, MacroStatus]:
    return {p: s for p, s in statuses.items() if s.state == WITHDRAWN}

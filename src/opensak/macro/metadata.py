"""
src/opensak/macro/metadata.py — the metadata header of a macro file and the
list of local macros the Macro manager shows.

A macro may start with a comment block naming it (concept §8, like GSAK's
MacHeader):

    -- OpenSAK Macro
    -- Name: Find easy traditionals
    -- Author: Jane Doe
    -- Version: 1.0
    -- Requires-API: 3
    -- Description: Filters for unfound traditional caches with low difficulty.
    -- Permissions: write, open
    -- Network: api.example.com

Every key is optional and the "OpenSAK Macro" line is not required. Keys
are matched case-insensitively in the comment lines at the top of the file
(up to the first line of code); other comment lines are ignored. Without a
Name the file name is used. Without a Description, the text after the dash
of a first line in the older "-- name.lua — what it does" style is used.

Requires-API is compared with the macro API version (opensak.api_version(),
runtime.API_VERSION) by its leading number, so "3" and "3.0" are the same.

Permissions and Network declare what the macro does that a user (or a
reviewer of the community macro repository) should know before running
it. They are informational only: a declaration never approves anything,
and the runtime still asks for every write, folder and web site approval,
whether it is declared or not (the web site question only says whether
the host is declared).

Permissions lists words from PERMISSIONS, separated by commas and/or
spaces, case-insensitively; "none" says the macro needs none of them, so
that "declares nothing" can be told apart from "not declared". Network
lists the hosts the macro contacts with opensak.get_url() (#1079): host
names or IP addresses, without scheme, user, port or path, and without
wildcards, since web sites are approved one host at a time. Both keys may
be repeated; their values add up. A word not in PERMISSIONS and an entry
that is not a host are kept (unknown_permissions, invalid_hosts), so a
newer macro's words are shown rather than dropped; a host macros may never
contact (BLOCKED_DOMAINS) is kept as well and listed by blocked_hosts.

added_permissions() compares two versions of a macro: an update for which
it returns anything asks for more than the version the user had.

header_keys() lists the keys a header actually sets, without the
defaults parse_header() fills in, e.g. for checking that a macro submitted
to the community repository has a complete header.

A header key can be deprecated (HEADER_KEYS, #1185): MacroInfo lists the
deprecated keys a macro uses, which the Macro manager shows and the run
prints. deprecated_names() and find_deprecated() give the community
repository's CI everything deprecated in the macro API — functions,
parameters, cache fields and header keys — to warn about.
"""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from opensak.macro.deprecation import Deprecated, deprecated_message

# Only the top of the file is read: the header is a few lines, and a
# folder may hold large generated macros.
_HEAD_BYTES = 16 * 1024

# The shared macro repository (#1173): one folder per macro, its header
# checked by CI with this module. Linked from Macros → Find more macros…,
# the Macro manager and the API reference.
MACROS_REPO_URL = "https://github.com/OpenSAK-Org/OpenSAK-Macros"

_KEYS = {
    "name": "name",
    "author": "author",
    "version": "version",
    "requires-api": "requires_api",
    "description": "description",
}

# The words of Permissions, in the order they are listed, with what each
# one covers.
PERMISSIONS: dict[str, str] = {
    "write": "changes data in a database: the write API (asks for write "
             "approval), create_database(), copy_caches() and move_caches()",
    "read-files": "reads files or folders outside the macros folder and "
                  "OpenSAK's temp folder (asks for folder approval); a file "
                  "the user picks in choose_file() does not count",
    "write-files": "writes, copies, moves or deletes files or folders outside "
                   "OpenSAK's temp folder, exports included (asks for folder "
                   "approval)",
    "open": "opens files in their default app with opensak.open()",
    "run-macros": "runs other macro files with opensak.run_macro(); what "
                  "those do is not covered by this macro's declaration",
}
NO_PERMISSIONS = "none"

# Sites macros may never contact, whatever the user approves (#1079): the
# terms of use of Geocaching.com don't allow automated access outside
# their API, so none of Groundspeak's sites can be contacted. A domain
# covers its subdomains. Checked on 2026-10-25: each one uses Groundspeak's
# name server (ns1.groundspeak.net), or the mail servers and content of
# geocaching.com (waymarking.com, wherigo.com, geocachinghq.com). Not
# Groundspeak's, so not listed: geocaching.co.uk (Geocaching UK),
# geocaching.com.au (Geocaching Australia), geocaching.de, geocaching.ca,
# groundspeak.de, benchmarking.com and adventurelab.app.
BLOCKED_DOMAINS = (
    "geocaching.com", "coord.info", "geocachinghq.com",
    "groundspeak.com", "groundspeak.net", "groundspeak.org", "groundspeak.info",
    "geocaching.net", "geocaching.org", "geocaching.us", "geocaching.eu",
    "geocaching.co", "geocaching.mobi",
    "waymarking.com", "waymarking.net", "waymarking.org",
    "wherigo.com", "wherigo.net", "wherigo.org",
)

_LIST_KEYS = ("permissions", "network")


@dataclass(frozen=True)
class HeaderKey:
    """A key of the macro header, as documented."""

    name: str
    # (API version, what to use instead) once the key is deprecated; the
    # old key still works until it is removed (deprecation.py).
    deprecated: Optional[Deprecated] = None


# Every header key, in documentation order. A key renamed later stays here
# with `deprecated` set, and in _KEYS or _LIST_KEYS, mapped to the field of
# its new name.
HEADER_KEYS: tuple[HeaderKey, ...] = (
    HeaderKey("Name"),
    HeaderKey("Author"),
    HeaderKey("Version"),
    HeaderKey("Requires-API"),
    HeaderKey("Description"),
    HeaderKey("Permissions"),
    HeaderKey("Network"),
)
_LIST_SEPARATOR = re.compile(r"[\s,]+")
_HOST_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")

_KEY_LINE = re.compile(r"^([A-Za-z][A-Za-z-]*)\s*:\s*(.*?)\s*$")
_API_NUMBER = re.compile(r"^\s*(\d+)(?:\.\d+)*\s*$")
_LEGACY_SUMMARY = re.compile(r"^\S+\.lua\s+(?:—|–|-)\s+(.+?)\s*$")


@dataclass(frozen=True)
class MacroInfo:
    """What the header of one macro file says. *error* is set (and the
    other fields are mostly empty) when the file could not be read."""

    path: Path
    name: str
    description: str = ""
    author: str = ""
    version: str = ""
    requires_api: str = ""
    # Permissions: the known words in PERMISSIONS order ("none" left out),
    # the other words (lower-cased), and whether the key is there at all.
    permissions: tuple[str, ...] = ()
    unknown_permissions: tuple[str, ...] = ()
    permissions_declared: bool = False
    # Network: the hosts (normalised, in the order written) and the entries
    # that are not hosts, as written.
    network: tuple[str, ...] = ()
    invalid_hosts: tuple[str, ...] = ()
    # The deprecated HEADER_KEYS the header uses
    deprecated_keys: tuple[HeaderKey, ...] = ()
    error: str = ""

    @property
    def deprecation_lines(self) -> tuple[str, ...]:
        """deprecated_message() for each of deprecated_keys."""
        return tuple(deprecated_message(f"header key {k.name}", k.deprecated)
                     for k in self.deprecated_keys if k.deprecated)

    @property
    def requires_api_number(self) -> Optional[int]:
        """Requires-API as a number, None when missing or not a number."""
        m = _API_NUMBER.match(self.requires_api)
        return int(m.group(1)) if m else None

    def needs_newer_api(self, api_version: int) -> bool:
        """True if the macro asks for a newer API than *api_version*."""
        needed = self.requires_api_number
        return needed is not None and needed > api_version

    @property
    def blocked_hosts(self) -> tuple[str, ...]:
        """The Network hosts in BLOCKED_DOMAINS."""
        return tuple(h for h in self.network if is_blocked_host(h))

    @property
    def declared(self) -> tuple[str, ...]:
        """Everything declared, for comparing versions: the permissions
        (unknown ones included) and "network:<host>" for each host."""
        return (self.permissions + self.unknown_permissions
                + tuple(f"network:{h}" for h in self.network + self.invalid_hosts))


def added_permissions(old: MacroInfo, new: MacroInfo) -> tuple[str, ...]:
    """What *new* declares that *old* did not (see MacroInfo.declared);
    empty if the update asks for nothing more. If *old* has no Permissions
    key, what it did is not known, so everything *new* declares counts."""
    return added_declarations(old.declared, old.permissions_declared, new)


def added_declarations(
    old_declared: tuple[str, ...], old_permissions_declared: bool, new: MacroInfo
) -> tuple[str, ...]:
    """added_permissions() for an old version known only by its
    MacroInfo.declared and permissions_declared (e.g. as stored with an
    approval)."""
    before = set(old_declared) if old_permissions_declared else set()
    return tuple(d for d in new.declared if d not in before)


def is_blocked_host(host: str) -> bool:
    """True if *host* is one of BLOCKED_DOMAINS or a subdomain of one."""
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


def normalize_host(entry: str) -> Optional[str]:
    """*entry* as a host name (lower-case, IDNA-encoded, without a trailing
    dot) or an IP address; None if it is neither, e.g. a URL, "host:port",
    "user@host" or "*.example.com"."""
    text = entry.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]               # [::1], the way a URL writes IPv6
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        pass
    try:
        host = text.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    labels = host.split(".")
    if len(host) > 253 or len(labels) < 2 or not all(_HOST_LABEL.match(x) for x in labels):
        return None
    if labels[-1].isdigit():
        return None                     # "10.1.2": neither an IP address nor a name
    return host


def _header_lines(text: str) -> list[str]:
    """The text of the comment lines at the top of *text*, without "--".
    Blank lines are skipped, a block comment ("--[[") ends the header."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#!") and not lines:
            continue                    # shebang of a macro run from a shell
        if not line.startswith("--") or line.startswith("--[["):
            break
        lines.append(line[2:].strip())
    return lines


def _header_items(lines: list[str]) -> Iterator[tuple[str, str]]:
    """The (lower-cased key, value) of each key line in *lines* (from
    _header_lines) with a value; a key without one counts as not set."""
    for line in lines:
        m = _KEY_LINE.match(line)
        if m and m.group(2):
            yield m.group(1).lower(), m.group(2)


def header_keys(text: str) -> tuple[str, ...]:
    """The keys set in the header of macro source *text*, lower-cased, each
    once, in the order first written. Unlike parse_header() nothing is
    filled in: a header without Name gives no "name". Keys that are not
    known (also "Note: ..." prose) are listed too, and a key with an empty
    value is not."""
    lines = _header_lines(text.lstrip("﻿"))
    return tuple(dict.fromkeys(key for key, _ in _header_items(lines)))


def parse_header(text: str, path: Path) -> MacroInfo:
    """The metadata in the header of macro source *text* from *path*."""
    fields: dict[str, str] = {}
    lists: dict[str, list[str]] = {}
    lines = _header_lines(text.lstrip("﻿"))
    for key, value in _header_items(lines):
        field = _KEYS.get(key)
        if field and field not in fields:
            fields[field] = value
        elif key in _LIST_KEYS:
            lists.setdefault(key, []).extend(
                w for w in _LIST_SEPARATOR.split(value) if w)
    if "description" not in fields and lines:
        m = _LEGACY_SUMMARY.match(lines[0])
        if m:
            fields["description"] = m.group(1)
    fields.setdefault("name", path.stem)
    used = set(header_keys(text))

    words = [w.lower() for w in lists.get("permissions", [])]
    hosts: dict[str, None] = {}
    invalid: dict[str, None] = {}
    for entry in lists.get("network", []):
        host = normalize_host(entry)
        if host is None:
            invalid[entry] = None
        else:
            hosts[host] = None
    return MacroInfo(
        path=path,
        permissions=tuple(p for p in PERMISSIONS if p in words),
        unknown_permissions=tuple(dict.fromkeys(
            w for w in words if w not in PERMISSIONS and w != NO_PERMISSIONS)),
        permissions_declared="permissions" in lists,
        network=tuple(hosts),
        invalid_hosts=tuple(invalid),
        deprecated_keys=tuple(k for k in HEADER_KEYS
                              if k.deprecated and k.name.lower() in used),
        **fields,
    )


def read_metadata(path: Path) -> MacroInfo:
    """The metadata of the macro file *path*; an unreadable file gives a
    MacroInfo with *error* set."""
    path = Path(path)
    try:
        with path.open("rb") as f:
            head = f.read(_HEAD_BYTES)
        text = head.decode("utf-8", errors="strict" if len(head) < _HEAD_BYTES else "ignore")
    except (OSError, UnicodeDecodeError) as exc:
        return MacroInfo(path=path, name=path.stem, error=str(exc))
    return parse_header(text, path)


def discover_macros(root: Path) -> list[MacroInfo]:
    """The .lua files in *root* and its subfolders, sorted by relative
    path (case-insensitively). Hidden folders (".git", ".vscode", …) are
    left out, and so is <root>/types with the editor stub opensak.lua."""
    from opensak.macro.editor_support import TYPES_DIR

    root = Path(root)
    if not root.is_dir():
        return []
    found: list[Path] = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")
                   and not (d == TYPES_DIR and Path(folder) == root)]
        found += [Path(folder) / f for f in files if f.lower().endswith(".lua")]
    found.sort(key=lambda p: p.relative_to(root).as_posix().casefold())
    return [read_metadata(p) for p in found]


@dataclass(frozen=True)
class DeprecatedName:
    """Something deprecated in the macro API, for deprecated_names()."""

    # "function", "parameter", "field" or "header"
    kind: str
    # "opensak.old_name", "opensak.func parameter old", "old_field" or
    # "Old-Key"
    name: str
    message: str


def deprecated_names() -> tuple[DeprecatedName, ...]:
    """Everything deprecated in this version of the macro API: functions,
    their parameters, cache fields and header keys, e.g. for the
    OpenSAK-Macros CI to warn about. Needs OpenSAK installed (it reads the
    registries of runtime.py and cache_data.py), unlike the rest of this
    module."""
    from opensak.macro.cache_data import CACHE_FIELDS
    from opensak.macro.runtime import API

    names: list[DeprecatedName] = []
    for func in API:
        if func.deprecated:
            name = f"opensak.{func.name}"
            names.append(DeprecatedName("function", name,
                                        deprecated_message(name, func.deprecated)))
        params = {p.name: p for form in (func.params, *func.overloads) for p in form}
        for p in params.values():
            if p.deprecated:
                name = f"opensak.{func.name} parameter {p.name}"
                names.append(DeprecatedName("parameter", name,
                                            deprecated_message(name, p.deprecated)))
    for field in CACHE_FIELDS:
        if field.deprecated:
            names.append(DeprecatedName(
                "field", field.name,
                deprecated_message(f"cache field {field.name}", field.deprecated)))
    for key in HEADER_KEYS:
        if key.deprecated:
            names.append(DeprecatedName(
                "header", key.name,
                deprecated_message(f"header key {key.name}", key.deprecated)))
    return tuple(names)


def find_deprecated(text: str) -> list[tuple[int, str]]:
    """(line number, message) for each use of something deprecated in
    macro source *text*, in line order: deprecated header keys, and calls
    of deprecated functions and reads of deprecated cache fields found by
    name (`opensak.old_name`, `.old_field`, `["old_field"]`) outside
    comments. Parameters are positional and can't be found this way. A
    quick check for CI warnings, not a Lua parser: a name in a string can
    be reported too."""
    names = deprecated_names()
    found: list[tuple[int, str]] = []
    lines = text.lstrip("\ufeff").splitlines()
    header = {n.name.lower(): n.message for n in names if n.kind == "header"}
    patterns: list[tuple[re.Pattern[str], str]] = []
    for n in names:
        if n.kind == "function":
            patterns.append((re.compile(re.escape(n.name) + r"(?![\w.])"), n.message))
        elif n.kind == "field":
            word = re.escape(n.name)
            patterns.append((re.compile(rf"\.{word}\b|\[\s*[\"']{word}[\"']\s*\]"),
                             n.message))
    in_header = True
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if in_header and line.startswith("--") and not line.startswith("--[["):
            m = _KEY_LINE.match(line[2:].strip())
            if m and m.group(2) and m.group(1).lower() in header:
                found.append((number, header[m.group(1).lower()]))
            continue
        if line and not line.startswith("#!"):
            in_header = False
        code = raw.split("--", 1)[0]
        for pattern, message in patterns:
            if pattern.search(code):
                found.append((number, message))
    return found

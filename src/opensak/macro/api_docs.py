"""
src/opensak/macro/api_docs.py — render the Lua API reference.

Two files are generated from the registry in runtime.py by
scripts/generate_macro_api_docs.py; never edit them by hand:

  * docs/macros/api.md        — the reference for people (Markdown)
  * macros/types/opensak.lua  — a ---@meta stub for the Lua Language Server,
                                giving autocompletion and inline docs in
                                VS Code while writing macros
"""

from __future__ import annotations

import json
from pathlib import Path

from opensak.macro.cache_data import CACHE_FIELDS
from opensak.macro.editor_support import LUARC
from opensak.macro.runtime import API, API_VERSION, FILTER_KEY_DOCS, Param

DOC_PATH = Path("docs/macros/api.md")
STUB_PATH = Path("macros/types/opensak.lua")
EXAMPLES_DIR = Path("macros/examples")
# Links relative to DOC_PATH
_EXAMPLES_LINK = "../../macros/examples"
_STUB_LINK = "../../macros/types/opensak.lua"

_GENERATED = ("Generated from src/opensak/macro/runtime.py by "
              "scripts/generate_macro_api_docs.py — do not edit by hand.")


def _code(text: str, lang: str = "lua") -> list[str]:
    return [f"```{lang}", *text.splitlines(), "```"]


# ── Markdown ─────────────────────────────────────────────────────────────────


def _example_summary(path: Path) -> str:
    """The text after the dash in the first line, e.g.
    "-- name.lua — set corrected coordinates" → "set corrected coordinates"."""
    first = path.read_text(encoding="utf-8").splitlines()[0]
    if first.startswith("--"):
        for dash in ("—", " - "):
            if dash in first:
                return first.split(dash, 1)[1].strip()
    return ""


def _examples_section(examples_dir: Path) -> list[str]:
    lines = [
        "",
        "## Example macros",
        "",
        f"Ready-to-use scripts to copy and adapt are in [`{EXAMPLES_DIR.as_posix()}/`]"
        f"({_EXAMPLES_LINK}/). Each one starts with a comment explaining what it "
        "does and which files it expects.",
        "",
    ]
    for path in sorted(examples_dir.glob("*.lua")):
        summary = _example_summary(path)
        entry = f"- [`{path.name}`]({_EXAMPLES_LINK}/{path.name})"
        lines.append(f"{entry} — {summary}" if summary else entry)
    return lines


def _editor_section() -> list[str]:
    return [
        "",
        "## Editor support (VS Code)",
        "",
        f"[`{STUB_PATH.as_posix()}`]({_STUB_LINK}) describes this API for the "
        "[Lua Language Server](https://luals.github.io/) (VS Code extension "
        '"Lua" by sumneko): autocompletion, parameter hints and these docs '
        "while you type.",
        "",
        "OpenSAK keeps a copy of the stub in its macros folder "
        "(**Open macros folder** in the macro window) as "
        "`types/opensak.lua`, refreshed on every start so it matches the "
        "installed version, and puts a `.luarc.json` next to your macros "
        "that points the language server at it:",
        "",
        *_code(json.dumps(LUARC, indent=2), "json"),
        "",
        "To set it up in VS Code:",
        "",
        '1. Install the extension "Lua" by sumneko.',
        "2. **File → Open Folder…** and pick OpenSAK's macros folder.",
        "3. Open or create a `.lua` file and type `opensak.` — completion, "
        "parameter hints and these docs on hover appear.",
        "",
        "OpenSAK writes `.luarc.json` only when there is none, so your own "
        "settings in it are kept; delete it to get the default back. Macros "
        "in another folder can use the stub too: copy `.luarc.json` there "
        "and change `types` to the full path of the macros folder's `types` "
        "folder. Macros inside the OpenSAK repository pick up "
        f"[`{STUB_PATH.as_posix()}`]({_STUB_LINK}) automatically.",
    ]


def _param_line(p: Param) -> str:
    optional = ", optional" if p.optional else ""
    return f"- `{p.name}` (`{p.type}`{optional}) — {p.description}"


def render_api_markdown(examples_dir: Path = EXAMPLES_DIR) -> str:
    """The API reference as Markdown. *examples_dir* is where the example
    macros listed at the end are looked up (the in-app help passes the
    bundled folder)."""
    lines = [
        f"<!-- {_GENERATED} -->",
        "",
        "# OpenSAK Lua macro API",
        "",
        f"API version: **{API_VERSION}** (`opensak.api_version()`).",
        "",
        "Macros are Lua 5.4 scripts run in a sandbox. They talk to OpenSAK "
        "through the global `opensak` table. File access is limited to the "
        "folders listed in Settings → Folder permissions. When a macro needs "
        "a file in another folder, OpenSAK asks the user whether to allow "
        "that folder for this run only or always, or to deny it; reading and "
        "writing are asked separately. OpenSAK's own settings and database "
        "files are never accessible.",
        "",
        "See [Example macros](#example-macros) for complete scripts and "
        "[Editor support](#editor-support-vs-code) for autocompletion in VS Code.",
        "",
        "## Functions",
        "",
        "| Function | Since |",
        "|---|---|",
    ]
    for func in API:
        anchor = func.name.replace("_", "").replace(".", "")
        lines.append(f"| [`opensak.{func.name}`](#opensak{anchor}) | {func.since} |")

    for func in API:
        lines += ["", f"### opensak.{func.name}", ""]
        lines += _code("\n".join(func.signatures))
        lines += ["", func.description, ""]
        params: dict[str, Param] = {}
        for p in (*func.params, *(q for form in func.overloads for q in form)):
            params.setdefault(p.name, p)
        if params:
            lines += ["Parameters:", ""]
            lines += [_param_line(p) for p in params.values()]
            lines.append("")
        if func.returns:
            lines += [f"Returns `{func.returns[0]}` — {func.returns[1]}", ""]
        lines += [f"Since API version {func.since}.", "", "Example:", ""]
        lines += _code(func.example)

    lines += [
        "",
        "## Filter keys",
        "",
        "Keys understood by `opensak.filter{}`, combined with AND (or OR with `mode`).",
        "",
        "| Key | Value | Meaning |",
        "|---|---|---|",
    ]
    for doc in FILTER_KEY_DOCS:
        keys = ", ".join(f"`{k}`" for k in doc.keys)
        value = doc.value.replace("|", "\\|")
        lines.append(f"| {keys} | `{value}` | {doc.description} |")

    lines += [
        "",
        "`opensak.caches{}` also takes `fields`, an array of the cache fields "
        "to load (`code` is always included), and `database`.",
        "",
        "## Reading another database",
        "",
        "`opensak.cache()`, `opensak.caches{}`, `opensak.description()`, "
        "`opensak.sql()`, `opensak.sql_each()`, `opensak.tables()` and "
        "`opensak.columns()` take a `database` option with the name of a "
        "database from `opensak.databases()`. It reads that database without "
        "switching to it, so the active filter stays. The database is opened "
        "read-only. A database last opened by an older OpenSAK version must be "
        "opened once first, so its schema is updated. Naming the active "
        "database is the same as leaving `database` out.",
        "",
        "## Cache fields",
        "",
        "Keys of the table returned by `opensak.cache()`, `opensak.current()` "
        "and `opensak.caches()`. The table is a snapshot; missing data is `nil`.",
        "",
        "| Field | Type | Meaning |",
        "|---|---|---|",
    ]
    for field in CACHE_FIELDS:
        type_ = field.type.replace("|", "\\|")
        lines.append(f"| `{field.name}` | `{type_}` | {field.description} |")

    lines += _examples_section(examples_dir)
    lines += _editor_section()

    lines += [
        "",
        "## Globals",
        "",
        "### print",
        "",
        *_code("print(...)"),
        "",
        "Write the arguments, separated by tabs, to the macro output pane.",
        "",
    ]
    return "\n".join(lines)


# ── Lua Language Server stub ─────────────────────────────────────────────────


def _comment(text: str) -> list[str]:
    return [f"---{line}".rstrip() for line in text.splitlines()]


def _split_types(types: str) -> list[str]:
    """"number?, number?" → ["number?", "number?"]; commas inside <> or ()
    (e.g. "table<string, string>") do not split."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(types):
        if ch in "<({":
            depth += 1
        elif ch in ">)}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(types[start:i].strip())
            start = i + 1
    parts.append(types[start:].strip())
    return parts


def _return_lines(types: str, description: str) -> list[str]:
    lines = []
    for i, t in enumerate(_split_types(types)):
        name = ""
        if t.endswith("..."):
            t, name = t[:-3], " ..."
        comment = f" # {description}" if i == 0 else ""
        lines.append(f"---@return {t}{name}{comment}")
    return lines


def _overload(params: tuple[Param, ...], returns: str | None) -> str:
    args = ", ".join(f"{p.name}{'?' if p.optional else ''}: {p.type}" for p in params)
    return f"fun({args})" + (f": {returns}" if returns else "")


def render_lua_stub() -> str:
    lines = [
        "---@meta",
        f"-- {_GENERATED}",
        f"-- OpenSAK Lua macro API, version {API_VERSION}. Reference: {DOC_PATH.as_posix()}",
        "",
        "---Keys understood by `opensak.filter{}`, combined with AND (or OR with `mode`).",
        "---@class opensak.FilterSpec",
    ]
    for doc in FILTER_KEY_DOCS:
        for key in doc.keys:
            lines.append(f"---@field {key}? {doc.type} {doc.description}")

    lines += [
        "",
        "---Filter keys plus `fields` and `database`, understood by `opensak.caches{}`.",
        "---@class opensak.CachesSpec: opensak.FilterSpec",
        "---@field fields? string[] Cache fields to load (`code` is always included).",
        "---@field database? string Read this database instead of the active one.",
        "",
        "---Options of the read functions (`opensak.cache()`, `opensak.sql()`, ...).",
        "---@class opensak.ReadOptions",
        "---@field database? string Read this database instead of the active one.",
        "",
        "---Options of `opensak.move_caches()` and `opensak.copy_caches()`.",
        "---@class opensak.TransferOptions",
        "---@field codes? string[] GC codes to transfer (default: the caches of the active filter).",
        '---@field if_exists? "newer"|"replace"|"skip" When the cache exists in the target (default "newer").',
        "",
        "---What `opensak.export_gpx{}` writes.",
        "---@class opensak.ExportSpec",
        '---@field path? string File to write; the name may use {database}, {filter}, {date}, ... (default "{database}" for a device).',
        '---@field format? "gpx"|"ggz"|"loc"|"kml" Default: from the extension of `path`, else "gpx" (a device takes gpx or ggz).',
        "---@field corrected? boolean Use corrected coordinates where set (default true).",
        "---@field max? integer At most this many caches (default 0 = all).",
        "---@field rename? fun(c: opensak.Cache): string? Name to write instead of the cache name.",
        "---@field description? fun(c: opensak.Cache): string? Waypoint description to write (GPX desc, LOC label, KML pop-up).",
        "---@field pois? {attributes?: boolean, child_waypoints?: boolean} Leave out attributes (GPX/GGZ) or child waypoints (GPX/GGZ/KML); both default true.",
        '---@field if_exists? "overwrite"|"skip"|"ask" When the file exists (default "overwrite").',
        '---@field target? "file"|"device" "device": the GPX/GGZ folder of the connected Garmin (default "file").',
        "---@field device? string The device's folder, when several Garmin devices are connected.",
        "",
        "---A cache as returned by `opensak.cache()` and `opensak.caches()` (a snapshot).",
        "---@class opensak.Cache",
    ]
    for field in CACHE_FIELDS:
        lines.append(f"---@field {field.name} {field.type} {field.description}")

    lines += [
        "",
        "---The OpenSAK API, available as a global in every macro.",
        "opensak = {}",
    ]
    namespaces: set[str] = set()
    for func in API:
        lines.append("")
        namespace = func.name.rpartition(".")[0]
        if namespace and namespace not in namespaces:
            namespaces.add(namespace)
            lines += [f"opensak.{namespace} = {{}}", ""]
        lines += _comment(func.description)
        lines += ["---", f"---Since API version {func.since}.", "---"]
        lines += _comment(f"```lua\n{func.example}\n```")
        for p in func.params:
            lines.append(f"---@param {p.name}{'?' if p.optional else ''} {p.type} {p.description}")
        if func.returns:
            lines += _return_lines(*func.returns)
        for form in func.overloads:
            lines.append(f"---@overload {_overload(form, func.returns and func.returns[0])}")
        args = ", ".join(p.name for p in func.params)
        lines.append(f"function opensak.{func.name}({args}) end")
    lines.append("")
    return "\n".join(lines)

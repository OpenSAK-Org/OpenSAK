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

from pathlib import Path

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


def _examples_section() -> list[str]:
    lines = [
        "",
        "## Example macros",
        "",
        f"Ready-to-use scripts to copy and adapt are in [`{EXAMPLES_DIR.as_posix()}/`]"
        f"({_EXAMPLES_LINK}/). Each one starts with a comment explaining what it "
        "does and which files it expects.",
        "",
    ]
    for path in sorted(EXAMPLES_DIR.glob("*.lua")):
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
        "while you type. Macros inside the OpenSAK repository pick it up "
        "automatically. For macros in another folder, put a `.luarc.json` "
        "next to them that points at the folder holding the stub:",
        "",
        *_code('{\n'
               '  "runtime.version": "Lua 5.4",\n'
               '  "workspace.library": ["C:/path/to/OpenSAK/macros/types"]\n'
               '}', "json"),
    ]


def _param_line(p: Param) -> str:
    optional = ", optional" if p.optional else ""
    return f"- `{p.name}` (`{p.type}`{optional}) — {p.description}"


def render_api_markdown() -> str:
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
        lines.append(f"| [`opensak.{func.name}`](#opensak{func.name.replace('_', '')}) "
                     f"| {func.since} |")

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
        "Keys understood by `opensak.filter{}`, all combined with AND.",
        "",
        "| Key | Value | Meaning |",
        "|---|---|---|",
    ]
    for doc in FILTER_KEY_DOCS:
        keys = ", ".join(f"`{k}`" for k in doc.keys)
        value = doc.value.replace("|", "\\|")
        lines.append(f"| {keys} | `{value}` | {doc.description} |")

    lines += _examples_section()
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


def _overload(params: tuple[Param, ...], returns: str | None) -> str:
    args = ", ".join(f"{p.name}{'?' if p.optional else ''}: {p.type}" for p in params)
    return f"fun({args})" + (f": {returns}" if returns else "")


def render_lua_stub() -> str:
    lines = [
        "---@meta",
        f"-- {_GENERATED}",
        f"-- OpenSAK Lua macro API, version {API_VERSION}. Reference: {DOC_PATH.as_posix()}",
        "",
        "---Keys understood by `opensak.filter{}`, all combined with AND.",
        "---@class opensak.FilterSpec",
    ]
    for doc in FILTER_KEY_DOCS:
        for key in doc.keys:
            lines.append(f"---@field {key}? {doc.type} {doc.description}")

    lines += [
        "",
        "---The OpenSAK API, available as a global in every macro.",
        "opensak = {}",
    ]
    for func in API:
        lines.append("")
        lines += _comment(func.description)
        lines += ["---", f"---Since API version {func.since}.", "---"]
        lines += _comment(f"```lua\n{func.example}\n```")
        for p in func.params:
            lines.append(f"---@param {p.name}{'?' if p.optional else ''} {p.type} {p.description}")
        if func.returns:
            lines.append(f"---@return {func.returns[0]} # {func.returns[1]}")
        for form in func.overloads:
            lines.append(f"---@overload {_overload(form, func.returns and func.returns[0])}")
        args = ", ".join(p.name for p in func.params)
        lines.append(f"function opensak.{func.name}({args}) end")
    lines.append("")
    return "\n".join(lines)

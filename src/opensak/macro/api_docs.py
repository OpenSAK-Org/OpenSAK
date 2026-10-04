"""
src/opensak/macro/api_docs.py — render the Lua API reference (Markdown).

docs/macros/api.md is generated from the registry in runtime.py by
scripts/generate_macro_api_docs.py; never edit the Markdown by hand.
"""

from __future__ import annotations

from pathlib import Path

from opensak.macro.runtime import API, API_VERSION, FILTER_KEY_DOCS

DOC_PATH = Path("docs/macros/api.md")
EXAMPLES_DIR = Path("macros/examples")
# EXAMPLES_DIR as a link relative to DOC_PATH
_EXAMPLES_LINK = "../../macros/examples"


def _code(text: str) -> list[str]:
    return ["```lua", *text.splitlines(), "```"]


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


def render_api_markdown() -> str:
    lines = [
        "<!-- Generated from src/opensak/macro/runtime.py by "
        "scripts/generate_macro_api_docs.py — do not edit by hand. -->",
        "",
        "# OpenSAK Lua macro API",
        "",
        f"API version: **{API_VERSION}** (`opensak.api_version()`).",
        "",
        "Macros are Lua 5.4 scripts run in a sandbox. They talk to OpenSAK "
        "through the global `opensak` table. File access is limited to the "
        "folders listed in Settings → Folder permissions.",
        "",
        "See [Example macros](#example-macros) for complete scripts.",
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
        lines += ["", func.description, "", f"Since API version {func.since}.",
                  "", "Example:", ""]
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

"""
src/opensak/macro/api_docs.py — render the Lua API reference.

These files are generated from the registry in runtime.py by
scripts/generate_macro_api_docs.py; never edit them by hand:

  * docs/macros/api.md        — the reference for people (Markdown)
  * macros/types/opensak.lua  — a ---@meta stub for the Lua Language Server,
                                giving autocompletion and inline docs in
                                VS Code while writing macros
  * macros/types/notepad++/lua.xml
                              — Notepad++ auto-completion file: completion
                                and calltips for opensak.*
  * macros/types/ultraedit/opensak-lua.uew
                              — UltraEdit wordfile: highlighting, function
                                list and auto-complete keywords

Feature branches don't commit them: .github/workflows/macro-api-docs.yml
regenerates them after every merge to beta and opens a PR. The output must
therefore be deterministic — no timestamps, no ordering that depends on the
OS, hash seed or working directory — or that workflow opens noise PRs.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from xml.sax.saxutils import escape

from opensak.macro.cache_data import CACHE_FIELDS, CUSTOM_FIELD
from opensak.macro.cache_write import INSERT_REQUIRED, PROTECTED_FIELDS, WRITABLE_FIELDS
from opensak.macro.child_write import CHILD_TABLES, ChildField
from opensak.macro.editor_support import (
    LUARC,
    NPP_NAME,
    STUB_NAME,
    TYPES_DIR,
    ULTRAEDIT_NAME,
)
from opensak.macro.forms import (
    FIELD_KEY_DOCS,
    FIELD_TYPES,
    FORM_KEY_DOCS,
    FORM_METHOD_DOCS,
)
from opensak.macro.gsak_compare import GSAK_COMPARE
from opensak.macro.lua_stdlib import LUA_FUNCTIONS, LuaFunction
from opensak.macro import net_access
from opensak.macro.db_schema import FORMATS, INTERNAL, TABLES
from opensak.macro.deprecation import Deprecated, deprecated_note
from opensak.macro.metadata import (
    BLOCKED_DOMAINS,
    HEADER_KEYS,
    MACROS_REPO_URL,
    NO_PERMISSIONS,
    PERMISSIONS,
    read_metadata,
)
from opensak.macro.runtime import API, API_VERSION, FILTER_KEY_DOCS, ApiFunction, Param
from opensak.utils.flags import BETA_ONLY
from opensak.macro.sql import INSERT_COLUMNS, PROTECTED_COLUMNS, WRITABLE_TABLES

DOC_PATH = Path("docs/macros/api.md")
STUB_PATH = Path("macros") / TYPES_DIR / STUB_NAME
NPP_PATH = Path("macros") / TYPES_DIR / NPP_NAME
ULTRAEDIT_PATH = Path("macros") / TYPES_DIR / ULTRAEDIT_NAME
EXAMPLES_DIR = Path("macros/examples")
# Links relative to DOC_PATH
_EXAMPLES_LINK = "../../macros/examples"
_STUB_LINK = f"../../{STUB_PATH.as_posix()}"
_NPP_LINK = f"../../{NPP_PATH.as_posix()}"
_ULTRAEDIT_LINK = f"../../{ULTRAEDIT_PATH.as_posix()}"
# The copies in the user's macros folder, as Windows paths
_NPP_COPY = f"{TYPES_DIR}\\{NPP_NAME}".replace("/", "\\")
_ULTRAEDIT_COPY = f"{TYPES_DIR}\\{ULTRAEDIT_NAME}".replace("/", "\\")

LUA_KEYWORDS = (
    "and", "break", "do", "else", "elseif", "end", "false", "for",
    "function", "goto", "if", "in", "local", "nil", "not", "or", "repeat",
    "return", "then", "true", "until", "while",
)
LUA_LIBRARIES = ("coroutine", "math", "os", "string", "table", "utf8")
# The standard Lua globals a macro can use (the sandbox removes io, debug,
# package, require, load, ... and keeps only os.time/date/clock), for the
# editors that do not read the LuaLS stub.
LUA_GLOBALS = (
    "_G", "_VERSION", "assert", "error", "getmetatable", "ipairs", "next",
    "pairs", "pcall", "print", "rawequal", "rawget", "rawlen", "rawset",
    "select", "setmetatable", "tonumber", "tostring", "type", "warn",
    "xpcall",
    "coroutine.close", "coroutine.create", "coroutine.isyieldable",
    "coroutine.resume", "coroutine.running", "coroutine.status",
    "coroutine.wrap", "coroutine.yield",
    "math.abs", "math.acos", "math.asin", "math.atan", "math.ceil",
    "math.cos", "math.deg", "math.exp", "math.floor", "math.fmod",
    "math.huge", "math.log", "math.max", "math.maxinteger", "math.min",
    "math.mininteger", "math.modf", "math.pi", "math.rad", "math.random",
    "math.randomseed", "math.sin", "math.sqrt", "math.tan",
    "math.tointeger", "math.type", "math.ult",
    "os.clock", "os.date", "os.time",
    "string.byte", "string.char", "string.dump", "string.find",
    "string.format", "string.gmatch", "string.gsub", "string.len",
    "string.lower", "string.match", "string.pack", "string.packsize",
    "string.rep", "string.reverse", "string.sub", "string.unpack",
    "string.upper",
    "table.concat", "table.insert", "table.move", "table.pack",
    "table.remove", "table.sort", "table.unpack",
    "utf8.char", "utf8.charpattern", "utf8.codepoint", "utf8.codes",
    "utf8.len", "utf8.offset",
)

_GENERATED = ("Generated from src/opensak/macro/runtime.py by "
              "scripts/generate_macro_api_docs.py — do not edit by hand.")


def _code(text: str, lang: str = "lua") -> list[str]:
    return [f"```{lang}", *text.splitlines(), "```"]


# ── Markdown ─────────────────────────────────────────────────────────────────


def _example_summary(path: Path) -> str:
    """The Description of the example's metadata header (or the text after
    the dash of an older "-- name.lua — what it does" first line)."""
    return read_metadata(path).description


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
    # By name, not by Path: Windows paths sort case-insensitively, and the
    # output must be the same on every OS.
    for path in sorted(examples_dir.glob("*.lua"), key=lambda p: p.name):
        summary = _example_summary(path)
        entry = f"- [`{path.name}`]({_EXAMPLES_LINK}/{path.name})"
        lines.append(f"{entry} — {summary}" if summary else entry)
    return lines


def _header_section() -> list[str]:
    return [
        "",
        "## Macro header",
        "",
        "**Macros → Macro manager** lists the macros in your macros folder and "
        "its subfolders. It shows what a comment block at the top of each macro "
        "says about it:",
        "",
        *_code(
            "-- OpenSAK Macro\n"
            "-- Name: Find easy traditionals\n"
            "-- Author: Jane Doe\n"
            "-- Version: 1.0\n"
            f"-- Requires-API: {API_VERSION}\n"
            "-- Description: Filters for unfound traditional caches with low difficulty.\n"
            "-- Permissions: none"
        ),
        "",
        "Every line is optional, and the keys are not case-sensitive. Without a "
        "`Name` the file name is shown. `Requires-API` is the lowest "
        "`opensak.api_version()` the macro works with: the manager marks a "
        "macro that needs a newer one and asks before running it. Only the "
        "comment lines before the first line of code are read.",
        *_deprecated_keys_section(),
        "",
        "From the manager, **Shortcut…** gives a macro a key (with Ctrl or Alt, "
        "or a function key) that runs it from the main window. The output then "
        "goes to the manager's output pane.",
        *_permissions_section(),
    ]


def _deprecated_keys_section() -> list[str]:
    """The deprecated header keys, if any."""
    keys = [k for k in HEADER_KEYS if k.deprecated]
    if not keys:
        return []
    return ["", "Deprecated keys still work; the Macro manager marks a macro "
            "using one, and running it prints a warning:", "",
            *(f"- `{k.name}` — {deprecated_note(k.deprecated)}" for k in keys)]


def _blocked_domains() -> str:
    """BLOCKED_DOMAINS as "`a`, `b` and `c`"."""
    names = [f"`{d}`" for d in BLOCKED_DOMAINS]
    return ", ".join(names[:-1]) + f" and {names[-1]}" if len(names) > 1 else names[0]


def _permissions_section() -> list[str]:
    blocked = _blocked_domains()
    return [
        "",
        "### Permissions and Network",
        "",
        "`Permissions` says what the macro does that a user should know before "
        "running it, and `Network` which web sites it contacts:",
        "",
        *_code(
            "-- Permissions: write, read-files, open\n"
            "-- Network: api.example.com, data.example.org"
        ),
        "",
        "Both are **information only**: a declaration never approves anything. "
        "OpenSAK asks for every write, folder and web site approval as before, "
        "whether the macro declares it or not. The manager shows the "
        "declaration in its *Permissions* column, and the community macro "
        "repository can use it to review an update that asks for more. "
        "Before a macro file with a declaration first runs, OpenSAK shows "
        "what it declares, with **Run** and **Cancel**; it shows this again "
        "only when a later version declares more. Unchanged example macros "
        "shipped with OpenSAK, unsaved macros and runs without a user "
        "interface skip it.",
        "",
        "`Permissions` takes these words, separated by commas or spaces:",
        "",
        "| Permission | The macro … |",
        "|---|---|",
        *(f"| `{name}` | {text} |" for name, text in PERMISSIONS.items()),
        f"| `{NO_PERMISSIONS}` | needs none of these |",
        "",
        f"Write `Permissions: {NO_PERMISSIONS}` for a macro that needs none of "
        "them: a macro without a `Permissions` line has not declared anything, "
        "which is not the same. A word OpenSAK does not know is shown as "
        "unknown, not dropped.",
        "",
        "`Network` lists every host the macro contacts with "
        "`opensak.get_url()`, as a host name or IP address: no `https://`, "
        "port, path or `user@`, and no wildcards, since web sites are approved "
        "one host at a time. `Network` alone covers network access; there is "
        f"no `network` permission word. Groundspeak's sites ({blocked}, with "
        "their subdomains) can "
        "never be contacted by a macro and are marked when listed.",
        "",
        "Both lines may be repeated, their values add up. A new version of a "
        "macro asks for more when it declares a permission or host the "
        "previous version did not, or when the previous version had no "
        "`Permissions` line.",
    ]


def _editor_section() -> list[str]:
    return [
        "",
        "## Editor support",
        "",
        "OpenSAK keeps editor files for this API in its macros folder "
        f"(**Open macros folder** in the macro window), in `{TYPES_DIR}`, "
        "refreshed on every start so they match the installed version:",
        "",
        f"- [`{STUB_PATH.as_posix()}`]({_STUB_LINK}) — for the "
        "[Lua Language Server](https://luals.github.io/): VS Code and other "
        "editors with LSP support (Neovim, Sublime Text, Zed, Kate, ...)",
        f"- [`{NPP_PATH.as_posix()}`]({_NPP_LINK}) — for Notepad++",
        f"- [`{ULTRAEDIT_PATH.as_posix()}`]({_ULTRAEDIT_LINK}) — for UltraEdit",
        "",
        "### VS Code",
        "",
        "The stub gives autocompletion, parameter hints and these docs while "
        'you type, through the extension "Lua" by sumneko. OpenSAK puts a '
        "`.luarc.json` next to your macros that points the language server "
        "at it:",
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
        "",
        "To run a macro in OpenSAK straight from VS Code or another editor, "
        "see [Running macros from an editor](run-from-editor.md).",
        "",
        "### Notepad++",
        "",
        f"`{_NPP_COPY}` is an auto-completion file for Notepad++: typing "
        "`opensak.` lists the API functions, and after `(` a calltip shows "
        "the parameters, the return value and the description. When a "
        "function takes several parameter lists, the arrows in the calltip "
        "switch between them. Notepad++ reads it from its own program "
        "folder, so copy it there:",
        "",
        "1. Close Notepad++.",
        "2. In Notepad++'s program folder (usually `C:\\Program Files\\Notepad++`), "
        "open `autoCompletion` and rename `lua.xml` to `lua.xml.bak`.",
        f"3. Copy `{_NPP_COPY}` from OpenSAK's macros folder into "
        "`autoCompletion`. Windows asks for administrator rights.",
        "4. Start Notepad++, open **Settings → Preferences… → "
        "Auto-Completion**, tick **Enable auto-completion on each input**, "
        "choose **Function and word completion** and tick **Function "
        "parameters hint on input**.",
        "5. Open a macro and type `opensak.` — the list appears. "
        "**Ctrl+Space** opens it by hand, **Ctrl+Shift+Space** the calltip.",
        "",
        "The file replaces Notepad++'s own one for Lua. It has calltips for "
        "the standard Lua functions too, but leaves out what macros cannot "
        "use (`io`, `debug`, `require`, `load`, most of `os`, ...) and "
        "functions of older Lua versions; rename `lua.xml.bak` back to "
        "undo. Copy the file again after an OpenSAK update to get new "
        "functions.",
        "",
        "### UltraEdit",
        "",
        f"`{_ULTRAEDIT_COPY}` is a wordfile for UltraEdit: Lua syntax "
        "highlighting with the `opensak.*` functions in their own colour, "
        "the macro's functions in the function list, and the API names in "
        "auto-complete. To set it up:",
        "",
        "1. Look up the wordfiles folder in **Advanced → Settings → Editor "
        "display → Syntax highlighting** (**Full directory path for "
        "wordfiles**).",
        f"2. Copy `{_ULTRAEDIT_COPY}` from OpenSAK's macros folder into it. "
        "UltraEdit's own `lua.uew` there claims `.lua` files too: move it "
        "out of the folder, so the OpenSAK one is used.",
        "3. Restart UltraEdit and open a macro. **View → View as "
        "(highlighting file type)** shows **Lua (OpenSAK)**.",
        "4. For completion, turn on auto-complete including the syntax "
        "highlighting keywords in **Advanced → Settings → Editor → "
        "Auto-complete**; **Ctrl+Space** opens the list by hand.",
        "",
        "Copy the wordfile again after an OpenSAK update to get new "
        "functions.",
    ]


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _forms_section() -> list[str]:
    lines = [
        "## Forms",
        "",
        "`opensak.form{}` builds a dialog from a table. With the default "
        '`layout = "auto"` the fields are stacked top to bottom with their '
        "`label` on the left, and a `group` is a framed section with its own "
        '`fields`. With `layout = "absolute"` every field is placed at its '
        "`x`, `y` (and `w`, `h`) in pixels, like in GSAK's form designer.",
        "",
        "Callbacks run while the form stays open: `on_change` of a field, "
        "`on_click` of a button, `on_open` and `validate` of the form. Each "
        "gets a handle `f` to read and change the form (see Form handle). An "
        "error in a callback stops the macro. `exit_on_change = true` closes "
        "the form instead, as GSAK's ExitOnChange did.",
        "",
        "The form returns two values: a table with the value of every field "
        "that has an `id` and a value, and the id of the button that closed "
        "it (or of the `exit_on_change` field, or \"timeout\"). Cancel, Esc, "
        "the window's close button and a button with `cancel = true` return nil.",
        "",
        "**Tools → Form designer** in the macro window builds a form with the "
        "mouse: it inserts the `opensak.form{…}` table at the cursor or "
        "edits the one the cursor is in, and imports forms of GSAK macros "
        "(`<Data> VarName=$form …`).",
        "",
        "### Form keys",
        "",
        "| Key | Type | Meaning |",
        "|---|---|---|",
    ]
    lines += [f"| `{d.key}` | `{_cell(d.type)}` | {d.description} |" for d in FORM_KEY_DOCS]
    lines += [
        "",
        "### Form fields",
        "",
        "Every field takes `type`, `id`, `label`, `tooltip`, `enabled`, "
        "`visible`, `x`, `y`, `w`, `h`, `bold`, `italic`, `color` and "
        "`font_size`; fields with a value also take `default`, `on_change`, "
        "`exit_on_change` and `remember`.",
        "",
        "| Type | GSAK | Value | Own keys | Meaning |",
        "|---|---|---|---|---|",
    ]
    for t in FIELD_TYPES:
        value = f"`{_cell(t.value)}`" if t.value else "—"
        keys = ", ".join(f"`{k}`" for k in t.keys) or "—"
        lines.append(f"| `{t.name}` | {t.gsak or '—'} | {value} | {keys} | {t.description} |")
    lines += [
        "",
        "### Field keys",
        "",
        "| Key | Type | Meaning |",
        "|---|---|---|",
    ]
    lines += [f"| `{d.key}` | `{_cell(d.type)}` | {d.description} |" for d in FIELD_KEY_DOCS]
    lines += [
        "",
        "### Form handle",
        "",
        "Callbacks get the open form as their first argument; call its "
        "methods with a colon.",
        "",
        "| Method | Meaning |",
        "|---|---|",
    ]
    lines += [f"| `{m.signature}` | {m.description} |" for m in FORM_METHOD_DOCS]
    lines.append("")
    return lines


# Appended to the functions that need the lua-macro-net feature flag
_NET_BETA = ("Beta: only available while the `lua-macro-net` feature flag "
             "is on, as it is in beta builds; check with `if opensak.get_url then`. "
             "Each web site needs the user's approval, given per macro "
             "(see Web sites).")


def _beta_note(func: ApiFunction) -> str | None:
    """The note for a function a stable release does not have: one whose
    feature flag is off in stable builds (flags.BETA_ONLY), None otherwise.
    Based on the stable defaults, not on this build's flags, so the docs are
    the same whichever build generates them."""
    if func.flag not in BETA_ONLY:
        return None
    if func.net:
        return _NET_BETA
    return (f"Beta: only available while the `{func.flag}` feature flag is "
            f"on, as it is in beta builds; check with `if opensak.{func.name} then`.")


def _flag_note(flag: str, text: str) -> list[str]:
    """*text* and a blank line if *flag* is off in stable builds, else nothing."""
    return [text, ""] if flag in BETA_ONLY else []


def _companion_section() -> list[str]:
    return [
        "",
        "## The companion database",
        "",
        *_flag_note("companion-db", "Beta: only available while the "
                    "`companion-db` feature flag is on, as it is in beta builds."),
        "Every OpenSAK database can have a companion database next to it "
        "(`Found.db` → `Found.companion.db3`) for data that doesn't fit the "
        "cache tables: per-cache data a macro collects (FTF information, "
        "solver notes, lookups), or tables one macro fills and others use. "
        "Unlike `opensak.macro_db()`, which is private to one macro, it "
        "belongs to the database and is shared by all macros. "
        "`opensak.companion()` creates it; from then on it is attached to "
        "every macro run as schema `companion`.",
        "",
        "| | `opensak.macro_db()` | companion database |",
        "|---|---|---|",
        "| One per | macro (`foo.lua` → `foo.db3`) | database |",
        "| Read | the macro | every macro |",
        "| Write | the macro | the owner of the table: the macro that created it |",
        "| Rows follow their caches | no | yes, in tables with a `gc_code` column |",
        "| Follows its database on copy, move, rename, delete, backup | no | yes |",
        "",
        "A macro creates its tables, indexes and views with plain SQL "
        "(`CREATE TABLE companion.<name> …`) through `opensak.sql()`, "
        "`opensak.sql_each()` or `opensak.sql_write()`, and OpenSAK records "
        "the macro's file as their owner. Only the owner can change, alter "
        "or drop them; every other macro can read them. A moved or renamed "
        "macro file is a new owner. Tables starting with `opensak_` belong "
        "to OpenSAK (`opensak_tables` lists the tables with their owners) "
        "and are read-only. Triggers can't be created there.",
        "",
        "A table with a `gc_code` column is *cache-linked*: its rows belong "
        "to the cache with that code. When caches are deleted, their rows "
        "are deleted; when they are copied or moved to another database, "
        "their rows go along, and the table is created there if needed. Rows "
        "whose cache is gone (after a crash) are removed when the database "
        "is opened. `opensak.set_companion_table{ name = ..., linked = false }` "
        "turns that off for a table; `code_column` names another column.",
        "",
        *_code(
            "-- ftf.lua: collects FTF information\n"
            "opensak.companion()\n"
            'opensak.sql("CREATE TABLE IF NOT EXISTS companion.ftf_info "\n'
            '    .. "(gc_code TEXT PRIMARY KEY, ftf_date TEXT, ftf_by TEXT)")\n'
            'opensak.sql("INSERT INTO companion.ftf_info VALUES (?, ?, ?) "\n'
            '    .. "ON CONFLICT (gc_code) DO UPDATE SET ftf_date = excluded.ftf_date, "\n'
            '    .. "ftf_by = excluded.ftf_by", { "GC12345", "2024-05-01", "someone" })\n'
            'opensak.set_companion_table{ name = "ftf_info", description = "FTF data from ftf.lua" }\n'
            "\n"
            "-- any other macro: reads it\n"
            'for _, r in ipairs(opensak.sql("SELECT c.gc_code, c.name, f.ftf_date "\n'
            '        .. "FROM caches c JOIN companion.ftf_info f USING (gc_code)")) do\n'
            "    print(r.gc_code, r.name, r.ftf_date)\n"
            "end"
        ),
        "",
        "`opensak.attach()` attaches a database's companion along with it, "
        "read-only, as `<schema>_companion`.",
    ]


def _custom_fields_section() -> list[str]:
    return [
        "",
        "## Custom fields",
        "",
        *_flag_note("companion-db", "Beta: only available while the "
                    "`companion-db` feature flag is on, as it is in beta builds."),
        "Custom fields are user-defined fields per cache, as in GSAK: the "
        "user defines them in Tools → Custom fields, the GSAK import brings "
        "them along, and a macro can define its own with "
        "`opensak.define_custom_field{}`. A *local* field belongs to one "
        "database, a *global* one to every database. They are shown in the "
        "cache list and the detail panel, and the Filter dialog has a Custom "
        "tab for them.",
        "",
        "The `custom` field of a cache holds them all, name → value: a "
        "boolean as `true`/`false`, numbers as numbers, text and dates "
        "(`\"YYYY-MM-DD\"`) as strings. A cache nobody set a field for has "
        "the field's default. `opensak.update(code, { custom = { … } })` "
        "changes them, with the same permission as any change of a cache; "
        "`false` clears a field that is not a boolean, so it shows its "
        "default again. Field names are matched in any case.",
        "",
        *_code(
            'opensak.define_custom_field{ name = "Visited", type = "date" }\n'
            "for c in opensak.caches{ found = true, fields = {\"found_date\", \"custom\"} } do\n"
            "    if c.found_date and not c.custom.Visited then\n"
            '        opensak.update(c.code, { custom = { Visited = c.found_date } })\n'
            "    end\n"
            "end"
        ),
        "",
        "Inside `opensak.transaction()` custom fields are written when the "
        "outermost transaction commits, and not at all when it is rolled "
        "back. They live in the companion database, in OpenSAK's table "
        "`companion.custom_values` (one row per cache that has values, "
        "keyed by `gc_code`, one column per field); SQL can read it, but "
        "only `opensak.update()` can change it.",
    ]


def _attach_section() -> list[str]:
    return [
        "",
        "## Several databases in one query",
        "",
        *_flag_note("lua-macro-write", "Beta: `opensak.attach()`, "
                    "`opensak.detach()` and `opensak.attached()` are only "
                    "available while the `lua-macro-write` feature flag is on, "
                    "as it is in beta builds."),
        "The `database` option reads one other database per call. To join "
        "several databases in one statement, as GSAK macros do with SQL "
        "`ATTACH`, attach them with `opensak.attach()`: each one becomes a "
        "schema of the active database's SQL connection, next to "
        "`opensak.macro_db()`'s `macro`. Attached databases are read-only; "
        "`opensak.sql_write()` still only changes the active database (and "
        "`macro`), but may read the attached ones, e.g. in a subquery. SQL "
        "`ATTACH` and `DETACH` stay refused, so a macro cannot open files "
        "past the folder permissions.",
        "",
        "For example, with found caches in one database and placed caches in "
        "another, as FindStatGen keeps them:",
        "",
        *_code(
            'opensak.attach("Found")                -- schema "found"\n'
            'opensak.attach("Placed 2020", "placed")\n'
            "\n"
            "-- found caches that are also in the active database\n"
            'for _, r in ipairs(opensak.sql([[\n'
            "    SELECT c.gc_code, c.name, f.found_date FROM caches c\n"
            "    JOIN found.caches f ON f.gc_code = c.gc_code\n"
            "    ORDER BY f.found_date]])) do\n"
            "    print(r.gc_code, r.name, r.found_date)\n"
            "end\n"
            "\n"
            "-- flag the active database's caches placed by me\n"
            'opensak.sql_write("UPDATE caches SET user_flag = 1 "\n'
            '    .. "WHERE gc_code IN (SELECT gc_code FROM placed.caches)")'
        ),
        "",
        "An attached database is not updated to the current schema: one last "
        "opened by an older OpenSAK version may lack columns. Open it once "
        "(Manage Databases) to update it, or check with "
        '`opensak.columns("found.caches")`. A database in use by another '
        "OpenSAK window can be attached; the statement sees what that "
        "window has committed.",
        "",
        "| GSAK | OpenSAK |",
        "|---|---|",
        "| `ATTACH 'x\\sqlite.db3' AS x` (an OpenSAK database) | `opensak.attach(\"x\", \"x\")` |",
        "| `ATTACH 'any.db3' AS y` (any other file) | not available yet |",
        "| `DETACH x` | `opensak.detach(\"x\")`, or nothing: all are detached when the macro ends |",
        "| own writable database | `opensak.macro_db()` |",
    ]


def _changing_section() -> list[str]:
    writable = ", ".join(f"`{f.name}`" for f in WRITABLE_FIELDS)
    required = ", ".join(f"`{k}`" for k in INSERT_REQUIRED)
    lines = [
        "",
        "## Changing caches",
        "",
        *_flag_note("lua-macro-write", "Beta: `opensak.update()`, "
                    "`opensak.insert{}`, `opensak.delete()`, "
                    "`opensak.insert_<x>()`, `opensak.update_<x>()`, "
                    "`opensak.delete_<x>()`, `opensak.sql_write()`, "
                    "`opensak.transaction()`, `opensak.in_transaction()` and "
                    "`opensak.resync()` are only available while the "
                    "`lua-macro-write` feature flag is on, as it is in beta "
                    "builds. Without it they are nil, and "
                    "`opensak.set_corrected()` and `opensak.clear_corrected()` "
                    "change the database without asking."),
        "`opensak.update()`, `opensak.insert{}`, `opensak.delete()`, "
        "`opensak.insert_<x>()`, `opensak.update_<x>()`, `opensak.delete_<x>()`, "
        "`opensak.sql_write()`, "
        "`opensak.set_corrected()` and `opensak.clear_corrected()` change the "
        "active database. Before a macro's first change, OpenSAK asks "
        "whether this macro may change your OpenSAK databases: **Deny** (the "
        "function fails, and OpenSAK does not ask again during this run), "
        "**Until OpenSAK closes**, or **Always**. The answer belongs to the "
        "macro file and its code, not to a database: editing the macro in "
        "OpenSAK's macro editor keeps it, while a file changed in any other "
        "way (replaced by a downloaded update, edited in another editor) is "
        "asked about again, saying what changed: the version, and what the "
        "`Permissions:` and `Network:` header now declares that it did not "
        "before. A moved or renamed file is asked about again. Macros allowed always are "
        "listed in Settings → Folder permissions, where they can be removed "
        "again. An unsaved macro can only be allowed until OpenSAK closes. "
        "A macro started with `opensak.run_macro()` is asked about on its "
        "own. A run from an external editor (`opensak --run-macro`) asks in "
        "the OpenSAK window, like any other run, and also offers **While I "
        "edit it**: the macro may then change databases after every change "
        "of the file until OpenSAK closes, but only when run from the "
        "external editor.",
        "",
        "Locked caches (`locked`) are only protected from being overwritten "
        "by an import. Macros can change and delete them like any other "
        "cache, as the GUI can; to leave them alone, filter them out, e.g. "
        "`opensak.caches{ locked = false }`.",
        "",
        "Which function to use:",
        "",
        "- A cache: `opensak.update(code, fields)` changes it, "
        "`opensak.insert{}` adds a new one, `opensak.delete(code)` deletes "
        "it with all its rows (also a list of codes).",
        "- Its logs, waypoints, attributes and trackables: "
        "`opensak.insert_<x>(code, fields)`, `opensak.update_<x>(id, fields)` "
        "and `opensak.delete_<x>(id)` "
        "(see Logs, waypoints, attributes and trackables).",
        "- Corrected coordinates: `opensak.set_corrected()` and "
        "`opensak.clear_corrected()`.",
        "- `opensak.sql_write()` is the power tool for bulk work: one "
        "INSERT, UPDATE or DELETE statement that changes many rows at once, e.g. "
        "marking every unfound cache of a country, or copying rows from the "
        "macro's own database. Use it when a loop over the functions above "
        "would be too slow or too long-winded.",
        "",
        "Start with the dedicated functions: they take Lua values, check each "
        "field (type, range, date format) with a clear error message, and need "
        "no knowledge of the database schema. `opensak.sql_write()` only "
        "refuses protected columns and what SQLite itself rejects; it works "
        "with the tables and columns of "
        "[Database tables for macros](#database-tables-for-macros), but "
        "what it may change is not part of that promise.",
        "",
        f"Writable cache fields: {writable}. `false` clears a field that is "
        'not a boolean, and "" clears a text field. `user_data` takes the '
        'slots to change, e.g. `{ [2] = "solved" }`; `corrected` takes '
        "`{ lat = .., lon = .. }` or a coordinate string. Dates are "
        f'`"YYYY-MM-DD"`. `opensak.insert{{}}` also needs `code`, and {required}.',
        "",
        "These fields cannot be written:",
        "",
    ]
    lines += [f"- `{name}` — {why}" for name, why in PROTECTED_FIELDS.items()]
    lines += [
        "",
        "Each change is committed on its own. To keep several changes "
        "together, or undo them all on an error, run them inside "
        "`opensak.transaction(function() ... end)`, which is also faster for "
        "many changes. SQL `BEGIN`/`COMMIT` is refused.",
        "",
        "`opensak.sql_write()` may INSERT into, UPDATE and DELETE from these "
        "tables. An "
        "INSERT only has to give the required columns; every other column "
        "gets its default (`opensak.columns()` marks them as `required`). "
        "An UPDATE may not set the protected columns. Whatever an INSERT "
        "puts into the columns OpenSAK maintains is recalculated right away. "
        "A DELETE from `caches` also deletes the logs, waypoints, attributes, "
        "trackables and user note of each cache, like `opensak.delete()`.",
        "",
        "Deleted caches cannot be restored from inside OpenSAK, only from a "
        "backup. At the end of a run that deleted caches, the macro output "
        "says how many, per database; the cache list, map and detail panel "
        "drop them once the macro has ended. Deletes inside "
        "`opensak.transaction()` are undone with it.",
        "",
        "OpenSAK recalculates the columns it maintains (distance, bearing, "
        "counts, log dates) after every write. Inside "
        "`opensak.transaction()` it does so only once, just before the "
        "commit, for all caches the transaction changed — much faster for "
        "many writes. Until then reads inside the transaction see the old "
        "values; `opensak.resync()` recalculates them on demand.",
        "",
        "| Table | Required for INSERT | Protected columns |",
        "|---|---|---|",
    ]
    for table in sorted(WRITABLE_TABLES):
        required = ", ".join(f"`{c}`" for c in INSERT_COLUMNS[table])
        cols = ", ".join(f"`{c}`" for c in sorted(PROTECTED_COLUMNS[table]))
        lines.append(f"| `{table}` | {required} | {cols} |")
    return lines + _children_section()


def _web_section() -> list[str]:
    blocked = _blocked_domains()
    return [
        "",
        "## Web sites",
        "",
        *_flag_note("lua-macro-net", "Beta: `opensak.get_url()` is only "
                    "available while the `lua-macro-net` feature flag is on, "
                    "as it is in beta builds."),
        "A macro can read everything in your databases, so a macro that "
        "contacts a web site could also send your data there. Before a "
        "macro first contacts a web site (host), OpenSAK therefore asks "
        "whether this macro may: **This time only** (for this run), "
        "**Always** or **Deny** (not asked again during this run). The "
        "question names the host, says when the connection is not "
        "encrypted (`http:`) and when the macro's `Network:` header does "
        "not list the host. A host on the local network (your router, a "
        "NAS) is asked about on its own, also when it was allowed as an "
        "internet address: OpenSAK checks the address it actually connects "
        "to. A redirect to another host is asked about like the first one. "
        f"Groundspeak's sites ({blocked}, with their subdomains) can never be "
        "contacted: their terms of use don't allow automated access outside "
        "their API.",
        "",
        "Like folders, the answer belongs to this macro and its code: "
        "web sites allowed always are listed with the macro in "
        "Settings → Folder permissions, and a macro changed outside "
        "OpenSAK's macro editor is asked once whether it may have them "
        "again. A macro started with `opensak.run_macro()` is asked on its "
        "own. An unsaved macro can be allowed a web site for one run only. "
        f"A run asks about at most {net_access.MAX_HOST_QUESTIONS_PER_RUN} "
        "web sites; after that, the ones not allowed fail without asking.",
        "",
        "Requests are GET only, with the User-Agent `OpenSAK-Macro/<version>` "
        "and without cookies. The system's proxy settings apply.",
    ]


def _lua_fields() -> dict[tuple[str, str], str]:
    """(table, column) → the field holding the same value in opensak.cache()
    or opensak.logs(), waypoints(), ..."""
    fields: dict[tuple[str, str], str] = {}
    for field in CACHE_FIELDS:
        for column in field.columns:
            fields.setdefault((column.class_.__tablename__, column.key), field.name)
    for t in CHILD_TABLES:
        for f in t.fields:
            fields[(t.model.__tablename__, f.attribute)] = f.name
    return fields


def _schema_section() -> list[str]:
    internal = "; ".join(
        f"`{table}`: " + ", ".join(f"`{c}`" for c in sorted(columns))
        for table, columns in sorted(INTERNAL.items()))
    lines = [
        "",
        "## Database tables for macros",
        "",
        "`opensak.sql()` and `opensak.sql_each()` read the tables of the "
        "database directly. The tables and columns listed here are part of the "
        "macro API for reading, like the functions: within an API version they "
        "keep their names and their value formats (cache type strings, date "
        "format, units, `0`/`1` for booleans), and tables and columns are only "
        "added. A rename or removal comes with a new API version, after a "
        "deprecation period, as for functions. If the structure of a table has "
        "to change, its old table and column names stay available as a "
        "compatibility view, so queries using them keep working.",
        "",
        "Not covered by this:",
        "",
        "- Writing to these tables with `opensak.sql_write()`. Writes are only "
        "promised through `opensak.update()`, `opensak.insert{}`, "
        "`opensak.delete()` and `opensak.insert_<x>()`, `opensak.update_<x>()`, "
        "`opensak.delete_<x>()` (see [Changing caches](#changing-caches)).",
        "- Tables starting with `opensak_`, and tables not listed here "
        "(e.g. `db_settings`).",
        "- Indexes: which ones exist, and so how fast a query is.",
        f"- Columns not listed here: {internal}.",
        "- The tables other macros made in the companion database, and "
        "`companion.custom_values`, whose columns are the user's custom fields "
        "(read them with the `custom` cache field).",
        "",
        "A column is NULL (`nil` in Lua) where nothing is known. *Field* names "
        "the same value in the tables `opensak.cache()`, `opensak.logs()`, "
        "`opensak.waypoints()`, … return, which convert it to Lua: booleans to "
        '`true`/`false`, dates to `"YYYY-MM-DD"`.',
        "",
        "### Value formats",
        "",
        "| Format | Values |",
        "|---|---|",
    ]
    lines += [f"| {f.label} | {f.description} |" for f in FORMATS.values()]
    fields = _lua_fields()
    for t in TABLES:
        lines += [
            "",
            f"### Table `{t.name}`",
            "",
            t.description,
            "",
            "| Column | Format | Field | Meaning |",
            "|---|---|---|---|",
        ]
        for c in t.columns:
            field = fields.get((t.name, c.name))
            lines.append(f"| `{c.name}` | {FORMATS[c.format].label} | "
                         f"{f'`{field}`' if field else ''} | "
                         f"{_deprecated_prefix(c.deprecated)}{c.description} |")
    return lines


def _update_type(field: ChildField) -> str:
    """The type opensak.update_<x>() accepts: `false` clears a nil-able field."""
    return f"{field.type[:-1]}|false" if field.type.endswith("?") else field.type


def _children_section() -> list[str]:
    lines = [
        "",
        "## Logs, waypoints, attributes and trackables",
        "",
        "The rows belonging to a cache are read with "
        + ", ".join(f"`opensak.{t.plural}()`" for t in CHILD_TABLES)
        + ", added with `opensak.insert_<x>(code, fields)`, changed with "
        "`opensak.update_<x>(id, fields)` and deleted with "
        "`opensak.delete_<x>(id)`, without SQL. These are the way to "
        "add, change or delete a single row; keep `opensak.sql_write()` for bulk "
        "changes across many caches. Each row has an `id`, "
        "which identifies it and cannot be changed, as cannot the cache it "
        "belongs to. The changes need the user's permission and can be kept "
        "together with `opensak.transaction()`, like those of caches; OpenSAK "
        "updates the cache's counts and log dates itself.",
    ]
    for t in CHILD_TABLES:
        lines += [
            "",
            f"### {t.plural.capitalize()}",
            "",
            "| Field | Type | Meaning |",
            "|---|---|---|",
        ]
        for f in t.fields:
            required = " Required for `opensak.insert_" + t.singular + "`." if f.name in t.required else ""
            lines.append(f"| `{f.name}` | `{f.type.replace('|', chr(92) + '|')}` | {f.description}{required} |")
    return lines


def _param_line(p: Param) -> str:
    optional = ", optional" if p.optional else ""
    return (f"- `{p.name}` (`{p.type}`{optional}) — "
            f"{_deprecated_prefix(p.deprecated)}{p.description}")


def _deprecated_prefix(deprecated: Deprecated | None) -> str:
    """E.g. "**Deprecated in API version 5: use `new`.** " before a
    description; "" if not deprecated."""
    return f"**{deprecated_note(deprecated)}** " if deprecated else ""


def _deprecated_comment(deprecated: Deprecated | None) -> str:
    """E.g. "(deprecated, use new) " before a description in the editor
    files; "" if not deprecated."""
    return f"(deprecated, use {deprecated[1]}) " if deprecated else ""


def _since(func) -> str:
    """E.g. "Since API version 2. Changed in API version 3: added `fmt`." """
    text = f"Since API version {func.since}."
    for version, what in func.changed:
        text += f" Changed in API version {version}: {what}."
    return text


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
        "writing are asked separately. Both belong to this macro alone: "
        "another macro, also one started with `opensak.run_macro()`, is "
        "asked on its own, and a macro changed outside OpenSAK's macro editor "
        "is asked once whether it may have its folders again. An unsaved "
        "macro can be allowed a folder for one run only. OpenSAK's own "
        "settings and database files are never accessible.",
        "",
        "Within an API version, functions, parameters, cache fields and header "
        "keys are only added. A rename or removal comes with a new API version, "
        "after a deprecation period of at least one stable release: the "
        "reference marks what is deprecated and what to use instead, and the "
        "first call of a deprecated function in a run prints a warning naming "
        "the macro line. The macro keeps running.",
        "",
        "See [Example macros](#example-macros) for complete scripts and "
        "[Editor support](#editor-support) for autocompletion in VS Code, "
        "Notepad++ and UltraEdit.",
        "",
        f"Macros shared by other users are in the [OpenSAK-Macros]({MACROS_REPO_URL}) "
        "repository, one folder per macro; its README explains how to install "
        "one and how to share your own.",
        "",
        "## Functions",
        "",
        "| Function | Since |",
        "|---|---|",
    ]
    for func in API:
        anchor = func.name.replace("_", "").replace(".", "")
        changed = "".join(f", changed in {v}" for v, _ in func.changed)
        if func.deprecated:
            changed += f", deprecated in {func.deprecated[0]}"
        lines.append(f"| [`opensak.{func.name}`](#opensak{anchor}) | {func.since}{changed} |")

    for func in API:
        lines += ["", f"### opensak.{func.name}", ""]
        if func.deprecated:
            lines += [f"**{deprecated_note(func.deprecated)}**", ""]
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
        lines += [_since(func), ""]
        beta = _beta_note(func)
        if beta:
            lines += [beta, ""]
        lines += ["Example:", ""]
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
        "database is the same as leaving `database` out. To join another "
        "database with the active one in one statement, see "
        "[Several databases in one query](#several-databases-in-one-query).",
        *_attach_section(),
        *_companion_section(),
        *_custom_fields_section(),
        "",
        *_forms_section(),
        "## Cache fields",
        "",
        "Keys of the table returned by `opensak.cache()`, `opensak.current()` "
        "and `opensak.caches()`. The table is a snapshot; missing data is `nil`.",
        "",
        "| Field | Type | Meaning |",
        "|---|---|---|",
    ]
    for field in (*CACHE_FIELDS, CUSTOM_FIELD):
        type_ = field.type.replace("|", "\\|")
        lines.append(f"| `{field.name}` | `{type_}` | "
                     f"{_deprecated_prefix(field.deprecated)}{field.description} |")

    lines += _schema_section()
    lines += _changing_section()
    lines += _web_section()
    lines += _examples_section(examples_dir)
    lines += _header_section()
    lines += _editor_section()

    lines += [
        "",
        "## Globals",
        "",
        "### print",
        "",
        *_code("print(...)"),
        "",
        "Write the arguments, separated by tabs, to the macro output pane "
        "(to stdout when run with `opensak --run-macro`, see "
        "[Running macros from an editor](run-from-editor.md)).",
        "",
        *GSAK_COMPARE.splitlines(),
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
        "---Filter keys plus `fields`, `all` and `database`, understood by `opensak.caches{}`.",
        "---@class opensak.CachesSpec: opensak.FilterSpec",
        "---@field all? boolean Every cache of the database, whatever the active filter shows.",
        "---@field fields? string[] Cache fields to load (`code` is always included).",
        "---@field database? string Read this database instead of the active one.",
        "",
        "---Options of the read functions (`opensak.cache()`, `opensak.sql()`, ...).",
        "---@class opensak.ReadOptions",
        "---@field database? string Read this database instead of the active one.",
        "",
        "---The table of `opensak.set_companion_table{}`.",
        "---@class opensak.CompanionTableSpec",
        "---@field name string A table or view of the running macro in the companion database.",
        "---@field linked? boolean Cache-linked: its rows follow their caches.",
        "---@field code_column? string The column with the GC code (default `gc_code`); implies `linked`.",
        "---@field description? string Shown with the table in OpenSAK.",
        "",
        "---A table or view in the companion database, from `opensak.companion_tables()`.",
        "---@class opensak.CompanionTable",
        "---@field name string Table or view name, used as `companion.<name>`.",
        '---@field type "table"|"view"',
        "---@field owner? string The owner's key: the macro file's path, or `opensak`; nil without an owner.",
        "---@field owner_name? string The owner's name, for display.",
        "---@field mine boolean The running macro owns it, so it can change it.",
        '---@field kind "cache"|"free" `cache`: cache-linked, its rows follow their caches.',
        "---@field code_column? string For a cache-linked table: the column with the GC code.",
        "---@field description? string",
        "---@field rows? integer Number of rows (tables only).",
        "",
        "---A custom field, from `opensak.custom_fields()`.",
        "---@class opensak.CustomField",
        "---@field name string The field's name, the key in a cache's `custom` table.",
        '---@field type "string"|"integer"|"real"|"date"|"boolean"',
        "---@field default any The value of a cache nobody set the field for.",
        '---@field scope "local"|"global" `global`: defined for every database.',
        '---@field origin "opensak"|"gsak" Where the definition came from.',
        "---@field position integer Display order.",
        "---@field expression? string GSAK's display expression, if any.",
        "",
        "---The table of `opensak.define_custom_field{}`.",
        "---@class opensak.CustomFieldDefinition",
        "---@field name string Letters, digits and `_`; not a cache column or an SQL keyword.",
        '---@field type "string"|"integer"|"real"|"date"|"boolean"',
        "---@field default? any Shown for every cache nobody set the field for.",
        '---@field scope? "local"|"global" Default "local" (this database only).',
        "---@field expression? string SQL shown in the cache list instead of the value, e.g. `nullif(Finds, -1)`.",
        "",
        "---Options of `opensak.get_url()`.",
        "---@class opensak.GetUrlOptions",
        "---@field timeout? number Seconds without an answer, 1-120 (default 30).",
        "---@field caption? string Show a progress window with this title.",
        "---@field binary? boolean Return the bytes as they are, without decoding (default false).",
        "---@field max_bytes? integer The largest body in bytes (default 10 MB, at most 50 MB).",
        "",
        "---What `opensak.get_url()` got besides the body.",
        "---@class opensak.UrlInfo",
        "---@field status integer The HTTP status, e.g. 200 or 404.",
        "---@field content_type string The Content-Type header (\"\" if none).",
        "---@field url string The URL after redirects.",
        "",
        "---Options of `opensak.move_caches()` and `opensak.copy_caches()`.",
        "---@class opensak.TransferOptions",
        "---@field codes? string[] GC codes to transfer (default: the caches of the active filter).",
        '---@field if_exists? "newer"|"replace"|"skip" When the cache exists in the target (default "newer").',
        "",
        "---A matrix from `opensak.stats.pivot()`, for `opensak.html.matrix()`.",
        "---@class opensak.Matrix",
        "---@field rows any[] Values of the row field.",
        "---@field cols any[] Values of the column field.",
        "---@field cells number[][] `cells[i][j]`: the count for rows[i] and cols[j].",
        "---@field max number The largest cell.",
        "---@field total number Sum of all cells.",
        "---@field filled integer Number of cells that are not 0.",
        "",
        "---A run of days from `opensak.stats.streaks()`.",
        "---@class opensak.Run",
        "---@field days integer Number of days.",
        '---@field first string First day, "YYYY-MM-DD".',
        '---@field last string Last day, "YYYY-MM-DD".',
        "---Options of `opensak.import_gsak()`.",
        "---@class opensak.GsakImportOptions",
        "---@field databases? string|string[] GSAK databases to import, by name (default: all of them).",
        "---@field exclude? string|string[] GSAK databases to leave out.",
        "---@field target? string|table<string, string> One OpenSAK database for all, or GSAK name = OpenSAK database (default: the GSAK name).",
        '---@field if_exists? "skip"|"merge"|"overwrite" When the OpenSAK database exists (default "skip").',
        "---@field create? boolean Create a missing OpenSAK database (default true).",
        '---@field filters? boolean|"skip"|"overwrite" Import the saved filters of gsak.db3 (default false).',
        '---@field locations? boolean|"skip"|"overwrite" Import the user locations of gsak.db3 (default false).',
        "---@field quiet? boolean Don't print the summary (default false).",
        "",
        "---What one GSAK database of `opensak.import_gsak()` did.",
        "---@class opensak.GsakImportDatabase",
        "---@field gsak string GSAK database name.",
        "---@field target string OpenSAK database.",
        '---@field action "created"|"merged"|"overwritten"|"skipped"|"failed"|"cancelled"',
        "---@field reason? string Why it was skipped, failed or cancelled.",
        "---@field cleared integer Caches removed first (overwrite).",
        "---@field created? integer New caches.",
        "---@field updated? integer Updated caches.",
        "---@field skipped? integer Caches that could not be imported.",
        "---@field waypoints? integer",
        "---@field logs? integer",
        "---@field attributes? integer",
        "---@field notes? integer",
        "---@field trackables? integer",
        "---@field corrected? integer",
        "---@field note_images? integer Embedded note images replaced by placeholders.",
        "---@field warnings? integer",
        "---@field errors? string[] The first cache errors.",
        "",
        "---The report of `opensak.import_gsak()`.",
        "---@class opensak.GsakImportReport",
        "---@field ok boolean Nothing failed and the run was not cancelled.",
        "---@field cancelled boolean",
        "---@field source string",
        '---@field kind "backup"|"folder"|"file"|"settings"',
        "---@field created integer New caches, all databases.",
        "---@field updated integer Updated caches, all databases.",
        "---@field skipped integer Caches that could not be imported.",
        "---@field errors integer Failed caches, databases and settings imports.",
        "---@field databases opensak.GsakImportDatabase[]",
        "---@field new_databases string[] OpenSAK databases created or added to the list.",
        "---@field locations? {added?: integer, updated?: integer, skipped?: integer, invalid?: integer, error?: string}",
        "---@field filters? {imported?: integer, skipped?: integer, failed?: integer, error?: string}",
        "---@field notes string[]",
        "",
        "---Options of `opensak.import_gpx()`.",
        "---@class opensak.GpxImportOptions",
        "---@field database? string OpenSAK database to import into (default: the setting's database, else the active one).",
        "---@field quiet? boolean Don't print the summary (default false).",
        "",
        "---What one file of `opensak.import_gpx()` did.",
        "---@class opensak.GpxImportFile",
        "---@field path string",
        "---@field error? string Why the file (or a refused file or folder of the setting) failed.",
        "---@field created? integer New caches.",
        "---@field updated? integer Updated caches.",
        "---@field skipped? integer Caches that could not be imported.",
        "---@field waypoints? integer",
        "---@field warnings? integer",
        "---@field errors? string[] The first cache errors.",
        "",
        "---The report of `opensak.import_gpx()`.",
        "---@class opensak.GpxImportReport",
        "---@field ok boolean Nothing failed and the run was not cancelled.",
        "---@field cancelled boolean",
        "---@field source string",
        '---@field kind "setting"|"folder"|"file"',
        "---@field database string OpenSAK database imported into.",
        "---@field created integer New caches, all files.",
        "---@field updated integer Updated caches, all files.",
        "---@field skipped integer Caches that could not be imported.",
        "---@field waypoints integer",
        "---@field errors integer Failed caches and files.",
        "---@field files opensak.GpxImportFile[]",
        "---@field missing string[] Files and folders of the setting that no longer exist.",
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
        "---The table passed to `opensak.form{}`.",
        "---@class opensak.FormSpec",
        *(f"---@field {d.key}? {d.type} {d.description}" for d in FORM_KEY_DOCS),
        "",
        "---A field of `opensak.form{}` (see Forms in the API reference).",
        "---@class opensak.FormField",
        "---@field type " + "|".join(f'"{t.name}"' for t in FIELD_TYPES) + " The field type.",
        *(f"---@field {d.key}? {d.type} {d.description}" for d in FIELD_KEY_DOCS
          if d.key != "type"),
        "",
        "---A button at the bottom of a form.",
        "---@class opensak.FormButton",
        *(f"---@field {d.key}? {d.type} {d.description}" for d in FIELD_KEY_DOCS
          if d.key in ("id", "label", "tooltip", "enabled", "visible", "default",
                       "cancel", "on_click", "bold", "italic", "color", "font_size")),
        "",
        "---The open form, passed to form callbacks.",
        "---@class opensak.Form",
        *(f"---@field {m.name} {m.type} {m.description}" for m in FORM_METHOD_DOCS),
        "",
        "---The window returned by `opensak.progress()`.",
        "---@class opensak.Progress",
        "---@field update fun(self: opensak.Progress, i?: integer, text?: string): boolean "
        "Show step *i* and *text*; false once the user has pressed Cancel.",
        "---@field close fun(self: opensak.Progress) Close the window.",
        "",
        "---A cache as returned by `opensak.cache()` and `opensak.caches()` (a snapshot).",
        "---@class opensak.Cache",
    ]
    for field in (*CACHE_FIELDS, CUSTOM_FIELD):
        lines.append(f"---@field {field.name} {field.type} "
                     f"{_deprecated_comment(field.deprecated)}{field.description}")
    # Macros may add fields of their own, e.g. to group by with opensak.stats.
    lines.append("---@field [string] any")

    descriptions = {f.name: f.description for f in CACHE_FIELDS}
    lines += [
        "",
        "---The fields `opensak.update()` may change; `false` clears a field that is not a boolean.",
        "---@class opensak.CacheUpdate",
    ]
    for writable in WRITABLE_FIELDS:
        lines.append(f"---@field {writable.name}? {writable.type} {descriptions[writable.name]}")
    lines.append("---@field custom? table<string, any> Custom fields to change, name → value "
                 "(see Custom fields).")
    lines += [
        "",
        "---A new cache for `opensak.insert{}`.",
        "---@class opensak.CacheInsert: opensak.CacheUpdate",
        '---@field code string Cache code, e.g. "GC12345".',
    ]
    for writable in WRITABLE_FIELDS:
        if writable.name in INSERT_REQUIRED:
            lines.append(f"---@field {writable.name} {writable.type} {descriptions[writable.name]}")

    for t in CHILD_TABLES:
        lines += [
            "",
            f"---A {t.singular} as returned by `opensak.{t.plural}()` (a snapshot).",
            f"---@class {t.class_name}",
            f"---@field id integer Identifies the {t.singular}, for `opensak.update_{t.singular}()`.",
        ]
        lines += [f"---@field {f.name} {f.type} {f.description}" for f in t.fields]
        lines += [
            "",
            f"---The fields `opensak.update_{t.singular}()` may change; `false` clears a field that may be nil.",
            f"---@class {t.class_name}Update",
        ]
        lines += [f"---@field {f.name}? {_update_type(f)} {f.description}" for f in t.fields]
        lines += [
            "",
            f"---A new {t.singular} for `opensak.insert_{t.singular}()`.",
            f"---@class {t.class_name}Insert: {t.class_name}Update",
        ]
        lines += [f"---@field {f.name} {f.type} {f.description}"
                  for f in t.fields if f.name in t.required]

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
        lines += ["---", f"---{_since(func)}", "---"]
        beta = _beta_note(func)
        if beta:
            lines += _comment(beta) + ["---"]
        lines += _comment(f"```lua\n{func.example}\n```")
        if func.deprecated:
            # Calls are shown struck through, with the hint
            lines.append(f"---@deprecated Use {func.deprecated[1]}")
        for p in func.params:
            lines.append(f"---@param {p.name}{'?' if p.optional else ''} {p.type} "
                         f"{_deprecated_comment(p.deprecated)}{p.description}")
        if func.returns:
            lines += _return_lines(*func.returns)
        for form in func.overloads:
            lines.append(f"---@overload {_overload(form, func.returns and func.returns[0])}")
        args = ", ".join(p.name for p in func.params)
        lines.append(f"function opensak.{func.name}({args}) end")
    lines.append("")
    return "\n".join(lines)


# ── Notepad++ and UltraEdit ──────────────────────────────────────────────────


def _editor_words() -> dict[str, ApiFunction | LuaFunction | None]:
    """Every name to complete, mapped to its function (None for Lua
    keywords, constants and the tables)."""
    words: dict[str, ApiFunction | LuaFunction | None] = dict.fromkeys(
        (*LUA_KEYWORDS, *LUA_LIBRARIES, *LUA_GLOBALS, "opensak"))
    for lua_func in LUA_FUNCTIONS:
        words[lua_func.name] = lua_func
    for func in API:
        namespace = func.name.rpartition(".")[0]
        if namespace:
            words.setdefault(f"opensak.{namespace}", None)
        words[f"opensak.{func.name}"] = func
    return words


def _calltip(func: ApiFunction | LuaFunction) -> str:
    text = textwrap.fill(func.description, 80)
    if isinstance(func, ApiFunction) and func.deprecated:
        text = f"(deprecated) Use {func.deprecated[1]} instead.\n{text}"
    if func.returns:
        text += "\n" + textwrap.fill(f"Returns {func.returns[0]} — {func.returns[1]}", 80)
    return text


def _xml_attr(text: str) -> str:
    return escape(text, {'"': "&quot;", "\n": "&#x0a;"})


def render_npp_autocomplete() -> str:
    """Notepad++ autoCompletion/lua.xml. Notepad++ needs the keywords
    sorted; `.` is a word character, so "opensak.cache" is one word."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8" ?>',
        f"<!-- {_GENERATED} -->",
        f"<!-- OpenSAK Lua macro API, version {API_VERSION}. Reference: {DOC_PATH.as_posix()} -->",
        "<NotepadPlus>",
        '\t<AutoComplete language="Lua">',
        '\t\t<Environment ignoreCase="no" startFunc="(" stopFunc=")" '
        'paramSeparator="," terminal=";" additionalWordChar="." />',
    ]
    words = _editor_words()
    for word in sorted(words):
        func = words[word]
        if func is None:
            lines.append(f'\t\t<KeyWord name="{word}" />')
            continue
        lines.append(f'\t\t<KeyWord name="{word}" func="yes">')
        ret = f' retVal="{_xml_attr(func.returns[0])}"' if func.returns else ""
        descr = _xml_attr(_calltip(func))
        for params in (func.params, *func.overloads):
            lines.append(f'\t\t\t<Overload{ret} descr="{descr}">')
            for p in params:
                name = f"{p.name}: {p.type}"
                name = f"[{name}]" if p.optional else name
                lines.append(f'\t\t\t\t<Param name="{_xml_attr(name)}" />')
            lines.append("\t\t\t</Overload>")
        lines.append("\t\t</KeyWord>")
    lines += ["\t</AutoComplete>", "</NotepadPlus>", ""]
    return "\n".join(lines)


def _wordfile_group(words: list[str]) -> list[str]:
    """One colour group of a wordfile: sorted, one line per first letter."""
    lines: list[str] = []
    for word in sorted(words):
        if lines and lines[-1][0] == word[0]:
            lines[-1] += f" {word}"
        else:
            lines.append(word)
    return lines


def render_ultraedit_wordfile() -> str:
    """UltraEdit wordfile (ASCII only). `.` is not a delimiter, so
    "opensak.cache" and "string.format" are highlighted as one word."""
    words = [w for w in _editor_words() if w not in LUA_KEYWORDS]
    return "\n".join([
        '/L20"Lua (OpenSAK)" Line Comment = -- Block Comment On = --[[ '
        'Block Comment Off = ]] Escape Char = \\ String Chars = "\' '
        "File Extensions = LUA",
        "/Delimiters = ~!@%^&*()-+=|\\/{}[]:;\"'<> ,\t?#",
        '/Function String = "%[ ^t]++function[ ^t]+^([a-zA-Z0-9_.:]+^)"',
        '/Function String 1 = "%[ ^t]++local[ ^t]+function[ ^t]+^([a-zA-Z0-9_]+^)"',
        '/Indent Strings = "do" "then" "repeat" "function" "else"',
        '/Unindent Strings = "end" "until" "else" "elseif"',
        '/Open Brace Strings = "{" "(" "["',
        '/Close Brace Strings = "}" ")" "]"',
        '/C1"Keywords"',
        *_wordfile_group(list(LUA_KEYWORDS)),
        '/C2"Lua functions"',
        *_wordfile_group([w for w in words if not w.startswith("opensak")]),
        '/C3"OpenSAK"',
        *_wordfile_group([w for w in words if w.startswith("opensak")]),
        "",
    ])


# (file, renderer) for everything scripts/generate_macro_api_docs.py writes.
# The Macro API docs workflow commits these paths (add-paths in
# .github/workflows/macro-api-docs.yml); a new generated file must live under
# one of them (docs/macros/api.md or macros/types/).
GENERATED_FILES = (
    (DOC_PATH, render_api_markdown),
    (STUB_PATH, render_lua_stub),
    (NPP_PATH, render_npp_autocomplete),
    (ULTRAEDIT_PATH, render_ultraedit_wordfile),
)

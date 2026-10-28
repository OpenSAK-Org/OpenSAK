"""
src/opensak/importer/gsak_view_importer.py — import saved GSAK views as
OpenSAK Column Views.

GSAK stores every saved view as one row in ``gsak.db3``::

    Settings(Type, Description, Data)
        Type        'GV' for a view
        Description the view's name as shown in GSAK
        Data        the grid columns, then a few option sections

``Data`` lists one column per line, in display order, and the first line
without ``=`` starts the option sections (``wFormat``, ``Other``, …)::

    Code=82~Code
    Name=323~Waypoint name
    ToSolve=67~ToSolve
    wFormat
    rbtMinutes.Checked=True

Each column line is ``<field>=<width>~<caption>``: the GSAK field (a cache
column or a custom field's name), its width in pixels and the header text.
Views saved by old GSAK versions use upper-case field names
(``DESC=30~Waypoint Name~F``) and a width in characters rather than pixels —
those widths are dropped, the columns are kept.

OpenSAK keeps one JSON file per Column View (see
``opensak.gui.dialogs.column_dialog.ColumnView``). Every GSAK column becomes
either

  * the matching **built-in column** (``GSAK_COLUMN_MAP``),
  * a **custom-field column** (``custom:<name>``) when gsak.db3 defines a
    custom field of that name, or the active OpenSAK database has one — the
    GSAK database import creates the field, and a database without it
    simply doesn't show the column, or
  * nothing: OpenSAK has no such column, and the column is reported as
    ignored.

The coordinate format, ``Other`` options and any linked filter are not
imported.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Callable, Optional

from opensak.importer.gsak_filter_importer import GsakFilterSourceError

# GSAK column (lower-case) → OpenSAK column id. Both the field names of
# current GSAK versions and the upper-case names of old views are listed.
GSAK_COLUMN_MAP: dict[str, str] = {
    "code":           "gc_code",
    "name":           "name",
    "cachetype":      "cache_type",
    "container":      "container",
    "difficulty":     "difficulty",
    "terrain":        "terrain",
    "distance":       "distance",
    "bearing":        "bearing",
    "found":          "found",
    "hascorrected":   "corrected",
    "userflag":       "user_flag",
    "lock":           "locked",
    "country":        "country",
    "state":          "state",
    "county":         "county",
    "placedby":       "placed_by",
    "placeddate":     "hidden_date",
    "lastlog":        "last_log",
    "numberoflogs":   "log_count",
    "dnf":            "dnf",
    "dnfdate":        "dnf_date",
    "ispremium":      "premium_only",
    "archived":       "archived",
    "latitude":       "latitude",
    "longitude":      "longitude",
    "foundbymedate":  "found_date",
    "ftf":            "first_to_find",
    "favpoints":      "favorite_points",
    "hastravelbug":   "trackables",
    "usersort":       "user_sort",
    "userdata":       "user_data_1",
    "user2":          "user_data_2",
    "user3":          "user_data_3",
    "user4":          "user_data_4",
    "cacheid":        "gc_cache_id",
    "changed":        "changed_date",
    "created":        "creation_date",
    "elevation":      "elevation",
    "foundcount":     "find_count",
    "gcnote":         "gc_note",
    "guid":           "guid",
    "hints":          "hints",
    "usernote":       "notes",
    "ownerid":        "owner_id",
    "ownername":      "owner_name",
    "source":         "source",
    "url":            "url",
    "watch":          "watch",
    "lastfounddate":  "last_found_date",
    "lastgpxdate":    "last_gpx_update",
    "last4logs":      "last_four_logs",
    "lastuserdate":   "last_user_update",
    "symbol":         "symbol",
    # Old GSAK views
    "desc":           "name",
    "typeicon":       "cache_type",
    "tbicon":         "trackables",
    "lastupdate":     "last_gpx_update",
    "lastfound":      "last_found_date",
    "first2find":     "first_to_find",
    "userfound":      "found_date",
}

# Columns the cache list always shows (column_dialog.ALWAYS_VISIBLE), in the
# order they are put in front of a view that lacks them.
_ALWAYS_VISIBLE = ("gc_code", "name")

# What OpenSAK's custom-field columns are called (column_dialog.custom_column_id).
_CUSTOM_PREFIX = "custom:"


@dataclass
class GsakViewColumn:
    """One column line of a GSAK view."""
    field: str
    width: Optional[int]      # pixels; None for an old view's character width
    caption: str = ""


@dataclass
class ViewConversion:
    """What one GSAK view becomes."""
    name: str
    visible_columns: list[str] = dc_field(default_factory=list)
    widths: dict[str, int] = dc_field(default_factory=dict)
    custom: list[str] = dc_field(default_factory=list)     # custom-field columns kept
    ignored: list[str] = dc_field(default_factory=list)    # GSAK columns dropped

    def view_data(self) -> dict:
        """The JSON a Column View is saved as (ColumnView.save())."""
        return {
            "name": self.name,
            "visible_columns": self.visible_columns,
            "widths": self.widths,
            "container_display": "bar",
            "type_display": "icon",
        }


def parse_view_blob(data: str) -> list[GsakViewColumn]:
    """The columns of a saved GSAK view, in GSAK's order."""
    columns: list[GsakViewColumn] = []
    for raw in str(data or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        field, eq, value = line.partition("=")
        if not eq:
            break       # first option section (wFormat, wDisplay, Other, …)
        parts = value.split("~")
        width: Optional[int]
        try:
            width = int(parts[0])
        except ValueError:
            width = None
        if len(parts) > 2:
            width = None    # old view: the width counts characters
        caption = parts[1] if len(parts) > 1 else ""
        columns.append(GsakViewColumn(field.strip(), width, caption.strip()))
    return columns


def convert_view(name: str, data: str, custom_fields: dict[str, str]) -> ViewConversion:
    """Convert one GSAK view. *custom_fields* maps a GSAK custom field's name
    (lower-case) to the name OpenSAK imports it under."""
    from opensak.db.custom_fields import rename_for_import

    c = ViewConversion(name=name)
    for col in parse_view_blob(data):
        key = col.field.lower()
        col_id = GSAK_COLUMN_MAP.get(key)
        if col_id is None and key in custom_fields:
            col_id = _CUSTOM_PREFIX + rename_for_import(custom_fields[key])
            c.custom.append(col_id[len(_CUSTOM_PREFIX):])
        if col_id is None:
            c.ignored.append(col.field)
            continue
        if col_id in c.visible_columns:
            continue
        c.visible_columns.append(col_id)
        if col.width is not None and col.width > 0:
            c.widths[col_id] = col.width
    # gc_code first, name right after it — where the cache list puts them.
    for position, col_id in enumerate(_ALWAYS_VISIBLE):
        if col_id not in c.visible_columns:
            before = c.visible_columns.index(_ALWAYS_VISIBLE[0]) + 1 if position else 0
            c.visible_columns.insert(before, col_id)
    return c


# ── Reading gsak.db3 ─────────────────────────────────────────────────────────

def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {name.lower() for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}


def load_gsak_views(db_path: Path) -> list[tuple[str, str]]:
    """Return [(name, data)] for every saved view in gsak.db3 (read-only)."""
    try:
        # closing(): sqlite3's own context manager leaves the file open,
        # which on Windows blocks removing an unpacked gsak.db3.
        with closing(_connect(db_path)) as conn:
            if "settings" not in _tables(conn):
                raise GsakFilterSourceError("no such table: Settings")
            rows = conn.execute(
                "SELECT Description, Data FROM Settings WHERE Type = 'GV'"
            ).fetchall()
    except sqlite3.Error as exc:
        raise GsakFilterSourceError(str(exc)) from exc
    return sorted(
        ((str(name), str(data or "")) for name, data in rows if name),
        key=lambda row: row[0].lower(),
    )


def gsak_custom_fields(db_path: Path) -> dict[str, str]:
    """{lower-case name: name} of every custom field gsak.db3 defines —
    the global ones (CustomGlobal) and the local ones of every database
    (CustomDefs)."""
    found: dict[str, str] = {}
    try:
        with closing(_connect(db_path)) as conn:
            tables = _tables(conn)
            for table in ("CustomGlobal", "CustomDefs"):
                if table.lower() not in tables:
                    continue
                for (fname,) in conn.execute(f"SELECT fname FROM {table}"):
                    if fname:
                        found.setdefault(str(fname).lower(), str(fname))
    except sqlite3.Error:
        pass
    return found


def known_custom_fields(db_path: Path) -> dict[str, str]:
    """The custom fields a view's column may name: those gsak.db3 defines,
    plus those the active OpenSAK database has (local fields of a GSAK
    database are defined in that database, not in gsak.db3)."""
    from opensak.importer.gsak_filter_importer import active_custom_fields

    found = gsak_custom_fields(db_path)
    for name in active_custom_fields() or {}:
        found.setdefault(name.lower(), name)
    return found


# ── Writing views ────────────────────────────────────────────────────────────

def safe_filename(name: str) -> str:
    """Mirror ColumnView.path_for()'s sanitisation, so the imported view and
    a later save from inside OpenSAK land on the same file."""
    return "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)


@dataclass
class ViewImportEntry:
    """What happened to one GSAK view."""
    name: str
    conversion: Optional[ViewConversion] = None
    path: Optional[Path] = None
    written: bool = False
    skipped_existing: bool = False
    error: Optional[str] = None


@dataclass
class GsakViewImportResult:
    """Everything the dialog reports after an import."""
    source: Optional[Path] = None
    entries: list[ViewImportEntry] = dc_field(default_factory=list)

    @property
    def written(self) -> int:
        return sum(1 for e in self.entries if e.written)

    @property
    def skipped(self) -> int:
        return sum(1 for e in self.entries if e.skipped_existing)

    @property
    def failed(self) -> int:
        return sum(1 for e in self.entries if e.error)


def import_gsak_views(
    db_path: Path,
    out_dir: Optional[Path] = None,
    names: Optional[list[str]] = None,
    overwrite: bool = False,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> GsakViewImportResult:
    """Convert saved GSAK views into OpenSAK Column Views on disk.

    *names* limits the import to those GSAK views (all of them when None),
    *overwrite* decides what happens to a view file that already exists,
    and *out_dir* defaults to OpenSAK's own Column View folder.
    """
    db_path = Path(db_path)
    if out_dir is None:
        from opensak.config import get_app_data_dir
        out_dir = get_app_data_dir() / "column_views"
    out_dir = Path(out_dir)

    rows = load_gsak_views(db_path)
    if names is not None:
        wanted = set(names)
        rows = [r for r in rows if r[0] in wanted]
    custom = known_custom_fields(db_path)

    out_dir.mkdir(parents=True, exist_ok=True)
    result = GsakViewImportResult(source=db_path)
    used: set[str] = set()
    total = len(rows)

    for index, (name, data) in enumerate(rows, start=1):
        entry = ViewImportEntry(name=name)
        result.entries.append(entry)
        try:
            conversion = convert_view(name, data, custom)
            entry.conversion = conversion
            # Two GSAK names can sanitise to the same file ("A/B", "A_B").
            stem = safe_filename(name)
            if stem in used:
                n = 2
                while f"{stem}_{n}" in used:
                    n += 1
                stem = f"{stem}_{n}"
            used.add(stem)
            path = out_dir / f"{stem}.json"
            entry.path = path
            if path.exists() and not overwrite:
                entry.skipped_existing = True
            else:
                path.write_text(json.dumps(conversion.view_data(), indent=2),
                                encoding="utf-8")
                entry.written = True
        except Exception as exc:     # one bad view must not stop the rest
            entry.error = f"{type(exc).__name__}: {exc}"
        if progress_cb is not None:
            progress_cb(index, total)

    return result

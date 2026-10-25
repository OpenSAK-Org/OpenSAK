"""
src/opensak/macro/declarations.py — what a macro's header declares, shown
to the user before the macro first runs (#1079).

The Permissions and Network keys of the header (metadata.py) say what a
macro does. The Macro manager shows them in a column, but a user who
starts a macro some other way would never see them, so before a macro file
first runs with a declaration, the runtime shows it through OpenSAK's own
dialog (MacroHost.confirm_declarations): Run or Cancel. It is a notice,
never an approval: the macro still asks for every write, folder and web
site, declared or not.

What was shown is stored per macro file in opensak.json under
"macros.declarations_seen" as [{"path": ..., "declared": [...]}]
(MacroInfo.declared). The notice comes again only when the macro declares
something it did not then, e.g. an update that adds "write" or a host; the
same file declaring less, or as much, is run without it.

Not shown for an unsaved macro (typed in OpenSAK's editor), a macro that
declares nothing (no header, or "Permissions: none"), an example shipped
with OpenSAK that was not changed (examples.shipped_hashes()), and a run
without a user interface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from opensak.macro.db_access import macro_key
from opensak.macro.metadata import MacroInfo

STORE_KEY = "macros.declarations_seen"


def _load() -> list[dict]:
    from opensak.settings_store import get_store

    raw = get_store().get(STORE_KEY)
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict) and isinstance(e.get("path"), str)
            and isinstance(e.get("declared"), list)]


def seen(macro: str | Path) -> Optional[tuple[str, ...]]:
    """What the macro file *macro* declared when the user last saw its
    declaration; None if they never did."""
    key = macro_key(macro)
    for entry in _load():
        if macro_key(entry["path"]) == key:
            return tuple(str(d) for d in entry["declared"])
    return None


def added(before: Optional[tuple[str, ...]], info: MacroInfo) -> tuple[str, ...]:
    """What *info* declares that the user has not seen (*before*: as
    returned by seen()); empty if there is nothing to show."""
    known = set(before or ())
    return tuple(d for d in info.declared if d not in known)


def mark_seen(macro: str | Path, info: MacroInfo) -> None:
    """Remember that the user saw what the macro file *macro* declares."""
    from opensak.settings_store import get_store

    key = macro_key(macro)
    entries = [e for e in _load() if macro_key(e["path"]) != key]
    entries.append({"path": str(Path(macro).resolve()), "declared": list(info.declared)})
    get_store().set(STORE_KEY, entries)

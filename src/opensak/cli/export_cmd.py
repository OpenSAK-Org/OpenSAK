"""
src/opensak/cli/export_cmd.py — `opensak export gpx …` (#1203).

Writes the caches of a saved filter with the same code as File → Export in
OpenSAK (opensak.export.file_export), on a database found, locked and opened
as every headless command does it (opensak.cli.common). With no caches to
export no file is written, and an older file stays as it is.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TextIO

from opensak.cli.common import Fail, emit, opened_database, resolve_database, run_headless
from opensak.utils.run_macro import EXIT_OK, EXIT_USAGE


def run_export(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    """`opensak export gpx …`; returns the exit code."""
    return run_headless(args, out, err, f"export {args.kind}", lambda: _run(args, out, err))


def _load_filter(name: str):
    """The saved filter *name* (exact, else ignoring case). Raises
    Fail(EXIT_USAGE) when there is none."""
    from opensak.filters.engine import FilterProfile

    profiles = []
    for path in FilterProfile.list_profiles():
        try:
            profiles.append(FilterProfile.load(path))
        except Exception:
            continue
    for matches in (lambda p: p.name == name, lambda p: p.name.casefold() == name.casefold()):
        found = next((p for p in profiles if matches(p)), None)
        if found is not None:
            return found
    names = ", ".join(repr(p.name) for p in profiles) or "none"
    raise Fail(EXIT_USAGE, f"no saved filter {name!r} — saved filters: {names}")


def _run(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    from opensak.db.database import get_session
    from opensak.db.manager import get_db_manager
    from opensak.export.file_export import select_for_export, write_export_file
    from opensak.filters.engine import apply_filters_auto

    # Check the options before anything is locked or opened.
    profile = _load_filter(args.filter)
    target = Path(args.out).expanduser()
    db = resolve_database(args.db)
    manager = get_db_manager()
    with opened_database(db, args.wait, err, quiet=args.quiet):
        manager.activate_for_run(db)
        try:
            with get_session() as session:
                caches = select_for_export(
                    apply_filters_auto(session, profile.filterset, profile.sort))
            count = 0
            if caches:
                target.parent.mkdir(parents=True, exist_ok=True)
                count = write_export_file(caches, target, "gpx")
        finally:
            manager.end_run()

    lines = [f"Exported {count} caches of {db.name}, filter {profile.name}, to {target}"
             if count else f"No caches to export: {db.name}, filter {profile.name}"]
    emit(args, out, lines, {
        "command": "export gpx",
        "ok": True,
        "database": db.name,
        "database_path": str(db.path),
        "filter": profile.name,
        "count": count,
        "file": str(target) if count else None,
    })
    return EXIT_OK

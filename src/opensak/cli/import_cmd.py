"""
src/opensak/cli/import_cmd.py — `opensak import gpx|gsak …` (#937).

Runs the same import services as the GUI (gpx_import_service,
gsak_import_service) on one existing database, found, locked and opened as
every headless command does it (opensak.cli.common), then writes the summary
to stdout (text or --json) and the log.
"""

from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path
from typing import Any, Optional, TextIO

from opensak.cli.common import Fail, emit, opened_database, resolve_database, run_headless
from opensak.utils.run_macro import (
    EXIT_MACRO_ERROR as EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
)

logger = logging.getLogger(__name__)


# ── Command ─────────────────────────────────────────────────────────────────

def run_import(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    """`opensak import …`; returns the exit code."""
    return run_headless(args, out, err, f"import {args.kind}",
                        lambda: _run(args, out, err))


def _run(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    # Check the sources before anything is locked or opened.
    files: list[Path] = []
    if args.kind == "gpx":
        from opensak.importer.gpx_import_service import GpxImportError, collect_files
        try:
            files = collect_files([Path(p) for p in args.paths], recursive=args.recursive)
        except GpxImportError as exc:
            raise Fail(EXIT_USAGE, str(exc)) from None
    else:
        source = Path(args.source).expanduser()
        if not source.exists():
            raise Fail(EXIT_USAGE, f"{source} does not exist")

    db = resolve_database(args.db)
    with opened_database(db, args.wait, err, quiet=args.quiet):
        if args.kind == "gpx":
            return _import_gpx(args, files, db, out, err)
        return _import_gsak(args, db, out, err)


class _Progress:
    """One line on stderr per step (file, GSAK database), unless --quiet or
    --json."""

    def __init__(self, err: TextIO, enabled: bool):
        self._err = err
        self._enabled = enabled
        self._last: Optional[str] = None

    def __call__(self, text: str, *_counts: int) -> None:
        if self._enabled and text != self._last:
            self._last = text
            self._err.write(f"{text} …\n")
            self._err.flush()


# ── GPX ─────────────────────────────────────────────────────────────────────

def _import_gpx(args: argparse.Namespace, files: list[Path], db, out: TextIO,
                err: TextIO) -> int:
    from opensak.importer.gpx_import_service import run_gpx_import

    progress = _Progress(err, not (args.quiet or args.json))
    report = run_gpx_import(files, db.path, progress_cb=progress)

    moved: dict[Path, Path] = {}
    move_errors: list[str] = []
    if args.move_imported_to:
        moved, move_errors = _move_imported(report, Path(args.move_imported_to).expanduser())

    lines = report.summary_lines()
    lines[0] = f"GPX import into {db.name} ({db.path})"
    lines += [f"  Moved {len(moved)} files to {args.move_imported_to}"] if moved else []
    lines += [f"  Not moved: {e}" for e in move_errors]
    for e in move_errors:
        logger.warning("headless: %s", e)

    data: dict[str, Any] = {
        "command": "import gpx",
        "ok": report.ok,
        "database": db.name,
        "database_path": str(db.path),
        "created": report.total("created"),
        "updated": report.total("updated"),
        "skipped": report.total("skipped"),
        "waypoints": report.total("waypoints"),
        "errors": report.errors,
        "files": [
            {
                "path": str(f.path),
                **({"error": f.error} if f.error else {}),
                **({
                    "created": f.result.created,
                    "updated": f.result.updated,
                    "skipped": f.result.skipped,
                    "waypoints": f.result.waypoints,
                    "errors": f.result.errors,
                    "warnings": f.result.warnings,
                } if f.result is not None else {}),
                **({"moved_to": str(moved[f.path])} if f.path in moved else {}),
            }
            for f in report.files
        ],
        "locations": None if report.locations is None else {
            "updated": report.locations.updated,
            "errors": report.locations.errors,
        },
        "move_errors": move_errors,
    }
    emit(args, out, lines, data)
    return EXIT_OK if report.ok else EXIT_FAILED


def _move_imported(report, target: Path) -> tuple[dict[Path, Path], list[str]]:
    """Move every file imported without errors (and the waypoints file
    imported with it) into *target*. A name that is taken there gets
    " (2)", " (3)", …. Returns ({file: new path}, error messages)."""
    moved: dict[Path, Path] = {}
    errors: list[str] = []
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return moved, [f"cannot create {target}: {exc}"]
    for f in report.files:
        if not f.ok:
            continue
        for path in (f.path, f.companion):
            if path is None or not path.exists():
                continue
            try:
                dest = _free_path(target / path.name)
                shutil.move(str(path), str(dest))
            except OSError as exc:
                errors.append(f"{path}: {exc}")
                continue
            moved[path] = dest
    return moved, errors


def _free_path(path: Path) -> Path:
    candidate, n = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


# ── GSAK ────────────────────────────────────────────────────────────────────

def _import_gsak(args: argparse.Namespace, db, out: TextIO, err: TextIO) -> int:
    from opensak.importer.gsak_import_service import (
        GsakImportError, GsakImportOptions, run_gsak_import,
    )

    options = GsakImportOptions(
        databases=args.gsak_db,
        target_path=db.path,
        if_exists=args.if_exists,
        create=False,
        filters=args.filters,
        locations=args.locations,
    )
    progress = _Progress(err, not (args.quiet or args.json))
    try:
        report = run_gsak_import(Path(args.source).expanduser(), options,
                                 progress_cb=progress)
    except GsakImportError as exc:
        raise Fail(EXIT_USAGE, str(exc)) from None

    data: dict[str, Any] = {
        "command": "import gsak",
        "ok": report.ok,
        "source": str(report.source),
        "kind": report.kind,
        "database": db.name,
        "database_path": str(db.path),
        "created": report.total("created"),
        "updated": report.total("updated"),
        "skipped": report.total("skipped"),
        "waypoints": report.total("waypoints"),
        "logs": report.total("logs"),
        "errors": report.errors,
        "databases": [
            {
                "gsak_name": d.gsak_name,
                "action": d.action,
                **({"reason": d.reason} if d.reason else {}),
                **({"cleared": d.cleared} if d.cleared else {}),
                **({
                    "created": d.result.created,
                    "updated": d.result.updated,
                    "skipped": d.result.skipped,
                    "waypoints": d.result.waypoints,
                    "logs": d.result.logs,
                    "errors": d.result.errors,
                } if d.result is not None else {}),
            }
            for d in report.databases
        ],
        "notes": report.notes,
    }
    emit(args, out, report.summary_lines(), data)
    return EXIT_OK if report.ok else EXIT_FAILED

"""
src/opensak/cli/import_cmd.py — `opensak import gpx|gsak …` (#937).

Runs the same import services as the GUI (gpx_import_service,
gsak_import_service) on one existing database:

1. start-up without a window (opensak.bootstrap.init_headless);
2. find the database given with --db — never create one;
3. take its lock, so OpenSAK (or another run) isn't writing to it at the
   same time (opensak.db.lock), waiting up to --wait seconds;
4. open it — an older database is migrated, after the usual pre-migration
   backup (#549);
5. import, then write the summary to stdout (text or --json) and the log.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Optional, TextIO

from opensak.utils.run_macro import (
    EXIT_DB_LOCKED,
    EXIT_DB_NOT_FOUND,
    EXIT_MACRO_ERROR as EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
)

logger = logging.getLogger(__name__)


class _Fail(Exception):
    """Stop the command with *code* and *message* on stderr."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


# ── Target database ─────────────────────────────────────────────────────────

def resolve_database(spec: str):
    """The DatabaseInfo for --db: a name in the database list
    (case-insensitive), else the path of an OpenSAK database file. Raises
    _Fail(EXIT_DB_NOT_FOUND) when there is no such database."""
    from opensak.db.manager import DatabaseInfo, get_db_manager

    manager = get_db_manager()
    wanted = spec.strip()
    for db in manager.databases:
        if db.name.lower() == wanted.lower():
            if not db.exists:
                raise _Fail(EXIT_DB_NOT_FOUND,
                            f"the file of database {db.name!r} is missing: {db.path}")
            return db

    path = Path(wanted).expanduser()
    try:
        path = path.resolve()
    except OSError:
        pass
    for db in manager.databases:
        if _same_file(db.path, path):
            if not db.exists:
                raise _Fail(EXIT_DB_NOT_FOUND, f"database file not found: {path}")
            return db
    if path.is_file():
        if not manager.is_valid_database_file(path):
            raise _Fail(EXIT_DB_NOT_FOUND, f"not an OpenSAK database: {path}")
        return DatabaseInfo(path.stem, path)

    names = ", ".join(repr(db.name) for db in manager.databases) or "none"
    raise _Fail(EXIT_DB_NOT_FOUND,
                f"no database {spec!r} — give a name from the database list "
                f"({names}) or the path of a .db file")


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


# ── Command ─────────────────────────────────────────────────────────────────

def run_import(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    """`opensak import …`; returns the exit code."""
    from opensak import bootstrap

    # stdout carries the summary only (scripts parse --json): anything else
    # that print()s along the way (e.g. the settings migration) goes to
    # stderr, or nowhere with --quiet.
    with contextlib.ExitStack() as stack:
        stray = stack.enter_context(open(os.devnull, "w")) if args.quiet else err
        stack.enter_context(contextlib.redirect_stdout(stray))
        return _run_logged(args, out, err, bootstrap.init_headless())


def _run_logged(args: argparse.Namespace, out: TextIO, err: TextIO, log_path: Path) -> int:
    command = f"import {args.kind}"
    logger.info("headless: %s %s", command, vars(args))
    try:
        code = _run(args, command, out, err)
    except _Fail as exc:
        err.write(f"opensak: {exc}\n")
        logger.error("headless: %s failed: %s", command, exc)
        code = exc.code
    except Exception as exc:
        logger.exception("headless: %s failed", command)
        err.write(f"opensak: {command} failed: {exc}\n"
                  f"opensak: details are in the log file {log_path}\n")
        code = EXIT_FAILED
    logger.info("headless: %s finished, exit code %d", command, code)
    return code


def _run(args: argparse.Namespace, command: str, out: TextIO, err: TextIO) -> int:
    from opensak.db.lock import DatabaseLock

    # Check the sources before anything is locked or opened.
    files: list[Path] = []
    if args.kind == "gpx":
        from opensak.importer.gpx_import_service import GpxImportError, collect_files
        try:
            files = collect_files([Path(p) for p in args.paths], recursive=args.recursive)
        except GpxImportError as exc:
            raise _Fail(EXIT_USAGE, str(exc)) from None
    else:
        source = Path(args.source).expanduser()
        if not source.exists():
            raise _Fail(EXIT_USAGE, f"{source} does not exist")

    db = resolve_database(args.db)
    lock = DatabaseLock(db.path)
    try:
        acquired = lock.acquire(args.wait)
    except OSError as exc:
        raise _Fail(EXIT_FAILED, str(exc)) from None
    if not acquired:
        holder = lock.holder()
        raise _Fail(EXIT_DB_LOCKED,
                    f"database {db.name!r} is in use"
                    + (f" by {holder}" if holder else "")
                    + " — close it in OpenSAK, or use --wait SECONDS")
    try:
        _open(db, err, quiet=args.quiet)
        if args.kind == "gpx":
            return _import_gpx(args, files, db, out, err)
        return _import_gsak(args, db, out, err)
    finally:
        from opensak.db.database import dispose_engine
        dispose_engine(db.path)
        lock.release()


def _open(db, err: TextIO, quiet: bool) -> None:
    """Open *db* as this process's database (migrating it, with a backup
    first, if it comes from an older OpenSAK)."""
    from opensak.backup.premigration import take_notices
    from opensak.db.database import init_db

    try:
        init_db(db_path=db.path)
    except Exception as exc:
        logger.exception("headless: could not open %s", db.path)
        message = f"cannot open database {db.name!r}: {exc}"
        for notice in take_notices():
            message += f"\nopensak: it was backed up before the update: {notice.backup_path}"
        raise _Fail(EXIT_FAILED, message) from None
    for notice in take_notices():
        line = (f"database {notice.db_path.name} was updated to the current version; "
                f"a backup of it is in {notice.backup_path}")
        logger.info("headless: %s", line)
        if not quiet:
            err.write(f"opensak: {line}\n")


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


def _emit(args: argparse.Namespace, out: TextIO, lines: list[str], data: dict[str, Any]) -> None:
    if args.json:
        out.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    elif not args.quiet:
        out.write("\n".join(lines) + "\n")
    out.flush()


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
    _emit(args, out, lines, data)
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
        raise _Fail(EXIT_USAGE, str(exc)) from None

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
    _emit(args, out, report.summary_lines(), data)
    return EXIT_OK if report.ok else EXIT_FAILED

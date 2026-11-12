"""
src/opensak/cli/db_cmd.py — `opensak db list|backup|check …` (#1203).

None of these opens a database as the active one or migrates it: a check
shouldn't change what it checks, and a backup keeps the file as it is. So they
take no lock either. A backup is a consistent snapshot and a check only reads,
also while OpenSAK has the database open.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any, Optional, TextIO

from opensak.cli.common import Fail, emit, resolve_database, run_headless
from opensak.utils.run_macro import EXIT_MACRO_ERROR as EXIT_FAILED, EXIT_OK


def run_db(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    """`opensak db …`; returns the exit code."""
    action = {"list": _list, "backup": _backup, "check": _check}[args.kind]
    return run_headless(args, out, err, f"db {args.kind}", lambda: action(args, out, err))


# ── list ────────────────────────────────────────────────────────────────────

def _list(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    from opensak.db.manager import get_db_manager

    manager = get_db_manager()
    rows = [{
        "name": db.name,
        "path": str(db.path),
        "active": db.path == manager.active_path,
        "exists": db.exists,
        "size_mb": round(db.size_mb, 2) if db.exists else None,
    } for db in manager.databases]
    lines = [
        f"{'*' if row['active'] else ' '} {row['name']}  {row['path']}  "
        + (f"{row['size_mb']} MB" if row["exists"] else "file missing")
        for row in rows
    ] or ["No databases"]
    emit(args, out, lines, {"command": "db list", "ok": True, "databases": rows})
    return EXIT_OK


# ── backup ──────────────────────────────────────────────────────────────────

def _backup(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    from opensak.backup.backupset import KIND_MANUAL, BackupError, write_backup_set

    db = resolve_database(args.name)
    folder: Optional[Path] = Path(args.out).expanduser() if args.out else None
    last: list[str] = []

    def progress(name: str, _db_fraction: float, _overall: float) -> None:
        if name and [name] != last and not (args.quiet or args.json):
            last[:] = [name]
            err.write(f"{name} …\n")
            err.flush()

    try:
        result = write_backup_set([db], KIND_MANUAL, progress, folder=folder)
    except BackupError as exc:
        raise Fail(EXIT_FAILED, f"backup of {db.name!r} failed: {exc}") from None
    # A file that could not be copied never fails a backup, as in OpenSAK.
    left_out = [*result.skipped_companions, *result.skipped_macro_databases,
                *result.skipped_macro_files]
    lines = [f"Backed up {db.name} to {result.backup_set.path}"]
    lines += [f"  Left out: {name}" for name in left_out]
    emit(args, out, lines, {
        "command": "db backup",
        "ok": True,
        "database": db.name,
        "database_path": str(db.path),
        "backup": str(result.backup_set.path),
        "left_out": left_out,
    })
    return EXIT_OK


# ── check ───────────────────────────────────────────────────────────────────

def _check_one(path: Path) -> dict[str, Any]:
    from opensak.db import maintenance

    try:
        report = maintenance.check_file(path, thorough=True)
    except sqlite3.Error as exc:
        return {"path": str(path), "ok": False, "problems": [str(exc)],
                "foreign_key_problems": 0}
    return {"path": str(path), "ok": report.ok, "problems": list(report.problems),
            "foreign_key_problems": report.foreign_key_problems}


def _check(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    from opensak.db.companion import companion_path

    db = resolve_database(args.name)
    companion = companion_path(db.path)
    files = [_check_one(db.path), *([_check_one(companion)] if companion.exists() else [])]
    ok = all(f["ok"] for f in files)
    lines = [f"Check of {db.name}: {'ok' if ok else 'PROBLEMS FOUND'}"]
    for f in files:
        lines.append(f"  {f['path']}: {'ok' if f['ok'] else 'problems'}")
        lines += [f"    {problem}" for problem in f["problems"]]
        if f["foreign_key_problems"]:
            lines.append(f"    {f['foreign_key_problems']} foreign key problems")
    emit(args, out, lines, {
        "command": "db check",
        "ok": ok,
        "database": db.name,
        "database_path": str(db.path),
        "files": files,
    })
    return EXIT_OK if ok else EXIT_FAILED

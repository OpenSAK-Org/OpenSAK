"""
src/opensak/cli/common.py — what the headless commands share (#937, #1203):

1. start-up without a window (opensak.bootstrap.init_headless) and logging
   of the run (run_headless);
2. finding the database given on the command line — never creating one
   (resolve_database);
3. taking its lock, so OpenSAK (or another run) isn't writing to it at the
   same time (opensak.db.lock), waiting up to --wait seconds (lock_database);
4. opening it — an older database is migrated, after the usual pre-migration
   backup (#549) (open_database, or opened_database for steps 3 and 4);
5. writing the summary to stdout, as text or --json (emit).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Iterator, TextIO

from opensak.utils.run_macro import (
    EXIT_DB_LOCKED,
    EXIT_DB_NOT_FOUND,
    EXIT_MACRO_ERROR as EXIT_FAILED,
)

logger = logging.getLogger(__name__)


class Fail(Exception):
    """Stop the command with *code* and *message* on stderr."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


# ── Running a command ───────────────────────────────────────────────────────

def run_headless(args: argparse.Namespace, out: TextIO, err: TextIO, command: str,
                 body: Callable[[], int]) -> int:
    """Start up without a window and run *body*; returns its exit code. A
    Fail or an unexpected error goes to stderr and the log."""
    from opensak import bootstrap

    # stdout carries the summary only (scripts parse --json): anything else
    # that print()s along the way (e.g. the settings migration) goes to
    # stderr, or nowhere with --quiet.
    with contextlib.ExitStack() as stack:
        stray = stack.enter_context(open(os.devnull, "w")) if args.quiet else err
        stack.enter_context(contextlib.redirect_stdout(stray))
        return _run_logged(args, err, command, body, bootstrap.init_headless())


def _run_logged(args: argparse.Namespace, err: TextIO, command: str,
                body: Callable[[], int], log_path: Path) -> int:
    logger.info("headless: %s %s", command, vars(args))
    try:
        code = body()
    except Fail as exc:
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


def emit(args: argparse.Namespace, out: TextIO, lines: list[str], data: dict[str, Any]) -> None:
    if args.json:
        out.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    elif not args.quiet:
        out.write("\n".join(lines) + "\n")
    out.flush()


# ── Target database ─────────────────────────────────────────────────────────

def resolve_database(spec: str):
    """The DatabaseInfo for --db: a name in the database list
    (case-insensitive), else the path of an OpenSAK database file. Raises
    Fail(EXIT_DB_NOT_FOUND) when there is no such database."""
    from opensak.db.manager import DatabaseInfo, get_db_manager

    manager = get_db_manager()
    wanted = spec.strip()
    for db in manager.databases:
        if db.name.lower() == wanted.lower():
            if not db.exists:
                raise Fail(EXIT_DB_NOT_FOUND,
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
                raise Fail(EXIT_DB_NOT_FOUND, f"database file not found: {path}")
            return db
    if path.is_file():
        if not manager.is_valid_database_file(path):
            raise Fail(EXIT_DB_NOT_FOUND, f"not an OpenSAK database: {path}")
        return DatabaseInfo(path.stem, path)

    names = ", ".join(repr(db.name) for db in manager.databases) or "none"
    raise Fail(EXIT_DB_NOT_FOUND,
               f"no database {spec!r} — give a name from the database list "
               f"({names}) or the path of a .db file")


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


def lock_database(db, wait: float):
    """The acquired DatabaseLock of *db*, waiting up to *wait* seconds. Raises
    Fail(EXIT_DB_LOCKED) when the database stays in use."""
    from opensak.db.lock import DatabaseLock

    lock = DatabaseLock(db.path)
    try:
        acquired = lock.acquire(wait)
    except OSError as exc:
        raise Fail(EXIT_FAILED, str(exc)) from None
    if not acquired:
        holder = lock.holder()
        raise Fail(EXIT_DB_LOCKED,
                   f"database {db.name!r} is in use"
                   + (f" by {holder}" if holder else "")
                   + " — close it in OpenSAK, or use --wait SECONDS")
    return lock


def open_database(db, err: TextIO, quiet: bool) -> None:
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
        raise Fail(EXIT_FAILED, message) from None
    for notice in take_notices():
        line = (f"database {notice.db_path.name} was updated to the current version; "
                f"a backup of it is in {notice.backup_path}")
        logger.info("headless: %s", line)
        if not quiet:
            err.write(f"opensak: {line}\n")


@contextlib.contextmanager
def opened_database(db, wait: float, err: TextIO, quiet: bool) -> Iterator[None]:
    """Hold the lock of *db* with *db* open as this process's database."""
    from opensak.db.database import dispose_engine

    lock = lock_database(db, wait)
    try:
        open_database(db, err, quiet)
        yield
    finally:
        dispose_engine(db.path)
        lock.release()

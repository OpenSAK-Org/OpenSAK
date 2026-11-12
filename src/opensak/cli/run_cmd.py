"""
src/opensak/cli/run_cmd.py — `opensak run <macro> …` (#1203).

Runs a Lua macro with HeadlessHost (opensak.macro.headless) on one database,
found, locked and opened as every headless command does it
(opensak.cli.common). The database is active for this run only: the settings
keep the active database, so the GUI opens the same one afterwards. A database
the macro uses besides that one (switch_database, attach, copy_caches, …) is
locked the same way, until the run ends.

print() and message() go to stdout, or with --json into the summary; progress
goes to stderr. Both also go to OpenSAK's log and to --log-file. The exit code
is the macro's own (opensak.exit), or one of opensak.utils.run_macro.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Callable, Optional, TextIO, cast

from opensak.cli.common import (
    Fail,
    emit,
    lock_database,
    open_database,
    resolve_database,
    run_headless,
)
from opensak.utils.run_macro import (
    EXIT_DB_NOT_FOUND,
    EXIT_MACRO_ERROR as EXIT_FAILED,
    EXIT_NEEDS_UI,
    EXIT_USAGE,
    format_error,
)

logger = logging.getLogger(__name__)


def run_macro(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    """`opensak run …`; returns the exit code."""
    return run_headless(args, out, err, "run", lambda: _run(args, out, err))


# ── The macro ───────────────────────────────────────────────────────────────

def find_macro(spec: str) -> Path:
    """The macro file for *spec*: a path (the .lua may be left out), else the
    file name or Name of a macro in the macros folder. Raises Fail(EXIT_USAGE)
    when there is none, or several."""
    from opensak.macro.metadata import discover_macros
    from opensak.macro.permissions import macros_dir

    path = Path(spec).expanduser()
    for candidate in (path, Path(f"{path}.lua")):
        if candidate.is_file():
            return candidate.resolve()
    wanted = spec.strip().casefold().removesuffix(".lua")
    found = [m.path for m in discover_macros(macros_dir())
             if wanted in (m.path.stem.casefold(), m.name.casefold())]
    if len(found) > 1:
        names = ", ".join(str(p) for p in found)
        raise Fail(EXIT_USAGE, f"several macros are called {spec!r}: {names} — give the path")
    if not found:
        raise Fail(EXIT_USAGE, f"macro not found: {spec}")
    return found[0].resolve()


def _read_macro(path: Path) -> str:
    from opensak.macro.db_access import read_macro_source
    from opensak.macro.metadata import parse_header
    from opensak.macro.runtime import MAX_MACRO_BYTES

    try:
        if path.stat().st_size > MAX_MACRO_BYTES:
            raise Fail(EXIT_USAGE, f"{path.name} is larger than "
                                   f"{MAX_MACRO_BYTES // (1024 * 1024)} MB")
        source = read_macro_source(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise Fail(EXIT_USAGE, f"cannot read {path}: {exc}") from None
    if not parse_header(source, path).runs_headless:
        raise Fail(EXIT_NEEDS_UI, f"{path.name} says it can't run without a window "
                                  "(Headless: no) — run it in OpenSAK")
    return source


def _folder_permissions(folders: list[str]) -> Optional[list[Any]]:
    """The folder list of this run: the saved one plus --allow-folder, for
    reading and writing. None without --allow-folder: the saved list as is.
    The list never opens protected data or autostart locations
    (macro.permissions.check_access)."""
    from opensak.macro.permissions import is_root_folder, load_permissions, resolve_path, with_grant

    if not folders:
        return None
    permissions = load_permissions()
    for folder in folders:
        if is_root_folder(folder):
            raise Fail(EXIT_USAGE, f"--allow-folder can't open a whole drive: {folder}")
        try:
            resolved = resolve_path(folder)
        except (OSError, RuntimeError) as exc:
            raise Fail(EXIT_USAGE, f"--allow-folder {folder}: {exc}") from None
        for write in (False, True):
            permissions = with_grant(permissions, resolved, write)
    return permissions


# ── Output ──────────────────────────────────────────────────────────────────

class _Output:
    """Where a run's lines go."""

    def __init__(self, args: argparse.Namespace, out: TextIO, err: TextIO,
                 log_file: Optional[TextIO]):
        self._args = args
        self._out = out
        self._err = err
        self._log_file = log_file
        self.lines: list[str] = []

    def _to_logs(self, text: str) -> None:
        logger.info("macro: %s", text)
        if self._log_file is not None:
            self._log_file.write(text + "\n")
            self._log_file.flush()

    def line(self, text: str) -> None:
        """print() and message()."""
        self._to_logs(text)
        if self._args.json:
            self.lines.append(text)
        elif not self._args.quiet:
            self._out.write(text + "\n")
            self._out.flush()

    def status(self, text: str) -> None:
        """Progress."""
        self._to_logs(text)
        if not (self._args.json or self._args.quiet):
            self._err.write(f"{text} …\n")
            self._err.flush()

    def error(self, text: str) -> None:
        if self._log_file is not None:
            self._log_file.write(f"opensak: {text}\n")
            self._log_file.flush()
        self._err.write(f"opensak: {text}\n")
        self._err.flush()


# ── Databases of the run ────────────────────────────────────────────────────

class _Databases:
    """The locks this run holds, until close(). A macro may switch back to a
    database, so none is released earlier."""

    def __init__(self, wait: float, err: TextIO, quiet: bool):
        self._wait = wait
        self._err = err
        self._quiet = quiet
        self._locks: dict[Path, Any] = {}
        self._opened: list[Path] = []

    def lock(self, db) -> None:
        if db.path not in self._locks:
            self._locks[db.path] = lock_database(db, self._wait)

    def activate(self, db) -> None:
        from opensak.db.manager import get_db_manager

        self.lock(db)
        open_database(db, self._err, self._quiet)
        self._opened.append(db.path)
        get_db_manager().activate_for_run(db)

    def switch(self, name: str) -> None:
        from opensak.db.manager import get_db_manager

        db = next((d for d in get_db_manager().databases if d.name == name), None)
        if db is None:
            raise ValueError(f"no database named {name!r}")
        self.activate(db)

    def close(self) -> None:
        from opensak.db.database import dispose_engine
        from opensak.db.manager import get_db_manager

        get_db_manager().end_run()
        for path in self._opened:
            dispose_engine(path)
        for lock in self._locks.values():
            lock.release()
        self._locks = {}


def _active_database():
    from opensak.db.manager import get_db_manager

    db = get_db_manager().active
    if db is None:
        raise Fail(EXIT_DB_NOT_FOUND, "no active database — give one with --db")
    if not db.exists:
        raise Fail(EXIT_DB_NOT_FOUND, f"the file of database {db.name!r} is missing: {db.path}")
    return db


# ── Command ─────────────────────────────────────────────────────────────────

def _run(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    from opensak.utils import flags

    if not flags.lua_macros:
        raise Fail(EXIT_USAGE, "Lua macros are turned off in this build")
    # Check the macro and the options before anything is locked or opened.
    path = find_macro(args.macro)
    source = _read_macro(path)
    permissions = _folder_permissions(args.allow_folder)
    db = resolve_database(args.db) if args.db else _active_database()

    log_file: Optional[TextIO] = None
    if args.log_file:
        try:
            log_file = open(Path(args.log_file).expanduser(), "a", encoding="utf-8")
        except OSError as exc:
            raise Fail(EXIT_USAGE, f"--log-file: {exc}") from None
    try:
        output = _Output(args, out, err, log_file)
        databases = _Databases(args.wait, err, args.quiet)
        try:
            databases.activate(db)
            return _run_on_database(args, out, output, databases, path, source, permissions)
        finally:
            databases.close()
    finally:
        if log_file is not None:
            log_file.close()


def _run_on_database(args: argparse.Namespace, out: TextIO, output: _Output,
                     databases: _Databases, path: Path, source: str,
                     permissions: Optional[list[Any]]) -> int:
    from opensak.db.manager import get_db_manager
    from opensak.macro import MacroError, MacroHost, MacroRuntime
    from opensak.macro.headless import HeadlessHost

    failure: list[str] = []

    def stop(code: int, message: str) -> None:
        failure.append(message)
        runtime.stop_run(code, message)

    def stopping(use: Callable[[Any], None]) -> Callable[[Any], None]:
        """A locked database ends the run with its exit code, like --db."""
        def call(db: Any) -> None:
            try:
                use(db)
            except Fail as exc:
                stop(exc.code, str(exc))
        return call

    host = HeadlessHost(
        answers=args.macro_args,
        yes=args.yes,
        allow_write=args.allow_write,
        output=output.line,
        status=output.status,
        switch=stopping(databases.switch),
        lock=stopping(databases.lock),
    )
    # cast: the optional MacroHost functions are left out on purpose
    runtime = MacroRuntime(cast(MacroHost, host), output=output.line,
                           folder_permissions=permissions, headless=True,
                           macro_args=args.macro_args)
    host.set_stop(stop)
    try:
        code = runtime.run(source, chunk_name=path.name, base_dir=path.parent, macro_path=path)
    except MacroError as exc:
        failure.append(format_error(str(exc), path.name, path))
        code = EXIT_FAILED
    for message in failure:
        logger.error("headless: run: %s", message)
        output.error(message)
    if args.json:
        active = get_db_manager().active
        emit(args, out, [], {
            "command": "run",
            "ok": code == 0,
            "exit_code": code,
            "macro": str(path),
            "database": active.name if active else "",
            "database_path": str(active.path) if active else "",
            "output": output.lines,
            "errors": failure,
        })
    return code

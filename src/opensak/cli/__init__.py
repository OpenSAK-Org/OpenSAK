"""
src/opensak/cli — headless commands: `opensak <command> …` (#937).

A bare `opensak` (or one with a file argument) starts the GUI as before. A
command runs without a window and exits, so it can be scheduled with the
OS's own tools (Task Scheduler, cron, systemd timers, launchd):

    opensak import gpx  <file|folder>... --db "My Caches" [--recursive]
                        [--move-imported-to DIR]
    opensak import gsak <zip|folder|sqlite.db3> --db "My Caches"
                        [--if-exists merge|overwrite]
    opensak run <macro.lua> [--db "My Caches"] [--arg key=value ...] [--yes]
                        [--allow-write] [--allow-folder DIR ...]
                        [--log-file FILE]
    opensak export gpx  --db "My Caches" --filter "Saved filter" --out FILE
    opensak db list
    opensak db backup <name> [--out DIR]
    opensak db check <name>

The choice is made in entry() (run.py and the `opensak` console script),
before anything imports QtWidgets or opensak.gui: a headless run needs no
display, never initialises QtWebEngine and shows no Dock icon on macOS. It
uses the same settings, database list, language and log file as the GUI
(opensak.bootstrap).

On Windows, opensak.exe is a windowed program: a shell doesn't wait for it,
and its exit code is lost. The build therefore also has opensak-cli.exe,
the same program as a console program (opensak.spec); the MSIX package
puts it on the PATH as `opensak` / `opensak-beta` (App Execution Alias).
A shell waits for opensak-cli.exe, so when it is asked for the GUI (no
arguments, or a file argument) it starts opensak.exe next to it with the same
arguments and exits at once (#1180): a bare `opensak` gives the prompt back.
Commands, --help and --run-macro still run in the console program, so the
shell gets their output and exit code.

Exit codes are those of `opensak --run-macro` (opensak.utils.run_macro):
0 success, 1 the import failed or had errors, 2 bad arguments or a missing
source, 5 database not found, 6 database in use (open in OpenSAK, or
another run), 7 the macro needs a dialog, 130 interrupted.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

# First arguments that select a headless command.
COMMANDS = ("import", "run", "export", "db")

# The Windows console program and the windowed program next to it (opensak.spec).
CLI_EXE = "opensak-cli.exe"
GUI_EXE = "opensak.exe"


def is_cli_command(argv: list[str]) -> bool:
    """True if *argv* (without the program name) asks for a headless
    command, or for the command-line help."""
    return bool(argv) and argv[0] in (*COMMANDS, "-h", "--help")


def entry() -> None:
    """The program's entry point: a headless command, else the GUI."""
    argv = sys.argv[1:]
    if is_cli_command(argv):
        sys.exit(main(argv))
    if hand_off_to_gui_exe(argv):
        sys.exit(0)
    from opensak.app import main as gui_main
    gui_main()


def _is_run_macro(argv: list[str]) -> bool:
    return any(a == "--run-macro" or a.startswith("--run-macro=") for a in argv)


def hand_off_to_gui_exe(argv: list[str]) -> bool:
    """In the frozen opensak-cli.exe, start the GUI *argv* asks for in the
    windowed opensak.exe next to it, so the shell doesn't wait for the GUI
    (#1180). True if it was started; False to run it here (not opensak-cli.exe,
    --run-macro, or opensak.exe is missing)."""
    if not getattr(sys, "frozen", False) or _is_run_macro(argv):
        return False
    exe = Path(sys.executable)
    if exe.name.lower() != CLI_EXE:
        return False
    gui = exe.with_name(GUI_EXE)
    if not gui.is_file():
        return False
    # No console and its own Ctrl+C group: closing or interrupting the shell
    # leaves the GUI running. No inherited stdio, so a pipe doesn't stay open.
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    try:
        subprocess.Popen(
            [str(gui), *argv], creationflags=flags, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    db_help = ("its name in the database list, or the path of its .db file. "
               "It must exist — a database is never created.")

    output = argparse.ArgumentParser(add_help=False)
    out = output.add_mutually_exclusive_group()
    out.add_argument("--json", action="store_true",
                     help="write the summary to stdout as JSON")
    out.add_argument("--quiet", action="store_true",
                     help="write nothing to stdout; errors still go to stderr "
                          "and everything to the log file")
    # Feature flags, read by opensak.utils.flags from sys.argv itself.
    output.add_argument("--feature", action="append", metavar="NAME=VALUE",
                        help=argparse.SUPPRESS)

    wait = argparse.ArgumentParser(add_help=False)
    wait.add_argument(
        "--wait", type=float, default=0, metavar="SECONDS",
        help="if the database is open in OpenSAK or used by another run, wait "
             "up to this long for it to be released (default: fail at once)")

    common = argparse.ArgumentParser(add_help=False, parents=[wait, output])
    common.add_argument(
        "--db", required=True, metavar="NAME|PATH",
        help=f"the OpenSAK database to import into: {db_help}")

    parser = argparse.ArgumentParser(
        prog="opensak",
        description="OpenSAK without a window, for scheduled and unattended "
                    "runs. Without a command OpenSAK starts as usual.",
        epilog="The summary also goes to OpenSAK's log file. Exit codes: 0 ok, "
               "1 failed or finished with errors, 2 bad arguments, 5 database "
               "not found, 6 database in use, 7 the macro needs a dialog, "
               "130 interrupted.",
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    imp = commands.add_parser("import", help="import GPX / PQ files or a GSAK database",
                              description="Import into an existing OpenSAK database.")
    sources = imp.add_subparsers(dest="kind", metavar="SOURCE", required=True)

    gpx = sources.add_parser(
        "gpx", parents=[common], help="import .gpx and Pocket Query .zip files",
        description="Import .gpx files (with their waypoints file) and Pocket "
                    "Query .zip files, then fill in missing country / state / "
                    "county and update the distances, as an import in OpenSAK "
                    "does. Caches already in the database are updated.")
    gpx.add_argument("paths", nargs="+", metavar="FILE|FOLDER",
                     help="a .gpx or .zip file, or a folder: every .gpx and .zip "
                          "directly in it")
    gpx.add_argument("--recursive", action="store_true",
                     help="also import the files in subfolders of a folder")
    gpx.add_argument("--move-imported-to", metavar="FOLDER",
                     help="move each file that was imported without errors into "
                          "this folder (created if needed), so the next run "
                          "doesn't import it again")

    gsak = sources.add_parser(
        "gsak", parents=[common], help="import a GSAK database",
        description="Import GSAK caches, as File → Import from GSAK does: new "
                    "caches are added, existing ones updated, locked caches "
                    "left alone. All GSAK databases of the source go into the "
                    "database given with --db.")
    gsak.add_argument("source", metavar="ZIP|FOLDER|DB3",
                      help="a GSAK backup .zip, a GSAK folder (install, data "
                           "folder or one database's folder) or a sqlite.db3")
    gsak.add_argument("--gsak-db", action="append", metavar="NAME",
                      help="only this GSAK database of a backup or folder "
                           "(repeat for more; default: all)")
    gsak.add_argument("--if-exists", choices=("merge", "overwrite"), default="merge",
                      help="merge (default): add and update caches; overwrite: "
                           "empty the OpenSAK database first")
    gsak.add_argument("--filters", choices=("skip", "overwrite"),
                      help="also import GSAK's saved filters; skip or overwrite "
                           "those with the same name (default: not imported)")
    gsak.add_argument("--locations", choices=("skip", "overwrite"),
                      help="also import GSAK's user locations; skip or overwrite "
                           "those with the same name (default: not imported)")

    run = commands.add_parser(
        "run", parents=[wait, output], help="run a Lua macro without a window",
        description="Run a Lua macro without a window. A macro that needs a "
                    "dialog nobody can answer stops with exit code 7.")
    run.add_argument("macro", metavar="MACRO", help="the macro's .lua file, or the file name or Name of a macro in "
                          "the macros folder")
    run.add_argument("--db", metavar="NAME|PATH",
                     help=f"the OpenSAK database to run it on: {db_help} "
                          "(default: the active database)")
    run.add_argument("--arg", action="append", default=[], dest="macro_args",
                     metavar="KEY=VALUE",
                     help="a value for opensak.args and for the macro's dialogs "
                          "(repeat for more)")
    run.add_argument("--yes", action="store_true",
                     help="answer the dialogs --arg doesn't answer with their default")
    run.add_argument("--allow-write", action="store_true",
                     help="let the macro change databases although it has no "
                          "\"Always\" approval for its current code")
    run.add_argument("--allow-folder", action="append", default=[], metavar="DIR",
                     help="let the macro read and write this folder during this "
                          "run (repeat for more)")
    run.add_argument("--log-file", metavar="FILE",
                     help="also write the macro's output to this file")

    export = commands.add_parser("export", help="export caches to a file",
                                 description="Export caches of a database.")
    formats = export.add_subparsers(dest="kind", metavar="FORMAT", required=True)
    export_gpx = formats.add_parser(
        "gpx", parents=[wait, output], help="export a saved filter's caches as GPX",
        description="Export the caches of a saved filter as GPX, with the same "
                    "code as the export in OpenSAK.")
    export_gpx.add_argument("--db", required=True, metavar="NAME|PATH",
                            help=f"the OpenSAK database to export from: {db_help}")
    export_gpx.add_argument("--filter", required=True, metavar="NAME",
                            help="the saved filter to export")
    export_gpx.add_argument("--out", required=True, metavar="FILE",
                            help="the GPX file to write")

    database = commands.add_parser("db", help="list, back up or check databases",
                                   description="Work with OpenSAK's databases.")
    actions = database.add_subparsers(dest="kind", metavar="ACTION", required=True)
    actions.add_parser("list", parents=[output], help="list the databases",
                       description="List the databases OpenSAK knows.")
    backup = actions.add_parser(
        "backup", parents=[output], help="back up a database",
        description="Make the same backup set as OpenSAK: the database, its "
                    "companion database, settings and macros.")
    backup.add_argument("name", metavar="NAME|PATH", help=f"the database to back up: {db_help}")
    backup.add_argument("--out", metavar="DIR",
                        help="the folder for the backup (default: the backup folder)")
    check = actions.add_parser(
        "check", parents=[output], help="check a database's integrity",
        description="Run the integrity check on a database. The exit code is "
                    "not 0 if the check fails.")
    check.add_argument("name", metavar="NAME|PATH", help=f"the database to check: {db_help}")
    return parser


def _parse_macro_args(pairs: list[str]) -> Optional[dict[str, str]]:
    """The --arg KEY=VALUE pairs as a dict (a repeated key: the last one
    counts); None if one has no KEY=."""
    result: dict[str, str] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator or not key.strip():
            return None
        result[key.strip()] = value
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse *argv*; raises SystemExit (code 2, or 0 for --help) as argparse
    does."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "wait", 0) < 0:
        parser.error("--wait expects seconds ≥ 0")
    if args.command == "run":
        macro_args = _parse_macro_args(args.macro_args)
        if macro_args is None:
            parser.error("--arg expects KEY=VALUE")
        args.macro_args = macro_args
    return args


def main(argv: list[str]) -> int:
    """Run the headless command in *argv*; returns the exit code."""
    from opensak.utils.run_macro import EXIT_INTERRUPTED, open_stdio

    out, err = open_stdio()
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0
    try:
        if args.command == "export":
            from opensak.cli.export_cmd import run_export
            return run_export(args, out, err)
        if args.command == "db":
            from opensak.cli.db_cmd import run_db
            return run_db(args, out, err)
        if args.command == "run":
            from opensak.cli.run_cmd import run_macro
            return run_macro(args, out, err)
        from opensak.cli.import_cmd import run_import
        return run_import(args, out, err)
    except KeyboardInterrupt:
        err.write("opensak: interrupted\n")
        return EXIT_INTERRUPTED

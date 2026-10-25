"""
src/opensak/cli — headless commands: `opensak <command> …` (#937).

A bare `opensak` (or one with a file argument) starts the GUI as before. A
command runs without a window and exits, so it can be scheduled with the
OS's own tools (Task Scheduler, cron, systemd timers, launchd):

    opensak import gpx  <file|folder>... --db "My Caches" [--recursive]
                        [--move-imported-to DIR]
    opensak import gsak <zip|folder|sqlite.db3> --db "My Caches"
                        [--if-exists merge|overwrite]

The choice is made in entry() (run.py and the `opensak` console script),
before anything imports QtWidgets or opensak.gui: a headless run needs no
display, never initialises QtWebEngine and shows no Dock icon on macOS. It
uses the same settings, database list, language and log file as the GUI
(opensak.bootstrap).

On Windows, opensak.exe is a windowed program: a shell doesn't wait for it,
and its exit code is lost. The build therefore also has opensak-cli.exe,
the same program as a console program (opensak.spec); the MSIX package
puts it on the PATH as `opensak` / `opensak-beta` (App Execution Alias).

Exit codes are those of `opensak --run-macro` (opensak.utils.run_macro):
0 success, 1 the import failed or had errors, 2 bad arguments or a missing
source, 5 database not found, 6 database in use (open in OpenSAK, or
another run), 130 interrupted.
"""

from __future__ import annotations

import argparse
import sys

# First arguments that select a headless command.
COMMANDS = ("import",)


def is_cli_command(argv: list[str]) -> bool:
    """True if *argv* (without the program name) asks for a headless
    command, or for the command-line help."""
    return bool(argv) and argv[0] in (*COMMANDS, "-h", "--help")


def entry() -> None:
    """The program's entry point: a headless command, else the GUI."""
    argv = sys.argv[1:]
    if is_cli_command(argv):
        sys.exit(main(argv))
    from opensak.app import main as gui_main
    gui_main()


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--db", required=True, metavar="NAME|PATH",
        help="the OpenSAK database to import into: its name in the database "
             "list, or the path of its .db file. It must exist — a database "
             "is never created.")
    common.add_argument(
        "--wait", type=float, default=0, metavar="SECONDS",
        help="if the database is open in OpenSAK or used by another run, wait "
             "up to this long for it to be released (default: fail at once)")
    out = common.add_mutually_exclusive_group()
    out.add_argument("--json", action="store_true",
                     help="write the summary to stdout as JSON")
    out.add_argument("--quiet", action="store_true",
                     help="write nothing to stdout; errors still go to stderr "
                          "and everything to the log file")
    # Feature flags, read by opensak.utils.flags from sys.argv itself.
    common.add_argument("--feature", action="append", metavar="NAME=VALUE",
                        help=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(
        prog="opensak",
        description="OpenSAK without a window, for scheduled and unattended "
                    "runs. Without a command OpenSAK starts as usual.",
        epilog="The summary also goes to OpenSAK's log file. Exit codes: 0 ok, "
               "1 failed or finished with errors, 2 bad arguments, 5 database "
               "not found, 6 database in use, 130 interrupted.",
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
    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse *argv*; raises SystemExit (code 2, or 0 for --help) as argparse
    does."""
    args = build_parser().parse_args(argv)
    if args.wait < 0:
        build_parser().error("--wait expects seconds ≥ 0")
    return args


def main(argv: list[str]) -> int:
    """Run the headless command in *argv*; returns the exit code."""
    from opensak.utils.run_macro import EXIT_INTERRUPTED, open_stdio

    out, err = open_stdio()
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0
    from opensak.cli.import_cmd import run_import
    try:
        return run_import(args, out, err)
    except KeyboardInterrupt:
        err.write("opensak: interrupted\n")
        return EXIT_INTERRUPTED

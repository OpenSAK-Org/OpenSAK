"""
tests/unit-tests/test_cli_commands_1203.py — the command line of `opensak run`,
`export gpx` and `db list|backup|check` (#1203): the dispatcher and argument
parsing. No QApplication is needed.
"""

import pytest

from opensak import cli
from opensak.utils.run_macro import EXIT_NEEDS_UI, EXIT_USAGE


@pytest.mark.parametrize("command", ["run", "export", "db"])
def test_is_cli_command(command):
    assert cli.is_cli_command([command, "x"]) is True


def test_exit_code_for_a_macro_needing_a_dialog():
    assert EXIT_NEEDS_UI == 7


def test_parse_run_args():
    args = cli.parse_args([
        "run", "x.lua", "--db", "Nightly", "--arg", "a=1", "--arg", "b=x=y", "--yes",
        "--allow-write", "--allow-folder", "one", "--allow-folder", "two",
        "--wait", "30", "--json", "--log-file", "run.log",
    ])
    assert args.command == "run" and args.macro == "x.lua" and args.db == "Nightly"
    assert args.macro_args == {"a": "1", "b": "x=y"}
    assert args.yes and args.allow_write and args.json
    assert args.allow_folder == ["one", "two"]
    assert args.wait == 30 and args.log_file == "run.log"


def test_parse_run_defaults():
    args = cli.parse_args(["run", "x.lua"])
    assert args.db is None and args.macro_args == {} and args.allow_folder == []
    assert not (args.yes or args.allow_write or args.json or args.quiet)
    assert args.wait == 0 and args.log_file is None


def test_repeated_arg_keeps_the_last_value():
    args = cli.parse_args(["run", "x.lua", "--arg", "a=1", "--arg", "a="])
    assert args.macro_args == {"a": ""}


@pytest.mark.parametrize("argv", [
    ["run"],
    ["run", "x.lua", "--arg", "novalue"],
    ["run", "x.lua", "--arg", "=value"],
    ["run", "x.lua", "--wait", "-1"],
    ["run", "x.lua", "--json", "--quiet"],
    ["export"],
    ["export", "gpx", "--db", "A", "--filter", "F"],
    ["export", "gpx", "--db", "A", "--out", "x.gpx"],
    ["export", "gpx", "--filter", "F", "--out", "x.gpx"],
    ["db"],
    ["db", "backup"],
    ["db", "check"],
    ["db", "list", "--wait", "1"],
])
def test_bad_arguments(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(argv)
    assert exc.value.code == EXIT_USAGE
    capsys.readouterr()


def test_parse_export_gpx_args():
    args = cli.parse_args(["export", "gpx", "--db", "A", "--filter", "F", "--out", "x.gpx",
                           "--wait", "5"])
    assert (args.command, args.kind) == ("export", "gpx")
    assert (args.db, args.filter, args.out, args.wait) == ("A", "F", "x.gpx", 5)


def test_parse_db_args():
    args = cli.parse_args(["db", "list", "--json"])
    assert (args.command, args.kind, args.json) == ("db", "list", True)
    args = cli.parse_args(["db", "backup", "A", "--out", "folder"])
    assert (args.kind, args.name, args.out) == ("backup", "A", "folder")
    args = cli.parse_args(["db", "check", "A", "--quiet"])
    assert (args.kind, args.name, args.quiet) == ("check", "A", True)


@pytest.mark.parametrize("argv", [
    ["run", "--help"], ["export", "gpx", "--help"], ["db", "list", "--help"],
    ["db", "backup", "--help"], ["db", "check", "--help"],
])
def test_every_command_has_help(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(argv)
    assert exc.value.code == 0
    assert "usage: opensak" in capsys.readouterr().out

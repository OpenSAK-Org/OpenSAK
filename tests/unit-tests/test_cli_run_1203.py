"""
tests/unit-tests/test_cli_run_1203.py — whole runs of `opensak run` (#1203).
No QApplication and no display.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from opensak import cli
from opensak.db.database import init_db, session_for
from opensak.db.lock import DatabaseLock
from opensak.db.manager import get_db_manager
from opensak.db.models import Cache
from opensak.macro import MacroHost, MacroRuntime, WriteApproval, db_access
from opensak.macro.db_access import source_hash
from opensak.macro.headless import HeadlessHost
from opensak.settings_store import get_store
from opensak.utils.run_macro import (
    EXIT_DB_LOCKED, EXIT_DB_NOT_FOUND, EXIT_MACRO_ERROR, EXIT_NEEDS_UI, EXIT_OK, EXIT_USAGE,
)

INSERT = 'opensak.insert{ code = "GCRUN1", name = "Run", type = "Traditional Cache", lat = 47.0, lon = 8.0 }\n'


@pytest.fixture
def nightly():
    """A database "Nightly" next to the active Default one."""
    manager = get_db_manager()
    manager.ensure_active_initialised()
    existing = next((d for d in manager.databases if d.name == "Nightly"), None)
    return existing or manager.new_database("Nightly")


def _macro(folder: Path, source: str, name: str = "m.lua") -> Path:
    path = folder / name
    path.write_text(source, encoding="utf-8")
    return path


def _run(capsys, *argv) -> tuple[int, str, str]:
    code = cli.main([str(a) for a in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _codes(db) -> list[str]:
    conn = sqlite3.connect(db.path)
    try:
        return sorted(r[0] for r in conn.execute("SELECT gc_code FROM caches"))
    finally:
        conn.close()


def test_runs_on_the_given_database_and_keeps_the_active_one(capsys, tmp_path, nightly):
    manager = get_db_manager()
    before = manager.active
    stored = get_store().get("databases.active")
    macro = _macro(tmp_path, 'print(opensak.database())\nopensak.exit(3)\n')
    code, out, _ = _run(capsys, "run", macro, "--db", "Nightly")
    assert code == 3
    assert out == "Nightly\n"
    assert manager.active == before
    assert get_store().get("databases.active") == stored


def test_defaults_to_the_active_database(capsys, tmp_path, nightly):
    macro = _macro(tmp_path, 'print(opensak.database())\n')
    code, out, _ = _run(capsys, "run", macro)
    assert (code, out) == (EXIT_OK, f"{get_db_manager().active.name}\n")


def test_lock_is_released_after_the_run(capsys, tmp_path, nightly):
    assert _run(capsys, "run", _macro(tmp_path, ""), "--db", "Nightly")[0] == EXIT_OK
    lock = DatabaseLock(nightly.path)
    assert lock.acquire()
    lock.release()


def test_database_in_use(capsys, tmp_path, nightly):
    lock = DatabaseLock(nightly.path)
    assert lock.acquire()
    try:
        code, _, err = _run(capsys, "run", _macro(tmp_path, 'print("ran")'), "--db", "Nightly")
    finally:
        lock.release()
    assert code == EXIT_DB_LOCKED and "in use" in err


def test_unknown_database(capsys, tmp_path, nightly):
    code, _, _ = _run(capsys, "run", _macro(tmp_path, ""), "--db", "No such")
    assert code == EXIT_DB_NOT_FOUND


def test_macro_not_found(capsys, tmp_path, nightly):
    assert _run(capsys, "run", tmp_path / "none.lua")[0] == EXIT_USAGE


def test_macro_by_name_from_the_macros_folder(capsys, nightly):
    from opensak.macro.permissions import macros_dir

    folder = macros_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = _macro(folder, '-- Name: My nightly job\nprint("ran")\n', "job_1203.lua")
    try:
        for spec in ("job_1203", "job_1203.lua", "my NIGHTLY job"):
            assert _run(capsys, "run", spec)[:2] == (EXIT_OK, "ran\n")
    finally:
        path.unlink()


def test_a_lua_error_names_the_file_and_line(capsys, tmp_path, nightly):
    macro = _macro(tmp_path, '\nerror("boom")\n')
    code, _, err = _run(capsys, "run", macro)
    assert code == EXIT_MACRO_ERROR
    assert f"{macro}:2:" in err and "boom" in err


def test_needing_a_dialog(capsys, tmp_path, nightly):
    macro = _macro(tmp_path, 'opensak.confirm("Sure?")\nprint("went on")\n')
    code, out, err = _run(capsys, "run", macro)
    assert code == EXIT_NEEDS_UI
    assert out == "" and "Sure?" in err and "--yes" in err
    assert _run(capsys, "run", macro, "--yes")[:2] == (EXIT_OK, "went on\n")


def test_headless_no_is_refused_before_it_starts(capsys, tmp_path, nightly):
    macro = _macro(tmp_path, '-- Headless: no\nprint("ran")\n')
    code, out, err = _run(capsys, "run", macro, "--db", "Nightly")
    assert code == EXIT_NEEDS_UI
    assert out == "" and "Headless: no" in err


def test_write_needs_approval_or_allow_write(capsys, tmp_path, nightly):
    source = INSERT.replace("GCRUN1", "GCRUN2")
    macro = _macro(tmp_path, source)
    code, _, err = _run(capsys, "run", macro, "--db", "Nightly")
    assert code == EXIT_NEEDS_UI and "--allow-write" in err
    assert "GCRUN2" not in _codes(nightly)
    assert _run(capsys, "run", macro, "--db", "Nightly", "--allow-write")[0] == EXIT_OK
    assert "GCRUN2" in _codes(nightly)


def test_an_always_approval_allows_the_write(capsys, tmp_path, nightly):
    source = INSERT.replace("GCRUN1", "GCRUN3")
    macro = _macro(tmp_path, source)
    db_access.approve(macro.resolve(), WriteApproval.ALWAYS, source_hash(source))
    assert _run(capsys, "run", macro, "--db", "Nightly")[0] == EXIT_OK
    assert "GCRUN3" in _codes(nightly)


def test_allow_folder_is_for_this_run_only(capsys, tmp_path, nightly):
    target = tmp_path / "out"
    source = f'opensak.write_text("{(target / "x.txt").as_posix()}", "x")\n'
    macro = _macro(tmp_path, source)
    target.mkdir()
    code, _, err = _run(capsys, "run", macro)
    assert code == EXIT_NEEDS_UI and "--allow-folder" in err
    assert _run(capsys, "run", macro, "--allow-folder", target)[0] == EXIT_OK
    assert (target / "x.txt").read_text() == "x"
    (target / "x.txt").unlink()
    assert _run(capsys, "run", macro)[0] == EXIT_NEEDS_UI


def test_allow_folder_never_opens_a_drive(capsys, tmp_path, nightly):
    root = Path(tmp_path.anchor)
    assert _run(capsys, "run", _macro(tmp_path, ""), "--allow-folder", root)[0] == EXIT_USAGE


def test_allow_folder_never_opens_protected_data(capsys, tmp_path, nightly):
    source = f'opensak.write_text("{nightly.path.as_posix()}.txt", "x")\n'
    macro = _macro(tmp_path, source)
    code, _, _ = _run(capsys, "run", macro, "--allow-folder", nightly.path.parent)
    assert code == EXIT_MACRO_ERROR
    assert not Path(f"{nightly.path}.txt").exists()


def test_switch_database_locks_and_changes_only_this_run(capsys, tmp_path, nightly):
    manager = get_db_manager()
    before = manager.active
    stored = get_store().get("databases.active")
    macro = _macro(tmp_path, 'opensak.switch_database("Nightly")\nprint(opensak.database())\n')
    assert _run(capsys, "run", macro)[:2] == (EXIT_OK, "Nightly\n")
    assert manager.active == before
    assert get_store().get("databases.active") == stored


def test_switch_to_a_database_in_use_stops_the_run(capsys, tmp_path, nightly):
    macro = _macro(tmp_path, 'pcall(opensak.switch_database, "Nightly")\nprint("went on")\n')
    lock = DatabaseLock(nightly.path)
    assert lock.acquire()
    try:
        code, out, err = _run(capsys, "run", macro)
    finally:
        lock.release()
    assert code == EXIT_DB_LOCKED
    assert out == "" and "in use" in err


def test_attach_of_a_database_in_use_stops_the_run(capsys, tmp_path, nightly):
    macro = _macro(tmp_path, 'opensak.attach("Nightly")\nprint("went on")\n')
    lock = DatabaseLock(nightly.path)
    assert lock.acquire()
    try:
        code, out, _ = _run(capsys, "run", macro)
    finally:
        lock.release()
    assert (code, out) == (EXIT_DB_LOCKED, "")


def test_copying_to_a_database_in_use_stops_the_run(capsys, tmp_path, nightly):
    macro = _macro(tmp_path, 'pcall(opensak.copy_caches, "Nightly")\nprint("went on")\n')
    lock = DatabaseLock(nightly.path)
    assert lock.acquire()
    try:
        code, out, _ = _run(capsys, "run", macro, "--allow-write")
    finally:
        lock.release()
    assert (code, out) == (EXIT_DB_LOCKED, "")


def test_creating_a_database_keeps_the_stored_active_one(capsys, tmp_path, nightly):
    stored = get_store().get("databases.active")
    macro = _macro(tmp_path, 'opensak.create_database("Created 1203")\n')
    assert _run(capsys, "run", macro, "--db", "Nightly", "--allow-write")[0] == EXIT_OK
    assert get_store().get("databases.active") == stored


def test_called_macro_runs_under_the_same_rules(capsys, tmp_path, nightly):
    _macro(tmp_path, 'opensak.confirm("Inner?")\nprint("inner went on")\n', "inner.lua")
    macro = _macro(tmp_path, 'pcall(opensak.run_macro, "inner.lua")\nprint("outer went on")\n')
    code, out, err = _run(capsys, "run", macro, "--allow-folder", tmp_path)
    assert code == EXIT_NEEDS_UI
    assert out == "" and "Inner?" in err


def test_called_macro_needs_its_own_approval(capsys, tmp_path, nightly):
    _macro(tmp_path, INSERT.replace("GCRUN1", "GCRUN4"), "inner.lua")
    source = 'opensak.run_macro("inner.lua")\n'
    macro = _macro(tmp_path, source)
    db_access.approve(macro.resolve(), WriteApproval.ALWAYS, source_hash(source))
    code, _, _ = _run(capsys, "run", macro, "--db", "Nightly", "--allow-folder", tmp_path)
    assert code == EXIT_NEEDS_UI
    assert "GCRUN4" not in _codes(nightly)


def test_json_summary(capsys, tmp_path, nightly):
    macro = _macro(tmp_path, 'print("hello")\nopensak.exit(4)\n')
    code, out, _ = _run(capsys, "run", macro, "--db", "Nightly", "--json")
    data = json.loads(out)
    assert code == 4
    assert data["command"] == "run" and data["ok"] is False and data["exit_code"] == 4
    assert data["output"] == ["hello"] and data["database"] == "Nightly"
    assert data["macro"] == str(macro.resolve()) and data["errors"] == []


def test_json_reports_the_stop(capsys, tmp_path, nightly):
    macro = _macro(tmp_path, 'opensak.confirm("Sure?")\n')
    code, out, _ = _run(capsys, "run", macro, "--json")
    data = json.loads(out)
    assert code == EXIT_NEEDS_UI and data["exit_code"] == EXIT_NEEDS_UI
    assert "Sure?" in data["errors"][0]


def test_quiet_writes_nothing_to_stdout(capsys, tmp_path, nightly):
    macro = _macro(tmp_path, 'print("hello")\nerror("boom")\n')
    code, out, err = _run(capsys, "run", macro, "--quiet")
    assert code == EXIT_MACRO_ERROR
    assert out == "" and "boom" in err


def test_log_file_gets_the_output(capsys, tmp_path, nightly):
    log = tmp_path / "run.log"
    macro = _macro(tmp_path, 'print("hello")\nopensak.message("Hi")\n'
                             'opensak.progress("Working"):close()\nerror("boom")\n')
    assert _run(capsys, "run", macro, "--quiet", "--log-file", log)[0] == EXIT_MACRO_ERROR
    text = log.read_text(encoding="utf-8")
    assert "hello" in text and "Hi" in text and "Working" in text and "boom" in text


def test_args_are_answers(capsys, tmp_path, nightly):
    macro = _macro(tmp_path, 'print(opensak.input("Name?"))\n')
    assert _run(capsys, "run", macro, "--arg", "Name?=given")[:2] == (EXIT_OK, "given\n")


def test_is_headless_and_args(capsys, tmp_path, nightly):
    macro = _macro(tmp_path, 'print(opensak.is_headless(), opensak.args.a, opensak.args.b, '
                             'opensak.args.none)\n')
    code, out, _ = _run(capsys, "run", macro, "--arg", "a=1", "--arg", "b=x=y")
    assert (code, out) == (EXIT_OK, "true\t1\tx=y\tnil\n")


def test_a_called_macro_sees_the_same_args(capsys, tmp_path, nightly):
    _macro(tmp_path, 'print(opensak.is_headless(), opensak.args.a)\n', "inner.lua")
    macro = _macro(tmp_path, 'opensak.run_macro("inner.lua")\n')
    code, out, _ = _run(capsys, "run", macro, "--arg", "a=1", "--allow-folder", tmp_path)
    assert (code, out) == (EXIT_OK, "true\t1\n")


# ── The example delete_archived.lua ──────────────────────────────────────────

EXAMPLE = Path(__file__).parents[2] / "macros" / "examples" / "delete_archived.lua"


@pytest.fixture
def archived(nightly):
    """Nightly with an archived cache to delete, and others to keep."""
    conn = sqlite3.connect(nightly.path)
    conn.execute("DELETE FROM caches")
    conn.commit()
    conn.close()
    with session_for(nightly.path) as s:
        for code, values in {
            "GCDEL1": {"archived": True},
            "GCKEEPF": {"archived": True, "found": True},
            "GCKEEPO": {"archived": True, "is_owner": True},
            "GCKEEPL": {"archived": True, "locked": True},
            "GCKEEPA": {"archived": False},
        }.items():
            s.add(Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0, **values))
    return nightly


def test_example_deletes_only_unfound_archived_caches(capsys, archived):
    code, out, _ = _run(capsys, "run", EXAMPLE, "--db", "Nightly", "--allow-write")
    assert code == EXIT_OK and "1 archived caches deleted" in out
    assert _codes(archived) == ["GCKEEPA", "GCKEEPF", "GCKEEPL", "GCKEEPO"]


def test_example_dry_run_deletes_nothing(capsys, archived):
    before = _codes(archived)
    code, out, _ = _run(capsys, "run", EXAMPLE, "--db", "Nightly", "--allow-write",
                        "--arg", "dry_run=yes")
    assert code == EXIT_OK
    assert "GCDEL1" in out and "1 archived caches would be deleted" in out
    assert _codes(archived) == before


def test_example_without_approval_deletes_nothing(capsys, archived):
    before = _codes(archived)
    code, _, err = _run(capsys, "run", EXAMPLE, "--db", "Nightly")
    assert code == EXIT_NEEDS_UI and "--allow-write" in err
    assert _codes(archived) == before


def test_example_with_nothing_to_delete(capsys, archived):
    assert _run(capsys, "run", EXAMPLE, "--db", "Nightly", "--allow-write")[0] == EXIT_OK
    code, out, _ = _run(capsys, "run", EXAMPLE, "--db", "Nightly", "--allow-write")
    assert code == EXIT_OK and "No archived caches to delete" in out


def test_example_asks_first_in_opensak(archived):
    # Not headless: a No keeps the caches.
    init_db(db_path=archived.path)
    lines: list[str] = []
    host = HeadlessHost(answers={"Delete 1 archived caches?": "no"}, allow_write=True,
                        output=lines.append, status=lines.append)
    runtime = MacroRuntime(cast(MacroHost, host), output=lines.append)
    before = _codes(archived)
    source = EXAMPLE.read_text(encoding="utf-8")
    try:
        assert runtime.run(source, macro_path=EXAMPLE) == 0
    finally:
        init_db(db_path=get_db_manager().active_path)
    assert lines == ["Nothing deleted"]
    assert _codes(archived) == before


# ── Without a window ─────────────────────────────────────────────────────────

def test_runs_without_a_display_or_the_gui(tmp_path, nightly):
    # A real process: nothing on the way may pull in QtWidgets or the main
    # window. Its empty profile has no list, so --db is a path outside one.
    macro = _macro(tmp_path, 'print("ran")\n')
    code = (
        "import sys\n"
        f"sys.argv = ['opensak', 'run', {str(macro)!r}, '--db', {str(nightly.path)!r}]\n"
        "from opensak import cli\n"
        "try:\n    cli.entry()\nexcept SystemExit as exc:\n    print('EXIT', exc.code)\n"
        "print('BAD', [m for m in sys.modules if m.startswith('PySide6.QtWidgets')"
        " or m in ('opensak.app', 'opensak.gui.mainwindow')])\n"
    )
    src = Path(cli.__file__).parents[2]
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    env.update(PYTHONPATH=str(src), OPENSAK_DATA_DIR=str(tmp_path / "data"),
               APPDATA=str(tmp_path / "data"), HOME=str(tmp_path / "data"),
               USERPROFILE=str(tmp_path / "data"), XDG_CONFIG_HOME=str(tmp_path / "data"),
               XDG_DATA_HOME=str(tmp_path / "data"))
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            cwd=tmp_path, env=env)
    assert "BAD []" in result.stdout, result.stdout + result.stderr
    assert "EXIT 0" in result.stdout and "ran" in result.stdout, result.stdout + result.stderr

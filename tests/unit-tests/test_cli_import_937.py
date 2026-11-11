"""
tests/unit-tests/test_cli_import_937.py — headless `opensak import gpx|gsak`
(#937): argument parsing, the dispatcher, the database lock, the GPX import
service and whole command runs. No QApplication is needed for any of it.
"""

import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from opensak import cli
from opensak.db.lock import DatabaseLock, lock_path_for
from opensak.db.manager import get_db_manager
from opensak.importer import gpx_import_service as gpx_service
from opensak.importer.gpx_import_service import GpxImportError, collect_files
from opensak.utils.run_macro import (
    EXIT_DB_LOCKED, EXIT_DB_NOT_FOUND, EXIT_MACRO_ERROR, EXIT_OK, EXIT_USAGE,
)
from tests.data import SAMPLE_GPX, SAMPLE_WPTS_GPX, write_gpx

_spec = importlib.util.spec_from_file_location(
    "_gsak_importer_tests", Path(__file__).with_name("test_gsak_importer.py")
)
assert _spec is not None and _spec.loader is not None
_gsak_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gsak_helpers)


@pytest.fixture(autouse=True)
def _no_geocoding(monkeypatch, tmp_path):
    """No boundary data download from a test; GSAK zips unpack in tmp_path."""
    import tempfile
    from opensak.utils import flags
    monkeypatch.setattr(flags, "reverse_geocoding", False)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


@pytest.fixture
def nightly():
    """An OpenSAK database "Nightly" next to the active Default one."""
    manager = get_db_manager()
    manager.ensure_active_initialised()
    return manager.new_database("Nightly")


def _codes(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return sorted(r[0] for r in conn.execute("SELECT gc_code FROM caches"))
    finally:
        conn.close()


def _pq_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "pq"
    folder.mkdir()
    write_gpx(folder, "pq.gpx", SAMPLE_GPX)
    write_gpx(folder, "pq-wpts.gpx", SAMPLE_WPTS_GPX)
    return folder


def _run(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ── Dispatcher and arguments ────────────────────────────────────────────────

@pytest.mark.parametrize("argv, expected", [
    ([], False),
    (["some.gpx"], False),
    (["--run-macro", "x.lua"], False),
    (["--version"], False),
    (["import", "gpx"], True),
    (["--help"], True),
    (["-h"], True),
])
def test_is_cli_command(argv, expected):
    assert cli.is_cli_command(argv) is expected


def test_entry_without_a_command_starts_the_gui(monkeypatch):
    calls = []
    fake_app = types.ModuleType("opensak.app")
    fake_app.main = lambda: calls.append("gui")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "opensak.app", fake_app)
    monkeypatch.setattr(sys, "argv", ["opensak"])
    cli.entry()
    assert calls == ["gui"]


def test_entry_with_a_command_exits_with_its_code(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["opensak", "import", "gpx", "x.gpx", "--db", "A"])
    monkeypatch.setattr(cli, "main", lambda argv: 7)
    with pytest.raises(SystemExit) as exc:
        cli.entry()
    assert exc.value.code == 7


def test_entry_does_not_import_the_gui():
    # A headless command must work without a display: nothing on its way
    # may pull in QtWidgets or opensak.gui (other than gui.settings).
    import subprocess
    code = (
        "import sys; sys.argv = ['opensak', 'import', 'gpx', '--help']\n"
        "from opensak import cli\n"
        "try:\n    cli.entry()\nexcept SystemExit:\n    pass\n"
        "bad = [m for m in sys.modules if m.startswith('PySide6.QtWidgets')"
        " or m == 'opensak.app']\n"
        "print('BAD', bad)\n"
    )
    src = Path(cli.__file__).parents[2]
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=src, env={**__import__("os").environ, "PYTHONPATH": str(src)})
    assert "BAD []" in out.stdout, out.stdout + out.stderr


def test_parse_gpx_args():
    args = cli.parse_args(["import", "gpx", "a.gpx", "folder", "--db", "Nightly",
                           "--recursive", "--move-imported-to", "done", "--wait", "30"])
    assert (args.command, args.kind) == ("import", "gpx")
    assert args.paths == ["a.gpx", "folder"]
    assert args.db == "Nightly" and args.recursive and args.wait == 30
    assert args.move_imported_to == "done"
    assert not args.json and not args.quiet


def test_parse_gsak_args_defaults():
    args = cli.parse_args(["import", "gsak", "backup.zip", "--db", "Nightly"])
    assert (args.kind, args.source) == ("gsak", "backup.zip")
    assert args.if_exists == "merge"
    assert args.gsak_db is None and args.filters is None and args.locations is None


def test_parse_gsak_args_repeat_gsak_db():
    args = cli.parse_args(["import", "gsak", "x", "--db", "N", "--gsak-db", "A",
                           "--gsak-db", "B", "--if-exists", "overwrite"])
    assert args.gsak_db == ["A", "B"] and args.if_exists == "overwrite"


@pytest.mark.parametrize("argv", [
    ["import"],
    ["import", "gpx", "a.gpx"],                              # no --db
    ["import", "gpx", "--db", "N"],                          # no source
    ["import", "gpx", "a.gpx", "--db", "N", "--json", "--quiet"],
    ["import", "gpx", "a.gpx", "--db", "N", "--wait", "-1"],
    ["import", "gsak", "x", "--db", "N", "--if-exists", "skip"],
    ["import", "kml", "x", "--db", "N"],
])
def test_bad_arguments_exit_2(capsys, argv):
    code, out, err = _run(capsys, *argv)
    assert code == EXIT_USAGE
    assert "usage: opensak" in err


def test_help_exits_0(capsys):
    code, out, _ = _run(capsys, "import", "gpx", "--help")
    assert code == EXIT_OK
    assert "--move-imported-to" in out


# ── Sources ─────────────────────────────────────────────────────────────────

def test_collect_files_folder_skips_the_companion(tmp_path):
    folder = _pq_folder(tmp_path)
    (folder / "notes.txt").write_text("x")
    assert [f.name for f in collect_files([folder])] == ["pq.gpx"]


def test_collect_files_recursive_and_each_file_once(tmp_path):
    folder = _pq_folder(tmp_path)
    sub = folder / "2026-10-24"
    sub.mkdir()
    write_gpx(sub, "other.gpx", SAMPLE_GPX)
    assert [f.name for f in collect_files([folder])] == ["pq.gpx"]
    files = collect_files([folder / "pq.gpx", folder], recursive=True)
    assert [f.name for f in files] == ["pq.gpx", "other.gpx"]


def test_collect_files_rejects_missing_and_other_types(tmp_path):
    with pytest.raises(GpxImportError, match="does not exist"):
        collect_files([tmp_path / "gone"])
    (tmp_path / "a.loc").write_text("x")
    with pytest.raises(GpxImportError, match="not a .gpx or .zip"):
        collect_files([tmp_path / "a.loc"])


# ── Lock ────────────────────────────────────────────────────────────────────

def test_lock_is_exclusive_and_released(tmp_path):
    db = tmp_path / "Nightly.db"
    first, second = DatabaseLock(db), DatabaseLock(db)
    assert first.acquire()
    assert lock_path_for(db).exists()
    assert not second.acquire()
    assert "process" in (second.holder() or "")
    first.release()
    assert not lock_path_for(db).exists()
    assert second.acquire()
    second.release()


def test_stale_lock_of_a_dead_process_is_taken_over(tmp_path):
    import subprocess
    db = tmp_path / "Nightly.db"
    # A process that takes the lock and dies without releasing it.
    code = (
        "import os, sys\n"
        "from PySide6.QtCore import QLockFile\n"
        f"lock = QLockFile({str(lock_path_for(db))!r}); lock.setStaleLockTime(0)\n"
        "assert lock.tryLock(0)\n"
        "os._exit(0)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
    assert lock_path_for(db).exists()
    lock = DatabaseLock(db)
    assert lock.acquire()
    lock.release()


def test_gui_holds_the_lock_of_the_active_database(nightly):
    manager = get_db_manager()
    default = manager.active
    assert default is not None
    manager.hold_active_lock()
    try:
        assert not DatabaseLock(default.path).acquire()
        assert DatabaseLock(nightly.path).acquire() is True
    finally:
        manager.release_active_lock()
    assert DatabaseLock(default.path).acquire()


def test_gui_lock_follows_a_switch(nightly):
    manager = get_db_manager()
    default = manager.active
    assert default is not None
    manager.hold_active_lock()
    try:
        manager.switch_to(nightly)
        other = DatabaseLock(default.path)
        assert other.acquire()
        other.release()
        assert not DatabaseLock(nightly.path).acquire()
    finally:
        manager.release_active_lock()


# ── import gpx ──────────────────────────────────────────────────────────────

def test_import_gpx_into_a_database_by_name(capsys, tmp_path, nightly):
    folder = _pq_folder(tmp_path)
    code, out, err = _run(capsys, "import", "gpx", str(folder), "--db", "nightly")
    assert code == EXIT_OK, err
    assert _codes(nightly.path) == ["GC12345", "GC99999"]
    assert "pq.gpx: 2 new, 0 updated" in out
    assert "GPX import into Nightly" in out
    # The active database is left alone.
    active = get_db_manager().active
    assert active is not None and _codes(active.path) == []
    # The summary is in the log file as well.
    from opensak.config import get_log_path
    assert "pq.gpx: 2 new" in get_log_path().read_text(encoding="utf-8")
    assert not lock_path_for(nightly.path).exists()


def test_import_gpx_by_path_json(capsys, tmp_path, nightly):
    folder = _pq_folder(tmp_path)
    code, out, _ = _run(capsys, "import", "gpx", str(folder / "pq.gpx"),
                        "--db", str(nightly.path), "--json")
    assert code == EXIT_OK
    data = json.loads(out)
    assert data["ok"] is True
    assert (data["database"], data["created"], data["errors"]) == ("Nightly", 2, 0)
    assert data["files"][0]["path"].endswith("pq.gpx")


def test_import_gpx_quiet_writes_nothing_to_stdout(capsys, tmp_path, nightly):
    folder = _pq_folder(tmp_path)
    code, out, err = _run(capsys, "import", "gpx", str(folder), "--db", "Nightly", "--quiet")
    assert code == EXIT_OK
    assert out == "" and err == ""


def test_import_gpx_moves_imported_files(capsys, tmp_path, nightly):
    folder = _pq_folder(tmp_path)
    write_gpx(folder, "broken.gpx", "<gpx")
    done = tmp_path / "done"
    code, out, _ = _run(capsys, "import", "gpx", str(folder), "--db", "Nightly",
                        "--move-imported-to", str(done))
    # broken.gpx fails, so the run reports an error and the file stays.
    assert code == EXIT_MACRO_ERROR
    assert sorted(p.name for p in done.iterdir()) == ["pq-wpts.gpx", "pq.gpx"]
    assert sorted(p.name for p in folder.iterdir()) == ["broken.gpx"]
    assert "Moved 2 files" in out


def test_moved_file_gets_a_free_name(capsys, tmp_path, nightly):
    folder = tmp_path / "pq"
    folder.mkdir()
    write_gpx(folder, "pq.gpx", SAMPLE_GPX)
    done = tmp_path / "done"
    done.mkdir()
    (done / "pq.gpx").write_text("older")
    code, _, _ = _run(capsys, "import", "gpx", str(folder), "--db", "Nightly",
                      "--move-imported-to", str(done))
    assert code == EXIT_OK
    assert sorted(p.name for p in done.iterdir()) == ["pq (2).gpx", "pq.gpx"]


def test_unknown_database_exits_5(capsys, tmp_path, nightly):
    folder = _pq_folder(tmp_path)
    code, out, err = _run(capsys, "import", "gpx", str(folder), "--db", "Nope")
    assert code == EXIT_DB_NOT_FOUND
    assert "no database 'Nope'" in err and "'Nightly'" in err
    assert out == ""


def test_a_database_is_never_created(capsys, tmp_path, nightly):
    folder = _pq_folder(tmp_path)
    code, _, _ = _run(capsys, "import", "gpx", str(folder), "--db", str(tmp_path / "new.db"))
    assert code == EXIT_DB_NOT_FOUND
    assert not (tmp_path / "new.db").exists()


def test_missing_source_exits_2_before_opening_the_database(capsys, tmp_path, nightly):
    code, _, err = _run(capsys, "import", "gpx", str(tmp_path / "gone"), "--db", "Nightly")
    assert code == EXIT_USAGE
    assert "does not exist" in err


def test_database_in_use_exits_6(capsys, tmp_path, nightly):
    folder = _pq_folder(tmp_path)
    with DatabaseLock(nightly.path) as held:
        assert held.acquire()
        code, _, err = _run(capsys, "import", "gpx", str(folder), "--db", "Nightly",
                            "--wait", "0.2")
    assert code == EXIT_DB_LOCKED
    assert "is in use" in err and "--wait" in err
    assert _codes(nightly.path) == []


def test_database_open_in_the_gui_exits_6(capsys, tmp_path, nightly):
    manager = get_db_manager()
    manager.switch_to(nightly)
    manager.hold_active_lock()
    try:
        code, _, _ = _run(capsys, "import", "gpx", str(_pq_folder(tmp_path)),
                          "--db", "Nightly")
    finally:
        manager.release_active_lock()
    assert code == EXIT_DB_LOCKED


def test_old_database_is_migrated_with_a_backup(capsys, tmp_path, nightly):
    from opensak.db import database
    conn = sqlite3.connect(nightly.path)
    conn.execute(f"PRAGMA user_version = {database.SCHEMA_VERSION - 1}")
    conn.commit()
    conn.close()
    database._migrated_paths.discard(nightly.path)
    code, _, err = _run(capsys, "import", "gpx", str(_pq_folder(tmp_path)), "--db", "Nightly")
    assert code == EXIT_OK
    assert "was updated to the current version" in err


# ── GPX import service: the steps after a GUI import ────────────────────────

def test_after_import_geocodes_and_recalculates_the_target(tmp_path, nightly, monkeypatch):
    from opensak.geo import reverse_geocode as geo
    calls = []
    monkeypatch.setattr(geo, "rows_missing_location",
                        lambda db_path: calls.append(("rows", db_path)) or ["row"])
    monkeypatch.setattr(geo, "reverse_geocode",
                        lambda rows, db_path: calls.append(("geocode", db_path))
                        or geo.UpdateLocationResult(updated=2))
    monkeypatch.setattr(gpx_service, "recalculate_distances_for",
                        lambda db_path: calls.append(("distances", db_path)))
    files = collect_files([_pq_folder(tmp_path)])
    report = gpx_service.run_gpx_import(files, nightly.path, geocode=True)
    assert report.ok
    assert calls == [("rows", nightly.path), ("geocode", nightly.path),
                     ("distances", nightly.path)]
    assert "Locations: 2 updated" in "\n".join(report.summary_lines())


def test_nothing_imported_skips_the_steps_after(tmp_path, nightly, monkeypatch):
    monkeypatch.setattr(gpx_service, "after_import",
                        lambda *a, **k: pytest.fail("no caches changed"))
    write_gpx(tmp_path, "empty.gpx", '<?xml version="1.0"?><gpx></gpx>')
    report = gpx_service.run_gpx_import([tmp_path / "empty.gpx"], nightly.path)
    assert not report.changed


def test_distances_are_recalculated_in_the_target(tmp_path, nightly):
    files = collect_files([_pq_folder(tmp_path)])
    gpx_service.run_gpx_import(files, nightly.path)
    conn = sqlite3.connect(nightly.path)
    try:
        distances = [r[0] for r in conn.execute("SELECT distance FROM caches")]
    finally:
        conn.close()
    assert distances and all(d is not None for d in distances)


# ── import gsak ─────────────────────────────────────────────────────────────

def _gsak_db(path: Path, *codes: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return _gsak_helpers._make_gsak_db(
        path, caches=[{"Code": c} for c in codes],
        memos=[{"Code": c, "Url": f"https://coord.info/{c}"} for c in codes],
    )


def test_import_gsak_folder_into_one_database(capsys, tmp_path, nightly):
    root = tmp_path / "gsak"
    _gsak_db(root / "data" / "Found" / "sqlite.db3", "GC1A", "GC1B")
    _gsak_db(root / "data" / "Holidays" / "sqlite.db3", "GC2A")
    before = {d.name for d in get_db_manager().databases}
    code, out, err = _run(capsys, "import", "gsak", str(root), "--db", "Nightly")
    assert code == EXIT_OK, err
    assert _codes(nightly.path) == ["GC1A", "GC1B", "GC2A"]
    assert "Found → Nightly: merged" in out
    assert {d.name for d in get_db_manager().databases} == before


def test_import_gsak_selected_database_json(capsys, tmp_path, nightly):
    root = tmp_path / "gsak"
    _gsak_db(root / "data" / "Found" / "sqlite.db3", "GC1A")
    _gsak_db(root / "data" / "Holidays" / "sqlite.db3", "GC2A")
    code, out, _ = _run(capsys, "import", "gsak", str(root), "--db", "Nightly",
                        "--gsak-db", "holidays", "--json")
    assert code == EXIT_OK
    data = json.loads(out)
    assert [d["gsak_name"] for d in data["databases"]] == ["Holidays"]
    assert data["created"] == 1
    assert _codes(nightly.path) == ["GC2A"]


def test_import_gsak_overwrite_empties_first(capsys, tmp_path, nightly):
    db3 = _gsak_db(tmp_path / "one" / "sqlite.db3", "GC1A")
    assert _run(capsys, "import", "gsak", str(db3), "--db", "Nightly")[0] == EXIT_OK
    db3 = _gsak_db(tmp_path / "two" / "sqlite.db3", "GC9Z")
    code, _, _ = _run(capsys, "import", "gsak", str(db3), "--db", "Nightly",
                      "--if-exists", "overwrite")
    assert code == EXIT_OK
    assert _codes(nightly.path) == ["GC9Z"]


def test_import_gsak_into_an_unlisted_database_file(capsys, tmp_path, nightly):
    manager = get_db_manager()
    manager.remove_from_list(nightly)
    db3 = _gsak_db(tmp_path / "one" / "sqlite.db3", "GC1A")
    code, out, err = _run(capsys, "import", "gsak", str(db3), "--db", str(nightly.path))
    assert code == EXIT_OK, err
    assert _codes(nightly.path) == ["GC1A"]
    assert nightly.name not in {d.name for d in manager.databases}


def test_import_gsak_unknown_gsak_database_exits_2(capsys, tmp_path, nightly):
    root = tmp_path / "gsak"
    _gsak_db(root / "data" / "Found" / "sqlite.db3", "GC1A")
    code, _, err = _run(capsys, "import", "gsak", str(root), "--db", "Nightly",
                        "--gsak-db", "Nope")
    assert code == EXIT_USAGE
    assert "no GSAK database named 'nope'" in err


def test_import_gsak_missing_source_exits_2(capsys, tmp_path, nightly):
    code, _, err = _run(capsys, "import", "gsak", str(tmp_path / "gone.zip"), "--db", "Nightly")
    assert code == EXIT_USAGE
    assert "does not exist" in err

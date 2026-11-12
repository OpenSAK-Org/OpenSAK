"""
tests/unit-tests/test_cli_export_db_1203.py — whole runs of `opensak export gpx`
and `opensak db list|backup|check` (#1203). No QApplication and no display.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from opensak import cli
from opensak.backup.backupset import read_backup_set
from opensak.db.lock import DatabaseLock
from opensak.db.manager import get_db_manager
from opensak.filters.engine import CacheTypeFilter, FilterProfile, FilterSet
from opensak.settings_store import get_store
from opensak.utils import flags
from opensak.utils.run_macro import (
    EXIT_DB_LOCKED, EXIT_DB_NOT_FOUND, EXIT_MACRO_ERROR, EXIT_OK, EXIT_USAGE,
)
from tests.data import SAMPLE_GPX, SAMPLE_WPTS_GPX, write_gpx


@pytest.fixture(autouse=True)
def _no_geocoding(monkeypatch):
    monkeypatch.setattr(flags, "reverse_geocoding", False)


@pytest.fixture
def filled(tmp_path, capsys):
    """A database "Filled 1203" with the sample caches, and saved filters."""
    manager = get_db_manager()
    manager.ensure_active_initialised()
    db = next((d for d in manager.databases if d.name == "Filled 1203"), None)
    if db is None:
        db = manager.new_database("Filled 1203")
    folder = tmp_path / "pq"
    folder.mkdir()
    write_gpx(folder, "pq.gpx", SAMPLE_GPX)
    write_gpx(folder, "pq-wpts.gpx", SAMPLE_WPTS_GPX)
    assert cli.main(["import", "gpx", str(folder), "--db", db.name, "--quiet"]) == EXIT_OK
    capsys.readouterr()
    FilterProfile("Mysteries 1203", FilterSet().add(CacheTypeFilter(["Unknown Cache"]))).save()
    FilterProfile("Nothing 1203", FilterSet().add(CacheTypeFilter(["Webcam Cache"]))).save()
    return db


def _run(capsys, *argv) -> tuple[int, str, str]:
    code = cli.main([str(a) for a in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _gc_codes(db) -> dict[str, str]:
    conn = sqlite3.connect(db.path)
    try:
        return dict(conn.execute("SELECT cache_type, gc_code FROM caches").fetchall())
    finally:
        conn.close()


# ── export gpx ───────────────────────────────────────────────────────────────

def test_export_writes_the_caches_of_the_filter(capsys, tmp_path, filled):
    codes = _gc_codes(filled)
    target = tmp_path / "sub" / "out.gpx"
    code, out, _ = _run(capsys, "export", "gpx", "--db", filled.name,
                        "--filter", "mysteries 1203", "--out", target, "--json")
    data = json.loads(out)
    assert code == EXIT_OK
    assert data["count"] == 1 and data["file"] == str(target)
    assert data["filter"] == "Mysteries 1203"
    text = target.read_text(encoding="utf-8")
    assert codes["Unknown Cache"] in text
    assert codes["Traditional Cache"] not in text


def test_export_is_the_same_as_the_export_code_of_the_gui(capsys, tmp_path, filled):
    from opensak.db.database import get_session, init_db
    from opensak.export.file_export import select_for_export, write_export_file
    from opensak.filters.engine import apply_filters_auto

    target = tmp_path / "cli.gpx"
    assert _run(capsys, "export", "gpx", "--db", filled.name, "--filter", "Mysteries 1203",
                "--out", target)[0] == EXIT_OK
    active = get_db_manager().active
    init_db(db_path=filled.path)
    try:
        profile = FilterProfile.load(FilterProfile.profile_path("Mysteries 1203"))
        with get_session() as session:
            caches = select_for_export(apply_filters_auto(session, profile.filterset, profile.sort))
        expected = tmp_path / "cli_gui.gpx"
        write_export_file(caches, expected, "gpx")
    finally:
        init_db(db_path=active.path)
    # The document name comes from the file name, the time from the clock.
    def body(path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        return text[text.index("<wpt"):]
    assert body(target) == body(expected)


def test_export_without_caches_writes_no_file(capsys, tmp_path, filled):
    target = tmp_path / "none.gpx"
    code, out, _ = _run(capsys, "export", "gpx", "--db", filled.name,
                        "--filter", "Nothing 1203", "--out", target)
    assert code == EXIT_OK and "No caches" in out
    assert not target.exists()


def test_export_unknown_filter(capsys, tmp_path, filled):
    code, _, err = _run(capsys, "export", "gpx", "--db", filled.name, "--filter", "No such",
                        "--out", tmp_path / "x.gpx")
    assert code == EXIT_USAGE and "Mysteries 1203" in err


def test_export_database_in_use(capsys, tmp_path, filled):
    lock = DatabaseLock(filled.path)
    assert lock.acquire()
    try:
        code = _run(capsys, "export", "gpx", "--db", filled.name, "--filter", "Mysteries 1203",
                    "--out", tmp_path / "x.gpx")[0]
    finally:
        lock.release()
    assert code == EXIT_DB_LOCKED


def test_export_keeps_the_active_database(capsys, tmp_path, filled):
    manager = get_db_manager()
    before = manager.active
    stored = get_store().get("databases.active")
    _run(capsys, "export", "gpx", "--db", filled.name, "--filter", "Mysteries 1203",
         "--out", tmp_path / "x.gpx")
    assert manager.active == before
    assert get_store().get("databases.active") == stored


# ── db list ──────────────────────────────────────────────────────────────────

def test_db_list(capsys, filled):
    manager = get_db_manager()
    code, out, _ = _run(capsys, "db", "list", "--json")
    rows = {r["name"]: r for r in json.loads(out)["databases"]}
    assert code == EXIT_OK
    assert rows[filled.name]["path"] == str(filled.path) and rows[filled.name]["exists"]
    assert [n for n, r in rows.items() if r["active"]] == [manager.active.name]
    text = _run(capsys, "db", "list")[1]
    assert filled.name in text and f"* {manager.active.name}" in text


# ── db backup ────────────────────────────────────────────────────────────────

def test_db_backup_makes_a_set_with_the_database_settings_and_macros(capsys, tmp_path, filled):
    code, out, _ = _run(capsys, "db", "backup", filled.name, "--out", tmp_path / "bak", "--json")
    data = json.loads(out)
    assert code == EXIT_OK and data["left_out"] == []
    backup = read_backup_set(Path(data["backup"]))
    assert Path(data["backup"]).is_relative_to(tmp_path / "bak")
    assert [d.name for d in backup.databases] == [filled.name]
    assert backup.settings is True
    assert backup.kind == "manual"


def test_db_backup_works_while_the_database_is_in_use(capsys, tmp_path, filled):
    lock = DatabaseLock(filled.path)
    assert lock.acquire()
    try:
        assert _run(capsys, "db", "backup", filled.name, "--out", tmp_path / "bak")[0] == EXIT_OK
    finally:
        lock.release()


def test_db_backup_unknown_database(capsys):
    assert _run(capsys, "db", "backup", "No such")[0] == EXIT_DB_NOT_FOUND


# ── db check ─────────────────────────────────────────────────────────────────

def test_db_check_ok(capsys, filled):
    code, out, _ = _run(capsys, "db", "check", filled.name, "--json")
    data = json.loads(out)
    assert code == EXIT_OK and data["ok"] is True
    assert data["files"][0]["path"] == str(filled.path)


def test_db_check_finds_damage(capsys, tmp_path, filled):
    from opensak.db.database import dispose_engine

    dispose_engine(filled.path)
    damaged = tmp_path / "damaged.db"
    conn = sqlite3.connect(filled.path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    pages, size = (conn.execute(f"PRAGMA {p}").fetchone()[0] for p in ("page_count", "page_size"))
    conn.execute(f"VACUUM INTO '{damaged.as_posix()}'")
    conn.close()
    assert pages > 3
    with open(damaged, "r+b") as f:
        f.seek(size * 2)
        f.write(b"\xff" * size * (pages - 3))
    code, out, _ = _run(capsys, "db", "check", damaged)
    assert code == EXIT_MACRO_ERROR
    assert "PROBLEMS FOUND" in out


def test_db_check_does_not_migrate_or_lock(capsys, filled):
    conn = sqlite3.connect(filled.path)
    conn.execute("PRAGMA user_version = 1")
    conn.close()
    from opensak.db.database import SCHEMA_VERSION
    lock = DatabaseLock(filled.path)
    assert lock.acquire()
    try:
        assert _run(capsys, "db", "check", filled.name)[0] == EXIT_OK
    finally:
        lock.release()
    conn = sqlite3.connect(filled.path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    finally:
        conn.close()


# ── Without a window ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("argv", [["db", "list"], ["db", "check", "{db}"],
                                  ["db", "backup", "{db}", "--out", "{bak}"]])
def test_db_commands_do_not_import_the_gui(tmp_path, filled, argv):
    argv = [a.format(db=filled.path, bak=tmp_path / "bak") for a in argv]
    code = (
        "import sys\n"
        f"sys.argv = ['opensak', *{argv!r}]\n"
        "from opensak import cli\n"
        "try:\n    cli.entry()\nexcept SystemExit as exc:\n    print('EXIT', exc.code)\n"
        "print('BAD', [m for m in sys.modules if m.startswith('PySide6.QtWidgets')"
        " or m in ('opensak.app', 'opensak.gui.mainwindow')])\n"
    )
    src = Path(cli.__file__).parents[2]
    home = str(tmp_path / "data")
    env = {**os.environ, "PYTHONPATH": str(src), "APPDATA": home, "LOCALAPPDATA": home,
           "HOME": home, "USERPROFILE": home, "XDG_CONFIG_HOME": home, "XDG_DATA_HOME": home}
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            cwd=tmp_path, env=env)
    assert "BAD []" in result.stdout, result.stdout + result.stderr
    assert "EXIT 0" in result.stdout, result.stdout + result.stderr

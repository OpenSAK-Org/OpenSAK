"""
tests/unit-tests/test_backup_macro_db_942.py — backups include the Lua
macros' own databases (opensak.macro_db(), #1034), part of #942.

The root conftest isolates the install folder (and Path.home()) per test,
so the recorded macro databases and the backups all live under tmp_path.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from opensak.backup.backupset import (
    KIND_MANUAL,
    KIND_SAFETY,
    MACRO_DATABASES_KEY,
    known_macro_databases,
    read_backup_set,
    remember_macro_database,
    write_backup_set,
)
from opensak.settings_store import get_store

NOW = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)


def _make_db(path: Path, rows: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE seen (code TEXT PRIMARY KEY)")
    conn.executemany("INSERT INTO seen VALUES (?)", [(f"GC{i:05d}",) for i in range(rows)])
    conn.commit()
    conn.close()
    return path


def _rows(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT count(*) FROM seen").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def backups(tmp_path: Path) -> Path:
    return tmp_path / "backups"


@pytest.fixture
def dbs(tmp_path: Path) -> list[SimpleNamespace]:
    path = _make_db(tmp_path / "dbs" / "Default.db")
    return [SimpleNamespace(name="Default", path=path)]


def test_remember_records_each_file_once(tmp_path):
    a = _make_db(tmp_path / "macros" / "a.db3")
    remember_macro_database(a)
    remember_macro_database(tmp_path / "macros" / "." / "a.db3")
    remember_macro_database(tmp_path / "other" / "b.db3")
    assert known_macro_databases() == [a.resolve(), (tmp_path / "other" / "b.db3").resolve()]


def test_garbage_in_the_setting_is_ignored():
    get_store().set(MACRO_DATABASES_KEY, ["", 3, None])
    assert known_macro_databases() == []
    get_store().set(MACRO_DATABASES_KEY, "not a list")
    assert known_macro_databases() == []


@pytest.mark.parametrize("compress", [False, True], ids=["folder", "zip"])
def test_recorded_macro_databases_are_backed_up(tmp_path, dbs, backups, compress):
    one = _make_db(tmp_path / "macros" / "stat.db3", rows=7)
    # Same file name in another folder: both are kept, under unique names.
    two = _make_db(tmp_path / "elsewhere" / "stat.db3", rows=3)
    remember_macro_database(one)
    remember_macro_database(two)
    remember_macro_database(tmp_path / "gone" / "missing.db3")   # left out silently

    result = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW, compress=compress)

    assert result.skipped == ()
    assert result.skipped_macro_databases == ()
    entries = result.backup_set.macro_databases
    assert [(e.file, e.path) for e in entries] == [
        ("macro_databases/stat.db3", str(one.resolve())),
        ("macro_databases/stat-2.db3", str(two.resolve())),
    ]
    if compress:
        with zipfile.ZipFile(result.backup_set.path) as zf:
            names = set(zf.namelist())
            zf.extractall(tmp_path / "unzipped")
        root = tmp_path / "unzipped"
        assert {"macro_databases/stat.db3", "macro_databases/stat-2.db3"} <= names
    else:
        root = result.backup_set.path
    assert _rows(root / "macro_databases" / "stat.db3") == 7
    assert _rows(root / "macro_databases" / "stat-2.db3") == 3


def test_a_damaged_macro_database_is_reported_but_never_fails_the_backup(tmp_path, dbs, backups):
    bad = tmp_path / "macros" / "bad.db3"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"this is not an SQLite database" * 200)
    good = _make_db(tmp_path / "macros" / "good.db3")
    remember_macro_database(bad)
    remember_macro_database(good)

    result = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW, compress=False)

    assert result.skipped_macro_databases == (str(bad.resolve()),)
    assert [e.file for e in result.backup_set.macro_databases] == ["macro_databases/good.db3"]
    assert [e.name for e in result.backup_set.databases] == ["Default"]
    assert not (result.backup_set.path / "macro_databases" / "bad.db3").exists()


def test_a_safety_set_has_no_macro_databases(tmp_path, backups):
    remember_macro_database(_make_db(tmp_path / "macros" / "stat.db3"))
    result = write_backup_set([], KIND_SAFETY, folder=backups, now=NOW, compress=False)
    assert result.backup_set.macro_databases == ()
    assert not (result.backup_set.path / "macro_databases").exists()


def test_without_macro_databases_the_folder_is_not_created(dbs, backups):
    result = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW, compress=False)
    assert result.backup_set.macro_databases == ()
    assert not (result.backup_set.path / "macro_databases").exists()
    manifest = json.loads((result.backup_set.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["macro_databases"] == []
    assert manifest["format"] == 1


def test_a_set_from_before_macro_databases_still_reads(dbs, backups):
    result = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW, compress=False)
    manifest_path = result.backup_set.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["macro_databases"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert read_backup_set(result.backup_set.path).macro_databases == ()


def test_macro_databases_count_in_the_progress(tmp_path, dbs, backups):
    stat = _make_db(tmp_path / "macros" / "stat.db3", rows=500)
    remember_macro_database(stat)
    seen: list[tuple[str, float]] = []
    write_backup_set(dbs, KIND_MANUAL, lambda name, _f, overall: seen.append((name, overall)),
                     folder=backups, now=NOW, compress=False)
    assert "stat.db3" in {name for name, _ in seen}
    overall = [o for _, o in seen]
    assert overall == sorted(overall)
    assert overall[-1] == pytest.approx(1.0)

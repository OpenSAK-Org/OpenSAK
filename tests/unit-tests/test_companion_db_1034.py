"""
tests/unit-tests/test_companion_db_1034.py — the companion database of a
cache database (#1034), step 1: the file, attaching it, and its lifecycle
(rename, move, copy, delete, discovery, backup, restore, exit state).

Uses real SQLite files, the real engine lifecycle and a real
DatabaseManager (isolated per test by the root conftest).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from opensak.backup import exit_state as es
from opensak.backup.backupset import (
    COMPANIONS_DIRNAME,
    KIND_MANUAL,
    read_backup_set,
    write_backup_set,
)
from opensak.db import companion
from opensak.db.companion import Status
from opensak.db.database import dispose_engine, get_session, init_db, session_for
from opensak.db.db_settings import ensure_seeded, read_file
from opensak.db.discover import find_unregistered_databases
from opensak.lang import load_language
from opensak.utils import flags

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 22)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """English messages, the flag on, and no status left from another test."""
    load_language("en")
    monkeypatch.setattr(flags, "companion_db", True)
    companion.reset()
    yield
    dispose_engine()
    companion.reset()


def _main_db(path: Path) -> Path:
    """A real OpenSAK database with a db_uuid, closed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path=path)
    dispose_engine(path)
    ensure_seeded(path, path.stem)
    return path


def _with_companion(path: Path, rows: int = 3) -> Path:
    """*path* as a database with a companion holding a table of *rows* rows."""
    _main_db(path)
    comp = companion.create(path)
    conn = sqlite3.connect(str(comp))
    conn.execute("CREATE TABLE notes (gc_code TEXT PRIMARY KEY, note TEXT)")
    conn.executemany("INSERT INTO notes VALUES (?, ?)",
                     [(f"GC{i}", f"note {i}") for i in range(rows)])
    conn.commit()
    conn.close()
    return path


def _notes(db_path: Path) -> int:
    conn = sqlite3.connect(str(companion.companion_path(db_path)))
    try:
        return conn.execute("SELECT count(*) FROM notes").fetchone()[0]
    finally:
        conn.close()


def _uuid(path: Path) -> str:
    return read_file(path)["db_uuid"]


def _attached(session) -> bool:
    names = {row[1] for row in session.execute(text("PRAGMA database_list"))}
    return companion.COMPANION_SCHEMA in names


# ── The file ──────────────────────────────────────────────────────────────────

class TestFile:
    def test_named_after_its_database(self, tmp_path):
        assert companion.companion_path(tmp_path / "Found.db") == tmp_path / "Found.companion.db3"
        assert companion.is_companion_file(tmp_path / "Found.companion.db3")
        assert not companion.is_companion_file(tmp_path / "Found.db")

    def test_create_writes_schema_and_main_uuid(self, tmp_path):
        db = _main_db(tmp_path / "Found.db")
        path = companion.create(db)

        assert path == companion.companion_path(db)
        conn = sqlite3.connect(str(path))
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == companion.SCHEMA_VERSION
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        assert {companion.META_TABLE, companion.REGISTRY_TABLE} <= tables
        assert companion.read_meta(db)[companion.MAIN_UUID_KEY] == _uuid(db)
        assert companion.status(db) is Status.OK
        # Written under a temporary name: nothing else is left in the folder.
        assert sorted(p.name for p in tmp_path.iterdir() if "companion" in p.name) == [path.name]

    def test_create_twice_keeps_the_file(self, tmp_path):
        db = _with_companion(tmp_path / "Found.db")
        companion.create(db)
        assert _notes(db) == 3

    def test_create_needs_the_database(self, tmp_path):
        with pytest.raises(companion.CompanionError):
            companion.create(tmp_path / "Missing.db")
        assert not companion.companion_path(tmp_path / "Missing.db").exists()

    def test_a_database_without_companion_gets_none_when_opened(self, tmp_path):
        db = _main_db(tmp_path / "Found.db")
        init_db(db_path=db)
        assert companion.status(db) is Status.MISSING
        assert not companion.exists(db)


class TestPrepare:
    def test_ok(self, tmp_path):
        db = _with_companion(tmp_path / "Found.db")
        companion.reset()
        assert companion.prepare(db) is Status.OK

    def test_newer_schema_is_left_alone(self, tmp_path):
        db = _with_companion(tmp_path / "Found.db")
        conn = sqlite3.connect(str(companion.companion_path(db)))
        conn.execute(f"PRAGMA user_version = {companion.SCHEMA_VERSION + 1}")
        conn.close()
        assert companion.prepare(db) is Status.NEWER

    def test_companion_of_another_database_is_foreign(self, tmp_path):
        db = _with_companion(tmp_path / "Found.db")
        other = _main_db(tmp_path / "Other.db")
        companion.companion_path(db).rename(companion.companion_path(other))
        assert companion.prepare(other) is Status.FOREIGN

    def test_not_a_database(self, tmp_path):
        db = _main_db(tmp_path / "Found.db")
        companion.companion_path(db).write_bytes(b"not a database at all" * 100)
        assert companion.prepare(db) is Status.BROKEN

    def test_sqlite_file_without_companion_tables(self, tmp_path):
        db = _main_db(tmp_path / "Found.db")
        conn = sqlite3.connect(str(companion.companion_path(db)))
        conn.execute("CREATE TABLE something (x)")
        conn.close()
        assert companion.prepare(db) is Status.BROKEN

    def test_takes_over_the_uuid_of_a_database_that_had_none(self, tmp_path):
        db = _with_companion(tmp_path / "Found.db")
        companion.set_main_uuid(db, None)
        assert companion.prepare(db) is Status.OK
        assert companion.read_meta(db)[companion.MAIN_UUID_KEY] == _uuid(db)

    def test_older_schema_is_migrated_after_a_backup(self, tmp_path):
        db = _with_companion(tmp_path / "Found.db")
        conn = sqlite3.connect(str(companion.companion_path(db)))
        conn.execute("PRAGMA user_version = 0")
        conn.close()

        assert companion.prepare(db) is Status.OK
        conn = sqlite3.connect(str(companion.companion_path(db)))
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == companion.SCHEMA_VERSION
        finally:
            conn.close()
        backups = list((tmp_path / "backups").glob("Found.companion.pre-migration-schema0*.db3"))
        assert len(backups) == 1


# ── Attaching ─────────────────────────────────────────────────────────────────

class TestAttach:
    def test_active_engine_attaches_it(self, tmp_path):
        db = _with_companion(tmp_path / "Found.db")
        init_db(db_path=db)
        with get_session() as session:
            assert _attached(session)
            count = session.execute(text("SELECT count(*) FROM companion.notes")).scalar()
        assert count == 3

    def test_created_while_open_is_attached_by_the_next_session(self, tmp_path):
        db = _main_db(tmp_path / "Found.db")
        init_db(db_path=db)
        with get_session() as session:
            assert not _attached(session)
        companion.create(db)
        with get_session() as session:
            assert _attached(session)

    def test_session_for_attaches_it(self, tmp_path):
        db = _with_companion(tmp_path / "Found.db")
        with session_for(db) as session:
            assert _attached(session)

    def test_not_attached_without_the_flag(self, tmp_path, monkeypatch):
        db = _with_companion(tmp_path / "Found.db")
        monkeypatch.setattr(flags, "companion_db", False)
        init_db(db_path=db)
        with get_session() as session:
            assert not _attached(session)

    @pytest.mark.parametrize("damage", ["garbage", "foreign"])
    def test_main_database_works_with_an_unusable_companion(self, tmp_path, damage):
        db = _main_db(tmp_path / "Found.db")
        if damage == "garbage":
            companion.companion_path(db).write_bytes(b"x" * 4096)
        else:
            other = _with_companion(tmp_path / "Other.db")
            companion.companion_path(other).rename(companion.companion_path(db))

        init_db(db_path=db)
        with get_session() as session:
            assert not _attached(session)
            assert session.execute(text("SELECT count(*) FROM caches")).scalar() == 0

    def test_a_companion_that_fails_to_attach_is_not_tried_again(self, tmp_path, monkeypatch):
        db = _with_companion(tmp_path / "Found.db")
        calls = []

        def failing(dbapi_connection, db_path):
            calls.append(db_path)
            companion._set_status(db_path, Status.BROKEN)
            return False

        monkeypatch.setattr(companion, "attach", failing)
        init_db(db_path=db)
        for _ in range(3):
            with get_session() as session:
                assert session.execute(text("SELECT 1")).scalar() == 1
        assert len(calls) == 1


# ── Lifecycle in the database manager ─────────────────────────────────────────

@pytest.fixture
def mgr(tmp_path, monkeypatch):
    """A manager with an active database 'Home' and 'Trip' with a companion."""
    from opensak import settings_store as ss
    from opensak.db.manager import DatabaseManager

    db_dir = tmp_path / "dbs"
    home = _main_db(db_dir / "Home.db")
    trip = _with_companion(db_dir / "Trip.db")
    fresh = ss.SettingsStore()
    fresh._data = {
        "databases.dir": str(db_dir),
        "databases.list": [
            {"name": "Home", "path": str(home)},
            {"name": "Trip", "path": str(trip)},
        ],
        "databases.active": str(home),
    }
    fresh._path = tmp_path / "opensak.json"
    monkeypatch.setattr(ss, "_store", fresh)
    monkeypatch.setattr("opensak.config.get_app_data_dir", lambda: tmp_path)
    manager = DatabaseManager()
    manager.ensure_active_initialised()
    return manager


def _db(mgr, name):
    return next(db for db in mgr.databases if db.name == name)


class TestLifecycle:
    def test_rename_renames_the_companion(self, mgr, tmp_path):
        trip = _db(mgr, "Trip")
        old = trip.path
        mgr.rename(trip, "Holiday")

        assert trip.path.name == "Holiday.db"
        assert not companion.exists(old)
        assert _notes(trip.path) == 3

    def test_rename_of_the_active_database_keeps_it_attached(self, mgr):
        trip = _db(mgr, "Trip")
        mgr.switch_to(trip)
        mgr.rename(trip, "Holiday")
        with get_session() as session:
            assert _attached(session)

    def test_rename_refused_when_a_companion_is_in_the_way(self, mgr, tmp_path):
        trip = _db(mgr, "Trip")
        stray = companion.companion_path(tmp_path / "dbs" / "Holiday.db")
        stray.write_bytes(b"left over")
        with pytest.raises(ValueError):
            mgr.rename(trip, "Holiday")
        assert trip.path.name == "Trip.db"
        assert _notes(trip.path) == 3

    def test_rename_rolls_the_companion_back_if_the_database_fails(self, mgr, monkeypatch):
        trip = _db(mgr, "Trip")
        real_rename = Path.rename

        def rename(self, target):
            if self.name == "Trip.db":
                raise OSError("locked")
            return real_rename(self, target)

        monkeypatch.setattr(Path, "rename", rename)
        with pytest.raises(ValueError):
            mgr.rename(trip, "Holiday")
        assert companion.exists(trip.path)
        assert not companion.exists(trip.path.with_name("Holiday.db"))

    def test_move_moves_the_companion(self, mgr, tmp_path):
        trip = _db(mgr, "Trip")
        old = trip.path
        assert mgr.move_database(trip, tmp_path / "usb") is None

        assert trip.path == tmp_path / "usb" / "Trip.db"
        assert not companion.exists(old)
        assert _notes(trip.path) == 3

    def test_move_refused_when_a_companion_is_in_the_way(self, mgr, tmp_path):
        trip = _db(mgr, "Trip")
        (tmp_path / "usb").mkdir()
        companion.companion_path(tmp_path / "usb" / "Trip.db").write_bytes(b"left over")
        with pytest.raises(ValueError):
            mgr.move_database(trip, tmp_path / "usb")
        assert trip.path == tmp_path / "dbs" / "Trip.db"
        assert not (tmp_path / "usb" / "Trip.db").exists()

    def test_move_all_moves_the_companions(self, mgr, tmp_path):
        assert mgr.move_databases_to(tmp_path / "new", delete_originals=True) == []
        trip = _db(mgr, "Trip")
        assert trip.path.parent == tmp_path / "new"
        assert _notes(trip.path) == 3
        assert not companion.exists(tmp_path / "dbs" / "Trip.db")

    def test_copy_copies_the_companion_for_the_copy(self, mgr, tmp_path):
        trip = _db(mgr, "Trip")
        copy = mgr.copy_database(trip, "Trip copy", tmp_path / "usb" / "Trip copy.db")

        assert _notes(copy.path) == 3
        assert _notes(trip.path) == 3
        assert _uuid(copy.path) != _uuid(trip.path)
        assert companion.read_meta(copy.path)[companion.MAIN_UUID_KEY] == _uuid(copy.path)
        assert companion.read_meta(trip.path)[companion.MAIN_UUID_KEY] == _uuid(trip.path)
        assert companion.prepare(copy.path) is Status.OK

    def test_copy_without_companion_makes_none(self, mgr, tmp_path):
        copy = mgr.copy_database(_db(mgr, "Home"), "Home copy", tmp_path / "usb" / "Home copy.db")
        assert copy.path.is_file()
        assert not companion.exists(copy.path)

    def test_delete_deletes_the_companion(self, mgr, tmp_path):
        trip = _db(mgr, "Trip")
        path = trip.path
        mgr.delete_database(trip)
        assert not path.exists()
        assert companion.files(path) == []

    def test_remove_from_list_keeps_the_companion(self, mgr):
        trip = _db(mgr, "Trip")
        mgr.remove_from_list(trip)
        assert _notes(trip.path) == 3

    def test_a_companion_cannot_be_opened_as_a_database(self, mgr):
        path = companion.companion_path(_db(mgr, "Trip").path)
        with pytest.raises(ValueError, match="Trip.db"):
            mgr.open_database(path)
        assert len(mgr.databases) == 2

    def test_scan_does_not_offer_companions(self, mgr, tmp_path):
        found = find_unregistered_databases(tmp_path / "dbs", [])
        assert sorted(f.path.name for f in found) == ["Home.db", "Trip.db"]


# ── Welcome wizard: moving the install folder ─────────────────────────────────

def test_wizard_leaves_companions_to_the_database_move(tmp_path, monkeypatch):
    pytest.importorskip("pytestqt")
    from opensak import settings_store as ss
    from opensak.gui.dialogs.welcome_wizard import WelcomeWizard

    root = tmp_path / "install"
    db = _with_companion(root / "Trip.db")
    monkeypatch.setattr(ss.get_store(), "get",
                        lambda key, default=None: [{"name": "Trip", "path": str(db)}]
                        if key == "databases.list" else default)
    keep = WelcomeWizard._database_entries_to_keep(root, tmp_path / "elsewhere")
    assert companion.companion_path(db).resolve() in keep


# ── Backup and restore ────────────────────────────────────────────────────────

def _set(tmp_path: Path, databases, compress: bool):
    return write_backup_set(databases, KIND_MANUAL, folder=tmp_path / "backups",
                            now=NOW, compress=compress, macro_databases=[])


class TestBackup:
    @pytest.mark.parametrize("compress", [False, True])
    def test_set_includes_the_companion(self, tmp_path, compress):
        trip = _with_companion(tmp_path / "dbs" / "Trip.db")
        home = _main_db(tmp_path / "dbs" / "Home.db")
        result = _set(tmp_path, [SimpleNamespace(name="Trip", path=trip),
                                 SimpleNamespace(name="Home", path=home)], compress)

        entries = {e.name: e for e in result.backup_set.databases}
        assert entries["Trip"].companion == f"{COMPANIONS_DIRNAME}/Trip.companion.db3"
        assert entries["Trip"].companion_size_bytes > 0
        assert entries["Home"].companion is None
        assert result.skipped_companions == ()
        assert result.backup_set.format == 1

    def test_manifest_only_names_companions_that_exist(self, tmp_path):
        trip = _with_companion(tmp_path / "dbs" / "Trip.db")
        home = _main_db(tmp_path / "dbs" / "Home.db")
        backup = _set(tmp_path, [SimpleNamespace(name="Trip", path=trip),
                                 SimpleNamespace(name="Home", path=home)], False).backup_set
        data = json.loads((backup.path / "manifest.json").read_text(encoding="utf-8"))
        by_name = {d["name"]: d for d in data["databases"]}
        assert by_name["Trip"]["companion"]["file"] == "companions/Trip.companion.db3"
        assert "companion" not in by_name["Home"]

    def test_a_damaged_companion_is_reported_but_never_fails_the_backup(self, tmp_path):
        trip = _main_db(tmp_path / "dbs" / "Trip.db")
        companion.companion_path(trip).write_bytes(b"x" * 4096)
        result = _set(tmp_path, [SimpleNamespace(name="Trip", path=trip)], False)
        assert result.skipped_companions == ("Trip",)
        assert result.backup_set.databases[0].companion is None

    def test_a_set_from_before_companions_still_reads(self, tmp_path):
        home = _main_db(tmp_path / "dbs" / "Home.db")
        backup = _set(tmp_path, [SimpleNamespace(name="Home", path=home)], False).backup_set
        assert read_backup_set(backup.path).databases[0].companion is None


@pytest.fixture
def trip_manager(qapp, tmp_path):
    from opensak.db.manager import get_db_manager
    m = get_db_manager()
    path = tmp_path / "dbs" / "Trip.db"
    m.new_database("Trip", path)
    dispose_engine(path)
    ensure_seeded(path, "Trip")
    companion.create(path)
    _with_companion_rows(path)
    return m


def _with_companion_rows(path: Path) -> None:
    conn = sqlite3.connect(str(companion.companion_path(path)))
    conn.execute("CREATE TABLE notes (gc_code TEXT PRIMARY KEY, note TEXT)")
    conn.executemany("INSERT INTO notes VALUES (?, ?)", [("GC1", "a"), ("GC2", "b")])
    conn.commit()
    conn.close()


class TestRestore:
    @pytest.mark.parametrize("compress", [False, True])
    def test_restores_the_companion_next_to_the_database(self, trip_manager, tmp_path, compress):
        from opensak.backup.restore import restore_database

        trip = _db(trip_manager, "Trip")
        backup = _set(tmp_path, [trip], compress).backup_set
        info = restore_database(backup, backup.databases[0], today=TODAY)

        assert _notes(info.path) == 2
        assert companion.read_meta(info.path)[companion.MAIN_UUID_KEY] == _uuid(info.path)
        assert companion.prepare(info.path) is Status.OK

    def test_restores_without_companion_from_a_set_without_one(self, trip_manager, tmp_path):
        from opensak.backup.restore import restore_database

        trip = _db(trip_manager, "Trip")
        for f in companion.files(trip.path):
            f.unlink()
        backup = _set(tmp_path, [trip], False).backup_set
        info = restore_database(backup, backup.databases[0], today=TODAY)
        assert info.path.is_file()
        assert not companion.exists(info.path)

    def test_a_missing_companion_in_the_set_restores_nothing(self, trip_manager, tmp_path):
        from opensak.backup.restore import RestoreError, restore_database

        trip = _db(trip_manager, "Trip")
        backup = _set(tmp_path, [trip], False).backup_set
        (backup.path / backup.databases[0].companion).unlink()
        before = len(trip_manager.databases)
        with pytest.raises(RestoreError):
            restore_database(backup, backup.databases[0], today=TODAY)
        assert len(trip_manager.databases) == before
        assert not [p for p in trip.path.parent.iterdir() if "restored" in p.name]


# ── Back up on exit ───────────────────────────────────────────────────────────

class TestExitState:
    def test_database_without_companion_has_the_same_fingerprint(self, tmp_path):
        db = _main_db(tmp_path / "Found.db")
        assert set(es._database_fingerprint(db)) == {"db", "wal"}

    def test_a_changed_companion_counts_as_a_change(self, tmp_path, monkeypatch):
        db = _with_companion(tmp_path / "Found.db")
        listed = [SimpleNamespace(name="Found", path=db)]
        monkeypatch.setattr(es, "_listed_databases", lambda: list(listed))
        es.record_backup_state()
        assert not es.has_changes_since_backup()

        path = companion.companion_path(db)
        conn = sqlite3.connect(str(path))
        conn.execute("INSERT INTO notes VALUES ('GC99', 'new')")
        conn.commit()
        conn.close()
        # The insert fits the file's pages (same size); make the rewrite
        # visible on coarse clocks, as test_backup_exit_state_959 does.
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10 * 1_000_000_000))
        assert es.has_changes_since_backup()

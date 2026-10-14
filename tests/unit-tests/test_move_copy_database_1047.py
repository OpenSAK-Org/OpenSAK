# tests/unit-tests/test_move_copy_database_1047.py
"""
Issue #1047: Manage Databases can copy a database to a folder of the user's
choice, and move a single database (with its -wal/-shm sidecars) to another
folder. Uses real SQLite files and the real engine lifecycle.
"""

import json
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from opensak.db.database import get_session, init_db
from opensak.db.manager import DatabaseManager


@pytest.fixture
def mgr(tmp_path, qapp, monkeypatch):
    """A manager with an active database 'Home' and a second one 'Trip'."""
    from opensak import settings_store as ss

    db_dir = tmp_path / "dbs"
    db_dir.mkdir()
    home, trip = db_dir / "Home.db", db_dir / "Trip.db"
    for p in (trip, home):  # home last, so it is the open engine
        init_db(db_path=p)

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


def _uuid(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute(
            "SELECT value FROM db_settings WHERE key = 'db_uuid'"
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row[0])


def _seed_uuid(mgr, name) -> str:
    from opensak.db.db_settings import ensure_seeded
    db = _db(mgr, name)
    ensure_seeded(db.path, db.name)
    return _uuid(db.path)


# ── Copy to a chosen folder ───────────────────────────────────────────────────

class TestCopyToFolder:
    def test_copy_lands_in_chosen_folder_with_new_id(self, mgr, tmp_path):
        usb = tmp_path / "usb"
        usb.mkdir()
        orig_uuid = _seed_uuid(mgr, "Trip")

        copy = mgr.copy_database(_db(mgr, "Trip"), "Trip USB", usb / "Trip USB.db")

        assert copy.path == usb / "Trip USB.db"
        assert copy.path.exists()
        assert _db(mgr, "Trip USB").path == usb / "Trip USB.db"
        assert _uuid(copy.path) != orig_uuid
        assert _db(mgr, "Trip").path.exists()

    def test_copy_refuses_existing_target(self, mgr, tmp_path):
        usb = tmp_path / "usb"
        usb.mkdir()
        target = usb / "Trip USB.db"
        target.write_bytes(b"keep me")

        with pytest.raises(ValueError):
            mgr.copy_database(_db(mgr, "Trip"), "Trip USB", target)

        assert target.read_bytes() == b"keep me"
        assert "Trip USB" not in [db.name for db in mgr.databases]

    def test_copy_refuses_missing_source(self, mgr, tmp_path):
        trip = _db(mgr, "Trip")
        trip.path.unlink()
        with pytest.raises(FileNotFoundError):
            mgr.copy_database(trip, "Trip USB", tmp_path / "Trip USB.db")


# ── Move a single database ────────────────────────────────────────────────────

class TestMoveDatabase:
    def test_move_non_active_keeps_id_and_removes_source(self, mgr, tmp_path):
        usb = tmp_path / "usb"
        usb.mkdir()
        trip = _db(mgr, "Trip")
        old_path = trip.path
        orig_uuid = _seed_uuid(mgr, "Trip")

        warning = mgr.move_database(trip, usb)

        assert warning is None
        assert trip.path == usb / "Trip.db"
        assert trip.path.exists()
        assert not old_path.exists()
        assert _uuid(trip.path) == orig_uuid
        from opensak.settings_store import get_store
        saved = {e["name"]: e["path"] for e in get_store().get("databases.list")}
        assert saved["Trip"] == str(usb / "Trip.db")

    def test_move_active_database_keeps_app_usable(self, mgr, tmp_path):
        from opensak.db.models import Cache

        usb = tmp_path / "usb"
        usb.mkdir()
        home = _db(mgr, "Home")
        old_path = home.path

        assert mgr.move_database(home, usb) is None

        assert mgr.active is home
        assert home.path == usb / "Home.db"
        assert not old_path.exists()
        from opensak.settings_store import get_store
        assert get_store().get("databases.active") == str(usb / "Home.db")
        # The engine was reopened at the new path — no restart needed.
        with get_session() as session:
            assert session.query(Cache).count() == 0
            assert Path(session.get_bind().url.database).resolve() == (usb / "Home.db").resolve()

    def test_move_takes_sidecars_along(self, mgr, tmp_path, monkeypatch):
        # Seeding opens the file with sqlite3, which would checkpoint and
        # remove these stand-in sidecars before the move copies them.
        monkeypatch.setattr(mgr, "_seed_db_settings", lambda db: None)
        usb = tmp_path / "usb"
        usb.mkdir()
        trip = _db(mgr, "Trip")
        old_path = trip.path
        for suffix in ("-wal", "-shm"):
            Path(str(old_path) + suffix).write_bytes(b"sidecar" + suffix.encode())

        mgr.move_database(trip, usb)

        for suffix in ("-wal", "-shm"):
            assert Path(str(usb / "Trip.db") + suffix).read_bytes() == (
                b"sidecar" + suffix.encode()
            )
            assert not Path(str(old_path) + suffix).exists()

    def test_move_refuses_existing_target(self, mgr, tmp_path):
        usb = tmp_path / "usb"
        usb.mkdir()
        (usb / "Trip.db").write_bytes(b"keep me")
        trip = _db(mgr, "Trip")
        old_path = trip.path

        with pytest.raises(ValueError):
            mgr.move_database(trip, usb)

        assert trip.path == old_path
        assert old_path.exists()
        assert (usb / "Trip.db").read_bytes() == b"keep me"

    def test_move_refuses_same_folder(self, mgr):
        trip = _db(mgr, "Trip")
        with pytest.raises(ValueError):
            mgr.move_database(trip, trip.path.parent)

    def test_move_refuses_missing_source(self, mgr, tmp_path):
        trip = _db(mgr, "Trip")
        trip.path.unlink()
        with pytest.raises(FileNotFoundError):
            mgr.move_database(trip, tmp_path)

    def test_failed_copy_leaves_everything_unchanged(self, mgr, tmp_path, monkeypatch):
        import opensak.db.manager as manager_mod

        usb = tmp_path / "usb"
        usb.mkdir()
        trip = _db(mgr, "Trip")
        old_path = trip.path

        def broken_copy(src, dst):
            Path(dst).write_bytes(b"partial")
            raise OSError("disk full")

        monkeypatch.setattr(manager_mod.shutil, "copy2", broken_copy)
        with pytest.raises(ValueError):
            mgr.move_database(trip, usb)

        assert trip.path == old_path
        assert old_path.exists()
        assert not (usb / "Trip.db").exists()

    def test_undeletable_source_uses_new_path_and_warns(self, mgr, tmp_path, monkeypatch):
        usb = tmp_path / "usb"
        usb.mkdir()
        trip = _db(mgr, "Trip")
        old_path = trip.path

        real_unlink = Path.unlink

        def read_only_unlink(self, *args, **kwargs):
            if self.parent == old_path.parent:
                raise PermissionError("read-only file system")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", read_only_unlink)
        warning = mgr.move_database(trip, usb)

        assert warning  # db_move_original_left
        assert trip.path == usb / "Trip.db"
        assert old_path.exists()
        assert trip.path.exists()


# ── move_databases_to() still works through the shared code path ────────────

class TestMoveAllStillWorks:
    def test_moves_all_and_reopens_active(self, mgr, tmp_path):
        new_dir = tmp_path / "elsewhere"
        errors = mgr.move_databases_to(new_dir, delete_originals=True)

        assert errors == []
        assert {db.path for db in mgr.databases} == {
            new_dir / "Home.db", new_dir / "Trip.db",
        }
        assert not (tmp_path / "dbs" / "Home.db").exists()
        with get_session() as session:
            assert Path(session.get_bind().url.database).resolve() == (
                new_dir / "Home.db"
            ).resolve()

    def test_keep_originals(self, mgr, tmp_path):
        new_dir = tmp_path / "elsewhere"
        assert mgr.move_databases_to(new_dir, delete_originals=False) == []
        assert (tmp_path / "dbs" / "Trip.db").exists()
        assert (new_dir / "Trip.db").exists()

    def test_existing_target_is_reported_and_skipped(self, mgr, tmp_path):
        new_dir = tmp_path / "elsewhere"
        new_dir.mkdir()
        (new_dir / "Trip.db").write_bytes(b"keep me")

        errors = mgr.move_databases_to(new_dir, delete_originals=True)

        assert len(errors) == 1
        assert _db(mgr, "Trip").path == tmp_path / "dbs" / "Trip.db"
        assert _db(mgr, "Home").path == new_dir / "Home.db"

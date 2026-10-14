# tests/unit-tests/test_missing_database_1045.py
"""
Issue #1045: OpenSAK must start when the active database is unavailable
(e.g. on a removed USB drive), and opening/switching to a missing database
must never create folders or an empty database in its place.
"""

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from opensak.db.database import _make_engine, init_db
from opensak.db.manager import DatabaseManager


@pytest.fixture
def store(tmp_path, qapp, monkeypatch):
    """Fresh in-memory settings store with the database folder in tmp_path."""
    from opensak import settings_store as ss

    fresh = ss.SettingsStore()
    fresh._data = {"databases.dir": str(tmp_path / "dbs")}
    fresh._path = tmp_path / "opensak.json"
    monkeypatch.setattr(ss, "_store", fresh)
    monkeypatch.setattr("opensak.config.get_app_data_dir", lambda: tmp_path)
    return fresh


def _make_db(path: Path) -> Path:
    """Create a real, empty OpenSAK database at *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path=path)
    return path


def _removed_usb_path(tmp_path: Path) -> Path:
    return tmp_path / "media" / "KINGSTON" / "backup" / "databases" / "CarTrip.db"


# ── _make_engine / init_db ────────────────────────────────────────────────────

class TestNoFolderCreation:
    def test_make_engine_does_not_create_missing_folder(self, tmp_path):
        missing = _removed_usb_path(tmp_path)
        with pytest.raises(FileNotFoundError):
            _make_engine(missing)
        assert not (tmp_path / "media").exists()

    def test_init_db_on_missing_folder_raises_without_creating_it(self, tmp_path):
        missing = _removed_usb_path(tmp_path)
        with pytest.raises(FileNotFoundError):
            init_db(db_path=missing)
        assert not (tmp_path / "media").exists()

    def test_init_db_still_creates_file_in_existing_folder(self, tmp_path):
        path = tmp_path / "new.db"
        init_db(db_path=path)
        assert path.exists()


# ── Startup fallback ──────────────────────────────────────────────────────────

class TestStartupFallback:
    def test_falls_back_to_first_existing_database(self, store, tmp_path):
        local = _make_db(tmp_path / "dbs" / "Local.db")
        usb = _removed_usb_path(tmp_path)
        store._data["databases.list"] = [
            {"name": "CarTrip", "path": str(usb)},
            {"name": "Local", "path": str(local)},
        ]
        store._data["databases.active"] = str(usb)

        mgr = DatabaseManager()
        mgr.ensure_active_initialised()

        assert mgr.active is not None
        assert mgr.active.path == local
        assert store.get("databases.active") == str(local)
        assert not (tmp_path / "media").exists()

    def test_missing_database_stays_in_list(self, store, tmp_path):
        local = _make_db(tmp_path / "dbs" / "Local.db")
        usb = _removed_usb_path(tmp_path)
        store._data["databases.list"] = [
            {"name": "CarTrip", "path": str(usb)},
            {"name": "Local", "path": str(local)},
        ]
        store._data["databases.active"] = str(usb)

        mgr = DatabaseManager()
        mgr.ensure_active_initialised()

        names = [db.name for db in mgr.databases]
        assert "CarTrip" in names
        saved = [e["path"] for e in store.get("databases.list")]
        assert str(usb) in saved

    def test_reports_fallback_once(self, store, tmp_path):
        local = _make_db(tmp_path / "dbs" / "Local.db")
        usb = _removed_usb_path(tmp_path)
        store._data["databases.list"] = [
            {"name": "CarTrip", "path": str(usb)},
            {"name": "Local", "path": str(local)},
        ]
        store._data["databases.active"] = str(usb)

        mgr = DatabaseManager()
        mgr.ensure_active_initialised()

        fallback = mgr.take_startup_fallback()
        assert fallback is not None
        missing, used = fallback
        assert missing.name == "CarTrip"
        assert used.name == "Local"
        assert mgr.take_startup_fallback() is None

    def test_all_missing_uses_default_db(self, store, tmp_path):
        usb = _removed_usb_path(tmp_path)
        store._data["databases.list"] = [{"name": "CarTrip", "path": str(usb)}]
        store._data["databases.active"] = str(usb)

        mgr = DatabaseManager()
        mgr.ensure_active_initialised()

        default = tmp_path / "dbs" / "Default.db"
        assert mgr.active is not None
        assert mgr.active.path == default
        assert default.exists()
        assert "CarTrip" in [db.name for db in mgr.databases]
        assert mgr.take_startup_fallback() is not None

    def test_default_name_taken_gets_unique_name(self, store, tmp_path):
        usb = _removed_usb_path(tmp_path)
        store._data["databases.list"] = [{"name": "Default", "path": str(usb)}]
        store._data["databases.active"] = str(usb)

        mgr = DatabaseManager()
        mgr.ensure_active_initialised()

        assert mgr.active is not None
        assert mgr.active.name == "Default (2)"
        assert mgr.active.path == tmp_path / "dbs" / "Default.db"

    def test_fresh_install_creates_default_without_notice(self, store, tmp_path):
        mgr = DatabaseManager()
        mgr.ensure_active_initialised()

        assert mgr.active is not None
        assert mgr.active.exists
        assert mgr.take_startup_fallback() is None

    def test_existing_active_database_is_opened_without_notice(self, store, tmp_path):
        local = _make_db(tmp_path / "dbs" / "Local.db")
        store._data["databases.list"] = [{"name": "Local", "path": str(local)}]
        store._data["databases.active"] = str(local)

        mgr = DatabaseManager()
        mgr.ensure_active_initialised()

        assert mgr.active is not None
        assert mgr.active.path == local
        assert mgr.take_startup_fallback() is None


# ── Switching ─────────────────────────────────────────────────────────────────

class TestSwitchToMissing:
    def test_switch_to_missing_database_raises_and_keeps_active(self, store, tmp_path):
        local = _make_db(tmp_path / "dbs" / "Local.db")
        usb = _removed_usb_path(tmp_path)
        store._data["databases.list"] = [
            {"name": "Local", "path": str(local)},
            {"name": "CarTrip", "path": str(usb)},
        ]
        store._data["databases.active"] = str(local)

        mgr = DatabaseManager()
        mgr.ensure_active_initialised()
        cartrip = next(db for db in mgr.databases if db.name == "CarTrip")

        with pytest.raises(FileNotFoundError):
            mgr.switch_to(cartrip)

        assert mgr.active is not None
        assert mgr.active.path == local
        assert not (tmp_path / "media").exists()

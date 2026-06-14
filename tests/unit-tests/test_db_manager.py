"""tests/unit-tests/test_db_manager.py — DatabaseManager unit tests (QSettings mocked)."""

from datetime import datetime

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

pytest.importorskip("pytestqt")

from PySide6.QtCore import QSettings
from opensak.db.manager import DatabaseManager, DatabaseInfo


def _empty_settings_mock() -> MagicMock:
    """Return a QSettings mock representing an empty (first-run) configuration."""
    s = MagicMock(spec=QSettings)
    s.beginReadArray.return_value = 0  # no databases stored
    s.value.return_value = None        # no active_database stored
    return s


@pytest.fixture
def manager(tmp_path, qapp):
    """Isolated DatabaseManager: QSettings mocked, init_db no-op, tmp_path for files."""
    mock_s = _empty_settings_mock()
    with (
        patch("opensak.db.manager.QSettings", return_value=mock_s),
        patch("opensak.db.database.init_db"),
        patch("opensak.config.get_app_data_dir", return_value=tmp_path),
    ):
        yield DatabaseManager()


# ── Initialisation ────────────────────────────────────────────────────────────


class TestDatabaseManagerInit:
    def test_creates_default_database_when_settings_empty(self, manager):
        assert len(manager.databases) == 1
        assert manager.databases[0].name == "Default"

    def test_active_is_set_after_init(self, manager):
        assert manager.active is not None
        assert manager.active.name == "Default"

    def test_active_path_matches_active_info(self, manager):
        assert manager.active_path == manager.active.path

    def test_databases_property_returns_copy(self, manager):
        assert manager.databases is not manager.databases


# ── new_database ──────────────────────────────────────────────────────────────


class TestNewDatabase:
    def test_adds_entry_to_list(self, manager, tmp_path):
        manager.new_database("Alpha", tmp_path / "alpha.db")
        assert any(d.name == "Alpha" for d in manager.databases)

    def test_returns_database_info_instance(self, manager, tmp_path):
        info = manager.new_database("Beta", tmp_path / "beta.db")
        assert isinstance(info, DatabaseInfo)
        assert info.name == "Beta"

    def test_increments_database_count(self, manager, tmp_path):
        manager.new_database("Gamma", tmp_path / "gamma.db")
        assert len(manager.databases) == 2

    def test_rejects_duplicate_name(self, manager):
        with pytest.raises(ValueError, match="(?:Default|db_err_name_exists)"):
            manager.new_database("Default")

    def test_two_new_databases_are_distinct(self, manager, tmp_path):
        info1 = manager.new_database("X", tmp_path / "x.db")
        info2 = manager.new_database("Y", tmp_path / "y.db")
        assert info1 is not info2
        assert info1.name != info2.name


# ── rename ────────────────────────────────────────────────────────────────────


class TestRename:
    def test_renames_database(self, manager, tmp_path):
        info = manager.new_database("Original", tmp_path / "orig.db")
        manager.rename(info, "Renamed")
        assert info.name == "Renamed"

    def test_renamed_entry_visible_in_list(self, manager, tmp_path):
        info = manager.new_database("Old", tmp_path / "old.db")
        manager.rename(info, "New")
        assert any(d.name == "New" for d in manager.databases)
        assert not any(d.name == "Old" for d in manager.databases)

    def test_rejects_name_already_taken_by_another(self, manager, tmp_path):
        info = manager.new_database("Second", tmp_path / "second.db")
        with pytest.raises(ValueError, match="(?:Default|db_err_name_exists)"):
            manager.rename(info, "Default")

    def test_rename_to_same_name_is_allowed(self, manager):
        default = manager.databases[0]
        manager.rename(default, "Default")  # must not raise


# ── remove_from_list ──────────────────────────────────────────────────────────


class TestRemoveFromList:
    def test_removes_entry_from_list(self, manager, tmp_path):
        info = manager.new_database("Removable", tmp_path / "removable.db")
        manager.remove_from_list(info)
        assert info not in manager.databases

    def test_file_is_not_deleted(self, manager, tmp_path):
        db_path = tmp_path / "kept.db"
        db_path.touch()
        info = manager.new_database("Kept", db_path)
        manager.remove_from_list(info)
        assert db_path.exists()

    def test_refuses_to_remove_active_database(self, manager):
        with pytest.raises(ValueError, match="active"):
            manager.remove_from_list(manager.active)


# ── delete_database ───────────────────────────────────────────────────────────


class TestDeleteDatabase:
    def test_removes_entry_from_list(self, manager, tmp_path):
        db_path = tmp_path / "todelete.db"
        db_path.touch()
        info = manager.new_database("ToDelete", db_path)
        manager.delete_database(info)
        assert info not in manager.databases

    def test_deletes_file_from_disk(self, manager, tmp_path):
        db_path = tmp_path / "todelete2.db"
        db_path.touch()
        info = manager.new_database("ToDelete2", db_path)
        manager.delete_database(info)
        assert not db_path.exists()

    def test_refuses_to_delete_active_database(self, manager):
        with pytest.raises(ValueError, match="active"):
            manager.delete_database(manager.active)

    def test_missing_file_does_not_raise(self, manager, tmp_path):
        db_path = tmp_path / "ghost.db"
        info = manager.new_database("Ghost", db_path)
        manager.delete_database(info)  # file never existed — must not raise
        assert info not in manager.databases


# ── DatabaseInfo (pure logic) ─────────────────────────────────────────────────

class TestDatabaseInfo:
    def test_exists_reflects_file(self, tmp_path):
        p = tmp_path / "x.db"
        info = DatabaseInfo("X", p)
        assert info.exists is False
        p.touch()
        assert info.exists is True

    def test_size_mb(self, tmp_path):
        p = tmp_path / "x.db"
        p.write_bytes(b"a" * (1024 * 1024))
        assert DatabaseInfo("X", p).size_mb == pytest.approx(1.0, abs=0.01)

    def test_size_mb_zero_when_missing(self, tmp_path):
        assert DatabaseInfo("X", tmp_path / "missing.db").size_mb == 0.0

    def test_modified_returns_datetime(self, tmp_path):
        p = tmp_path / "x.db"
        p.touch()
        assert isinstance(DatabaseInfo("X", p).modified, datetime)

    def test_modified_none_when_missing(self, tmp_path):
        assert DatabaseInfo("X", tmp_path / "missing.db").modified is None

    def test_to_from_dict_roundtrip(self, tmp_path):
        p = tmp_path / "x.db"
        d = DatabaseInfo("X", p).to_dict()
        assert d == {"name": "X", "path": str(p)}
        restored = DatabaseInfo.from_dict(d)
        assert restored.name == "X"
        assert restored.path == p

    def test_repr(self, tmp_path):
        assert "DatabaseInfo" in repr(DatabaseInfo("X", tmp_path / "x.db"))


# ── open_database ─────────────────────────────────────────────────────────────

class TestOpenDatabase:
    def test_file_not_found_raises(self, manager, tmp_path):
        with pytest.raises(FileNotFoundError):
            manager.open_database(tmp_path / "nope.db")

    def test_opens_existing_file(self, manager, tmp_path):
        p = tmp_path / "ext.db"
        p.touch()
        info = manager.open_database(p)
        assert info.path == p
        assert info in manager.databases

    def test_returns_existing_entry_for_same_path(self, manager, tmp_path):
        p = tmp_path / "ext.db"
        p.touch()
        assert manager.open_database(p) is manager.open_database(p)

    def test_dedupes_name_collision(self, manager, tmp_path):
        manager.new_database("data", tmp_path / "a.db")
        p = tmp_path / "data.db"
        p.touch()
        assert manager.open_database(p).name == "data (2)"


# ── switch_to / copy_database / ensure_active_initialised ──────────────────────

class TestSwitchTo:
    def test_switch_sets_active(self, manager, tmp_path):
        info = manager.new_database("Other", tmp_path / "other.db")
        manager.switch_to(info)
        assert manager.active is info
        assert manager.active_path == info.path


class TestCopyDatabase:
    def test_copies_file_and_adds_entry(self, manager, tmp_path):
        src = manager.new_database("Src", tmp_path / "src.db")
        src.path.touch()
        copy = manager.copy_database(src, "Copy", tmp_path / "copy.db")
        assert copy.path.exists()
        assert copy in manager.databases

    def test_default_path_uses_app_data_dir(self, manager, tmp_path):
        src = manager.new_database("Src", tmp_path / "src.db")
        src.path.touch()
        copy = manager.copy_database(src, "CopyDefault")
        assert copy.path == tmp_path / "CopyDefault.db"
        assert copy.path.exists()

    def test_rejects_duplicate_name(self, manager, tmp_path):
        src = manager.new_database("Src", tmp_path / "src.db")
        src.path.touch()
        with pytest.raises(ValueError):
            manager.copy_database(src, "Default", tmp_path / "c.db")


class TestEnsureActiveInitialised:
    def test_initialises_active(self, manager):
        manager.ensure_active_initialised()  # init_db is patched; exercises the call path
        assert manager.active is not None

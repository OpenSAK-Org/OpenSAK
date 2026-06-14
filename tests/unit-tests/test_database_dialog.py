"""tests/unit-tests/test_database_dialog.py — database manager + new-database dialogs."""

from datetime import datetime
from pathlib import Path

import pytest
from unittest.mock import MagicMock

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QDialog, QInputDialog

from opensak.gui.dialogs import database_dialog as dd
from opensak.gui.dialogs.database_dialog import NewDatabaseDialog, DatabaseManagerDialog


class _DB:
    def __init__(self, name, path, exists=True, size_mb=1.5, modified=None):
        self.name = name
        self.path = Path(path)
        self.exists = exists
        self.size_mb = size_mb
        self.modified = modified or datetime(2024, 1, 2, 3, 4)

    def __eq__(self, other):
        return isinstance(other, _DB) and self.path == other.path

    def __hash__(self):
        return hash(self.path)


@pytest.fixture(autouse=True)
def app_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("opensak.config.get_app_data_dir", lambda: tmp_path)
    return tmp_path


# ── NewDatabaseDialog ───────────────────────────────────────────────────────────

class TestNewDatabaseDialog:
    def test_path_preview_folder_then_file(self, qtbot, tmp_path):
        dlg = NewDatabaseDialog()
        qtbot.addWidget(dlg)
        assert dlg._path_edit.text() == str(tmp_path)  # no name -> folder
        dlg._name_edit.setText("MyDB")
        assert dlg._path_edit.text() == str(tmp_path / "MyDB.db")

    def test_browse_sets_custom_path(self, qtbot, tmp_path, monkeypatch):
        dlg = NewDatabaseDialog()
        qtbot.addWidget(dlg)
        target = tmp_path / "sub"
        target.mkdir()
        monkeypatch.setattr(dd.QFileDialog, "getExistingDirectory", lambda *a, **k: str(target))
        dlg._name_edit.setText("DB")
        dlg._browse()
        assert dlg._custom_path == target
        assert dlg._path_edit.text() == str(target / "DB.db")

    def test_browse_cancel(self, qtbot, monkeypatch):
        dlg = NewDatabaseDialog()
        qtbot.addWidget(dlg)
        monkeypatch.setattr(dd.QFileDialog, "getExistingDirectory", lambda *a, **k: "")
        dlg._browse()
        assert dlg._custom_path is None

    def test_validate_requires_name(self, qtbot, monkeypatch):
        dlg = NewDatabaseDialog()
        qtbot.addWidget(dlg)
        warn = MagicMock()
        monkeypatch.setattr(dd.QMessageBox, "warning", warn)
        dlg._validate()
        warn.assert_called_once()
        assert dlg.result() != QDialog.DialogCode.Accepted

    def test_validate_accepts(self, qtbot):
        dlg = NewDatabaseDialog()
        qtbot.addWidget(dlg)
        dlg._name_edit.setText("Good")
        dlg._validate()
        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.name == "Good"

    def test_custom_path_property(self, qtbot, tmp_path):
        dlg = NewDatabaseDialog()
        qtbot.addWidget(dlg)
        assert dlg.custom_path is None  # no custom folder
        dlg._custom_path = tmp_path
        dlg._name_edit.setText("My DB!")        # illegal char sanitised
        assert dlg.custom_path == tmp_path / "My DB_.db"

    def test_custom_path_no_name(self, qtbot, tmp_path):
        dlg = NewDatabaseDialog()
        qtbot.addWidget(dlg)
        dlg._custom_path = tmp_path
        assert dlg.custom_path == tmp_path  # falls back to folder when name empty


# ── DatabaseManagerDialog ───────────────────────────────────────────────────────

@pytest.fixture
def manager(monkeypatch):
    active = _DB("Active", "/data/active.db")
    other = _DB("Other", "/data/other.db")
    missing = _DB("Gone", "/data/gone.db", exists=False)
    mgr = MagicMock()
    mgr.active = active
    mgr.databases = [active, other, missing]
    monkeypatch.setattr(dd, "get_db_manager", lambda: mgr)
    mgr._dbs = {"active": active, "other": other, "missing": missing}
    return mgr


@pytest.fixture
def dlg(qtbot, manager):
    d = DatabaseManagerDialog()
    qtbot.addWidget(d)
    return d


def _select(dlg, name):
    for row in range(dlg._list.count()):
        if dlg._list.item(row).data(dd.Qt.ItemDataRole.UserRole).name == name:
            dlg._list.setCurrentRow(row)
            return
    raise AssertionError(f"{name} not in list")


class TestManagerDialog:
    def test_list_populated_and_active_marked(self, dlg):
        assert dlg._list.count() == 3
        # active item carries a check mark
        texts = [dlg._list.item(i).text() for i in range(3)]
        assert any("✓" in t for t in texts)

    def test_selection_updates_info_and_buttons(self, dlg, manager):
        _select(dlg, "Other")
        assert dlg._info_name.text() == "Other"
        assert "MB" in dlg._info_size.text()
        assert dlg._btn_switch.isEnabled() is True   # non-active, exists
        assert dlg._btn_delete.isEnabled() is True

    def test_active_selection_disables_switch_delete(self, dlg):
        _select(dlg, "Active")
        assert dlg._btn_switch.isEnabled() is False
        assert dlg._btn_delete.isEnabled() is False
        assert dlg._btn_remove.isEnabled() is False

    def test_missing_db_shows_not_found(self, dlg):
        _select(dlg, "Gone")
        assert dlg._info_size.text() != ""
        assert dlg._btn_switch.isEnabled() is False  # file missing
        assert dlg._btn_copy.isEnabled() is False

    def test_switch_to_selected(self, dlg, manager, monkeypatch):
        monkeypatch.setattr(dd.QMessageBox, "information", MagicMock())
        emitted = []
        dlg.database_switched.connect(lambda db: emitted.append(db.name))
        _select(dlg, "Other")
        dlg._switch_to_selected()
        manager.switch_to.assert_called_once()
        assert emitted == ["Other"]

    def test_switch_noop_when_active(self, dlg, manager):
        _select(dlg, "Active")
        dlg._switch_to_selected()
        manager.switch_to.assert_not_called()

    def test_new_database_success(self, dlg, manager, monkeypatch):
        new_db = _DB("Fresh", "/data/fresh.db")
        manager.new_database.return_value = new_db

        class FakeNew:
            def __init__(self, parent=None):
                pass
            def exec(self):
                return 1
            name = "Fresh"
            custom_path = None
        monkeypatch.setattr(dd, "NewDatabaseDialog", FakeNew)
        monkeypatch.setattr(dd.QMessageBox, "information", MagicMock())
        monkeypatch.setattr("opensak.gui.settings.get_settings",
                            lambda: MagicMock())
        emitted = []
        dlg.database_switched.connect(lambda db: emitted.append(db.name))
        dlg._new_database()
        manager.new_database.assert_called_once()
        assert emitted == ["Fresh"]

    def test_new_database_value_error(self, dlg, manager, monkeypatch):
        manager.new_database.side_effect = ValueError("dup")

        class FakeNew:
            def __init__(self, parent=None):
                pass
            def exec(self):
                return 1
            name = "X"
            custom_path = None
        monkeypatch.setattr(dd, "NewDatabaseDialog", FakeNew)
        warn = MagicMock()
        monkeypatch.setattr(dd.QMessageBox, "warning", warn)
        dlg._new_database()
        warn.assert_called_once()

    def test_new_database_cancelled(self, dlg, manager, monkeypatch):
        class FakeNew:
            def __init__(self, parent=None):
                pass
            def exec(self):
                return 0
        monkeypatch.setattr(dd, "NewDatabaseDialog", FakeNew)
        dlg._new_database()
        manager.new_database.assert_not_called()

    def test_open_database_success(self, dlg, manager, monkeypatch):
        manager.open_database.return_value = _DB("Opened", "/data/opened.db")
        monkeypatch.setattr(dd.QFileDialog, "getOpenFileName",
                            lambda *a, **k: ("/data/opened.db", "f"))
        monkeypatch.setattr(dd.QMessageBox, "information", MagicMock())
        dlg._open_database()
        manager.open_database.assert_called_once()

    def test_open_database_cancel(self, dlg, manager, monkeypatch):
        monkeypatch.setattr(dd.QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
        dlg._open_database()
        manager.open_database.assert_not_called()

    def test_open_database_error(self, dlg, manager, monkeypatch):
        manager.open_database.side_effect = Exception("bad file")
        monkeypatch.setattr(dd.QFileDialog, "getOpenFileName",
                            lambda *a, **k: ("/data/x.db", "f"))
        warn = MagicMock()
        monkeypatch.setattr(dd.QMessageBox, "warning", warn)
        dlg._open_database()
        warn.assert_called_once()

    def test_copy_database(self, dlg, manager, monkeypatch):
        manager.copy_database.return_value = _DB("Copy", "/data/copy.db")
        monkeypatch.setattr(dlg, "_simple_input", lambda *a, **k: ("Copy", True))
        monkeypatch.setattr(dd.QMessageBox, "information", MagicMock())
        _select(dlg, "Other")
        dlg._copy_database()
        manager.copy_database.assert_called_once()

    def test_copy_database_error(self, dlg, manager, monkeypatch):
        manager.copy_database.side_effect = Exception("no space")
        monkeypatch.setattr(dlg, "_simple_input", lambda *a, **k: ("Copy", True))
        warn = MagicMock()
        monkeypatch.setattr(dd.QMessageBox, "warning", warn)
        _select(dlg, "Other")
        dlg._copy_database()
        warn.assert_called_once()

    def test_rename_database(self, dlg, manager, monkeypatch):
        monkeypatch.setattr(dlg, "_simple_input", lambda *a, **k: ("Renamed", True))
        _select(dlg, "Other")
        dlg._rename_database()
        manager.rename.assert_called_once()

    def test_rename_database_value_error(self, dlg, manager, monkeypatch):
        manager.rename.side_effect = ValueError("exists")
        monkeypatch.setattr(dlg, "_simple_input", lambda *a, **k: ("Renamed", True))
        warn = MagicMock()
        monkeypatch.setattr(dd.QMessageBox, "warning", warn)
        _select(dlg, "Other")
        dlg._rename_database()
        warn.assert_called_once()

    def test_remove_from_list(self, dlg, manager, monkeypatch):
        monkeypatch.setattr(dd.QMessageBox, "question",
                            lambda *a, **k: dd.QMessageBox.StandardButton.Yes)
        _select(dlg, "Other")
        dlg._remove_from_list()
        manager.remove_from_list.assert_called_once()

    def test_delete_active_guard(self, dlg, manager, monkeypatch):
        warn = MagicMock()
        monkeypatch.setattr(dd.QMessageBox, "warning", warn)
        _select(dlg, "Active")
        dlg._delete_database()
        warn.assert_called_once()
        manager.delete_database.assert_not_called()

    def test_delete_confirmed_with_folder_offer(self, dlg, manager, monkeypatch):
        manager.delete_database.return_value = Path("/data/emptyfolder")
        # first warning() = confirm -> Yes; question() = delete folder -> Yes
        monkeypatch.setattr(dd.QMessageBox, "warning",
                            lambda *a, **k: dd.QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(dd.QMessageBox, "question",
                            lambda *a, **k: dd.QMessageBox.StandardButton.Yes)
        _select(dlg, "Other")
        dlg._delete_database()
        manager.delete_database.assert_called_once()
        manager.delete_folder.assert_called_once()

    def test_delete_oserror(self, dlg, manager, monkeypatch):
        manager.delete_database.side_effect = OSError("locked")
        monkeypatch.setattr(dd.QMessageBox, "warning",
                            lambda *a, **k: dd.QMessageBox.StandardButton.Yes)
        _select(dlg, "Other")
        dlg._delete_database()  # OSError handled, no crash

    def test_simple_input(self, dlg, monkeypatch):
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("  typed  ", True))
        text, ok = dlg._simple_input("t", "l", "d")
        assert (text, ok) == ("typed", True)

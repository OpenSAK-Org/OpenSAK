# tests/unit-tests/test_db_combo_after_manager_1232.py — issue #1232.
#
# Deleting a database in Manage Databases left it in the toolbar database
# dropdown: the dialog only signals a switch or a rename, and nothing else
# reloaded the dropdown. It is now reloaded whenever the dialog closes, so
# deleting, removing from the list, opening, scanning, copying and moving
# all show up there.

import pytest

pytest.importorskip("pytestqt")

from opensak.gui.mainwindow import MainWindow


@pytest.fixture(autouse=True)
def _quiet_startup(monkeypatch):
    monkeypatch.setattr(MainWindow, "_initial_load", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_update_background", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_setup_complete", lambda self: None)


@pytest.fixture
def setup(qtbot, tmp_path, monkeypatch):
    import opensak.db.manager as mgr_module
    from opensak.db.database import init_db
    from opensak.db.manager import DatabaseInfo
    from opensak.lang import load_language
    from tests.data import make_fake_manager

    load_language("en")

    db_path = tmp_path / "active_1232.db"
    init_db(db_path=db_path)
    manager = make_fake_manager(db_path, name="Active")
    other = DatabaseInfo("Doomed", tmp_path / "doomed_1232.db")
    known = [manager.active, other]
    # The fake manager lists only the active database; give it a list the
    # test can change, as the real dialog's actions change the manager's.
    monkeypatch.setattr(type(manager), "databases", property(lambda _self: list(known)))
    monkeypatch.setattr(mgr_module, "_manager", manager)

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)

    yield win, known, other

    win.close()
    mgr_module._manager = None


def _combo_names(win) -> list[str]:
    return [win._db_combo.itemText(i) for i in range(win._db_combo.count())]


def _fake_dialog(action):
    """A DatabaseManagerDialog stand-in whose exec() runs *action* — like a
    user deleting or adding a database — and emits nothing."""
    from PySide6.QtCore import QObject, Signal

    class _Dialog(QObject):
        database_switched = Signal(object)
        database_renamed = Signal(object)

        def __init__(self, parent=None):
            super().__init__()

        def exec(self):
            action()
            return 0

    return _Dialog


class TestDbComboAfterManager:
    def test_deleted_database_leaves_the_dropdown(self, setup, monkeypatch):
        win, known, other = setup
        win._reload_db_combo()
        assert _combo_names(win) == ["Active", "Doomed"]

        import opensak.gui.dialogs.database_dialog as dd
        monkeypatch.setattr(dd, "DatabaseManagerDialog", _fake_dialog(lambda: known.remove(other)))
        win._open_db_manager()

        assert _combo_names(win) == ["Active"]

    def test_added_database_appears_in_the_dropdown(self, setup, monkeypatch, tmp_path):
        from opensak.db.manager import DatabaseInfo

        win, known, other = setup
        known.remove(other)
        win._reload_db_combo()
        assert _combo_names(win) == ["Active"]

        import opensak.gui.dialogs.database_dialog as dd
        added = DatabaseInfo("Opened", tmp_path / "opened_1232.db")
        monkeypatch.setattr(dd, "DatabaseManagerDialog", _fake_dialog(lambda: known.append(added)))
        win._open_db_manager()

        assert _combo_names(win) == ["Active", "Opened"]

    def test_active_database_stays_selected(self, setup, monkeypatch):
        win, known, other = setup
        import opensak.gui.dialogs.database_dialog as dd
        monkeypatch.setattr(dd, "DatabaseManagerDialog", _fake_dialog(lambda: known.remove(other)))
        win._open_db_manager()
        assert win._db_combo.currentText() == "Active"

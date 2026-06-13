"""tests/e2e-tests/test_e2e_db_manager.py — database manager dialog scenarios."""

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt


# ── Helper ─────────────────────────────────────────────────────────────────────


def _open_db_dialog(window, qtbot):
    """Open DatabaseManagerDialog via the main window action (non-blocking)."""
    from opensak.gui.dialogs.database_dialog import DatabaseManagerDialog

    dlg = DatabaseManagerDialog(window)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    return dlg


# ── Open / structure ───────────────────────────────────────────────────────────


def test_db_manager_opens_without_crash(seeded_window, qtbot):
    """DatabaseManagerDialog opens cleanly and is visible."""
    dlg = _open_db_dialog(seeded_window, qtbot)
    assert dlg.isVisible()
    dlg.close()


def test_db_manager_lists_active_database(seeded_window, qtbot):
    """The list widget contains exactly one entry — the seeded test database."""
    dlg = _open_db_dialog(seeded_window, qtbot)

    assert dlg._list.count() == 1
    item_text = dlg._list.item(0).text()
    assert "E2ETest" in item_text

    dlg.close()


def test_db_manager_info_panel_shows_name(seeded_window, qtbot):
    """Selecting the first database entry populates the info panel with its name."""
    dlg = _open_db_dialog(seeded_window, qtbot)

    dlg._list.setCurrentRow(0)
    qtbot.wait(50)

    assert dlg._info_name.text() == "E2ETest"

    dlg.close()


def test_db_manager_info_panel_shows_path(seeded_window, qtbot):
    """The info panel path label is non-empty after selection."""
    dlg = _open_db_dialog(seeded_window, qtbot)

    dlg._list.setCurrentRow(0)
    qtbot.wait(50)

    assert dlg._info_path.text() not in ("", "—")

    dlg.close()


# ── Switch button ──────────────────────────────────────────────────────────────


def test_db_switch_button_disabled_for_active_db(seeded_window, qtbot):
    """
    The Switch button is disabled when the selected entry is the currently
    active database — you cannot switch to the DB you are already on.
    This is the correct application behavior (line 226 of database_dialog.py:
    setEnabled(... and not is_active and ...)).
    """
    dlg = _open_db_dialog(seeded_window, qtbot)

    dlg._list.setCurrentRow(0)
    qtbot.wait(30)

    assert not dlg._btn_switch.isEnabled()

    dlg.close()


# ── New database dialog ────────────────────────────────────────────────────────


def test_new_database_dialog_opens(seeded_window, qtbot, monkeypatch):
    """
    Clicking 'New' opens the NewDatabaseDialog.  We intercept it via
    monkeypatch so it auto-rejects rather than blocking the test.
    """
    from opensak.gui.dialogs import database_dialog as db_mod

    opened = []

    class _AutoReject:
        def __init__(self, *a, **kw):
            opened.append(True)

        def exec(self):
            return False  # Rejected

    monkeypatch.setattr(db_mod, "NewDatabaseDialog", _AutoReject)

    dlg = _open_db_dialog(seeded_window, qtbot)
    qtbot.mouseClick(dlg._btn_new, Qt.MouseButton.LeftButton)
    qtbot.wait(50)

    assert opened, "NewDatabaseDialog was never instantiated"

    dlg.close()

"""tests/unit-tests/test_macro_toolbar_1271.py — the Macros toolbar (#1271).

Covers the stored toolbar (opensak.macro.toolbar), the "On toolbar" check
box in the Macro manager, and the main window side: one button per macro,
disabled buttons with the reason, running a macro from its button (with
Shift), the right-click menu, and when the toolbar is shown.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from opensak.macro.toolbar import (
    move_on_toolbar, on_toolbar, set_on_toolbar, toolbar_macros,
)


def _lua(path: Path, text: str = "print(1)\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _english():
    from opensak.lang import load_language
    load_language("en")


@pytest.fixture
def macros_dir(tmp_path, monkeypatch):
    d = tmp_path / "macros"
    d.mkdir()
    monkeypatch.setattr("opensak.config.get_macros_dir", lambda: d)
    return d.resolve()


# ── Stored toolbar ───────────────────────────────────────────────────────────

def test_stored_relative_to_macros_folder_in_button_order(macros_dir):
    from opensak.settings_store import get_store
    a, b = _lua(macros_dir / "sub" / "a.lua"), _lua(macros_dir / "b.lua")
    set_on_toolbar(b, True)
    set_on_toolbar(a, True)
    set_on_toolbar(b, True)                         # already on: stays where it is
    assert get_store().get("macros.toolbar") == ["b.lua", "sub/a.lua"]
    assert toolbar_macros() == [b, a]
    assert on_toolbar(a) and on_toolbar(b)


def test_outside_macros_folder_and_removal(macros_dir, tmp_path):
    from opensak.settings_store import get_store
    other = _lua(tmp_path / "elsewhere" / "o.lua")
    set_on_toolbar(other, True)
    assert toolbar_macros() == [other.resolve()]
    set_on_toolbar(other, False)
    assert not on_toolbar(other)
    assert get_store().get("macros.toolbar") is None


def test_missing_macro_is_left_out_but_kept(macros_dir):
    a = _lua(macros_dir / "a.lua")
    gone = _lua(macros_dir / "gone.lua")
    set_on_toolbar(gone, True)
    set_on_toolbar(a, True)
    gone.unlink()
    assert toolbar_macros() == [a]
    _lua(gone)                                      # back again: back on the toolbar
    assert toolbar_macros() == [gone, a]


def test_bad_store_values_are_ignored(macros_dir):
    from opensak.settings_store import get_store
    a = _lua(macros_dir / "a.lua")
    get_store().set("macros.toolbar", {"a.lua": True})
    assert toolbar_macros() == []
    get_store().set("macros.toolbar", ["a.lua", 7, "", "a.lua"])
    assert toolbar_macros() == [a]


def test_move_skips_missing_macros_and_stops_at_the_ends(macros_dir):
    from opensak.settings_store import get_store
    a, gone, b, c = (_lua(macros_dir / f"{n}.lua") for n in ("a", "gone", "b", "c"))
    for m in (a, gone, b, c):
        set_on_toolbar(m, True)
    gone.unlink()
    assert move_on_toolbar(b, -1)                   # past the missing one
    assert toolbar_macros() == [b, a, c]
    assert get_store().get("macros.toolbar") == ["b.lua", "gone.lua", "a.lua", "c.lua"]
    assert not move_on_toolbar(b, -1)
    assert not move_on_toolbar(c, 1)
    assert not move_on_toolbar(gone, 1)
    assert move_on_toolbar(a, 1)
    assert toolbar_macros() == [b, c, a]


pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402

from opensak.gui.dialogs import macro_manager as mm  # noqa: E402


# ── Macro manager: "On toolbar" ──────────────────────────────────────────────

@pytest.fixture
def make_manager(qtbot, macros_dir):
    def make(**kwargs):
        d = mm.MacroManagerDialog(host=MagicMock(), **kwargs)
        qtbot.addWidget(d)
        return d
    return make


def test_check_box_follows_and_sets_the_store(make_manager, macros_dir):
    a, b = _lua(macros_dir / "a.lua"), _lua(macros_dir / "b.lua")
    set_on_toolbar(b, True)
    changed = MagicMock()
    d = make_manager(toolbar_changed=changed)
    item_a, item_b = d._tree.topLevelItem(0), d._tree.topLevelItem(1)
    assert item_a.checkState(mm.COL_TOOLBAR) == Qt.CheckState.Unchecked
    assert item_b.checkState(mm.COL_TOOLBAR) == Qt.CheckState.Checked
    changed.assert_not_called()                     # filling the list isn't a change

    item_a.setCheckState(mm.COL_TOOLBAR, Qt.CheckState.Checked)
    assert toolbar_macros() == [b, a]
    changed.assert_called_once()
    item_b.setCheckState(mm.COL_TOOLBAR, Qt.CheckState.Unchecked)
    assert toolbar_macros() == [a]
    assert changed.call_count == 2

    d.reload()
    assert changed.call_count == 2


def test_other_columns_are_not_the_toolbar(make_manager, macros_dir):
    _lua(macros_dir / "a.lua")
    changed = MagicMock()
    d = make_manager(toolbar_changed=changed)
    d._tree.topLevelItem(0).setText(mm.COL_DESCRIPTION, "x")
    changed.assert_not_called()


# ── Main window ──────────────────────────────────────────────────────────────

@pytest.fixture
def window(qtbot, tmp_path, monkeypatch, macros_dir):
    import opensak.db.manager as mgr_module
    from opensak.db.database import init_db
    from opensak.gui.mainwindow import MainWindow
    from tests.data import make_fake_manager

    monkeypatch.setattr(MainWindow, "_initial_load", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_update_background", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_setup_complete", lambda self: None)
    monkeypatch.setattr("opensak.utils.flags.lua_macros", True)
    db_path = tmp_path / "macro_toolbar.db"
    init_db(db_path=db_path)
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="MacroBar"))

    win = MainWindow()
    qtbot.addWidget(win)
    yield win
    win.close()
    mgr_module._manager = None


def _buttons(win) -> list[str]:
    return [a.text() for a in win._macro_toolbar_actions]


def test_empty_toolbar_is_hidden(window):
    tb = window._macro_toolbar
    assert window._macro_toolbar_actions == []
    assert not tb.isVisibleTo(window)
    assert not tb.toggleViewAction().isVisible()
    assert window.toolBarArea(tb) == window.toolBarArea(window._search_toolbar)


def test_buttons_follow_the_store(window, macros_dir):
    a = _lua(macros_dir / "a.lua", "-- Name: Macro A\n-- Description: Does A\n")
    b = _lua(macros_dir / "b.lua")
    set_on_toolbar(a, True)
    set_on_toolbar(b, True)
    window._macro_toolbar_changed()
    assert _buttons(window) == ["Macro A", "b"]
    assert window._macro_toolbar.isVisibleTo(window)
    assert window._macro_toolbar.toggleViewAction().isVisible()
    assert window._macro_toolbar_actions[0].toolTip() == "Does A"
    set_on_toolbar(a, False)
    set_on_toolbar(b, False)
    window._macro_toolbar_changed()
    assert _buttons(window) == []
    assert not window._macro_toolbar.isVisibleTo(window)


def test_new_button_shows_a_toolbar_the_user_hid(window, macros_dir):
    a, b = _lua(macros_dir / "a.lua"), _lua(macros_dir / "b.lua")
    set_on_toolbar(a, True)
    window._macro_toolbar_changed()
    window._macro_toolbar.setVisible(False)         # hidden via the toolbar menu
    window._refresh_macro_toolbar()                 # nothing new: stays hidden
    assert not window._macro_toolbar.isVisibleTo(window)
    set_on_toolbar(b, True)
    window._macro_toolbar_changed()
    assert window._macro_toolbar.isVisibleTo(window)


def test_tooltip_has_shortcut_and_name_follows_the_file(window, macros_dir):
    from opensak.macro.shortcuts import set_macro_shortcut
    a = _lua(macros_dir / "a.lua", "-- Name: Old\n-- Description: Desc\n")
    set_on_toolbar(a, True)
    set_macro_shortcut(a, "Ctrl+Alt+A")
    window._macro_toolbar_changed()
    assert window._macro_toolbar_actions[0].toolTip().startswith("Desc (")
    _lua(a, "-- Name: New & better\n")
    window._refresh_macro_toolbar()                 # as when the window gets active again
    (act,) = window._macro_toolbar_actions
    assert act.text() == "New && better"


def test_long_name_is_shortened(window, macros_dir):
    name = "A rather long macro name that goes on"
    a = _lua(macros_dir / "a.lua", f"-- Name: {name}\n-- Description: D\n")
    set_on_toolbar(a, True)
    window._macro_toolbar_changed()
    (act,) = window._macro_toolbar_actions
    assert act.text().endswith("…") and len(act.text()) <= 30
    assert act.toolTip() == f"{name}\nD"


def test_newer_api_macro_is_disabled_with_reason(window, macros_dir):
    from opensak.macro.runtime import API_VERSION
    a = _lua(macros_dir / "a.lua", f"-- Requires-API: {API_VERSION + 1}\n")
    set_on_toolbar(a, True)
    window._macro_toolbar_changed()
    (act,) = window._macro_toolbar_actions
    assert not act.isEnabled()
    assert str(API_VERSION + 1) in act.toolTip()


def test_no_buttons_without_lua_macros(window, macros_dir, monkeypatch):
    set_on_toolbar(_lua(macros_dir / "a.lua"), True)
    monkeypatch.setattr("opensak.utils.flags.lua_macros", False)
    window._macro_toolbar_changed()
    assert window._macro_toolbar_actions == []
    assert not window._macro_toolbar.isVisibleTo(window)


@pytest.mark.parametrize("held, printed", [(True, "true"), (False, "false")])
def test_button_runs_the_macro_with_shift(window, macros_dir, monkeypatch, held, printed):
    a = _lua(macros_dir / "s.lua", "print(opensak.shift_key())\n")
    set_on_toolbar(a, True)
    window._macro_toolbar_changed()
    monkeypatch.setattr(mm, "shift_held", lambda: held)
    window._macro_toolbar_actions[0].trigger()
    assert window._macro_manager._output.toPlainText().splitlines()[1] == printed
    assert "s.lua" in window._statusbar.currentMessage()


def _menu_action(menu, key):
    from opensak.lang import tr
    return next(a for a in menu.actions() if a.text() == tr(key))


def test_menu_moves_edits_and_removes(window, macros_dir, monkeypatch, qtbot):
    a, b = _lua(macros_dir / "a.lua"), _lua(macros_dir / "b.lua")
    set_on_toolbar(a, True)
    set_on_toolbar(b, True)
    window._macro_toolbar_changed()

    menu = window._macro_toolbar_menu(a)
    assert not _menu_action(menu, "macro_toolbar_move_left").isEnabled()
    _menu_action(menu, "macro_toolbar_move_right").trigger()
    assert _buttons(window) == ["a", "b"]           # rebuilt after the menu handler
    qtbot.waitUntil(lambda: _buttons(window) == ["b", "a"])

    menu = window._macro_toolbar_menu(a)
    assert not _menu_action(menu, "macro_toolbar_move_right").isEnabled()
    edit = MagicMock()
    monkeypatch.setattr(window, "_edit_macro", edit)
    _menu_action(menu, "edit").trigger()
    edit.assert_called_once_with(a)

    manager = window._macro_manager_instance()
    window._macro_toolbar_menu(b).actions()[-1].trigger()      # Remove from toolbar
    qtbot.waitUntil(lambda: _buttons(window) == ["a"])
    assert toolbar_macros() == [a]
    row = next(manager._tree.topLevelItem(i) for i in range(2)
               if manager._tree.topLevelItem(i).text(mm.COL_NAME) == "b")
    assert row.checkState(mm.COL_TOOLBAR) == Qt.CheckState.Unchecked


def test_manager_check_box_updates_the_window(window, macros_dir):
    a = _lua(macros_dir / "a.lua")
    manager = window._macro_manager_instance()
    manager.reload()
    manager._tree.topLevelItem(0).setCheckState(mm.COL_TOOLBAR, Qt.CheckState.Checked)
    assert _buttons(window) == ["a"]
    assert toolbar_macros() == [a]


def test_right_click_on_the_button_itself(window, macros_dir, monkeypatch, qtbot):
    """Remove from toolbar, chosen in the menu of the button that goes."""
    from PySide6.QtCore import QPoint
    a, b = _lua(macros_dir / "a.lua"), _lua(macros_dir / "b.lua")
    set_on_toolbar(a, True)
    set_on_toolbar(b, True)
    window._macro_toolbar_changed()
    button = window._macro_toolbar.widgetForAction(window._macro_toolbar_actions[0])
    real_menu = window._macro_toolbar_menu

    class Menu:                     # QMenu.exec can't be patched on the class
        def __init__(self, path):
            self.menu = real_menu(path)

        def exec(self, _pos):
            self.menu.actions()[-1].trigger()       # Remove from toolbar

    monkeypatch.setattr(window, "_macro_toolbar_menu", Menu)
    button.customContextMenuRequested.emit(QPoint(1, 1))
    qtbot.waitUntil(lambda: _buttons(window) == ["b"])
    assert toolbar_macros() == [b]

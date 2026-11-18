"""tests/unit-tests/test_cleared_shortcut_1336.py — issue #1336.

Clearing a key in Help → Keyboard Shortcuts removed the setting, and at the
next start "no setting" meant "use the default", so the default key came
back. A removed key is now saved as an empty value and stays removed until
the user sets a key again or clicks Reset all.

A restart is simulated by building a new MainWindow on the same saved
settings.
"""

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtGui import QAction, QKeySequence  # noqa: E402

from opensak.gui.dialogs.shortcuts_dialog import ShortcutsDialog  # noqa: E402


@pytest.fixture(autouse=True)
def _english():
    from opensak.lang import load_language
    load_language("en")


class _FakeSettings:
    """Stands in for QSettings: only what the tests put in it, and it
    survives a new MainWindow like the real settings file does."""
    values: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def remove(self, key):
        self.values.pop(key, None)


@pytest.fixture
def settings(monkeypatch):
    import PySide6.QtCore
    _FakeSettings.values = {}
    monkeypatch.setattr(PySide6.QtCore, "QSettings", _FakeSettings)
    return _FakeSettings.values


@pytest.fixture
def make_window(qtbot, tmp_path, monkeypatch, settings):
    import opensak.db.manager as mgr_module
    from opensak.db.database import init_db
    from opensak.gui.mainwindow import MainWindow
    from tests.data import make_fake_manager

    monkeypatch.setattr(MainWindow, "_check_update_background", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_setup_complete", lambda self: None)
    db_path = tmp_path / "cleared_shortcut_1336.db"
    init_db(db_path=db_path)
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="Keys1336"))
    windows = []

    def _make():
        win = MainWindow()
        qtbot.addWidget(win)
        windows.append(win)
        return win

    yield _make
    for win in windows:
        win.close()
    mgr_module._manager = None


def _ok_in_dialog(qtbot, win, monkeypatch, change):
    """Open Keyboard Shortcuts, let *change* edit the dialog, click OK."""
    dlg = ShortcutsDialog(win._shortcut_registry, win)
    qtbot.addWidget(dlg)
    change(dlg)
    monkeypatch.setattr(dlg, "exec", lambda: True)
    monkeypatch.setattr("opensak.gui.dialogs.shortcuts_dialog.ShortcutsDialog",
                        lambda *a, **k: dlg)
    win._open_shortcuts()


def _editor(dlg, key):
    keys = [k for k, _label, _acts in dlg._registry]
    return dlg._editors[keys.index(key)]


def _clear(key):
    return lambda dlg: _editor(dlg, key).setKeySequence(QKeySequence())


def _key(act) -> str:
    return act.shortcut().toString(QKeySequence.SequenceFormat.PortableText)


@pytest.mark.parametrize("key, action, default", [
    ("refresh", "_act_refresh", "F5"),
    ("clear_filter", "_act_clear_filter", "Esc"),
    # User-flag keys get their default in _apply_user_flag_shortcut_defaults() (#1314).
    ("show_flagged", "_act_show_flagged", "F8"),
    ("clear_flags", "_act_clear_flags", "F7"),
])
def test_a_cleared_key_stays_cleared_after_a_restart(
        qtbot, make_window, monkeypatch, settings, key, action, default):
    win = make_window()
    assert _key(getattr(win, action)) == default

    _ok_in_dialog(qtbot, win, monkeypatch, _clear(key))
    assert _key(getattr(win, action)) == ""
    assert settings[f"shortcuts/{key}"] == ""

    restarted = make_window()
    assert _key(getattr(restarted, action)) == ""


def test_other_keys_keep_their_defaults(qtbot, make_window, monkeypatch):
    win = make_window()
    _ok_in_dialog(qtbot, win, monkeypatch, _clear("refresh"))
    restarted = make_window()
    assert _key(restarted._act_import) == "Ctrl+I"
    assert _key(restarted._act_show_flagged) == "F8"


def test_a_key_set_again_after_clearing_is_kept(qtbot, make_window, monkeypatch, settings):
    win = make_window()
    _ok_in_dialog(qtbot, win, monkeypatch, _clear("refresh"))
    _ok_in_dialog(qtbot, win, monkeypatch,
                  lambda dlg: _editor(dlg, "refresh").setKeySequence(QKeySequence("Ctrl+R")))
    assert settings["shortcuts/refresh"] == "Ctrl+R"
    assert _key(make_window()._act_refresh) == "Ctrl+R"


def test_reset_all_brings_a_cleared_key_back(qtbot, make_window, monkeypatch):
    win = make_window()
    _ok_in_dialog(qtbot, win, monkeypatch, _clear("show_flagged"))
    _ok_in_dialog(qtbot, win, monkeypatch, lambda dlg: dlg._reset_all())
    assert _key(win._act_show_flagged) == "F8"
    assert _key(make_window()._act_show_flagged) == "F8"


def test_a_key_left_to_a_macro_is_not_saved_as_cleared(qtbot, make_window, monkeypatch, settings):
    """#1314: Show flagged caches only has no key while a macro uses F8.
    Clicking OK without touching it must not store it as removed, so the
    action gets F8 back when the macro no longer has the key."""
    win = make_window()
    macro = QAction("Flagged macro", win)
    macro.setShortcut(QKeySequence("F8"))
    win._macro_shortcut_actions = [macro]
    win._act_show_flagged.setShortcut(QKeySequence())
    win._apply_user_flag_shortcut_defaults()
    assert _key(win._act_show_flagged) == ""

    _ok_in_dialog(qtbot, win, monkeypatch, lambda dlg: None)
    assert "shortcuts/show_flagged" not in settings
    assert _key(make_window()._act_show_flagged) == "F8"

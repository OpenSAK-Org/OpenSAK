"""tests/unit-tests/test_shortcut_reset_defaults.py

Help → Keyboard Shortcuts → "Reset all" takes its keys from _DEFAULTS in
shortcuts_dialog.py, while the actions get their startup keys where they
are created in mainwindow.py. Clear filter (Esc), Maximize map (F11) and
Pop-out map (Ctrl+Shift+M) were missing from _DEFAULTS, so "Reset all" +
OK left them with no key until the next start.

These tests tie the two together: every action in the Keyboard Shortcuts
dialog has a default, and it is the key the action starts with.
"""

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtGui import QKeySequence  # noqa: E402

from opensak.gui.dialogs.shortcuts_dialog import _DEFAULTS, ShortcutsDialog  # noqa: E402


@pytest.fixture(autouse=True)
def _english():
    from opensak.lang import load_language
    load_language("en")


class _FakeSettings:
    """Stands in for QSettings, so no saved keys from a real settings file
    get in the way."""
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
def window(qtbot, tmp_path, monkeypatch):
    import PySide6.QtCore
    import opensak.db.manager as mgr_module
    from opensak.db.database import init_db
    from opensak.gui.mainwindow import MainWindow
    from tests.data import make_fake_manager

    _FakeSettings.values = {}
    monkeypatch.setattr(PySide6.QtCore, "QSettings", _FakeSettings)
    monkeypatch.setattr(MainWindow, "_check_update_background", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_setup_complete", lambda self: None)
    db_path = tmp_path / "shortcut_defaults.db"
    init_db(db_path=db_path)
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="Keys"))
    win = MainWindow()
    qtbot.addWidget(win)
    yield win
    win.close()
    mgr_module._manager = None


def test_every_action_in_the_dialog_has_a_default(window):
    missing = [key for key, _label, _acts in window._shortcut_registry if key not in _DEFAULTS]
    assert missing == []


def test_no_default_without_an_action(window):
    registered = {key for key, _label, _acts in window._shortcut_registry}
    assert set(_DEFAULTS) - registered == set()


def test_actions_start_with_their_default(window):
    for key, _label, actions in window._shortcut_registry:
        for act in actions:
            assert act.shortcut() == QKeySequence(_DEFAULTS[key]), key


@pytest.mark.parametrize("key, action, default", [
    ("clear_filter", "_act_clear_filter", "Escape"),
    ("maximize_map", "_act_maximize_map", "F11"),
    ("popout_map", "_act_popout_map", "Ctrl+Shift+M"),
])
def test_reset_all_keeps_the_map_and_clear_filter_keys(qtbot, window, monkeypatch, key, action, default):
    dlg = ShortcutsDialog(window._shortcut_registry, window)
    qtbot.addWidget(dlg)
    dlg._reset_all()
    assert QKeySequence(dlg.get_shortcuts()[key]) == QKeySequence(default)

    # What _open_shortcuts() does after OK.
    monkeypatch.setattr(dlg, "exec", lambda: True)
    monkeypatch.setattr("opensak.gui.dialogs.shortcuts_dialog.ShortcutsDialog",
                        lambda *a, **k: dlg)
    window._open_shortcuts()
    assert getattr(window, action).shortcut() == QKeySequence(default)

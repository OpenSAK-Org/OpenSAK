"""tests/unit-tests/test_user_flag_shortcuts_1314.py — issue #1314.

GSAK users filter on user flags with one key (F8). OpenSAK gets a
"Show flagged caches only" action and GSAK-style default keys for the User
Flags actions, which can be changed in Keyboard Shortcuts:

    Show flagged caches only   F8
    Set flag for filter        Shift+F6
    Clear flag for filter      Shift+F7
    Clear all flags            F7

A macro the user already bound to one of these keys keeps it (the issue's
own workaround binds a macro to F8); two actions with one key would both
stop working (#678).
"""

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtGui import QAction, QKeySequence  # noqa: E402


@pytest.fixture(autouse=True)
def _english():
    from opensak.lang import load_language
    load_language("en")


class _FakeSettings:
    """Stands in for QSettings: only what the tests put in it."""
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
def window(qtbot, tmp_path, monkeypatch, settings):
    import opensak.db.manager as mgr_module
    from opensak.db.database import get_session, init_db
    from opensak.db.models import Cache
    from opensak.gui.mainwindow import MainWindow
    from tests.data import make_fake_manager

    monkeypatch.setattr(MainWindow, "_check_update_background", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_setup_complete", lambda self: None)
    db_path = tmp_path / "flag_keys_1314.db"
    init_db(db_path=db_path)
    with get_session() as s:
        for i in range(5):
            s.add(Cache(gc_code=f"GC14{i}", name=f"Cache {i}", cache_type="Traditional Cache",
                        latitude=55.0 + i / 100, longitude=12.0, user_flag=i < 2))
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="Keys1314"))
    win = MainWindow()
    qtbot.addWidget(win)
    _refresh(qtbot, win)
    assert win._cache_table.row_count() == 5
    yield win
    win.close()
    mgr_module._manager = None


def _refresh(qtbot, win, **kwargs):
    win._refresh_cache_list(**kwargs)
    qtbot.waitUntil(lambda: not win._active_refresh_workers)


def _keys(win) -> dict[str, str]:
    portable = QKeySequence.SequenceFormat.PortableText
    return {
        "show_flagged": win._act_show_flagged.shortcut().toString(portable),
        "flag_filtered": win._act_flag_filtered.shortcut().toString(portable),
        "unflag_filtered": win._act_unflag_filtered.shortcut().toString(portable),
        "clear_flags": win._act_clear_flags.shortcut().toString(portable),
    }


def _reapply(win):
    for act in (win._act_show_flagged, win._act_flag_filtered,
                win._act_unflag_filtered, win._act_clear_flags):
        act.setShortcut(QKeySequence())
    win._apply_user_flag_shortcut_defaults()


# ── default keys ─────────────────────────────────────────────────────────────

def test_gsak_default_keys(window):
    assert _keys(window) == {
        "show_flagged": "F8",
        "flag_filtered": "Shift+F6",
        "unflag_filtered": "Shift+F7",
        "clear_flags": "F7",
    }


def test_actions_are_in_the_shortcuts_dialog(window):
    keys = {key for key, _label, _acts in window._shortcut_registry}
    assert {"show_flagged", "flag_filtered", "unflag_filtered", "clear_flags"} <= keys


def test_defaults_match_the_reset_all_defaults(window):
    from opensak.gui.dialogs.shortcuts_dialog import _DEFAULTS
    for key, value in _keys(window).items():
        assert QKeySequence(_DEFAULTS[key]) == QKeySequence(value)


def test_no_other_action_uses_the_new_keys(window):
    portable = QKeySequence.SequenceFormat.PortableText
    mine = {window._act_show_flagged, window._act_flag_filtered,
            window._act_unflag_filtered, window._act_clear_flags}
    for act in window.findChildren(QAction):
        if act in mine:
            continue
        for seq in act.shortcuts():
            assert seq.toString(portable) not in {"F8", "Shift+F6", "Shift+F7", "F7"}, act.text()


def test_a_custom_key_is_kept(window, settings):
    settings["shortcuts/show_flagged"] = "Ctrl+Alt+F"
    window._apply_saved_shortcuts()
    window._apply_user_flag_shortcut_defaults()
    assert _keys(window)["show_flagged"] == "Ctrl+Alt+F"


def test_a_macro_on_f8_keeps_it(window):
    macro = QAction("Flagged macro", window)
    macro.setShortcut(QKeySequence("F8"))
    window._macro_shortcut_actions = [macro]
    _reapply(window)
    keys = _keys(window)
    assert keys["show_flagged"] == ""          # left to the macro
    assert keys["clear_flags"] == "F7"         # the others still get theirs


# ── Show flagged caches only ─────────────────────────────────────────────────

def test_show_flagged_only_filters_and_clear_filter_goes_back(qtbot, window):
    window._act_show_flagged.trigger()
    qtbot.waitUntil(lambda: window._cache_table.row_count() == 2)
    assert {c.gc_code for c in window._cache_table.get_all_caches()} == {"GC140", "GC141"}
    from opensak.lang import tr
    assert window._filter_label == tr("filter_label_flagged")
    # #1308: shown as a label, not stored as a saved profile.
    assert window._active_filter_name == ""

    window._act_clear_filter.trigger()
    qtbot.waitUntil(lambda: not window._active_refresh_workers)
    qtbot.waitUntil(lambda: window._cache_table.row_count() == 5)


def test_show_flagged_only_with_nothing_flagged_opens_no_dialog(qtbot, window, monkeypatch):
    from opensak.db.database import get_session
    from opensak.db.models import Cache
    from opensak.gui.icon import OpenSAKMessageBox
    from opensak.lang import tr

    with get_session() as s:
        s.query(Cache).update({Cache.user_flag: False})
    opened = []
    monkeypatch.setattr(OpenSAKMessageBox, "warning",
                        staticmethod(lambda *a, **k: opened.append(a)))
    monkeypatch.setattr(window, "_show_filter_dialog", lambda *a, **k: opened.append(a))

    window._show_flagged_only()
    qtbot.wait(50)
    assert opened == []
    assert window._statusbar.currentMessage() == tr("filter_lp_no_flagged")
    assert window._cache_table.row_count() == 5

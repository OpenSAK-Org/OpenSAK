# tests/unit-tests/test_shortcuts_dialog.py — keyboard shortcuts dialog.

import pytest
from unittest.mock import MagicMock
from PySide6.QtGui import QKeySequence

pytest.importorskip("pytestqt")

from opensak.gui.dialogs.shortcuts_dialog import ShortcutsDialog, _DEFAULTS
from opensak.lang import load_language

load_language("en")


def _make_action(shortcut: str):
    act = MagicMock()
    act.shortcut.return_value = QKeySequence(shortcut)
    return act


def _make_registry(keys: list[str] | None = None):
    keys = keys or list(_DEFAULTS.keys())
    return [
        (k, f"shortcut_{k.replace('-', '_')}", [_make_action(_DEFAULTS[k])])
        for k in keys
    ]


class TestShortcutsDialogLayout:
    def test_row_count_matches_registry(self, qtbot):
        reg = _make_registry()
        dlg = ShortcutsDialog(reg)
        qtbot.addWidget(dlg)
        assert dlg._table.rowCount() == len(reg)

    def test_column_count_is_two(self, qtbot):
        dlg = ShortcutsDialog(_make_registry())
        qtbot.addWidget(dlg)
        assert dlg._table.columnCount() == 2

    def test_editors_match_registry_length(self, qtbot):
        reg = _make_registry()
        dlg = ShortcutsDialog(reg)
        qtbot.addWidget(dlg)
        assert len(dlg._editors) == len(reg)

    def test_action_names_shown_in_first_column(self, qtbot):
        reg = [("quit", "shortcut_quit", [_make_action("Ctrl+Q")])]
        dlg = ShortcutsDialog(reg)
        qtbot.addWidget(dlg)
        item = dlg._table.item(0, 0)
        assert item is not None
        assert item.text() == "Quit"

    def test_window_title_is_translated(self, qtbot):
        dlg = ShortcutsDialog(_make_registry())
        qtbot.addWidget(dlg)
        assert dlg.windowTitle() == "Keyboard Shortcuts"


class TestGetShortcuts:
    def test_returns_dict_with_all_keys(self, qtbot):
        reg = _make_registry()
        dlg = ShortcutsDialog(reg)
        qtbot.addWidget(dlg)
        result = dlg.get_shortcuts()
        assert set(result.keys()) == {k for k, _, _ in reg}

    def test_initial_values_match_actions(self, qtbot):
        reg = [("quit", "shortcut_quit", [_make_action("Ctrl+Q")])]
        dlg = ShortcutsDialog(reg)
        qtbot.addWidget(dlg)
        result = dlg.get_shortcuts()
        seq = QKeySequence(result["quit"])
        assert not seq.isEmpty()

    def test_empty_shortcut_returns_empty_string(self, qtbot):
        reg = [("delete_cache", "shortcut_delete_cache", [_make_action("")])]
        dlg = ShortcutsDialog(reg)
        qtbot.addWidget(dlg)
        result = dlg.get_shortcuts()
        assert result["delete_cache"] == ""


class TestResetAll:
    def test_reset_restores_defaults(self, qtbot):
        reg = [
            ("quit", "shortcut_quit", [_make_action("Ctrl+Q")]),
            ("import", "shortcut_import", [_make_action("Ctrl+I")]),
        ]
        dlg = ShortcutsDialog(reg)
        qtbot.addWidget(dlg)
        # Change the first editor
        dlg._editors[0].setKeySequence(QKeySequence("Ctrl+Z"))
        dlg._reset_all()
        result = dlg.get_shortcuts()
        assert QKeySequence(result["quit"]) == QKeySequence(_DEFAULTS["quit"])
        assert QKeySequence(result["import"]) == QKeySequence(_DEFAULTS["import"])

    def test_reset_uses_defaults_dict(self, qtbot):
        for key, default in _DEFAULTS.items():
            reg = [(key, f"shortcut_{key}", [_make_action("")])]
            dlg = ShortcutsDialog(reg)
            qtbot.addWidget(dlg)
            dlg._reset_all()
            result = dlg.get_shortcuts()
            assert QKeySequence(result[key]) == QKeySequence(default), (
                f"reset for '{key}' should produce '{default}'"
            )


class TestDefaultsDict:
    def test_all_defaults_are_valid_key_sequences(self):
        for key, val in _DEFAULTS.items():
            seq = QKeySequence(val)
            assert not seq.isEmpty(), f"_DEFAULTS['{key}'] = '{val}' is not a valid sequence"

    def test_defaults_cover_all_expected_actions(self):
        expected = {
            "manage_databases", "import", "quit", "add_cache", "edit_cache",
            "delete_cache", "refresh", "filter", "settings", "gps_export",
            "trip_planner", "coord_converter", "projection",
        }
        assert set(_DEFAULTS.keys()) == expected

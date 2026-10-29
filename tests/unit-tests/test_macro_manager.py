"""tests/unit-tests/test_macro_manager.py — the Macro manager (#938 Phase 3).

Covers the metadata header of macro files, the list of local macros, the
stored macro shortcuts, the MacroManagerDialog (list, run, edit, shortcut
dialog) and the main window side: the menu entry, the shortcut actions and
running a macro from its shortcut.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from opensak.macro.examples import bundled_examples_dir
from opensak.macro.metadata import discover_macros, parse_header, read_metadata
from opensak.macro.runtime import API_VERSION
from opensak.macro.shortcuts import macro_shortcut, macro_shortcuts, set_macro_shortcut

HEADER = """\
-- OpenSAK Macro
-- Name: Find easy traditionals
-- Author: Jane Doe
-- Version: 1.2
-- Requires-API: 2
-- Description: Filters for unfound traditional caches.
-- Permissions: write, open
--
-- Name: a later key is ignored
print(1)
"""


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


# ── Metadata header ──────────────────────────────────────────────────────────

def test_header_fields():
    info = parse_header(HEADER, Path("m.lua"))
    assert (info.name, info.author, info.version, info.requires_api, info.description) == (
        "Find easy traditionals", "Jane Doe", "1.2", "2",
        "Filters for unfound traditional caches.",
    )
    assert info.requires_api_number == 2
    assert info.permissions == ("write", "open")
    assert not info.error


def test_header_keys_are_case_insensitive_and_marker_is_optional():
    info = parse_header("--name:  Mine \n-- REQUIRES-API: 3.0\nprint(1)\n", Path("m.lua"))
    assert info.name == "Mine"
    assert info.requires_api_number == 3


def test_header_stops_at_first_code_line():
    info = parse_header("print(1)\n-- Name: too late\n", Path("late.lua"))
    assert info.name == "late"


def test_header_stops_at_block_comment():
    info = parse_header("-- Name: A\n--[[\n-- Version: 9\n]]\n", Path("m.lua"))
    assert (info.name, info.version) == ("A", "")


def test_header_skips_blank_lines_bom_and_shebang():
    text = "﻿#!/usr/bin/env opensak\n\n-- Name: A\n\n-- Version: 2\n"
    info = parse_header(text, Path("m.lua"))
    assert (info.name, info.version) == ("A", "2")


def test_header_ignores_prose_and_unknown_keys():
    info = parse_header("-- Note: not a key\n-- CSV layout (header):\n-- Name: A\n",
                        Path("m.lua"))
    assert info.name == "A"
    assert info.description == ""


def test_no_header_falls_back_to_file_name_and_legacy_summary():
    info = parse_header("-- tool.lua — does a thing\n--\nprint(1)\n", Path("tool.lua"))
    assert (info.name, info.description) == ("tool", "does a thing")
    assert parse_header("print(1)\n", Path("x.lua")).description == ""


def test_header_keys_lists_only_what_is_written():
    from opensak.macro.metadata import header_keys
    assert header_keys(HEADER) == (
        "name", "author", "version", "requires-api", "description", "permissions")
    # No defaults: parse_header() falls back to the file name and the
    # legacy summary, header_keys() does not.
    assert header_keys("-- tool.lua — does a thing\n--\nprint(1)\n") == ()
    text = ("﻿-- NAME: A\n-- Version:\n-- Note: prose\n-- Permissions: write\n"
            "-- Permissions: open\nprint(1)\n-- Author: too late\n")
    assert header_keys(text) == ("name", "note", "permissions")


@pytest.mark.parametrize("value, number, newer", [
    ("3", 3, False), ("3.1", 3, False), (str(API_VERSION + 1), API_VERSION + 1, True),
    ("", None, False), ("soon", None, False),
])
def test_requires_api(value, number, newer):
    info = parse_header(f"-- Requires-API: {value}\n", Path("m.lua"))
    assert info.requires_api_number == number
    assert info.needs_newer_api(API_VERSION) is newer


def _perms(header: str):
    return parse_header(header + "print(1)\n", Path("m.lua"))


def test_permissions_in_canonical_order_case_insensitive():
    info = _perms("-- Permissions: Open,write  READ-FILES, write\n")
    assert info.permissions == ("write", "read-files", "open")
    assert info.permissions_declared
    assert info.unknown_permissions == ()


def test_permissions_none_differs_from_undeclared():
    none = _perms("-- Permissions: none\n")
    assert (none.permissions, none.permissions_declared) == ((), True)
    missing = _perms("-- Name: A\n")
    assert (missing.permissions, missing.permissions_declared) == ((), False)
    # An empty value counts as not set, as for the other keys.
    assert not _perms("-- Permissions:\n").permissions_declared


def test_permissions_unknown_words_are_kept():
    info = _perms("-- Permissions: write, telepathy, none, Telepathy\n")
    assert info.permissions == ("write",)
    assert info.unknown_permissions == ("telepathy",)


def test_permissions_and_network_lines_add_up():
    info = _perms("-- Permissions: write\n-- Name: A\n-- Permissions: open\n"
                  "-- Network: a.example.com\n-- Network: b.example.com, a.example.com\n")
    assert info.permissions == ("write", "open")
    assert info.network == ("a.example.com", "b.example.com")


@pytest.mark.parametrize("entry, host", [
    ("API.Example.com", "api.example.com"),
    ("example.com.", "example.com"),
    ("bücher.example", "xn--bcher-kva.example"),
    ("192.168.1.10", "192.168.1.10"),
    ("[::1]", "::1"),
    ("https://example.com", None),
    ("example.com/path", None),
    ("example.com:8080", None),
    ("user@example.com", None),
    ("*.example.com", None),
    ("localhost", None),
    ("10.1.2", None),
    ("-bad.example.com", None),
])
def test_network_hosts(entry, host):
    from opensak.macro.metadata import normalize_host
    assert normalize_host(entry) == host
    info = _perms(f"-- Network: {entry}\n")
    assert info.network == ((host,) if host else ())
    assert info.invalid_hosts == (() if host else (entry,))


def test_network_alone_does_not_declare_permissions():
    info = _perms("-- Network: api.example.com\n")
    assert info.network == ("api.example.com",)
    assert not info.permissions_declared


@pytest.mark.parametrize("host, blocked", [
    ("geocaching.com", True), ("www.geocaching.com", True), ("coord.info", True),
    ("waymarking.com", True), ("www.wherigo.com", True), ("ns1.groundspeak.net", True),
    ("geocaching.eu", True), ("geocaching.co", True),
    ("api.groundspeak.example", False), ("notgeocaching.com", False),
    # Community sites that are not Groundspeak's
    ("geocaching.co.uk", False), ("geocaching.com.au", False), ("geocaching.de", False),
])
def test_blocked_hosts(host, blocked):
    info = _perms(f"-- Network: {host}\n")
    assert info.blocked_hosts == ((host,) if blocked else ())


def test_added_permissions():
    from opensak.macro.metadata import added_permissions
    v1 = _perms("-- Permissions: write\n-- Network: a.example.com\n")
    assert added_permissions(v1, v1) == ()
    fewer = _perms("-- Permissions: none\n")
    assert added_permissions(v1, fewer) == ()
    more = _perms("-- Permissions: write, open, magic\n"
                  "-- Network: a.example.com, b.example.com, http://bad\n")
    assert added_permissions(v1, more) == (
        "open", "magic", "network:b.example.com", "network:http://bad")
    # The old version declared nothing: everything the new one declares is new.
    assert added_permissions(_perms("-- Name: A\n"), v1) == ("write", "network:a.example.com")


def test_permissions_column_and_tooltip():
    from opensak.gui.dialogs.macro_manager import permissions_text, permissions_tooltip
    info = _perms("-- Permissions: write, magic\n-- Network: a.example.com, geocaching.com\n")
    assert permissions_text(info) == "write, magic, network: a.example.com, geocaching.com"
    tip = permissions_tooltip(info)
    assert "changes data in a database" in tip
    assert "magic" in tip and "geocaching.com" in tip
    assert "still asks" in tip
    assert permissions_text(_perms("-- Permissions: none\n")) == "none"
    assert permissions_text(_perms("-- Name: A\n")) == ""
    assert "does not declare" in permissions_tooltip(_perms("-- Name: A\n"))


def test_unreadable_macro_has_error(tmp_path):
    path = tmp_path / "bad.lua"
    path.write_bytes(b"-- Name: \xff\xfe\n")
    info = read_metadata(path)
    assert info.error
    assert info.name == "bad"


def test_discover_is_recursive_sorted_and_skips_hidden_and_types(tmp_path):
    for rel in ("b.lua", "A/z.lua", "a.lua", ".git/hook.lua", "types/opensak.lua",
                "sub/types/kept.lua", "notes.txt"):
        _lua(tmp_path / rel)
    found = [m.path.relative_to(tmp_path).as_posix() for m in discover_macros(tmp_path)]
    assert found == ["a.lua", "A/z.lua", "b.lua", "sub/types/kept.lua"]
    assert discover_macros(tmp_path / "missing") == []


@pytest.mark.parametrize("name", sorted(p.name for p in bundled_examples_dir().glob("*.lua")))
def test_examples_have_a_header(name):
    info = read_metadata(bundled_examples_dir() / name)
    assert info.name != Path(name).stem
    assert info.description and info.version and info.author
    assert info.requires_api_number is not None
    assert not info.needs_newer_api(API_VERSION)
    assert info.permissions_declared and not info.unknown_permissions


def test_editor_templates_have_a_header():
    from opensak.gui.dialogs.macro_dialog import EXAMPLE_MACRO, NEW_MACRO
    example = parse_header(EXAMPLE_MACRO, Path("macro.lua"))
    assert (example.name, example.author) == ("Easy unfound caches", "OpenSAK")
    assert example.description and not example.needs_newer_api(API_VERSION)
    # A new macro: empty values count as not set, until the user fills them in.
    new = parse_header(NEW_MACRO, Path("mine.lua"))
    assert (new.name, new.author, new.description) == ("mine", "", "")
    assert new.requires_api_number == API_VERSION


# ── Stored shortcuts ─────────────────────────────────────────────────────────

def test_shortcut_stored_relative_to_macros_folder(macros_dir):
    from opensak.settings_store import get_store
    macro = _lua(macros_dir / "sub" / "m.lua")
    set_macro_shortcut(macro, "Ctrl+Alt+1")
    assert get_store().get("macros.shortcuts") == {"sub/m.lua": "Ctrl+Alt+1"}
    assert macro_shortcut(macro) == "Ctrl+Alt+1"
    assert macro_shortcuts() == {macro: "Ctrl+Alt+1"}


def test_shortcut_outside_macros_folder_and_removal(macros_dir, tmp_path):
    from opensak.settings_store import get_store
    other = _lua(tmp_path / "elsewhere" / "o.lua")
    set_macro_shortcut(other, "F9")
    assert macro_shortcuts() == {other.resolve(): "F9"}
    set_macro_shortcut(other, "")
    assert macro_shortcut(other) == ""
    assert get_store().get("macros.shortcuts") is None


def test_shortcut_of_missing_macro_is_left_out(macros_dir):
    macro = _lua(macros_dir / "gone.lua")
    set_macro_shortcut(macro, "Ctrl+Alt+2")
    macro.unlink()
    assert macro_shortcuts() == {}


pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QDialog, QDialogButtonBox  # noqa: E402

from opensak.gui.dialogs import macro_manager as mm  # noqa: E402


@pytest.mark.parametrize("seq, ok", [
    ("Ctrl+Alt+1", True), ("Alt+M", True), ("Meta+K", True), ("F9", True),
    ("Shift+F9", True), ("M", False), ("Shift+M", False), ("Ctrl+K, Ctrl+L", False), ("", False),
])
def test_shortcut_allowed(seq, ok):
    assert mm.shortcut_allowed(seq) is ok


# ── MacroManagerDialog ───────────────────────────────────────────────────────

@pytest.fixture
def make_manager(qtbot, macros_dir):
    def make(**kwargs):
        d = mm.MacroManagerDialog(host=MagicMock(), **kwargs)
        qtbot.addWidget(d)
        return d
    return make


def _rows(d) -> list[list[str]]:
    tree = d._tree
    return [[tree.topLevelItem(i).text(c) for c in range(tree.columnCount())]
            for i in range(tree.topLevelItemCount())]


def test_lists_macros_with_metadata(make_manager, macros_dir):
    _lua(macros_dir / "find.lua", HEADER)
    _lua(macros_dir / "examples" / "plain.lua", "-- plain.lua — no header\nprint(1)\n")
    set_macro_shortcut(macros_dir / "find.lua", "Ctrl+Alt+F")
    d = make_manager()
    rows = _rows(d)
    assert rows[0][:mm.COL_SHORTCUT] == ["Find easy traditionals",
                                         "Filters for unfound traditional caches.",
                                         "1.2", "Jane Doe", "2", "write, open"]
    assert rows[0][mm.COL_SHORTCUT]                     # native text of Ctrl+Alt+F
    assert rows[0][mm.COL_FOLDER] == ""
    assert rows[1][mm.COL_NAME] == "plain"
    assert rows[1][mm.COL_DESCRIPTION] == "no header"
    assert rows[1][mm.COL_FOLDER] == "examples"
    assert rows[1][mm.COL_PERMISSIONS] == ""
    assert d._tree.isVisibleTo(d) and not d._empty.isVisibleTo(d)


def test_empty_folder_shows_hint(make_manager):
    d = make_manager()
    assert not d._tree.isVisibleTo(d)
    assert d._empty.isVisibleTo(d)
    assert not d._btn_run.isEnabled()


def test_reload_picks_up_new_macro_and_keeps_selection(make_manager, macros_dir):
    a = _lua(macros_dir / "a.lua")
    d = make_manager()
    assert d.select_path(a)
    _lua(macros_dir / "b.lua")
    d.reload()
    assert len(d.macros) == 2
    assert d.selected_path() == a
    assert d._btn_run.isEnabled()


def _shown(d) -> list[str]:
    tree = d._tree
    return [tree.topLevelItem(i).text(mm.COL_NAME) for i in range(tree.topLevelItemCount())
            if not tree.topLevelItem(i).isHidden()]


def test_search_by_name_folder_file_and_words(make_manager, macros_dir):
    _lua(macros_dir / "find.lua", HEADER)                         # Find easy traditionals
    _lua(macros_dir / "tools" / "gpx_dump.lua", "-- Name: Dump\n-- Author: Bob\n")
    _lua(macros_dir / "tools" / "other.lua")
    d = make_manager()
    d._search.setText("EASY")
    assert _shown(d) == ["Find easy traditionals"]
    d._search.setText("tools")                                  # folder
    assert _shown(d) == ["Dump", "other"]
    d._search.setText("gpx_dump")                               # file name
    assert _shown(d) == ["Dump"]
    d._search.setText("tools bob")                              # every word must match
    assert _shown(d) == ["Dump"]
    d._search.setText("nothing like this")
    assert _shown(d) == []
    d._search.clear()
    assert len(_shown(d)) == 3


def test_search_deselects_hidden_and_select_path_clears_it(make_manager, macros_dir):
    a = _lua(macros_dir / "alpha.lua")
    _lua(macros_dir / "beta.lua")
    d = make_manager()
    d.select_path(a)
    d._search.setText("beta")
    assert d.selected_path() is None
    assert not d._btn_run.isEnabled()
    assert d.select_path(a)
    assert d._search.text() == ""
    assert d.selected_path() == a


def test_search_survives_reload_and_enter_selects_first(make_manager, macros_dir):
    _lua(macros_dir / "alpha.lua")
    d = make_manager()
    d._search.setText("beta")
    _lua(macros_dir / "beta.lua")
    d.reload()
    assert _shown(d) == ["beta"]
    d._search.returnPressed.emit()
    assert d.selected_path() == (macros_dir / "beta.lua")


def test_newer_api_is_marked(make_manager, macros_dir):
    _lua(macros_dir / "future.lua", f"-- Requires-API: {API_VERSION + 1}\nprint(1)\n")
    d = make_manager()
    item = d._tree.topLevelItem(0)
    assert not item.icon(mm.COL_API).isNull()
    assert str(API_VERSION + 1) in item.toolTip(mm.COL_API)


@pytest.mark.parametrize("header, marked", [
    ("-- Permissions: write, open\n", False),
    ("-- Network: api.example.com\n", False),
    ("-- Permissions: magic\n", True),
    ("-- Network: https://api.example.com\n", True),
    ("-- Network: www.geocaching.com\n", True),
])
def test_permission_problems_are_marked(make_manager, macros_dir, header, marked):
    _lua(macros_dir / "m.lua", header + "print(1)\n")
    d = make_manager()
    item = d._tree.topLevelItem(0)
    assert item.icon(mm.COL_PERMISSIONS).isNull() is not marked
    assert "still asks" in item.toolTip(mm.COL_PERMISSIONS)


def test_run_shows_output(make_manager, macros_dir):
    macro = _lua(macros_dir / "hello.lua", "-- Name: Hello\nprint('hi there')\n")
    d = make_manager()
    d.select_path(macro)
    d._btn_run.click()
    out = d._output.toPlainText()
    assert "Hello" in out and "hi there" in out
    assert d._btn_run.isEnabled()


def test_double_click_asks_before_running(make_manager, macros_dir, monkeypatch):
    macro = _lua(macros_dir / "hello.lua", "-- Name: Hello\nprint('ran')\n")
    d = make_manager()
    d.select_path(macro)
    ask = MagicMock(return_value=mm.QMessageBox.StandardButton.No)
    monkeypatch.setattr(mm.QMessageBox, "question", ask)
    d._tree.itemActivated.emit(d._tree.currentItem(), 0)
    assert "Hello" in ask.call_args.args[2]
    assert ask.call_args.args[4] == mm.QMessageBox.StandardButton.No    # default
    assert "ran" not in d._output.toPlainText()
    ask.return_value = mm.QMessageBox.StandardButton.Yes
    d._tree.itemActivated.emit(d._tree.currentItem(), 0)
    assert "ran" in d._output.toPlainText()
    # The Run button runs without asking.
    ask.reset_mock()
    d._btn_run.click()
    ask.assert_not_called()


def test_run_error_goes_to_edit(make_manager, macros_dir):
    macro = _lua(macros_dir / "boom.lua", "local x = 1\nerror('boom')\n")
    edit = MagicMock()
    d = make_manager(edit_macro=edit)
    error = d.run_path(macro)
    assert "boom.lua:2:" in error
    assert "boom" in d._output.toPlainText()
    d.select_path(macro)
    d._btn_edit.click()
    edit.assert_called_once_with(macro, error)
    # A successful run forgets the error.
    _lua(macro, "print(1)\n")
    assert d.run_path(macro) is None
    d._btn_edit.click()
    assert edit.call_args.args == (macro, None)


def test_run_newer_api_asks_first(make_manager, macros_dir, monkeypatch):
    macro = _lua(macros_dir / "future.lua",
                 f"-- Requires-API: {API_VERSION + 1}\nprint('ran')\n")
    d = make_manager()
    ask = MagicMock(return_value=mm.QMessageBox.StandardButton.No)
    monkeypatch.setattr(mm.QMessageBox, "question", ask)
    assert d.run_path(macro) is None
    assert ask.called
    assert "ran" not in d._output.toPlainText()
    ask.return_value = mm.QMessageBox.StandardButton.Yes
    assert d.run_path(macro) is None
    assert "ran" in d._output.toPlainText()


def test_set_shortcut(make_manager, macros_dir, monkeypatch):
    macro = _lua(macros_dir / "m.lua")
    changed = MagicMock()
    taken = MagicMock(return_value={})
    d = make_manager(shortcuts_changed=changed, taken_shortcuts=taken)
    d.select_path(macro)

    class FakeDialog:
        def __init__(self, name, current, taken_keys, parent):
            assert (name, current) == ("m", "")
        def exec(self):
            return QDialog.DialogCode.Accepted
        def sequence(self):
            return "Ctrl+Alt+M"

    monkeypatch.setattr(mm, "MacroShortcutDialog", FakeDialog)
    d._btn_shortcut.click()
    taken.assert_called_once_with(macro)
    assert macro_shortcut(macro) == "Ctrl+Alt+M"
    changed.assert_called_once()
    assert d._tree.topLevelItem(0).text(mm.COL_SHORTCUT)


def test_shortcut_dialog_refuses_taken_and_plain_keys(qtbot):
    from PySide6.QtGui import QKeySequence
    dlg = mm.MacroShortcutDialog("m", "", {"Ctrl+E": "Edit cache"})
    qtbot.addWidget(dlg)
    ok = dlg._buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok.isEnabled()                                   # empty = no shortcut
    dlg._edit.setKeySequence(QKeySequence("Ctrl+E"))
    assert "Edit cache" in dlg.problem()
    assert not ok.isEnabled()
    dlg._edit.setKeySequence(QKeySequence("M"))
    assert dlg.problem()
    assert not ok.isEnabled()
    dlg._edit.setKeySequence(QKeySequence("Ctrl+Alt+M"))
    assert dlg.problem() == ""
    assert ok.isEnabled()
    assert dlg.sequence() == "Ctrl+Alt+M"


# ── Editor: error from a run in the manager ──────────────────────────────────

def test_editor_show_error_marks_line(qtbot, macros_dir, monkeypatch):
    from opensak.gui.dialogs.macro_dialog import MacroDialog
    macro = _lua(macros_dir / "boom.lua", "local x = 1\n\nerror('boom')\n")
    d = MacroDialog(host=MagicMock())
    qtbot.addWidget(d)
    d.open_path(macro, ask=False)
    d.show_error("boom.lua:3: boom")
    assert d._editor.error_line() == 3
    assert "boom" in d._output.toPlainText()


# ── Main window ──────────────────────────────────────────────────────────────

@pytest.fixture
def window(qtbot, tmp_path, monkeypatch, macros_dir):
    import opensak.db.manager as mgr_module
    from opensak.db.database import init_db
    from opensak.gui.mainwindow import MainWindow
    from opensak.lang import load_language
    from tests.data import make_fake_manager

    monkeypatch.setattr(MainWindow, "_initial_load", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_update_background", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_setup_complete", lambda self: None)
    monkeypatch.setattr("opensak.utils.flags.lua_macros", True)
    load_language("en")
    db_path = tmp_path / "macro_manager.db"
    init_db(db_path=db_path)
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="MacroMgr"))

    win = MainWindow()
    qtbot.addWidget(win)
    yield win
    win.close()
    mgr_module._manager = None


def test_menu_has_macro_manager(window):
    from opensak.lang import tr
    texts = [a.text() for m in window.menuBar().actions() if m.menu()
             for a in m.menu().actions()]
    assert tr("action_macro_manager") in texts


def test_shortcut_actions_follow_the_store(window, macros_dir):
    macro = _lua(macros_dir / "m.lua")
    assert window._macro_shortcut_actions == []
    set_macro_shortcut(macro, "Ctrl+Alt+M")
    window._apply_macro_shortcuts()
    (act,) = window._macro_shortcut_actions
    assert act.shortcut().toString() == "Ctrl+Alt+M"
    assert act in window.actions()
    set_macro_shortcut(macro, "")
    window._apply_macro_shortcuts()
    assert window._macro_shortcut_actions == []
    assert act not in window.actions()


def test_taken_shortcuts(window, macros_dir):
    a, b = _lua(macros_dir / "a.lua", "-- Name: Macro A\n"), _lua(macros_dir / "b.lua")
    set_macro_shortcut(a, "Ctrl+Alt+A")
    set_macro_shortcut(b, "Ctrl+Alt+B")
    window._apply_macro_shortcuts()
    taken = window._taken_shortcuts(b)
    assert taken["Ctrl+Alt+A"] == "Macro A"
    assert "Ctrl+Alt+B" not in taken                # the macro being edited
    assert "Ctrl+E" in taken                        # Edit cache


def test_shortcut_run_reports_in_status_bar(window, macros_dir, monkeypatch):
    macro = _lua(macros_dir / "ok.lua", "print('done')\n")
    window._run_macro_shortcut(macro)
    assert "ok.lua" in window._statusbar.currentMessage()
    assert "done" in window._macro_manager._output.toPlainText()
    assert not window._macro_manager.isVisible()


@pytest.mark.parametrize("shortcut, held, printed", [
    ("Ctrl+Alt+Q", True, "true"),
    ("Ctrl+Alt+Q", False, "false"),
    ("Shift+F9", True, "false"),          # Shift is part of the key (#1038)
])
def test_shortcut_run_passes_shift(window, macros_dir, monkeypatch, shortcut, held, printed):
    macro = _lua(macros_dir / "s.lua", "print(opensak.shift_key())\n")
    monkeypatch.setattr(mm, "shift_held", lambda: held)
    window._run_macro_shortcut(macro, shortcut)
    assert window._macro_manager._output.toPlainText().splitlines()[1] == printed


def test_shortcut_run_error_offers_edit(window, macros_dir, monkeypatch):
    from opensak.gui import mainwindow as mw
    macro = _lua(macros_dir / "boom.lua", "error('boom')\n")
    shown = []

    def fake_exec(box):
        shown.append(box.text())
        box_buttons = [b for b in box.buttons() if b.text() == mw.tr("edit")]
        box.clickedButton = lambda: box_buttons[0]
        return 0

    monkeypatch.setattr(mw.QMessageBox, "exec", fake_exec)
    edit = MagicMock()
    monkeypatch.setattr(window, "_edit_macro", edit)
    window._run_macro_shortcut(macro)
    assert shown and "boom.lua" in shown[0]
    assert edit.call_args.args[0] == macro
    assert "boom.lua:1:" in edit.call_args.args[1]

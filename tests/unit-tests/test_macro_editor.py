"""tests/unit-tests/test_macro_editor.py — the Lua macro editor window.

Covers the recent-macros list, syntax highlighting, the editing helpers of
CodeEditor, find/replace, the MacroDialog file workflow (open, save, save
as, unsaved changes, recent menu, run with error-line highlight) and the
in-app API reference.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from opensak.macro.recent import (
    MAX_RECENT,
    add_recent_macro,
    clear_recent_macros,
    recent_macros,
    remove_recent_macro,
)


def _lua(path: Path, text: str = "print(1)\n") -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# ── Recent macros ────────────────────────────────────────────────────────────

def test_recent_newest_first_without_duplicates(tmp_path):
    a, b = _lua(tmp_path / "a.lua"), _lua(tmp_path / "b.lua")
    add_recent_macro(a)
    add_recent_macro(b)
    add_recent_macro(tmp_path / "x" / ".." / "a.lua")
    assert recent_macros() == [a.resolve(), b.resolve()]


def test_recent_keeps_at_most_ten(tmp_path):
    files = [_lua(tmp_path / f"m{i}.lua") for i in range(MAX_RECENT + 3)]
    for f in files:
        add_recent_macro(f)
    assert recent_macros() == [f.resolve() for f in reversed(files)][:MAX_RECENT]


def test_recent_skips_missing_and_can_be_cleared(tmp_path):
    a, b = _lua(tmp_path / "a.lua"), _lua(tmp_path / "b.lua")
    add_recent_macro(a)
    add_recent_macro(b)
    b.unlink()
    assert recent_macros() == [a.resolve()]
    remove_recent_macro(a)
    assert recent_macros() == []
    add_recent_macro(a)
    clear_recent_macros()
    assert recent_macros() == []


pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402


# ── Syntax highlighting ──────────────────────────────────────────────────────

@pytest.fixture
def editor(qtbot):
    from opensak.gui.dialogs.macro_editor import CodeEditor
    e = CodeEditor()
    qtbot.addWidget(e)
    return e


def _color_at(editor, line: int, col: int) -> str | None:
    block = editor.document().findBlockByNumber(line)
    for r in block.layout().formats():
        if r.start <= col < r.start + r.length:
            return r.format.foreground().color().name()
    return None


def test_highlighter_colours_tokens(editor):
    editor.setPlainText(
        'local n = opensak.filter{ code = "GC1" } -- comment\n'
        "print(42)\n"
    )
    fmts = editor.highlighter.formats
    color = {k: f.foreground().color().name() for k, f in fmts.items()}
    assert _color_at(editor, 0, 0) == color["keyword"]          # local
    assert _color_at(editor, 0, 6) is None                      # n
    assert _color_at(editor, 0, 10) == color["api"]             # opensak
    assert _color_at(editor, 0, 18) == color["api"]             # .filter
    assert _color_at(editor, 0, 33) == color["string"]          # "GC1"
    assert _color_at(editor, 0, 44) == color["comment"]
    assert _color_at(editor, 1, 0) == color["builtin"]          # print
    assert _color_at(editor, 1, 6) == color["number"]           # 42


def test_highlighter_multiline_comment_and_string(editor):
    editor.setPlainText("--[[ one\ntwo ]] local x\ns = [==[\nlocal ]==] end\n")
    color = {k: f.foreground().color().name() for k, f in editor.highlighter.formats.items()}
    assert _color_at(editor, 1, 0) == color["comment"]
    assert _color_at(editor, 1, 7) == color["keyword"]          # local after ]]
    assert _color_at(editor, 3, 0) == color["string"]           # inside [==[
    assert _color_at(editor, 3, 11) == color["keyword"]         # end


def test_string_with_escaped_quote(editor):
    editor.setPlainText('x = "a\\"b" end\n')
    color = {k: f.foreground().color().name() for k, f in editor.highlighter.formats.items()}
    assert _color_at(editor, 0, 7) == color["string"]
    assert _color_at(editor, 0, 11) == color["keyword"]


# ── Editing helpers ──────────────────────────────────────────────────────────

def _select_all(editor):
    c = editor.textCursor()
    c.select(QTextCursor.SelectionType.Document)
    editor.setTextCursor(c)


def test_toggle_comment_round_trip(editor):
    editor.setPlainText("if x then\n    y()\n\nend")
    _select_all(editor)
    editor.toggle_comment()
    assert editor.toPlainText() == "-- if x then\n--     y()\n\n-- end"
    _select_all(editor)
    editor.toggle_comment()
    assert editor.toPlainText() == "if x then\n    y()\n\nend"


def test_toggle_comment_is_one_undo_step(editor):
    editor.setPlainText("a()\nb()")
    _select_all(editor)
    editor.toggle_comment()
    editor.undo()
    assert editor.toPlainText() == "a()\nb()"


def test_tab_indents_selection_and_backtab_dedents(editor, qtbot):
    editor.setPlainText("a()\nb()")
    _select_all(editor)
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText() == "    a()\n    b()"
    qtbot.keyClick(editor, Qt.Key.Key_Backtab)
    assert editor.toPlainText() == "a()\nb()"


def test_tab_without_selection_inserts_spaces(editor, qtbot):
    editor.setPlainText("ab")
    editor.moveCursor(QTextCursor.MoveOperation.End)
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText() == "ab  "


@pytest.mark.parametrize("line, expected", [
    ("    x = 1", "    x = 1\n    "),
    ("if x then", "if x then\n    "),
    ("for i = 1, 3 do", "for i = 1, 3 do\n    "),
    ("local t = {", "local t = {\n    "),
    ("print('then')", "print('then')\n"),
])
def test_enter_keeps_and_adds_indent(editor, qtbot, line, expected):
    editor.setPlainText(line)
    editor.moveCursor(QTextCursor.MoveOperation.End)
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    assert editor.toPlainText() == expected


def test_error_line_is_marked_and_cleared_by_editing(editor):
    editor.setPlainText("a\nb\nc")
    editor.mark_error_line(2)
    assert editor.error_line() == 2
    assert editor.textCursor().blockNumber() == 1
    editor.mark_error_line(99)                     # out of range: ignored
    assert editor.error_line() == 2
    editor.insertPlainText("x")
    assert editor.error_line() is None


# ── Find / replace ───────────────────────────────────────────────────────────

@pytest.fixture
def bar(editor):
    from opensak.gui.dialogs.macro_editor import FindReplaceBar
    b = FindReplaceBar(editor)
    editor.setPlainText("Foo foo\nfoo food")
    return b


def test_find_wraps_around(bar, editor):
    bar.find_edit.setText("foo")
    positions = []
    for _ in range(5):
        assert bar.find_next()
        positions.append(editor.textCursor().selectionStart())
    assert positions == [0, 4, 8, 12, 0]
    assert bar.find_next(False)
    assert editor.textCursor().selectionStart() == 12


def test_find_options(bar, editor):
    bar.find_edit.setText("Foo")
    bar.case.setChecked(True)
    bar.find_next()
    assert bar.find_next() and editor.textCursor().selectionStart() == 0
    bar.case.setChecked(False)
    bar.word.setChecked(True)
    bar.find_edit.setText("foo")
    editor.moveCursor(QTextCursor.MoveOperation.Start)
    found = []
    for _ in range(3):
        bar.find_next()
        found.append(editor.textCursor().selectionStart())
    assert 12 not in found                                 # "food"
    bar.find_edit.setText("nothing")
    assert not bar.find_next()
    assert bar.status.text()


def test_replace_one_then_all_in_one_undo_step(bar, editor):
    bar.find_edit.setText("foo")
    bar.replace_edit.setText("bar")
    bar.find_next()                                       # selects "Foo"
    bar.replace()
    assert editor.toPlainText().startswith("bar foo")
    assert bar.replace_all() == 3
    assert editor.toPlainText() == "bar bar\nbar bard"
    editor.undo()
    assert editor.toPlainText() == "bar foo\nfoo food"


def test_replace_all_with_text_containing_the_search(bar, editor):
    bar.find_edit.setText("foo")
    bar.replace_edit.setText("foofoo")
    assert bar.replace_all() == 4


def test_open_bar_takes_selected_word(bar, editor):
    c = editor.textCursor()
    c.setPosition(4)
    c.setPosition(7, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(c)
    bar.open_bar(replace=False)
    assert bar.find_edit.text() == "foo"
    assert not bar.replace_edit.isVisibleTo(bar)
    bar.open_bar(replace=True)
    assert bar.replace_edit.isVisibleTo(bar)


# ── MacroDialog ──────────────────────────────────────────────────────────────

@pytest.fixture
def macros_dir(tmp_path, monkeypatch):
    d = tmp_path / "macros"
    d.mkdir()
    monkeypatch.setattr("opensak.config.get_macros_dir", lambda: d)
    return d


@pytest.fixture
def make_dialog(qtbot, macros_dir):
    from opensak.gui.dialogs.macro_dialog import MacroDialog

    def make():
        d = MacroDialog(host=MagicMock())
        qtbot.addWidget(d)
        return d
    return make


@pytest.fixture
def md():
    from opensak.gui.dialogs import macro_dialog
    return macro_dialog


@pytest.fixture(autouse=True)
def _discard_on_teardown(monkeypatch):
    """qtbot closes the dialogs after each test; an edited one would then
    block on its modal "save changes?" question. Tests that care about the
    answer patch it again themselves."""
    from opensak.gui.dialogs import macro_dialog
    monkeypatch.setattr(macro_dialog.QMessageBox, "question",
                        lambda *a, **k: macro_dialog.QMessageBox.StandardButton.Discard)


def _answer(monkeypatch, md, name):
    ask = MagicMock(return_value=getattr(md.QMessageBox.StandardButton, name))
    monkeypatch.setattr(md.QMessageBox, "question", ask)
    return ask


def _type(d, text):
    """Replace the text the way typing does (setPlainText() would reset the
    modified flag)."""
    _select_all(d._editor)
    d._editor.insertPlainText(text)


def _save_to(monkeypatch, md, path):
    monkeypatch.setattr(md.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(path), "")))


def test_starts_untitled_with_example(make_dialog, md):
    d = make_dialog()
    assert d.path is None
    assert d._editor.toPlainText() == md.EXAMPLE_MACRO
    assert not d.isWindowModified()


def test_reopens_most_recent_macro(make_dialog, tmp_path):
    add_recent_macro(_lua(tmp_path / "old.lua", "-- old\n"))
    add_recent_macro(_lua(tmp_path / "last.lua", "-- last\n"))
    d = make_dialog()
    assert d.path == (tmp_path / "last.lua").resolve()
    assert d._editor.toPlainText() == "-- last\n"
    assert "last.lua" in d.windowTitle()


def test_save_as_writes_file_and_adds_recent(make_dialog, md, monkeypatch, macros_dir):
    d = make_dialog()
    _type(d, "print('x')\n")
    assert d.isWindowModified()
    _save_to(monkeypatch, md, macros_dir / "new")          # suffix added
    assert d._save()
    target = macros_dir / "new.lua"
    assert target.read_text(encoding="utf-8") == "print('x')\n"
    assert d.path == target and not d.isWindowModified()
    assert recent_macros()[0] == target.resolve()


def test_save_writes_to_current_file(make_dialog, tmp_path):
    f = _lua(tmp_path / "m.lua", "a\n")
    d = make_dialog()
    d.open_path(f)
    _type(d, "b\n")
    assert d._save()
    assert f.read_text(encoding="utf-8") == "b\n"


def test_cancelled_save_as_keeps_changes(make_dialog, md, monkeypatch):
    d = make_dialog()
    _type(d, "x")
    monkeypatch.setattr(md.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    assert not d._save()
    assert d.isWindowModified()


@pytest.mark.parametrize("answer, opened", [("Discard", True), ("Cancel", False)])
def test_open_asks_about_unsaved_changes(make_dialog, md, monkeypatch, tmp_path, answer, opened):
    f = _lua(tmp_path / "m.lua", "-- other\n")
    d = make_dialog()
    _type(d, "edited")
    ask = _answer(monkeypatch, md, answer)
    assert d.open_path(f) is opened
    ask.assert_called_once()
    assert d._editor.toPlainText() == ("-- other\n" if opened else "edited")


def test_unsaved_save_answer_saves_before_new(make_dialog, md, monkeypatch, tmp_path):
    f = _lua(tmp_path / "m.lua", "a\n")
    d = make_dialog()
    d.open_path(f)
    _type(d, "changed\n")
    _answer(monkeypatch, md, "Save")
    d._new()
    assert f.read_text(encoding="utf-8") == "changed\n"
    assert d.path is None and d._editor.toPlainText() == md.NEW_MACRO


def test_open_failure_warns_and_drops_from_recent(make_dialog, md, monkeypatch, tmp_path):
    f = tmp_path / "bad.lua"
    f.write_bytes(b"\xff\xfe\x00bad")
    add_recent_macro(f)
    warn = MagicMock()
    monkeypatch.setattr(md.QMessageBox, "warning", warn)
    d = make_dialog()                       # tries to reopen it
    warn.assert_called_once()
    assert d.path is None and recent_macros() == []


def test_recent_menu_lists_and_clears(make_dialog, tmp_path):
    a = _lua(tmp_path / "a.lua", "-- a\n")
    b = _lua(tmp_path / "b.lua", "-- b\n")
    d = make_dialog()
    d.open_path(a)
    d.open_path(b)
    d._fill_recent_menu()
    entries = [act for act in d._recent_menu.actions() if not act.isSeparator()]
    assert [act.text() for act in entries[:2]] == ["&1  b.lua", "&2  a.lua"]
    entries[1].trigger()
    assert d.path == a.resolve()
    entries[-1].trigger()                   # Clear list
    d._fill_recent_menu()
    (empty,) = d._recent_menu.actions()
    assert not empty.isEnabled()


def test_run_highlights_error_line(make_dialog, tmp_path):
    f = _lua(tmp_path / "boom.lua", "local x = 1\n\nerror('boom')\n")
    d = make_dialog()
    d.open_path(f)
    d._btn_run.click()
    assert d._output.toPlainText()
    assert d._editor.error_line() == 3


def test_run_untitled_uses_macro_chunk_name(make_dialog):
    d = make_dialog()
    d._editor.setPlainText("x = = 1")                   # syntax error
    d._run()
    assert d._editor.error_line() == 1


@pytest.mark.parametrize("message, line", [
    ("boom.lua:7: attempt to call a nil value", 7),
    ("[string \"boom.lua\"]:3: x", None),
    ("other_boom.lua:2: x", None),
    ("x: boom.lua:12: y", 12),
])
def test_error_line_parsing(make_dialog, tmp_path, message, line):
    d = make_dialog()
    d.open_path(_lua(tmp_path / "boom.lua"))
    assert d.error_line(message) == line


def test_save_and_run(make_dialog, md, monkeypatch, macros_dir):
    host = MagicMock()
    d = make_dialog()
    d._runtime._host = host
    d._editor.setPlainText("opensak.clear_filter()\n")
    _save_to(monkeypatch, md, macros_dir / "run.lua")
    d._act_save_run.trigger()
    assert (macros_dir / "run.lua").is_file()
    host.clear_filter.assert_called_once()


def test_escape_closes_find_bar_not_window(make_dialog, qtbot):
    d = make_dialog()
    d.show()
    d._act_find.trigger()
    assert d._find_bar.isVisible()
    qtbot.keyClick(d, Qt.Key.Key_Escape)
    assert not d._find_bar.isVisible()
    qtbot.keyClick(d, Qt.Key.Key_Escape)
    assert d.isVisible()


def test_close_with_unsaved_changes_can_be_cancelled(make_dialog, md, monkeypatch):
    d = make_dialog()
    d.show()
    _type(d, "x")
    _answer(monkeypatch, md, "Cancel")
    d.close()
    assert d.isVisible()
    _answer(monkeypatch, md, "Discard")
    d.close()
    assert not d.isVisible()


# ── API reference ────────────────────────────────────────────────────────────

@pytest.fixture
def help_dlg(qtbot):
    from opensak.gui.dialogs.macro_help import MacroHelpDialog
    h = MacroHelpDialog()
    qtbot.addWidget(h)
    return h


def test_help_lists_every_function(help_dlg):
    from opensak.macro.runtime import API
    headings = help_dlg.headings()
    for func in API:
        assert f"opensak.{func.name}" in headings
    assert "Filter keys" in headings


def test_help_internal_link_and_contents_jump(help_dlg):
    from PySide6.QtCore import QUrl
    help_dlg._on_link(QUrl("#opensakreadcsv"))
    assert help_dlg.browser.textCursor().block().text() == "opensak.read_csv"
    item = help_dlg.contents.item(help_dlg.headings().index("opensak.confirm"))
    help_dlg._on_contents(item)
    assert help_dlg.browser.textCursor().block().text() == "opensak.confirm"
    assert not help_dlg.scroll_to_heading("#nosuchheading")


def test_help_search_wraps(help_dlg):
    assert help_dlg.find_text("choose_file")
    first = help_dlg.browser.textCursor().selectionStart()
    while help_dlg.find_text("choose_file"):
        if help_dlg.browser.textCursor().selectionStart() == first:
            break
    assert help_dlg.browser.textCursor().selectionStart() == first
    assert not help_dlg.find_text("zzz-not-in-the-reference")


def test_help_example_link_opens_example(help_dlg):
    from PySide6.QtCore import QUrl
    from opensak.macro.examples import list_examples
    names = list_examples()
    if not names:
        pytest.skip("no example macros in this checkout")
    got = []
    help_dlg.open_example.connect(got.append)
    help_dlg._on_link(QUrl(f"../../macros/examples/{names[0]}"))
    help_dlg._on_link(QUrl("../../macros/types/opensak.lua"))
    assert got == [names[0]]


def test_help_example_opens_in_editor(make_dialog, macros_dir, monkeypatch):
    from opensak.macro.examples import list_examples
    names = list_examples()
    if not names:
        pytest.skip("no example macros in this checkout")
    d = make_dialog()
    d._show_help()
    d._help.open_example.emit(names[0])
    assert d.path == macros_dir / "examples" / names[0]


@pytest.mark.parametrize("answer, expected", [
    (False, "-- edited"),     # open my copy
    (True, None),             # restore original
])
def test_open_example_asks_when_copy_differs(make_dialog, macros_dir, monkeypatch,
                                             answer, expected):
    from opensak.macro.examples import bundled_examples_dir, install_example
    name = "corrected_coords_from_csv.lua"
    path = install_example(name)
    original = path.read_text(encoding="utf-8")
    d = make_dialog()

    asked = []
    monkeypatch.setattr(d, "_ask_restore_example", lambda n: asked.append(n) or answer)
    d.open_example(name)        # unchanged copy: no question
    assert asked == []

    path.write_text("-- edited", encoding="utf-8")
    d.open_example(name)
    assert asked == [name]
    assert d.path == path
    assert d._editor.toPlainText() == (expected or original)
    assert path.read_text(encoding="utf-8") == (expected or
        (bundled_examples_dir() / name).read_text(encoding="utf-8"))


def test_open_example_cancel_keeps_editor_and_copy(make_dialog, macros_dir, monkeypatch):
    from opensak.macro.examples import install_example
    name = "corrected_coords_from_csv.lua"
    path = install_example(name)
    path.write_text("-- edited", encoding="utf-8")
    d = make_dialog()
    before = d._editor.toPlainText()
    monkeypatch.setattr(d, "_ask_restore_example", lambda n: None)
    d.open_example(name)
    assert d._editor.toPlainText() == before
    assert path.read_text(encoding="utf-8") == "-- edited"


def test_window_is_wide_enough_for_the_whole_toolbar(make_dialog):
    from PySide6.QtWidgets import QApplication, QToolBar
    d = make_dialog()
    toolbar = d.findChild(QToolBar)
    # The widening is capped to 90% of the screen; CI's offscreen screen is
    # small, so only expect as much as that cap allows.
    screen = d.screen() or QApplication.primaryScreen()
    cap = int(screen.availableGeometry().width() * 0.9)
    assert d.width() >= min(toolbar.sizeHint().width(), cap)

"""tests/e2e-tests/test_e2e_macro.py — Lua macro dialog drives the real main window."""

import pytest

from opensak.utils import flags


def _run_macro(window, source: str) -> str:
    window._open_macro_dialog()
    dlg = window._macro_dialog
    dlg._editor.setPlainText(source)
    dlg._btn_run.click()
    return dlg._output.toPlainText()


def test_macro_filter_selects_caches(seeded_window):
    out = _run_macro(seeded_window, """
        local n = opensak.filter{ code = "GCAAA0", label = "From macro" }
        print("n=" .. n .. " shown=" .. opensak.count())
    """)
    assert "n=2 shown=2" in out
    assert seeded_window._cache_table.row_count() == 2
    assert seeded_window._active_filter_name == "From macro"
    assert "From macro" in seeded_window._filter_lbl.text()


def test_macro_filter_without_match_keeps_view(seeded_window):
    before = seeded_window._cache_table.row_count()
    out = _run_macro(seeded_window, 'print(opensak.filter{ name = "no such cache" })')
    assert out.splitlines()[0] == "0"
    assert seeded_window._cache_table.row_count() == before


def test_macro_count_is_current_right_after_clear_filter(seeded_window):
    # clear_filter() reloads the table asynchronously, so count() must not
    # read the table's (still filtered) row count.
    out = _run_macro(seeded_window, """
        local all = opensak.count()
        opensak.filter{ code = "GCAAA0" }
        local filtered = opensak.count()
        opensak.clear_filter()
        print(filtered, opensak.count() == all, all > filtered)
    """)
    assert out.splitlines()[0] == "2\ttrue\ttrue"


def test_macro_reads_selected_cache_and_filtered_codes(seeded_window):
    _run_macro(seeded_window, 'opensak.filter{ code = "GCAAA0" }')
    table = seeded_window._cache_table
    shown = [table._model.cache_at(i).gc_code for i in range(table.row_count())]
    table.select_by_gc_code(shown[1])

    out = _run_macro(seeded_window, """
        local c = opensak.current()
        print(c.code, c.name == opensak.cache(c.code).name, table.concat(opensak.selected(), ","))
        print(table.concat(opensak.codes(), ","))
    """)

    assert out.splitlines()[:2] == [f"{shown[1]}\ttrue\t{shown[1]}", ",".join(shown)]




@pytest.fixture
def macros_flag(monkeypatch, request):
    # Must be listed before seeded_window: the menu is built in MainWindow.__init__.
    monkeypatch.setattr(flags, "lua_macros", request.param)
    return request.param


@pytest.mark.parametrize("macros_flag", [True, False], indirect=True)
def test_macro_menu_follows_tools_menu_and_is_gated(macros_flag, seeded_window):
    from opensak.lang import tr
    actions = seeded_window.menuBar().actions()
    titles = [a.text() for a in actions]
    macros = titles.index(tr("menu_macros"))
    assert macros == titles.index(tr("menu_gc_tools")) + 1
    assert actions[macros].isVisible() is macros_flag


def test_macro_error_is_shown_in_output(seeded_window):
    out = _run_macro(seeded_window, "opensak.filter{ bogus = 1 }")
    assert "Macro error" in out and "unknown filter key" in out


def test_quitting_asks_about_unsaved_macro_edits(seeded_window, monkeypatch):
    from unittest.mock import MagicMock
    from opensak.gui.dialogs import macro_dialog as md
    seeded_window.show()
    seeded_window._open_macro_dialog()
    dlg = seeded_window._macro_dialog
    dlg._editor.selectAll()
    dlg._editor.insertPlainText("print(1)")
    ask = MagicMock(return_value=md.QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(md.QMessageBox, "question", ask)
    seeded_window.close()
    ask.assert_called_once()
    assert seeded_window.isVisible() and dlg.isVisible()
    ask.return_value = md.QMessageBox.StandardButton.Discard
    dlg.close()
    assert not dlg.isVisible()

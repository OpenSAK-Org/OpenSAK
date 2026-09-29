"""tests/e2e-tests/test_e2e_macro.py — Lua macro dialog drives the real main window."""


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


def test_macro_error_is_shown_in_output(seeded_window):
    out = _run_macro(seeded_window, "opensak.filter{ bogus = 1 }")
    assert "Macro error" in out and "unknown filter key" in out

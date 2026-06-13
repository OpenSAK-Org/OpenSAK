"""tests/e2e-tests/test_e2e_cache_detail.py — cache detail panel scenarios."""

import pytest

pytest.importorskip("pytestqt")


def _select_row(window, qtbot, row: int = 0) -> None:
    view = window._cache_table
    view.setCurrentIndex(view.model().index(row, 0))
    qtbot.wait(100)


def _select_by_gc(window, qtbot, gc_code: str) -> None:
    table = window._cache_table
    model = table.model()
    for row in range(model.rowCount()):
        cache = model.cache_at(row)
        if cache and cache.gc_code == gc_code:
            table.setCurrentIndex(model.index(row, 0))
            qtbot.wait(100)
            return
    pytest.fail(f"{gc_code} not found in table")


# ── Detail panel population ────────────────────────────────────────────────────


def test_selecting_cache_shows_name_in_title(seeded_window, qtbot):
    """Clicking the first row sets the detail panel title to that cache's name."""
    window = seeded_window
    _select_row(window, qtbot, 0)

    title = window._detail_panel._title.text()
    assert title != ""
    assert title not in ("Select a cache", "Vælg en cache")


def test_detail_panel_shows_gc_code(seeded_window, qtbot):
    """After selecting GC12345 the detail panel GC-code label reads 'GC12345'."""
    window = seeded_window
    _select_by_gc(window, qtbot, "GC12345")
    assert window._detail_panel._gc_code_lbl.text() == "GC12345"


def test_detail_panel_shows_difficulty_terrain(seeded_window, qtbot):
    """The D/T label is populated after a cache is selected."""
    window = seeded_window
    _select_row(window, qtbot, 0)

    dt = window._detail_panel._dt_lbl.text()
    assert "/" in dt, f"Expected 'D / T' format, got: {dt!r}"
    assert dt != "— / —"


def test_detail_panel_clears_between_caches(seeded_window, qtbot):
    """Selecting a second cache replaces the detail panel content."""
    window = seeded_window
    table = window._cache_table
    model = table.model()

    table.setCurrentIndex(model.index(0, 0))
    qtbot.wait(100)
    gc_first = window._detail_panel._gc_code_lbl.text()

    table.setCurrentIndex(model.index(1, 0))
    qtbot.wait(100)
    gc_second = window._detail_panel._gc_code_lbl.text()

    assert gc_first != gc_second, "Detail panel GC code did not update on row change"


# ── Hint tab ───────────────────────────────────────────────────────────────────


def test_hint_tab_shows_raw_encoded_text(seeded_window, qtbot):
    """The hint browser initially shows the raw (ROT13-encoded) hint text."""
    window = seeded_window
    _select_by_gc(window, qtbot, "GC12345")

    raw = window._detail_panel._hint_browser.toPlainText()
    assert raw == "Under a rock."


def test_decode_button_rot13_decodes_hint(seeded_window, qtbot):
    """Clicking 'Decode' translates 'Under a rock.' via ROT13 to 'Haqre n ebpx.'."""
    window = seeded_window
    _select_by_gc(window, qtbot, "GC12345")

    panel = window._detail_panel
    assert not panel._hint_decoded

    panel._decode_btn.click()
    qtbot.wait(50)

    assert panel._hint_decoded
    assert panel._hint_browser.toPlainText() == "Haqre n ebpx."


def test_encode_button_restores_raw_hint(seeded_window, qtbot):
    """Clicking 'Encode' after 'Decode' restores the original hint text."""
    window = seeded_window
    _select_by_gc(window, qtbot, "GC12345")

    panel = window._detail_panel
    panel._decode_btn.click()
    qtbot.wait(30)
    panel._decode_btn.click()
    qtbot.wait(30)

    assert not panel._hint_decoded
    assert panel._hint_browser.toPlainText() == "Under a rock."


# ── Log search ─────────────────────────────────────────────────────────────────


def test_log_search_filters_visible_log_entries(seeded_window, qtbot):
    """Typing in the log search field re-renders logs to show only matching entries."""
    window = seeded_window
    _select_by_gc(window, qtbot, "GC12345")

    panel = window._detail_panel
    panel._log_search.setText("TFTC")
    qtbot.wait(50)

    html = panel._log_browser.toHtml()
    assert "TFTC" in html
    assert "Could not find it" not in html


def test_log_search_clear_restores_all_logs(seeded_window, qtbot):
    """Clearing the log search field shows all log entries again."""
    window = seeded_window
    _select_by_gc(window, qtbot, "GC12345")

    panel = window._detail_panel
    panel._log_search.setText("TFTC")
    qtbot.wait(50)
    panel._log_search.setText("")
    qtbot.wait(50)

    html = panel._log_browser.toHtml()
    assert "TFTC" in html
    assert "Could not find it" in html

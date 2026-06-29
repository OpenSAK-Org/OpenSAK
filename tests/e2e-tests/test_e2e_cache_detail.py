# tests/e2e-tests/test_e2e_cache_detail.py — cache detail panel scenarios.

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
    # Clicking the first row sets the detail panel title to that cache's name.
    window = seeded_window
    _select_row(window, qtbot, 0)

    title = window._detail_panel._title.text()
    assert title != ""
    assert title not in ("Select a cache", "Vælg en cache")


def test_detail_panel_shows_gc_code(seeded_window, qtbot):
    # After selecting GC12345 the detail panel GC-code label reads 'GC12345'.
    window = seeded_window
    _select_by_gc(window, qtbot, "GC12345")
    assert window._detail_panel._gc_code_lbl.text() == "GC12345"


def test_detail_panel_shows_difficulty_terrain(seeded_window, qtbot):
    # The D/T label is populated after a cache is selected.
    window = seeded_window
    _select_row(window, qtbot, 0)

    dt = window._detail_panel._dt_lbl.text()
    assert "/" in dt, f"Expected 'D / T' format, got: {dt!r}"
    assert dt != "— / —"


def test_detail_panel_clears_between_caches(seeded_window, qtbot):
    # Selecting a second cache replaces the detail panel content.
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
# issue #329: geocaching.com leverer hints i klartekst i moderne PQ'er.
# OpenSAK gætter (via split_hint/vokal-heuristik) hvilken retning der er
# læsbar, og viser altid den SKJULTE udgave som standard (spoiler-
# beskyttelse) — uanset om kildedata reelt var klartekst eller ROT13.
# Test-fixturens hint "Under a rock." er kort (under heuristikkens
# tærskel) og antages derfor at være klartekst, så den skjulte
# standardvisning er dens ROT13-transformation "Haqre n ebpx.".


def test_hint_tab_shows_obscured_text_by_default(seeded_window, qtbot):
    # Hint-browseren viser som udgangspunkt den skjulte (ROT13'ede) udgave,
    # ikke den læsbare klartekst — uanset hvilken retning kildedata var i.
    window = seeded_window
    _select_by_gc(window, qtbot, "GC12345")

    shown = window._detail_panel._hint_browser.toPlainText()
    assert shown == "Haqre n ebpx."


def test_decode_button_reveals_plaintext_hint(seeded_window, qtbot):
    # Klik på 'Decode' afslører den læsbare klartekst.
    window = seeded_window
    _select_by_gc(window, qtbot, "GC12345")

    panel = window._detail_panel
    assert not panel._hint_decoded

    panel._decode_btn.click()
    qtbot.wait(50)

    assert panel._hint_decoded
    assert panel._hint_browser.toPlainText() == "Under a rock."


def test_encode_button_restores_obscured_hint(seeded_window, qtbot):
    # Klik på 'Encode' efter 'Decode' skjuler hint'et igen.
    window = seeded_window
    _select_by_gc(window, qtbot, "GC12345")

    panel = window._detail_panel
    panel._decode_btn.click()
    qtbot.wait(30)
    panel._decode_btn.click()
    qtbot.wait(30)

    assert not panel._hint_decoded
    assert panel._hint_browser.toPlainText() == "Haqre n ebpx."


# ── Log rendering ──────────────────────────────────────────────────────────────


def test_logs_tab_renders_all_log_entries(seeded_window, qtbot):
    # All log entries for the selected cache are displayed in the logs browser.
    window = seeded_window
    _select_by_gc(window, qtbot, "GC12345")

    panel = window._detail_panel
    html = panel._log_browser.toHtml()
    assert "TFTC" in html
    assert "Could not find it" in html


def test_logs_tab_renders_when_some_logs_have_no_date(qtbot, tmp_path, monkeypatch):
    # Regression: sorting mixed datetime/None log_date values crashed with TypeError
    # before the fix (key returned int 0 for None, incomparable with datetime).
    pytest.importorskip("pytestqt")
    from tests.data import (
        cache_wpt, build_gpx, write_gpx,
        make_fake_manager, seed_standard_caches,
    )
    from opensak.db.database import init_db, get_session
    from opensak.importer import import_gpx
    from opensak.lang import load_language
    import opensak.db.manager as mgr_module
    from opensak.gui.mainwindow import MainWindow

    load_language("en")
    db_path = tmp_path / "test_nolog.db"
    init_db(db_path=db_path)

    gpx = build_gpx(cache_wpt(
        "GCNOLOG",
        logs=[
            {"id": 1, "type": "Found it", "date": "2025-06-01T00:00:00Z", "text": "dated log"},
            {"id": 2, "type": "Note",     "date": "",                     "text": "dateless log"},
        ],
    ))
    gpx_file = write_gpx(tmp_path, "nolog.gpx", gpx)
    with get_session() as session:
        import_gpx(gpx_file, session)

    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="NologTest"))
    monkeypatch.setattr(MainWindow, "_initial_load", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_update_background", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_setup_complete", lambda self: None)

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    window._refresh_cache_list()

    _select_by_gc(window, qtbot, "GCNOLOG")

    html = window._detail_panel._log_browser.toHtml()
    assert "dated log" in html
    assert "dateless log" in html

    window.close()
    mgr_module._manager = None

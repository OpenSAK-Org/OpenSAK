"""tests/e2e-tests/test_e2e_import.py — import scenarios (GPX/ZIP + ImportDialog worker)."""

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt


# ── GPX import via direct call ─────────────────────────────────────────────────


def test_gpx_import_populates_table(empty_window, qtbot, tmp_path):
    """After importing a GPX file the main window table shows the imported caches."""
    from opensak.db.database import get_session
    from opensak.importer import import_gpx
    from tests.data import SAMPLE_GPX

    window = empty_window
    assert window._cache_table.row_count() == 0

    gpx_file = tmp_path / "import.gpx"
    gpx_file.write_text(SAMPLE_GPX, encoding="utf-8")

    with get_session() as session:
        import_gpx(gpx_file, session)

    window._refresh_after_import()
    qtbot.wait(50)

    assert window._cache_table.row_count() == 2


def test_duplicate_import_is_idempotent(empty_window, qtbot, tmp_path):
    """Importing the same GPX a second time must not increase the row count."""
    from opensak.db.database import get_session
    from opensak.importer import import_gpx
    from tests.data import SAMPLE_GPX

    window = empty_window
    gpx_file = tmp_path / "dup.gpx"
    gpx_file.write_text(SAMPLE_GPX, encoding="utf-8")

    with get_session() as session:
        import_gpx(gpx_file, session)
    window._refresh_after_import()
    qtbot.wait(50)
    count_after_first = window._cache_table.row_count()

    with get_session() as session:
        import_gpx(gpx_file, session)
    window._refresh_after_import()
    qtbot.wait(50)

    assert window._cache_table.row_count() == count_after_first


def test_zip_import_populates_table(empty_window, qtbot, tmp_path):
    """Importing a PQ ZIP file adds caches to the table."""
    from opensak.db.database import get_session
    from opensak.importer import import_zip
    from tests.data import SAMPLE_GPX, SAMPLE_WPTS_GPX, make_zip

    window = empty_window
    zip_path = make_zip(tmp_path, "pq.zip", {
        "caches.gpx": SAMPLE_GPX,
        "waypoints.gpx": SAMPLE_WPTS_GPX,
    })

    with get_session() as session:
        import_zip(zip_path, session)
    window._refresh_after_import()
    qtbot.wait(50)

    assert window._cache_table.row_count() == 2


# ── ImportDialog background-thread smoke test ──────────────────────────────────


def test_import_dialog_worker_succeeds(qtbot, tmp_path, monkeypatch):
    """
    ImportDialog runs GPX import in a background thread and reports success.
    Tests the dialog's internal worker + signal wiring without going through
    the main window or QFileDialog.
    """
    import opensak.db.manager as mgr_module
    from opensak.db.database import init_db
    from opensak.lang import load_language
    from opensak.gui.dialogs.import_dialog import ImportDialog
    from tests.data import SAMPLE_GPX, make_fake_manager

    load_language("en")

    db_path = tmp_path / "dlg_import.db"
    init_db(db_path=db_path)
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="DlgTest"))

    gpx_file = tmp_path / "dlg_sample.gpx"
    gpx_file.write_text(SAMPLE_GPX, encoding="utf-8")

    dlg = ImportDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)

    from PySide6.QtWidgets import QListWidgetItem
    dlg._selected_paths.append(gpx_file)
    dlg._file_list.addItem(QListWidgetItem(gpx_file.name))
    dlg._import_btn.setEnabled(True)

    with qtbot.waitSignal(dlg.import_completed, timeout=10_000):
        qtbot.mouseClick(dlg._import_btn, Qt.MouseButton.LeftButton)

    assert dlg._any_success
    log_text = dlg._log.toPlainText()
    assert "GC12345" in log_text or "2" in log_text

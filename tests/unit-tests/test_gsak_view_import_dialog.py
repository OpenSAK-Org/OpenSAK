# tests/unit-tests/test_gsak_view_import_dialog.py — GSAK view import dialog:
# view list, selection, the import and the results it prints; plus its
# checkbox in the GSAK import dialog.

import sqlite3
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt

from opensak.gui.dialogs import gsak_view_import_dialog as vdlg
from opensak.gui.dialogs.gsak_view_import_dialog import GsakViewImportDialog, format_result
from opensak.importer.gsak_view_importer import (
    GsakViewImportResult,
    ViewConversion,
    ViewImportEntry,
)


@pytest.fixture
def gsak_db(tmp_path) -> Path:
    path = tmp_path / "gsak.db3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE Settings (Type collate nocase, Description, Data)")
    conn.executemany("INSERT INTO Settings VALUES ('GV', ?, ?)", [
        ("Alpha", "Code=80~C\r\nName=200~N"),
        ("Beta", "Code=80~C\r\nTbLocked=60~T"),
        ("Gamma export", "Code=80~C"),
    ])
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def appdata(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr("opensak.config.get_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "opensak.importer.gsak_filter_importer.active_custom_fields", lambda: None)
    return tmp_path


@pytest.fixture
def dlg(qtbot):
    d = GsakViewImportDialog()
    qtbot.addWidget(d)
    return d


class TestListing:
    def test_lists_views_all_ticked(self, dlg, gsak_db):
        dlg.set_path(gsak_db)
        assert dlg._selected_names() == ["Alpha", "Beta", "Gamma export"]
        assert dlg._import_btn.isEnabled()

    def test_search_and_select_none(self, dlg, gsak_db):
        dlg.set_path(gsak_db)
        dlg._search.setText("export")
        dlg._check_visible(False)
        assert dlg._selected_names() == ["Alpha", "Beta"]
        dlg._search.setText("")
        dlg._check_visible(False)
        assert dlg._selected_names() == []
        assert not dlg._import_btn.isEnabled()

    def test_no_views(self, dlg, tmp_path, monkeypatch):
        shown = []
        monkeypatch.setattr(vdlg.QMessageBox, "information",
                            lambda *a, **k: shown.append(a[2]))
        path = tmp_path / "gsak.db3"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE Settings (Type, Description, Data)")
        conn.close()
        dlg.set_path(path)
        assert shown == [vdlg.tr("gsak_view_import_none_found", name="gsak.db3")]
        assert not dlg._import_btn.isEnabled()

    def test_not_a_settings_db(self, dlg, tmp_path, monkeypatch):
        shown = []
        monkeypatch.setattr(vdlg.QMessageBox, "critical", lambda *a, **k: shown.append(a[2]))
        path = tmp_path / "sqlite.db3"
        sqlite3.connect(path).close()
        dlg.set_path(path)
        assert len(shown) == 1
        assert not dlg._import_btn.isEnabled()


class TestImport:
    def test_writes_selected_views_and_reports_ignored_columns(self, dlg, gsak_db, appdata):
        emitted = []
        dlg.import_completed.connect(lambda: emitted.append(True))
        dlg.set_path(gsak_db)
        dlg._list.item(2).setCheckState(Qt.CheckState.Unchecked)
        dlg._start_import()

        assert {p.name for p in (appdata / "column_views").iterdir()} == {
            "Alpha.json", "Beta.json"}
        assert emitted == [True]
        log = dlg._log.toPlainText()
        assert vdlg.tr("gsak_view_import_ignored_header", count=1) in log
        assert "Beta: TbLocked" in log

    def test_second_run_skips_existing(self, dlg, gsak_db, appdata):
        dlg.set_path(gsak_db)
        dlg._start_import()
        emitted = []
        dlg.import_completed.connect(lambda: emitted.append(True))
        dlg._start_import()
        assert emitted == []
        assert vdlg.tr("gsak_view_import_skipped_header", count=3) in dlg._log.toPlainText()


class TestFormatResult:
    def test_counts_and_failures(self):
        result = GsakViewImportResult(entries=[
            ViewImportEntry("A", ViewConversion("A", ["gc_code"]), written=True),
            ViewImportEntry("B", error="ValueError: bad"),
        ])
        text = format_result(result)
        assert vdlg.tr("gsak_view_import_failed_header", count=1) in text
        assert "B: ValueError: bad" in text
        assert vdlg.tr("gsak_view_import_ignored_header", count=1) not in text


# ── The checkbox in the GSAK import dialog ────────────────────────────────────

def _settings_zip(tmp_path: Path, gsak_db: Path) -> Path:
    archive = tmp_path / "GSAKAuto1.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(gsak_db, "gsak.db3")
    return archive


@pytest.fixture
def import_dlg(qtbot, monkeypatch):
    from opensak.db.manager import get_db_manager
    from opensak.gui.dialogs import gsak_import_dialog as gdlg
    from opensak.gui.dialogs.gsak_import_dialog import GsakImportDialog

    def _start(self):     # run the unpacking on this thread, as in test_gsak_import_dialog
        self.run()
        self.finished.emit()
    monkeypatch.setattr(gdlg.GsakExtractWorker, "start", _start)
    get_db_manager().ensure_active_initialised()
    d = GsakImportDialog()
    qtbot.addWidget(d)
    return d


class TestGsakImportDialogViews:
    def test_view_import_gets_the_unpacked_gsak_db3(self, import_dlg, gsak_db, tmp_path,
                                                    monkeypatch, appdata):
        from opensak.gui.dialogs import gsak_import_dialog as gdlg
        monkeypatch.setattr(import_dlg, "_run_filter_import", lambda p: None)
        monkeypatch.setattr(import_dlg, "_run_location_import", lambda p: None)
        opened = []

        def _exec(self):
            opened.append(self._selected_names())
            self._start_import()
        monkeypatch.setattr(vdlg.GsakViewImportDialog, "exec", _exec)
        emitted = []
        import_dlg.views_imported.connect(lambda: emitted.append(True))

        import_dlg.set_path(_settings_zip(tmp_path, gsak_db))
        assert not import_dlg._views_cb.isHidden() and import_dlg._views_cb.isChecked()
        import_dlg._start_import()

        assert gdlg.tr("gsak_import_done") in import_dlg._log.toPlainText()
        assert opened == [["Alpha", "Beta", "Gamma export"]]
        assert emitted == [True]
        assert import_dlg._temp_dir is None

    def test_views_alone_enable_import(self, import_dlg, gsak_db, tmp_path):
        import_dlg.set_path(_settings_zip(tmp_path, gsak_db))
        import_dlg._filters_cb.setChecked(False)
        import_dlg._locations_cb.setChecked(False)
        assert import_dlg._import_btn.isEnabled()
        import_dlg._views_cb.setChecked(False)
        assert not import_dlg._import_btn.isEnabled()

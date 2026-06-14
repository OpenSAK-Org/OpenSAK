"""tests/unit-tests/test_export_dialogs.py — KML and file (GPX/LOC/GGZ) export dialogs."""

from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QDialog

from opensak.gui.dialogs import file_export_dialog as fed
from opensak.gui.dialogs import kml_export_dialog as ked
from opensak.gui.dialogs.file_export_dialog import FileExportDialog
from opensak.gui.dialogs.file_export_dialog import _ExportWorker as FileWorker
from opensak.gui.dialogs.kml_export_dialog import KmlExportDialog
from opensak.gui.dialogs.kml_export_dialog import _ExportWorker as KmlWorker


def _cache(gc_code="GC12345", latitude=55.0, longitude=12.0):
    return SimpleNamespace(
        id=1, gc_code=gc_code, name="Test", cache_type="Traditional Cache",
        latitude=latitude, longitude=longitude, difficulty=2.0, terrain=3.0,
        placed_by="Owner", available=True, archived=False, country="Denmark",
        encoded_hints=None, hidden_date=None, logs=[], user_note=None,
        container="Small", found=False, short_description="", waypoints=[],
    )


# ── KML export worker ─────────────────────────────────────────────────────────

class TestKmlExportWorker:
    def test_run_success_emits_count(self, monkeypatch):
        monkeypatch.setattr(ked, "export_kml", lambda *a, **k: 7)
        w = KmlWorker([], "/tmp/x.kml", True, True)
        got = []
        w.finished.connect(got.append)
        w.run()
        assert got == [7]

    def test_run_error_emits_message(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("kaboom")
        monkeypatch.setattr(ked, "export_kml", boom)
        w = KmlWorker([], "/tmp/x.kml", True, True)
        errs = []
        w.error.connect(errs.append)
        w.run()
        assert errs and "kaboom" in errs[0]


# ── KML export dialog ─────────────────────────────────────────────────────────

class TestKmlExportDialog:
    def test_default_path_ends_with_kml(self, qtbot):
        dlg = KmlExportDialog([_cache()])
        qtbot.addWidget(dlg)
        assert dlg._path_edit.text().endswith(".kml")

    def test_browse_appends_extension(self, qtbot, monkeypatch):
        dlg = KmlExportDialog([])
        qtbot.addWidget(dlg)
        monkeypatch.setattr(ked.QFileDialog, "getSaveFileName", lambda *a, **k: ("/tmp/foo", "f"))
        dlg._browse()
        assert dlg._path_edit.text() == "/tmp/foo.kml"

    def test_browse_cancel_keeps_path(self, qtbot, monkeypatch):
        dlg = KmlExportDialog([])
        qtbot.addWidget(dlg)
        before = dlg._path_edit.text()
        monkeypatch.setattr(ked.QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
        dlg._browse()
        assert dlg._path_edit.text() == before

    def test_start_export_missing_path_warns(self, qtbot, monkeypatch):
        dlg = KmlExportDialog([])
        qtbot.addWidget(dlg)
        dlg._path_edit.setText("   ")
        warn = MagicMock()
        monkeypatch.setattr(ked.QMessageBox, "warning", warn)
        dlg._start_export()
        warn.assert_called_once()

    def test_start_export_launches_worker(self, qtbot, monkeypatch):
        dlg = KmlExportDialog([])
        qtbot.addWidget(dlg)
        started = []

        class FakeWorker:
            def __init__(self, **kw):
                self.finished = MagicMock()
                self.error = MagicMock()

            def start(self):
                started.append(True)

        monkeypatch.setattr(ked, "_ExportWorker", FakeWorker)
        dlg._path_edit.setText("/tmp/out.kml")
        dlg._start_export()
        assert started == [True]
        assert dlg._export_btn.isEnabled() is False
        assert not dlg._progress.isHidden()

    def test_on_finished_accepts(self, qtbot, monkeypatch):
        dlg = KmlExportDialog([])
        qtbot.addWidget(dlg)
        monkeypatch.setattr(ked.QMessageBox, "information", MagicMock())
        dlg._on_finished(3)
        assert dlg._progress.isHidden()
        assert dlg.result() == QDialog.DialogCode.Accepted

    def test_on_error_reenables(self, qtbot, monkeypatch):
        dlg = KmlExportDialog([])
        qtbot.addWidget(dlg)
        monkeypatch.setattr(ked.QMessageBox, "critical", MagicMock())
        dlg._export_btn.setEnabled(False)
        dlg._on_error("oops")
        assert dlg._export_btn.isEnabled() is True
        assert dlg._progress.isHidden()


# ── File export worker ────────────────────────────────────────────────────────

class TestFileExportWorker:
    @pytest.mark.parametrize("fmt,suffix", [("gpx", ".gpx"), ("loc", ".loc"), ("ggz", ".ggz")])
    def test_run_writes_file(self, tmp_path, fmt, suffix):
        out = tmp_path / f"export{suffix}"
        w = FileWorker([_cache()], out, fmt)
        msgs = []
        w.finished.connect(msgs.append)
        w.run()
        assert out.exists()
        assert msgs  # success message emitted

    def test_run_error_emits_traceback(self, tmp_path, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("gen failed")
        monkeypatch.setattr("opensak.gps.garmin.generate_gpx", boom)
        w = FileWorker([_cache()], tmp_path / "x.gpx", "gpx")
        errs = []
        w.error.connect(errs.append)
        w.run()
        assert errs and "gen failed" in errs[0]


# ── File export dialog ────────────────────────────────────────────────────────

class TestFileExportDialog:
    def test_current_fmt_reflects_radio(self, qtbot):
        dlg = FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        assert dlg._current_fmt() == "gpx"
        dlg._btn_loc.setChecked(True)
        assert dlg._current_fmt() == "loc"
        dlg._btn_ggz.setChecked(True)
        assert dlg._current_fmt() == "ggz"

    def test_do_export_cancel_does_nothing(self, qtbot, monkeypatch):
        dlg = FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        monkeypatch.setattr(fed.QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
        dlg._do_export()
        assert dlg._btn_export.isEnabled() is True  # untouched

    def test_do_export_launches_worker_with_suffix(self, qtbot, monkeypatch):
        dlg = FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        monkeypatch.setattr(fed.QFileDialog, "getSaveFileName", lambda *a, **k: ("/tmp/noext", "f"))
        captured = {}

        class FakeWorker:
            def __init__(self, caches, output_path, fmt):
                captured["path"] = output_path
                self.finished = MagicMock()
                self.error = MagicMock()

            def start(self):
                captured["started"] = True

        monkeypatch.setattr(fed, "_ExportWorker", FakeWorker)
        dlg._do_export()
        assert captured["started"] is True
        assert str(captured["path"]).endswith(".gpx")
        assert dlg._btn_export.isEnabled() is False

    def test_on_success_shows_message(self, qtbot):
        dlg = FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        dlg._on_success("done")
        assert "done" in dlg._log.toPlainText()
        assert dlg._btn_export.isEnabled() is True

    def test_on_error_shows_message(self, qtbot, monkeypatch):
        dlg = FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        monkeypatch.setattr(fed.QMessageBox, "critical", MagicMock())
        dlg._on_error("bad")
        assert "bad" in dlg._log.toPlainText()
        assert dlg._btn_export.isEnabled() is True

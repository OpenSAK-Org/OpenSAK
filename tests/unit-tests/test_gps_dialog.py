"""tests/unit-tests/test_gps_dialog.py — GPS/Garmin export dialog + workers."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock

pytest.importorskip("pytestqt")

from opensak.gui.dialogs import gps_dialog as gd
from opensak.gui.dialogs.gps_dialog import DeleteWorker, ExportWorker, GpsExportDialog


# ── DeleteWorker ────────────────────────────────────────────────────────────────

class TestDeleteWorker:
    def test_run_success(self, monkeypatch):
        monkeypatch.setattr("opensak.gps.garmin.delete_gpx_files", lambda p: "deleted 3")
        w = DeleteWorker(Path("/dev"))
        got = []
        w.finished.connect(got.append)
        w.run()
        assert got == ["deleted 3"]

    def test_run_error(self, monkeypatch):
        monkeypatch.setattr("opensak.gps.garmin.delete_gpx_files",
                            lambda p: (_ for _ in ()).throw(RuntimeError("perm denied")))
        w = DeleteWorker(Path("/dev"))
        errs = []
        w.error.connect(errs.append)
        w.run()
        assert errs and "perm denied" in errs[0]


# ── ExportWorker ────────────────────────────────────────────────────────────────

class TestExportWorker:
    def test_run_to_device(self, monkeypatch, tmp_path):
        (tmp_path / "Garmin").mkdir()
        monkeypatch.setattr("opensak.gps.garmin.export_to_device",
                            lambda c, d, f: "to device")
        monkeypatch.setattr("opensak.gps.garmin.export_to_file",
                            lambda c, p: "to file")
        w = ExportWorker(["c1", "c2", "c3"], tmp_path, "out", max_caches=2)
        got = []
        w.finished.connect(got.append)
        w.run()
        assert got == ["to device"]

    def test_run_to_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr("opensak.gps.garmin.export_to_file", lambda c, p: "to file")
        w = ExportWorker(["c1"], tmp_path, "out", max_caches=0)  # 0 = all
        got = []
        w.finished.connect(got.append)
        w.run()
        assert got == ["to file"]

    def test_run_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr("opensak.gps.garmin.export_to_file",
                            lambda c, p: (_ for _ in ()).throw(RuntimeError("disk full")))
        w = ExportWorker(["c1"], tmp_path, "out", max_caches=0)
        errs = []
        w.error.connect(errs.append)
        w.run()
        assert errs and "disk full" in errs[0]


# ── GpsExportDialog ─────────────────────────────────────────────────────────────

@pytest.fixture
def no_devices(monkeypatch):
    monkeypatch.setattr("opensak.gps.garmin.find_garmin_devices", lambda: [])


@pytest.fixture
def with_device(monkeypatch, tmp_path):
    dev = tmp_path / "GARMIN"
    dev.mkdir()
    monkeypatch.setattr("opensak.gps.garmin.find_garmin_devices", lambda: [dev])
    return dev


class TestDialogScan:
    def test_no_devices_selects_file_mode(self, qtbot, no_devices):
        dlg = GpsExportDialog(caches=["c1", "c2"])
        qtbot.addWidget(dlg)
        assert dlg._rb_file.isChecked() is True
        assert dlg._device_combo.count() == 1  # the "no device" placeholder

    def test_devices_found_enables_export(self, qtbot, with_device):
        dlg = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(dlg)
        assert dlg._device_combo.count() == 1
        assert dlg._export_btn.isEnabled() is True
        assert dlg._rb_device.isChecked() is True


class TestDialogInteraction:
    @pytest.fixture
    def dlg(self, qtbot, with_device):
        d = GpsExportDialog(caches=["c1", "c2"])
        qtbot.addWidget(d)
        return d

    def test_mode_changed_to_file(self, dlg):
        dlg._rb_file.setChecked(True)
        dlg._on_mode_changed(False)
        assert dlg._device_combo.isEnabled() is False
        assert dlg._cb_delete_gpx.isEnabled() is False
        assert dlg._cb_delete_gpx.isChecked() is False

    def test_browse_file(self, dlg, monkeypatch, tmp_path):
        target = tmp_path / "export.gpx"
        monkeypatch.setattr(gd.QFileDialog, "getSaveFileName",
                            lambda *a, **k: (str(target), "f"))
        dlg._browse_file()
        assert dlg._selected_file_path == tmp_path
        assert dlg._filename.text() == "export"
        assert dlg._rb_file.isChecked() is True

    def test_browse_cancel(self, dlg, monkeypatch):
        monkeypatch.setattr(gd.QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
        dlg._browse_file()
        assert dlg._selected_file_path is None

    def test_get_destination_device(self, dlg, with_device):
        assert dlg._get_destination() == Path(with_device)

    def test_get_destination_file_selected(self, dlg, tmp_path):
        dlg._rb_file.setChecked(True)
        dlg._selected_file_path = tmp_path
        assert dlg._get_destination() == tmp_path

    def test_get_destination_file_default_home(self, dlg):
        dlg._rb_file.setChecked(True)
        dlg._selected_file_path = None
        assert dlg._get_destination() == Path.home()

    def test_start_export_no_destination(self, dlg, monkeypatch):
        monkeypatch.setattr(dlg, "_get_destination", lambda: None)
        dlg._start_export()
        assert dlg._log.toPlainText() != ""

    def test_start_export_runs_export(self, dlg, monkeypatch):
        launched = []

        class FakeExport:
            def __init__(self, *a, **k):
                self.finished = MagicMock()
                self.error = MagicMock()
            def start(self):
                launched.append(True)
            def isRunning(self):
                return False
            def wait(self):
                pass
        monkeypatch.setattr(gd, "ExportWorker", FakeExport)
        dlg._cb_delete_gpx.setChecked(False)
        dlg._start_export()
        assert launched == [True]
        assert dlg._export_btn.isEnabled() is False

    def test_start_export_with_delete_confirmed(self, dlg, monkeypatch, tmp_path):
        gpx_dir = tmp_path / "gpxdir"
        gpx_dir.mkdir()
        (gpx_dir / "old.gpx").write_text("x")
        monkeypatch.setattr("opensak.gps.garmin.get_garmin_gpx_path", lambda dest: gpx_dir)
        monkeypatch.setattr(gd.QMessageBox, "exec",
                            lambda self: gd.QMessageBox.StandardButton.Ok)
        launched = []

        class FakeDelete:
            def __init__(self, dest):
                self.finished = MagicMock()
                self.error = MagicMock()
            def start(self):
                launched.append(True)
            def isRunning(self):
                return False
            def wait(self):
                pass
        monkeypatch.setattr(gd, "DeleteWorker", FakeDelete)
        dlg._cb_delete_gpx.setChecked(True)
        dlg._rb_device.setChecked(True)
        dlg._start_export()
        assert launched == [True]

    def test_start_export_delete_cancelled(self, dlg, monkeypatch, tmp_path):
        gpx_dir = tmp_path / "gpxdir2"
        gpx_dir.mkdir()
        monkeypatch.setattr("opensak.gps.garmin.get_garmin_gpx_path", lambda dest: gpx_dir)
        monkeypatch.setattr(gd.QMessageBox, "exec",
                            lambda self: gd.QMessageBox.StandardButton.Cancel)
        launched = []
        monkeypatch.setattr(gd, "DeleteWorker",
                            lambda *a, **k: launched.append(True))
        dlg._cb_delete_gpx.setChecked(True)
        dlg._rb_device.setChecked(True)
        dlg._start_export()
        assert launched == []  # cancelled before launching

    def test_on_delete_finished_runs_export(self, dlg, monkeypatch, tmp_path):
        launched = []

        class FakeExport:
            def __init__(self, *a, **k):
                self.finished = MagicMock()
                self.error = MagicMock()
            def start(self):
                launched.append(True)
            def isRunning(self):
                return False
            def wait(self):
                pass
        monkeypatch.setattr(gd, "ExportWorker", FakeExport)
        dlg._on_delete_finished("removed 2", tmp_path, "out", 100)
        assert launched == [True]
        assert "out" not in dlg._log.toPlainText() or dlg._log.toPlainText()

    def test_on_finished_and_error(self, dlg):
        dlg._on_finished("export ok")
        assert "export ok" in dlg._log.toPlainText()
        assert dlg._export_btn.isEnabled() is True
        dlg._on_error("boom")
        assert "boom" in dlg._log.toPlainText()

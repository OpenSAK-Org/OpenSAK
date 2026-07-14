# tests/unit-tests/test_gps_dialog.py — GPS/Garmin export dialog + workers.

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
                            lambda c, d, f, progress_cb=None: "to device")
        monkeypatch.setattr("opensak.gps.garmin.export_to_file",
                            lambda c, p, progress_cb=None: "to file")
        w = ExportWorker(["c1", "c2", "c3"], tmp_path, "out", max_caches=2)
        got = []
        w.finished.connect(got.append)
        w.run()
        assert got == ["to device"]

    def test_run_to_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr("opensak.gps.garmin.export_to_file",
                            lambda c, p, progress_cb=None: "to file")
        w = ExportWorker(["c1"], tmp_path, "out", max_caches=0)  # 0 = all
        got = []
        w.finished.connect(got.append)
        w.run()
        assert got == ["to file"]

    def test_run_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr("opensak.gps.garmin.export_to_file",
                            lambda c, p, progress_cb=None: (_ for _ in ()).throw(RuntimeError("disk full")))
        w = ExportWorker(["c1"], tmp_path, "out", max_caches=0)
        errs = []
        w.error.connect(errs.append)
        w.run()
        assert errs and "disk full" in errs[0]

    def test_run_emits_progress(self, monkeypatch, tmp_path):
        # Drive the real generator so progress_cb fires per cache.
        from types import SimpleNamespace

        def _cache(gc):
            return SimpleNamespace(
                id=1, gc_code=gc, name="n", cache_type="Traditional Cache",
                latitude=55.0, longitude=12.0, difficulty=1.0, terrain=1.0,
                placed_by="o", available=True, archived=False, country="DK",
                encoded_hints=None, hidden_date=None, logs=[], user_note=None,
                container="Small", found=False,
            )

        caches = [_cache(f"GC{i}") for i in range(3)]
        w = ExportWorker(caches, tmp_path, "out", max_caches=0)  # file mode
        seen = []
        w.progress.connect(lambda d, t: seen.append((d, t)))
        w.run()
        assert seen and seen[-1] == (3, 3)


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
                self.progress = MagicMock()
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
                self.progress = MagicMock()
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

    def test_on_progress_makes_bar_determinate(self, dlg):
        dlg._reset_progress()
        assert dlg._progress.maximum() == 0  # indeterminate
        dlg._on_progress(3, 10)
        assert dlg._progress.maximum() == 10
        assert dlg._progress.value() == 3
        assert dlg._progress.isTextVisible() is True

    def test_on_progress_ignores_zero_total(self, dlg):
        dlg._reset_progress()
        dlg._on_progress(0, 0)
        assert dlg._progress.maximum() == 0  # still indeterminate


# ── Issue #501: file-mode export must not silently overwrite ───────────────────

class _FakeExportWorker:
    """Minimal ExportWorker stand-in — records that .start() was called."""
    launched: list

    def __init__(self, *a, **k):
        self.finished = MagicMock()
        self.error = MagicMock()
        self.progress = MagicMock()

    def start(self):
        type(self).launched.append(True)

    def isRunning(self):
        return False

    def wait(self):
        pass


class TestFilenameCollisionPrompt:
    @pytest.fixture
    def dlg(self, qtbot, with_device, tmp_path):
        d = GpsExportDialog(caches=["c1", "c2"])
        qtbot.addWidget(d)
        d._rb_file.setChecked(True)
        d._selected_file_path = tmp_path
        d._export_format = "ggz"
        return d

    def _install_fake_export(self, monkeypatch):
        launched: list = []
        _FakeExportWorker.launched = launched
        monkeypatch.setattr(gd, "ExportWorker", _FakeExportWorker)
        return launched

    def test_no_collision_runs_export_without_prompting(self, dlg, monkeypatch, tmp_path):
        prompted = []
        monkeypatch.setattr(gd.QInputDialog, "getText",
                             lambda *a, **k: (prompted.append(True), ("x", True))[1])
        launched = self._install_fake_export(monkeypatch)
        dlg._filename.setText("myexport")
        dlg._start_export()
        assert launched == [True]
        assert prompted == []  # no existing file → never prompted
        assert dlg._filename.text() == "myexport"

    def test_collision_prompts_and_uses_new_name(self, dlg, monkeypatch, tmp_path):
        (tmp_path / "opensak.ggz").write_text("existing")
        monkeypatch.setattr(gd.QInputDialog, "getText",
                             lambda *a, **k: ("renamed", True))
        launched = self._install_fake_export(monkeypatch)
        dlg._filename.setText("opensak")
        dlg._start_export()
        assert launched == [True]
        assert dlg._filename.text() == "renamed"

    def test_collision_cancelled_does_not_export(self, dlg, monkeypatch, tmp_path):
        (tmp_path / "opensak.ggz").write_text("existing")
        monkeypatch.setattr(gd.QInputDialog, "getText",
                             lambda *a, **k: ("", False))
        launched = self._install_fake_export(monkeypatch)
        dlg._filename.setText("opensak")
        dlg._start_export()
        assert launched == []

    def test_collision_reprompts_if_new_name_also_exists(self, dlg, monkeypatch, tmp_path):
        (tmp_path / "opensak.ggz").write_text("existing")
        (tmp_path / "also_taken.ggz").write_text("existing too")
        responses = iter([("also_taken", True), ("free_name", True)])
        monkeypatch.setattr(gd.QInputDialog, "getText",
                             lambda *a, **k: next(responses))
        launched = self._install_fake_export(monkeypatch)
        dlg._filename.setText("opensak")
        dlg._start_export()
        assert launched == [True]
        assert dlg._filename.text() == "free_name"

    def test_prompt_suggests_next_available_name(self, dlg, monkeypatch, tmp_path):
        (tmp_path / "opensak.ggz").write_text("x")
        (tmp_path / "opensak1.ggz").write_text("x")
        captured = {}

        def fake_get_text(parent, title, label, text=""):
            captured["suggestion"] = text
            return ("", False)  # cancel — we only care about the suggestion
        monkeypatch.setattr(gd.QInputDialog, "getText", fake_get_text)
        dlg._prompt_new_filename(tmp_path / "opensak.ggz")
        assert captured["suggestion"] == "opensak2"

    def test_device_mode_never_prompts(self, qtbot, with_device, tmp_path, monkeypatch):
        # Device exports intentionally keep syncing to the same canonical
        # Garmin/GPX path each time — the collision check must not apply.
        d = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(d)
        d._rb_device.setChecked(True)
        d._cb_delete_gpx.setChecked(False)
        prompted = []
        monkeypatch.setattr(gd.QInputDialog, "getText",
                             lambda *a, **k: (prompted.append(True), ("x", True))[1])
        launched = self._install_fake_export(monkeypatch)
        d._start_export()
        assert launched == [True]
        assert prompted == []

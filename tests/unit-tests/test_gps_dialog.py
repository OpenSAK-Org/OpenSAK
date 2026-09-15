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
                id=1, gc_code=gc, gc_cache_id=None, name="n", cache_type="Traditional Cache",
                latitude=55.0, longitude=12.0, difficulty=1.0, terrain=1.0,
                placed_by="o", owner_name=None, owner_id=None,
                available=True, archived=False, country="DK", state=None,
                encoded_hints=None, hidden_date=None, logs=[], user_note=None,
                container="Small", found=False,
                short_description=None, short_desc_html=False,
                long_description=None, long_desc_html=False, attributes=[],
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
            def __init__(self, dest, export_format="gpx"):
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

    def test_on_delete_failure_does_not_run_export(self, dlg, monkeypatch, tmp_path):
        run_export = MagicMock()
        monkeypatch.setattr(dlg, "_run_export", run_export)
        result = SimpleNamespace(success=False, failed_count=1)
        result.__str__ = lambda: "delete failed"

        dlg._on_delete_finished(result, tmp_path, "out", 100)

        run_export.assert_not_called()

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

    def test_device_mode_prompts_on_collision(self, qtbot, with_device, tmp_path, monkeypatch):
        # Follow-up to #656 (CheminerWill): "Send to GPS" silently overwrote
        # a same-named file on the device with no warning. Device-mode
        # exports must now prompt on collision just like file-mode does.
        from opensak.gps.garmin import get_garmin_gpx_path
        gpx_dir = get_garmin_gpx_path(with_device)
        gpx_dir.mkdir(parents=True)
        (gpx_dir / "opensak.gpx").write_text("existing")

        d = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(d)
        d._cb_use_db_name.setChecked(False)  # keep filename deterministic here
        d._filename.setText("opensak")
        d._rb_device.setChecked(True)
        d._cb_delete_gpx.setChecked(False)
        monkeypatch.setattr(gd.QInputDialog, "getText",
                             lambda *a, **k: ("renamed", True))
        launched = self._install_fake_export(monkeypatch)
        d._start_export()
        assert launched == [True]
        assert d._filename.text() == "renamed"

    def test_device_mode_no_collision_runs_without_prompting(self, qtbot, with_device, monkeypatch):
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
        assert prompted == []  # no existing file on device → never prompted

    def test_device_mode_delete_checked_skips_collision_prompt(
        self, qtbot, with_device, tmp_path, monkeypatch,
    ):
        # When "delete old files first" is checked, the old file is about to
        # be wiped anyway, so there's nothing to collide with — go straight
        # to the delete-confirmation flow instead of the rename prompt.
        from opensak.gps.garmin import get_garmin_gpx_path
        gpx_dir = get_garmin_gpx_path(with_device)
        gpx_dir.mkdir(parents=True)
        (gpx_dir / "opensak.gpx").write_text("existing")

        d = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(d)
        d._rb_device.setChecked(True)
        d._cb_delete_gpx.setChecked(True)
        prompted = []
        monkeypatch.setattr(gd.QInputDialog, "getText",
                             lambda *a, **k: (prompted.append(True), ("x", True))[1])
        monkeypatch.setattr(
            gd.QMessageBox, "exec",
            lambda self: gd.QMessageBox.StandardButton.Ok,
        )

        class FakeDelete:
            def __init__(self, dest, export_format="gpx"):
                self.finished = MagicMock()
                self.error = MagicMock()
            def start(self):
                pass
            def isRunning(self):
                return False
            def wait(self):
                pass
        monkeypatch.setattr(gd, "DeleteWorker", FakeDelete)

        d._start_export()
        assert prompted == []  # collision-rename prompt skipped


class TestDeleteCheckboxFormat:
    """Regression tests for #656 follow-up: the delete-old-files checkbox
    used to only be enabled for GPX device exports; it now applies to GGZ
    device exports too (CheminerWill's report covered both formats)."""

    def test_checkbox_enabled_for_gpx_device_mode(self, qtbot, with_device):
        d = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(d)
        d._rb_device.setChecked(True)
        d._rb_gpx.setChecked(True)
        d._on_format_changed()
        assert d._cb_delete_gpx.isEnabled() is True

    def test_checkbox_enabled_for_ggz_device_mode(self, qtbot, with_device):
        d = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(d)
        d._rb_device.setChecked(True)
        d._rb_ggz.setChecked(True)
        d._on_format_changed()
        assert d._cb_delete_gpx.isEnabled() is True

    def test_checkbox_disabled_for_file_mode(self, qtbot, with_device, tmp_path):
        d = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(d)
        d._rb_file.setChecked(True)
        d._selected_file_path = tmp_path
        d._on_mode_changed(False)
        assert d._cb_delete_gpx.isEnabled() is False
        assert d._cb_delete_gpx.isChecked() is False

class TestUseDatabaseNameCheckbox:
    """Regression tests for the community-suggested 'use database name as
    filename' toggle on #656 (GSAK has an equivalent feature). Default is
    checked (Allan's decision), applying to both file-mode and device-mode."""

    def test_checked_by_default(self, qtbot):
        d = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(d)
        assert d._cb_use_db_name.isChecked() is True

    def test_autofills_from_active_database(self, qtbot, monkeypatch):
        fake_manager = SimpleNamespace(active=SimpleNamespace(name="MyGeocaches"))
        monkeypatch.setattr(
            "opensak.db.manager.get_db_manager", lambda: fake_manager,
        )
        d = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(d)
        assert d._filename.text() == "MyGeocaches"

    def test_falls_back_to_opensak_when_no_active_database(self, qtbot, monkeypatch):
        fake_manager = SimpleNamespace(active=None)
        monkeypatch.setattr(
            "opensak.db.manager.get_db_manager", lambda: fake_manager,
        )
        d = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(d)
        assert d._filename.text() == "opensak"

    def test_unchecking_does_not_clear_current_filename(self, qtbot, monkeypatch):
        fake_manager = SimpleNamespace(active=SimpleNamespace(name="MyGeocaches"))
        monkeypatch.setattr(
            "opensak.db.manager.get_db_manager", lambda: fake_manager,
        )
        d = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(d)
        assert d._filename.text() == "MyGeocaches"
        d._cb_use_db_name.setChecked(False)
        assert d._filename.text() == "MyGeocaches"  # untouched, not reset

    def test_rechecking_refills_from_active_database(self, qtbot, monkeypatch):
        fake_manager = SimpleNamespace(active=SimpleNamespace(name="MyGeocaches"))
        monkeypatch.setattr(
            "opensak.db.manager.get_db_manager", lambda: fake_manager,
        )
        d = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(d)
        d._cb_use_db_name.setChecked(False)
        d._filename.setText("something_else")
        d._cb_use_db_name.setChecked(True)
        assert d._filename.text() == "MyGeocaches"

    def test_unchecked_state_persists_to_next_dialog(self, qtbot):
        # CheminerWill's follow-up suggestion on #656: remember the
        # checkbox state across exports, since some testers frequently
        # export named filtered subsets and don't want the filename
        # auto-overwritten every time.
        d1 = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(d1)
        d1._cb_use_db_name.setChecked(False)

        d2 = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(d2)
        assert d2._cb_use_db_name.isChecked() is False

    def test_checked_state_persists_to_next_dialog(self, qtbot):
        d1 = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(d1)
        d1._cb_use_db_name.setChecked(False)  # change away from default first
        d1._cb_use_db_name.setChecked(True)   # then explicitly re-check

        d2 = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(d2)
        assert d2._cb_use_db_name.isChecked() is True

    def test_default_is_checked_when_nothing_persisted_yet(self, qtbot):
        # First-ever run (no settings-store key yet) should default to
        # checked, per Allan's decision.
        from opensak.settings_store import get_store
        assert get_store().get(gd._USE_DB_NAME_KEY) is None  # nothing saved yet
        d = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(d)
        assert d._cb_use_db_name.isChecked() is True

    def test_toggling_writes_to_settings_store_immediately(self, qtbot):
        from opensak.settings_store import get_store
        d = GpsExportDialog(caches=["c1"])
        qtbot.addWidget(d)
        d._cb_use_db_name.setChecked(False)
        assert get_store().get(gd._USE_DB_NAME_KEY) is False
        d._cb_use_db_name.setChecked(True)
        assert get_store().get(gd._USE_DB_NAME_KEY) is True


class TestSanitizeDbNameForFilename:
    def test_replaces_invalid_characters(self):
        result = gd._sanitize_db_name_for_filename('My:Cache/Db*Test?"<>|')
        assert result == "My_Cache_Db_Test_____"

    def test_leaves_normal_names_untouched(self):
        assert gd._sanitize_db_name_for_filename("MyGeocaches") == "MyGeocaches"

    def test_strips_surrounding_whitespace_and_dots(self):
        assert gd._sanitize_db_name_for_filename("  MyDb.  ") == "MyDb"

    def test_fully_invalid_name_becomes_underscores(self):
        # "???" sanitizes to "___" — still a valid (if ugly) filename stem,
        # not treated as empty. Only a truly empty result falls back.
        assert gd._sanitize_db_name_for_filename('???') == "___"

    def test_empty_string_falls_back_to_opensak(self):
        assert gd._sanitize_db_name_for_filename("") == "opensak"

# tests/unit-tests/test_gsak_import_dialog.py — GSAK import dialog worker + UI (#469 session 4).

import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock

pytest.importorskip("pytestqt")

from opensak.gui.dialogs import gsak_import_dialog as gdlg
from opensak.gui.dialogs.gsak_import_dialog import GsakImportWorker, GsakImportDialog


def _result(created=1, updated=0, waypoints=0, attributes=0, logs=0, notes=0,
            note_images_replaced=0, trackables=0, corrected=0, skipped=0,
            warnings=None, errors=None):
    return SimpleNamespace(
        created=created, updated=updated, waypoints=waypoints, attributes=attributes,
        logs=logs, notes=notes, note_images_replaced=note_images_replaced,
        trackables=trackables,
        corrected=corrected, skipped=skipped, warnings=warnings or [], errors=errors or [],
    )


@contextlib.contextmanager
def _fake_session():
    yield MagicMock()


# ── GsakImportWorker.run ──────────────────────────────────────────────────────

class TestGsakImportWorker:
    def _patch_common(self, monkeypatch, active_path=Path("/active.db")):
        monkeypatch.setattr("opensak.db.manager.get_db_manager",
                            lambda: SimpleNamespace(active_path=active_path))
        monkeypatch.setattr("opensak.db.database.get_session", _fake_session)

    def test_run_success_emits_result(self, monkeypatch):
        self._patch_common(monkeypatch)
        monkeypatch.setattr(
            "opensak.importer.gsak_importer.import_gsak_db",
            lambda path, session, progress_cb=None: _result(created=48),
        )
        w = GsakImportWorker(Path("/gsak.db3"))
        got = []
        w.result_ready.connect(lambda r: got.append(r))
        w.run()
        assert len(got) == 1 and got[0].created == 48

    def test_run_reports_progress(self, monkeypatch):
        self._patch_common(monkeypatch)

        def fake_import(path, session, progress_cb=None):
            progress_cb(5, 10)
            return _result()

        monkeypatch.setattr("opensak.importer.gsak_importer.import_gsak_db", fake_import)
        w = GsakImportWorker(Path("/gsak.db3"))
        seen = []
        w.progress.connect(lambda done, total: seen.append((done, total)))
        w.run()
        assert seen == [(5, 10)]

    def test_run_exception_emits_error(self, monkeypatch):
        self._patch_common(monkeypatch)
        monkeypatch.setattr(
            "opensak.importer.gsak_importer.import_gsak_db",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        w = GsakImportWorker(Path("/gsak.db3"))
        errs = []
        w.error.connect(lambda m: errs.append(m))
        w.run()
        assert errs and "boom" in errs[0]

    def test_run_switches_and_restores_db(self, monkeypatch):
        self._patch_common(monkeypatch, active_path=Path("/active.db"))
        monkeypatch.setattr("opensak.importer.gsak_importer.import_gsak_db",
                            lambda path, session, progress_cb=None: _result())
        inits = []
        monkeypatch.setattr("opensak.db.database.init_db", lambda **k: inits.append(k.get("db_path")))
        w = GsakImportWorker(Path("/gsak.db3"), target_db_path=Path("/other.db"))
        w.run()
        assert inits == [Path("/other.db"), Path("/active.db")]

    def test_run_no_switch_when_target_is_active(self, monkeypatch):
        self._patch_common(monkeypatch, active_path=Path("/active.db"))
        monkeypatch.setattr("opensak.importer.gsak_importer.import_gsak_db",
                            lambda path, session, progress_cb=None: _result())
        inits = []
        monkeypatch.setattr("opensak.db.database.init_db", lambda **k: inits.append(k.get("db_path")))
        w = GsakImportWorker(Path("/gsak.db3"), target_db_path=Path("/active.db"))
        w.run()
        assert inits == []


# ── GsakImportDialog ──────────────────────────────────────────────────────────

@pytest.fixture
def manager(monkeypatch):
    a = SimpleNamespace(name="Active", path=Path("/active.db"))
    b = SimpleNamespace(name="Other", path=Path("/other.db"))
    mgr = SimpleNamespace(active_path=Path("/active.db"), databases=[a, b])
    monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: mgr)
    return mgr


@pytest.fixture
def dlg(qtbot, manager):
    d = GsakImportDialog()
    qtbot.addWidget(d)
    return d


class TestGsakImportDialog:
    def test_db_combo_populated_active_selected(self, dlg):
        assert dlg._db_combo.count() == 2
        assert dlg._db_combo.currentIndex() == 0

    def test_set_path_enables_import(self, dlg):
        dlg.set_path(Path("/x/Sommerhus.zip"))
        assert dlg._selected_path == Path("/x/Sommerhus.zip")
        assert dlg._import_btn.isEnabled() is True
        assert "Sommerhus.zip" in dlg._file_label.text()

    def test_browse_sets_path(self, dlg, monkeypatch):
        monkeypatch.setattr(gdlg.QFileDialog, "getOpenFileName",
                            lambda *a, **k: ("/d/GSAK_Backup.zip", "f"))
        dlg._browse()
        assert dlg._selected_path == Path("/d/GSAK_Backup.zip")
        assert dlg._import_btn.isEnabled() is True

    def test_browse_cancel_leaves_path_unset(self, dlg, monkeypatch):
        monkeypatch.setattr(gdlg.QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
        dlg._browse()
        assert dlg._selected_path is None
        assert dlg._import_btn.isEnabled() is False

    def test_start_import_no_path_noop(self, dlg):
        dlg._start_import()  # nothing selected -> early return
        assert dlg._progress.isVisible() is False

    def test_start_import_no_db3_found_shows_error(self, dlg, monkeypatch):
        dlg.set_path(Path("/x/not_a_gsak.zip"))
        monkeypatch.setattr(
            "opensak.importer.gsak_importer.find_gsak_db3_in_zip",
            lambda p: (_ for _ in ()).throw(ValueError("no db3")),
        )
        shown = []
        monkeypatch.setattr(gdlg.QMessageBox, "critical", lambda *a, **k: shown.append(a))
        dlg._start_import()
        assert shown
        assert dlg._worker is None

    def test_start_import_no_affected_notes_launches_worker_directly(self, dlg, monkeypatch):
        dlg.set_path(Path("/x/gsak.db3"))
        monkeypatch.setattr("opensak.importer.gsak_importer.find_gsak_db3_in_zip", lambda p: p)
        monkeypatch.setattr(
            "opensak.importer.gsak_importer.scan_gsak_notes_for_embedded_images",
            lambda p: {"affected_notes": 0, "total_images": 0},
        )

        started = []

        class FakeWorker:
            def __init__(self, db3_path, target_db_path=None):
                self.progress = MagicMock()
                self.result_ready = MagicMock()
                self.error = MagicMock()
                self.finished = MagicMock()
                self.deleteLater = MagicMock()
                self.isRunning = MagicMock(return_value=False)
                self.wait = MagicMock()

            def start(self):
                started.append(True)

        monkeypatch.setattr(gdlg, "GsakImportWorker", FakeWorker)
        dlg._start_import()
        assert started == [True]
        assert dlg._import_btn.isEnabled() is False
        assert dlg._worker is not None

    def test_start_import_with_affected_notes_shows_prescan_dialog(self, dlg, monkeypatch):
        dlg.set_path(Path("/x/gsak.db3"))
        monkeypatch.setattr("opensak.importer.gsak_importer.find_gsak_db3_in_zip", lambda p: p)
        monkeypatch.setattr(
            "opensak.importer.gsak_importer.scan_gsak_notes_for_embedded_images",
            lambda p: {"affected_notes": 3, "total_images": 5},
        )

        class FakeBox:
            instances = []

            def __init__(self, parent=None):
                self.buttons = []
                FakeBox.instances.append(self)

            def setWindowTitle(self, t): self.title = t
            def setText(self, t): self.text = t
            def addButton(self, *a, **k):
                btn = MagicMock()
                self.buttons.append(btn)
                return btn
            def setDefaultButton(self, b): self.default = b
            def exec(self): pass
            def clickedButton(self): return self.buttons[-1]  # simulate Cancel clicked

        monkeypatch.setattr(gdlg, "QMessageBox", FakeBox)
        FakeBox.ButtonRole = MagicMock()
        FakeBox.StandardButton = MagicMock()

        started = []
        monkeypatch.setattr(gdlg, "GsakImportWorker",
                            lambda *a, **k: SimpleNamespace(start=lambda: started.append(True)))

        dlg._start_import()
        assert len(FakeBox.instances) == 1  # the pre-scan dialog was shown
        assert started == []  # cancelled -> worker never started

    def test_on_result_appends_summary_and_emits_completed(self, dlg, qtbot):
        dlg._selected_path = Path("/x/gsak.db3")
        completed = []
        dlg.import_completed.connect(lambda: completed.append(True))
        dlg._on_result(_result(created=48, waypoints=18, attributes=341, logs=1378,
                                notes=2, note_images_replaced=1, corrected=1))
        text = dlg._log.toPlainText()
        assert "48" in text
        assert completed == [True]

    def test_on_result_no_changes_does_not_emit_completed(self, dlg):
        dlg._selected_path = Path("/x/gsak.db3")
        completed = []
        dlg.import_completed.connect(lambda: completed.append(True))
        dlg._on_result(_result(created=0, updated=0))
        assert completed == []

    def test_on_error_appends_log(self, dlg):
        dlg._on_error("boom traceback")
        assert "boom traceback" in dlg._log.toPlainText()

    def test_on_progress_determinate(self, dlg):
        dlg._progress.setVisible(True)
        dlg._on_progress(5, 10)
        assert dlg._progress.maximum() == 10
        assert dlg._progress.value() == 5

    def test_on_progress_indeterminate(self, dlg):
        dlg._on_progress(0, -1)
        assert dlg._progress.maximum() == 0

    def test_on_done_resets_ui(self, dlg):
        dlg._import_btn.setEnabled(False)
        dlg._browse_btn.setEnabled(False)
        dlg._progress.setVisible(True)
        dlg._on_done()
        assert dlg._progress.isVisible() is False
        assert dlg._import_btn.isEnabled() is True
        assert dlg._browse_btn.isEnabled() is True

    def test_close_event_waits_for_running_worker(self, dlg):
        worker = MagicMock()
        worker.isRunning.return_value = True
        dlg._worker = worker
        dlg.close()
        worker.wait.assert_called_once()

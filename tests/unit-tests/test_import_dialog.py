"""tests/unit-tests/test_import_dialog.py — GPX/PQ import worker + dialog."""

import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock

pytest.importorskip("pytestqt")

from opensak.gui.dialogs import import_dialog as idlg
from opensak.gui.dialogs.import_dialog import ImportWorker, ImportDialog


def _result(created=1, updated=0, waypoints=0, skipped=0, errors=None):
    return SimpleNamespace(created=created, updated=updated, waypoints=waypoints,
                           skipped=skipped, errors=errors or [])


@contextlib.contextmanager
def _fake_session():
    yield MagicMock()


# ── ImportWorker.run ────────────────────────────────────────────────────────────

class TestImportWorker:
    def _patch_common(self, monkeypatch, active_path=Path("/active.db")):
        monkeypatch.setattr("opensak.db.manager.get_db_manager",
                            lambda: SimpleNamespace(active_path=active_path))
        monkeypatch.setattr("opensak.db.database.get_session", _fake_session)
        monkeypatch.setattr("opensak.importer.import_gpx",
                            lambda p, s, progress_cb=None: _result())
        monkeypatch.setattr("opensak.importer.import_zip",
                            lambda p, s, progress_cb=None: _result(created=5))
        from opensak.utils.utils import ImportType
        return ImportType

    def test_run_gpx_success(self, monkeypatch):
        ImportType = self._patch_common(monkeypatch)
        monkeypatch.setattr("opensak.utils.utils.get_import_type", lambda p: ImportType.GPX)
        w = ImportWorker([Path("/a.gpx")])
        got = []
        w.file_finished.connect(lambda i, r: got.append(r))
        w.run()
        assert len(got) == 1 and got[0].created == 1

    def test_run_zip_success(self, monkeypatch):
        ImportType = self._patch_common(monkeypatch)
        monkeypatch.setattr("opensak.utils.utils.get_import_type", lambda p: ImportType.ZIP)
        w = ImportWorker([Path("/a.zip")])
        got = []
        w.file_finished.connect(lambda i, r: got.append(r))
        w.run()
        assert got[0].created == 5

    def test_run_value_error(self, monkeypatch):
        ImportType = self._patch_common(monkeypatch)
        monkeypatch.setattr("opensak.utils.utils.get_import_type", lambda p: ImportType.GPX)
        monkeypatch.setattr("opensak.importer.import_gpx",
                            lambda *a, **k: (_ for _ in ()).throw(ValueError("bad gpx")))
        w = ImportWorker([Path("/a.gpx")])
        errs = []
        w.file_error.connect(lambda i, m: errs.append(m))
        w.run()
        assert errs and "bad gpx" in errs[0]

    def test_run_generic_exception_traceback(self, monkeypatch):
        ImportType = self._patch_common(monkeypatch)
        monkeypatch.setattr("opensak.utils.utils.get_import_type", lambda p: ImportType.GPX)
        monkeypatch.setattr("opensak.importer.import_gpx",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        w = ImportWorker([Path("/a.gpx")])
        errs = []
        w.file_error.connect(lambda i, m: errs.append(m))
        w.run()
        assert errs and "boom" in errs[0]

    def test_run_switches_and_restores_db(self, monkeypatch):
        ImportType = self._patch_common(monkeypatch, active_path=Path("/active.db"))
        monkeypatch.setattr("opensak.utils.utils.get_import_type", lambda p: ImportType.GPX)
        inits = []
        monkeypatch.setattr("opensak.db.database.init_db", lambda **k: inits.append(k.get("db_path")))
        w = ImportWorker([Path("/a.gpx")], target_db_path=Path("/other.db"))
        w.run()
        # switched to target, then restored original
        assert inits == [Path("/other.db"), Path("/active.db")]


# ── ImportDialog ────────────────────────────────────────────────────────────────

@pytest.fixture
def manager(monkeypatch):
    a = SimpleNamespace(name="Active", path=Path("/active.db"))
    b = SimpleNamespace(name="Other", path=Path("/other.db"))
    mgr = SimpleNamespace(active_path=Path("/active.db"), databases=[a, b])
    monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: mgr)
    return mgr


@pytest.fixture
def dlg(qtbot, manager):
    d = ImportDialog()
    qtbot.addWidget(d)
    return d


class TestImportDialog:
    def test_db_combo_populated_active_selected(self, dlg):
        assert dlg._db_combo.count() == 2
        assert "active" in dlg._db_combo.itemText(0).lower() or dlg._db_combo.currentIndex() == 0

    def test_add_files_dedupes(self, dlg):
        dlg.add_files([Path("/x/a.gpx"), Path("/y/a.gpx"), Path("/x/b.gpx")])
        assert len(dlg._selected_paths) == 2  # a.gpx deduped by name
        assert dlg._import_btn.isEnabled() is True

    def test_browse_adds_files(self, dlg, monkeypatch):
        monkeypatch.setattr(idlg.QFileDialog, "getOpenFileNames",
                            lambda *a, **k: (["/d/one.gpx", "/d/two.zip"], "f"))
        dlg._browse()
        assert len(dlg._selected_paths) == 2
        assert dlg._import_btn.isEnabled() is True

    def test_browse_cancel(self, dlg, monkeypatch):
        monkeypatch.setattr(idlg.QFileDialog, "getOpenFileNames", lambda *a, **k: ([], ""))
        dlg._browse()
        assert dlg._selected_paths == []

    def test_remove_selected(self, dlg):
        dlg.add_files([Path("/a.gpx"), Path("/b.gpx")])
        dlg._file_list.item(0).setSelected(True)
        dlg._remove_selected()
        assert [p.name for p in dlg._selected_paths] == ["b.gpx"]

    def test_selection_toggles_remove_btn(self, dlg):
        dlg.add_files([Path("/a.gpx")])
        dlg._file_list.item(0).setSelected(True)
        dlg._on_selection_changed()
        assert dlg._remove_btn.isEnabled() is True

    def test_start_import_launches_worker(self, dlg, monkeypatch):
        dlg.add_files([Path("/a.gpx")])
        started = []

        class FakeWorker:
            def __init__(self, paths, target_db_path=None):
                self.file_started = MagicMock()
                self.file_finished = MagicMock()
                self.file_error = MagicMock()
                self.progress = MagicMock()
                self.finished = MagicMock()
                self.deleteLater = MagicMock()
                self.isRunning = MagicMock(return_value=False)
                self.wait = MagicMock()
            def start(self):
                started.append(True)
        monkeypatch.setattr(idlg, "ImportWorker", FakeWorker)
        dlg._start_import()
        assert started == [True]
        assert dlg._import_btn.isEnabled() is False
        assert dlg._worker is not None

    def test_start_import_no_paths_noop(self, dlg):
        dlg._start_import()  # nothing selected -> early return
        assert dlg._progress.isVisible() is False

    def test_on_file_started_and_progress(self, dlg):
        dlg.add_files([Path("/a.gpx")])
        dlg._on_file_started(0, "a.gpx")
        assert "🔄" in dlg._file_list.item(0).text()
        dlg._on_progress(-1)            # saving
        dlg._on_progress(100)           # progress line
        dlg._on_progress(50)            # ignored (not multiple of 100)
        assert dlg._log.toPlainText() != ""

    def test_on_file_finished_marks_success(self, dlg):
        dlg.add_files([Path("/a.gpx")])
        dlg._on_file_finished(0, _result(created=2, errors=["e1", "e2"]))
        assert "✅" in dlg._file_list.item(0).text()
        assert dlg._any_success is True
        assert "e1" in dlg._log.toPlainText()

    def test_on_file_error_marks_failure(self, dlg):
        dlg.add_files([Path("/a.gpx")])
        dlg._on_file_error(0, "kaboom")
        assert "❌" in dlg._file_list.item(0).text()
        assert "kaboom" in dlg._log.toPlainText()

    def test_on_all_done_emits_completed(self, dlg, monkeypatch):
        from opensak.utils import flags
        monkeypatch.setattr(flags, "update_location", False, raising=False)
        dlg.add_files([Path("/a.gpx")])
        dlg._any_success = True
        fired = []
        dlg.import_completed.connect(lambda: fired.append(True))
        dlg._on_all_done()
        assert fired == [True]
        assert dlg._import_btn.isEnabled() is True

    def test_on_all_done_triggers_geocoding(self, dlg, monkeypatch):
        from opensak.utils import flags
        monkeypatch.setattr(flags, "update_location", True, raising=False)
        called = []
        monkeypatch.setattr(dlg, "_start_geocoding", lambda: called.append(True))
        dlg._any_success = True
        dlg._on_all_done()
        assert called == [True]

    def test_start_geocoding_no_rows_finishes(self, dlg, db_session, monkeypatch):
        # empty DB -> no rows -> _finish_geocoding
        finished = []
        monkeypatch.setattr(dlg, "_finish_geocoding", lambda: finished.append(True))
        dlg._start_geocoding()
        assert finished == [True]

    def test_start_geocoding_launches_worker(self, dlg, db_session, make_cache, monkeypatch):
        c = make_cache(gc_code="GCGEO", country=None, latitude=55.0, longitude=12.0)
        db_session.add(c)
        db_session.commit()
        started = []

        class FakeGeo:
            def __init__(self, rows):
                self.all_done = MagicMock()
                self.cancelled = MagicMock()
                self.finished = MagicMock()
                self.deleteLater = MagicMock()
                self.isRunning = MagicMock(return_value=False)
                self.wait = MagicMock()
                self.request_cancel = MagicMock()
            def start(self):
                started.append(True)
        monkeypatch.setattr(
            "opensak.gui.dialogs.update_location_dialog.ReverseGeocodeWorker", FakeGeo
        )
        dlg._start_geocoding()
        assert started == [True]

    def test_on_geocode_done(self, dlg):
        fired = []
        dlg.import_completed.connect(lambda: fired.append(True))
        dlg._on_geocode_done(SimpleNamespace(updated=3, skipped=1, errors=0))
        assert fired == [True]
        assert dlg._import_btn.isEnabled() is True

    def test_close_event_waits_for_workers(self, dlg):
        dlg._worker = SimpleNamespace(isRunning=lambda: True, wait=MagicMock())
        dlg._geo_worker = SimpleNamespace(
            isRunning=lambda: True, request_cancel=MagicMock(), wait=MagicMock()
        )
        dlg.close()
        assert dlg._worker is None
        assert dlg._geo_worker is None

    def test_log_helpers(self, dlg):
        dlg._append_log("first")
        dlg._append_log("second")
        assert "first" in dlg._log.toPlainText()
        assert "second" in dlg._log.toPlainText()
        dlg._replace_last_log_line("replaced")
        assert "replaced" in dlg._log.toPlainText()

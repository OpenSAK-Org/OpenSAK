# tests/unit-tests/test_import_dialog.py — GPX/PQ import worker + dialog.

import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock

pytest.importorskip("pytestqt")

from opensak.gui.dialogs import import_dialog as idlg
from opensak.gui.dialogs.import_dialog import ImportWorker, ImportDialog
from opensak.importer.gpx_import_settings import GpxImportProfile, GpxImportSettings
from opensak.lang import tr


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
                            lambda p, s, wpts_path=None, progress_cb=None: _result())
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

    def test_run_other_db_uses_private_session(self, monkeypatch):
        # Another target DB gets its own session; the active (global) engine
        # is never swapped from the worker thread.
        ImportType = self._patch_common(monkeypatch, active_path=Path("/active.db"))
        monkeypatch.setattr("opensak.utils.utils.get_import_type", lambda p: ImportType.GPX)
        inits, opened = [], []
        monkeypatch.setattr("opensak.db.database.init_db", lambda **k: inits.append(k.get("db_path")))
        monkeypatch.setattr("opensak.db.database.get_session",
                            lambda: pytest.fail("active DB session used for another DB"))
        monkeypatch.setattr("opensak.db.database.session_for",
                            lambda p: opened.append(p) or _fake_session())
        w = ImportWorker([Path("/a.gpx"), Path("/b.gpx")], target_db_path=Path("/other.db"))
        w.run()
        assert opened == [Path("/other.db"), Path("/other.db")]
        assert inits == []

    def test_run_gpx_passes_companion_wpts_path(self, monkeypatch, tmp_path):
        # Companion detected by content inspection, not filename.
        from tests.data import SAMPLE_GPX, SAMPLE_WPTS_GPX

        gpx = tmp_path / "pq.gpx"
        gpx.write_text(SAMPLE_GPX)
        wpts = tmp_path / "anyname.gpx"       # name is irrelevant — content decides
        wpts.write_text(SAMPLE_WPTS_GPX)

        ImportType = self._patch_common(monkeypatch)
        monkeypatch.setattr("opensak.utils.utils.get_import_type", lambda p: ImportType.GPX)
        monkeypatch.setattr("opensak.importer._count_wpts", lambda p: 0)

        received = []
        monkeypatch.setattr(
            "opensak.importer.import_gpx",
            lambda p, s, wpts_path=None, progress_cb=None: (
                received.append(wpts_path) or _result()
            ),
        )

        w = ImportWorker([gpx])
        w.run()

        assert len(received) == 1
        assert received[0] == wpts

    def test_run_gpx_no_companion_wpts_path_is_none(self, monkeypatch, tmp_path):
        # No other GPX in directory → wpts_path must be None.
        from tests.data import SAMPLE_GPX

        gpx = tmp_path / "solo.gpx"
        gpx.write_text(SAMPLE_GPX)

        ImportType = self._patch_common(monkeypatch)
        monkeypatch.setattr("opensak.utils.utils.get_import_type", lambda p: ImportType.GPX)
        monkeypatch.setattr("opensak.importer._count_wpts", lambda p: 0)

        received = []
        monkeypatch.setattr(
            "opensak.importer.import_gpx",
            lambda p, s, wpts_path=None, progress_cb=None: (
                received.append(wpts_path) or _result()
            ),
        )

        w = ImportWorker([gpx])
        w.run()

        assert len(received) == 1
        assert received[0] is None


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

    def test_add_files_drops_companion_wpts_when_parent_present(self, dlg, monkeypatch):
        # Dropping both files: companion detected by content, not name.
        monkeypatch.setattr(
            "opensak.importer._is_companion_gpx",
            lambda p: p.name == "pq-wpts.gpx",
        )
        dlg.add_files([Path("/d/pq.gpx"), Path("/d/pq-wpts.gpx")])
        assert [p.name for p in dlg._selected_paths] == ["pq.gpx"]

    def test_add_files_keeps_companion_wpts_when_parent_absent(self, dlg, monkeypatch):
        # Dropping just a companion file (no parent in list) must keep it.
        monkeypatch.setattr("opensak.importer._is_companion_gpx", lambda p: True)
        dlg.add_files([Path("/d/pq-wpts.gpx")])
        assert [p.name for p in dlg._selected_paths] == ["pq-wpts.gpx"]

    def test_browse_adds_files(self, dlg, monkeypatch):
        monkeypatch.setattr(idlg.QFileDialog, "getOpenFileNames",
                            lambda *a, **k: (["/d/one.gpx", "/d/two.zip"], "f"))
        dlg._browse()
        assert len(dlg._selected_paths) == 2
        assert dlg._import_btn.isEnabled() is True

    def test_browse_drops_companion_wpts_when_parent_selected(self, dlg, monkeypatch):
        monkeypatch.setattr(
            idlg.QFileDialog, "getOpenFileNames",
            lambda *a, **k: (["/d/pq.gpx", "/d/pq-wpts.gpx"], "f"),
        )
        monkeypatch.setattr(
            "opensak.importer._is_companion_gpx",
            lambda p: p.name == "pq-wpts.gpx",
        )
        dlg._browse()
        assert [p.name for p in dlg._selected_paths] == ["pq.gpx"]

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
                self.total = MagicMock()
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
        monkeypatch.setattr(flags, "reverse_geocoding", False, raising=False)
        dlg.add_files([Path("/a.gpx")])
        dlg._any_success = True
        fired = []
        dlg.import_completed.connect(lambda: fired.append(True))
        dlg._on_all_done()
        assert fired == [True]
        assert dlg._import_btn.isEnabled() is True

    def test_on_all_done_triggers_geocoding(self, dlg, monkeypatch):
        from opensak.utils import flags
        monkeypatch.setattr(flags, "reverse_geocoding", True, raising=False)
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
            def __init__(self, rows, parent=None, db_path=None):
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

    def test_geocoding_works_on_the_database_imported_into(self, dlg, monkeypatch):
        # #937: the import target, not the active database.
        import opensak.geo.reverse_geocode as geo
        target = Path("/other.db")
        seen = []
        monkeypatch.setattr(geo, "rows_missing_location",
                            lambda db_path: seen.append(db_path) or ["row"])

        class FakeGeo:
            def __init__(self, rows, parent=None, db_path=None):
                seen.append(db_path)
                self.all_done = self.cancelled = self.finished = MagicMock()
                self.deleteLater = MagicMock()
                self.isRunning = MagicMock(return_value=False)
            def start(self):
                pass
        monkeypatch.setattr(
            "opensak.gui.dialogs.update_location_dialog.ReverseGeocodeWorker", FakeGeo
        )
        dlg._target_db_path = target
        dlg._start_geocoding()
        assert seen == [target, target]

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

    # ── Issue #802: Close button responsiveness ─────────────────────────────

    def test_start_import_disables_close_button(self, dlg, monkeypatch):
        dlg.add_files([Path("/a.gpx")])

        class FakeWorker:
            def __init__(self, paths, target_db_path=None):
                self.file_started = MagicMock()
                self.file_finished = MagicMock()
                self.file_error = MagicMock()
                self.progress = MagicMock()
                self.total = MagicMock()
                self.finished = MagicMock()
                self.deleteLater = MagicMock()
                self.isRunning = MagicMock(return_value=False)
                self.wait = MagicMock()
            def start(self):
                pass
        monkeypatch.setattr(idlg, "ImportWorker", FakeWorker)
        dlg._start_import()
        assert dlg._close_btn.isEnabled() is False

    def test_on_all_done_reenables_close_when_no_geocoding(self, dlg, monkeypatch):
        from opensak.utils import flags
        monkeypatch.setattr(flags, "reverse_geocoding", False, raising=False)
        dlg.add_files([Path("/a.gpx")])
        dlg._close_btn.setEnabled(False)  # simulate mid-import state
        dlg._any_success = True
        dlg._on_all_done()
        assert dlg._close_btn.isEnabled() is True
        assert dlg._close_btn.text() == tr("close")

    def test_start_geocoding_relabels_close_to_cancel(self, dlg, db_session, make_cache, monkeypatch):
        c = make_cache(gc_code="GCGEO2", country=None, latitude=55.0, longitude=12.0)
        db_session.add(c)
        db_session.commit()
        dlg._close_btn.setEnabled(False)

        class FakeGeo:
            def __init__(self, rows, parent=None, db_path=None):
                self.all_done = MagicMock()
                self.cancelled = MagicMock()
                self.finished = MagicMock()
                self.deleteLater = MagicMock()
                self.isRunning = MagicMock(return_value=True)
                self.wait = MagicMock()
                self.request_cancel = MagicMock()
            def start(self):
                pass
        monkeypatch.setattr(
            "opensak.gui.dialogs.update_location_dialog.ReverseGeocodeWorker", FakeGeo
        )
        dlg._start_geocoding()
        # Responsive, clickable "Cancel" — not disabled/frozen.
        assert dlg._close_btn.isEnabled() is True
        assert dlg._close_btn.text() == tr("cancel")

    def test_finish_geocoding_reverts_close_button(self, dlg):
        dlg._close_btn.setText(tr("cancel"))
        dlg._close_btn.setEnabled(False)
        dlg._finish_geocoding()
        assert dlg._close_btn.text() == tr("close")
        assert dlg._close_btn.isEnabled() is True

    def test_close_clicked_while_geocoding_cancels_without_blocking(self, dlg):
        # The core of #802: clicking Close/Cancel while the geocode step is
        # running must call the non-blocking request_cancel(), never wait().
        geo = SimpleNamespace(
            isRunning=lambda: True, request_cancel=MagicMock(), wait=MagicMock(),
        )
        dlg._geo_worker = geo
        dlg._worker = None
        dlg._on_close_clicked()
        geo.request_cancel.assert_called_once()
        geo.wait.assert_not_called()
        assert dlg._close_btn.isEnabled() is False
        assert dlg.result() == 0  # dialog not closed/accepted
        # Teardown closes the dialog automatically — make isRunning() report
        # False afterwards so closeEvent()'s cleanup path doesn't choke on
        # this minimal fake (which has no other QThread methods).
        geo.isRunning = lambda: False

    def test_close_clicked_while_importing_does_not_close(self, dlg):
        # Defensive guard: the import step has no cancel support, so a
        # click while it's (somehow) still enabled must not close the
        # dialog or block — it's a no-op.
        worker = SimpleNamespace(isRunning=lambda: True)
        dlg._worker = worker
        dlg._geo_worker = None
        dlg._on_close_clicked()
        assert dlg.result() == 0  # dialog not closed/accepted
        # See comment in the geocoding test above re: teardown.
        worker.isRunning = lambda: False

    def test_close_clicked_when_idle_accepts(self, dlg, monkeypatch):
        dlg._worker = None
        dlg._geo_worker = None
        accepted = []
        monkeypatch.setattr(dlg, "accept", lambda: accepted.append(True))
        dlg._on_close_clicked()
        assert accepted == [True]

    # ── Issue #802 follow-up: stale worker reference after completion ───────
    # Reported by Allan: Close crashed with "RuntimeError: libshiboken:
    # Internal C++ object (ReverseGeocodeWorker) already deleted." after a
    # completed import/geocode, because self._geo_worker (and self._worker)
    # were only ever nulled in closeEvent() — never on normal completion —
    # so they kept pointing at an object deleteLater() had since destroyed.

    def test_on_worker_finished_clears_reference(self, dlg):
        dlg._worker = SimpleNamespace(isRunning=lambda: False)
        dlg._on_worker_finished()
        assert dlg._worker is None

    def test_on_geo_worker_finished_clears_reference(self, dlg):
        dlg._geo_worker = SimpleNamespace(isRunning=lambda: False)
        dlg._on_geo_worker_finished()
        assert dlg._geo_worker is None

    def test_close_clicked_after_normal_completion_does_not_crash(self, dlg, monkeypatch):
        # End-to-end version of the two tests above: simulate the real
        # finished-signal wiring (_on_geo_worker_finished nulls the
        # reference) then click Close — must accept cleanly, not raise.
        dlg._geo_worker = SimpleNamespace(isRunning=lambda: False)
        dlg._on_geo_worker_finished()  # what QThread.finished now triggers
        dlg._worker = None
        accepted = []
        monkeypatch.setattr(dlg, "accept", lambda: accepted.append(True))
        dlg._on_close_clicked()
        assert accepted == [True]

    def test_close_clicked_handles_deleted_geo_worker_defensively(self, dlg, monkeypatch):
        # Belt-and-suspenders layer: even if a reference somehow survives
        # to a deleted C++ object, isRunning() raising RuntimeError must
        # not propagate — treat it as "not running" and recover.
        class DeletedCppObject:
            def isRunning(self):
                raise RuntimeError(
                    "libshiboken: Internal C++ object (ReverseGeocodeWorker) already deleted."
                )
        dlg._geo_worker = DeletedCppObject()
        dlg._worker = None
        accepted = []
        monkeypatch.setattr(dlg, "accept", lambda: accepted.append(True))
        dlg._on_close_clicked()
        assert accepted == [True]
        assert dlg._geo_worker is None

    def test_close_clicked_handles_deleted_worker_defensively(self, dlg, monkeypatch):
        class DeletedCppObject:
            def isRunning(self):
                raise RuntimeError("libshiboken: Internal C++ object already deleted.")
        dlg._geo_worker = None
        dlg._worker = DeletedCppObject()
        accepted = []
        monkeypatch.setattr(dlg, "accept", lambda: accepted.append(True))
        dlg._on_close_clicked()
        assert accepted == [True]
        assert dlg._worker is None

    def test_log_helpers(self, dlg):
        dlg._append_log("first")
        dlg._append_log("second")
        assert "first" in dlg._log.toPlainText()
        assert "second" in dlg._log.toPlainText()
        dlg._replace_last_log_line("replaced")
        assert "replaced" in dlg._log.toPlainText()

    # ── Progress bar determinism (#372) ─────────────────────────────────────────

    def test_on_total_positive_makes_bar_determinate(self, dlg):
        dlg._on_total(50)
        assert dlg._progress.maximum() == 50
        assert dlg._progress.value() == 0

    def test_on_total_negative_keeps_bar_indeterminate(self, dlg):
        dlg._on_total(-1)
        assert dlg._progress.maximum() == 0

    def test_on_progress_drives_bar_when_determinate(self, dlg):
        dlg._on_total(100)
        dlg._on_progress(42)
        assert dlg._progress.value() == 42

    def test_on_progress_no_setValue_when_indeterminate(self, dlg):
        # indeterminate bar: maximum is 0, setValue must not change that
        assert dlg._progress.maximum() == 0
        dlg._on_progress(42)
        assert dlg._progress.maximum() == 0

    def test_on_file_started_resets_bar_to_indeterminate(self, dlg):
        dlg.add_files([Path("/a.gpx")])
        dlg._on_total(200)  # bar goes determinate
        assert dlg._progress.maximum() == 200
        dlg._on_file_started(0, "a.gpx")  # must reset to indeterminate
        assert dlg._progress.maximum() == 0


class TestImportWorkerTotal:
    def _patch(self, monkeypatch, import_type_val, active_path=Path("/active.db")):
        monkeypatch.setattr("opensak.db.manager.get_db_manager",
                            lambda: SimpleNamespace(active_path=active_path))
        monkeypatch.setattr("opensak.db.database.get_session", _fake_session)
        monkeypatch.setattr("opensak.importer.import_gpx",
                            lambda p, s, wpts_path=None, progress_cb=None: _result())
        monkeypatch.setattr("opensak.importer.import_zip",
                            lambda p, s, progress_cb=None: _result(created=5))
        from opensak.utils.utils import ImportType
        monkeypatch.setattr("opensak.utils.utils.get_import_type",
                            lambda p: import_type_val)
        return ImportType

    def test_emits_total_count_for_gpx(self, monkeypatch):
        from opensak.utils.utils import ImportType
        self._patch(monkeypatch, ImportType.GPX)
        monkeypatch.setattr("opensak.importer._count_wpts", lambda p: 42)
        w = ImportWorker([Path("/a.gpx")])
        totals = []
        w.total.connect(lambda t: totals.append(t))
        w.run()
        assert totals == [42]

    def test_emits_minus1_for_zip(self, monkeypatch):
        from opensak.utils.utils import ImportType
        self._patch(monkeypatch, ImportType.ZIP)
        w = ImportWorker([Path("/a.zip")])
        totals = []
        w.total.connect(lambda t: totals.append(t))
        w.run()
        assert totals == [-1]

    def test_emits_minus1_when_count_raises(self, monkeypatch):
        from opensak.utils.utils import ImportType
        self._patch(monkeypatch, ImportType.GPX)
        monkeypatch.setattr("opensak.importer._count_wpts",
                            lambda p: (_ for _ in ()).throw(RuntimeError("oops")))
        w = ImportWorker([Path("/a.gpx")])
        totals = []
        w.total.connect(lambda t: totals.append(t))
        w.run()
        assert totals == [-1]


# ── Saved settings ──────────────────────────────────────────────────────────────

class TestImportDialogSettings:
    def test_settings_survive_the_dialog(self, dlg, tmp_path):
        files = [tmp_path / "a.gpx", tmp_path / "b.zip"]
        for f in files:
            f.write_text("x")
        settings = GpxImportSettings(database=str(Path("/other.db")),
                                     files=[str(f) for f in files])
        dlg._apply_settings(settings)
        assert dlg._db_combo.currentData() == Path("/other.db")
        assert dlg._import_btn.isEnabled() is True
        assert dlg._collect_settings() == settings

    def test_apply_replaces_file_list(self, dlg, tmp_path):
        new = tmp_path / "new.gpx"
        new.write_text("x")
        dlg.add_files([Path("/old.gpx")])
        dlg._apply_settings(GpxImportSettings(files=[str(new)]))
        assert dlg._selected_paths == [new]
        assert dlg._file_list.count() == 1

    def test_apply_leaves_out_missing_files(self, dlg, tmp_path, monkeypatch):
        monkeypatch.setattr(idlg, "tr", lambda key, **kw: f"{key}:{kw.get('count', '')}")
        there = tmp_path / "there.gpx"
        there.write_text("x")
        dlg._apply_settings(GpxImportSettings(
            files=[str(there), str(tmp_path / "gone.gpx")]))
        assert dlg._selected_paths == [there]
        assert "import_settings_missing_files:1" in dlg._log.toPlainText()

    def test_apply_unknown_database_keeps_selection(self, dlg):
        dlg._apply_settings(GpxImportSettings(database="/nowhere.db"))
        assert dlg._db_combo.currentData() == Path("/active.db")

    def test_opens_with_last_used_settings(self, qtbot, manager, tmp_path):
        f = tmp_path / "pq.zip"
        f.write_text("x")
        GpxImportProfile.save_last_used(
            GpxImportSettings(database=str(Path("/other.db")), files=[str(f)]))
        d = ImportDialog()
        qtbot.addWidget(d)
        assert d._db_combo.currentData() == Path("/other.db")
        assert d._selected_paths == [f]

    def test_dropped_paths_replace_last_used_files(self, qtbot, manager, tmp_path):
        old, dropped = tmp_path / "old.gpx", tmp_path / "dropped.gpx"
        for f in (old, dropped):
            f.write_text("x")
        GpxImportProfile.save_last_used(
            GpxImportSettings(database=str(Path("/other.db")), files=[str(old)]))
        d = ImportDialog(paths=[dropped])
        qtbot.addWidget(d)
        assert d._selected_paths == [dropped]
        assert d._db_combo.currentData() == Path("/other.db")

    def test_start_import_saves_last_used(self, dlg, monkeypatch, tmp_path):
        f = tmp_path / "a.gpx"
        f.write_text("x")
        dlg.add_files([f])
        monkeypatch.setattr(idlg, "ImportWorker", MagicMock())
        dlg._start_import()
        last = GpxImportProfile.load_last_used()
        assert last.files == [str(f)]
        assert last.database == str(Path("/active.db"))
        assert dlg._settings_group.isEnabled() is False

    def test_settings_group_reenabled_after_import(self, dlg, monkeypatch):
        monkeypatch.setattr("opensak.utils.flags.reverse_geocoding", False)
        dlg._settings_group.setEnabled(False)
        dlg._on_all_done()
        assert dlg._settings_group.isEnabled() is True

    def test_saved_profile_listed_in_combo(self, qtbot, manager):
        GpxImportProfile("Nightly PQs", GpxImportSettings()).save()
        d = ImportDialog()
        qtbot.addWidget(d)
        assert d._settings_combo.findText("Nightly PQs") >= 0


# ── Folder entries ──────────────────────────────────────────────────────────────

def _make_files(folder, *names):
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        (folder / n).write_text("x")
    return [folder / n for n in names]


class TestFolderImportFiles:
    def test_gpx_and_zip_only_sorted_not_recursive(self, tmp_path, monkeypatch):
        monkeypatch.setattr("opensak.importer._is_companion_gpx", lambda p: False)
        _make_files(tmp_path, "b.zip", "a.gpx", "notes.txt", "c.GPX")
        _make_files(tmp_path / "sub", "deep.gpx")
        assert [f.name for f in idlg.folder_import_files(tmp_path)] == ["a.gpx", "b.zip", "c.GPX"]

    def test_drops_companion_when_main_gpx_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr("opensak.importer._is_companion_gpx",
                            lambda p: p.name.endswith("-wpts.gpx"))
        _make_files(tmp_path, "pq.gpx", "pq-wpts.gpx")
        assert [f.name for f in idlg.folder_import_files(tmp_path)] == ["pq.gpx"]

    def test_keeps_companion_alone(self, tmp_path, monkeypatch):
        monkeypatch.setattr("opensak.importer._is_companion_gpx", lambda p: True)
        _make_files(tmp_path, "pq-wpts.gpx")
        assert [f.name for f in idlg.folder_import_files(tmp_path)] == ["pq-wpts.gpx"]

    def test_missing_folder_is_empty(self, tmp_path):
        assert idlg.folder_import_files(tmp_path / "gone") == []


class TestImportDialogFolders:
    @pytest.fixture(autouse=True)
    def _no_companions(self, monkeypatch):
        monkeypatch.setattr("opensak.importer._is_companion_gpx", lambda p: False)

    def test_add_folder_entry(self, dlg, tmp_path):
        files = _make_files(tmp_path / "pq", "a.gpx", "b.zip")
        dlg.add_folders([tmp_path / "pq", tmp_path / "pq"])
        assert dlg._selected_paths == [tmp_path / "pq"]   # deduped
        assert "📁 pq" in dlg._file_list.item(0).text()
        assert dlg._run_paths == files and dlg._run_rows == [0, 0]
        assert dlg._import_btn.isEnabled() is True

    def test_browse_folder(self, dlg, tmp_path, monkeypatch):
        monkeypatch.setattr(idlg.QFileDialog, "getExistingDirectory",
                            lambda *a, **k: str(tmp_path))
        dlg._browse_folder()
        assert dlg._selected_paths == [tmp_path]
        assert tmp_path in dlg._folders

    def test_browse_folder_cancel(self, dlg, monkeypatch):
        monkeypatch.setattr(idlg.QFileDialog, "getExistingDirectory", lambda *a, **k: "")
        dlg._browse_folder()
        assert dlg._selected_paths == []

    def test_file_in_listed_folder_imported_once(self, dlg, tmp_path):
        (a,) = _make_files(tmp_path, "a.gpx")
        dlg.add_files([a])
        dlg.add_folders([tmp_path])
        assert dlg._run_paths == [a] and dlg._run_rows == [0]

    def test_start_import_scans_folder_again(self, dlg, tmp_path, monkeypatch):
        dlg.add_folders([tmp_path])
        (late,) = _make_files(tmp_path, "late.zip")
        worker = MagicMock()
        monkeypatch.setattr(idlg, "ImportWorker", worker)
        dlg._start_import()
        assert worker.call_args.args[0] == [late]

    def test_start_import_empty_folder_logs_and_stops(self, dlg, tmp_path, monkeypatch):
        worker = MagicMock()
        monkeypatch.setattr(idlg, "ImportWorker", worker)
        dlg.add_folders([tmp_path])
        dlg._start_import()
        worker.assert_not_called()
        assert tr("import_folder_empty") in dlg._log.toPlainText()
        assert dlg._browse_btn.isEnabled() is True

    def test_folder_row_status_follows_its_files(self, dlg, tmp_path):
        _make_files(tmp_path, "a.gpx", "b.gpx", "c.gpx")
        dlg.add_folders([tmp_path])
        dlg._on_file_started(0, "a.gpx")
        assert dlg._file_list.item(0).text().startswith("🔄")
        dlg._on_file_error(1, "bad")
        dlg._on_file_finished(2, _result())
        # One failed file keeps the folder marked failed.
        assert dlg._file_list.item(0).text().startswith("❌")

    def test_remove_folder_entry(self, dlg, tmp_path):
        _make_files(tmp_path, "a.gpx")
        dlg.add_folders([tmp_path])
        dlg._file_list.item(0).setSelected(True)
        dlg._remove_selected()
        assert dlg._selected_paths == [] and dlg._folders == set()
        assert dlg._run_paths == []
        assert dlg._import_btn.isEnabled() is False

    def test_settings_keep_folders(self, dlg, tmp_path):
        (f,) = _make_files(tmp_path / "files", "one.gpx")
        folder = tmp_path / "pq"
        folder.mkdir()
        settings = GpxImportSettings(database=str(Path("/active.db")),
                                     files=[str(f)], folders=[str(folder)])
        dlg._apply_settings(settings)
        assert folder in dlg._folders
        assert dlg._collect_settings() == settings

    def test_apply_leaves_out_missing_folder(self, dlg, tmp_path, monkeypatch):
        monkeypatch.setattr(idlg, "tr", lambda key, **kw: f"{key}:{kw.get('count', '')}")
        dlg._apply_settings(GpxImportSettings(folders=[str(tmp_path / "gone")]))
        assert dlg._selected_paths == []
        assert "import_settings_missing_files:1" in dlg._log.toPlainText()

    def test_dropped_folder_becomes_folder_entry(self, qtbot, manager, tmp_path):
        d = ImportDialog(paths=[tmp_path])
        qtbot.addWidget(d)
        assert d._selected_paths == [tmp_path] and tmp_path in d._folders

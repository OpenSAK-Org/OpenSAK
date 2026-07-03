# tests/unit-tests/test_update_location_dialog.py — reverse-geocode location updater.

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("pytestqt")

import opensak.utils.flags as flags
from opensak.db.database import get_session, init_db
from opensak.db.models import Cache
from opensak.gui.dialogs import update_location_dialog as uld
from opensak.gui.dialogs.update_location_dialog import (
    ReverseGeocodeWorker,
    UpdateLocationDialog,
    UpdateLocationResult,
    _CacheRow,
)


def _loc(country="DK", state="ZL", county="CPH"):
    return SimpleNamespace(country=country, state=state, county=county)


def _fake_store(available=True, version="42"):
    store = MagicMock()
    store.available.return_value = available
    store.dataset_version.return_value = version
    return store


def _fake_resolver(loc=None):
    resolver = MagicMock()
    resolver.resolve.return_value = loc or _loc()
    return resolver


@pytest.fixture
def db(tmp_path):
    init_db(db_path=tmp_path / "u.db")
    with get_session() as s:
        s.add(Cache(gc_code="GC1", name="A", cache_type="Traditional Cache",
                    latitude=55.0, longitude=12.0))
        s.add(Cache(gc_code="GC2", name="B", cache_type="Traditional Cache",
                    latitude=56.0, longitude=10.0, country="X", state="Y", county="Z"))
    return tmp_path


@pytest.fixture
def geo_mocks(monkeypatch):
    store = _fake_store()
    resolver = _fake_resolver()
    monkeypatch.setattr("opensak.geo.store.BoundaryStore", lambda: store)
    monkeypatch.setattr("opensak.geo.boundaries.TerritoryResolver", lambda s: resolver)
    monkeypatch.setattr("opensak.geo.store.ensure_baseline_seeded", lambda: None)
    return store, resolver


# ── ReverseGeocodeWorker ──────────────────────────────────────────────────────

class TestReverseGeocodeWorker:
    def test_run_updates_db(self, db, geo_mocks):
        w = ReverseGeocodeWorker([_CacheRow("GC1", 55.0, 12.0)])
        done = []
        w.all_done.connect(done.append)
        w.run()
        assert done[0].updated == 1
        with get_session() as s:
            c = s.query(Cache).filter_by(gc_code="GC1").first()
            assert c.country == "DK"
            assert c.state == "ZL"
            assert c.county == "CPH"

    def test_run_writes_provenance(self, db, geo_mocks):
        w = ReverseGeocodeWorker([_CacheRow("GC1", 55.0, 12.0, basis="corrected")])
        done = []
        w.all_done.connect(done.append)
        w.run()
        with get_session() as s:
            c = s.query(Cache).filter_by(gc_code="GC1").first()
            assert c.location_source == "boundary"
            assert c.location_basis == "corrected"
            assert c.location_updated is not None
            assert c.location_dataset == "42"

    def test_run_cancelled_before_start(self, db):
        w = ReverseGeocodeWorker([_CacheRow("GC1", 55.0, 12.0)])
        w.request_cancel()
        cancelled = []
        w.cancelled.connect(cancelled.append)
        w.run()
        assert cancelled

    def test_run_seeds_baseline_on_worker_thread(self, db, geo_mocks, monkeypatch):
        # ensure_baseline_seeded runs here rather than at app startup, so any
        # first-run network fallback happens on this QThread, never the main
        # thread. See update_location_dialog.py's ReverseGeocodeWorker.run().
        calls = []
        monkeypatch.setattr("opensak.geo.store.ensure_baseline_seeded", lambda: calls.append(None))
        w = ReverseGeocodeWorker([_CacheRow("GC1", 55.0, 12.0)])
        w.run()
        assert calls == [None]

    def test_run_cancelled_before_start_skips_baseline_seed(self, db, monkeypatch):
        calls = []
        monkeypatch.setattr("opensak.geo.store.ensure_baseline_seeded", lambda: calls.append(None))
        w = ReverseGeocodeWorker([_CacheRow("GC1", 55.0, 12.0)])
        w.request_cancel()
        w.run()
        assert calls == []

    def test_run_no_boundaries_emits_error(self, db, monkeypatch):
        store = _fake_store(available=False)
        monkeypatch.setattr("opensak.geo.store.BoundaryStore", lambda: store)
        monkeypatch.setattr("opensak.geo.store.ensure_baseline_seeded", lambda: None)
        w = ReverseGeocodeWorker([_CacheRow("GC1", 55.0, 12.0)])
        done = []
        w.all_done.connect(done.append)
        w.run()
        assert done[0].errors == 1
        assert done[0].updated == 0

    def test_run_db_error_records_error(self, db, geo_mocks, monkeypatch):
        class _Boom:
            def __enter__(self): raise RuntimeError("db boom")
            def __exit__(self, *a): pass
        monkeypatch.setattr("opensak.db.database.get_session", lambda: _Boom())
        w = ReverseGeocodeWorker([_CacheRow("GC1", 55.0, 12.0)])
        done = []
        w.all_done.connect(done.append)
        w.run()
        assert done[0].errors == 1

    def test_run_updates_multiple_caches(self, db, geo_mocks):
        # Exercises parallel resolve + bulk write path with more than one row.
        rows = [_CacheRow("GC1", 55.0, 12.0), _CacheRow("GC2", 56.0, 10.0)]
        w = ReverseGeocodeWorker(rows)
        done = []
        w.all_done.connect(done.append)
        w.run()
        assert done[0].updated == 2
        with get_session() as s:
            for gc_code in ("GC1", "GC2"):
                c = s.query(Cache).filter_by(gc_code=gc_code).first()
                assert c.country == "DK"
                assert c.state == "ZL"

    def test_default_basis_is_posted(self):
        row = _CacheRow("GC1", 55.0, 12.0)
        assert row.basis == "posted"

    def test_explicit_basis_corrected(self):
        row = _CacheRow("GC1", 55.0, 12.0, basis="corrected")
        assert row.basis == "corrected"


# ── UpdateLocationDialog ──────────────────────────────────────────────────────

class TestUpdateLocationDialog:
    def test_menu_mode_defaults(self, qtbot):
        dlg = UpdateLocationDialog()
        qtbot.addWidget(dlg)
        assert dlg._rb_this.isEnabled() is False
        assert dlg._rb_missing.isChecked() is True

    def test_context_menu_mode_defaults(self, qtbot):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        assert dlg._rb_this.isChecked() is True

    def test_corrected_checkbox_defaults_unchecked(self, qtbot):
        dlg = UpdateLocationDialog()
        qtbot.addWidget(dlg)
        assert dlg._cb_corrected.isChecked() is False

    def test_build_rows_missing_scope(self, qtbot, db):
        dlg = UpdateLocationDialog()
        qtbot.addWidget(dlg)
        dlg._rb_missing.setChecked(True)
        codes = {r.gc_code for r in dlg._build_rows()}
        assert "GC1" in codes and "GC2" not in codes

    def test_build_rows_all_scope(self, qtbot, db):
        dlg = UpdateLocationDialog()
        qtbot.addWidget(dlg)
        dlg._rb_all.setChecked(True)
        assert {r.gc_code for r in dlg._build_rows()} == {"GC1", "GC2"}

    def test_build_rows_this_scope(self, qtbot, db):
        dlg = UpdateLocationDialog(gc_codes=["GC2"])
        qtbot.addWidget(dlg)
        dlg._rb_this.setChecked(True)
        assert {r.gc_code for r in dlg._build_rows()} == {"GC2"}

    def test_build_rows_uses_corrected_coords(self, qtbot, tmp_path):
        from opensak.db.models import UserNote
        init_db(db_path=tmp_path / "corr.db")
        with get_session() as s:
            c = Cache(gc_code="GCX", name="X", cache_type="Traditional Cache",
                      latitude=10.0, longitude=10.0)
            c.user_note = UserNote(is_corrected=True, corrected_lat=20.0, corrected_lon=21.0)
            s.add(c)
        dlg = UpdateLocationDialog(gc_codes=["GCX"])
        qtbot.addWidget(dlg)
        dlg._rb_this.setChecked(True)
        dlg._cb_corrected.setChecked(True)
        rows = dlg._build_rows()
        assert (rows[0].lat, rows[0].lon) == (20.0, 21.0)
        assert rows[0].basis == "corrected"

    def test_build_rows_posted_basis_when_not_corrected(self, qtbot, db):
        dlg = UpdateLocationDialog()
        qtbot.addWidget(dlg)
        dlg._rb_all.setChecked(True)
        dlg._cb_corrected.setChecked(False)
        for row in dlg._build_rows():
            assert row.basis == "posted"

    def test_start_nothing_to_do(self, qtbot, db, monkeypatch):
        dlg = UpdateLocationDialog()
        qtbot.addWidget(dlg)
        monkeypatch.setattr(dlg, "_build_rows", lambda: [])
        dlg._start()
        assert dlg._progress_label.text() != ""

    def test_start_launches_worker(self, qtbot, db, monkeypatch):
        dlg = UpdateLocationDialog()
        qtbot.addWidget(dlg)
        monkeypatch.setattr(dlg, "_build_rows", lambda: [_CacheRow("GC1", 55.0, 12.0)])
        started = []

        class FakeWorker:
            def __init__(self, rows):
                self.row_done = MagicMock()
                self.all_done = MagicMock()
                self.cancelled = MagicMock()
                self.finished = MagicMock()

            def start(self):
                started.append(True)

            def isRunning(self):
                return False

            def deleteLater(self):
                pass

        monkeypatch.setattr(uld, "ReverseGeocodeWorker", FakeWorker)
        dlg._start()
        assert started == [True]
        assert dlg._start_btn.isEnabled() is False

    def test_done_signal_and_finalize(self, qtbot, db):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        fired = []
        dlg.location_updated.connect(lambda: fired.append(True))
        dlg._on_done(UpdateLocationResult(updated=2))
        assert fired == [True]
        assert dlg._start_btn.isEnabled() is True

    def test_cancelled_emits_when_updated(self, qtbot, db):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        fired = []
        dlg.location_updated.connect(lambda: fired.append(True))
        dlg._on_cancelled(UpdateLocationResult(updated=1))
        assert fired == [True]

    def test_cancelled_no_emit_when_zero_updated(self, qtbot, db):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        fired = []
        dlg.location_updated.connect(lambda: fired.append(True))
        dlg._on_cancelled(UpdateLocationResult(updated=0))
        assert fired == []

    def test_row_done_appends_to_log(self, qtbot, db):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        dlg._on_row_done("GC1", "a log line")
        assert "a log line" in dlg._log.toPlainText()

    def test_request_cancel(self, qtbot, db):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        dlg._worker = MagicMock()
        dlg._request_cancel()
        dlg._worker.request_cancel.assert_called_once()

    def test_close_event_cancels_running_worker(self, qtbot, db):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        worker = MagicMock()
        worker.isRunning.return_value = True
        dlg._worker = worker
        dlg.close()
        worker.request_cancel.assert_called_once()
        # wait() must be called with no timeout so the dialog never destroys
        # a still-running QThread (which causes SIGABRT).
        worker.wait.assert_called_once_with()

    def test_start_connects_finished_to_delete_later(self, qtbot, db, monkeypatch):
        # finished must be connected to deleteLater so Qt cleans up the C++
        # thread object only after the thread finishes, preventing GC-driven SIGABRT.
        dlg = UpdateLocationDialog()
        qtbot.addWidget(dlg)
        monkeypatch.setattr(dlg, "_build_rows", lambda: [_CacheRow("GC1", 55.0, 12.0)])
        connected = []

        class FakeWorker:
            def __init__(self, rows):
                self.row_done = MagicMock()
                self.all_done = MagicMock()
                self.cancelled = MagicMock()
                self.finished = MagicMock()

            def start(self):
                pass

            def isRunning(self):
                return False

            def deleteLater(self):
                pass

        fake = FakeWorker([])
        monkeypatch.setattr(uld, "ReverseGeocodeWorker", lambda rows: fake)
        dlg._start()
        fake.finished.connect.assert_any_call(fake.deleteLater)

    def test_menu_mode_finalize_redisables_this(self, qtbot, db):
        dlg = UpdateLocationDialog()
        qtbot.addWidget(dlg)
        dlg._on_done(UpdateLocationResult(updated=1))
        assert dlg._rb_this.isEnabled() is False

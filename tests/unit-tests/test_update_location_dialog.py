# tests/unit-tests/test_update_location_dialog.py — reverse-geocode location updater.

from pathlib import Path
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
from opensak.geo.store import Region


def _loc(country="DK", state="ZL", county="CPH"):
    return SimpleNamespace(country=country, state=state, county=county)


def _fake_store(available=True, version="42", data_dir=None):
    store = MagicMock()
    store.available.return_value = available
    store.dataset_version.return_value = version
    # No candidates by default — Phase 0 pre-fetch (#722) cleanly finds
    # nothing to do rather than falling through the try/except by accident.
    store.candidates.return_value = []
    store.data_dir = data_dir or Path("/tmp/opensak-test-boundaries")
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
def geo_mocks(monkeypatch, tmp_path):
    store = _fake_store(data_dir=tmp_path / "boundaries")
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

    def test_run_chunks_large_bulk_write(self, db, geo_mocks, monkeypatch):
        # Regression (#60): a large DB overflowed SQLite's bound-parameter limit
        # because the bulk write bound every gc_code in one IN(...). The write is
        # chunked so no single IN exceeds 500 codes.
        with get_session() as s:
            for i in range(3, 1103):
                s.add(Cache(gc_code=f"GC{i}", name="x",
                            cache_type="Traditional Cache",
                            latitude=55.0, longitude=12.0))
        rows = [_CacheRow(f"GC{i}", 55.0, 12.0) for i in range(1, 1103)]

        sizes = []
        real_in = type(Cache.gc_code).in_
        def spy(self, other):
            other = list(other)
            sizes.append(len(other))
            return real_in(self, other)
        monkeypatch.setattr(type(Cache.gc_code), "in_", spy)

        w = ReverseGeocodeWorker(rows)
        done = []
        w.all_done.connect(done.append)
        w.run()

        assert done[0].updated == 1102
        assert sizes and max(sizes) <= 500

    def test_default_basis_is_posted(self):
        row = _CacheRow("GC1", 55.0, 12.0)
        assert row.basis == "posted"

    def test_explicit_basis_corrected(self):
        row = _CacheRow("GC1", 55.0, 12.0, basis="corrected")
        assert row.basis == "corrected"


# ── ReverseGeocodeWorker Phase 0: county pre-fetch (#722) ──────────────────────

class TestPreFetchPhase:
    def test_collects_and_downloads_needed_packs(self, db, geo_mocks, monkeypatch):
        store, _resolver = geo_mocks
        store.candidates.return_value = [1]
        store.region.return_value = Region(
            id=1, name="AlphaCounty", parent=None, pack="aa.geojson", feature_index=0
        )
        calls = []
        monkeypatch.setattr(
            "opensak.geo.packs.fetch_packs",
            lambda names, dest, **kw: calls.append((list(names), dest)) or 1,
        )
        w = ReverseGeocodeWorker([_CacheRow("GC1", 55.0, 12.0)])
        done = []
        w.all_done.connect(done.append)
        w.run()
        assert len(calls) == 1
        names, dest = calls[0]
        assert names == ["aa.geojson"]
        assert dest == store.data_dir / "counties"
        assert done[0].updated == 1  # Phase 1 still ran normally afterwards

    def test_skips_fetch_when_no_candidates(self, db, geo_mocks, monkeypatch):
        # geo_mocks defaults store.candidates to [] — nothing needed.
        calls = []
        monkeypatch.setattr(
            "opensak.geo.packs.fetch_packs",
            lambda *a, **kw: calls.append(a) or 0,
        )
        w = ReverseGeocodeWorker([_CacheRow("GC1", 55.0, 12.0)])
        done = []
        w.all_done.connect(done.append)
        w.run()
        assert calls == []
        assert done[0].updated == 1

    def test_deduplicates_packs_needed_by_multiple_rows(self, db, geo_mocks, monkeypatch):
        store, _resolver = geo_mocks
        store.candidates.return_value = [1]
        store.region.return_value = Region(
            id=1, name="AlphaCounty", parent=None, pack="aa.geojson", feature_index=0
        )
        calls = []
        monkeypatch.setattr(
            "opensak.geo.packs.fetch_packs",
            lambda names, dest, **kw: calls.append(list(names)) or 1,
        )
        rows = [_CacheRow("GC1", 55.0, 12.0), _CacheRow("GC2", 56.0, 10.0)]
        w = ReverseGeocodeWorker(rows)
        done = []
        w.all_done.connect(done.append)
        w.run()
        assert len(calls) == 1
        assert calls[0] == ["aa.geojson"]  # both rows needed it, fetched once

    def test_progress_emitted_via_row_done_signal(self, db, geo_mocks, monkeypatch):
        # Language state is global (opensak.lang._translations) and can be
        # left loaded by an earlier test in the same session — pin it
        # explicitly so this assertion doesn't depend on run order.
        from opensak.lang import load_language
        load_language("en")

        store, _resolver = geo_mocks
        store.candidates.return_value = [1]
        store.region.return_value = Region(
            id=1, name="AlphaCounty", parent=None, pack="aa.geojson", feature_index=0
        )

        def _fake_fetch_packs(names, dest, progress_cb=None, **kw):
            if progress_cb:
                progress_cb(1, 1)
            return 1

        monkeypatch.setattr("opensak.geo.packs.fetch_packs", _fake_fetch_packs)
        w = ReverseGeocodeWorker([_CacheRow("GC1", 55.0, 12.0)])
        lines = []
        w.row_done.connect(lambda gc_code, line: lines.append((gc_code, line)))
        w.run()
        # An empty gc_code marks an informational line (not a resolved row),
        # matching the existing update_loc_no_boundaries convention.
        prefetch_lines = [line for gc_code, line in lines if gc_code == ""]
        assert any("1/1" in line for line in prefetch_lines)

    def test_prefetch_failure_falls_back_gracefully(self, db, geo_mocks, monkeypatch):
        # Any unexpected error scanning candidates must not abort the whole
        # geocode run — Phase 1's normal on-demand fetch is the fallback.
        store, _resolver = geo_mocks
        store.candidates.side_effect = RuntimeError("boom")
        w = ReverseGeocodeWorker([_CacheRow("GC1", 55.0, 12.0)])
        done = []
        w.all_done.connect(done.append)
        w.run()
        assert done[0].updated == 1
        assert done[0].errors == 0

    def test_fetch_packs_error_falls_back_gracefully(self, db, geo_mocks, monkeypatch):
        store, _resolver = geo_mocks
        store.candidates.return_value = [1]
        store.region.return_value = Region(
            id=1, name="AlphaCounty", parent=None, pack="aa.geojson", feature_index=0
        )
        monkeypatch.setattr(
            "opensak.geo.packs.fetch_packs",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("network boom")),
        )
        w = ReverseGeocodeWorker([_CacheRow("GC1", 55.0, 12.0)])
        done = []
        w.all_done.connect(done.append)
        w.run()
        assert done[0].updated == 1
        assert done[0].errors == 0


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
            def __init__(self, rows, parent=None):
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

    def test_close_cancels_running_worker_and_stays_open(self, qtbot, db):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        dlg.show()
        worker = MagicMock()
        worker.isRunning.return_value = True
        dlg._worker = worker
        dlg.close()
        worker.request_cancel.assert_called_once()
        # #1265: the dialog stays open until the worker is done, so it never
        # destroys a still-running QThread (SIGABRT) — without blocking on
        # wait() the way closeEvent() used to.
        assert dlg.isVisible()
        worker.wait.assert_not_called()

    def test_start_connects_finished_to_delete_later(self, qtbot, db, monkeypatch):
        # finished must be connected to deleteLater so Qt cleans up the C++
        # thread object only after the thread finishes, preventing GC-driven SIGABRT.
        dlg = UpdateLocationDialog()
        qtbot.addWidget(dlg)
        monkeypatch.setattr(dlg, "_build_rows", lambda: [_CacheRow("GC1", 55.0, 12.0)])
        connected = []

        class FakeWorker:
            def __init__(self, rows, parent=None):
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
        monkeypatch.setattr(
            uld, "ReverseGeocodeWorker", lambda rows, parent=None: fake
        )
        dlg._start()
        fake.finished.connect.assert_any_call(fake.deleteLater)
        # The reference must be dropped from a finished-slot, never from
        # all_done/cancelled (which fire while run() is still on the stack).
        fake.finished.connect.assert_any_call(dlg._on_worker_finished)

    def test_worker_reference_survives_all_done_and_drops_on_finished(
        self, qtbot, db
    ):
        # Regression: _finalize() used to clear _worker while run() was still
        # executing, leaving run()'s own stack frame as the last reference —
        # the QThread was then destroyed from inside its own thread, which Qt
        # aborts on ("QThread: Destroyed while thread is still running").
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        worker = MagicMock()
        dlg._worker = worker
        dlg._on_done(UpdateLocationResult(updated=1))
        assert dlg._worker is worker
        dlg._on_worker_finished()
        assert dlg._worker is None

    def test_menu_mode_finalize_redisables_this(self, qtbot, db):
        dlg = UpdateLocationDialog()
        qtbot.addWidget(dlg)
        dlg._on_done(UpdateLocationResult(updated=1))
        assert dlg._rb_this.isEnabled() is False

"""tests/unit-tests/test_update_location_dialog.py — reverse-geocode location updater."""

from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock

pytest.importorskip("pytestqt")

import opensak.utils.flags as flags
from opensak.db.database import get_session, init_db
from opensak.db.models import Cache
from opensak.gui.dialogs import update_location_dialog as uld
from opensak.gui.dialogs.update_location_dialog import (
    OnlineLookupWorker,
    ReverseGeocodeWorker,
    UpdateLocationDialog,
    UpdateLocationResult,
    _CacheRow,
    _format_eta,
)


def _loc(country="DK", state="ZL", county="CPH"):
    return SimpleNamespace(country=country, state=state, county=county)


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
def gui(monkeypatch):
    monkeypatch.setattr(flags, "update_location", True, raising=False)
    monkeypatch.setattr(
        "opensak.gui.settings.get_settings",
        lambda: SimpleNamespace(nominatim_enabled=False),
    )


# ── _format_eta ───────────────────────────────────────────────────────────────

class TestFormatEta:
    @pytest.mark.parametrize("secs", [30, 120, 7200])
    def test_returns_string(self, secs):
        assert isinstance(_format_eta(secs), str)


# ── ReverseGeocodeWorker ──────────────────────────────────────────────────────

class TestReverseGeocodeWorker:
    def test_run_updates_db(self, db, monkeypatch):
        monkeypatch.setattr("opensak.geocoder.fast_batch_geocode", lambda c: [_loc()])
        w = ReverseGeocodeWorker([_CacheRow("GC1", 55.0, 12.0)])
        done = []
        w.all_done.connect(done.append)
        w.run()
        assert done[0].updated == 1
        with get_session() as s:
            assert s.query(Cache).filter_by(gc_code="GC1").first().country == "DK"

    def test_run_cancelled_before_start(self, db):
        w = ReverseGeocodeWorker([_CacheRow("GC1", 55.0, 12.0)])
        w.request_cancel()
        cancelled = []
        w.cancelled.connect(cancelled.append)
        w.run()
        assert cancelled

    def test_run_error_still_finishes(self, db, monkeypatch):
        monkeypatch.setattr("opensak.geocoder.fast_batch_geocode", lambda c: [_loc()])
        def boom():
            raise RuntimeError("db boom")
        monkeypatch.setattr("opensak.db.database.get_session", boom)
        w = ReverseGeocodeWorker([_CacheRow("GC1", 55.0, 12.0)])
        done = []
        w.all_done.connect(done.append)
        w.run()
        assert done[0].errors == 1


# ── OnlineLookupWorker ────────────────────────────────────────────────────────

class TestOnlineLookupWorker:
    def test_run_updates_partial_fields(self, db, monkeypatch):
        monkeypatch.setattr(
            "opensak.geocoder.nominatim_reverse",
            lambda lat, lon: _loc(country="NEW", state="ST", county="C2"),
        )
        w = OnlineLookupWorker([_CacheRow("GC1", 55.0, 12.0)])
        done = []
        w.all_done.connect(done.append)
        w.run()
        assert done[0].updated == 1
        with get_session() as s:
            assert s.query(Cache).filter_by(gc_code="GC1").first().country == "NEW"

    def test_run_error_path(self, db, monkeypatch):
        monkeypatch.setattr(
            "opensak.geocoder.nominatim_reverse", lambda lat, lon: _loc(country="X")
        )
        def boom():
            raise RuntimeError("db down")
        monkeypatch.setattr("opensak.db.database.get_session", boom)
        w = OnlineLookupWorker([_CacheRow("GC1", 55.0, 12.0)])
        done = []
        w.all_done.connect(done.append)
        w.run()
        assert done[0].errors == 1

    def test_run_skips_empty_response(self, db, monkeypatch):
        monkeypatch.setattr(
            "opensak.geocoder.nominatim_reverse", lambda lat, lon: _loc(None, None, None)
        )
        w = OnlineLookupWorker([_CacheRow("GC1", 55.0, 12.0)])
        done = []
        w.all_done.connect(done.append)
        w.run()
        assert done[0].skipped == 1

    def test_run_cancelled_before_start(self, db):
        w = OnlineLookupWorker([_CacheRow("GC1", 55.0, 12.0)])
        w.request_cancel()
        cancelled = []
        w.cancelled.connect(cancelled.append)
        w.run()
        assert cancelled


# ── UpdateLocationDialog ──────────────────────────────────────────────────────

class TestUpdateLocationDialog:
    def test_menu_mode_defaults(self, qtbot, gui):
        dlg = UpdateLocationDialog()
        qtbot.addWidget(dlg)
        assert dlg._rb_this.isEnabled() is False
        assert dlg._rb_missing.isChecked() is True

    def test_context_menu_mode_defaults(self, qtbot, gui):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        assert dlg._rb_this.isChecked() is True

    def test_online_flag_off_hides_checkbox(self, qtbot, monkeypatch):
        monkeypatch.setattr(flags, "update_location", False, raising=False)
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        assert dlg._cb_online is None

    def test_online_toggle_changes_info(self, qtbot, gui):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        dlg._cb_online.setChecked(True)
        on = dlg._info_label.text()
        dlg._cb_online.setChecked(False)
        assert on != dlg._info_label.text()

    def test_build_rows_missing_scope(self, qtbot, gui, db):
        dlg = UpdateLocationDialog()
        qtbot.addWidget(dlg)
        dlg._rb_missing.setChecked(True)
        codes = {r.gc_code for r in dlg._build_rows()}
        assert "GC1" in codes and "GC2" not in codes

    def test_build_rows_all_scope(self, qtbot, gui, db):
        dlg = UpdateLocationDialog()
        qtbot.addWidget(dlg)
        dlg._rb_all.setChecked(True)
        assert {r.gc_code for r in dlg._build_rows()} == {"GC1", "GC2"}

    def test_build_rows_this_scope(self, qtbot, gui, db):
        dlg = UpdateLocationDialog(gc_codes=["GC2"])
        qtbot.addWidget(dlg)
        dlg._rb_this.setChecked(True)
        assert {r.gc_code for r in dlg._build_rows()} == {"GC2"}

    def test_build_rows_uses_corrected_coords(self, qtbot, gui, tmp_path):
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

    def test_menu_mode_finalize_redisables_this(self, qtbot, gui, db):
        dlg = UpdateLocationDialog()  # menu mode
        qtbot.addWidget(dlg)
        dlg._cb_online.setChecked(False)
        dlg._on_phase1_done(UpdateLocationResult(updated=1))
        assert dlg._rb_this.isEnabled() is False

    def test_start_nothing_to_do(self, qtbot, gui, db, monkeypatch):
        dlg = UpdateLocationDialog()
        qtbot.addWidget(dlg)
        monkeypatch.setattr(dlg, "_build_rows", lambda: [])
        dlg._start()
        assert dlg._progress_label.text() != ""

    def test_start_launches_phase1_worker(self, qtbot, gui, db, monkeypatch):
        dlg = UpdateLocationDialog()
        qtbot.addWidget(dlg)
        monkeypatch.setattr(dlg, "_build_rows", lambda: [_CacheRow("GC1", 55.0, 12.0)])
        started = []

        class FakeWorker:
            def __init__(self, rows):
                self.row_done = MagicMock()
                self.all_done = MagicMock()
                self.cancelled = MagicMock()

            def start(self):
                started.append(True)

            def isRunning(self):
                return False

        monkeypatch.setattr(uld, "ReverseGeocodeWorker", FakeWorker)
        dlg._start()
        assert started == [True]
        assert dlg._start_btn.isEnabled() is False

    def test_phase1_done_finalizes_without_online(self, qtbot, gui, db):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        dlg._cb_online.setChecked(False)
        fired = []
        dlg.location_updated.connect(lambda: fired.append(True))
        dlg._on_phase1_done(UpdateLocationResult(updated=2))
        assert fired == [True]
        assert dlg._start_btn.isEnabled() is True

    def test_phase1_done_starts_online_phase(self, qtbot, gui, db, monkeypatch):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        dlg._cb_online.setChecked(True)
        dlg._pending_rows = [_CacheRow("GC1", 55.0, 12.0)]
        started = []

        class FakeWorker:
            def __init__(self, rows):
                self.row_done = MagicMock()
                self.progress = MagicMock()
                self.all_done = MagicMock()
                self.cancelled = MagicMock()

            def start(self):
                started.append(True)

            def isRunning(self):
                return False

        monkeypatch.setattr(uld, "OnlineLookupWorker", FakeWorker)
        dlg._on_phase1_done(UpdateLocationResult(updated=1))
        assert started == [True]

    def test_phase1_cancelled_emits_when_updated(self, qtbot, gui, db):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        fired = []
        dlg.location_updated.connect(lambda: fired.append(True))
        dlg._on_phase1_cancelled(UpdateLocationResult(updated=1))
        assert fired == [True]

    def test_online_progress_updates_bar(self, qtbot, gui, db):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        dlg._progress.setRange(0, 10)
        dlg._on_online_progress(3, 10)
        assert dlg._progress.value() == 3

    def test_online_done_and_cancelled(self, qtbot, gui, db):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        fired = []
        dlg.location_updated.connect(lambda: fired.append(True))
        dlg._on_online_done(UpdateLocationResult(updated=1))
        dlg._on_online_cancelled(UpdateLocationResult(updated=1))
        assert fired.count(True) >= 1
        assert dlg._progress_label.text() != ""

    def test_row_done_appends_to_log(self, qtbot, gui, db):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        dlg._on_row_done("GC1", "a log line")
        assert "a log line" in dlg._log.toPlainText()

    def test_request_cancel(self, qtbot, gui, db):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        dlg._worker = MagicMock()
        dlg._request_cancel()
        dlg._worker.request_cancel.assert_called_once()

    def test_close_event_cancels_running_worker(self, qtbot, gui, db):
        dlg = UpdateLocationDialog(gc_codes=["GC1"])
        qtbot.addWidget(dlg)
        worker = MagicMock()
        worker.isRunning.return_value = True
        dlg._worker = worker
        dlg.close()
        worker.request_cancel.assert_called_once()

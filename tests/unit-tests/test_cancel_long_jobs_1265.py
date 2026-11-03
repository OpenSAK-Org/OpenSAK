# tests/unit-tests/test_cancel_long_jobs_1265.py
#
# #1265: Cancel (and Escape) stops the long jobs — GPX/ZIP import, the file,
# KML and GPS exports and Move/Copy caches — at points where stopping leaves
# nothing half written. The dialogs' side (they stay open until the worker is
# done) is in tests/e2e-tests/test_e2e_dialog_close_running_worker.py.

from types import SimpleNamespace

import pytest

from opensak.db.database import get_session, init_db
from opensak.db.models import Cache
from opensak.utils.cancel import CancelFlag, Cancelled

from tests.data import build_gpx, cache_wpt, make_zip, write_gpx


def _codes(db_path) -> set[str]:
    init_db(db_path=db_path)
    with get_session() as s:
        return {c for (c,) in s.query(Cache.gc_code)}


def _cache(gc_code: str):
    # Enough of a Cache for the exporters (same shape as test_export_dialogs).
    return SimpleNamespace(
        id=1, gc_code=gc_code, gc_cache_id=None, name="Test", cache_type="Traditional Cache",
        latitude=55.0, longitude=12.0, difficulty=2.0, terrain=3.0,
        placed_by="Owner", owner_name=None, owner_id=None,
        available=True, archived=False, country="Denmark", state=None,
        encoded_hints=None, hidden_date=None, logs=[], user_note=None,
        container="Small", found=False,
        short_description="", short_desc_html=False,
        long_description=None, long_desc_html=False, attributes=[],
        waypoints=[],
    )


# ── CancelFlag and the export progress callback ──────────────────────────────

class TestProgressCallback:
    def test_raises_once_the_flag_is_set(self):
        from opensak.gui.dialogs import make_progress_cb

        flag = CancelFlag()
        seen: list[tuple[int, int]] = []
        cb = make_progress_cb(lambda d, t: seen.append((d, t)), flag)
        cb(1, 2)
        flag.set()
        with pytest.raises(Cancelled):
            cb(2, 2)
        assert seen == [(1, 2)]

    def test_cancelled_passes_except_exception(self):
        # The GPS exports turn any Exception into an error message; a cancel
        # must not end up as one.
        with pytest.raises(Cancelled):
            try:
                raise Cancelled
            except Exception:
                pytest.fail("Cancelled was caught as an Exception")


# ── Import ────────────────────────────────────────────────────────────────────

def _gpx(tmp_path, count: int):
    wpts = [cache_wpt(f"GC{i:03d}", gs_id=i + 1) for i in range(count)]
    return write_gpx(tmp_path, "pq.gpx", build_gpx(*wpts))


class TestImportCancel:
    def test_gpx_stops_after_the_committed_batch(self, tmp_path):
        from opensak.importer import import_gpx

        db = tmp_path / "t.db"
        init_db(db_path=db)
        result = import_gpx(_gpx(tmp_path, 5), batch_size=2, should_cancel=lambda: True)
        assert result.cancelled is True
        assert _codes(db) == {"GC000", "GC001"}

    def test_gpx_without_cancel_imports_everything(self, tmp_path):
        from opensak.importer import import_gpx

        db = tmp_path / "t.db"
        init_db(db_path=db)
        result = import_gpx(_gpx(tmp_path, 5), batch_size=2, should_cancel=lambda: False)
        assert result.cancelled is False
        assert len(_codes(db)) == 5

    def test_zip_cancelled_before_writing_writes_nothing(self, tmp_path):
        from opensak.importer import import_zip

        db = tmp_path / "t.db"
        init_db(db_path=db)
        gpx = _gpx(tmp_path, 3)
        result = import_zip(make_zip(tmp_path, "pq.zip", {"pq.gpx": gpx}),
                            should_cancel=lambda: True)
        assert result.cancelled is True
        assert _codes(db) == set()

    def test_worker_stops_before_the_next_file(self, tmp_path, monkeypatch):
        from opensak.gui.dialogs import import_dialog
        from opensak.importer import ImportResult

        init_db(db_path=tmp_path / "t.db")
        imported: list[str] = []

        def fake_import(path, session, progress_cb=None, should_cancel=None):
            imported.append(path.name)
            worker.cancel.set()   # the user presses Cancel during the first file
            return ImportResult()

        monkeypatch.setattr(import_dialog, "import_file", fake_import)
        paths = [write_gpx(tmp_path, n, build_gpx(cache_wpt("GC1"))) for n in ("a.gpx", "b.gpx")]
        worker = import_dialog.ImportWorker(paths)
        worker.run()
        assert imported == ["a.gpx"]


# ── Move / copy caches ────────────────────────────────────────────────────────

class TestTransferCancel:
    @pytest.fixture
    def dbs(self, tmp_path, monkeypatch):
        from opensak.db import transfer

        monkeypatch.setattr(transfer, "CHUNK_SIZE", 1)
        source, target = tmp_path / "source.db", tmp_path / "target.db"
        init_db(db_path=source)
        with get_session() as s:
            for code in ("GC1", "GC2", "GC3"):
                s.add(Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                            latitude=55.0, longitude=12.0))
        init_db(db_path=target)
        return source, target

    @pytest.mark.parametrize("copy_only", [False, True])
    def test_stops_between_chunks_only(self, dbs, copy_only):
        """AgreeDK on #1265: the gap between the target's commit and the
        source's delete. Cancel is only looked at before a chunk, so a chunk
        that reached the target is always deleted from the source too."""
        from opensak.db.transfer import transfer_caches

        source, target = dbs
        answers = iter([False, True])   # cancelled while the first chunk runs
        count = transfer_caches(["GC1", "GC2", "GC3"], source, target,
                                copy_only=copy_only, should_cancel=lambda: next(answers))
        assert count == 1
        assert _codes(target) == {"GC1"}
        if copy_only:
            assert _codes(source) == {"GC1", "GC2", "GC3"}
        else:
            # Nothing is in both databases.
            assert _codes(source) == {"GC2", "GC3"}

    def test_move_worker_reports_cancel(self, dbs):
        from opensak.gui.dialogs.move_caches_dialog import _MoveWorker

        source, target = dbs
        worker = _MoveWorker(["GC1", "GC2"], source, target)
        worker.cancel.set()
        cancelled: list[int] = []
        finished: list[int] = []
        worker.cancelled.connect(cancelled.append)
        worker.finished.connect(finished.append)
        worker.run()
        assert cancelled == [0] and finished == []
        assert _codes(source) == {"GC1", "GC2", "GC3"}


# ── Exports ───────────────────────────────────────────────────────────────────

class TestExportCancel:
    @pytest.mark.parametrize("fmt", ["gpx", "loc", "ggz"])
    def test_file_export_keeps_the_previous_file(self, tmp_path, fmt):
        from opensak.gui.dialogs.file_export_dialog import _ExportWorker

        out = tmp_path / f"export.{fmt}"
        out.write_bytes(b"previous export")
        worker = _ExportWorker([_cache("GC1"), _cache("GC2")], out, fmt)
        worker.cancel.set()
        cancelled: list[bool] = []
        errors: list[str] = []
        worker.cancelled.connect(lambda: cancelled.append(True))
        worker.error.connect(errors.append)
        worker.run()
        assert cancelled == [True] and errors == []
        assert out.read_bytes() == b"previous export"
        assert list(tmp_path.iterdir()) == [out]   # no .part file left

    def test_kml_export_writes_nothing(self, tmp_path, monkeypatch):
        from opensak.gui.dialogs.kml_export_dialog import _ExportWorker

        monkeypatch.setattr("opensak.db.database.reload_caches_full", lambda caches: caches)
        out = tmp_path / "export.kml"
        worker = _ExportWorker([_cache("GC1")], str(out), True, True)
        worker.cancel.set()
        cancelled: list[bool] = []
        worker.cancelled.connect(lambda: cancelled.append(True))
        worker.run()
        assert cancelled == [True]
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize("fmt", ["gpx", "ggz"])
    def test_gps_export_reports_cancel_not_an_error(self, tmp_path, monkeypatch, fmt):
        from opensak.gui.dialogs.gps_dialog import ExportWorker

        monkeypatch.setattr("opensak.db.database.iter_caches_full", lambda caches: iter(caches))
        worker = ExportWorker([_cache("GC1"), _cache("GC2")], tmp_path, "opensak", 0, fmt)
        worker.cancel.set()
        cancelled: list[bool] = []
        results: list[object] = []
        worker.cancelled.connect(lambda: cancelled.append(True))
        worker.finished.connect(results.append)
        worker.run()
        assert cancelled == [True] and results == []
        assert list(tmp_path.iterdir()) == []

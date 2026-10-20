# tests/unit-tests/test_found_dialog.py — found-status updater dialog.

from pathlib import Path
from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock

pytest.importorskip("pytestqt")

from opensak.gui.dialogs import found_dialog as fd
from opensak.gui.dialogs.found_dialog import FoundUpdaterDialog, UpdateWorker


def _db(name, path, exists=True):
    return SimpleNamespace(name=name, path=Path(path), exists=exists)


def _manager(others=None):
    active = _db("Active", "/active.db")
    return SimpleNamespace(active=active, databases=[active] + (others or []))


@pytest.fixture
def patch_manager(monkeypatch):
    def _apply(mgr):
        monkeypatch.setattr(fd, "get_db_manager", lambda: mgr)
        return mgr
    return _apply


# ── UpdateWorker ──────────────────────────────────────────────────────────────

class TestUpdateWorker:
    def test_run_success_emits_result(self, monkeypatch):
        result = SimpleNamespace(updated=2, errors=[])
        monkeypatch.setattr(
            "opensak.db.found_updater.update_found_from_reference",
            lambda p: result,
        )
        w = UpdateWorker(Path("/ref.db"))
        got = []
        w.finished.connect(got.append)
        w.run()
        assert got == [result]

    def test_run_error_emits_traceback(self, monkeypatch):
        def boom(p):
            raise RuntimeError("update failed")
        monkeypatch.setattr(
            "opensak.db.found_updater.update_found_from_reference", boom
        )
        w = UpdateWorker(Path("/ref.db"))
        errs = []
        w.error.connect(errs.append)
        w.run()
        assert errs and "update failed" in errs[0]


# ── FoundUpdaterDialog ────────────────────────────────────────────────────────

class TestFoundUpdaterDialog:
    def test_combo_lists_other_databases(self, qtbot, patch_manager):
        patch_manager(_manager(others=[_db("Ref", "/ref.db")]))
        dlg = FoundUpdaterDialog()
        qtbot.addWidget(dlg)
        assert dlg._db_combo.isEnabled() is True
        assert dlg._db_combo.count() == 1

    def test_no_other_databases_disables_combo(self, qtbot, patch_manager):
        patch_manager(_manager(others=[]))
        dlg = FoundUpdaterDialog()
        qtbot.addWidget(dlg)
        assert dlg._db_combo.isEnabled() is False

    def test_source_toggle_enables_disables_combo(self, qtbot, patch_manager):
        patch_manager(_manager(others=[_db("Ref", "/ref.db")]))
        dlg = FoundUpdaterDialog()
        qtbot.addWidget(dlg)
        dlg._rb_file.setChecked(True)
        assert dlg._db_combo.isEnabled() is False
        dlg._rb_known.setChecked(True)
        assert dlg._db_combo.isEnabled() is True

    def test_browse_sets_reference_path(self, qtbot, patch_manager, monkeypatch):
        patch_manager(_manager(others=[_db("Ref", "/ref.db")]))
        dlg = FoundUpdaterDialog()
        qtbot.addWidget(dlg)
        monkeypatch.setattr(fd.QFileDialog, "getOpenFileName", lambda *a, **k: ("/tmp/finds.db", "f"))
        dlg._browse_file()
        assert dlg._reference_path == Path("/tmp/finds.db")
        assert dlg._rb_file.isChecked() is True

    def test_browse_cancel_keeps_none(self, qtbot, patch_manager, monkeypatch):
        patch_manager(_manager(others=[_db("Ref", "/ref.db")]))
        dlg = FoundUpdaterDialog()
        qtbot.addWidget(dlg)
        monkeypatch.setattr(fd.QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
        dlg._browse_file()
        assert dlg._reference_path is None

    def test_get_reference_path_known_vs_file(self, qtbot, patch_manager):
        patch_manager(_manager(others=[_db("Ref", "/ref.db")]))
        dlg = FoundUpdaterDialog()
        qtbot.addWidget(dlg)
        assert dlg._get_reference_path() == Path("/ref.db")  # known mode
        dlg._reference_path = Path("/file.db")
        dlg._rb_file.setChecked(True)
        assert dlg._get_reference_path() == Path("/file.db")

    def test_start_update_without_ref_logs(self, qtbot, patch_manager):
        patch_manager(_manager(others=[]))
        dlg = FoundUpdaterDialog()
        qtbot.addWidget(dlg)
        dlg._rb_file.setChecked(True)  # file mode, no file chosen
        dlg._start_update()
        assert dlg._log.toPlainText() != ""

    def test_start_update_rejects_same_as_active(self, qtbot, patch_manager):
        patch_manager(_manager(others=[]))
        dlg = FoundUpdaterDialog()
        qtbot.addWidget(dlg)
        dlg._rb_file.setChecked(True)
        dlg._reference_path = Path("/active.db")  # == active
        dlg._start_update()
        assert dlg._log.toPlainText() != ""
        assert dlg._update_btn.isEnabled() is True  # not started

    def test_start_update_launches_worker(self, qtbot, patch_manager, monkeypatch):
        patch_manager(_manager(others=[_db("Ref", "/ref.db")]))
        dlg = FoundUpdaterDialog()
        qtbot.addWidget(dlg)
        started = []

        class FakeWorker:
            def __init__(self, path):
                self.finished = MagicMock()
                self.error = MagicMock()

            def start(self):
                started.append(True)

        monkeypatch.setattr(fd, "UpdateWorker", FakeWorker)
        dlg._start_update()
        assert started == [True]
        assert dlg._update_btn.isEnabled() is False

    def test_on_finished_emits_completed_when_updated(self, qtbot, patch_manager):
        patch_manager(_manager(others=[]))
        dlg = FoundUpdaterDialog()
        qtbot.addWidget(dlg)
        fired = []
        dlg.update_completed.connect(lambda: fired.append(True))
        dlg._on_finished(SimpleNamespace(updated=3, already=0, not_found=0, errors=["e1"]))
        assert fired == [True]
        assert "e1" in dlg._log.toPlainText()

    def test_on_finished_no_signal_when_zero_updated(self, qtbot, patch_manager):
        patch_manager(_manager(others=[]))
        dlg = FoundUpdaterDialog()
        qtbot.addWidget(dlg)
        fired = []
        dlg.update_completed.connect(lambda: fired.append(True))
        dlg._on_finished(SimpleNamespace(updated=0, already=0, not_found=0, errors=[]))
        assert fired == []

    def test_on_error_logs(self, qtbot, patch_manager):
        patch_manager(_manager(others=[]))
        dlg = FoundUpdaterDialog()
        qtbot.addWidget(dlg)
        dlg._on_error("boom")
        assert "boom" in dlg._log.toPlainText()
        assert dlg._update_btn.isEnabled() is True

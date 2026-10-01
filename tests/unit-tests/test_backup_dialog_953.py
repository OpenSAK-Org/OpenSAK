"""
tests/unit-tests/test_backup_dialog_953.py — File → Back up now… (#953).

The worker is tested by calling run() directly (as in
test_gsak_import_dialog.py). The dialog runs its worker synchronously here
and its message boxes are replaced by a mock, so nothing blocks.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt

from opensak.backup import backupset
from opensak.backup.backupset import (
    BACKUP_DIR_KEY,
    KIND_AUTO,
    KIND_MANUAL,
    BackupError,
    NotEnoughSpaceError,
    list_backup_sets,
)
from opensak.gui.dialogs import backup_dialog as bdlg
from opensak.gui.dialogs.backup_dialog import BackupDialog, BackupWorker
from opensak.lang import tr
from opensak.settings_store import get_install_dir, get_store


def _make_db(path: Path, rows: int = 20) -> SimpleNamespace:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE caches (code TEXT PRIMARY KEY)")
    conn.executemany("INSERT INTO caches VALUES (?)",
                     [(f"GC{i:04d}",) for i in range(rows)])
    conn.commit()
    conn.close()
    return SimpleNamespace(name=path.stem, path=path)


class _Recorder:
    """Collects a worker's signals."""

    def __init__(self, worker: BackupWorker) -> None:
        self.progress: list[tuple] = []
        self.succeeded: list = []
        self.failed: list[tuple] = []
        self.cancelled = 0
        worker.progress.connect(lambda *a: self.progress.append(a))
        worker.succeeded.connect(self.succeeded.append)
        worker.failed.connect(lambda *a: self.failed.append(a))
        worker.cancelled.connect(self._on_cancel)

    def _on_cancel(self) -> None:
        self.cancelled += 1


# ── BackupWorker ──────────────────────────────────────────────────────────────

class TestBackupWorker:
    def test_success_emits_result_and_progress(self, qapp, tmp_path):
        dbs = [_make_db(tmp_path / "dbs" / "A.db"), _make_db(tmp_path / "dbs" / "B.db")]
        w = BackupWorker(dbs, KIND_MANUAL, tmp_path / "backups")
        rec = _Recorder(w)
        w.run()
        assert len(rec.succeeded) == 1 and not rec.failed and not rec.cancelled
        result = rec.succeeded[0]
        assert result.backup_set.kind == KIND_MANUAL
        assert {name for name, _, _ in rec.progress} == {"A", "B"}
        assert rec.progress[-1][2] == pytest.approx(1.0)

    def test_cancel_before_start(self, qapp, tmp_path):
        w = BackupWorker([_make_db(tmp_path / "A.db")], KIND_MANUAL, tmp_path / "backups")
        rec = _Recorder(w)
        w.request_cancel()
        w.run()
        assert rec.cancelled == 1 and not rec.succeeded
        assert not (tmp_path / "backups").exists() or list_backup_sets(tmp_path / "backups") == []

    def test_cancel_mid_way_leaves_nothing(self, qapp, tmp_path):
        dbs = [_make_db(tmp_path / "dbs" / "A.db"), _make_db(tmp_path / "dbs" / "B.db")]
        backups = tmp_path / "backups"
        w = BackupWorker(dbs, KIND_MANUAL, backups)
        rec = _Recorder(w)
        w.progress.connect(lambda *a: w.request_cancel())  # cancel on first report
        w.run()
        assert rec.cancelled == 1 and not rec.succeeded and not rec.failed
        assert list(backups.iterdir()) == []

    def test_not_enough_space_is_reported_as_such(self, qapp, tmp_path):
        w = BackupWorker([_make_db(tmp_path / "A.db")], KIND_MANUAL, tmp_path / "b")
        rec = _Recorder(w)
        with patch.object(bdlg, "write_backup_set",
                          side_effect=NotEnoughSpaceError("Not enough free space")):
            w.run()
        assert rec.failed == [("Not enough free space", True)]

    def test_other_errors_are_reported(self, qapp, tmp_path):
        w = BackupWorker([_make_db(tmp_path / "A.db")], KIND_MANUAL, tmp_path / "b")
        rec = _Recorder(w)
        with patch.object(bdlg, "write_backup_set", side_effect=BackupError("boom")):
            w.run()
        assert rec.failed == [("boom", False)]

    def test_rotate_after_for_the_on_exit_prompt(self, qapp, tmp_path):
        backups = tmp_path / "backups"
        w = BackupWorker([_make_db(tmp_path / "A.db")], KIND_AUTO, backups,
                         rotate_after=True)
        rec = _Recorder(w)
        with patch.object(bdlg, "rotate") as rot:
            w.run()
        rot.assert_called_once_with(backups)
        assert rec.succeeded[0].backup_set.kind == KIND_AUTO

    def test_rotation_failure_does_not_fail_the_backup(self, qapp, tmp_path):
        w = BackupWorker([_make_db(tmp_path / "A.db")], KIND_AUTO, tmp_path / "b",
                         rotate_after=True)
        rec = _Recorder(w)
        with patch.object(bdlg, "rotate", side_effect=OSError("locked")):
            w.run()
        assert len(rec.succeeded) == 1 and not rec.failed

    def test_manual_backup_does_not_rotate(self, qapp, tmp_path):
        w = BackupWorker([_make_db(tmp_path / "A.db")], KIND_MANUAL, tmp_path / "b")
        with patch.object(bdlg, "rotate") as rot:
            w.run()
        rot.assert_not_called()


# ── BackupDialog ──────────────────────────────────────────────────────────────

@pytest.fixture
def manager(qapp, tmp_path):
    from opensak.db.manager import get_db_manager
    m = get_db_manager()
    with patch("opensak.db.database.init_db"):
        m.new_database("Trip", tmp_path / "dbs" / "Trip.db")
        m.new_database("Gone", tmp_path / "dbs" / "Gone.db")
    for name in ("Trip",):
        _make_db(tmp_path / "dbs" / f"{name}.db")
    (tmp_path / "dbs" / "Gone.db").unlink(missing_ok=True)
    return m


@pytest.fixture
def boxes():
    with patch.object(bdlg, "QMessageBox") as box:
        yield box


@pytest.fixture
def sync_worker(monkeypatch):
    """Run the dialog's worker on the calling thread."""
    monkeypatch.setattr(BackupWorker, "start", lambda self: self.run())


@pytest.fixture
def dlg(qtbot, manager, boxes):
    d = BackupDialog()
    qtbot.addWidget(d)
    return d


def _items(d: BackupDialog) -> dict[str, object]:
    return {d._db_list.item(i).data(Qt.ItemDataRole.UserRole).name: d._db_list.item(i)
            for i in range(d._db_list.count())}


class TestBackupDialog:
    def test_lists_databases_checked_and_greys_out_missing_files(self, dlg):
        items = _items(dlg)
        assert items["Trip"].checkState() == Qt.CheckState.Checked
        gone = items["Gone"]
        assert gone.checkState() == Qt.CheckState.Unchecked
        assert not (gone.flags() & Qt.ItemFlag.ItemIsEnabled)
        assert gone.text() == tr("backup_missing_file", name="Gone")
        assert "Gone" not in [db.name for db in dlg.selected_databases()]

    def test_starts_with_the_backup_folder(self, dlg):
        assert dlg.folder() == backupset.get_backup_dir()

    def test_unsuitable_folder_is_refused_with_a_reason(self, dlg, boxes):
        before = dlg.folder()
        assert dlg.choose_folder(get_install_dir() / "backups") is False
        assert dlg.folder() == before
        boxes.warning.assert_called_once()
        assert boxes.warning.call_args.args[2] == tr("backup_folder_invalid")

    def test_suitable_folder_is_used(self, dlg, tmp_path):
        assert dlg.choose_folder(tmp_path / "usb") is True
        assert dlg.folder() == tmp_path / "usb"
        assert dlg._folder_edit.text() == str(tmp_path / "usb")

    def test_browse_uses_the_chosen_folder(self, dlg, tmp_path):
        with patch.object(bdlg.QFileDialog, "getExistingDirectory",
                          return_value=str(tmp_path / "picked")):
            dlg._browse()
        assert dlg.folder() == tmp_path / "picked"

    def test_nothing_selected(self, dlg, boxes):
        for item in _items(dlg).values():
            item.setCheckState(Qt.CheckState.Unchecked)
        dlg._start()
        assert boxes.warning.call_args.args[2] == tr("backup_none_selected")
        assert not dlg.is_running()

    def test_backup_writes_a_manual_set_and_remembers_the_folder(
        self, dlg, tmp_path, sync_worker, qtbot
    ):
        folder = tmp_path / "usb"
        dlg.choose_folder(folder)
        with qtbot.waitSignal(dlg.backup_made, timeout=5000) as made:
            dlg._start()
        result = made.args[0]
        assert result.backup_set.kind == KIND_MANUAL
        assert result.backup_set.path.parent == folder
        assert [s.path for s in list_backup_sets(folder)] == [result.backup_set.path]
        assert get_store().get(BACKUP_DIR_KEY) == str(folder)
        assert dlg.result() == BackupDialog.DialogCode.Accepted
        assert not dlg.is_running()

    def test_success_message_names_the_folder(self, dlg, tmp_path, sync_worker, boxes):
        dlg.choose_folder(tmp_path / "usb")
        dlg._start()
        made = list_backup_sets(tmp_path / "usb")[0]
        box = boxes.return_value
        assert box.setText.call_args.args[0] == tr(
            "backup_done_msg", path=str(made.path))
        box.setWindowTitle.assert_called_once_with(tr("backup_done_title"))

    def test_no_space_message(self, dlg, tmp_path, sync_worker, boxes):
        dlg.choose_folder(tmp_path / "usb")
        with patch.object(bdlg, "write_backup_set",
                          side_effect=NotEnoughSpaceError("Not enough free space")):
            dlg._start()
        title, text = boxes.critical.call_args.args[1:3]
        assert title == tr("backup_failed_title")
        assert text == tr("backup_no_space", path=str(tmp_path / "usb"),
                          error="Not enough free space")
        assert dlg.result() != BackupDialog.DialogCode.Accepted
        assert not dlg.is_running()

    def test_failure_message(self, dlg, tmp_path, sync_worker, boxes):
        dlg.choose_folder(tmp_path / "usb")
        with patch.object(bdlg, "write_backup_set", side_effect=BackupError("boom")):
            dlg._start()
        assert boxes.critical.call_args.args[2] == tr(
            "backup_failed_msg", path=str(tmp_path / "usb"), error="boom")

    def test_cancelled_message(self, dlg, tmp_path, sync_worker, boxes):
        dlg.choose_folder(tmp_path / "usb")
        with patch.object(BackupWorker, "run",
                          lambda self: self.cancelled.emit()):
            dlg._start()
        assert boxes.information.call_args.args[2] == tr("backup_cancelled_msg")
        assert list_backup_sets(tmp_path / "usb") == []

    def test_closing_while_running_cancels_instead(self, dlg):
        worker = MagicMock()
        dlg._worker = worker
        with patch.object(bdlg.QDialog, "reject") as close:
            dlg.reject()
        worker.request_cancel.assert_called_once()
        close.assert_not_called()
        dlg._worker = None

    def test_closing_when_idle_closes(self, dlg):
        with patch.object(bdlg.QDialog, "reject") as close:
            dlg.reject()
        close.assert_called_once()


class TestRealThread:
    def test_backup_on_a_real_worker_thread(self, dlg, tmp_path, qtbot):
        # The other dialog tests run the worker synchronously; this one
        # checks the signals really cross from the worker thread.
        dlg.choose_folder(tmp_path / "usb")
        with qtbot.waitSignal(dlg.backup_made, timeout=10000) as made:
            dlg._start()
        assert made.args[0].backup_set.path.parent == tmp_path / "usb"
        assert not dlg.is_running()

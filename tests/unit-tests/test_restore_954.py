"""
tests/unit-tests/test_restore_954.py — restore databases from a backup
(issue #954, part of #942).

Uses a real DatabaseManager (isolated per test by the root conftest) and
real backup sets written by the backup core (#952).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt

from opensak.backup import restore as restore_mod
from opensak.backup.backupset import (
    KIND_AUTO,
    KIND_MANUAL,
    list_backup_sets,
    read_backup_set,
    write_backup_set,
)
from opensak.backup.restore import (
    RestoreError,
    is_newer_than_this_version,
    restore_database,
    restore_missing_filter_profiles,
)
from opensak.config import get_app_data_dir
from opensak.db.database import SCHEMA_VERSION, dispose_engine
from opensak.db.db_settings import read_file
from opensak.gui.dialogs import restore_dialog as rdlg
from opensak.gui.dialogs.restore_dialog import RestoreDialog, RestoreWorker
from opensak.lang import load_language, tr

TODAY = date(2026, 10, 1)


@pytest.fixture(autouse=True)
def english():
    load_language("en")


@pytest.fixture
def manager(qapp, tmp_path):
    from opensak.db.manager import get_db_manager
    m = get_db_manager()
    m.new_database("Trip", tmp_path / "dbs" / "Trip.db")
    dispose_engine(tmp_path / "dbs" / "Trip.db")
    _add_rows(tmp_path / "dbs" / "Trip.db", 30)
    yield m
    dispose_engine()


def _add_rows(path: Path, n: int) -> None:
    """Put *n* recognisable rows in a small probe table of a real database."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE probe (n INTEGER)")
    conn.executemany("INSERT INTO probe VALUES (?)", [(i,) for i in range(n)])
    conn.commit()
    conn.close()


def _count(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT count(*) FROM probe").fetchone()[0]
    finally:
        conn.close()


def _db(manager, name: str):
    return next(db for db in manager.databases if db.name == name)


def _restored_files() -> list[str]:
    """Files of restored databases (and their sidecars) in the database folder."""
    from opensak.settings_store import get_db_dir
    return sorted(p.name for p in get_db_dir().iterdir() if "restored" in p.name)


def _profiles_dir() -> Path:
    return get_app_data_dir() / "filters"


@pytest.fixture
def backup(manager, tmp_path):
    """A manual backup of Trip, with two filter profiles in its settings."""
    _profiles_dir().mkdir(parents=True, exist_ok=True)
    (_profiles_dir() / "Unfound.json").write_text('{"v": 1}', encoding="utf-8")
    (_profiles_dir() / "Nearby.json").write_text('{"v": 1}', encoding="utf-8")
    return write_backup_set([_db(manager, "Trip")], KIND_MANUAL,
                            folder=tmp_path / "backups").backup_set


def _set_schema(backup_set, version: int):
    """Make the backed-up database (and its manifest) claim *version*."""
    path = backup_set.path / backup_set.databases[0].file
    conn = sqlite3.connect(str(path))
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    conn.close()
    manifest = backup_set.path / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["databases"][0]["schema_version"] = version
    manifest.write_text(json.dumps(data), encoding="utf-8")
    return read_backup_set(backup_set.path)


# ── Core: restore_database ────────────────────────────────────────────────────

class TestRestoreDatabase:
    def test_adds_a_new_database_and_leaves_the_original(self, manager, backup):
        original = _db(manager, "Trip")
        before = len(manager.databases)
        info = restore_database(backup, backup.databases[0], today=TODAY)

        assert info.name == "Trip (restored 2026-10-01)"
        assert _restored_files() == [info.path.name]
        assert len(manager.databases) == before + 1
        assert info.path != original.path and info.path.is_file()
        assert _count(info.path) == 30
        assert _count(original.path) == 30

    def test_keeps_db_uuid_and_own_settings(self, manager, backup):
        info = restore_database(backup, backup.databases[0], today=TODAY)
        restored = read_file(info.path)
        assert restored["db_uuid"] == backup.databases[0].db_uuid
        assert restored["db_uuid"] == read_file(_db(manager, "Trip").path)["db_uuid"]

    def test_restoring_twice_gives_two_databases(self, manager, backup):
        first = restore_database(backup, backup.databases[0], today=TODAY)
        second = restore_database(backup, backup.databases[0], today=TODAY)
        assert second.name == "Trip (restored 2026-10-01) (2)"
        assert second.path != first.path

    def test_never_overwrites_an_existing_file(self, manager, backup):
        from opensak.db.manager import DatabaseManager
        squatter = DatabaseManager.default_path_for("Trip (restored 2026-10-01)")
        squatter.write_bytes(b"not mine to overwrite")
        info = restore_database(backup, backup.databases[0], today=TODAY)
        assert info.path != squatter
        assert squatter.read_bytes() == b"not mine to overwrite"

    def test_newer_schema_is_refused(self, manager, backup):
        newer = _set_schema(backup, SCHEMA_VERSION + 1)
        before = list(manager.databases)
        with pytest.raises(RestoreError, match="newer database format"):
            restore_database(newer, newer.databases[0], today=TODAY)
        assert manager.databases == before

    def test_damaged_database_is_refused_and_leaves_nothing(self, manager, backup):
        path = backup.path / backup.databases[0].file
        data = bytearray(path.read_bytes())
        data[4096:8192] = b"\xff" * 4096        # wreck page 2
        path.write_bytes(bytes(data))
        before = list(manager.databases)

        with pytest.raises(RestoreError):
            restore_database(backup, backup.databases[0], today=TODAY)
        assert manager.databases == before
        assert _restored_files() == []

    def test_missing_file_in_the_set(self, manager, backup):
        (backup.path / backup.databases[0].file).unlink()
        with pytest.raises(RestoreError, match="missing"):
            restore_database(backup, backup.databases[0], today=TODAY)

    def test_file_outside_the_set_is_refused(self, manager, backup):
        from dataclasses import replace
        evil = replace(backup.databases[0], file="../../dbs/Trip.db")
        with pytest.raises(RestoreError, match="Invalid file name"):
            restore_database(backup, evil, today=TODAY)

    def test_failed_registration_removes_the_copy(self, manager, backup):
        with patch.object(type(manager), "add_existing",
                          side_effect=ValueError("taken")):
            with pytest.raises(ValueError):
                restore_database(backup, backup.databases[0], today=TODAY)
        assert _restored_files() == []

    def test_is_newer_than_this_version(self, backup):
        entry = backup.databases[0]
        assert not is_newer_than_this_version(entry)
        from dataclasses import replace
        assert is_newer_than_this_version(replace(entry, schema_version=SCHEMA_VERSION + 1))
        assert not is_newer_than_this_version(replace(entry, schema_version=None))


# ── Core: filter profiles ─────────────────────────────────────────────────────

class TestFilterProfiles:
    def test_adds_missing_profiles_and_leaves_existing_ones(self, backup):
        (_profiles_dir() / "Unfound.json").unlink()                       # deleted since
        (_profiles_dir() / "Nearby.json").write_text('{"v": 2}', encoding="utf-8")  # changed since
        added = restore_missing_filter_profiles(backup)
        assert added == ["Unfound"]
        assert (_profiles_dir() / "Unfound.json").read_text(encoding="utf-8") == '{"v": 1}'
        assert (_profiles_dir() / "Nearby.json").read_text(encoding="utf-8") == '{"v": 2}'

    def test_nothing_missing(self, backup):
        assert restore_missing_filter_profiles(backup) == []


# ── RestoreWorker ─────────────────────────────────────────────────────────────

class _Recorder:
    def __init__(self, w: RestoreWorker) -> None:
        self.done: list = []
        self.failed: list = []
        self.cancelled = 0
        self.progress: list = []
        w.done.connect(self.done.append)
        w.failed.connect(self.failed.append)
        w.cancelled.connect(self._cancel)
        w.progress.connect(lambda *a: self.progress.append(a))

    def _cancel(self) -> None:
        self.cancelled += 1


class TestRestoreWorker:
    def test_restores_and_reports(self, manager, backup):
        (_profiles_dir() / "Unfound.json").unlink()
        w = RestoreWorker(backup, backup.databases)
        rec = _Recorder(w)
        w.run()
        summary = rec.done[0]
        assert [i.name.startswith("Trip (restored") for i in summary.restored] == [True]
        assert summary.refused == []
        assert summary.profiles_added == ["Unfound"]
        assert rec.progress[-1][2] == pytest.approx(1.0)

    def test_refused_database_does_not_stop_the_others(self, manager, backup, tmp_path):
        other = manager.new_database("Other", tmp_path / "dbs" / "Other.db")
        dispose_engine(other.path)
        two = write_backup_set([_db(manager, "Trip"), other], KIND_MANUAL,
                               folder=tmp_path / "two").backup_set
        conn = sqlite3.connect(str(two.path / two.databases[0].file))
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.commit()
        conn.close()

        w = RestoreWorker(two, two.databases)
        rec = _Recorder(w)
        w.run()
        summary = rec.done[0]
        assert [n for n, _ in summary.refused] == ["Trip"]
        assert [i.name for i in summary.restored] == [
            tr("restore_db_name", name="Other", date=date.today().isoformat())]

    def test_cancel_rolls_back_everything(self, manager, backup, tmp_path):
        other = manager.new_database("Other", tmp_path / "dbs" / "Other.db")
        dispose_engine(other.path)
        two = write_backup_set([_db(manager, "Trip"), other], KIND_MANUAL,
                               folder=tmp_path / "two").backup_set
        before = list(manager.databases)

        w = RestoreWorker(two, two.databases)
        rec = _Recorder(w)
        # Cancel once the second database has started: the first one is
        # already restored by then and must be rolled back.
        w.progress.connect(lambda name, *_: name == "Other" and w.request_cancel())
        w.run()

        assert rec.cancelled == 1 and not rec.done
        assert manager.databases == before
        # Restored copies go into the database folder; none may be left there.
        assert _restored_files() == []

    def test_unexpected_error_rolls_back(self, manager, backup):
        before = list(manager.databases)
        w = RestoreWorker(backup, backup.databases)
        rec = _Recorder(w)
        with patch.object(rdlg, "restore_missing_filter_profiles",
                          side_effect=OSError("disk gone")):
            w.run()
        assert rec.failed == ["disk gone"]
        assert manager.databases == before


# ── RestoreDialog ─────────────────────────────────────────────────────────────

@pytest.fixture
def boxes():
    # Inside this patch, rdlg.QMessageBox *is* the mock, so tests compare the
    # dialog's answer against the mock's own StandardButton.Yes / .No.
    with patch.object(rdlg, "QMessageBox") as box:
        yield box


@pytest.fixture
def sync_worker(monkeypatch):
    monkeypatch.setattr(RestoreWorker, "start", lambda self: self.run())


def _open(qtbot, folder: Path) -> RestoreDialog:
    with patch.object(rdlg, "get_backup_dir", return_value=folder):
        d = RestoreDialog()
    qtbot.addWidget(d)
    return d


class TestRestoreDialog:
    def test_lists_sets_newest_first(self, qtbot, manager, backup, tmp_path, boxes):
        newer = write_backup_set([_db(manager, "Trip")], KIND_AUTO,
                                 folder=tmp_path / "backups").backup_set
        d = _open(qtbot, tmp_path / "backups")
        assert d._set_list.count() == 2
        sets = list_backup_sets(tmp_path / "backups")
        assert d.selected_set() == sets[0]
        assert d._set_list.item(0).toolTip() == str(sets[0].path)
        assert newer.path in {s.path for s in sets}

    def test_empty_folder(self, qtbot, manager, tmp_path, boxes):
        d = _open(qtbot, tmp_path / "nothing-here")
        assert d._set_list.count() == 0
        assert not d._empty_label.isHidden()
        assert not d._start_btn.isEnabled()

    def test_databases_of_the_selected_set_are_ticked(self, qtbot, manager, backup, boxes):
        d = _open(qtbot, backup.path.parent)
        assert [e.name for e in d.selected_entries()] == ["Trip"]

    def test_database_from_a_newer_version_is_greyed_out(
        self, qtbot, manager, backup, boxes
    ):
        _set_schema(backup, SCHEMA_VERSION + 1)
        d = _open(qtbot, backup.path.parent)
        item = d._db_list.item(0)
        assert item.text() == tr("restore_needs_newer", name="Trip")
        assert not (item.flags() & Qt.ItemFlag.ItemIsEnabled)
        assert d.selected_entries() == []

    def test_browse_to_a_single_set(self, qtbot, manager, backup, tmp_path, boxes):
        d = _open(qtbot, tmp_path / "nothing-here")
        with patch.object(rdlg.QFileDialog, "getExistingDirectory",
                          return_value=str(backup.path)):
            d._browse()
        assert d._set_list.count() == 1
        assert d.selected_set().path == backup.path

    def test_nothing_selected(self, qtbot, manager, backup, boxes):
        d = _open(qtbot, backup.path.parent)
        d._db_list.item(0).setCheckState(Qt.CheckState.Unchecked)
        d._start()
        assert boxes.warning.call_args.args[2] == tr("restore_none_selected")

    def test_restore_one_and_switch_to_it(
        self, qtbot, manager, backup, boxes, sync_worker
    ):
        boxes.question.return_value = rdlg.QMessageBox.StandardButton.Yes
        d = _open(qtbot, backup.path.parent)
        switched, added = [], []
        d.database_switched.connect(switched.append)
        d.databases_added.connect(lambda: added.append(True))
        with patch.object(type(manager), "switch_to") as switch:
            d._start()
        assert added == [True]
        assert len(switched) == 1 and switched[0].name.startswith("Trip (restored")
        switch.assert_called_once_with(switched[0])
        assert d.result() == RestoreDialog.DialogCode.Accepted

    def test_restore_one_without_switching(
        self, qtbot, manager, backup, boxes, sync_worker
    ):
        boxes.question.return_value = rdlg.QMessageBox.StandardButton.No
        d = _open(qtbot, backup.path.parent)
        switched = []
        d.database_switched.connect(switched.append)
        with patch.object(type(manager), "switch_to") as switch:
            d._start()
        switch.assert_not_called()
        assert switched == []
        assert any(db.name.startswith("Trip (restored") for db in manager.databases)

    def test_all_refused_is_a_warning(self, qtbot, manager, backup, boxes, sync_worker):
        d = _open(qtbot, backup.path.parent)
        with patch.object(rdlg, "restore_database",
                          side_effect=RestoreError("damaged")):
            d._start()
        title, text = boxes.warning.call_args.args[1:3]
        assert title == tr("restore_failed_title")
        assert "damaged" in text
        assert d.result() != RestoreDialog.DialogCode.Accepted

    def test_cancelled(self, qtbot, manager, backup, boxes, sync_worker):
        d = _open(qtbot, backup.path.parent)
        with patch.object(RestoreWorker, "run", lambda self: self.cancelled.emit()):
            d._start()
        assert boxes.information.call_args.args[2] == tr("restore_cancelled_msg")

    def test_closing_while_running_cancels_instead(self, qtbot, manager, backup, boxes):
        d = _open(qtbot, backup.path.parent)
        worker = type("W", (), {"request_cancel": lambda self: setattr(self, "c", True)})()
        d._worker = worker
        with patch.object(rdlg.QDialog, "reject") as close:
            d.reject()
        assert getattr(worker, "c", False) is True
        close.assert_not_called()
        d._worker = None

    # No real-thread test here on purpose. One was written, but it fails when
    # the whole of test_filter_dialog.py has run earlier in the same process:
    # the worker finishes, yet none of its queued signals reach the GUI thread.
    # It passes on its own and in every smaller group. Real-thread delivery for
    # this worker pattern is covered by test_backup_dialog_953.py; the test
    # pollution is tracked in its own follow-up issue.

"""
tests/unit-tests/test_backup_every_days_1240.py — "Back up automatically every
few days" on exit (#1240).

With this setting the close backs up without asking, but only when something
changed and at least X days (default 5) have passed since the last full
backup. Only a full backup restarts the count; a failed or cancelled one
doesn't. The fixtures follow test_exit_backup_dialog_959.py and
test_backup_dialog_953.py.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from opensak.backup import exit_state as es
from opensak.backup.backupset import BACKUP_DIR_KEY, BackupError, list_backup_sets
from opensak.gui.dialogs import exit_backup_dialog as ebd
from opensak.gui.dialogs.backup_dialog import BackupWorker
from opensak.gui.dialogs.exit_backup_dialog import ExitBackupController, ExitChoice
from opensak.settings_store import get_store

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def _make_db(path: Path) -> SimpleNamespace:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS caches (code TEXT)")
    conn.execute("INSERT INTO caches VALUES ('GC1')")
    conn.commit()
    conn.close()
    return SimpleNamespace(name=path.stem, path=path)


# ── Settings values ───────────────────────────────────────────────────────────

class TestSettingValues:
    def test_every_days_is_an_on_exit_choice(self):
        es.set_on_exit(es.ON_EXIT_EVERY_DAYS)
        assert es.get_on_exit() == es.ON_EXIT_EVERY_DAYS

    def test_default_is_five_days(self):
        assert es.get_every_days() == 5

    def test_store_and_read(self):
        es.set_every_days(12)
        assert es.get_every_days() == 12

    @pytest.mark.parametrize("bad", [0, 366, -1, "7", 2.5, True])
    def test_invalid_value_is_refused(self, bad):
        with pytest.raises(ValueError):
            es.set_every_days(bad)

    @pytest.mark.parametrize("stored", ["7", None, 0, 9999, True])
    def test_invalid_stored_value_reads_as_valid(self, stored):
        get_store().set(es.EVERY_DAYS_KEY, stored)
        assert es.MIN_EVERY_DAYS <= es.get_every_days() <= es.MAX_EVERY_DAYS


class TestDue:
    def test_never_backed_up_is_due(self):
        assert es.get_last_full_backup() is None
        assert es.full_backup_due(NOW) is True

    def test_not_due_before_x_days(self):
        es.record_full_backup(NOW - timedelta(days=4, hours=23))
        assert es.full_backup_due(NOW) is False

    def test_due_after_x_days(self):
        es.record_full_backup(NOW - timedelta(days=5))
        assert es.full_backup_due(NOW) is True

    def test_follows_the_setting(self):
        es.set_every_days(1)
        es.record_full_backup(NOW - timedelta(days=1, minutes=1))
        assert es.full_backup_due(NOW) is True

    def test_a_backup_in_the_future_counts_as_due(self):
        es.record_full_backup(NOW + timedelta(days=30))   # the clock was changed
        assert es.full_backup_due(NOW) is True

    def test_unreadable_time_counts_as_never(self):
        get_store().set(es.LAST_FULL_BACKUP_KEY, "not a date")
        assert es.get_last_full_backup() is None
        assert es.full_backup_due(NOW) is True

    def test_stored_in_utc(self):
        es.record_full_backup(datetime(2026, 11, 11, 13, 0,
                                       tzinfo=timezone(timedelta(hours=1))))
        assert get_store().get(es.LAST_FULL_BACKUP_KEY) == "2026-11-11T12:00:00+00:00"


class TestIsFullBackup:
    def test_all_listed_databases(self, tmp_path, monkeypatch):
        dbs = [_make_db(tmp_path / "A.db"), _make_db(tmp_path / "B.db")]
        monkeypatch.setattr(es, "_listed_databases", lambda: list(dbs))
        assert es.is_full_backup(dbs) is True
        assert es.is_full_backup(dbs[:1]) is False

    def test_a_missing_database_file_does_not_count(self, tmp_path, monkeypatch):
        a = _make_db(tmp_path / "A.db")
        gone = SimpleNamespace(name="Gone", path=tmp_path / "Gone.db")
        monkeypatch.setattr(es, "_listed_databases", lambda: [a, gone])
        assert es.is_full_backup([a]) is True


# ── On exit ───────────────────────────────────────────────────────────────────

class _FakePrompt:
    created: list["_FakePrompt"] = []

    def __init__(self, folder: Path, *, show_dont_ask: bool, parent=None) -> None:
        self._folder = Path(folder)
        self.show_dont_ask = show_dont_ask
        _FakePrompt.created.append(self)

    def exec(self) -> int:
        return 0

    def choice(self) -> ExitChoice:
        return ExitChoice.CANCEL

    def dont_ask_again(self) -> bool:
        return False

    def folder(self) -> Path:
        return self._folder


@pytest.fixture
def env(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(es, "_suppressed", False)
    monkeypatch.setattr(ebd, "_session_ending", lambda: False)
    dbs = [_make_db(tmp_path / "dbs" / "A.db"), _make_db(tmp_path / "dbs" / "B.db")]
    monkeypatch.setattr(es, "_listed_databases", lambda: list(dbs))
    monkeypatch.setattr(ExitBackupController, "_databases", lambda self: list(dbs))
    folder = tmp_path / "backups"
    folder.mkdir()
    get_store().set(BACKUP_DIR_KEY, str(folder))
    monkeypatch.setattr(BackupWorker, "start", lambda self: self.run())
    _FakePrompt.created = []
    monkeypatch.setattr(ebd, "ExitBackupPrompt", _FakePrompt)
    es.set_on_exit(es.ON_EXIT_EVERY_DAYS)
    window = QWidget()
    qtbot.addWidget(window)
    return SimpleNamespace(window=window, dbs=dbs, folder=folder)


def _run(env):
    c = ExitBackupController(env.window)
    done: list[bool] = []
    c.done.connect(done.append)
    return c.start(), done


class TestOnExit:
    def test_first_time_backs_up_without_asking(self, env):
        decision, done = _run(env)
        assert decision is None and done == [True]
        assert _FakePrompt.created == []
        assert len(list_backup_sets(env.folder)) == 1
        assert es.get_last_full_backup() is not None
        assert es._clean_on_close is True

    def test_recent_backup_closes_without_backing_up(self, env):
        es.record_full_backup(datetime.now(timezone.utc) - timedelta(days=2))
        decision, done = _run(env)
        assert decision is True and done == []
        assert _FakePrompt.created == []
        assert list_backup_sets(env.folder) == []
        # Something changed, so the state is not "clean".
        assert es._clean_on_close is False

    def test_old_backup_backs_up_and_restarts_the_count(self, env):
        old = datetime.now(timezone.utc) - timedelta(days=6)
        es.record_full_backup(old)
        decision, done = _run(env)
        assert decision is None and done == [True]
        assert len(list_backup_sets(env.folder)) == 1
        assert es.get_last_full_backup() > old

    def test_nothing_changed_closes_even_when_due(self, env):
        es.record_backup_state()
        decision, _ = _run(env)
        assert decision is True
        assert list_backup_sets(env.folder) == []
        assert es._clean_on_close is True

    def test_unusable_folder_asks(self, env, tmp_path):
        get_store().set(BACKUP_DIR_KEY, str(tmp_path / "usb" / "OpenSAK Backups"))
        decision, _ = _run(env)
        assert decision is False                       # fake prompt: Cancel
        assert _FakePrompt.created[0].show_dont_ask is False

    def test_failed_backup_does_not_restart_the_count(self, env, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.backup_dialog.write_backup_set",
            lambda *a, **k: (_ for _ in ()).throw(BackupError("boom")),
        )
        monkeypatch.setattr(ExitBackupController, "_ask_close_anyway",
                            lambda self, text: True)
        _run(env)
        assert es.get_last_full_backup() is None

    def test_cancelled_backup_does_not_restart_the_count(self, env, monkeypatch):
        original = BackupWorker.run

        def _cancelling_run(self):
            self.request_cancel()
            original(self)

        monkeypatch.setattr(BackupWorker, "start", _cancelling_run)
        _run(env)
        assert es.get_last_full_backup() is None

    def test_ask_and_always_backups_also_restart_the_count(self, env):
        es.set_on_exit(es.ON_EXIT_ALWAYS)
        _run(env)
        assert es.get_last_full_backup() is not None


# ── Backup dialog: only a full backup counts ─────────────────────────────────

@pytest.fixture
def backup_dlg(qtbot, qapp, tmp_path, monkeypatch):
    from opensak.db.manager import get_db_manager
    from opensak.gui.dialogs import backup_dialog as bdlg
    from opensak.gui.dialogs import backup_folder_check
    m = get_db_manager()
    with patch("opensak.db.database.init_db"):
        m.new_database("One", tmp_path / "dbs" / "One.db")
        m.new_database("Two", tmp_path / "dbs" / "Two.db")
    _make_db(tmp_path / "dbs" / "One.db")
    _make_db(tmp_path / "dbs" / "Two.db")
    monkeypatch.setattr(BackupWorker, "start", lambda self: self.run())
    with patch.object(bdlg, "QMessageBox") as box, \
            patch.object(backup_folder_check, "QMessageBox", box):
        d = bdlg.BackupDialog()
        qtbot.addWidget(d)
        d.choose_folder(tmp_path / "usb")
        yield d


def _tick(d, name: str, on: bool) -> None:
    for i in range(d._db_list.count()):
        item = d._db_list.item(i)
        if item.data(Qt.ItemDataRole.UserRole).name == name:
            item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)


class TestBackupDialog:
    def test_backup_of_every_database_restarts_the_count(self, backup_dlg):
        _tick(backup_dlg, "One", True)
        _tick(backup_dlg, "Two", True)
        backup_dlg._start()
        assert es.get_last_full_backup() is not None

    def test_backup_of_one_database_does_not(self, backup_dlg):
        _tick(backup_dlg, "One", True)
        _tick(backup_dlg, "Two", False)
        backup_dlg._start()
        assert len(list_backup_sets(backup_dlg._folder)) == 1
        assert es.get_last_full_backup() is None


# ── Settings → Advanced ───────────────────────────────────────────────────────

@pytest.fixture
def settings_dlg(qtbot, monkeypatch):
    from opensak.gui.dialogs import settings_dialog as sd
    from opensak.gui.settings import AppSettings
    s = AppSettings()
    monkeypatch.setattr(sd, "get_settings", lambda: s)
    monkeypatch.setattr("opensak.gui.settings.get_settings", lambda: s)
    monkeypatch.setattr("opensak.db.manager.get_db_manager",
                        lambda: (_ for _ in ()).throw(RuntimeError("no manager in test")))
    monkeypatch.setattr("opensak.api.geocaching.is_logged_in", lambda: False)

    def _make():
        d = sd.SettingsDialog()
        qtbot.addWidget(d)
        return d
    return _make


def _days_row_visible(d) -> bool:
    return d._backup_form.isRowVisible(d._backup_every_days)


class TestSettingsRow:
    def test_days_hidden_unless_every_days_is_chosen(self, settings_dlg):
        d = settings_dlg()
        assert _days_row_visible(d) is False
        d._backup_on_exit_combo.setCurrentIndex(
            d._backup_on_exit_combo.findData(es.ON_EXIT_EVERY_DAYS))
        assert _days_row_visible(d) is True
        d._backup_on_exit_combo.setCurrentIndex(
            d._backup_on_exit_combo.findData(es.ON_EXIT_NEVER))
        assert _days_row_visible(d) is False

    def test_loads_and_saves(self, settings_dlg):
        es.set_on_exit(es.ON_EXIT_EVERY_DAYS)
        es.set_every_days(9)
        d = settings_dlg()
        assert d._backup_on_exit_combo.currentData() == es.ON_EXIT_EVERY_DAYS
        assert d._backup_every_days.value() == 9
        assert _days_row_visible(d) is True
        d._backup_every_days.setValue(3)
        d._save()
        assert es.get_on_exit() == es.ON_EXIT_EVERY_DAYS
        assert es.get_every_days() == 3

    def test_default_days(self, settings_dlg):
        d = settings_dlg()
        assert d._backup_every_days.value() == 5
        assert d._backup_every_days.minimum() == 1

"""
tests/unit-tests/test_backup_folder_setup_986.py — the backup folder in the
Welcome Wizard and in Settings → Advanced (issue #986, part of #942).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("pytestqt")

from opensak import settings_store as ss
from opensak.backup.backupset import (
    BACKUP_DIR_KEY,
    BackupError,
    default_backup_dir,
    get_backup_dir,
    validate_backup_dir,
)
from opensak.gui.dialogs import settings_dialog as sd
from opensak.gui.dialogs import welcome_wizard as ww
from opensak.lang import load_language, tr


@pytest.fixture(autouse=True)
def _language():
    load_language("en")


# ── validate_backup_dir with folders that aren't saved yet ───────────────────

class TestValidateWithChosenFolders:
    def test_inside_a_chosen_database_folder_is_refused(self, tmp_path):
        db = tmp_path / "new_db"
        with pytest.raises(BackupError, match="database folder"):
            validate_backup_dir(db / "backups", db_dir=db)

    def test_inside_a_chosen_install_folder_is_refused(self, tmp_path):
        install = tmp_path / "new_install"
        with pytest.raises(BackupError, match="install folder"):
            validate_backup_dir(install, install_dir=install)

    def test_outside_both_chosen_folders_is_accepted(self, tmp_path):
        validate_backup_dir(
            tmp_path / "usb",
            install_dir=tmp_path / "install",
            db_dir=tmp_path / "db",
        )

    def test_without_arguments_the_folders_in_use_are_checked(self):
        with pytest.raises(BackupError):
            validate_backup_dir(ss.get_db_dir() / "backups")


# ── Welcome Wizard ────────────────────────────────────────────────────────────

@pytest.fixture
def install_dir(tmp_path, monkeypatch):
    """A fresh install folder, with bootstrap.json kept in tmp_path."""
    bootstrap = tmp_path / "bootstrap.json"
    monkeypatch.setattr(ss, "_bootstrap_path", lambda: bootstrap)
    monkeypatch.setattr(ss, "_store", None)
    folder = tmp_path / "install"
    folder.mkdir()
    ss.set_install_dir(folder)
    return folder


@pytest.fixture
def wizard(qtbot, install_dir):
    w = ww.WelcomeWizard()
    qtbot.addWidget(w)
    return w


@pytest.fixture
def warnings(monkeypatch):
    warn = MagicMock()
    monkeypatch.setattr(ww.QMessageBox, "warning", warn)
    return warn


def _go_to(w: ww.WelcomeWizard, index: int) -> None:
    w._stack.setCurrentIndex(index)
    w._update_buttons()


class TestWizardPage:
    def test_backup_page_comes_right_after_the_database_folder_page(self, wizard):
        assert wizard._total == 6
        assert wizard._backup_page_index == 3
        assert wizard._stack.indexOf(wizard._db_row.parentWidget()) == 2
        assert wizard._stack.indexOf(wizard._backup_row.parentWidget()) == 3

    def test_suggests_the_default_folder(self, wizard):
        assert wizard._backup_row.path == default_backup_dir()

    def test_run_again_shows_the_folder_in_use(self, qtbot, install_dir, tmp_path):
        ss.get_store().set(BACKUP_DIR_KEY, str(tmp_path / "usb"))
        w = ww.WelcomeWizard()
        qtbot.addWidget(w)
        assert w._backup_row.path == tmp_path / "usb"

    def test_next_with_a_good_folder_moves_on(self, wizard, warnings, tmp_path):
        _go_to(wizard, wizard._backup_page_index)
        wizard._backup_row.set_path(tmp_path / "usb")
        wizard._go_next()
        warnings.assert_not_called()
        assert wizard._current == wizard._backup_page_index + 1

    def test_next_with_a_folder_inside_the_chosen_database_folder_stays(
        self, wizard, warnings, tmp_path
    ):
        # Checked against the database folder chosen on the page before,
        # which isn't saved yet.
        wizard._db_row.set_path(tmp_path / "my_dbs")
        _go_to(wizard, wizard._backup_page_index)
        wizard._backup_row.set_path(tmp_path / "my_dbs" / "backups")
        wizard._go_next()
        assert warnings.call_args.args[2] == tr("backup_folder_invalid")
        assert wizard._current == wizard._backup_page_index

    def test_finish_checks_again_after_going_back(
        self, wizard, warnings, tmp_path, monkeypatch
    ):
        # Backup page passed, then the database folder changed so that it
        # now contains the backup folder.
        wizard._backup_row.set_path(tmp_path / "data" / "backups")
        wizard._db_row.set_path(tmp_path / "data")
        saved = []
        monkeypatch.setattr(wizard, "_save_all", lambda **k: saved.append(k))
        _go_to(wizard, wizard._total - 1)
        wizard._go_next()
        assert saved == []
        warnings.assert_called_once()
        assert wizard._current == wizard._backup_page_index
        assert wizard.result() != ww.QDialog.DialogCode.Accepted


class TestWizardSave:
    def test_finish_stores_the_chosen_folder(self, wizard, tmp_path):
        wizard._backup_row.set_path(tmp_path / "usb")
        wizard._finish()
        assert ss.get_store().get(BACKUP_DIR_KEY) == str(tmp_path / "usb")
        assert get_backup_dir() == tmp_path / "usb"

    def test_finish_does_not_create_the_folder(self, wizard, tmp_path):
        wizard._backup_row.set_path(tmp_path / "usb")
        wizard._finish()
        assert not (tmp_path / "usb").exists()

    def test_skip_leaves_the_folder_unset(self, wizard, tmp_path):
        wizard._backup_row.set_path(tmp_path / "usb")
        wizard._skip()
        assert ss.get_store().get(BACKUP_DIR_KEY) is None
        assert get_backup_dir() == default_backup_dir()


# ── Settings → Advanced ───────────────────────────────────────────────────────

@pytest.fixture
def settings_dlg(qtbot, monkeypatch):
    from opensak.gui.settings import AppSettings
    s = AppSettings()
    monkeypatch.setattr(sd, "get_settings", lambda: s)
    monkeypatch.setattr("opensak.gui.settings.get_settings", lambda: s)
    monkeypatch.setattr("opensak.api.geocaching.is_logged_in", lambda: False)
    d = sd.SettingsDialog()
    qtbot.addWidget(d)
    return d


class TestSettingsRow:
    def test_shows_the_folder_in_use(self, settings_dlg):
        assert settings_dlg._backup_dir_row.path == get_backup_dir()

    def test_load_refreshes_the_row(self, settings_dlg, tmp_path):
        ss.get_store().set(BACKUP_DIR_KEY, str(tmp_path / "usb"))
        settings_dlg._load()
        assert settings_dlg._backup_dir_row.path == tmp_path / "usb"

    def test_saving_a_new_folder_stores_it(self, settings_dlg, tmp_path):
        settings_dlg._backup_dir_row.set_path(tmp_path / "usb")
        settings_dlg._save()
        assert ss.get_store().get(BACKUP_DIR_KEY) == str(tmp_path / "usb")
        assert settings_dlg.result() == sd.QDialog.DialogCode.Accepted

    def test_saving_other_settings_does_not_freeze_the_default(self, settings_dlg):
        settings_dlg._save()
        assert ss.get_store().get(BACKUP_DIR_KEY) is None

    def test_unsuitable_folder_keeps_the_dialog_open_and_saves_nothing(
        self, settings_dlg, monkeypatch
    ):
        warn = MagicMock()
        monkeypatch.setattr(sd.QMessageBox, "warning", warn)
        accept = MagicMock()
        monkeypatch.setattr(settings_dlg, "accept", accept)
        settings_dlg._backup_dir_row.set_path(ss.get_db_dir() / "backups")
        settings_dlg._save()
        assert warn.call_args.args[2] == tr("backup_folder_invalid")
        accept.assert_not_called()
        assert ss.get_store().get(BACKUP_DIR_KEY) is None

    def test_checked_against_a_new_database_folder_too(
        self, settings_dlg, monkeypatch, tmp_path
    ):
        warn = MagicMock()
        monkeypatch.setattr(sd.QMessageBox, "warning", warn)
        monkeypatch.setattr(settings_dlg, "accept", MagicMock())
        settings_dlg._backup_dir_row.set_path(tmp_path / "data" / "backups")
        settings_dlg._db_dir_row.set_path(tmp_path / "data")
        settings_dlg._save()
        warn.assert_called_once()
        assert ss.get_store().get("databases.dir") != str(tmp_path / "data")

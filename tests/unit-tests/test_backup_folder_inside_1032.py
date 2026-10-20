# tests/unit-tests/test_backup_folder_inside_1032.py — backup folder inside OpenSAK's folders (#1032).
#
# A folder inside the install or database folder used to be refused. It is
# now allowed after a warning; only the folders themselves, the settings
# folders a backup copies and OpenSAK's working folders are still refused.
# Browse opens at the nearest existing folder, and "remove all data" says
# when it would delete the backup folder.

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("pytestqt")

from opensak import settings_store as ss
from opensak.backup.backupset import (
    BACKUP_DIR_KEY,
    BackupError,
    backup_dir_inside_opensak,
    backup_dir_removed_on_purge,
    nearest_existing_dir,
    validate_backup_dir,
)
from opensak.gui.dialogs import backup_folder_check as bfc
from opensak.lang import load_language, tr


@pytest.fixture(autouse=True)
def _english():
    load_language("en")


@pytest.fixture
def folders(tmp_path):
    return tmp_path / "install", tmp_path / "db"


# ── Core: refused / warned / fine ─────────────────────────────────────────────

REFUSED = [
    "install", "db",
    "install/filters", "install/filters/sub", "install/column_views", "install/icons",
    "install/backup-staging", "install/backup-staging/x",
    "install/settings-restore-pending", "install/settings-restore-failed/x",
]
WARNED = [
    ("install/backups", "install"), ("install/my/deep/backups", "install"),
    ("db/backups", "db"), ("db/OpenSAK Backups", "db"),
]
FINE = ["usb", "usb/OpenSAK Backups", "install-backups", "dbx"]


def _p(tmp_path: Path, rel: str) -> Path:
    return tmp_path.joinpath(*rel.split("/"))


class TestCoreSplit:
    @pytest.mark.parametrize("rel", REFUSED)
    def test_refused(self, tmp_path, folders, rel):
        install, db = folders
        with pytest.raises(BackupError):
            validate_backup_dir(_p(tmp_path, rel), install_dir=install, db_dir=db)

    @pytest.mark.parametrize("rel, root", WARNED)
    def test_allowed_but_inside(self, tmp_path, folders, rel, root):
        install, db = folders
        validate_backup_dir(_p(tmp_path, rel), install_dir=install, db_dir=db)
        assert backup_dir_inside_opensak(
            _p(tmp_path, rel), install_dir=install, db_dir=db
        ) == tmp_path / root

    @pytest.mark.parametrize("rel", FINE)
    def test_fine_and_not_inside(self, tmp_path, folders, rel):
        # "install-backups" and "dbx" share a prefix with the folders but
        # are not inside them.
        install, db = folders
        validate_backup_dir(_p(tmp_path, rel), install_dir=install, db_dir=db)
        assert backup_dir_inside_opensak(
            _p(tmp_path, rel), install_dir=install, db_dir=db
        ) is None

    def test_folders_themselves_are_not_reported_as_inside(self, folders):
        install, db = folders
        assert backup_dir_inside_opensak(install, install_dir=install, db_dir=db) is None

    def test_database_folder_inside_install_folder(self, tmp_path):
        # The default layout: the database folder is the install folder.
        install = tmp_path / "install"
        validate_backup_dir(install / "backups", install_dir=install, db_dir=install)
        with pytest.raises(BackupError):
            validate_backup_dir(install, install_dir=install, db_dir=install)

    def test_defaults_to_the_folders_in_use(self):
        with pytest.raises(BackupError):
            validate_backup_dir(ss.get_install_dir())
        assert backup_dir_inside_opensak(ss.get_install_dir() / "backups") is not None


# ── Browse start folder ───────────────────────────────────────────────────────

class TestNearestExistingDir:
    def test_existing_folder_is_used(self, tmp_path):
        assert nearest_existing_dir(tmp_path) == tmp_path

    def test_missing_folder_gives_nearest_parent(self, tmp_path):
        (tmp_path / "a").mkdir()
        assert nearest_existing_dir(tmp_path / "a" / "b" / "c") == tmp_path / "a"

    def test_a_file_is_not_a_folder(self, tmp_path):
        (tmp_path / "f.txt").write_text("x")
        assert nearest_existing_dir(tmp_path / "f.txt") == tmp_path

    def test_none_gives_home(self):
        assert nearest_existing_dir(None) == Path.home()

    def test_no_existing_parent_gives_home(self, monkeypatch):
        monkeypatch.setattr(Path, "is_dir", lambda self: False)
        assert nearest_existing_dir(Path("/nowhere/at/all")) == Path.home()

    def test_dir_row_browse_starts_at_nearest_parent(self, qtbot, tmp_path):
        from opensak.gui.dialogs import widgets as w
        row = w.DirRow(tmp_path / "not" / "yet")
        qtbot.addWidget(row)
        with patch.object(w.QFileDialog, "getExistingDirectory", return_value="") as dlg:
            row._browse()
        assert dlg.call_args.args[2] == str(tmp_path)

    def test_exit_prompt_browse_starts_at_nearest_parent(self, qtbot, tmp_path):
        from opensak.gui.dialogs import exit_backup_dialog as ebd
        (tmp_path / "media").mkdir()
        p = ebd.ExitBackupPrompt(tmp_path / "media" / "USB" / "OpenSAK", show_dont_ask=True)
        qtbot.addWidget(p)
        with patch.object(ebd.QFileDialog, "getExistingDirectory", return_value="") as dlg:
            p._browse()
        assert dlg.call_args.args[2] == str(tmp_path / "media")


# ── The shared confirmation ───────────────────────────────────────────────────

@pytest.fixture
def ui(monkeypatch):
    """The refusal box and the Use/Choose-another question, recorded."""
    warn = MagicMock()
    monkeypatch.setattr(bfc.QMessageBox, "warning", warn)
    ask = MagicMock(return_value=True)
    monkeypatch.setattr(bfc, "_ask_use_inside", ask)
    return warn, ask


class TestConfirmBackupFolder:
    def test_refused_folder_explains_and_returns_false(self, ui, folders):
        warn, ask = ui
        install, db = folders
        assert not bfc.confirm_backup_folder(
            None, install, title="T", install_dir=install, db_dir=db)
        assert warn.call_args.args[2] == tr("backup_folder_invalid")
        ask.assert_not_called()

    def test_folder_elsewhere_needs_no_question(self, ui, folders, tmp_path):
        warn, ask = ui
        install, db = folders
        assert bfc.confirm_backup_folder(
            None, tmp_path / "usb", title="T", install_dir=install, db_dir=db)
        warn.assert_not_called()
        ask.assert_not_called()

    def test_inside_asks_and_use_accepts(self, ui, folders):
        warn, ask = ui
        install, db = folders
        assert bfc.confirm_backup_folder(
            None, db / "backups", title="T", install_dir=install, db_dir=db)
        assert ask.call_args.args[2:] == (db / "backups", db)
        warn.assert_not_called()

    def test_inside_choose_another_refuses_and_remembers_nothing(self, ui, folders):
        _, ask = ui
        ask.return_value = False
        install, db = folders
        assert not bfc.confirm_backup_folder(
            None, db / "backups", title="T", install_dir=install, db_dir=db)
        assert ss.get_store().get(bfc.CONFIRMED_KEY) is None

    def test_asked_only_once_per_folder(self, ui, folders):
        _, ask = ui
        install, db = folders
        for _ in range(3):
            assert bfc.confirm_backup_folder(
                None, db / "backups", title="T", install_dir=install, db_dir=db)
        assert ask.call_count == 1
        # A different folder is asked about again.
        bfc.confirm_backup_folder(
            None, install / "backups", title="T", install_dir=install, db_dir=db)
        assert ask.call_count == 2

    def test_question_text_and_buttons(self, monkeypatch, folders):
        install, db = folders
        seen = {}

        def fake_exec(box):
            seen["text"] = box.text()
            seen["buttons"] = [b.text() for b in box.buttons()]
            seen["default"] = box.defaultButton().text()
            return 0

        monkeypatch.setattr(bfc.QMessageBox, "exec", fake_exec)
        monkeypatch.setattr(
            bfc.QMessageBox, "clickedButton",
            lambda box: next(b for b in box.buttons()
                             if b.text() == tr("backup_folder_use_anyway")),
        )
        assert bfc.confirm_backup_folder(
            None, db / "backups", title="T", install_dir=install, db_dir=db)
        assert seen["text"] == tr("backup_folder_inside_warn",
                                  path=str(db / "backups"), folder=str(db))
        assert set(seen["buttons"]) == {tr("backup_folder_use_anyway"),
                                        tr("backup_folder_choose_another")}
        # The safe choice is the default (Enter).
        assert seen["default"] == tr("backup_folder_choose_another")


class TestDialogsUseTheConfirmation:
    def test_exit_prompt_inside_folder_after_yes(self, qtbot, ui, tmp_path):
        from opensak.gui.dialogs.exit_backup_dialog import ExitBackupPrompt
        _, ask = ui
        p = ExitBackupPrompt(tmp_path / "b", show_dont_ask=True)
        qtbot.addWidget(p)
        target = ss.get_install_dir() / "backups"
        assert p.choose_folder(target) is True
        assert p.folder() == target
        ask.assert_called_once()

    def test_exit_prompt_inside_folder_choose_another(self, qtbot, ui, tmp_path):
        from opensak.gui.dialogs.exit_backup_dialog import ExitBackupPrompt
        _, ask = ui
        ask.return_value = False
        p = ExitBackupPrompt(tmp_path / "b", show_dont_ask=True)
        qtbot.addWidget(p)
        assert p.choose_folder(ss.get_install_dir() / "backups") is False
        assert p.folder() == tmp_path / "b"


# ── "Remove all data" names a backup folder it would delete ───────────────────

class TestPurgeNote:
    def test_unset_backup_folder_is_never_reported(self):
        assert backup_dir_removed_on_purge() is None

    def test_backup_folder_elsewhere_is_not_reported(self, tmp_path):
        ss.get_store().set(BACKUP_DIR_KEY, str(tmp_path / "usb"))
        assert backup_dir_removed_on_purge() is None

    def test_backup_folder_inside_install_folder_is_reported(self):
        inside = ss.get_install_dir() / "backups"
        ss.get_store().set(BACKUP_DIR_KEY, str(inside))
        assert backup_dir_removed_on_purge() == inside

    @pytest.mark.parametrize("inside", [True, False])
    def test_confirmation_text(self, monkeypatch, tmp_path, inside):
        from opensak.gui.dialogs import uninstall_choice_dialog as ucd
        folder = ss.get_install_dir() / "backups" if inside else tmp_path / "usb"
        ss.get_store().set(BACKUP_DIR_KEY, str(folder))
        box_cls = MagicMock()
        program_only, purge = object(), object()
        box = box_cls.return_value
        box.addButton.side_effect = [program_only, purge, object()]
        box.clickedButton.return_value = purge
        monkeypatch.setattr(ucd, "QMessageBox", box_cls)
        ucd.ask_uninstall_choice(None)
        text = box_cls.warning.call_args.args[2]
        note = tr("appimage_uninstall_purge_backup_note", path=str(folder))
        assert text.startswith(tr("appimage_uninstall_purge_confirm_msg"))
        assert (note in text) is inside

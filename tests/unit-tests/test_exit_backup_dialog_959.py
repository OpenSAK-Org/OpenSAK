"""
tests/unit-tests/test_exit_backup_dialog_959.py — back up on exit, UI (#959).

The prompt, the close-time controller and the Settings → Advanced rows. The
controller's worker runs synchronously and its prompt / message boxes are
replaced, so nothing blocks. The core (change detection) is tested in
test_backup_exit_state_959.py; the MainWindow wiring in
tests/e2e-tests/test_e2e_exit_backup.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QWidget

from opensak.backup import exit_state as es
from opensak.backup.backupset import (
    BACKUP_DIR_KEY,
    KEEP_AUTO_KEY,
    KIND_AUTO,
    BackupError,
    NotEnoughSpaceError,
    list_backup_sets,
)
from opensak.gui.dialogs import exit_backup_dialog as ebd
from opensak.gui.dialogs.backup_dialog import BackupWorker
from opensak.gui.dialogs.exit_backup_dialog import (
    ExitBackupController,
    ExitBackupPrompt,
    ExitChoice,
    folder_available,
)
from opensak.lang import tr
from opensak.settings_store import get_install_dir, get_store


def _make_db(path: Path) -> SimpleNamespace:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS caches (code TEXT)")
    conn.execute("INSERT INTO caches VALUES ('GC1')")
    conn.commit()
    conn.close()
    return SimpleNamespace(name=path.stem, path=path)


# ── folder_available ──────────────────────────────────────────────────────────

class TestFolderAvailable:
    def test_existing_folder(self, tmp_path):
        assert folder_available(tmp_path) is True

    def test_default_folder_even_without_documents(self):
        from opensak.backup.backupset import default_backup_dir
        assert not default_backup_dir().parent.exists()   # fake home: no Documents
        assert folder_available(default_backup_dir()) is True

    def test_disconnected_drive(self, tmp_path):
        assert folder_available(tmp_path / "usb-not-mounted" / "OpenSAK Backups") is False

    def test_missing_mount_point_whose_parent_exists(self, tmp_path):
        """E.g. /Volumes/USB when the drive isn't connected: /Volumes exists."""
        assert folder_available(tmp_path / "USB") is False

    def test_a_file_is_not_a_folder(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x", encoding="utf-8")
        assert folder_available(f) is False


# ── Prompt ────────────────────────────────────────────────────────────────────

@pytest.fixture
def boxes():
    with patch.object(ebd, "QMessageBox") as box:
        yield box


class TestPrompt:
    @pytest.fixture(autouse=True)
    def _folders(self, tmp_path):
        (tmp_path / "b").mkdir()
        (tmp_path / "other").mkdir()
        (tmp_path / "picked").mkdir()

    def test_buttons_set_the_choice(self, qtbot, tmp_path):
        for button, expected in (
            ("_backup_btn", ExitChoice.BACK_UP),
            ("_not_now_btn", ExitChoice.NOT_NOW),
            ("_cancel_btn", ExitChoice.CANCEL),
        ):
            p = ExitBackupPrompt(tmp_path / "b", show_dont_ask=True)
            qtbot.addWidget(p)
            getattr(p, button).click()
            assert p.choice() is expected

    def test_closing_the_prompt_is_cancel(self, qtbot, tmp_path):
        p = ExitBackupPrompt(tmp_path / "b", show_dont_ask=True)
        qtbot.addWidget(p)
        p.reject()
        assert p.choice() is ExitChoice.CANCEL

    def test_dont_ask_again(self, qtbot, tmp_path):
        p = ExitBackupPrompt(tmp_path / "b", show_dont_ask=True)
        qtbot.addWidget(p)
        assert p.dont_ask_again() is False
        p._dont_ask.setChecked(True)
        assert p.dont_ask_again() is True

    def test_dont_ask_hidden_when_not_asking(self, qtbot, tmp_path):
        p = ExitBackupPrompt(tmp_path / "b", show_dont_ask=False)
        qtbot.addWidget(p)
        assert p._dont_ask is None and p.dont_ask_again() is False

    def test_available_folder(self, qtbot, tmp_path):
        p = ExitBackupPrompt(tmp_path / "b", show_dont_ask=True)
        qtbot.addWidget(p)
        assert p._backup_btn.isEnabled()
        assert p._unavailable.isHidden()

    def test_unavailable_folder_disables_back_up_until_another_is_chosen(
        self, qtbot, tmp_path
    ):
        gone = tmp_path / "usb" / "OpenSAK Backups"
        p = ExitBackupPrompt(gone, show_dont_ask=True)
        qtbot.addWidget(p)
        assert not p._backup_btn.isEnabled()
        assert not p._unavailable.isHidden()
        assert p._unavailable.text() == tr(
            "exit_backup_folder_unavailable", path=str(gone))
        assert p.choose_folder(tmp_path / "other") is True
        assert p._backup_btn.isEnabled()
        assert p.folder() == tmp_path / "other"

    def test_unsuitable_folder_is_refused(self, qtbot, tmp_path, boxes):
        p = ExitBackupPrompt(tmp_path / "b", show_dont_ask=True)
        qtbot.addWidget(p)
        assert p.choose_folder(get_install_dir() / "backups") is False
        assert p.folder() == tmp_path / "b"
        assert boxes.warning.call_args.args[2] == tr("backup_folder_invalid")

    def test_browse(self, qtbot, tmp_path):
        p = ExitBackupPrompt(tmp_path / "b", show_dont_ask=True)
        qtbot.addWidget(p)
        with patch.object(ebd.QFileDialog, "getExistingDirectory",
                          return_value=str(tmp_path / "picked")):
            p._browse()
        assert p.folder() == tmp_path / "picked"


# ── Controller ────────────────────────────────────────────────────────────────

class _FakePrompt:
    """Stands in for ExitBackupPrompt; records how it was created."""

    created: list["_FakePrompt"] = []
    answer = ExitChoice.CANCEL
    dont_ask = False
    folder_override: Path | None = None

    def __init__(self, folder: Path, *, show_dont_ask: bool, parent=None) -> None:
        self._folder = Path(folder)
        self.show_dont_ask = show_dont_ask
        _FakePrompt.created.append(self)

    def exec(self) -> int:
        return 0

    def choice(self) -> ExitChoice:
        return _FakePrompt.answer

    def dont_ask_again(self) -> bool:
        return _FakePrompt.dont_ask

    def folder(self) -> Path:
        return _FakePrompt.folder_override or self._folder


@pytest.fixture
def env(qtbot, tmp_path, monkeypatch):
    """Unsuppressed, two databases, a backup folder, sync worker, fake prompt."""
    monkeypatch.setattr(es, "_suppressed", False)
    monkeypatch.setattr(ebd, "_session_ending", lambda: False)
    dbs = [_make_db(tmp_path / "dbs" / "A.db"), _make_db(tmp_path / "dbs" / "B.db")]
    monkeypatch.setattr(es, "_listed_databases", lambda: list(dbs))
    monkeypatch.setattr(ExitBackupController, "_databases", lambda self: list(dbs))
    folder = tmp_path / "backups"
    folder.mkdir()
    (tmp_path / "usb").mkdir()
    get_store().set(BACKUP_DIR_KEY, str(folder))
    monkeypatch.setattr(BackupWorker, "start", lambda self: self.run())
    _FakePrompt.created = []
    _FakePrompt.answer = ExitChoice.CANCEL
    _FakePrompt.dont_ask = False
    _FakePrompt.folder_override = None
    monkeypatch.setattr(ebd, "ExitBackupPrompt", _FakePrompt)
    window = QWidget()
    qtbot.addWidget(window)
    return SimpleNamespace(window=window, dbs=dbs, folder=folder)


def _run(env, qtbot):
    """Start a controller; return (decision, done-values)."""
    c = ExitBackupController(env.window)
    done: list[bool] = []
    c.done.connect(done.append)
    decision = c.start()
    return decision, done


class TestControllerDecision:
    def test_suppressed_closes_without_asking(self, env, qtbot, monkeypatch):
        monkeypatch.setattr(es, "_suppressed", True)
        assert _run(env, qtbot)[0] is True
        assert _FakePrompt.created == []

    def test_session_ending_closes_without_asking(self, env, qtbot, monkeypatch):
        monkeypatch.setattr(ebd, "_session_ending", lambda: True)
        assert _run(env, qtbot)[0] is True
        assert _FakePrompt.created == []

    def test_never(self, env, qtbot):
        es.set_on_exit(es.ON_EXIT_NEVER)
        assert _run(env, qtbot)[0] is True
        assert _FakePrompt.created == []
        assert es._clean_on_close is False

    def test_nothing_changed_closes_and_marks_clean(self, env, qtbot):
        es.record_backup_state()
        assert _run(env, qtbot)[0] is True
        assert _FakePrompt.created == []
        assert es._clean_on_close is True

    def test_failing_change_check_never_blocks_closing(self, env, qtbot, monkeypatch):
        monkeypatch.setattr(es, "has_changes_since_backup",
                            lambda: (_ for _ in ()).throw(OSError("disk")))
        assert _run(env, qtbot)[0] is True

    def test_ask_cancel_keeps_the_window_open(self, env, qtbot):
        decision, done = _run(env, qtbot)
        assert decision is False and done == []
        assert _FakePrompt.created[0].show_dont_ask is True
        assert list_backup_sets(env.folder) == []

    def test_ask_not_now(self, env, qtbot):
        _FakePrompt.answer = ExitChoice.NOT_NOW
        assert _run(env, qtbot)[0] is True
        assert es.get_on_exit() == es.ON_EXIT_ASK
        assert list_backup_sets(env.folder) == []
        assert es._clean_on_close is False

    def test_not_now_dont_ask_again_means_never(self, env, qtbot):
        _FakePrompt.answer = ExitChoice.NOT_NOW
        _FakePrompt.dont_ask = True
        _run(env, qtbot)
        assert es.get_on_exit() == es.ON_EXIT_NEVER


class TestControllerBackup:
    def test_back_up_writes_an_auto_set_and_closes(self, env, qtbot):
        _FakePrompt.answer = ExitChoice.BACK_UP
        decision, done = _run(env, qtbot)
        assert decision is None
        assert done == [True]
        sets = list_backup_sets(env.folder)
        assert [s.kind for s in sets] == [KIND_AUTO]
        assert {d.name for d in sets[0].databases} == {"A", "B"}
        assert es.has_changes_since_backup() is False
        assert es._clean_on_close is True
        assert es.get_on_exit() == es.ON_EXIT_ASK

    def test_back_up_dont_ask_again_means_always(self, env, qtbot):
        _FakePrompt.answer = ExitChoice.BACK_UP
        _FakePrompt.dont_ask = True
        _run(env, qtbot)
        assert es.get_on_exit() == es.ON_EXIT_ALWAYS

    def test_back_up_remembers_a_folder_chosen_in_the_prompt(self, env, qtbot, tmp_path):
        _FakePrompt.answer = ExitChoice.BACK_UP
        _FakePrompt.folder_override = tmp_path / "usb"
        _run(env, qtbot)
        assert get_store().get(BACKUP_DIR_KEY) == str(tmp_path / "usb")
        assert len(list_backup_sets(tmp_path / "usb")) == 1

    def test_always_backs_up_without_asking(self, env, qtbot):
        es.set_on_exit(es.ON_EXIT_ALWAYS)
        decision, done = _run(env, qtbot)
        assert decision is None and done == [True]
        assert _FakePrompt.created == []
        assert len(list_backup_sets(env.folder)) == 1

    def test_default_folder_is_created_on_first_use(self, env, qtbot):
        from opensak.backup.backupset import default_backup_dir
        get_store().delete(BACKUP_DIR_KEY)
        es.set_on_exit(es.ON_EXIT_ALWAYS)
        assert not default_backup_dir().exists()
        decision, done = _run(env, qtbot)
        assert decision is None and done == [True]
        assert _FakePrompt.created == []
        assert len(list_backup_sets(default_backup_dir())) == 1

    def test_always_with_an_unavailable_folder_asks(self, env, qtbot, tmp_path):
        es.set_on_exit(es.ON_EXIT_ALWAYS)
        get_store().set(BACKUP_DIR_KEY, str(tmp_path / "usb" / "OpenSAK Backups"))
        decision, _ = _run(env, qtbot)
        assert decision is False                     # fake prompt: Cancel
        assert _FakePrompt.created[0].show_dont_ask is False

    def test_rotates_automatic_sets(self, env, qtbot):
        get_store().set(KEEP_AUTO_KEY, 1)
        es.set_on_exit(es.ON_EXIT_ALWAYS)
        from datetime import datetime, timezone
        from opensak.backup.backupset import write_backup_set
        write_backup_set(env.dbs, KIND_AUTO, folder=env.folder,
                         now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        _run(env, qtbot)
        assert len([s for s in list_backup_sets(env.folder) if s.kind == KIND_AUTO]) == 1

    def test_cancel_during_backup_closes_and_leaves_nothing(self, env, qtbot, monkeypatch):
        es.set_on_exit(es.ON_EXIT_ALWAYS)
        original = BackupWorker.run

        def _cancelling_run(self):
            self.request_cancel()
            original(self)

        monkeypatch.setattr(BackupWorker, "start", _cancelling_run)
        decision, done = _run(env, qtbot)
        assert decision is None and done == [True]
        assert list_backup_sets(env.folder) == []
        assert not any(env.folder.glob("*.partial"))
        assert es._clean_on_close is False

    @pytest.mark.parametrize("close_anyway", [True, False])
    def test_not_enough_space(self, env, qtbot, monkeypatch, close_anyway):
        es.set_on_exit(es.ON_EXIT_ALWAYS)
        asked: list[str] = []
        monkeypatch.setattr(
            "opensak.gui.dialogs.backup_dialog.write_backup_set",
            lambda *a, **k: (_ for _ in ()).throw(NotEnoughSpaceError("full")),
        )
        monkeypatch.setattr(ExitBackupController, "_ask_close_anyway",
                            lambda self, text: asked.append(text) or close_anyway)
        decision, done = _run(env, qtbot)
        assert decision is None and done == [close_anyway]
        assert asked == [tr("backup_no_space", path=str(env.folder), error="full")]
        assert es._clean_on_close is False

    def test_other_failure(self, env, qtbot, monkeypatch):
        es.set_on_exit(es.ON_EXIT_ALWAYS)
        asked: list[str] = []
        monkeypatch.setattr(
            "opensak.gui.dialogs.backup_dialog.write_backup_set",
            lambda *a, **k: (_ for _ in ()).throw(BackupError("boom")),
        )
        monkeypatch.setattr(ExitBackupController, "_ask_close_anyway",
                            lambda self, text: asked.append(text) or False)
        decision, done = _run(env, qtbot)
        assert done == [False]
        assert asked == [tr("backup_failed_msg", path=str(env.folder), error="boom")]


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


class TestSettingsRows:
    def test_defaults(self, settings_dlg):
        d = settings_dlg()
        assert d._backup_on_exit_combo.currentData() == es.ON_EXIT_ASK
        assert d._backup_keep_auto.value() == 5
        assert d._backup_keep_auto.minimum() == 1

    def test_loads_stored_values(self, settings_dlg):
        es.set_on_exit(es.ON_EXIT_NEVER)
        get_store().set(KEEP_AUTO_KEY, 9)
        d = settings_dlg()
        assert d._backup_on_exit_combo.currentData() == es.ON_EXIT_NEVER
        assert d._backup_keep_auto.value() == 9

    def test_saves(self, settings_dlg):
        d = settings_dlg()
        d._backup_on_exit_combo.setCurrentIndex(
            d._backup_on_exit_combo.findData(es.ON_EXIT_ALWAYS))
        d._backup_keep_auto.setValue(3)
        d._save()
        assert es.get_on_exit() == es.ON_EXIT_ALWAYS
        assert get_store().get(KEEP_AUTO_KEY) == 3


# ── Self-closing paths ────────────────────────────────────────────────────────

class TestSelfClosingPathsSuppress:
    @pytest.mark.parametrize("handler", [
        "_on_appimage_uninstall_clicked", "_on_macos_uninstall_clicked",
    ])
    def test_uninstall_suppresses_the_prompt(self, settings_dlg, monkeypatch, handler):
        monkeypatch.setattr(es, "_suppressed", False)
        mod = ("opensak.gui.dialogs.appimage_uninstall_dialog"
               if "appimage" in handler else
               "opensak.gui.dialogs.macos_uninstall_dialog")
        monkeypatch.setattr(f"{mod}.confirm_and_uninstall", lambda parent: True)
        with patch("PySide6.QtWidgets.QApplication.quit") as quit_:
            getattr(settings_dlg(), handler)()
        quit_.assert_called_once()
        assert es.is_exit_backup_suppressed() is True

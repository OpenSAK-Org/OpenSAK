"""
src/opensak/gui/dialogs/exit_backup_dialog.py — back up on exit (#959).

Part of the backup epic (#942); design: docs/architecture/backup.md
("Backup flows → On exit").

ExitBackupController decides, when the main window is closed, whether to
offer a backup, asks if the setting is Ask, and runs the backup with the
same worker as File → Back up now… (#953). MainWindow.closeEvent() uses it:

    decision = controller.start()
    True   close now
    False  stay open (the user chose Cancel)
    None   a backup is running; ``done(close: bool)`` follows

Change detection and the setting live in opensak.backup.exit_state.
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QProgressDialog, QPushButton, QVBoxLayout, QWidget,
)

from opensak.backup import exit_state
from opensak.backup.backupset import (
    folder_available,  # moved to the core in #988; still imported from here
    KIND_AUTO,
    BackupError,
    BackupResult,
    get_backup_dir,
    set_backup_dir,
    validate_backup_dir,
)
from opensak.gui.dialogs.backup_dialog import BackupWorker
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.gui.theme import hint_style
from opensak.lang import tr

logger = logging.getLogger(__name__)

_PROGRESS_STEPS = 1000


class ExitChoice(Enum):
    BACK_UP = "back_up"
    NOT_NOW = "not_now"
    CANCEL = "cancel"


def _folder_usable(folder: Path) -> bool:
    if not folder_available(folder):
        return False
    try:
        validate_backup_dir(folder)
    except BackupError:
        return False
    return True


# ── Prompt ────────────────────────────────────────────────────────────────────

class ExitBackupPrompt(QDialog):
    """Back up / Not now / Cancel, with the backup folder and "Don't ask again"."""

    def __init__(
        self,
        folder: Path,
        *,
        show_dont_ask: bool,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("exit_backup_title"))
        self.setMinimumWidth(480)
        self._folder = Path(folder)
        self._choice = ExitChoice.CANCEL

        layout = QVBoxLayout(self)

        self._message = QLabel(tr("exit_backup_msg"))
        self._message.setWordWrap(True)
        layout.addWidget(self._message)

        self._unavailable = QLabel()
        self._unavailable.setWordWrap(True)
        self._unavailable.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._unavailable)

        layout.addSpacing(6)
        layout.addWidget(QLabel(tr("backup_folder_label")))
        row = QHBoxLayout()
        self._folder_edit = QLineEdit(str(self._folder))
        self._folder_edit.setReadOnly(True)
        row.addWidget(self._folder_edit, 1)
        self._browse_btn = QPushButton(tr("import_browse"))
        self._browse_btn.clicked.connect(self._browse)
        row.addWidget(self._browse_btn)
        layout.addLayout(row)

        self._dont_ask: Optional[QCheckBox] = None
        if show_dont_ask:
            layout.addSpacing(6)
            self._dont_ask = QCheckBox(tr("appimage_integrate_btn_dont_ask"))
            layout.addWidget(self._dont_ask)
            hint = QLabel(tr("exit_backup_dont_ask_hint"))
            hint.setWordWrap(True)
            hint.setStyleSheet(hint_style())
            layout.addWidget(hint)

        buttons = QDialogButtonBox()
        self._backup_btn = buttons.addButton(
            tr("exit_backup_start"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._not_now_btn = buttons.addButton(
            tr("exit_backup_not_now"), QDialogButtonBox.ButtonRole.DestructiveRole
        )
        self._cancel_btn = buttons.addButton(
            tr("cancel"), QDialogButtonBox.ButtonRole.RejectRole
        )
        self._backup_btn.clicked.connect(lambda: self._finish(ExitChoice.BACK_UP))
        self._not_now_btn.clicked.connect(lambda: self._finish(ExitChoice.NOT_NOW))
        self._cancel_btn.clicked.connect(self.reject)
        self._backup_btn.setDefault(True)
        layout.addWidget(buttons)

        self._update_availability()
        # Issue #811: never taller than the screen.
        clamp_dialog_height_to_screen(self, parent)

    # ── State ──────────────────────────────────────────────────────────────

    def choice(self) -> ExitChoice:
        return self._choice

    def folder(self) -> Path:
        return self._folder

    def dont_ask_again(self) -> bool:
        return self._dont_ask is not None and self._dont_ask.isChecked()

    def _finish(self, choice: ExitChoice) -> None:
        self._choice = choice
        self.accept()

    def reject(self) -> None:
        self._choice = ExitChoice.CANCEL
        super().reject()

    # ── Folder ─────────────────────────────────────────────────────────────

    def _update_availability(self) -> None:
        available = folder_available(self._folder)
        self._unavailable.setText(
            "" if available
            else tr("exit_backup_folder_unavailable", path=str(self._folder))
        )
        self._unavailable.setVisible(not available)
        self._backup_btn.setEnabled(available)

    def _browse(self) -> None:
        start = self._folder if folder_available(self._folder) else Path.home()
        chosen = QFileDialog.getExistingDirectory(
            self, tr("backup_folder_label"), str(start),
            QFileDialog.Option.ShowDirsOnly,
        )
        if chosen:
            self.choose_folder(Path(chosen))

    def choose_folder(self, folder: Path) -> bool:
        """Use *folder* if it can hold backups; explain why not otherwise."""
        try:
            validate_backup_dir(folder)
        except BackupError:
            QMessageBox.warning(self, tr("exit_backup_title"), tr("backup_folder_invalid"))
            return False
        self._folder = Path(folder)
        self._folder_edit.setText(str(self._folder))
        self._update_availability()
        return True


# ── Controller ────────────────────────────────────────────────────────────────

class ExitBackupController(QObject):
    """Runs the on-exit decision, prompt and backup for one close attempt."""

    done = Signal(bool)   # True: close the window now; False: stay open

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        self._worker: Optional[BackupWorker] = None
        self._progress: Optional[QProgressDialog] = None
        self._folder: Path = get_backup_dir()

    # ── Decision ───────────────────────────────────────────────────────────

    def start(self) -> Optional[bool]:
        if exit_state.is_exit_backup_suppressed() or _session_ending():
            return True
        mode = exit_state.get_on_exit()
        if mode == exit_state.ON_EXIT_NEVER:
            return True
        try:
            changed = exit_state.has_changes_since_backup()
        except Exception:
            # Never let a failing check keep the user from closing OpenSAK.
            logger.warning("backup: change check failed", exc_info=True)
            return True
        if not changed:
            exit_state.mark_clean_on_close()
            return True

        if mode == exit_state.ON_EXIT_ALWAYS and _folder_usable(self._folder):
            self._start_backup()
            return None

        # Ask — or Always with a folder that can't be used right now.
        prompt = self._make_prompt(show_dont_ask=(mode == exit_state.ON_EXIT_ASK))
        prompt.exec()
        choice = prompt.choice()
        if choice is ExitChoice.CANCEL:
            return False
        if choice is ExitChoice.NOT_NOW:
            if prompt.dont_ask_again():
                exit_state.set_on_exit(exit_state.ON_EXIT_NEVER)
            return True
        if prompt.dont_ask_again():
            exit_state.set_on_exit(exit_state.ON_EXIT_ALWAYS)
        self._folder = prompt.folder()
        try:
            set_backup_dir(self._folder)   # remember the folder for next time
        except BackupError:
            # The prompt only accepts valid folders; be safe anyway.
            logger.warning("backup: unsuitable folder %s", self._folder)
            return True
        self._start_backup()
        return None

    def _make_prompt(self, *, show_dont_ask: bool) -> ExitBackupPrompt:
        return ExitBackupPrompt(
            self._folder, show_dont_ask=show_dont_ask, parent=self._window
        )

    def is_running(self) -> bool:
        return self._worker is not None

    # ── Backup ─────────────────────────────────────────────────────────────

    def _databases(self) -> list[Any]:
        from opensak.db.manager import get_db_manager
        return list(get_db_manager().databases)

    def _start_backup(self) -> None:
        progress = QProgressDialog(
            tr("backup_progress_preparing"), tr("cancel"),
            0, _PROGRESS_STEPS, self._window,
        )
        progress.setWindowTitle(tr("backup_dialog_title"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        self._progress = progress

        worker = BackupWorker(
            self._databases(), KIND_AUTO, self._folder,
            rotate_after=True, parent=self._window,
        )
        worker.progress.connect(self._on_progress)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        progress.canceled.connect(worker.request_cancel)
        self._worker = worker
        worker.start()

    def _on_progress(self, name: str, _db_fraction: float, overall: float) -> None:
        if self._progress is not None:
            self._progress.setLabelText(tr("backup_progress_label", name=name))
            self._progress.setValue(int(overall * _PROGRESS_STEPS))

    def _finish(self) -> None:
        if self._progress is not None:
            self._progress.close()
            self._progress = None
        if self._worker is not None:
            self._worker.wait()
            self._worker = None

    def _on_succeeded(self, result: BackupResult) -> None:
        self._finish()
        if result.skipped:
            logger.info("backup on exit: skipped missing databases: %s",
                        ", ".join(result.skipped))
        try:
            exit_state.record_backup_state()
            exit_state.mark_clean_on_close()
        except Exception:
            logger.warning("backup: could not record the backup state", exc_info=True)
        self.done.emit(True)

    def _on_cancelled(self) -> None:
        # Nothing was left behind; the user asked to close, so close.
        self._finish()
        self.done.emit(True)

    def _on_failed(self, reason: str, no_space: bool) -> None:
        self._finish()
        key = "backup_no_space" if no_space else "backup_failed_msg"
        text = tr(key, path=str(self._folder), error=reason)
        self.done.emit(self._ask_close_anyway(text))

    def _ask_close_anyway(self, text: str) -> bool:
        box = QMessageBox(self._window)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(tr("backup_failed_title"))
        box.setText(text)
        close_btn = box.addButton(
            tr("exit_backup_close_anyway"), QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_btn = box.addButton(tr("cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()
        return box.clickedButton() is close_btn


def _session_ending() -> bool:
    """True while the OS session is ending (logout/shutdown); no time for a backup."""
    app = QGuiApplication.instance()
    return isinstance(app, QGuiApplication) and app.isSavingSession()

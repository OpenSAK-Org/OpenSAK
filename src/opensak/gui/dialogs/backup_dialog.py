"""
src/opensak/gui/dialogs/backup_dialog.py — File → Back up now… (#953).

Part of the backup epic (#942); design: docs/architecture/backup.md.

BackupWorker runs the backup core (opensak.backup.backupset) in a thread and
is written to be reused by the on-exit prompt (sub-issue 4 of #942): pass
``kind="auto"`` and ``rotate_after=True``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional, Sequence

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QProgressDialog, QPushButton, QVBoxLayout,
    QWidget,
)

from opensak.backup.backupset import (
    KIND_MANUAL,
    BackupError,
    BackupResult,
    NotEnoughSpaceError,
    get_backup_dir,
    rotate,
    set_backup_dir,
    validate_backup_dir,
    write_backup_set,
)
from opensak.backup.exit_state import record_backup_state
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.lang import tr

logger = logging.getLogger(__name__)

# QProgressDialog works in integers; 1000 steps gives smooth progress.
_PROGRESS_STEPS = 1000


class _Cancelled(Exception):
    """Raised from the progress callback to stop a backup the user cancelled."""


class BackupWorker(QThread):
    """Writes one backup set off the GUI thread."""

    progress = Signal(str, float, float)   # (database name, its fraction, overall)
    succeeded = Signal(object)             # BackupResult
    failed = Signal(str, bool)             # (technical reason, not enough space)
    cancelled = Signal()

    def __init__(
        self,
        databases: Sequence[Any],
        kind: str,
        folder: Path,
        *,
        rotate_after: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._databases = list(databases)
        self._kind = kind
        self._folder = Path(folder)
        self._rotate_after = rotate_after
        self._cancel = False

    def request_cancel(self) -> None:
        """Stop at the next progress step; nothing is left behind."""
        self._cancel = True

    def run(self) -> None:
        def _progress(name: str, db_fraction: float, overall: float) -> None:
            if self._cancel:
                raise _Cancelled()
            self.progress.emit(name, db_fraction, overall)

        try:
            if self._cancel:
                raise _Cancelled()
            result = write_backup_set(
                self._databases, self._kind, _progress, folder=self._folder
            )
        except _Cancelled:
            self.cancelled.emit()
            return
        except NotEnoughSpaceError as exc:
            self.failed.emit(str(exc), True)
            return
        except Exception as exc:  # BackupError and anything unexpected
            logger.exception("backup failed")
            self.failed.emit(str(exc), False)
            return

        if self._rotate_after:
            try:
                rotate(self._folder)
            except Exception:
                # The backup itself succeeded; failing to tidy up old ones
                # must not turn that into an error for the user.
                logger.warning("backup: rotation failed", exc_info=True)
        self.succeeded.emit(result)


class BackupDialog(QDialog):
    """Choose the backup folder and databases, then back them up."""

    backup_made = Signal(object)   # BackupResult

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("backup_dialog_title"))
        self.setMinimumWidth(520)

        self._folder = get_backup_dir()
        self._worker: Optional[BackupWorker] = None
        self._progress_dialog: Optional[QProgressDialog] = None
        self._backing_up: list[Any] = []   # the databases of the running backup

        layout = QVBoxLayout(self)

        # ── Backup folder ────────────────────────────────────────────────
        layout.addWidget(QLabel(tr("backup_folder_label")))
        row = QHBoxLayout()
        self._folder_edit = QLineEdit(str(self._folder))
        self._folder_edit.setReadOnly(True)
        row.addWidget(self._folder_edit, 1)
        self._browse_btn = QPushButton(tr("import_browse"))
        self._browse_btn.clicked.connect(self._browse)
        row.addWidget(self._browse_btn)
        layout.addLayout(row)

        tip = QLabel(tr("backup_folder_tip"))
        tip.setWordWrap(True)
        tip.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(tip)

        # ── Databases ────────────────────────────────────────────────────
        layout.addSpacing(8)
        layout.addWidget(QLabel(tr("backup_databases_label")))
        self._db_list = QListWidget()
        self._fill_databases()
        layout.addWidget(self._db_list, 1)

        note = QLabel(tr("backup_settings_note"))
        note.setWordWrap(True)
        layout.addWidget(note)

        # ── Buttons ──────────────────────────────────────────────────────
        self._buttons = QDialogButtonBox()
        self._start_btn = self._buttons.addButton(
            tr("backup_start"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._buttons.addButton(tr("cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        self._buttons.accepted.connect(self._start)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        # Issue #811: never taller than the screen (a long database list at
        # high DPI scaling could otherwise push the buttons off-screen).
        clamp_dialog_height_to_screen(self, parent)

    # ── Databases ──────────────────────────────────────────────────────────

    def _fill_databases(self) -> None:
        from opensak.db.manager import get_db_manager

        for db in get_db_manager().databases:
            exists = Path(db.path).is_file()
            text = db.name if exists else tr("backup_missing_file", name=db.name)
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, db)
            if exists:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setFlags(Qt.ItemFlag.NoItemFlags)   # shown, greyed out
            item.setToolTip(str(db.path))
            self._db_list.addItem(item)

    def selected_databases(self) -> list[Any]:
        """The databases ticked in the list."""
        chosen = []
        for i in range(self._db_list.count()):
            item = self._db_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                chosen.append(item.data(Qt.ItemDataRole.UserRole))
        return chosen

    # ── Folder ─────────────────────────────────────────────────────────────

    def folder(self) -> Path:
        return self._folder

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, tr("backup_folder_label"), str(self._folder),
            QFileDialog.Option.ShowDirsOnly,
        )
        if chosen:
            self.choose_folder(Path(chosen))

    def choose_folder(self, folder: Path) -> bool:
        """Use *folder* if it can hold backups; explain why not otherwise."""
        try:
            validate_backup_dir(folder)
        except BackupError:
            QMessageBox.warning(
                self, tr("backup_dialog_title"), tr("backup_folder_invalid")
            )
            return False
        self._folder = Path(folder)
        self._folder_edit.setText(str(self._folder))
        return True

    # ── Running ────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        return self._worker is not None

    def _start(self) -> None:
        databases = self.selected_databases()
        if not databases:
            QMessageBox.warning(
                self, tr("backup_dialog_title"), tr("backup_none_selected")
            )
            return
        try:
            set_backup_dir(self._folder)   # remember the folder for next time
        except BackupError:
            QMessageBox.warning(
                self, tr("backup_dialog_title"), tr("backup_folder_invalid")
            )
            return

        self._set_inputs_enabled(False)

        progress = QProgressDialog(
            tr("backup_progress_preparing"), tr("cancel"),
            0, _PROGRESS_STEPS, self,
        )
        progress.setWindowTitle(tr("backup_dialog_title"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        self._progress_dialog = progress

        self._backing_up = list(databases)
        worker = BackupWorker(databases, KIND_MANUAL, self._folder, parent=self)
        worker.progress.connect(self._on_progress)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        progress.canceled.connect(worker.request_cancel)
        self._worker = worker
        worker.start()

    def _on_progress(self, name: str, _db_fraction: float, overall: float) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.setLabelText(tr("backup_progress_label", name=name))
            self._progress_dialog.setValue(int(overall * _PROGRESS_STEPS))

    def _finish(self) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog = None
        if self._worker is not None:
            self._worker.wait()
            self._worker = None
        self._set_inputs_enabled(True)

    def _on_succeeded(self, result: BackupResult) -> None:
        self._finish()
        # #959: a manual backup counts as "the last backup", so closing
        # straight after it doesn't ask again. Only the databases that were
        # ticked are recorded; a change to an unticked one still counts.
        try:
            record_backup_state(self._backing_up)
        except Exception:
            logger.warning("backup: could not record the backup state", exc_info=True)
        text = tr("backup_done_msg", path=str(result.backup_set.path))
        if result.skipped:
            text += "\n\n" + tr("backup_done_skipped", names=", ".join(result.skipped))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(tr("backup_done_title"))
        box.setText(text)
        open_btn = box.addButton(
            tr("file_locations_open_folder"), QMessageBox.ButtonRole.ActionRole
        )
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is open_btn:
            # A compressed set (#989) is a zip file: open the folder it's in
            # rather than the archive itself.
            target = result.backup_set.path
            if result.backup_set.compressed:
                target = target.parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        self.backup_made.emit(result)
        self.accept()

    def _on_failed(self, reason: str, no_space: bool) -> None:
        self._finish()
        key = "backup_no_space" if no_space else "backup_failed_msg"
        QMessageBox.critical(
            self, tr("backup_failed_title"),
            tr(key, path=str(self._folder), error=reason),
        )

    def _on_cancelled(self) -> None:
        self._finish()
        QMessageBox.information(
            self, tr("backup_dialog_title"), tr("backup_cancelled_msg")
        )

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for widget in (self._browse_btn, self._db_list, self._start_btn):
            widget.setEnabled(enabled)

    def reject(self) -> None:
        # While a backup runs, Escape or the window's close button cancels
        # it rather than leaving a worker running behind a closed dialog.
        if self._worker is not None:
            self._worker.request_cancel()
            return
        super().reject()

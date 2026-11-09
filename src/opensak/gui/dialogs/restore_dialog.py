"""
src/opensak/gui/dialogs/restore_dialog.py — File → Restore from backup… (#954).

Part of the backup epic (#942); design: docs/architecture/backup.md.

Restore always adds: every restored database becomes a new database in the
list (see opensak.backup.restore). Cancelling rolls back the databases
restored so far in the same run, so a cancelled restore leaves nothing.

#987: "Also restore settings" is opt-in. It saves the current settings as a
safety set and stages the backup's settings for the next start (see
opensak.backup.settings_restore); the user is offered to close OpenSAK.

#1137: "Also restore macros" is on by default for a set that has macros. It
puts the backup's macros back into today's macros folder right away (see
opensak.backup.macros_restore). Files that differ from the backup are listed
first (MacroConflictDialog); only the ones the user ticks are replaced.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QProgressDialog, QPushButton, QVBoxLayout,
    QWidget,
)

from opensak.backup.backupset import (
    is_compressed_set_file,
    KIND_AUTO,
    KIND_SAFETY,
    MANIFEST_NAME,
    BackupError,
    BackupSet,
    DatabaseEntry,
    get_backup_dir,
    list_backup_sets,
    read_backup_set,
)
from opensak.backup.macros_restore import (
    MacroRestoreError,
    MacroRestoreItem,
    MacroRestorePlan,
    MacroRestoreResult,
    has_macros,
    plan_macro_restore,
    restore_macros,
)
from opensak.backup.restore import (
    RestoreError,
    is_newer_than_this_version,
    restore_database,
    restore_missing_filter_profiles,
)
from opensak.backup.settings_restore import (
    SettingsRestoreError,
    can_restore_settings,
    stage_settings_restore,
)
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.lang import tr

logger = logging.getLogger(__name__)

_PROGRESS_STEPS = 1000


class _Cancelled(Exception):
    """Raised from the progress callback to stop a restore the user cancelled."""


@dataclass
class RestoreSummary:
    """What a restore run did."""

    restored: list[Any] = field(default_factory=list)            # DatabaseInfo
    refused: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)
    profiles_added: list[str] = field(default_factory=list)


class RestoreWorker(QThread):
    """Restores the chosen databases from one backup set off the GUI thread."""

    progress = Signal(str, float, float)   # (database name, its fraction, overall)
    done = Signal(object)                  # RestoreSummary
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        backup_set: BackupSet,
        entries: Sequence[DatabaseEntry],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._set = backup_set
        self._entries = list(entries)
        self._cancel = False

    def request_cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        summary = RestoreSummary()
        weights = [max(1, e.size_bytes) for e in self._entries]
        total = sum(weights)
        before = 0
        try:
            for entry, weight in zip(self._entries, weights):
                if self._cancel:
                    raise _Cancelled()

                def _progress(done: int, pages: int, *, _name: str = entry.name,
                              _before: int = before, _weight: int = weight) -> None:
                    if self._cancel:
                        raise _Cancelled()
                    fraction = done / pages if pages else 1.0
                    self.progress.emit(_name, fraction,
                                       (_before + fraction * _weight) / total)

                try:
                    summary.restored.append(
                        restore_database(self._set, entry, _progress)
                    )
                except RestoreError as exc:
                    summary.refused.append((entry.name, str(exc)))
                before += weight
                # A small database can finish in a single step, so a cancel
                # clicked during it is only seen here, not in _progress.
                if self._cancel:
                    raise _Cancelled()

            if summary.restored:
                summary.profiles_added = restore_missing_filter_profiles(self._set)
        except _Cancelled:
            self._roll_back(summary.restored)
            self.cancelled.emit()
            return
        except Exception as exc:
            logger.exception("restore failed")
            self._roll_back(summary.restored)
            self.failed.emit(str(exc))
            return
        self.done.emit(summary)

    @staticmethod
    def _roll_back(restored: list[Any]) -> None:
        """Remove the databases this run already restored (best-effort)."""
        from opensak.db.manager import get_db_manager
        manager = get_db_manager()
        for info in restored:
            try:
                manager.delete_database(info)
            except Exception:
                logger.warning("restore: could not roll back %s", info.path,
                               exc_info=True)


class MacroConflictDialog(QDialog):
    """#1137: the macro files that differ from the backup. Ticked ones are
    replaced by the backup's version; nothing is ticked at first."""

    def __init__(
        self, conflicts: Sequence[MacroRestoreItem], parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("restore_macros_conflicts_title"))
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        intro = QLabel(tr("restore_macros_conflicts_intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._list = QListWidget()
        for item in conflicts:
            text = (
                tr("restore_macros_conflict_db", name=item.rel) if item.database
                else item.rel
            )
            row = QListWidgetItem(text)
            row.setData(Qt.ItemDataRole.UserRole, item.rel)
            row.setToolTip(str(item.target))
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(Qt.CheckState.Unchecked)
            self._list.addItem(row)
        layout.addWidget(self._list, 1)

        note = QLabel(tr("restore_macros_conflicts_note"))
        note.setWordWrap(True)
        layout.addWidget(note)

        row_layout = QHBoxLayout()
        replace_all = QPushButton(tr("macro_replace_all"))
        replace_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        keep_all = QPushButton(tr("restore_macros_keep_all"))
        keep_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        row_layout.addWidget(replace_all)
        row_layout.addWidget(keep_all)
        row_layout.addStretch(1)
        layout.addLayout(row_layout)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        clamp_dialog_height_to_screen(self, parent)   # issue #811

    def _set_all(self, state: Qt.CheckState) -> None:
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(state)

    def replace_rels(self) -> list[str]:
        """The files (relative to the macros folder) to replace."""
        return [
            self._list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.CheckState.Checked
        ]


class RestoreDialog(QDialog):
    """Pick a backup set and the databases to restore from it."""

    database_switched = Signal(object)   # DatabaseInfo — the user switched to it
    databases_added = Signal()           # the database list changed
    close_app_requested = Signal()       # #987: settings staged, close OpenSAK now

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("restore_dialog_title"))
        self.setMinimumWidth(560)

        self._folder = get_backup_dir()
        self._sets: list[BackupSet] = []
        self._worker: Optional[RestoreWorker] = None
        self._progress_dialog: Optional[QProgressDialog] = None
        self._also_settings = False   # #987: chosen for the run in progress
        # #1137: the macros to restore in the run in progress, and the
        # conflicts the user chose to replace.
        self._macro_plan: Optional[MacroRestorePlan] = None
        self._macro_replace: list[str] = []
        self._running_set: Optional[BackupSet] = None

        layout = QVBoxLayout(self)

        # ── Where to look ────────────────────────────────────────────────
        layout.addWidget(QLabel(tr("restore_folder_label")))
        row = QHBoxLayout()
        self._folder_edit = QLineEdit(str(self._folder))
        self._folder_edit.setReadOnly(True)
        row.addWidget(self._folder_edit, 1)
        self._browse_btn = QPushButton(tr("import_browse"))
        self._browse_btn.clicked.connect(self._browse)
        row.addWidget(self._browse_btn)
        layout.addLayout(row)

        # ── Backup sets ──────────────────────────────────────────────────
        layout.addSpacing(8)
        layout.addWidget(QLabel(tr("restore_sets_label")))
        self._set_list = QListWidget()
        self._set_list.currentRowChanged.connect(self._on_set_selected)
        layout.addWidget(self._set_list, 1)
        self._empty_label = QLabel(tr("restore_no_sets"))
        self._empty_label.setWordWrap(True)
        layout.addWidget(self._empty_label)

        # ── Databases in the chosen set ──────────────────────────────────
        layout.addWidget(QLabel(tr("restore_databases_label")))
        self._db_list = QListWidget()
        layout.addWidget(self._db_list, 1)

        # #987: opt-in, off for every set that is selected.
        self._settings_cb = QCheckBox(tr("restore_settings_cb"))
        layout.addWidget(self._settings_cb)

        # #1137: on for every set that has macros.
        self._macros_cb = QCheckBox(tr("restore_macros_cb"))
        layout.addWidget(self._macros_cb)

        note = QLabel(tr("restore_note"))
        note.setWordWrap(True)
        layout.addWidget(note)

        self._buttons = QDialogButtonBox()
        self._start_btn = self._buttons.addButton(
            tr("restore_start"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._buttons.addButton(tr("cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        self._buttons.accepted.connect(self._start)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        clamp_dialog_height_to_screen(self, parent)   # issue #811
        self.load(self._folder)

    # ── Sets ───────────────────────────────────────────────────────────────

    def load(self, path: Path) -> None:
        """Show the sets in folder *path*, or the single set *path* itself."""
        path = Path(path)
        # A single set: a set folder, or a compressed set file (#989).
        if (path / MANIFEST_NAME).is_file() or is_compressed_set_file(path):
            try:
                self._sets = [read_backup_set(path)]
            except BackupError as exc:
                logger.info("restore: %s", exc)
                self._sets = []
        else:
            self._sets = list_backup_sets(path)
        self._folder = path
        self._folder_edit.setText(str(path))

        self._set_list.clear()
        for s in self._sets:
            kind = tr({
                KIND_AUTO: "restore_kind_auto",
                KIND_SAFETY: "restore_kind_safety",
            }.get(s.kind, "restore_kind_manual"))
            text = tr(
                "restore_set_item",
                date=s.created.astimezone().strftime("%Y-%m-%d %H:%M"),
                kind=kind,
                version=s.opensak_version or "?",
                count=len(s.databases),
            )
            item = QListWidgetItem(text)
            item.setToolTip(str(s.path))
            self._set_list.addItem(item)
        has_sets = bool(self._sets)
        self._empty_label.setVisible(not has_sets)
        self._start_btn.setEnabled(has_sets)
        if has_sets:
            self._set_list.setCurrentRow(0)
        else:
            self._db_list.clear()
            self._update_settings_cb(None)
            self._update_macros_cb(None)

    def selected_set(self) -> Optional[BackupSet]:
        row = self._set_list.currentRow()
        return self._sets[row] if 0 <= row < len(self._sets) else None

    def _update_settings_cb(self, backup_set: Optional[BackupSet]) -> None:
        """#987: unticked for every set; disabled with the reason if it can't."""
        reason = (
            can_restore_settings(backup_set) if backup_set is not None
            else tr("restore_settings_none")
        )
        self._settings_cb.setChecked(False)
        self._settings_cb.setEnabled(reason is None)
        self._settings_cb.setToolTip(reason or "")

    def _update_macros_cb(self, backup_set: Optional[BackupSet]) -> None:
        """#1137: ticked for a set with macros; disabled for one without."""
        available = backup_set is not None and has_macros(backup_set)
        self._macros_cb.setChecked(available)
        self._macros_cb.setEnabled(available)
        if backup_set is not None and backup_set.macros is not None:
            self._macros_cb.setToolTip(
                tr("restore_macros_count", count=backup_set.macros.files)
            )
        else:
            self._macros_cb.setToolTip("" if available else tr("restore_macros_none"))

    def _on_set_selected(self, _row: int) -> None:
        self._db_list.clear()
        backup_set = self.selected_set()
        self._update_settings_cb(backup_set)
        self._update_macros_cb(backup_set)
        if backup_set is None:
            return
        for entry in backup_set.databases:
            newer = is_newer_than_this_version(entry)
            text = tr("restore_needs_newer", name=entry.name) if newer else entry.name
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            if newer:
                item.setFlags(Qt.ItemFlag.NoItemFlags)   # shown, greyed out
            else:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
            self._db_list.addItem(item)

    def selected_entries(self) -> list[DatabaseEntry]:
        chosen = []
        for i in range(self._db_list.count()):
            item = self._db_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                chosen.append(item.data(Qt.ItemDataRole.UserRole))
        return chosen

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, tr("restore_folder_label"), str(self._folder),
            QFileDialog.Option.ShowDirsOnly,
        )
        if chosen:
            self.load(Path(chosen))

    # ── Running ────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        return self._worker is not None

    def _start(self) -> None:
        backup_set = self.selected_set()
        entries = self.selected_entries()
        also_settings = self._settings_cb.isChecked() and self._settings_cb.isEnabled()
        also_macros = self._macros_cb.isChecked() and self._macros_cb.isEnabled()
        if backup_set is None or not (entries or also_settings or also_macros):
            QMessageBox.warning(
                self, tr("restore_dialog_title"), tr("restore_none_selected")
            )
            return
        macro_plan: Optional[MacroRestorePlan] = None
        macro_replace: list[str] = []
        if also_macros:
            prepared = self._prepare_macros(backup_set)
            if prepared is None:
                return
            macro_plan, macro_replace = prepared
        if also_settings:
            answer = QMessageBox.question(
                self, tr("restore_dialog_title"), tr("restore_settings_confirm"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._also_settings = also_settings
        self._macro_plan, self._macro_replace = macro_plan, macro_replace

        if not entries:
            # Settings and/or macros only: quick and on this thread.
            self._report(backup_set, RestoreSummary())
            return

        for widget in (self._browse_btn, self._set_list, self._db_list,
                       self._settings_cb, self._macros_cb, self._start_btn):
            widget.setEnabled(False)

        progress = QProgressDialog(
            tr("restore_progress_preparing"), tr("cancel"),
            0, _PROGRESS_STEPS, self,
        )
        progress.setWindowTitle(tr("restore_dialog_title"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        self._progress_dialog = progress

        self._running_set = backup_set
        worker = RestoreWorker(backup_set, entries, parent=self)
        worker.progress.connect(self._on_progress)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        progress.canceled.connect(worker.request_cancel)
        self._worker = worker
        worker.start()

    def _prepare_macros(
        self, backup_set: BackupSet,
    ) -> Optional[tuple[MacroRestorePlan, list[str]]]:
        """#1137: plan the macros restore and let the user choose what to do
        with files that differ from the backup. None: don't start at all."""
        from opensak.macro import macro_running
        if macro_running():
            QMessageBox.warning(
                self, tr("restore_dialog_title"), tr("restore_macros_busy")
            )
            return None
        try:
            plan = plan_macro_restore(backup_set)
        except MacroRestoreError as exc:
            QMessageBox.critical(
                self, tr("restore_failed_title"),
                tr("restore_macros_failed", error=str(exc)),
            )
            return None
        replace: list[str] = []
        if plan.conflicts:
            dialog = MacroConflictDialog(plan.conflicts, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            replace = dialog.replace_rels()
        return plan, replace

    def _restore_macros(self) -> tuple[Optional[MacroRestoreResult], str]:
        """#1137: restore the planned macros (on this thread: they are small
        files) and describe the result for the report."""
        plan, replace = self._macro_plan, self._macro_replace
        self._macro_plan, self._macro_replace = None, []
        if plan is None:
            return None, ""
        try:
            result = restore_macros(plan, replace)
        except Exception as exc:
            logger.exception("restore: macros failed")
            return None, tr("restore_macros_failed", error=str(exc))
        parts = [tr(
            "restore_done_macros",
            restored=len(result.restored), replaced=len(result.replaced),
            kept=len(result.kept), unchanged=len(result.unchanged),
        )]
        if result.approvals:
            parts.append(tr("restore_done_macros_approvals", count=result.approvals))
        if result.failed:
            parts.append(tr(
                "restore_done_macros_failed",
                reasons="\n".join(f"{rel}: {reason}" for rel, reason in result.failed),
            ))
        if result.outside:
            parts.append(tr("restore_done_macros_outside", paths="\n".join(result.outside)))
        return result, "\n".join(parts)

    def _on_progress(self, name: str, _fraction: float, overall: float) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.setLabelText(tr("restore_progress_label", name=name))
            self._progress_dialog.setValue(int(overall * _PROGRESS_STEPS))

    def _finish(self) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog = None
        if self._worker is not None:
            self._worker.wait()
            self._worker = None
        for widget in (self._browse_btn, self._set_list, self._db_list):
            widget.setEnabled(True)
        self._start_btn.setEnabled(bool(self._sets))
        self._update_settings_cb(self.selected_set())
        self._update_macros_cb(self.selected_set())

    def _on_done(self, summary: RestoreSummary) -> None:
        self._finish()
        assert self._running_set is not None
        self._report(self._running_set, summary)

    def _report(self, backup_set: BackupSet, summary: RestoreSummary) -> None:
        """Restore the macros and stage the settings if chosen (#1137, #987),
        then tell the user what happened."""
        macro_result, macros_text = self._restore_macros()
        macros_done = macro_result is not None
        settings_staged = False
        settings_text = ""
        if self._also_settings:
            self._also_settings = False
            try:
                stage_settings_restore(backup_set)
                settings_staged = True
                settings_text = tr("restore_settings_staged")
            except SettingsRestoreError as exc:
                settings_text = tr("restore_settings_failed", error=str(exc))

        parts = []
        if summary.restored:
            parts.append(tr("restore_done_msg",
                            names="\n".join(i.name for i in summary.restored)))
        if summary.refused:
            parts.append(tr("restore_done_refused",
                            reasons="\n".join(f"{n}: {r}" for n, r in summary.refused)))
        if summary.profiles_added:
            parts.append(tr("restore_done_profiles",
                            names=", ".join(summary.profiles_added)))
        if macros_text:
            parts.append(macros_text)
        if settings_text:
            parts.append(settings_text)
        text = "\n\n".join(parts)

        if summary.restored:
            self.databases_added.emit()

        if settings_staged:
            # The app is about to be closed (or will be): don't also ask
            # about switching to a restored database.
            answer = QMessageBox.question(
                self, tr("restore_done_title"),
                text + "\n\n" + tr("restore_settings_close_now"),
            )
            self.accept()
            if answer == QMessageBox.StandardButton.Yes:
                self.close_app_requested.emit()
            return

        if not summary.restored:
            if macros_done and not summary.refused:
                QMessageBox.information(self, tr("restore_done_title"), text)
                self.accept()
            else:
                QMessageBox.warning(self, tr("restore_failed_title"), text)
            return

        if len(summary.restored) == 1:
            info = summary.restored[0]
            question = text + "\n\n" + tr("restore_switch_question", name=info.name)
            answer = QMessageBox.question(self, tr("restore_done_title"), question)
            if answer == QMessageBox.StandardButton.Yes:
                self._switch_to(info)
        else:
            QMessageBox.information(self, tr("restore_done_title"), text)
        self.accept()

    def _switch_to(self, info: Any) -> None:
        from opensak.db.manager import get_db_manager
        try:
            get_db_manager().switch_to(info)
        except Exception as exc:
            QMessageBox.critical(self, tr("restore_failed_title"), str(exc))
            return
        self.database_switched.emit(info)

    def _on_failed(self, reason: str) -> None:
        # Nothing was restored, so the settings and macros aren't either.
        self._also_settings = False
        self._macro_plan = None
        self._finish()
        QMessageBox.critical(
            self, tr("restore_failed_title"), tr("restore_failed_msg", error=reason)
        )

    def _on_cancelled(self) -> None:
        self._also_settings = False
        self._macro_plan = None
        self._finish()
        QMessageBox.information(
            self, tr("restore_dialog_title"), tr("restore_cancelled_msg")
        )

    def reject(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            return
        super().reject()

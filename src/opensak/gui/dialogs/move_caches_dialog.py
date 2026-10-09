"""move_caches_dialog.py — Dialog for moving or copying caches to another database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QButtonGroup,
)
from PySide6.QtCore import Qt, QThread, Signal

from opensak.db.transfer import transfer_caches
from opensak.lang import tr


class _MoveWorker(QThread):
    """Background thread that copies caches to the target DB, optionally
    deleting them from the source DB."""

    finished = Signal(int)   # number of caches moved/copied
    error = Signal(str)

    def __init__(
        self,
        gc_codes: list[str],
        source_db_path: Path,
        target_db_path: Path,
        copy_only: bool = False,
    ):
        super().__init__()
        self.gc_codes = gc_codes
        self.source_db_path = source_db_path
        self.target_db_path = target_db_path
        self.copy_only = copy_only

    def run(self) -> None:
        try:
            count = transfer_caches(
                self.gc_codes, self.source_db_path, self.target_db_path,
                copy_only=self.copy_only,
            )
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.finished.emit(count)


class MoveCachesDialog(QDialog):
    """Dialog that lets the user move or copy caches to another database."""

    caches_moved = Signal()

    def __init__(
        self,
        parent,
        selected_gc_code: Optional[str],
        flagged_gc_codes: list[str],
        all_gc_codes: list[str],
        copy_only: bool = False,
    ):
        super().__init__(parent)
        self._copy_only = copy_only
        self._title_key = "copy_caches_title" if copy_only else "move_caches_title"
        self.setWindowTitle(tr(self._title_key))
        self.setMinimumWidth(380)

        self._selected_gc_code = selected_gc_code
        self._flagged_gc_codes = flagged_gc_codes
        self._all_gc_codes = all_gc_codes
        self._worker: Optional[_MoveWorker] = None

        self._setup_ui()
        self._populate_db_combo()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── Scope selection ───────────────────────────────────────────────
        scope_lbl = QLabel(tr("move_caches_scope_label"))
        layout.addWidget(scope_lbl)

        self._scope_group = QButtonGroup(self)

        self._radio_selected = QRadioButton(
            tr("move_caches_scope_selected", gc_code=self._selected_gc_code or "—")
        )
        self._radio_selected.setEnabled(self._selected_gc_code is not None)
        self._scope_group.addButton(self._radio_selected, 0)
        layout.addWidget(self._radio_selected)

        self._radio_flagged = QRadioButton(
            tr("move_caches_scope_flagged", count=len(self._flagged_gc_codes))
        )
        self._radio_flagged.setEnabled(len(self._flagged_gc_codes) > 0)
        self._scope_group.addButton(self._radio_flagged, 1)
        layout.addWidget(self._radio_flagged)

        self._radio_all = QRadioButton(
            tr("move_caches_scope_all", count=len(self._all_gc_codes))
        )
        self._radio_all.setEnabled(len(self._all_gc_codes) > 0)
        self._scope_group.addButton(self._radio_all, 2)
        layout.addWidget(self._radio_all)

        # Default to selected if available, else flagged, else all
        if self._selected_gc_code:
            self._radio_selected.setChecked(True)
        elif self._flagged_gc_codes:
            self._radio_flagged.setChecked(True)
        else:
            self._radio_all.setChecked(True)

        # ── Target database ───────────────────────────────────────────────
        db_row = QHBoxLayout()
        target_key = "copy_caches_target_label" if self._copy_only else "move_caches_target_label"
        db_lbl = QLabel(tr(target_key))
        db_row.addWidget(db_lbl)
        self._db_combo = QComboBox()
        self._db_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._db_combo.setMinimumWidth(180)
        db_row.addWidget(self._db_combo)
        db_row.addStretch()
        layout.addLayout(db_row)

        # ── Progress ──────────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # ── Buttons ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_key = "coord_conv_copy_btn" if self._copy_only else "move_caches_btn_move"
        self._move_btn = QPushButton(tr(btn_key))
        self._move_btn.clicked.connect(self._start_move)
        btn_row.addWidget(self._move_btn)

        self._close_btn = QPushButton(tr("close"))
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._close_btn)

        layout.addLayout(btn_row)

    def _populate_db_combo(self) -> None:
        from opensak.db.manager import get_db_manager

        manager = get_db_manager()
        active_path = manager.active_path
        self._db_combo.clear()
        first_other = -1
        for i, db in enumerate(manager.databases):
            label = db.name
            if db.path == active_path:
                label += f"  ({tr('move_caches_current_db')})"
            else:
                if first_other < 0:
                    first_other = i
            self._db_combo.addItem(label, userData=db.path)

        # Pre-select first database that is NOT the active one
        if first_other >= 0:
            self._db_combo.setCurrentIndex(first_other)

    def _get_gc_codes(self) -> list[str]:
        checked = self._scope_group.checkedId()
        if checked == 0:
            return [self._selected_gc_code] if self._selected_gc_code else []
        elif checked == 1:
            return list(self._flagged_gc_codes)
        else:
            return list(self._all_gc_codes)

    def _start_move(self) -> None:
        from opensak.db.manager import get_db_manager

        gc_codes = self._get_gc_codes()
        if not gc_codes:
            QMessageBox.information(
                self,
                tr(self._title_key),
                tr("move_caches_none_selected"),
            )
            return

        target_path = self._db_combo.currentData()
        manager = get_db_manager()

        if target_path == manager.active_path:
            QMessageBox.warning(
                self,
                tr(self._title_key),
                tr("move_caches_same_db"),
            )
            return

        confirm_key = "copy_caches_confirm" if self._copy_only else "move_caches_confirm"
        reply = QMessageBox.question(
            self,
            tr(self._title_key),
            tr(confirm_key, count=len(gc_codes),
               target=self._db_combo.currentText().strip()),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._move_btn.setEnabled(False)
        self._close_btn.setEnabled(False)
        self._progress.setVisible(True)

        source_path = manager.active_path
        if source_path is None:
            return

        self._worker = _MoveWorker(
            gc_codes=gc_codes,
            source_db_path=source_path,
            target_db_path=Path(target_path),
            copy_only=self._copy_only,
        )
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_finished(self, count: int) -> None:
        self._progress.setVisible(False)
        self._move_btn.setEnabled(True)
        self._close_btn.setEnabled(True)
        done_key = "copy_caches_done" if self._copy_only else "move_caches_done"
        QMessageBox.information(
            self,
            tr(self._title_key),
            tr(done_key, count=count),
        )
        self.caches_moved.emit()
        self.accept()

    def _on_error(self, message: str) -> None:
        self._progress.setVisible(False)
        self._move_btn.setEnabled(True)
        self._close_btn.setEnabled(True)
        QMessageBox.critical(
            self,
            tr(self._title_key),
            tr("move_caches_error", message=message),
        )


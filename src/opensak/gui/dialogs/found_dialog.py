"""
src/opensak/gui/dialogs/found_dialog.py — Dialog til at opdatere
'fundet' status baseret på en reference database (Mine Fund).
"""

from __future__ import annotations
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QTextEdit,
    QProgressBar, QGroupBox, QRadioButton,
    QFileDialog, QDialogButtonBox, QButtonGroup
)

from opensak.db.manager import get_db_manager
from opensak.lang import tr
from opensak.gui.theme import hint_style


class UpdateWorker(QThread):
    """Kører opdateringen i baggrundstråd."""
    finished = Signal(object)   # UpdateResult
    error    = Signal(str)

    def __init__(self, reference_path: Path):
        super().__init__()
        self.reference_path = reference_path

    def run(self) -> None:
        try:
            from opensak.db.found_updater import update_found_from_reference
            result = update_found_from_reference(self.reference_path)
            self.finished.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


class FoundUpdaterDialog(QDialog):
    """
    Dialog der lader brugeren opdatere 'fundet' status i den aktive
    database baseret på en reference database (f.eks. Mine Fund).
    """

    update_completed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("found_dialog_title"))
        self.setMinimumWidth(520)
        self._worker: UpdateWorker | None = None
        self._reference_path: Path | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── Forklaring ────────────────────────────────────────────────────────
        info = QLabel(tr("found_info"))
        info.setWordWrap(True)
        info.setStyleSheet("color: #444; font-size: 11px;")
        layout.addWidget(info)

        # ── Aktiv database ────────────────────────────────────────────────────
        manager = get_db_manager()
        active_name = manager.active.name if manager.active else tr("found_no_active_db")
        active_lbl = QLabel(f"<b>{tr('found_active_db')}</b> {active_name}")
        active_lbl.setStyleSheet("color: #1565c0;")
        layout.addWidget(active_lbl)

        # ── Vælg reference database ───────────────────────────────────────────
        ref_group = QGroupBox(tr("found_ref_group"))
        ref_layout = QVBoxLayout(ref_group)

        # Radio: vælg fra kendte databaser
        self._rb_known = QRadioButton(tr("found_rb_known"))
        self._rb_known.setChecked(True)
        ref_layout.addWidget(self._rb_known)

        self._db_combo = QComboBox()
        other_dbs = [
            db for db in manager.databases
            if db != manager.active and db.exists
        ]
        for db in other_dbs:
            self._db_combo.addItem(db.name, db.path)
        if not other_dbs:
            self._db_combo.addItem(tr("found_no_other_dbs"))
            self._db_combo.setEnabled(False)
        ref_layout.addWidget(self._db_combo)

        # Radio: vælg fil
        self._rb_file = QRadioButton(tr("found_rb_file"))
        ref_layout.addWidget(self._rb_file)

        file_row = QHBoxLayout()
        self._file_lbl = QLabel(tr("found_no_file"))
        self._file_lbl.setStyleSheet(hint_style(font_size=None))
        file_row.addWidget(self._file_lbl, stretch=1)
        browse_btn = QPushButton(tr("gps_browse"))
        browse_btn.clicked.connect(self._browse_file)
        file_row.addWidget(browse_btn)
        ref_layout.addLayout(file_row)

        # Radioknap gruppe
        self._btn_group = QButtonGroup()
        self._btn_group.addButton(self._rb_known, 0)
        self._btn_group.addButton(self._rb_file, 1)
        self._rb_known.toggled.connect(self._on_source_changed)

        layout.addWidget(ref_group)

        # ── Progress og resultat ──────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(130)
        self._log.setPlaceholderText(tr("found_log_placeholder"))
        layout.addWidget(self._log)

        # ── Knapper ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._update_btn = QPushButton(tr("found_update_btn"))
        self._update_btn.setStyleSheet("font-weight: bold;")
        self._update_btn.clicked.connect(self._start_update)
        btn_row.addWidget(self._update_btn)

        close_btn = QPushButton(tr("close"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _on_source_changed(self, checked: bool) -> None:
        self._db_combo.setEnabled(self._rb_known.isChecked())

    def _browse_file(self) -> None:
        from opensak.config import get_app_data_dir
        path, _ = QFileDialog.getOpenFileName(
            self, tr("found_browse_title"),
            str(get_app_data_dir()),
            tr("db_file_filter")
        )
        if path:
            self._reference_path = Path(path)
            self._file_lbl.setText(self._reference_path.name)
            self._file_lbl.setStyleSheet("")
            self._rb_file.setChecked(True)

    def _get_reference_path(self) -> Path | None:
        if self._rb_file.isChecked():
            return self._reference_path
        else:
            data = self._db_combo.currentData()
            return Path(data) if data else None

    def _start_update(self) -> None:
        ref_path = self._get_reference_path()
        if not ref_path:
            self._log.setPlainText(tr("found_select_ref_first"))
            return

        manager = get_db_manager()
        if manager.active and ref_path == manager.active.path:
            self._log.setPlainText(tr("found_same_db_error"))
            return

        self._update_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._log.setPlainText(tr("found_running_file", name=ref_path.name))

        self._worker = UpdateWorker(ref_path)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_finished(self, result) -> None:
        self._progress.setVisible(False)
        self._update_btn.setEnabled(True)

        lines = [
            tr("found_completed") + "\n",
            tr("found_summary", updated=result.updated, already=result.already,
               not_found=result.not_found),
        ]
        if result.errors:
            lines.append(f"\n{tr('found_errors')}")
            for e in result.errors:
                lines.append(f"  - {e}")

        self._log.setPlainText("\n".join(lines))

        if result.updated > 0:
            self.update_completed.emit()

    def _on_error(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._update_btn.setEnabled(True)
        self._log.setPlainText(f"✗ {tr('found_errors')}\n{msg}")

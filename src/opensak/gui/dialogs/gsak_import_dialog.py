"""
src/opensak/gui/dialogs/gsak_import_dialog.py — GSAK direct database import dialog.

Session 4 of #469: the GUI wrapper around ``import_gsak_db()``. Mirrors
``import_dialog.py``'s worker/threading pattern (single background QThread,
progress signal, log widget) but is single-file rather than multi-file, and
adds a one-time confirmation step (#472) when the source database contains
personal notes with embedded local images that can't be carried over.
"""

from __future__ import annotations
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QProgressBar,
    QTextEdit, QComboBox, QMessageBox,
)

from opensak.gui.settings import get_settings
from opensak.lang import tr


class GsakImportWorker(QThread):
    """Imports a single GSAK database in a background thread."""
    result_ready = Signal(object)   # GsakImportResult
    error        = Signal(str)      # error message
    progress     = Signal(int, int)  # (done, total)
    # Completion is reported via QThread.finished (see ImportWorker in
    # import_dialog.py for the rationale — never emit a custom "done" signal
    # from inside run() itself).

    def __init__(self, db3_path: Path, target_db_path: Path | None = None):
        super().__init__()
        self.db3_path = db3_path
        self.target_db_path = target_db_path  # None → use currently active DB

    def run(self) -> None:
        from opensak.db.database import get_session, init_db
        from opensak.db.manager import get_db_manager
        from opensak.importer.gsak_importer import import_gsak_db

        manager = get_db_manager()
        original_path = manager.active_path
        switched = (
            self.target_db_path is not None
            and self.target_db_path != original_path
        )
        if switched:
            init_db(db_path=self.target_db_path)

        try:
            with get_session() as session:
                result = import_gsak_db(
                    self.db3_path, session,
                    progress_cb=lambda done, total: self.progress.emit(done, total),
                )
            self.result_ready.emit(result)
        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())
        finally:
            if switched and original_path is not None:
                init_db(db_path=original_path)


class GsakImportDialog(QDialog):
    """Dialog for importing a single GSAK database (.zip backup or .db3 file)."""

    import_completed = Signal()   # emitted when the import created/updated at least one cache

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("gsak_import_dialog_title"))
        self.setMinimumWidth(540)
        self.setMinimumHeight(360)
        self._worker: GsakImportWorker | None = None
        self._selected_path: Path | None = None
        self._db_combo: QComboBox | None = None
        self._setup_ui()
        self._populate_db_combo()

    def _populate_db_combo(self) -> None:
        """Fill the database combo with all known databases; pre-select the active one."""
        if self._db_combo is None:
            return
        from opensak.db.manager import get_db_manager
        manager = get_db_manager()
        active_path = manager.active_path
        self._db_combo.clear()
        active_index = 0
        for i, db in enumerate(manager.databases):
            label = db.name
            if db.path == active_path:
                label += f"  {tr('import_target_db_active')}"
                active_index = i
            self._db_combo.addItem(label, userData=db.path)
        self._db_combo.setCurrentIndex(active_index)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        file_lbl = QLabel(tr("gsak_import_select_file_label"))
        layout.addWidget(file_lbl)

        file_row = QHBoxLayout()
        self._file_label = QLabel("")
        self._file_label.setStyleSheet("color: gray;")
        file_row.addWidget(self._file_label, stretch=1)
        self._browse_btn = QPushButton(tr("import_browse"))
        self._browse_btn.clicked.connect(self._browse)
        file_row.addWidget(self._browse_btn)
        layout.addLayout(file_row)

        # ── Database selector ─────────────────────────────────────────────────
        db_row = QHBoxLayout()
        db_lbl = QLabel(tr("import_target_db_label"))
        db_row.addWidget(db_lbl)
        self._db_combo = QComboBox()
        self._db_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._db_combo.setMinimumWidth(180)
        db_row.addWidget(self._db_combo)
        db_row.addStretch()
        layout.addLayout(db_row)

        # Import + Close row
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._import_btn = QPushButton(tr("import_start"))
        self._import_btn.setEnabled(False)
        self._import_btn.clicked.connect(self._start_import)
        btn_row.addWidget(self._import_btn)

        self._close_btn = QPushButton(tr("close"))
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)

        layout.addLayout(btn_row)

        # ── Progress ──────────────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # ── Result log ────────────────────────────────────────────────────────
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText(tr("import_log_placeholder"))
        layout.addWidget(self._log)

    # ── File selection ───────────────────────────────────────────────────────

    def set_path(self, path: Path) -> None:
        """Select a file (used by drag & drop from MainWindow)."""
        self._selected_path = path
        self._file_label.setText(path.name)
        self._file_label.setToolTip(str(path))
        self._import_btn.setEnabled(True)

    def _browse(self) -> None:
        settings = get_settings()
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            tr("gsak_import_browse_title"),
            settings.last_import_dir,
            tr("gsak_import_file_filter"),
        )
        if path_str:
            path = Path(path_str)
            settings.last_import_dir = str(path.parent)
            self.set_path(path)

    # ── Import ────────────────────────────────────────────────────────────────

    def _start_import(self) -> None:
        if self._selected_path is None:
            return

        from opensak.importer.gsak_importer import (
            find_gsak_db3_in_zip,
            scan_gsak_notes_for_embedded_images,
        )

        source_path = self._selected_path
        if source_path.suffix.lower() == ".zip":
            self._append_log(tr("gsak_import_extracting", name=source_path.name))
        try:
            db3_path = find_gsak_db3_in_zip(source_path)
        except ValueError:
            QMessageBox.critical(
                self, tr("gsak_import_dialog_title"),
                tr("gsak_import_no_db3_found", name=source_path.name),
            )
            return

        # ── Issue #472: warn once about embedded local images in notes ────────
        scan = scan_gsak_notes_for_embedded_images(db3_path)
        if scan["affected_notes"]:
            box = QMessageBox(self)
            box.setWindowTitle(tr("gsak_prescan_title"))
            box.setText(tr(
                "gsak_prescan_body",
                notes=scan["affected_notes"],
                images=scan["total_images"],
            ))
            continue_btn = box.addButton(tr("gsak_prescan_continue"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(continue_btn)
            box.exec()
            if box.clickedButton() is not continue_btn:
                return

        self._import_btn.setEnabled(False)
        self._browse_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._log.clear()
        self._append_log(tr("gsak_import_running", name=source_path.name))

        target_db_path = (
            self._db_combo.currentData()
            if self._db_combo is not None and self._db_combo.count() > 0
            else None
        )
        self._worker = GsakImportWorker(db3_path, target_db_path=target_db_path)
        self._worker.progress.connect(self._on_progress)
        self._worker.result_ready.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_done)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
        else:
            self._progress.setRange(0, 0)

    def _on_result(self, result) -> None:
        lines = [
            tr("import_complete", name=self._selected_path.name if self._selected_path else ""),
            f"  {tr('import_new_caches'):<28} {result.created}",
            f"  {tr('import_updated'):<28} {result.updated}",
            f"  {tr('import_waypoints'):<28} {result.waypoints}",
            f"  {tr('gsak_import_attributes'):<28} {result.attributes}",
            f"  {tr('gsak_import_logs'):<28} {result.logs}",
            f"  {tr('gsak_import_notes'):<28} {result.notes}",
            f"  {tr('gsak_import_note_images'):<28} {result.note_images_replaced}",
            f"  {tr('corrected_dialog_corrected'):<28} {result.corrected}",
            f"  {tr('import_skipped'):<28} {result.skipped}",
        ]
        if result.warnings:
            lines.append(tr("gsak_import_warnings_header", count=len(result.warnings)))
            for w in result.warnings[:10]:
                lines.append(f"    - {w}")
        if result.errors:
            lines.append(tr("import_errors_header", count=len(result.errors)))
            for e in result.errors[:5]:
                lines.append(f"    - {e}")

        self._append_log("\n".join(lines))

        if result.created > 0 or result.updated > 0:
            self.import_completed.emit()

    def _on_error(self, msg: str) -> None:
        self._append_log(f"{tr('import_failed')}\n{msg}")

    def _on_done(self) -> None:
        self._progress.setVisible(False)
        self._append_log(tr("gsak_import_done"))
        self._browse_btn.setEnabled(True)
        self._import_btn.setText(tr("import_again"))
        self._import_btn.setEnabled(True)

    def closeEvent(self, event) -> None:
        try:
            if self._worker and self._worker.isRunning():
                self._worker.wait()
        except RuntimeError:
            pass
        self._worker = None
        super().closeEvent(event)

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _append_log(self, text: str) -> None:
        current = self._log.toPlainText()
        separator = "\n" + ("─" * 40) + "\n" if current else ""
        self._log.setPlainText(current + separator + text)
        self._log.verticalScrollBar().setValue(
            self._log.verticalScrollBar().maximum()
        )

"""
Dialog for KML export to Google Maps / Google My Maps.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ...export.kml import export_kml
from ...gui.icon import OpenSAKMessageBox as QMessageBox
from ...lang import tr
from . import make_progress_cb


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _ExportWorker(QThread):
    finished = Signal(int)
    error    = Signal(str)
    progress = Signal(int, int)   # (done, total)

    def __init__(self, caches, output_path: str, include_waypoints: bool, include_found: bool):
        super().__init__()
        self._caches            = caches
        self._output_path       = output_path
        self._include_waypoints = include_waypoints
        self._include_found     = include_found

    def run(self) -> None:
        try:
            from ...db.database import reload_caches_full
            caches = reload_caches_full(self._caches)
            count = export_kml(
                caches,
                self._output_path,
                include_waypoints=self._include_waypoints,
                include_found=self._include_found,
                progress_cb=make_progress_cb(self.progress.emit),
            )
            self.finished.emit(count)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class KmlExportDialog(QDialog):
    def __init__(self, caches: list, parent=None) -> None:
        super().__init__(parent)
        self._caches = caches
        self._worker: _ExportWorker | None = None

        self.setWindowTitle(tr("action_kml_export").replace("...", ""))
        self.setMinimumWidth(480)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        count = len(self._caches)
        info = QLabel(tr("kml_dialog_info", count=count))
        info.setWordWrap(True)
        layout.addWidget(info)

        # Output file
        file_group = QGroupBox(tr("kml_dialog_save_as"))
        file_layout = QHBoxLayout(file_group)
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText(tr("kml_dialog_choose_path"))
        default_path = str(Path.home() / "opensak_export.kml")
        self._path_edit.setText(default_path)
        browse_btn = QPushButton(tr("kml_dialog_browse"))
        browse_btn.clicked.connect(self._browse)
        file_layout.addWidget(self._path_edit)
        file_layout.addWidget(browse_btn)
        layout.addWidget(file_group)

        # Options
        opt_group = QGroupBox(tr("kml_dialog_options"))
        opt_layout = QVBoxLayout(opt_group)

        self._cb_waypoints = QCheckBox(tr("kml_dialog_include_waypoints"))
        self._cb_waypoints.setChecked(True)
        opt_layout.addWidget(self._cb_waypoints)

        self._cb_found = QCheckBox(tr("kml_dialog_include_found"))
        self._cb_found.setChecked(True)
        opt_layout.addWidget(self._cb_found)

        layout.addWidget(opt_group)

        # How-to hint
        hint = QLabel(tr("kml_dialog_hint"))
        hint.setOpenExternalLinks(True)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # Buttons
        self._buttons = QDialogButtonBox()
        self._export_btn = self._buttons.addButton(tr("kml_dialog_export_btn"), QDialogButtonBox.ButtonRole.AcceptRole)
        self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self._start_export)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("kml_dialog_save_as"),
            self._path_edit.text(),
            tr("kml_dialog_file_filter"),
        )
        if path:
            if not path.lower().endswith(".kml"):
                path += ".kml"
            self._path_edit.setText(path)

    def _start_export(self) -> None:
        output_path = self._path_edit.text().strip()
        if not output_path:
            QMessageBox.warning(self, tr("kml_dialog_missing_path_title"), tr("kml_dialog_missing_path_msg"))
            return

        self._export_btn.setEnabled(False)
        self._reset_progress()
        self._progress.setVisible(True)

        self._worker = _ExportWorker(
            caches            = self._caches,
            output_path       = output_path,
            include_waypoints = self._cb_waypoints.isChecked(),
            include_found     = self._cb_found.isChecked(),
        )
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.progress.connect(self._on_progress)
        self._worker.start()

    def _reset_progress(self) -> None:
        """Reset the bar to the indeterminate "running" state."""
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)

    def _on_progress(self, done: int, total: int) -> None:
        """Switch to a determinate bar showing count and percentage."""
        if total <= 0:
            return
        self._progress.setRange(0, total)
        self._progress.setValue(done)
        self._progress.setFormat("%v / %m  (%p%)")
        self._progress.setTextVisible(True)

    def _on_finished(self, count: int) -> None:
        self._progress.setVisible(False)
        path = self._path_edit.text().strip()
        QMessageBox.information(
            self,
            tr("kml_dialog_done_title"),
            tr("kml_dialog_done_msg", count=count, path=path),
        )
        self.accept()

    def _on_error(self, message: str) -> None:
        self._progress.setVisible(False)
        self._export_btn.setEnabled(True)
        QMessageBox.critical(self, tr("kml_dialog_error_title"), tr("kml_dialog_error_msg", message=message))

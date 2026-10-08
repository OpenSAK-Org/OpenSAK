"""
src/opensak/gui/dialogs/file_export_dialog.py — Export caches to GPX, LOC or GGZ file.

Simple dialog that lets the user choose a file format, a destination folder
and a file name (fixed or with variables, see
opensak.export.file_export_settings.expand_file_name), then writes the
selected format using the generators in opensak.gps.garmin.

The dialog options can be saved under a name and loaded again (see
opensak.export.file_export_settings); the options of the most recent export
are restored when the dialog opens.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore    import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QRadioButton,
    QButtonGroup, QGroupBox, QProgressBar,
    QTextEdit, QComboBox, QInputDialog, QSizePolicy,
    QCheckBox, QSpinBox, QFormLayout, QLineEdit,
    QToolButton, QStyle,
)

from opensak.lang import tr
from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.gui.dialogs import make_progress_cb
from opensak.export.file_export_settings import (
    DEFAULT_FILE_NAME, FileExportProfile, FileExportSettings,
    expand_file_name,
)
from opensak.export.file_export import (
    active_database_name, select_for_export, write_export_file,
)


class _ElidedLabel(QLabel):
    """Label that shortens its text in the middle ("…") to fit its width.

    A long export path would otherwise widen the whole dialog. The full text
    is available as the tooltip.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def full_text(self) -> str:
        return self._full_text

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        self.setText(self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideMiddle, max(self.width(), 1)
        ))


# ── Background worker ─────────────────────────────────────────────────────────

class _ExportWorker(QThread):
    finished = Signal(str)        # success message
    error    = Signal(str)        # error message
    progress = Signal(int, int)   # (done, total)

    def __init__(self, caches: list, output_path: Path, fmt: str,
                 use_corrected: bool = True):
        super().__init__()
        self._caches        = caches
        self._output_path   = output_path
        self._fmt           = fmt          # "gpx" | "loc" | "ggz"
        self._use_corrected = use_corrected

    def run(self) -> None:
        try:
            count = write_export_file(
                self._caches, self._output_path, self._fmt,
                use_corrected=self._use_corrected,
                progress_cb=make_progress_cb(self.progress.emit),
            )
            self.finished.emit(
                tr("file_export_done_msg").format(
                    count=count, path=str(self._output_path)
                )
            )
        except Exception as exc:
            import traceback
            self.error.emit(traceback.format_exc())


# ── Dialog ────────────────────────────────────────────────────────────────────

class FileExportDialog(QDialog):
    """Dialog for exporting filtered caches to GPX, LOC or GGZ format."""

    def __init__(self, caches: list, parent=None, filter_name: str = "",
                 center_name: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("file_export_dialog_title"))
        self.setMinimumWidth(480)
        self._caches = caches
        self._filter_name = filter_name   # active saved filter ("" = none)
        self._center_name = center_name   # active centre point ("" = none)
        self._worker: _ExportWorker | None = None
        self._output_path = ""
        self._setup_ui()
        self._apply_settings(FileExportProfile.load_last_used())

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Info label
        count = len([c for c in self._caches if c.latitude is not None])
        info = QLabel(tr("file_export_cache_count").format(count=count))
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info)

        # Format selector
        fmt_group = QGroupBox(tr("file_export_format_label"))
        fmt_layout = QVBoxLayout(fmt_group)

        self._btn_gpx = QRadioButton("GPX  —  " + tr("file_export_fmt_gpx_desc"))
        self._btn_loc = QRadioButton("LOC  —  " + tr("file_export_fmt_loc_desc"))
        self._btn_ggz = QRadioButton("GGZ  —  " + tr("file_export_fmt_ggz_desc"))
        self._btn_gpx.setChecked(True)

        self._fmt_grp = QButtonGroup(self)
        self._fmt_grp.addButton(self._btn_gpx)
        self._fmt_grp.addButton(self._btn_loc)
        self._fmt_grp.addButton(self._btn_ggz)

        fmt_layout.addWidget(self._btn_gpx)
        fmt_layout.addWidget(self._btn_loc)
        fmt_layout.addWidget(self._btn_ggz)
        layout.addWidget(fmt_group)

        # Export options
        opt_group = QGroupBox(tr("gps_opt_group"))
        opt_layout = QFormLayout(opt_group)
        folder_row = QHBoxLayout()
        self._edit_folder = QLineEdit()
        self._edit_folder.setPlaceholderText(tr("file_export_folder_placeholder"))
        folder_row.addWidget(self._edit_folder, 1)
        btn_browse = QPushButton(tr("kml_dialog_browse"))
        btn_browse.setAutoDefault(False)
        btn_browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(btn_browse)
        opt_layout.addRow(tr("file_export_folder"), folder_row)
        name_row = QHBoxLayout()
        self._edit_file_name = QLineEdit()
        self._edit_file_name.setPlaceholderText(DEFAULT_FILE_NAME)
        self._edit_file_name.setToolTip(tr("file_export_file_name_help"))
        name_row.addWidget(self._edit_file_name, 1)
        self._btn_name_help = QToolButton()
        self._btn_name_help.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)
        )
        self._btn_name_help.setAutoRaise(True)
        self._btn_name_help.setToolTip(tr("file_export_file_name_help"))
        self._btn_name_help.clicked.connect(self._show_file_name_help)
        name_row.addWidget(self._btn_name_help)
        opt_layout.addRow(tr("file_export_file_name"), name_row)
        self._lbl_file_name_preview = _ElidedLabel()
        opt_layout.addRow("", self._lbl_file_name_preview)
        self._combo_if_exists = QComboBox()
        for value, key in (
            ("ask", "file_export_if_exists_ask"),
            ("overwrite", "gsak_import_existing_overwrite"),
            ("skip", "gsak_import_existing_skip"),
        ):
            self._combo_if_exists.addItem(tr(key), value)
        opt_layout.addRow(tr("file_export_if_exists"), self._combo_if_exists)
        self._chk_corrected = QCheckBox(tr("file_export_use_corrected"))
        self._chk_corrected.setChecked(True)
        opt_layout.addRow(self._chk_corrected)
        self._spin_max = QSpinBox()
        self._spin_max.setRange(0, 1_000_000)
        self._spin_max.setSpecialValueText(tr("file_export_max_records_all"))
        self._spin_max.setToolTip(tr("file_export_max_records_tip"))
        opt_layout.addRow(tr("file_export_max_records"), self._spin_max)
        self._edit_folder.textChanged.connect(self._update_file_name_preview)
        self._edit_file_name.textChanged.connect(self._update_file_name_preview)
        self._fmt_grp.buttonToggled.connect(self._update_file_name_preview)
        self._spin_max.valueChanged.connect(self._update_file_name_preview)
        layout.addWidget(opt_group)

        # Saved settings
        settings_group = QGroupBox(tr("file_export_settings_label"))
        settings_row = QHBoxLayout(settings_group)
        self._settings_combo = QComboBox()
        self._settings_combo.setMinimumWidth(200)
        self._settings_combo.blockSignals(True)
        self._load_profiles_into_combo()
        self._settings_combo.blockSignals(False)
        self._settings_combo.currentIndexChanged.connect(self._on_profile_selected)
        settings_row.addWidget(self._settings_combo, 1)

        save_btn = QPushButton(tr("save"))
        save_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        save_btn.setAutoDefault(False)
        save_btn.clicked.connect(self._save_profile)
        settings_row.addWidget(save_btn)

        self._del_btn = QPushButton(tr("delete"))
        self._del_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._del_btn.setAutoDefault(False)
        self._del_btn.setEnabled(False)
        self._del_btn.clicked.connect(self._delete_profile)
        settings_row.addWidget(self._del_btn)
        layout.addWidget(settings_group)

        # Progress / log area
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(100)
        self._log.setVisible(False)
        layout.addWidget(self._log)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)   # indeterminate
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # Buttons
        btn_row = QHBoxLayout()
        self._btn_export = QPushButton(tr("file_export_btn_export"))
        self._btn_export.setDefault(True)
        self._btn_export.clicked.connect(self._do_export)

        btn_close = QPushButton(tr("close"))
        btn_close.clicked.connect(self.reject)

        btn_row.addStretch()
        btn_row.addWidget(self._btn_export)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    # ── Settings ──────────────────────────────────────────────────────────────

    def _collect_settings(self) -> FileExportSettings:
        return FileExportSettings(
            fmt=self._current_fmt(),
            output_path=self._output_path,
            use_corrected_coords=self._chk_corrected.isChecked(),
            max_records=self._spin_max.value(),
            file_name=self._edit_file_name.text().strip(),
            folder=self._edit_folder.text().strip(),
            if_exists=self._combo_if_exists.currentData(),
        )

    def _apply_settings(self, settings: FileExportSettings) -> None:
        {
            "gpx": self._btn_gpx,
            "loc": self._btn_loc,
            "ggz": self._btn_ggz,
        }.get(settings.fmt, self._btn_gpx).setChecked(True)
        self._output_path = settings.output_path
        self._chk_corrected.setChecked(settings.use_corrected_coords)
        self._spin_max.setValue(settings.max_records)
        self._edit_folder.setText(settings.folder)
        self._combo_if_exists.setCurrentIndex(
            max(0, self._combo_if_exists.findData(settings.if_exists))
        )
        self._edit_file_name.setText(settings.file_name)
        self._update_file_name_preview()

    # ── File name ─────────────────────────────────────────────────────────────

    @staticmethod
    def _database_name() -> str:
        """Name of the active database, or "" when there is none."""
        return active_database_name()

    def _export_count(self) -> int:
        """Number of caches the export will write (with the record limit)."""
        return len(select_for_export(self._caches, self._spin_max.value()))

    def _expanded_file_name(self) -> str:
        """File name (with extension) the template currently stands for."""
        fmt = self._current_fmt()
        name = expand_file_name(
            self._edit_file_name.text(),
            database=self._database_name(),
            filter_name=self._filter_name,
            center_name=self._center_name,
            fmt=fmt,
            count=self._export_count(),
        )
        return f"{name}.{fmt}"

    def _output_preview(self) -> str:
        """The file the export will write, as far as it is known yet."""
        folder = self._edit_folder.text().strip()
        name = self._expanded_file_name()
        return str(Path(folder) / name) if folder else name

    def _update_file_name_preview(self, *_args) -> None:
        self._lbl_file_name_preview.set_full_text(
            tr("file_export_file_name_preview", name=self._output_preview())
        )

    def _show_file_name_help(self) -> None:
        QMessageBox.information(
            self, tr("file_export_file_name_help_title"),
            tr("file_export_file_name_help"),
        )

    def _browse_folder(self) -> bool:
        """Let the user pick the export folder. Returns False when cancelled."""
        start = self._edit_folder.text().strip()
        if not start and self._output_path:
            start = str(Path(self._output_path).parent)
        folder = QFileDialog.getExistingDirectory(
            self, tr("file_export_folder_dialog_title"), start,
        )
        if not folder:
            return False
        self._edit_folder.setText(str(Path(folder)))
        return True

    def _load_profiles_into_combo(self) -> None:
        self._settings_combo.clear()
        self._settings_combo.addItem(tr("file_export_settings_last_used"), None)
        for path in FileExportProfile.list_profiles():
            try:
                self._settings_combo.addItem(FileExportProfile.load(path).name, path)
            except Exception:
                pass

    def _select_profile(self, name: str) -> None:
        for i in range(self._settings_combo.count()):
            if (self._settings_combo.itemData(i) is not None
                    and self._settings_combo.itemText(i) == name):
                self._settings_combo.setCurrentIndex(i)
                return

    def _on_profile_selected(self, index: int) -> None:
        path = self._settings_combo.currentData()
        self._del_btn.setEnabled(path is not None)
        try:
            if path is None:
                self._apply_settings(FileExportProfile.load_last_used())
            else:
                self._apply_settings(FileExportProfile.load(path).settings)
        except Exception as e:
            QMessageBox.warning(
                self, tr("error"), tr("file_export_settings_load_error", error=e)
            )

    def _save_profile(self) -> None:
        current = (
            self._settings_combo.currentText()
            if self._settings_combo.currentData() is not None
            else ""
        )
        name, ok = QInputDialog.getText(
            self, tr("file_export_settings_save_title"),
            tr("file_export_settings_name_label"), text=current,
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        if FileExportProfile.profile_path(name).exists():
            reply = QMessageBox.question(
                self, tr("file_export_settings_save_title"),
                tr("file_export_settings_overwrite_msg", name=name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        FileExportProfile(name, self._collect_settings()).save()
        self._settings_combo.blockSignals(True)
        self._load_profiles_into_combo()
        self._select_profile(name)
        self._settings_combo.blockSignals(False)
        self._del_btn.setEnabled(self._settings_combo.currentData() is not None)

    def _delete_profile(self) -> None:
        path = self._settings_combo.currentData()
        if path is None:
            return
        name = self._settings_combo.currentText()
        reply = QMessageBox.question(
            self, tr("delete"),
            tr("file_export_settings_delete_msg", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            Path(path).unlink()
        except OSError:
            pass
        # Keep the options currently shown — only the stored copy is gone.
        self._settings_combo.blockSignals(True)
        self._load_profiles_into_combo()
        self._settings_combo.blockSignals(False)
        self._del_btn.setEnabled(False)

    # ── Logic ─────────────────────────────────────────────────────────────────

    def _current_fmt(self) -> str:
        if self._btn_loc.isChecked():
            return "loc"
        if self._btn_ggz.isChecked():
            return "ggz"
        return "gpx"

    def _do_export(self) -> None:
        fmt = self._current_fmt()
        # No folder chosen yet — ask once; the choice is kept in the settings.
        if not self._edit_folder.text().strip() and not self._browse_folder():
            return

        output_path = Path(self._edit_folder.text().strip()) / self._expanded_file_name()
        if output_path.exists():
            if_exists = self._combo_if_exists.currentData()
            if if_exists == "skip":
                self._log.setVisible(True)
                self._log.setPlainText(
                    "– " + tr("file_export_skipped_msg", path=str(output_path))
                )
                return
            if if_exists == "ask":
                reply = QMessageBox.question(
                    self, tr("gps_file_exists_title"),
                    tr("file_export_overwrite_msg", path=str(output_path)),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        self._output_path = str(output_path)
        try:
            FileExportProfile.save_last_used(self._collect_settings())
        except OSError:
            pass  # failing to remember the settings must not block the export

        self._log.clear()
        self._log.setVisible(True)
        self._reset_progress()
        self._progress.setVisible(True)
        self._btn_export.setEnabled(False)

        # Only caches with coordinates are exported, so the limit counts those.
        caches = select_for_export(self._caches, self._spin_max.value())

        self._worker = _ExportWorker(
            caches, output_path, fmt,
            use_corrected=self._chk_corrected.isChecked(),
        )
        self._worker.finished.connect(self._on_success)
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

    def _on_success(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._btn_export.setEnabled(True)
        self._log.setPlainText("✓ " + msg)

    def _on_error(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._btn_export.setEnabled(True)
        self._log.setPlainText("✗ " + msg)
        QMessageBox.critical(self, tr("file_export_error_title"), msg)

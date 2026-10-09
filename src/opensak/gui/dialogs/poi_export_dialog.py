"""
src/opensak/gui/dialogs/poi_export_dialog.py — export caches and child
waypoints as Garmin POI files (.gpi).

Modelled on GSAK's Garmin POI dialog: which points (caches, child
waypoints, by flag), the POI texts as templates with {variables}, the
category, a proximity alert and an icon. The folder and file name work as
in the file export dialog, and the options can be saved under a name —
Lua macros export with such a saved setting (opensak.export_poi).

The caches are loaded and turned into POIs on a worker thread
(opensak.export.poi_export.plan_poi_export); the files are written back on
the GUI thread, after asking about existing files.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QProgressBar,
    QPushButton, QRadioButton, QSpinBox, QStyle, QTextEdit, QToolButton,
    QVBoxLayout,
)

from opensak.export.file_export import active_database_name, select_for_export
from opensak.export.file_export_settings import DEFAULT_FILE_NAME, expand_file_name
from opensak.export.poi_export import (
    PoiFile, plan_poi_export, settings_icon_path, write_poi_files,
)
from opensak.export.poi_export_settings import (
    PROXIMITY_UNITS, PoiExportProfile, PoiExportSettings,
)
from opensak.gui.dialogs.file_export_dialog import SavedSettingsDialog, _ElidedLabel
from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.lang import tr


# ── Background worker ─────────────────────────────────────────────────────────

class _PlanWorker(QThread):
    planned = Signal(list)    # list[PoiFile]
    error = Signal(str)

    def __init__(self, caches: list, settings: PoiExportSettings, folder: Path,
                 database: str, filter_name: str, center_name: str):
        super().__init__()
        self._caches = caches
        self._settings = settings
        self._folder = folder
        self._names = dict(database=database, filter_name=filter_name,
                           center_name=center_name)

    def run(self) -> None:
        try:
            self.planned.emit(plan_poi_export(
                self._caches, self._settings, self._folder, **self._names,
            ))
        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())


# ── Dialog ────────────────────────────────────────────────────────────────────

class PoiExportDialog(SavedSettingsDialog):
    """Dialog for exporting filtered caches as Garmin POI files."""

    _profile_cls = PoiExportProfile

    def __init__(self, caches: list, parent=None, filter_name: str = "",
                 center_name: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("poi_export_dialog_title"))
        self.setMinimumWidth(560)
        self._caches = caches
        self._filter_name = filter_name   # active saved filter ("" = none)
        self._center_name = center_name   # active centre point ("" = none)
        self._worker: _PlanWorker | None = None
        self._setup_ui()
        self._apply_settings(PoiExportProfile.load_last_used())

    # ── UI ────────────────────────────────────────────────────────────────────

    def _help_button(self, slot) -> QToolButton:
        btn = QToolButton()
        btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))
        btn.setAutoRaise(True)
        btn.clicked.connect(slot)
        return btn

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        count = len([c for c in self._caches if c.latitude is not None])
        info = QLabel(tr("file_export_cache_count").format(count=count))
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info)

        layout.addWidget(self._build_file_group())
        layout.addWidget(self._build_points_group())
        layout.addWidget(self._build_details_group())
        layout.addWidget(self._build_settings_group())

        # Progress / log area
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(100)
        self._log.setVisible(False)
        layout.addWidget(self._log)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)   # indeterminate
        self._progress.setTextVisible(False)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

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

    def _build_file_group(self) -> QGroupBox:
        group = QGroupBox(tr("gps_result_file"))
        form = QFormLayout(group)

        folder_row = QHBoxLayout()
        self._edit_folder = QLineEdit()
        self._edit_folder.setPlaceholderText(tr("file_export_folder_placeholder"))
        folder_row.addWidget(self._edit_folder, 1)
        btn_browse = QPushButton(tr("kml_dialog_browse"))
        btn_browse.setAutoDefault(False)
        btn_browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(btn_browse)
        form.addRow(tr("file_export_folder"), folder_row)

        name_row = QHBoxLayout()
        self._edit_file_name = QLineEdit()
        self._edit_file_name.setPlaceholderText(DEFAULT_FILE_NAME)
        self._edit_file_name.setToolTip(tr("file_export_file_name_help"))
        name_row.addWidget(self._edit_file_name, 1)
        btn_help = self._help_button(self._show_file_name_help)
        btn_help.setToolTip(tr("file_export_file_name_help"))
        name_row.addWidget(btn_help)
        form.addRow(tr("file_export_file_name"), name_row)
        self._lbl_file_name_preview = _ElidedLabel()
        form.addRow("", self._lbl_file_name_preview)

        self._combo_if_exists = QComboBox()
        for value, key in (
            ("ask", "file_export_if_exists_ask"),
            ("overwrite", "gsak_import_existing_overwrite"),
            ("skip", "gsak_import_existing_skip"),
        ):
            self._combo_if_exists.addItem(tr(key), value)
        form.addRow(tr("file_export_if_exists"), self._combo_if_exists)

        self._edit_folder.textChanged.connect(self._update_file_name_preview)
        self._edit_file_name.textChanged.connect(self._update_file_name_preview)
        return group

    def _build_points_group(self) -> QGroupBox:
        group = QGroupBox(tr("filter_lp_type_points"))
        form = QFormLayout(group)

        self._chk_waypoints = QCheckBox(tr("poi_export_include_waypoints"))
        self._chk_waypoints_only = QCheckBox(tr("poi_export_waypoints_only"))
        wp_row = QHBoxLayout()
        wp_row.addWidget(self._chk_waypoints)
        wp_row.addWidget(self._chk_waypoints_only)
        wp_row.addStretch()
        form.addRow(wp_row)

        flag_row = QHBoxLayout()
        self._flag_grp = QButtonGroup(self)
        self._flag_buttons: dict[str, QRadioButton] = {}
        for value, key in (
            ("all", "poi_export_flag_all"),
            ("flagged", "poi_export_flag_flagged"),
            ("unflagged", "poi_export_flag_unflagged"),
        ):
            btn = QRadioButton(tr(key))
            self._flag_grp.addButton(btn)
            self._flag_buttons[value] = btn
            flag_row.addWidget(btn)
        flag_row.addStretch()
        form.addRow(tr("poi_export_flag_label"), flag_row)

        self._chk_split = QCheckBox(tr("poi_export_split_by_type"))
        self._chk_split.setToolTip(tr("poi_export_split_by_type_tip"))
        form.addRow(self._chk_split)

        self._chk_corrected = QCheckBox(tr("file_export_use_corrected"))
        form.addRow(self._chk_corrected)

        self._spin_max = QSpinBox()
        self._spin_max.setRange(0, 1_000_000)
        self._spin_max.setSpecialValueText(tr("file_export_max_records_all"))
        self._spin_max.setToolTip(tr("poi_export_max_points_tip"))
        form.addRow(tr("poi_export_max_points"), self._spin_max)

        self._chk_waypoints.toggled.connect(self._update_enabled)
        self._chk_waypoints_only.toggled.connect(self._update_enabled)
        self._chk_split.toggled.connect(self._update_file_name_preview)
        return group

    def _build_details_group(self) -> QGroupBox:
        group = QGroupBox(tr("poi_export_details_group"))
        form = QFormLayout(group)

        name_row = QHBoxLayout()
        self._edit_name = QLineEdit()
        name_row.addWidget(self._edit_name, 1)
        name_row.addWidget(QLabel(tr("poi_export_smart_length")))
        self._spin_smart = QSpinBox()
        self._spin_smart.setRange(1, 255)
        self._spin_smart.setToolTip(tr("poi_export_smart_length_tip"))
        name_row.addWidget(self._spin_smart)
        name_row.addWidget(self._help_button(self._show_variables_help))
        form.addRow(tr("settings_hp_name_label"), name_row)

        self._edit_description = QLineEdit()
        form.addRow(tr("poi_export_description"), self._edit_description)

        extra_row = QHBoxLayout()
        self._edit_extra = QLineEdit()
        extra_row.addWidget(self._edit_extra, 1)
        self._btn_phone = QRadioButton(tr("poi_export_extra_phone"))
        self._btn_address = QRadioButton(tr("poi_export_extra_address"))
        self._extra_grp = QButtonGroup(self)
        self._extra_grp.addButton(self._btn_phone)
        self._extra_grp.addButton(self._btn_address)
        extra_row.addWidget(self._btn_phone)
        extra_row.addWidget(self._btn_address)
        form.addRow(tr("poi_export_extra"), extra_row)

        for edit in (self._edit_name, self._edit_description, self._edit_extra):
            edit.setToolTip(tr("poi_export_variables_help"))

        self._edit_category = QLineEdit()
        self._edit_category.setToolTip(tr("poi_export_category_tip"))
        form.addRow(tr("poi_export_category"), self._edit_category)

        prox_row = QHBoxLayout()
        self._spin_proximity = QDoubleSpinBox()
        self._spin_proximity.setRange(0, 100_000)
        self._spin_proximity.setDecimals(2)
        self._spin_proximity.setSpecialValueText(tr("poi_export_proximity_none"))
        prox_row.addWidget(self._spin_proximity)
        self._combo_unit = QComboBox()
        for unit in PROXIMITY_UNITS:
            self._combo_unit.addItem(unit, unit)
        prox_row.addWidget(self._combo_unit)
        prox_row.addStretch()
        form.addRow(tr("poi_export_proximity"), prox_row)

        icon_row = QHBoxLayout()
        self._edit_icon = QLineEdit()
        self._edit_icon.setPlaceholderText(tr("poi_export_icon_placeholder"))
        self._edit_icon.setToolTip(tr("poi_export_icon_tip"))
        icon_row.addWidget(self._edit_icon, 1)
        btn_icon = QPushButton(tr("kml_dialog_browse"))
        btn_icon.setAutoDefault(False)
        btn_icon.clicked.connect(self._browse_icon)
        icon_row.addWidget(btn_icon)
        form.addRow(tr("poi_export_icon"), icon_row)
        return group

    def _update_enabled(self, *_args) -> None:
        only = self._chk_waypoints_only.isChecked()
        self._chk_waypoints.setEnabled(not only)
        uses_waypoints = only or self._chk_waypoints.isChecked()
        for btn in self._flag_buttons.values():
            btn.setEnabled(uses_waypoints)
        self._chk_split.setEnabled(uses_waypoints)

    # ── Settings ──────────────────────────────────────────────────────────────

    def _collect_settings(self) -> PoiExportSettings:
        flag = next(
            (v for v, b in self._flag_buttons.items() if b.isChecked()), "all"
        )
        return PoiExportSettings(
            folder=self._edit_folder.text().strip(),
            file_name=self._edit_file_name.text().strip(),
            if_exists=self._combo_if_exists.currentData(),
            use_corrected_coords=self._chk_corrected.isChecked(),
            include_waypoints=self._chk_waypoints.isChecked(),
            waypoints_only=self._chk_waypoints_only.isChecked(),
            waypoint_flag=flag,
            split_by_type=self._chk_split.isChecked(),
            max_points=self._spin_max.value(),
            name=self._edit_name.text(),
            smart_length=self._spin_smart.value(),
            description=self._edit_description.text(),
            extra=self._edit_extra.text(),
            extra_field="address" if self._btn_address.isChecked() else "phone",
            category=self._edit_category.text().strip(),
            proximity=self._spin_proximity.value(),
            proximity_unit=self._combo_unit.currentData(),
            icon=self._edit_icon.text().strip(),
        )

    def _apply_settings(self, settings: PoiExportSettings) -> None:
        self._edit_folder.setText(settings.folder)
        self._edit_file_name.setText(settings.file_name)
        self._combo_if_exists.setCurrentIndex(
            max(0, self._combo_if_exists.findData(settings.if_exists))
        )
        self._chk_corrected.setChecked(settings.use_corrected_coords)
        self._chk_waypoints.setChecked(settings.include_waypoints)
        self._chk_waypoints_only.setChecked(settings.waypoints_only)
        self._flag_buttons.get(settings.waypoint_flag, self._flag_buttons["all"]).setChecked(True)
        self._chk_split.setChecked(settings.split_by_type)
        self._spin_max.setValue(settings.max_points)
        self._edit_name.setText(settings.name)
        self._spin_smart.setValue(settings.smart_length)
        self._edit_description.setText(settings.description)
        self._edit_extra.setText(settings.extra)
        (self._btn_address if settings.extra_field == "address" else self._btn_phone).setChecked(True)
        self._edit_category.setText(settings.category)
        self._spin_proximity.setValue(settings.proximity)
        self._combo_unit.setCurrentIndex(max(0, self._combo_unit.findData(settings.proximity_unit)))
        self._edit_icon.setText(settings.icon)
        self._update_enabled()
        self._update_file_name_preview()

    # ── File name ─────────────────────────────────────────────────────────────

    def _expanded_file_name(self) -> str:
        """File name of the main file (with extension). {count} is the
        number of caches, as the number of POIs is only known later."""
        name = expand_file_name(
            self._edit_file_name.text(),
            database=active_database_name(),
            filter_name=self._filter_name,
            center_name=self._center_name,
            fmt="gpi",
            count=len(select_for_export(self._caches)),
        )
        return f"{name}.gpi"

    def _update_file_name_preview(self, *_args) -> None:
        folder = self._edit_folder.text().strip()
        name = self._expanded_file_name()
        preview = str(Path(folder) / name) if folder else name
        if self._chk_split.isChecked() and self._chk_split.isEnabled():
            preview += " " + tr("poi_export_preview_split")
        self._lbl_file_name_preview.set_full_text(
            tr("file_export_file_name_preview", name=preview)
        )

    def _show_file_name_help(self) -> None:
        QMessageBox.information(
            self, tr("file_export_file_name_help_title"), tr("file_export_file_name_help"),
        )

    def _show_variables_help(self) -> None:
        QMessageBox.information(
            self, tr("poi_export_variables_help_title"), tr("poi_export_variables_help"),
        )

    def _browse_folder(self) -> bool:
        """Let the user pick the export folder. Returns False when cancelled."""
        folder = QFileDialog.getExistingDirectory(
            self, tr("file_export_folder_dialog_title"), self._edit_folder.text().strip(),
        )
        if not folder:
            return False
        self._edit_folder.setText(str(Path(folder)))
        return True

    def _browse_icon(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("poi_export_icon_dialog_title"), self._edit_icon.text().strip(),
            tr("poi_export_icon_filter"),
        )
        if path:
            self._edit_icon.setText(str(Path(path)))

    # ── Export ────────────────────────────────────────────────────────────────

    def _do_export(self) -> None:
        # No folder chosen yet — ask once; the choice is kept in the settings.
        if not self._edit_folder.text().strip() and not self._browse_folder():
            return
        settings = self._collect_settings()
        try:
            PoiExportProfile.save_last_used(settings)
        except OSError:
            pass  # failing to remember the settings must not block the export

        self._log.clear()
        self._log.setVisible(True)
        self._progress.setVisible(True)
        self._btn_export.setEnabled(False)

        self._worker = _PlanWorker(
            select_for_export(self._caches), settings, Path(settings.folder),
            active_database_name(), self._filter_name, self._center_name,
        )
        self._worker.planned.connect(lambda files: self._write(files, settings))
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _keep_existing(self, files: list[PoiFile], if_exists: str) -> list[PoiFile]:
        """Drop the files that exist and must not be overwritten."""
        existing = [f.path for f in files if f.path.exists()]
        if not existing or if_exists == "overwrite":
            return files
        if if_exists == "ask":
            reply = QMessageBox.question(
                self, tr("gps_file_exists_title"),
                tr("poi_export_overwrite_msg", paths="\n".join(map(str, existing))),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                return files
        return [f for f in files if f.path not in existing]

    def _write(self, planned: list[PoiFile], settings: PoiExportSettings) -> None:
        self._progress.setVisible(False)
        self._btn_export.setEnabled(True)
        if not planned:
            self._log.setPlainText("– " + tr("poi_export_nothing_msg"))
            return
        files = self._keep_existing(planned, settings.if_exists)
        kept = {f.path for f in files}
        lines = [
            "– " + tr("file_export_skipped_msg", path=str(f.path))
            for f in planned if f.path not in kept
        ]
        try:
            written = write_poi_files(files, settings_icon_path(settings)) if files else []
        except (OSError, ValueError) as exc:
            self._on_error(str(exc))
            return
        lines += [
            "✓ " + tr("poi_export_done_msg", count=n, path=str(path))
            for path, n in written
        ]
        self._log.setPlainText("\n".join(lines))

    def _on_error(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._btn_export.setEnabled(True)
        self._log.setPlainText("✗ " + msg)
        QMessageBox.critical(self, tr("file_export_error_title"), msg)

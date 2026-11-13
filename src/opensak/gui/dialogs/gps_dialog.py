"""
src/opensak/gui/dialogs/gps_dialog.py — GPS export dialog.

Finder automatisk tilsluttede Garmin enheder og eksporterer
valgte/filtrerede caches som GPX eller GGZ fil direkte til enheden.
"""

from __future__ import annotations
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from opensak.lang import tr
from opensak.gui.theme import hint_style
from opensak.gui.dialogs import make_progress_cb
from opensak.settings_store import get_store
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QTextEdit,
    QProgressBar, QGroupBox, QRadioButton,
    QFileDialog, QButtonGroup, QSpinBox,
    QCheckBox, QMessageBox, QInputDialog,
    QScrollArea, QWidget, QSizePolicy
)


from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.gui.dialogs.widgets import WorkerGuardMixin, clamp_dialog_height_to_screen
from opensak.utils.cancel import CancelFlag, Cancelled

# Issue #656 (CheminerWill's follow-up suggestion): remember the "use
# database name as filename" checkbox state across exports/sessions.
_USE_DB_NAME_KEY = "gps.use_db_name_as_filename"
# #775: where Send to GPS takes its content options (corrected coordinates,
# children, logs, user note log, GPX version, what the limit counts) from:
# "" = the defaults (what Send to GPS always wrote before), "last:" = the
# settings of the last file export, "profile:<name>" = a saved export setting.
_CONTENT_SETTINGS_KEY = "gps.content_settings"
_CONTENT_DEFAULT = ""
_CONTENT_LAST = "last:"
_CONTENT_PROFILE = "profile:"


def _default_content_settings():
    """The file export settings Send to GPS uses with "Default" content:
    the file export's defaults, but without GSAK's own fields (#1169).
    Devices have no use for them, and a file for a device should carry
    nothing it doesn't need (see #656). A saved setting, or the last file
    export, keeps its own choice."""
    from opensak.export.file_export_settings import FileExportSettings

    return FileExportSettings(gsak_fields=False)


def _sanitize_db_name_for_filename(name: str) -> str:
    """Gør et database-navn sikkert at bruge som filnavn på tværs af
    Windows/Linux/macOS — erstatter ugyldige tegn (\\ / : * ? " < > |) med
    underscore, og trimmer omkringliggende whitespace/punktummer."""
    invalid_chars = '\\/:*?"<>|'
    cleaned = "".join("_" if c in invalid_chars else c for c in name)
    cleaned = cleaned.strip(" .")
    return cleaned or "opensak"


class DeleteWorker(QThread):
    """Kører sletning af eksisterende eksport-filer (GPX eller GGZ) i baggrundstråd."""
    finished = Signal(object)
    error    = Signal(str)

    def __init__(self, device_path: Path, export_format: str = "gpx"):
        super().__init__()
        self.device_path   = device_path
        self.export_format = export_format

    def run(self) -> None:
        try:
            from opensak.gps.garmin import (
                delete_gpx_files, get_garmin_gpx_path, get_garmin_ggz_path,
            )
            if self.export_format == "ggz":
                result = delete_gpx_files(
                    self.device_path,
                    pattern="*.ggz",
                    folder=get_garmin_ggz_path(self.device_path),
                )
            else:
                result = delete_gpx_files(self.device_path)  # default: *.gpx i Garmin/GPX
            self.finished.emit(result)
        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())


class ExportWorker(QThread):
    """Kører eksporten i baggrundstråd."""
    finished  = Signal(object)
    error     = Signal(str)
    progress  = Signal(int, int)   # (done, total)
    cancelled = Signal()           # stopped by cancel (#1265)

    def __init__(self, caches, device_path, filename, max_caches, export_format="gpx",
                 settings=None):
        super().__init__()
        self.caches        = caches
        self.device_path   = device_path
        self.filename      = filename
        self.max_caches    = max_caches
        self.export_format = export_format   # "gpx" eller "ggz"
        self.cancel        = CancelFlag()
        # #775: the file export settings supplying the content options.
        self.settings      = settings if settings is not None else _default_content_settings()

    def _select(self) -> list:
        """The caches to send: max_caches of them, counted as the settings'
        max_counts says (#775)."""
        if self.max_caches <= 0:
            return self.caches
        if self.settings.max_counts != "waypoints":
            return self.caches[:self.max_caches]
        from opensak.export.file_export import select_for_export
        return select_for_export(
            self.caches, self.max_caches, "waypoints", self.export_format,
            self.settings.content(),
        )

    def run(self) -> None:
        try:
            from opensak.db.database import iter_caches_full
            from opensak.gps.garmin import gpx_bounds, is_mtp_device
            selected = self._select()
            content = self.settings.content().for_format(self.export_format)
            use_corrected = self.settings.use_corrected_coords
            # #1075: reloaded and written a chunk at a time, so a large
            # export never holds every fully loaded cache in memory.
            caches = iter_caches_full(selected)
            total = len(selected)
            # #700: the GPX <bounds>, needed before the first cache is written.
            bounds = gpx_bounds(selected, use_corrected, True, content)
            cb = make_progress_cb(self.progress.emit, self.cancel)
            options = {"content": content, "use_corrected": use_corrected}

            is_device = (
                is_mtp_device(self.device_path)
                or (
                    self.device_path.is_dir()
                    and (self.device_path / "Garmin").exists()
                )
            )

            if self.export_format == "ggz":
                from opensak.gps.garmin import export_ggz_to_device, export_ggz_to_file
                if is_device:
                    result = export_ggz_to_device(
                        caches, self.device_path, self.filename, progress_cb=cb, total=total, bounds=bounds,
                        **options,
                    )
                else:
                    result = export_ggz_to_file(
                        caches,
                        self.device_path / f"{self.filename}.ggz",
                        progress_cb=cb, total=total, bounds=bounds, **options,
                    )
            else:
                from opensak.gps.garmin import export_to_device, export_to_file
                if is_device:
                    result = export_to_device(
                        caches, self.device_path, self.filename, progress_cb=cb, total=total, bounds=bounds,
                        **options,
                    )
                else:
                    result = export_to_file(
                        caches,
                        self.device_path / f"{self.filename}.gpx",
                        progress_cb=cb, total=total, bounds=bounds, **options,
                    )

            self.finished.emit(result)
        except Cancelled:
            self.cancelled.emit()
        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())


class GpsExportDialog(WorkerGuardMixin, QDialog):
    """Dialog til at eksportere caches til GPS enhed."""

    def __init__(self, parent=None, caches=None):
        super().__init__(parent)
        self.setWindowTitle(tr("gps_dialog_title"))
        self.setMinimumWidth(520)
        # Issue #811: cap so this can't exceed the screen at high DPI
        # scaling — paired with wrapping dest_group + opt_group in a
        # QScrollArea in _setup_ui below, since neither group has any
        # scrolling of its own.
        clamp_dialog_height_to_screen(self, parent)
        self._caches        = caches or []
        self._worker        = None
        self._delete_worker = None
        self._cancel_requested = False   # #1265: while deleting old files
        self._export_format = "gpx"   # "gpx" eller "ggz"
        self._setup_ui()
        self._scan_devices()

    def _guarded_workers(self):
        return (self._worker, self._delete_worker)

    def _close_while_running(self) -> None:
        """#1265: Cancel, Escape and the window's close button stop the
        export (an MTP copy too). Deleting the old files can't be stopped;
        the export after it is then not started."""
        from opensak.gps.garmin import cancel_mtp_transfer
        self._cancel_requested = True
        if self._worker is not None:
            self._worker.cancel.set()
        cancel_mtp_transfer()
        self._close_btn.setEnabled(False)

    def _set_running(self, running: bool) -> None:
        """While sending, Close becomes Cancel (#1265)."""
        self._export_btn.setEnabled(not running)
        self._close_btn.setText(tr("cancel") if running else tr("close"))
        self._close_btn.setEnabled(True)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── Info ──────────────────────────────────────────────────────────────
        count_lbl = QLabel(tr("gps_caches_ready", count=len(self._caches)))
        layout.addWidget(count_lbl)

        # Issue #811: dest_group + opt_group go inside a scrollable
        # content widget below, so they can be reached even if the
        # dialog's screen-height cap (see __init__) leaves less room than
        # their combined sizeHint needs. Progress/log/buttons stay
        # outside the scroll area, in the dialog's own layout, so
        # they're always visible regardless of scroll position.
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)

        # ── Destination ───────────────────────────────────────────────────────
        dest_group = QGroupBox(tr("gps_dest_group"))
        dest_layout = QVBoxLayout(dest_group)

        # Auto-detekterede enheder
        self._rb_device = QRadioButton(tr("gps_rb_device"))
        self._rb_device.setChecked(True)
        dest_layout.addWidget(self._rb_device)

        device_row = QHBoxLayout()
        self._device_combo = QComboBox()
        self._device_combo.setMinimumWidth(300)
        device_row.addWidget(self._device_combo)

        self._scan_btn = QPushButton(tr("gps_scan_btn"))
        # #927: size to the translated label instead of a hardcoded width,
        # so longer translations are never clipped.
        self._scan_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self._scan_btn.clicked.connect(self._scan_devices)
        device_row.addWidget(self._scan_btn)
        dest_layout.addLayout(device_row)

        self._device_info = QLabel("")
        self._device_info.setWordWrap(True)
        self._device_info.setStyleSheet(hint_style())
        dest_layout.addWidget(self._device_info)

        # Gem som fil
        self._rb_file = QRadioButton(tr("gps_rb_file"))
        dest_layout.addWidget(self._rb_file)

        file_row = QHBoxLayout()
        self._file_path = QLineEdit()
        self._file_path.setPlaceholderText(tr("gps_file_placeholder"))
        self._file_path.setReadOnly(True)
        file_row.addWidget(self._file_path)
        browse_btn = QPushButton(tr("gps_browse"))
        # #927: size to the translated label instead of a hardcoded width,
        # so longer translations are never clipped.
        browse_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        browse_btn.clicked.connect(self._browse_file)
        file_row.addWidget(browse_btn)
        dest_layout.addLayout(file_row)

        # Radioknap gruppe til destination
        self._btn_group = QButtonGroup()
        self._btn_group.addButton(self._rb_device, 0)
        self._btn_group.addButton(self._rb_file, 1)
        self._rb_device.toggled.connect(self._on_mode_changed)

        content_layout.addWidget(dest_group)

        # ── Indstillinger ─────────────────────────────────────────────────────
        opt_group = QGroupBox(tr("gps_opt_group"))
        opt_layout = QVBoxLayout(opt_group)

        # Format: GPX / GGZ
        format_group_lbl = QLabel(f"<b>{tr('gps_format_group')}:</b>")
        opt_layout.addWidget(format_group_lbl)

        self._rb_gpx = QRadioButton(tr("gps_format_gpx"))
        self._rb_gpx.setChecked(True)
        self._rb_gpx.toggled.connect(self._on_format_changed)
        opt_layout.addWidget(self._rb_gpx)

        self._rb_ggz = QRadioButton(tr("gps_format_ggz"))
        self._rb_ggz.toggled.connect(self._on_format_changed)
        opt_layout.addWidget(self._rb_ggz)

        self._format_btn_group = QButtonGroup()
        self._format_btn_group.addButton(self._rb_gpx, 0)
        self._format_btn_group.addButton(self._rb_ggz, 1)

        # Filnavn
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(tr("gps_filename_label")))
        self._filename = QLineEdit("opensak")
        self._filename.setMaximumWidth(200)
        name_row.addWidget(self._filename)
        self._suffix_lbl = QLabel(".gpx")
        name_row.addWidget(self._suffix_lbl)
        name_row.addStretch()
        opt_layout.addLayout(name_row)

        # Brug database-navn som filnavn (community-forslag på #656 — GSAK
        # har en tilsvarende toggle; forhindrer kollisioner når man skifter
        # mellem databaser uden selv at skulle omdøbe hver gang). Status
        # huskes mellem eksporter (CheminerWill's opfølgende forslag på
        # #656) — nogle bruger den altid, andre eksporterer ofte navngivne
        # filtrerede undersæt og vil ikke have filnavnet overskrevet hver gang.
        self._cb_use_db_name = QCheckBox(tr("gps_use_db_name_cb"))
        self._cb_use_db_name.toggled.connect(self._on_use_db_name_toggled)
        self._cb_use_db_name.setChecked(
            get_store().get(_USE_DB_NAME_KEY, True)  # True = standard for nye brugere
        )
        opt_layout.addWidget(self._cb_use_db_name)

        # Max antal caches
        max_row = QHBoxLayout()
        max_row.addWidget(QLabel(tr("gps_max_label")))
        self._max_caches = QSpinBox()
        self._max_caches.setRange(0, 100000)
        self._max_caches.setValue(500)
        self._max_caches.setSpecialValueText(tr("gps_max_all"))
        self._max_caches.setMaximumWidth(100)
        max_row.addWidget(self._max_caches)
        max_row.addWidget(QLabel(tr("gps_max_hint")))
        max_row.addStretch()
        opt_layout.addLayout(max_row)

        # #775: the content options (children, logs, user note log, GPX
        # version, corrected coordinates, what the limit counts) come from
        # a file export setting, so they're set up in one place.
        content_row = QHBoxLayout()
        content_row.addWidget(QLabel(tr("gps_content_settings")))
        self._content_combo = QComboBox()
        self._content_combo.setToolTip(tr("gps_content_settings_tip"))
        self._load_content_settings()
        content_row.addWidget(self._content_combo, 1)
        # Export settings are created and changed in the file export
        # dialog; this opens it from here.
        self._content_edit_btn = QPushButton(tr("edit"))
        self._content_edit_btn.setToolTip(tr("gps_content_settings_edit_tip"))
        self._content_edit_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self._content_edit_btn.setAutoDefault(False)
        self._content_edit_btn.clicked.connect(self._edit_content_settings)
        content_row.addWidget(self._content_edit_btn)
        opt_layout.addLayout(content_row)

        # Slet eksisterende filer (kun relevant ved eksport direkte til enhed)
        self._cb_delete_gpx = QCheckBox(tr("gps_delete_cb"))
        self._cb_delete_gpx.setToolTip(tr("gps_delete_cb_tip"))
        opt_layout.addWidget(self._cb_delete_gpx)

        content_layout.addWidget(opt_group)

        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        layout.addWidget(scroll)

        # ── Progress og resultat ──────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(120)
        self._log.setPlaceholderText(tr("gps_log_placeholder"))
        layout.addWidget(self._log)

        # ── Knapper ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._export_btn = QPushButton(tr("gps_export_btn"))
        self._export_btn.setStyleSheet("font-weight: bold;")
        self._export_btn.clicked.connect(self._start_export)
        btn_row.addWidget(self._export_btn)

        # #1265: "Cancel" while sending (see _set_running).
        self._close_btn = QPushButton(tr("close"))
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

        self._selected_file_path: Path | None = None

    def _load_content_settings(self) -> None:
        """Fill the content settings combo — "Default" (what Send to GPS
        always wrote), the last file export, then the saved export
        settings — and select the one used last time."""
        from opensak.export.file_export_settings import FileExportProfile

        self._content_combo.addItem(tr("gps_content_settings_default"), _CONTENT_DEFAULT)
        self._content_combo.addItem(tr("gps_content_settings_last_export"), _CONTENT_LAST)
        for path in FileExportProfile.list_profiles():
            try:
                name = FileExportProfile.load(path).name
            except Exception:
                continue
            self._content_combo.addItem(name, _CONTENT_PROFILE + name)
        wanted = get_store().get(_CONTENT_SETTINGS_KEY, _CONTENT_DEFAULT)
        self._content_combo.setCurrentIndex(max(0, self._content_combo.findData(wanted)))

    def _edit_content_settings(self) -> None:
        """Open the file export dialog, where export settings are created
        and changed (#775). Afterwards the combo offers them all again and
        selects the setting chosen there (or keeps its own choice)."""
        if self._worker is not None and self._worker.isRunning():
            return
        from opensak.gui.dialogs.file_export_dialog import FileExportDialog

        dlg = FileExportDialog(self._caches, self)
        dlg.exec()
        chosen = None
        if dlg._settings_combo.currentData() is not None:
            chosen = _CONTENT_PROFILE + dlg._settings_combo.currentText()
        dlg.deleteLater()
        current = self._content_combo.currentData()
        self._content_combo.clear()
        self._load_content_settings()
        index = self._content_combo.findData(chosen or current)
        if index >= 0:
            self._content_combo.setCurrentIndex(index)

    def _content_settings(self):
        """The file export settings the content options come from (only
        their content options are used: format, folder and file name are
        this dialog's own)."""
        from opensak.export.file_export_settings import FileExportProfile

        choice = self._content_combo.currentData() or _CONTENT_DEFAULT
        get_store().set(_CONTENT_SETTINGS_KEY, choice)
        if choice == _CONTENT_LAST:
            return FileExportProfile.load_last_used()
        if choice.startswith(_CONTENT_PROFILE):
            name = choice[len(_CONTENT_PROFILE):]
            for path in FileExportProfile.list_profiles():
                try:
                    profile = FileExportProfile.load(path)
                except Exception:
                    continue
                if profile.name == name:
                    return profile.settings
        # The defaults: also when a saved setting was deleted meanwhile.
        return _default_content_settings()

    def _scan_devices(self) -> None:
        """Scan for tilsluttede Garmin enheder."""
        from opensak.gps.garmin import find_garmin_devices
        self._device_combo.clear()

        self._scan_btn.setText(tr("gps_scan_scanning"))
        self._scan_btn.setEnabled(False)

        devices = find_garmin_devices()

        if devices:
            for dev in devices:
                self._device_combo.addItem(str(dev), dev)
            self._device_info.setText(
                tr("gps_devices_found", count=len(devices))
            )
            self._device_info.setStyleSheet("color: #2e7d32; font-size: 10px;")
            self._export_btn.setEnabled(True)
        else:
            self._device_combo.addItem(tr("gps_no_device"))
            self._device_info.setText(tr("gps_no_device_hint"))
            self._device_info.setStyleSheet("color: #c62828; font-size: 10px;")
            self._rb_file.setChecked(True)

        self._scan_btn.setText(tr("gps_scan_btn"))
        self._scan_btn.setEnabled(True)

    def _on_mode_changed(self, device_checked: bool) -> None:
        self._device_combo.setEnabled(device_checked)
        self._scan_btn.setEnabled(device_checked)
        self._update_delete_checkbox_state()
        if not device_checked:
            self._cb_delete_gpx.setChecked(False)

    def _on_use_db_name_toggled(self, checked: bool) -> None:
        """Udfyld filnavnet med den aktive databases navn, når slået til, og
        husk brugerens valg til næste gang (issue #656 opfølgning).

        Community-forslag på #656: forhindrer at man overskriver tidligere
        eksporter ved uheld, når man skifter mellem flere databaser, uden at
        skulle huske selv at omdøbe filnavnet hver gang. Slår man den fra
        igen, ændres det aktuelle filnavn ikke — det er kun selve
        auto-udfyldningen, der stopper. Selve til/fra-status huskes dog
        altid til næste gang dialogen åbnes, uanset retning.
        """
        get_store().set(_USE_DB_NAME_KEY, checked)
        if not checked:
            return
        from opensak.db.manager import get_db_manager
        active = get_db_manager().active
        if active and active.name:
            self._filename.setText(_sanitize_db_name_for_filename(active.name))

    def _on_format_changed(self) -> None:
        """Opdater format, suffix-label og delete-checkbox når format skifter."""
        if self._rb_ggz.isChecked():
            self._export_format = "ggz"
            self._suffix_lbl.setText(".ggz")
        else:
            self._export_format = "gpx"
            self._suffix_lbl.setText(".gpx")
        self._update_delete_checkbox_state()
        # Nulstil valgt filsti når format skifter så Browse åbner med korrekt extension
        self._selected_file_path = None
        self._file_path.clear()

    def _update_delete_checkbox_state(self) -> None:
        """Delete-checkbox er kun aktiv ved eksport direkte til enhed (GPX eller GGZ)."""
        device_mode = self._rb_device.isChecked()
        enabled = device_mode
        self._cb_delete_gpx.setEnabled(enabled)
        if not enabled:
            self._cb_delete_gpx.setChecked(False)

    def _browse_file(self) -> None:
        ext    = self._export_format
        ext_up = ext.upper()
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("gps_save_file_title", fmt=ext_up),
            str(Path.home()),
            tr("gps_file_filter", fmt=ext_up, ext=ext),
        )
        if path:
            p = Path(path)
            self._selected_file_path = p.parent
            self._file_path.setText(str(p))
            self._filename.setText(p.stem)
            self._rb_file.setChecked(True)

    def _get_destination(self) -> Path | None:
        if self._rb_device.isChecked():
            data = self._device_combo.currentData()
            # Mass-storage devices are Paths; MTP devices are Path-like
            # adapters and must not be passed through pathlib.Path().
            return data if data else None
        else:
            if self._selected_file_path:
                return self._selected_file_path
            return Path.home()

    def _start_export(self) -> None:
        dest = self._get_destination()
        if not dest:
            self._log.setPlainText(tr("gps_no_dest"))
            return

        filename   = self._filename.text().strip() or "opensak"
        max_caches = self._max_caches.value()
        ext        = self._export_format

        do_delete  = (
            self._cb_delete_gpx.isChecked()
            and self._rb_device.isChecked()
        )

        # Issue #501 (file-mode) / follow-up report from CheminerWill on #656
        # (device-mode "Send to GPS" silently overwrote a same-named file with
        # no warning): check for an existing file with the same name and
        # prompt for a new one, for BOTH file-mode and device-mode exports.
        # Device-mode is skipped here when do_delete is set, since the old
        # files are about to be wiped anyway — no collision to warn about.
        if self._rb_file.isChecked():
            target = dest / f"{filename}.{ext}"
        elif not do_delete:
            from opensak.gps.garmin import get_garmin_gpx_path, get_garmin_ggz_path
            target_dir = get_garmin_ggz_path(dest) if ext == "ggz" else get_garmin_gpx_path(dest)
            target = target_dir / f"{filename}.{ext}"
        else:
            target = None

        if target is not None:
            while target.exists():
                filename, ok = self._prompt_new_filename(target)
                if not ok:
                    return  # bruger fortrød / annullerede
                target = target.parent / f"{filename}.{ext}"
            self._filename.setText(filename)

        # ── Bekræft sletning ──────────────────────────────────────────────────
        if do_delete:
            from opensak.gps.garmin import get_garmin_gpx_path, get_garmin_ggz_path
            target_dir = get_garmin_ggz_path(dest) if ext == "ggz" else get_garmin_gpx_path(dest)
            pattern  = f"*.{ext}"
            existing = list(target_dir.glob(pattern)) if target_dir.exists() else []
            count    = len(existing)

            msg = QMessageBox(self)
            msg.setWindowTitle(tr("gps_confirm_delete_title"))
            msg.setIcon(QMessageBox.Icon.Warning)
            if count > 0:
                msg.setText(tr("gps_confirm_delete_msg", count=count))
                details = "\n".join(f.name for f in existing)
                msg.setDetailedText(tr("gps_delete_file_list", files=details))
            else:
                msg.setText(tr("gps_confirm_no_files_msg"))
            msg.setStandardButtons(
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
            )
            msg.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if msg.exec() != QMessageBox.StandardButton.Ok:
                return

        self._cancel_requested = False
        self._set_running(True)
        self._reset_progress()
        self._progress.setVisible(True)

        if do_delete:
            self._log.setPlainText(tr("gps_deleting"))
            self._delete_worker = DeleteWorker(dest, self._export_format)
            self._delete_worker.finished.connect(
                lambda res: self._on_delete_finished(res, dest, filename, max_caches)
            )
            self._delete_worker.error.connect(self._on_error)
            self._delete_worker.start()
        else:
            self._run_export(dest, filename, max_caches)

    def _on_delete_finished(
        self,
        delete_result,
        dest: Path,
        filename: str,
        max_caches: int,
    ) -> None:
        """Kaldt når sletning er færdig — fortsæt med export."""
        self._log.setPlainText(str(delete_result) + "\n")
        if self._cancel_requested:
            self._on_cancelled()
            return
        if (
            not getattr(delete_result, "success", True)
            or getattr(delete_result, "failed_count", 0) > 0
        ):
            self._on_error(str(delete_result))
            return
        self._run_export(dest, filename, max_caches)

    def _prompt_new_filename(self, target: Path) -> tuple[str, bool]:
        """Bed brugeren om et nyt filnavn fordi 'target' allerede findes (issue #501).

        Foreslår automatisk næste ledige "navn1", "navn2", ... som udgangspunkt,
        så brugeren normalt bare kan trykke OK i stedet for selv at opfinde et
        nyt navn hver gang.
        """
        ext = target.suffix.lstrip(".")
        stem = target.stem
        suggestion = stem
        n = 1
        while (target.parent / f"{suggestion}.{ext}").exists():
            suggestion = f"{stem}{n}"
            n += 1
        name, ok = QInputDialog.getText(
            self,
            tr("gps_file_exists_title"),
            tr("gps_file_exists_prompt", filename=f"{stem}.{ext}"),
            text=suggestion,
        )
        name = name.strip()
        if not ok or not name:
            return "", False
        return name, True

    def _run_export(self, dest: Path, filename: str, max_caches: int) -> None:
        """Start selve export-arbejderen."""
        self._log.append(tr("gps_exporting", count=len(self._caches)))
        self._worker = ExportWorker(
            self._caches, dest, filename, max_caches, self._export_format,
            settings=self._content_settings(),
        )
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.progress.connect(self._on_progress)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.start()

    def _reset_progress(self) -> None:
        """Sæt fremgangsbjælken tilbage til "kører" (ubestemt) tilstand."""
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)

    def _on_progress(self, done: int, total: int) -> None:
        """Skift til bestemt fremgang og vis antal + procent."""
        if total <= 0:
            return
        self._progress.setRange(0, total)
        self._progress.setValue(done)
        self._progress.setFormat("%v / %m  (%p%)")
        self._progress.setTextVisible(True)

    def _on_finished(self, result) -> None:
        self._progress.setVisible(False)
        self._set_running(False)
        self._log.setPlainText(str(result))

    def _on_cancelled(self) -> None:
        self._progress.setVisible(False)
        self._set_running(False)
        self._log.append("– " + tr("export_cancelled"))

    def _on_error(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._set_running(False)
        self._log.setPlainText(f"✗ {tr('error')}:\n{msg}")

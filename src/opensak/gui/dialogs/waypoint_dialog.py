"""
src/opensak/gui/dialogs/waypoint_dialog.py — Tilføj/Rediger cache eller custom waypoint.

Issue #141: dialogen understøtter to modes:
  • Geocache     — GC-kode og D/T valideres strengt
  • Custom WP    — auto-genereret CW-id, waypoint-typer, optional parent GC-kode
"""

from __future__ import annotations
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QTextEdit, QComboBox,
    QDoubleSpinBox, QCheckBox,
    QPushButton, QDialogButtonBox, QTabWidget,
    QWidget, QGroupBox, QMessageBox, QButtonGroup, QRadioButton,
    QFrame,
)

from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.db.models import Cache
from opensak.lang import tr
from opensak.coords import format_coords, parse_coords
from opensak.gui.settings import get_settings

from opensak.utils.constants import CACHE_TYPES, CONTAINER_SIZES, VALID_DT, CUSTOM_WP_TYPES


class WaypointDialog(QDialog):
    """
    Dialog til at tilføje eller redigere en cache eller custom waypoint manuelt.

    Mode vælges via radioknapper øverst:
      Geocache       — GC-kode krævet, D/T valideres til lovlige værdier
      Custom WP      — CW-id auto-genereret, waypoint-typer, optional parent cache
    """

    def __init__(
        self,
        parent=None,
        cache: Optional[Cache] = None,
        next_cw_id: Optional[str] = None,
    ):
        super().__init__(parent)
        self._cache = cache
        self._is_edit = cache is not None
        self._next_cw_id = next_cw_id or "CW001"

        self._parsed_lat: Optional[float] = None
        self._parsed_lon: Optional[float] = None

        # Determine initial mode from existing cache
        if self._is_edit and cache is not None:
            self._is_custom = not (cache.gc_code or "").upper().startswith("GC")
        else:
            self._is_custom = False

        title = tr("wp_dialog_title_edit") if self._is_edit else tr("wp_dialog_title_add")
        self.setWindowTitle(title)
        self.setMinimumSize(540, 620)
        self._setup_ui()
        if cache is not None:
            self._populate(cache)
        self._apply_mode()

    # ── UI setup ──────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Mode selector ─────────────────────────────────────────────────────
        mode_group = QGroupBox(tr("wp_mode_label"))
        mode_layout = QHBoxLayout(mode_group)
        mode_layout.setSpacing(24)

        self._radio_geocache = QRadioButton(tr("wp_mode_geocache"))
        self._radio_custom   = QRadioButton(tr("wp_mode_custom"))
        self._radio_geocache.setChecked(not self._is_custom)
        self._radio_custom.setChecked(self._is_custom)

        self._mode_btn_group = QButtonGroup(self)
        self._mode_btn_group.addButton(self._radio_geocache, 0)
        self._mode_btn_group.addButton(self._radio_custom,   1)
        self._mode_btn_group.buttonClicked.connect(self._on_mode_changed)

        mode_layout.addWidget(self._radio_geocache)
        mode_layout.addWidget(self._radio_custom)
        mode_layout.addStretch()

        # Disable mode switch when editing (can't change an existing entry's type)
        if self._is_edit:
            self._radio_geocache.setEnabled(False)
            self._radio_custom.setEnabled(False)

        layout.addWidget(mode_group)

        # ── Separator ─────────────────────────────────────────────────────────
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        # ── Tabs ──────────────────────────────────────────────────────────────
        self._tabs = QTabWidget()

        self._tabs.addTab(self._build_basic_tab(),   tr("wp_tab_basic"))
        self._tabs.addTab(self._build_details_tab(), tr("db_details_group"))
        self._tabs.addTab(self._build_status_tab(),  tr("wp_tab_status"))

        layout.addWidget(self._tabs)

        # ── Buttons ───────────────────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("save"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("cancel"))
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_basic_tab(self) -> QWidget:
        basic = QWidget()
        form = QFormLayout(basic)
        form.setSpacing(8)

        # ── GC code (geocache mode) ───────────────────────────────────────────
        self._gc_code = QLineEdit()
        self._gc_code.setPlaceholderText(tr("wp_ph_gc_code"))
        if self._is_edit:
            self._gc_code.setReadOnly(True)
            self._gc_code.setStyleSheet("color: gray;")
        self._lbl_gc_code = QLabel(tr("wp_label_gc_code"))
        form.addRow(self._lbl_gc_code, self._gc_code)

        # ── CW id (custom mode) ───────────────────────────────────────────────
        self._cw_id = QLineEdit()
        self._cw_id.setReadOnly(True)
        self._cw_id.setStyleSheet("color: gray;")
        self._cw_id.setText(self._next_cw_id)
        self._lbl_cw_id = QLabel(tr("wp_label_cw_id"))
        form.addRow(self._lbl_cw_id, self._cw_id)

        # ── Name ──────────────────────────────────────────────────────────────
        self._name = QLineEdit()
        self._name.setPlaceholderText(tr("wp_ph_name"))
        form.addRow(tr("wp_label_name"), self._name)

        # ── Type — two combos swapped by mode ─────────────────────────────────
        self._cache_type_gc = QComboBox()
        self._cache_type_gc.addItems(CACHE_TYPES)
        self._lbl_type_gc = QLabel(tr("wp_label_type"))
        form.addRow(self._lbl_type_gc, self._cache_type_gc)

        self._cache_type_cw = QComboBox()
        self._cache_type_cw.addItems(CUSTOM_WP_TYPES)
        self._lbl_type_cw = QLabel(tr("wp_label_type"))
        form.addRow(self._lbl_type_cw, self._cache_type_cw)

        # ── Container ─────────────────────────────────────────────────────────
        self._container = QComboBox()
        self._container.addItems(CONTAINER_SIZES)
        self._lbl_container = QLabel(tr("wp_label_container"))
        form.addRow(self._lbl_container, self._container)

        # ── Coordinates ───────────────────────────────────────────────────────
        coord_group = QGroupBox(tr("detail_coords"))
        coord_layout = QVBoxLayout(coord_group)
        coord_layout.setSpacing(4)

        fmt = get_settings().coord_format
        placeholder = {
            "dmm": "N55 47.250 E012 25.000",
            "dms": "N55° 47' 15\" E012° 25' 00\"",
            "dd":  "55.78750, 12.41667",
        }.get(fmt, "N55 47.250 E012 25.000")

        self._coord_input = QLineEdit()
        self._coord_input.setPlaceholderText(placeholder)
        self._coord_input.textChanged.connect(self._on_coord_changed)
        coord_layout.addWidget(self._coord_input)

        self._coord_feedback = QLabel("")
        self._coord_feedback.setStyleSheet("font-size: 10px;")
        self._coord_feedback.setWordWrap(True)
        coord_layout.addWidget(self._coord_feedback)
        form.addRow(coord_group)

        # ── D/T (geocache mode only) ──────────────────────────────────────────
        dt_layout = QHBoxLayout()
        self._difficulty = QDoubleSpinBox()
        self._difficulty.setRange(1.0, 5.0)
        self._difficulty.setSingleStep(0.5)
        self._difficulty.setDecimals(1)
        self._difficulty.setValue(1.5)
        dt_layout.addWidget(QLabel(tr("wp_label_difficulty")))
        dt_layout.addWidget(self._difficulty)
        dt_layout.addSpacing(16)

        self._terrain = QDoubleSpinBox()
        self._terrain.setRange(1.0, 5.0)
        self._terrain.setSingleStep(0.5)
        self._terrain.setDecimals(1)
        self._terrain.setValue(1.5)
        dt_layout.addWidget(QLabel(tr("wp_label_terrain")))
        dt_layout.addWidget(self._terrain)
        dt_layout.addStretch()

        self._lbl_dt = QLabel(tr("wp_label_dt"))
        self._widget_dt = QWidget()
        self._widget_dt.setLayout(dt_layout)
        form.addRow(self._lbl_dt, self._widget_dt)

        # ── Parent cache (custom mode only) ───────────────────────────────────
        parent_vbox = QVBoxLayout()
        parent_vbox.setSpacing(2)
        parent_vbox.setContentsMargins(0, 0, 0, 0)
        self._parent_gc = QLineEdit()
        self._parent_gc.setPlaceholderText(tr("wp_ph_parent_gc"))
        self._parent_gc.setMaxLength(16)
        self._parent_gc.textChanged.connect(self._on_parent_gc_changed)
        self._parent_gc_feedback = QLabel("")
        self._parent_gc_feedback.setStyleSheet("font-size: 10px;")
        parent_vbox.addWidget(self._parent_gc)
        parent_vbox.addWidget(self._parent_gc_feedback)
        parent_widget = QWidget()
        parent_widget.setLayout(parent_vbox)

        self._lbl_parent = QLabel(tr("wp_label_parent_gc"))
        form.addRow(self._lbl_parent, parent_widget)

        self._basic_form = form
        return basic

    def _build_details_tab(self) -> QWidget:
        details = QWidget()
        form = QFormLayout(details)
        form.setSpacing(8)

        self._placed_by = QLineEdit()
        self._placed_by.setPlaceholderText(tr("wp_ph_placed_by"))
        form.addRow(tr("wp_label_placed_by"), self._placed_by)

        self._country = QLineEdit()
        self._country.setPlaceholderText(tr("wp_ph_country"))
        form.addRow(tr("wp_label_country"), self._country)

        self._state = QLineEdit()
        self._state.setPlaceholderText(tr("wp_ph_state"))
        form.addRow(tr("wp_label_state"), self._state)

        self._short_desc = QTextEdit()
        self._short_desc.setMaximumHeight(80)
        self._short_desc.setPlaceholderText(tr("wp_ph_short_desc"))
        form.addRow(tr("wp_label_short_desc"), self._short_desc)

        self._long_desc = QTextEdit()
        self._long_desc.setMaximumHeight(120)
        self._long_desc.setPlaceholderText(tr("wp_ph_long_desc"))
        form.addRow(tr("wp_label_long_desc"), self._long_desc)

        self._hints = QLineEdit()
        self._hints.setPlaceholderText(tr("wp_ph_hint"))
        form.addRow(tr("wp_label_hint"), self._hints)

        return details

    def _build_status_tab(self) -> QWidget:
        status = QWidget()
        form = QFormLayout(status)
        form.setSpacing(8)

        self._available = QCheckBox(tr("filter_available"))
        self._available.setChecked(True)
        form.addRow(tr("wp_label_status"), self._available)

        self._archived = QCheckBox(tr("col_archived"))
        form.addRow("", self._archived)

        self._premium = QCheckBox(tr("wp_cb_premium"))
        form.addRow("", self._premium)

        # Issue #202: lock a cache so PQ/GPX re-imports can't overwrite its
        # fields. Placed with the other general cache flags, not the personal
        # ones below — locking isn't about your own stats, it's about
        # protecting the cache record itself.
        self._locked = QCheckBox(tr("wp_cb_locked"))
        form.addRow("", self._locked)

        self._found = QCheckBox(tr("wp_cb_found"))
        form.addRow(tr("wp_label_personal"), self._found)

        self._dnf = QCheckBox(tr("wp_cb_dnf"))
        form.addRow("", self._dnf)

        self._ftf = QCheckBox(tr("wp_cb_ftf"))
        form.addRow("", self._ftf)

        return status

    # ── Mode switching ────────────────────────────────────────────────────────

    def _on_mode_changed(self, _btn) -> None:
        self._is_custom = self._radio_custom.isChecked()
        self._apply_mode()

    def _apply_mode(self) -> None:
        """Show/hide rows depending on geocache vs custom waypoint mode."""
        custom = self._is_custom

        # GC code ↔ CW id
        self._lbl_gc_code.setVisible(not custom)
        self._gc_code.setVisible(not custom)
        self._lbl_cw_id.setVisible(custom)
        self._cw_id.setVisible(custom)

        # Type dropdown
        self._lbl_type_gc.setVisible(not custom)
        self._cache_type_gc.setVisible(not custom)
        self._lbl_type_cw.setVisible(custom)
        self._cache_type_cw.setVisible(custom)

        # Container — geocache only
        self._lbl_container.setVisible(not custom)
        self._container.setVisible(not custom)

        # D/T — geocache only
        self._lbl_dt.setVisible(not custom)
        self._widget_dt.setVisible(not custom)

        # Parent cache — custom only
        self._lbl_parent.setVisible(custom)
        # parent_widget is the QWidget wrapping parent_gc + feedback
        parent_widget = self._parent_gc.parentWidget()
        if parent_widget is not None:
            parent_widget.setVisible(custom)

        # Status tab — hidden for custom waypoints
        self._tabs.setTabVisible(2, not custom)

    # ── Input feedback ────────────────────────────────────────────────────────

    def _on_coord_changed(self, text: str) -> None:
        text = text.strip()
        if not text:
            self._coord_feedback.setText("")
            self._parsed_lat = None
            self._parsed_lon = None
            return
        result = parse_coords(text)
        if result is not None:
            lat, lon = result
            self._parsed_lat = lat
            self._parsed_lon = lon
            fmt = get_settings().coord_format
            display = format_coords(lat, lon, fmt)
            self._coord_feedback.setText(f"✓  {display}")
            self._coord_feedback.setStyleSheet("color: #2e7d32; font-size: 10px;")
        else:
            self._parsed_lat = None
            self._parsed_lon = None
            self._coord_feedback.setText(tr("coord_conv_parse_error"))
            self._coord_feedback.setStyleSheet("color: #c62828; font-size: 10px;")

    def _on_parent_gc_changed(self, text: str) -> None:
        text = text.strip().upper()
        if not text:
            self._parent_gc_feedback.setText("")
            return
        if not text.startswith("GC"):
            self._parent_gc_feedback.setText(tr("wp_val_parent_gc_invalid"))
            self._parent_gc_feedback.setStyleSheet("color: #c62828; font-size: 10px;")
        else:
            self._parent_gc_feedback.setText("")

    # ── Populate (edit mode) ──────────────────────────────────────────────────

    def _populate(self, cache: Cache) -> None:
        gc = cache.gc_code or ""
        if self._is_custom:
            self._cw_id.setText(gc)
        else:
            self._gc_code.setText(gc)

        self._name.setText(cache.name or "")

        if self._is_custom:
            idx = self._cache_type_cw.findText(cache.cache_type or "")
            if idx >= 0:
                self._cache_type_cw.setCurrentIndex(idx)
            self._parent_gc.setText(cache.parent_gc_code or "")
        else:
            idx = self._cache_type_gc.findText(cache.cache_type or "")
            if idx >= 0:
                self._cache_type_gc.setCurrentIndex(idx)
            idx = self._container.findText(cache.container or "")
            if idx >= 0:
                self._container.setCurrentIndex(idx)
            if cache.difficulty:
                self._difficulty.setValue(cache.difficulty)
            if cache.terrain:
                self._terrain.setValue(cache.terrain)

        if cache.latitude is not None and cache.longitude is not None:
            fmt = get_settings().coord_format
            self._coord_input.setText(format_coords(cache.latitude, cache.longitude, fmt))
            self._parsed_lat = cache.latitude
            self._parsed_lon = cache.longitude

        self._placed_by.setText(cache.placed_by or "")
        self._country.setText(cache.country or "")
        self._state.setText(cache.state or "")
        self._short_desc.setPlainText(cache.short_description or "")
        self._long_desc.setPlainText(cache.long_description or "")
        self._hints.setText(cache.encoded_hints or "")

        if not self._is_custom:
            self._available.setChecked(cache.available if cache.available is not None else True)
            self._archived.setChecked(cache.archived if cache.archived is not None else False)
            self._premium.setChecked(cache.premium_only if cache.premium_only is not None else False)
            self._locked.setChecked(cache.locked if cache.locked is not None else False)
            self._found.setChecked(cache.found if cache.found is not None else False)
            self._dnf.setChecked(cache.dnf if cache.dnf is not None else False)
            self._ftf.setChecked(cache.first_to_find if cache.first_to_find is not None else False)

    # ── Validation & accept ───────────────────────────────────────────────────

    def _validate_and_accept(self) -> None:
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self, tr("warning"), tr("wp_val_name_required"))
            return

        coord_text = self._coord_input.text().strip()
        if coord_text and self._parsed_lat is None:
            QMessageBox.warning(self, tr("warning"), tr("coord_conv_parse_error"))
            return

        if self._is_custom:
            self._validate_custom_and_accept()
        else:
            self._validate_geocache_and_accept()

    def _validate_geocache_and_accept(self) -> None:
        gc_code = self._gc_code.text().strip().upper()
        if not gc_code:
            QMessageBox.warning(self, tr("warning"), tr("wp_val_gc_required"))
            return
        if not gc_code.startswith("GC") or not gc_code[2:].isalnum():
            QMessageBox.warning(self, tr("warning"), tr("wp_val_gc_invalid"))
            return

        d = self._difficulty.value()
        t = self._terrain.value()
        if d not in VALID_DT:
            QMessageBox.warning(self, tr("warning"), tr("wp_val_dt_invalid", value=d))
            return
        if t not in VALID_DT:
            QMessageBox.warning(self, tr("warning"), tr("wp_val_dt_invalid", value=t))
            return

        self.accept()

    def _validate_custom_and_accept(self) -> None:
        parent = self._parent_gc.text().strip().upper()
        if parent and not parent.startswith("GC"):
            QMessageBox.warning(self, tr("warning"), tr("wp_val_parent_gc_invalid"))
            return
        self.accept()

    # ── Data extraction ───────────────────────────────────────────────────────

    def get_data(self) -> dict:
        """Return form data as a dict ready to create/update a Cache row."""
        if self._is_custom:
            return self._get_custom_data()
        return self._get_geocache_data()

    def _get_geocache_data(self) -> dict:
        return {
            "gc_code":           self._gc_code.text().strip().upper(),
            "name":              self._name.text().strip(),
            "cache_type":        self._cache_type_gc.currentText(),
            "container":         self._container.currentText(),
            "latitude":          self._parsed_lat,
            "longitude":         self._parsed_lon,
            "difficulty":        self._difficulty.value(),
            "terrain":           self._terrain.value(),
            "placed_by":         self._placed_by.text().strip() or None,
            "country":           self._country.text().strip() or None,
            "state":             self._state.text().strip() or None,
            "short_description": self._short_desc.toPlainText().strip() or None,
            "long_description":  self._long_desc.toPlainText().strip() or None,
            "encoded_hints":     self._hints.text().strip() or None,
            "available":         self._available.isChecked(),
            "archived":          self._archived.isChecked(),
            "premium_only":      self._premium.isChecked(),
            "locked":            self._locked.isChecked(),
            "found":             self._found.isChecked(),
            "dnf":               self._dnf.isChecked(),
            "first_to_find":     self._ftf.isChecked(),
            "parent_gc_code":    None,
        }

    def _get_custom_data(self) -> dict:
        parent = self._parent_gc.text().strip().upper() or None
        return {
            "gc_code":           self._cw_id.text().strip(),
            "name":              self._name.text().strip(),
            "cache_type":        self._cache_type_cw.currentText(),
            "container":         None,
            "latitude":          self._parsed_lat,
            "longitude":         self._parsed_lon,
            "difficulty":        None,
            "terrain":           None,
            "placed_by":         self._placed_by.text().strip() or None,
            "country":           self._country.text().strip() or None,
            "state":             self._state.text().strip() or None,
            "short_description": self._short_desc.toPlainText().strip() or None,
            "long_description":  self._long_desc.toPlainText().strip() or None,
            "encoded_hints":     self._hints.text().strip() or None,
            "available":         True,
            "archived":          False,
            "premium_only":      False,
            "found":             False,
            "dnf":               False,
            "parent_gc_code":    parent,
        }

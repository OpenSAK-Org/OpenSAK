"""
src/opensak/gui/dialogs/waypoint_dialog.py — Tilføj/Rediger cache eller custom waypoint.

Issue #141: dialogen understøtter to modes:
  • Geocache     — GC-kode og D/T valideres strengt
  • Custom WP    — auto-genereret CW-id, waypoint-typer, optional parent GC-kode

Edit-cache enhancements: ud over caches-kolonnerne kan dialogen også redigere
personlige felter (user flag, user data 1–4, GC-note, watch), cachens
UserNote (lokal note + korrigerede koordinater) og child-waypoints. De to
sidste gemmes via save_related(), da de ligger i egne tabeller.
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QTextEdit, QComboBox,
    QDoubleSpinBox, QCheckBox, QDateEdit,
    QPushButton, QDialogButtonBox, QTabWidget,
    QWidget, QGroupBox, QMessageBox, QButtonGroup, QRadioButton,
    QFrame, QScrollArea, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView,
)

from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.db.models import Cache, UserNote, Waypoint
from opensak.lang import tr
from opensak.coords import format_coords, parse_coords
from opensak.gui.settings import get_settings
from opensak.gui.theme import hint_style
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen

from opensak.utils.constants import (
    CACHE_TYPES, CONTAINER_SIZES, VALID_DT, CUSTOM_WP_TYPES, KNOWN_PREFIXES,
)

# Waypoint-typer til child-waypoint-dialogens (redigerbare) type-dropdown —
# de kendte Groundspeak/GSAK-typer i deres første forekomst-rækkefølge.
CHILD_WP_TYPES: list[str] = list(dict.fromkeys(KNOWN_PREFIXES.values()))

# Waypoint-kolonner som child-waypoint-dialogen kan redigere.
_WP_FIELDS = (
    "prefix", "wp_type", "name", "description", "comment",
    "latitude", "longitude", "wp_code", "url", "wp_date", "wp_flag",
)


def _label(key: str) -> str:
    """Formular-label fra en eksisterende kolonne-/overskriftsnøgle."""
    return f"{tr(key)}:"


class OptionalDateEdit(QWidget):
    """
    Dato-felt der også kan være "ikke sat" (None).

    Checkboxen slår datoen til/fra. Tidspunktet fra den oprindelige værdi
    bevares, så længe brugeren ikke ændrer selve datoen — importerede
    found_date/dnf_date/hidden_date har ofte et klokkeslæt.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._check = QCheckBox()
        self._date = QDateEdit()
        self._date.setCalendarPopup(True)
        self._date.setDisplayFormat("yyyy-MM-dd")
        self._date.setDate(QDate.currentDate())
        self._date.setEnabled(False)
        self._check.toggled.connect(self._date.setEnabled)
        layout.addWidget(self._check)
        layout.addWidget(self._date)
        layout.addStretch()
        self._original: Optional[datetime] = None

    def set_value(self, value: Optional[datetime]) -> None:
        self._original = value
        if value is None:
            self._check.setChecked(False)
            self._date.setDate(QDate.currentDate())
        else:
            self._date.setDate(QDate(value.year, value.month, value.day))
            self._check.setChecked(True)

    def value(self) -> Optional[datetime]:
        if not self._check.isChecked():
            return None
        d = self._date.date()
        orig = self._original
        if orig is not None and (orig.year, orig.month, orig.day) == (d.year(), d.month(), d.day()):
            return orig
        return datetime(d.year(), d.month(), d.day())

    def is_set(self) -> bool:
        return self._check.isChecked()

    def set_today(self) -> None:
        self._date.setDate(QDate.currentDate())
        self._check.setChecked(True)

    def clear(self) -> None:
        self._check.setChecked(False)


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

        # UserNote.corrected_lat/lon — redigeres via CorrectedCoordsDialog
        self._corr_lat: Optional[float] = None
        self._corr_lon: Optional[float] = None

        # Child-waypoints som dicts (_WP_FIELDS + "id"; id=None for nye)
        self._waypoints: list[dict] = []

        # Determine initial mode from existing cache
        if self._is_edit and cache is not None:
            self._is_custom = not (cache.gc_code or "").upper().startswith("GC")
        else:
            self._is_custom = False

        title = tr("wp_dialog_title_edit") if self._is_edit else tr("wp_dialog_title_add")
        self.setWindowTitle(title)
        self.setMinimumSize(540, 620)
        # Issue #815 (follow-up to #811): the tallest of this dialog's three
        # tabs (Basic, with two coordinate/D-T groups plus the mode/parent
        # rows) can exceed available screen height at high DPI scaling, same
        # failure mode as Settings in #811. Each tab is wrapped in its own
        # QScrollArea below so the cap has somewhere to send the overflow
        # instead of clipping it — see clamp_dialog_height_to_screen()'s
        # docstring for the full reasoning.
        clamp_dialog_height_to_screen(self, parent)
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

        self._tabs.addTab(self._wrap_in_scroll(self._build_basic_tab()),   tr("wp_tab_basic"))
        self._tabs.addTab(self._wrap_in_scroll(self._build_details_tab()), tr("db_details_group"))
        self._status_tab_idx = self._tabs.addTab(
            self._wrap_in_scroll(self._build_status_tab()), tr("wp_tab_status"))
        self._tabs.addTab(self._wrap_in_scroll(self._build_personal_tab()), tr("wp_tab_personal"))
        # Ikke i scroll-area: tabellen scroller selv
        self._waypoints_tab_idx = self._tabs.addTab(
            self._build_waypoints_tab(), tr("detail_tab_waypoints"))

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

    @staticmethod
    def _wrap_in_scroll(tab: QWidget) -> QScrollArea:
        # Issue #815: lets the height cap from clamp_dialog_height_to_screen()
        # in __init__ actually be reachable via scrolling instead of just
        # clipping the tab's content — same pattern as settings_dialog.py.
        scroll = QScrollArea()
        scroll.setWidget(tab)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        return scroll

    def _build_basic_tab(self) -> QWidget:
        basic = QWidget()
        form = QFormLayout(basic)
        form.setSpacing(8)

        # ── GC code (geocache mode) ───────────────────────────────────────────
        self._gc_code = QLineEdit()
        self._gc_code.setPlaceholderText(tr("wp_ph_gc_code"))
        if self._is_edit:
            self._gc_code.setReadOnly(True)
            self._gc_code.setStyleSheet(hint_style(font_size=None))
        self._lbl_gc_code = QLabel(tr("wp_label_gc_code"))
        form.addRow(self._lbl_gc_code, self._gc_code)

        # ── CW id (custom mode) ───────────────────────────────────────────────
        self._cw_id = QLineEdit()
        self._cw_id.setReadOnly(True)
        self._cw_id.setStyleSheet(hint_style(font_size=None))
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

        self._owner_name = QLineEdit()
        form.addRow(_label("col_owner_name"), self._owner_name)

        self._hidden_date = OptionalDateEdit()
        form.addRow(_label("col_hidden_date"), self._hidden_date)

        self._country = QLineEdit()
        self._country.setPlaceholderText(tr("wp_ph_country"))
        form.addRow(tr("wp_label_country"), self._country)

        self._state = QLineEdit()
        self._state.setPlaceholderText(tr("wp_ph_state"))
        form.addRow(tr("wp_label_state"), self._state)

        self._county = QLineEdit()
        form.addRow(_label("col_county"), self._county)

        # Tomt felt = ukendt højde (None) — 0 m er en gyldig højde
        self._elevation = QLineEdit()
        self._elevation.setPlaceholderText("m")
        self._elevation.setMaximumWidth(120)
        form.addRow(_label("col_elevation"), self._elevation)

        self._short_desc = QTextEdit()
        self._short_desc.setMaximumHeight(80)
        self._short_desc.setPlaceholderText(tr("wp_ph_short_desc"))
        form.addRow(tr("wp_label_short_desc"), self._short_desc)
        self._short_desc_html = QCheckBox(tr("wp_cb_desc_html"))
        form.addRow("", self._short_desc_html)

        self._long_desc = QTextEdit()
        self._long_desc.setMaximumHeight(120)
        self._long_desc.setPlaceholderText(tr("wp_ph_long_desc"))
        form.addRow(tr("wp_label_long_desc"), self._long_desc)
        self._long_desc_html = QCheckBox(tr("wp_cb_desc_html"))
        form.addRow("", self._long_desc_html)

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

        # found/found_date og dnf/dnf_date holdes i sync: afkrydsning sætter
        # dags dato (hvis ingen dato), fjernelse rydder datoen, og datoen kan
        # kun redigeres mens flaget er sat.
        self._found_date = OptionalDateEdit()
        self._found_date.setEnabled(False)
        form.addRow(_label("col_found_date"), self._found_date)
        self._found.toggled.connect(
            lambda on: self._sync_flag_date(on, self._found_date))

        self._dnf = QCheckBox(tr("wp_cb_dnf"))
        form.addRow("", self._dnf)

        self._dnf_date = OptionalDateEdit()
        self._dnf_date.setEnabled(False)
        form.addRow(_label("col_dnf_date"), self._dnf_date)
        self._dnf.toggled.connect(
            lambda on: self._sync_flag_date(on, self._dnf_date))

        self._ftf = QCheckBox(tr("wp_cb_ftf"))
        form.addRow("", self._ftf)

        return status

    @staticmethod
    def _sync_flag_date(on: bool, date_edit: OptionalDateEdit) -> None:
        if on and not date_edit.is_set():
            date_edit.set_today()
        elif not on:
            date_edit.clear()
        date_edit.setEnabled(on)

    def _build_personal_tab(self) -> QWidget:
        personal = QWidget()
        form = QFormLayout(personal)
        form.setSpacing(8)

        self._user_flag = QCheckBox(tr("col_user_flag_label"))
        form.addRow("", self._user_flag)

        self._watch = QCheckBox(tr("col_watch"))
        form.addRow("", self._watch)

        self._user_data: list[QLineEdit] = []
        for i in range(1, 5):
            edit = QLineEdit()
            form.addRow(_label(f"col_user_data_{i}"), edit)
            self._user_data.append(edit)

        self._gc_note = QTextEdit()
        self._gc_note.setMaximumHeight(80)
        form.addRow(_label("col_gc_note"), self._gc_note)

        # UserNote.note — samme note som detaljepanelets "Noter"-fane
        self._user_note = QTextEdit()
        self._user_note.setMaximumHeight(100)
        self._user_note.setPlaceholderText(tr("detail_note_placeholder"))
        form.addRow(_label("col_notes"), self._user_note)

        # Korrigerede koordinater: visning + genvej til CorrectedCoordsDialog
        corr_row = QHBoxLayout()
        corr_row.setContentsMargins(0, 0, 0, 0)
        self._corr_display = QLabel("—")
        self._corr_display.setStyleSheet("color: #e65100; font-weight: bold;")
        self._corr_display.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        corr_row.addWidget(self._corr_display, 1)
        self._corr_edit_btn = QPushButton(tr("detail_corrected_edit_btn"))
        self._corr_edit_btn.setToolTip(tr("detail_corrected_edit_tooltip"))
        self._corr_edit_btn.clicked.connect(self._edit_corrected)
        corr_row.addWidget(self._corr_edit_btn)
        self._corr_clear_btn = QPushButton("✕")
        self._corr_clear_btn.setToolTip(tr("detail_corrected_clear_tooltip"))
        self._corr_clear_btn.setMaximumWidth(28)
        self._corr_clear_btn.setStyleSheet("color: #c62828;")
        self._corr_clear_btn.clicked.connect(lambda: self._set_corrected(None, None))
        corr_row.addWidget(self._corr_clear_btn)
        corr_widget = QWidget()
        corr_widget.setLayout(corr_row)
        form.addRow(_label("detail_corrected_coords"), corr_widget)
        self._update_corrected_display()

        return personal

    def _build_waypoints_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self._wp_table = QTableWidget(0, 4)
        self._wp_table.setHorizontalHeaderLabels([
            tr("wp_child_col_prefix"), tr("col_type"), tr("col_name"), tr("detail_coords"),
        ])
        self._wp_table.verticalHeader().setVisible(False)
        self._wp_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._wp_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._wp_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self._wp_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._wp_table.doubleClicked.connect(lambda _idx: self._edit_child_wp())
        self._wp_table.itemSelectionChanged.connect(self._update_wp_buttons)
        layout.addWidget(self._wp_table)

        btn_row = QHBoxLayout()
        self._wp_add_btn = QPushButton(tr("wp_child_add"))
        self._wp_add_btn.clicked.connect(self._add_child_wp)
        self._wp_edit_btn = QPushButton(tr("edit"))
        self._wp_edit_btn.clicked.connect(self._edit_child_wp)
        self._wp_del_btn = QPushButton(tr("delete"))
        self._wp_del_btn.clicked.connect(self._delete_child_wp)
        btn_row.addWidget(self._wp_add_btn)
        btn_row.addWidget(self._wp_edit_btn)
        btn_row.addWidget(self._wp_del_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        self._update_wp_buttons()

        return tab

    # ── Corrected coordinates ─────────────────────────────────────────────────

    def _edit_corrected(self) -> None:
        from opensak.gui.dialogs.corrected_coords_dialog import CorrectedCoordsDialog
        gc = (self._cw_id.text() if self._is_custom else self._gc_code.text()).strip().upper()
        dlg = CorrectedCoordsDialog(
            gc_code=gc,
            orig_lat=self._parsed_lat,
            orig_lon=self._parsed_lon,
            corrected_lat=self._corr_lat,
            corrected_lon=self._corr_lon,
            parent=self,
        )
        if dlg.exec():
            self._set_corrected(*dlg.get_coords())

    def _set_corrected(self, lat: Optional[float], lon: Optional[float]) -> None:
        self._corr_lat, self._corr_lon = lat, lon
        self._update_corrected_display()

    def _update_corrected_display(self) -> None:
        lat, lon = self._corr_lat, self._corr_lon
        if lat is not None and lon is not None:
            self._corr_display.setText(
                format_coords(lat, lon, get_settings().coord_format))
            self._corr_edit_btn.setText(tr("detail_corrected_edit_btn"))
            self._corr_clear_btn.setEnabled(True)
        else:
            self._corr_display.setText("—")
            self._corr_edit_btn.setText(tr("detail_corrected_add_btn"))
            self._corr_clear_btn.setEnabled(False)

    # ── Child waypoints ───────────────────────────────────────────────────────

    def _refresh_wp_table(self) -> None:
        fmt = get_settings().coord_format
        self._wp_table.setRowCount(len(self._waypoints))
        for row, wp in enumerate(self._waypoints):
            lat, lon = wp.get("latitude"), wp.get("longitude")
            coords = format_coords(lat, lon, fmt) if lat is not None and lon is not None \
                else tr("detail_wp_no_coords")
            for col, text in enumerate((
                wp.get("prefix") or "", wp.get("wp_type") or "", wp.get("name") or "", coords,
            )):
                self._wp_table.setItem(row, col, QTableWidgetItem(text))
        self._update_wp_buttons()

    def _selected_wp_row(self) -> int:
        rows = self._wp_table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _update_wp_buttons(self) -> None:
        has_sel = self._selected_wp_row() >= 0
        self._wp_edit_btn.setEnabled(has_sel)
        self._wp_del_btn.setEnabled(has_sel)

    def _wp_code_taken(self, code: Optional[str], skip_row: int) -> bool:
        # Unik pr. (cache_id, wp_code) i DB — fang dubletter før Save
        if not code:
            return False
        return any(
            (wp.get("wp_code") or "").upper() == code.upper()
            for i, wp in enumerate(self._waypoints) if i != skip_row
        )

    def _run_child_dialog(self, wp: Optional[dict], row: int) -> Optional[dict]:
        dlg = ChildWaypointDialog(self, wp=wp, origin=(self._parsed_lat, self._parsed_lon))
        while dlg.exec():
            data = dlg.get_data()
            if self._wp_code_taken(data["wp_code"], row):
                QMessageBox.warning(
                    self, tr("warning"), tr("wp_child_val_code_duplicate", code=data["wp_code"]))
                continue
            return data
        return None

    def _add_child_wp(self) -> None:
        data = self._run_child_dialog(None, -1)
        if data is not None:
            data["id"] = None
            self._waypoints.append(data)
            self._refresh_wp_table()
            self._wp_table.selectRow(len(self._waypoints) - 1)

    def _edit_child_wp(self) -> None:
        row = self._selected_wp_row()
        if row < 0:
            return
        data = self._run_child_dialog(self._waypoints[row], row)
        if data is not None:
            data["id"] = self._waypoints[row].get("id")
            self._waypoints[row] = data
            self._refresh_wp_table()
            self._wp_table.selectRow(row)

    def _delete_child_wp(self) -> None:
        # Slettes først i DB ved Save — Cancel fortryder
        row = self._selected_wp_row()
        if row >= 0:
            del self._waypoints[row]
            self._refresh_wp_table()

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

        # Status + child-waypoints tabs — hidden for custom waypoints
        self._tabs.setTabVisible(self._status_tab_idx, not custom)
        self._tabs.setTabVisible(self._waypoints_tab_idx, not custom)

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
        self._owner_name.setText(getattr(cache, "owner_name", None) or "")
        self._hidden_date.set_value(getattr(cache, "hidden_date", None))
        self._country.setText(cache.country or "")
        self._state.setText(cache.state or "")
        self._county.setText(getattr(cache, "county", None) or "")
        elevation = getattr(cache, "elevation", None)
        self._elevation.setText(f"{elevation:g}" if elevation is not None else "")
        self._short_desc.setPlainText(cache.short_description or "")
        self._short_desc_html.setChecked(bool(getattr(cache, "short_desc_html", False)))
        self._long_desc.setPlainText(cache.long_description or "")
        self._long_desc_html.setChecked(bool(getattr(cache, "long_desc_html", False)))
        self._hints.setText(cache.encoded_hints or "")

        if not self._is_custom:
            self._available.setChecked(cache.available if cache.available is not None else True)
            self._archived.setChecked(cache.archived if cache.archived is not None else False)
            self._premium.setChecked(cache.premium_only if cache.premium_only is not None else False)
            self._locked.setChecked(cache.locked if cache.locked is not None else False)
            self._found.setChecked(cache.found if cache.found is not None else False)
            self._dnf.setChecked(cache.dnf if cache.dnf is not None else False)
            self._ftf.setChecked(cache.first_to_find if cache.first_to_find is not None else False)
            # Efter checkboxene: toggled-signalet må ikke erstatte en
            # eksisterende (eller manglende) dato med dags dato ved åbning.
            self._found_date.set_value(getattr(cache, "found_date", None))
            self._dnf_date.set_value(getattr(cache, "dnf_date", None))

        # Personal
        self._user_flag.setChecked(bool(getattr(cache, "user_flag", False)))
        self._watch.setChecked(bool(getattr(cache, "watch", False)))
        for i, edit in enumerate(self._user_data, start=1):
            edit.setText(getattr(cache, f"user_data_{i}", None) or "")
        self._gc_note.setPlainText(getattr(cache, "gc_note", None) or "")

        note = getattr(cache, "user_note", None)
        if note is not None:
            self._user_note.setPlainText(note.note or "")
            if note.is_corrected:
                self._set_corrected(note.corrected_lat, note.corrected_lon)

        # Child waypoints
        self._waypoints = [
            {"id": wp.id, **{f: getattr(wp, f, None) for f in _WP_FIELDS}}
            for wp in (getattr(cache, "waypoints", None) or [])
        ]
        self._refresh_wp_table()

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

        if self._elevation.text().strip():
            try:
                self._elevation_value()
            except ValueError:
                QMessageBox.warning(self, tr("warning"), tr("wp_val_elevation_invalid"))
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
        """Return form data as a dict ready to create/update a Cache row.

        Only caches-table columns — UserNote and child waypoints live in
        their own tables and are written by save_related().
        """
        if self._is_custom:
            data = self._get_custom_data()
        else:
            data = self._get_geocache_data()
        data.update(self._get_common_data())
        return data

    def _elevation_value(self) -> Optional[float]:
        text = self._elevation.text().strip().replace(",", ".")
        return float(text) if text else None

    def _get_common_data(self) -> dict:
        """Columns shared by geocaches and custom waypoints."""
        return {
            "owner_name":      self._owner_name.text().strip() or None,
            "hidden_date":     self._hidden_date.value(),
            "county":          self._county.text().strip() or None,
            "elevation":       self._elevation_value(),
            "short_desc_html": self._short_desc_html.isChecked(),
            "long_desc_html":  self._long_desc_html.isChecked(),
            "user_flag":       self._user_flag.isChecked(),
            "watch":           self._watch.isChecked(),
            "user_data_1":     self._user_data[0].text().strip() or None,
            "user_data_2":     self._user_data[1].text().strip() or None,
            "user_data_3":     self._user_data[2].text().strip() or None,
            "user_data_4":     self._user_data[3].text().strip() or None,
            "gc_note":         self._gc_note.toPlainText().strip() or None,
        }

    def get_user_note_data(self) -> dict:
        """Fields for the cache's UserNote row (see save_related())."""
        return {
            "note":          self._user_note.toPlainText().strip() or None,
            "corrected_lat": self._corr_lat,
            "corrected_lon": self._corr_lon,
        }

    def get_waypoints_data(self) -> Optional[list[dict]]:
        """Child waypoints as dicts (id=None for new ones), or None when the
        waypoint list isn't editable (custom waypoint mode)."""
        if self._is_custom:
            return None
        return [dict(wp) for wp in self._waypoints]

    def save_related(self, session, cache_row: Cache) -> None:
        """Write UserNote and child waypoints for cache_row within session.

        cache_row must be persistent (flushed) so it has an id. Its
        user_note and waypoints relationships are loaded lazily on access.
        """
        self._save_user_note(session, cache_row)
        waypoints = self.get_waypoints_data()
        if waypoints is not None:
            self._save_waypoints(session, cache_row, waypoints)

    def _save_user_note(self, session, cache_row: Cache) -> None:
        data = self.get_user_note_data()
        note = cache_row.user_note
        if note is None:
            if data["note"] is None and data["corrected_lat"] is None:
                return
            note = UserNote(cache_id=cache_row.id)
            session.add(note)
        note.note = data["note"]
        note.corrected_lat = data["corrected_lat"]
        note.corrected_lon = data["corrected_lon"]
        note.is_corrected = data["corrected_lat"] is not None and data["corrected_lon"] is not None

    @staticmethod
    def _save_waypoints(session, cache_row: Cache, waypoints: list[dict]) -> None:
        existing = {wp.id: wp for wp in cache_row.waypoints}
        kept_ids = {wp["id"] for wp in waypoints if wp.get("id") is not None}
        # Fjern og flush først — unit-of-work'en indsætter ellers før den
        # sletter, og en genbrugt wp_code ville ramme unik-constrainten.
        for wp_id, wp in existing.items():
            if wp_id not in kept_ids:
                cache_row.waypoints.remove(wp)   # delete-orphan cascade
        session.flush()
        for data in waypoints:
            fields = {f: data.get(f) for f in _WP_FIELDS}
            data_id = data.get("id")
            row = existing.get(data_id) if data_id is not None else None
            if row is None:
                cache_row.waypoints.append(Waypoint(
                    **fields, parent_gc_code=cache_row.gc_code, created_by_user=True,
                ))
            else:
                for f, v in fields.items():
                    setattr(row, f, v)
        cache_row.waypoint_count = len(waypoints)

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
            "found_date":        self._found_date.value() if self._found.isChecked() else None,
            "dnf_date":          self._dnf_date.value() if self._dnf.isChecked() else None,
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


class ChildWaypointDialog(QDialog):
    """Tilføj/rediger et child-waypoint (parkering, stage, final …) på en cache.

    Arbejder kun på en dict — WaypointDialog.save_related() skriver til DB.
    """

    def __init__(
        self,
        parent=None,
        wp: Optional[dict] = None,
        origin: tuple[Optional[float], Optional[float]] = (None, None),
    ):
        super().__init__(parent)
        self._wp = wp or {}
        self._origin = origin
        self._lat: Optional[float] = self._wp.get("latitude")
        self._lon: Optional[float] = self._wp.get("longitude")
        self.setWindowTitle(
            tr("wp_child_dialog_title_edit") if wp else tr("wp_child_dialog_title_add"))
        self.setMinimumWidth(460)
        self._setup_ui()
        self._populate()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)

        self._prefix = QLineEdit()
        self._prefix.setMaxLength(8)
        self._prefix.textEdited.connect(self._on_prefix_edited)
        form.addRow(tr("wp_child_label_prefix"), self._prefix)

        self._wp_type = QComboBox()
        self._wp_type.setEditable(True)
        self._wp_type.addItems(CHILD_WP_TYPES)
        form.addRow(_label("col_type"), self._wp_type)

        self._name = QLineEdit()
        form.addRow(_label("col_name"), self._name)

        self._coord_input = QLineEdit()
        self._coord_input.setPlaceholderText(tr("coord_conv_placeholder"))
        self._coord_input.textChanged.connect(self._on_coord_changed)
        self._coord_feedback = QLabel("")
        self._coord_feedback.setStyleSheet("font-size: 10px;")
        coord_box = QVBoxLayout()
        coord_box.setContentsMargins(0, 0, 0, 0)
        coord_box.setSpacing(2)
        coord_box.addWidget(self._coord_input)
        coord_box.addWidget(self._coord_feedback)
        coord_widget = QWidget()
        coord_widget.setLayout(coord_box)
        form.addRow(_label("detail_coords"), coord_widget)

        self._description = QTextEdit()
        self._description.setMaximumHeight(70)
        form.addRow(_label("detail_tab_desc"), self._description)

        self._comment = QTextEdit()
        self._comment.setMaximumHeight(70)
        form.addRow(tr("wp_child_label_comment"), self._comment)

        self._wp_code = QLineEdit()
        self._wp_code.setMaxLength(16)
        form.addRow(tr("wp_child_label_code"), self._wp_code)

        self._url = QLineEdit()
        form.addRow(_label("col_url"), self._url)

        self._wp_date = OptionalDateEdit()
        form.addRow(tr("wp_child_label_date"), self._wp_date)

        self._flag = QCheckBox(tr("wp_child_cb_flag"))
        form.addRow("", self._flag)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("cancel"))
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate(self) -> None:
        wp = self._wp
        self._prefix.setText(wp.get("prefix") or "")
        wp_type = wp.get("wp_type") or ""
        if wp_type:
            self._wp_type.setCurrentText(wp_type)
        self._name.setText(wp.get("name") or "")
        if self._lat is not None and self._lon is not None:
            self._coord_input.setText(
                format_coords(self._lat, self._lon, get_settings().coord_format))
        elif self._origin[0] is not None and self._origin[1] is not None:
            # Waypoint uden koordinater: vis cachens koordinater som
            # placeholder, så brugeren har et udgangspunkt at taste ud fra
            self._coord_input.setPlaceholderText(
                format_coords(self._origin[0], self._origin[1], get_settings().coord_format))
        self._description.setPlainText(wp.get("description") or "")
        self._comment.setPlainText(wp.get("comment") or "")
        self._wp_code.setText(wp.get("wp_code") or "")
        self._url.setText(wp.get("url") or "")
        self._wp_date.set_value(wp.get("wp_date"))
        self._flag.setChecked(bool(wp.get("wp_flag")))

    def _on_prefix_edited(self, text: str) -> None:
        # Kendt prefix (PK, FN …) foreslår typen
        wp_type = KNOWN_PREFIXES.get(text.strip().upper())
        if wp_type:
            self._wp_type.setCurrentText(wp_type)

    def _on_coord_changed(self, text: str) -> None:
        text = text.strip()
        if not text:
            self._lat = self._lon = None
            self._coord_feedback.setText("")
            return
        result = parse_coords(text)
        if result is not None:
            self._lat, self._lon = result
            self._coord_feedback.setText(
                f"✓  {format_coords(self._lat, self._lon, get_settings().coord_format)}")
            self._coord_feedback.setStyleSheet("color: #2e7d32; font-size: 10px;")
        else:
            self._lat = self._lon = None
            self._coord_feedback.setText(tr("coord_conv_parse_error"))
            self._coord_feedback.setStyleSheet("color: #c62828; font-size: 10px;")

    def _validate_and_accept(self) -> None:
        if not self._prefix.text().strip():
            QMessageBox.warning(self, tr("warning"), tr("wp_child_val_prefix_required"))
            return
        if self._coord_input.text().strip() and self._lat is None:
            QMessageBox.warning(self, tr("warning"), tr("coord_conv_parse_error"))
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "prefix":      self._prefix.text().strip().upper(),
            "wp_type":     self._wp_type.currentText().strip() or "Waypoint",
            "name":        self._name.text().strip() or None,
            "description": self._description.toPlainText().strip() or None,
            "comment":     self._comment.toPlainText().strip() or None,
            "latitude":    self._lat,
            "longitude":   self._lon,
            "wp_code":     self._wp_code.text().strip().upper() or None,
            "url":         self._url.text().strip() or None,
            "wp_date":     self._wp_date.value(),
            "wp_flag":     self._flag.isChecked(),
        }

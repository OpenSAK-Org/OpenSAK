"""
src/opensak/gui/dialogs/coord_converter_dialog.py — Coordinate converter popup.

The user can type coordinates in any of the three supported formats.
All three formats are shown simultaneously and update live.
"""

from __future__ import annotations
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox,
    QDialogButtonBox, QApplication, QFrame, QSizePolicy
)
from PySide6.QtGui import QFont, QFontMetrics

from opensak.coords import parse_coords, format_coords
from opensak.utils.types import CoordFormat
from opensak.lang import tr
from opensak.gui.theme import hint_style

# Widest string any format can produce (DMS at extreme lat/lon values).
# Used to compute a consistent minimum width for all coordinate fields.
_WIDEST_COORD = 'S89° 59\' 59.96"  W179° 59\' 59.96"'


class CoordConverterDialog(QDialog):
    """
    Popup coordinate converter.
    Accepts DD, DMM or DMS input and shows all three formats live.
    Optionally pre-filled with a known lat/lon.
    """

    def __init__(self, lat: float | None = None, lon: float | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("coord_conv_title"))
        self.setMinimumWidth(480)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._lat: float | None = lat
        self._lon: float | None = lon
        self._setup_ui()
        if lat is not None and lon is not None:
            self._input.blockSignals(True)
            self._input.setText(format_coords(lat, lon, CoordFormat.DMM))
            self._input.blockSignals(False)
            self._update_outputs(lat, lon)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── Input ─────────────────────────────────────────────────────────────
        in_group = QGroupBox(tr("coord_conv_input_group"))
        in_layout = QVBoxLayout(in_group)

        hint = QLabel(tr("coord_conv_input_hint"))
        hint.setStyleSheet(hint_style())
        hint.setWordWrap(True)
        in_layout.addWidget(hint)

        self._input = QLineEdit()
        self._input.setPlaceholderText(tr("coord_conv_placeholder"))
        font = QFont()
        font.setFamily("monospace")
        self._input.setFont(font)
        self._input.setMinimumWidth(
            QFontMetrics(font).horizontalAdvance(_WIDEST_COORD) + 16
        )
        self._input.textChanged.connect(self._on_input_changed)
        in_layout.addWidget(self._input)

        self._error_lbl = QLabel("")
        self._error_lbl.setStyleSheet("color: #c62828; font-size: 10px;")
        in_layout.addWidget(self._error_lbl)

        layout.addWidget(in_group)

        # ── Output ────────────────────────────────────────────────────────────
        out_group = QGroupBox(tr("coord_conv_output_group"))
        out_form = QFormLayout(out_group)
        out_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        mono = QFont()
        mono.setFamily("monospace")
        mono.setPointSize(10)

        self._dmm_row = self._make_output_row(mono)
        self._dms_row = self._make_output_row(mono)
        self._dd_row  = self._make_output_row(mono)

        out_form.addRow("DMM:", self._dmm_row[0])
        out_form.addRow("DMS:", self._dms_row[0])
        out_form.addRow("DD:", self._dd_row[0])

        layout.addWidget(out_group)

        # ── Maps buttons ──────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        maps_row = QHBoxLayout()
        maps_row.addWidget(QLabel(tr("coord_conv_open_in")))
        self._osm_btn = QPushButton("OpenStreetMap")
        self._osm_btn.setEnabled(False)
        self._osm_btn.clicked.connect(self._open_osm)
        maps_row.addWidget(self._osm_btn)

        self._gmaps_btn = QPushButton("Google Maps")
        self._gmaps_btn.setEnabled(False)
        self._gmaps_btn.clicked.connect(self._open_gmaps)
        maps_row.addWidget(self._gmaps_btn)
        maps_row.addStretch()
        layout.addLayout(maps_row)

        # ── Close button ──────────────────────────────────────────────────────
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _make_output_row(self, font: QFont) -> tuple:
        """Return (container_widget, line_edit, copy_button)."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit()
        edit.setReadOnly(True)
        edit.setFont(font)
        edit.setPlaceholderText("—")
        edit.setMinimumWidth(
            QFontMetrics(font).horizontalAdvance(_WIDEST_COORD) + 16
        )
        copy_btn = QPushButton(tr("coord_conv_copy_btn"))
        # #927: size to the translated label instead of a hardcoded width,
        # so longer translations are never clipped.
        copy_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        copy_btn.setEnabled(False)
        copy_btn.setAutoDefault(False)
        copy_btn.clicked.connect(lambda: self._copy(edit.text()))
        row.addWidget(edit)
        row.addWidget(copy_btn)
        # wrap in a widget so QFormLayout gets a single widget
        container = QFrame()
        container.setLayout(row)
        return container, edit, copy_btn

    def _on_input_changed(self, text: str) -> None:
        result = parse_coords(text)
        if result:
            lat, lon = result
            self._lat, self._lon = lat, lon
            self._error_lbl.setText("")
            self._update_outputs(lat, lon)
        else:
            self._error_lbl.setText(
                tr("coord_conv_parse_error") if text.strip() else ""
            )
            self._clear_outputs()

    def _update_outputs(self, lat: float, lon: float) -> None:
        dmm = format_coords(lat, lon, CoordFormat.DMM)
        dms = format_coords(lat, lon, CoordFormat.DMS)
        dd  = format_coords(lat, lon, CoordFormat.DD)

        self._dmm_row[1].setText(dmm)
        self._dms_row[1].setText(dms)
        self._dd_row[1].setText(dd)

        for _, _, btn in (self._dmm_row, self._dms_row, self._dd_row):
            btn.setEnabled(True)

        self._osm_btn.setEnabled(True)
        self._gmaps_btn.setEnabled(True)

    def _clear_outputs(self) -> None:
        self._lat = None
        self._lon = None
        for _, edit, btn in (self._dmm_row, self._dms_row, self._dd_row):
            edit.clear()
            btn.setEnabled(False)
        self._osm_btn.setEnabled(False)
        self._gmaps_btn.setEnabled(False)

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)

    def _open_osm(self) -> None:
        if self._lat is not None:
            webbrowser.open(
                f"https://www.openstreetmap.org/?mlat={self._lat}&mlon={self._lon}&zoom=16"
            )

    def _open_gmaps(self) -> None:
        if self._lat is not None:
            webbrowser.open(
                f"https://www.google.com/maps?q={self._lat},{self._lon}"
            )

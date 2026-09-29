"""
src/opensak/gui/dialogs/midpoint_dialog.py — Midpoint calculator.

Finds the geographical midpoint between two coordinates using the
spherical mean (average of unit vectors on the sphere), which gives
the correct great-circle midpoint rather than a simple average of
lat/lon values.
"""

from __future__ import annotations
import math
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox,
    QDialogButtonBox, QFrame, QApplication, QSizePolicy
)
from PySide6.QtGui import QFont

from opensak.coords import format_coords, parse_coords
from opensak.utils.types import CoordFormat
from opensak.lang import tr


def _midpoint(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    """
    Return the great-circle midpoint between two (lat, lon) pairs in decimal degrees.
    Uses the spherical mean (average of Cartesian unit vectors).
    """
    lat1r, lon1r = math.radians(lat1), math.radians(lon1)
    lat2r, lon2r = math.radians(lat2), math.radians(lon2)

    x = (math.cos(lat1r) * math.cos(lon1r) + math.cos(lat2r) * math.cos(lon2r)) / 2
    y = (math.cos(lat1r) * math.sin(lon1r) + math.cos(lat2r) * math.sin(lon2r)) / 2
    z = (math.sin(lat1r) + math.sin(lat2r)) / 2

    lon_m = math.atan2(y, x)
    hyp   = math.sqrt(x * x + y * y)
    lat_m = math.atan2(z, hyp)

    return math.degrees(lat_m), math.degrees(lon_m)


class MidpointDialog(QDialog):
    """Geographical midpoint calculator."""

    def __init__(self, lat: float | None = None, lon: float | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("mid_title"))
        self.setMinimumWidth(500)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._result_lat: float | None = None
        self._result_lon: float | None = None
        self._setup_ui()
        # Pre-fill point A with selected cache if available
        if lat is not None and lon is not None:
            self._input_a.setText(format_coords(lat, lon, CoordFormat.DMM))

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        mono = QFont()
        mono.setFamily("monospace")
        mono.setPointSize(10)

        # ── Punkt A ───────────────────────────────────────────────────────────
        a_group = QGroupBox(tr("mid_point_a"))
        a_layout = QVBoxLayout(a_group)
        self._input_a = QLineEdit()
        self._input_a.setPlaceholderText(tr("coord_conv_placeholder"))
        self._input_a.setFont(mono)
        self._input_a.textChanged.connect(self._calculate)
        self._error_a = QLabel("")
        self._error_a.setStyleSheet("color: #c62828; font-size: 10px;")
        a_layout.addWidget(self._input_a)
        a_layout.addWidget(self._error_a)
        layout.addWidget(a_group)

        # ── Punkt B ───────────────────────────────────────────────────────────
        b_group = QGroupBox(tr("mid_point_b"))
        b_layout = QVBoxLayout(b_group)
        self._input_b = QLineEdit()
        self._input_b.setPlaceholderText(tr("coord_conv_placeholder"))
        self._input_b.setFont(mono)
        self._input_b.textChanged.connect(self._calculate)
        self._error_b = QLabel("")
        self._error_b.setStyleSheet("color: #c62828; font-size: 10px;")
        b_layout.addWidget(self._input_b)
        b_layout.addWidget(self._error_b)
        layout.addWidget(b_group)

        # ── Resultat ──────────────────────────────────────────────────────────
        res_group = QGroupBox(tr("mid_result_group"))
        res_form = QFormLayout(res_group)
        res_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._dmm_row = self._make_result_row(mono)
        self._dms_row = self._make_result_row(mono)
        self._dd_row  = self._make_result_row(mono)

        res_form.addRow("DMM:", self._dmm_row[0])
        res_form.addRow("DMS:", self._dms_row[0])
        res_form.addRow("DD:",  self._dd_row[0])

        layout.addWidget(res_group)

        # ── Kort knapper ──────────────────────────────────────────────────────
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

        # ── Luk ───────────────────────────────────────────────────────────────
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _make_result_row(self, font: QFont) -> tuple:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit()
        edit.setReadOnly(True)
        edit.setFont(font)
        edit.setPlaceholderText("—")
        copy_btn = QPushButton(tr("coord_conv_copy_btn"))
        # #927: size to the translated label instead of a hardcoded width,
        # so longer translations are never clipped.
        copy_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        copy_btn.setEnabled(False)
        copy_btn.clicked.connect(lambda: self._copy(edit.text()))
        row.addWidget(edit)
        row.addWidget(copy_btn)
        container = QFrame()
        container.setLayout(row)
        return container, edit, copy_btn

    def _calculate(self) -> None:
        text_a = self._input_a.text().strip()
        text_b = self._input_b.text().strip()

        res_a = parse_coords(text_a) if text_a else None
        res_b = parse_coords(text_b) if text_b else None

        self._error_a.setText(
            tr("coord_conv_parse_error") if text_a and not res_a else ""
        )
        self._error_b.setText(
            tr("coord_conv_parse_error") if text_b and not res_b else ""
        )

        if res_a and res_b:
            lat_m, lon_m = _midpoint(res_a[0], res_a[1], res_b[0], res_b[1])
            self._result_lat = lat_m
            self._result_lon = lon_m
            self._dmm_row[1].setText(format_coords(lat_m, lon_m, CoordFormat.DMM))
            self._dms_row[1].setText(format_coords(lat_m, lon_m, CoordFormat.DMS))
            self._dd_row[1].setText(format_coords(lat_m, lon_m, CoordFormat.DD))
            for _, _, btn in (self._dmm_row, self._dms_row, self._dd_row):
                btn.setEnabled(True)
            self._osm_btn.setEnabled(True)
            self._gmaps_btn.setEnabled(True)
        else:
            self._clear_results()

    def _clear_results(self) -> None:
        self._result_lat = None
        self._result_lon = None
        for _, edit, btn in (self._dmm_row, self._dms_row, self._dd_row):
            edit.clear()
            btn.setEnabled(False)
        self._osm_btn.setEnabled(False)
        self._gmaps_btn.setEnabled(False)

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)

    def _open_osm(self) -> None:
        if self._result_lat is not None:
            webbrowser.open(
                f"https://www.openstreetmap.org/?mlat={self._result_lat}"
                f"&mlon={self._result_lon}&zoom=14"
            )

    def _open_gmaps(self) -> None:
        if self._result_lat is not None:
            webbrowser.open(
                f"https://www.google.com/maps?q={self._result_lat},{self._result_lon}"
            )

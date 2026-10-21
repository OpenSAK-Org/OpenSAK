"""
src/opensak/gui/dialogs/distance_bearing_dialog.py — Distance & bearing calculator.

Given two coordinates, calculates:
  - Distance in metres / km (or feet / miles)
  - Forward bearing  A → B  (degrees, 0 = North, clockwise)
  - Reverse bearing  B → A

Uses the haversine formula on a spherical earth (WGS-84 mean radius).
"""

from __future__ import annotations
import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox,
    QDialogButtonBox, QFrame, QApplication
)
from PySide6.QtGui import QFont

from opensak.coords import format_coords, parse_coords
from opensak.utils.types import CoordFormat
from opensak.gui.settings import get_settings
from opensak.lang import tr
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.utils.constants import EARTH_RADIUS_M


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in metres between two points (decimal degrees)."""
    lat1r, lon1r = math.radians(lat1), math.radians(lon1)
    lat2r, lon2r = math.radians(lat2), math.radians(lon2)
    dlat = lat2r - lat1r
    dlon = lon2r - lon1r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return forward bearing in degrees (0–360) from point 1 to point 2."""
    lat1r, lon1r = math.radians(lat1), math.radians(lon1)
    lat2r, lon2r = math.radians(lat2), math.radians(lon2)
    dlon = lon2r - lon1r
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _format_distance(metres: float, use_miles: bool) -> str:
    if use_miles:
        feet = metres / 0.3048
        miles = metres / 1609.344
        if feet < 1000:
            return f"{feet:.0f} ft"
        return f"{miles:.3f} mi  ({feet:.0f} ft)"
    else:
        if metres < 1000:
            return f"{metres:.1f} m"
        return f"{metres / 1000:.3f} km  ({metres:.0f} m)"


class DistanceBearingDialog(QDialog):
    """Distance and bearing calculator between two coordinates."""

    def __init__(self, lat: float | None = None, lon: float | None = None, parent=None):
        super().__init__(parent)
        clamp_dialog_height_to_screen(self, parent)
        self.setWindowTitle(tr("dist_title"))
        self.setMinimumWidth(500)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._setup_ui()
        if lat is not None and lon is not None:
            self._input_a.setText(format_coords(lat, lon, CoordFormat.DMM))

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        mono = QFont()
        mono.setFamily("monospace")
        mono.setPointSize(10)

        big = QFont()
        big.setPointSize(13)
        big.setBold(True)

        # ── Punkt A ───────────────────────────────────────────────────────────
        a_group = QGroupBox(tr("dist_point_a"))
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
        b_group = QGroupBox(tr("dist_point_b"))
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

        # ── Resultater ────────────────────────────────────────────────────────
        res_group = QGroupBox(tr("dist_result_group"))
        res_form = QFormLayout(res_group)
        res_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._dist_lbl = QLabel("—")
        self._dist_lbl.setFont(big)
        self._dist_lbl.setStyleSheet("color: #1565c0;")
        res_form.addRow(tr("dist_distance_label"), self._dist_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        res_form.addRow(sep)

        self._fwd_lbl = QLabel("—")
        self._fwd_lbl.setFont(mono)
        res_form.addRow(tr("dist_bearing_fwd"), self._fwd_lbl)

        self._rev_lbl = QLabel("—")
        self._rev_lbl.setFont(mono)
        res_form.addRow(tr("dist_bearing_rev"), self._rev_lbl)

        layout.addWidget(res_group)

        # ── Luk ───────────────────────────────────────────────────────────────
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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
            lat1, lon1 = res_a
            lat2, lon2 = res_b
            dist_m = _haversine(lat1, lon1, lat2, lon2)
            fwd = _bearing(lat1, lon1, lat2, lon2)
            rev = _bearing(lat2, lon2, lat1, lon1)

            use_miles = get_settings().use_miles
            self._dist_lbl.setText(_format_distance(dist_m, use_miles))
            self._fwd_lbl.setText(f"{fwd:.2f}°  ({self._compass(fwd)})")
            self._rev_lbl.setText(f"{rev:.2f}°  ({self._compass(rev)})")
        else:
            self._dist_lbl.setText("—")
            self._fwd_lbl.setText("—")
            self._rev_lbl.setText("—")

    @staticmethod
    def _compass(deg: float) -> str:
        """Return an 8-point compass direction for a bearing in degrees."""
        dirs = tr("bearing_dirs").split()
        idx = round(deg / 45) % 8
        return dirs[idx]

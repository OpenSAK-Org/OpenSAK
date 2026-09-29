"""
src/opensak/gui/dialogs/projection_dialog.py — Coordinate projection tool.

Given a start coordinate, a bearing (degrees) and a distance (metres),
calculate the projected destination coordinate.

Uses the Vincenty/haversine formula on a spherical earth (WGS-84 mean radius).
Accuracy is well within 1 m for the distances relevant to geocaching.
"""

from __future__ import annotations
import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox,
    QDialogButtonBox, QFrame, QDoubleSpinBox, QApplication, QSizePolicy
)
from PySide6.QtGui import QFont

from opensak.coords import format_coords, parse_coords
from opensak.utils.types import CoordFormat
from opensak.gui.settings import get_settings
from opensak.lang import tr
from opensak.gui.theme import hint_style
from opensak.utils.constants import EARTH_RADIUS_M


def _project(lat_deg: float, lon_deg: float,
             bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """
    Return (lat, lon) of the point reached by travelling *distance_m* metres
    from (lat_deg, lon_deg) in direction *bearing_deg* (0 = North, clockwise).
    """
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    brng = math.radians(bearing_deg)
    d_r = distance_m / EARTH_RADIUS_M

    lat2 = math.asin(
        math.sin(lat) * math.cos(d_r)
        + math.cos(lat) * math.sin(d_r) * math.cos(brng)
    )
    lon2 = lon + math.atan2(
        math.sin(brng) * math.sin(d_r) * math.cos(lat),
        math.cos(d_r) - math.sin(lat) * math.sin(lat2)
    )
    return math.degrees(lat2), math.degrees(lon2)


class ProjectionDialog(QDialog):
    """
    Geocaching projection tool.
    Input:  start coordinate + bearing (°) + distance (m or ft)
    Output: projected destination in all three coordinate formats.
    """

    def __init__(self, lat: float | None = None, lon: float | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("proj_title"))
        self.setMinimumWidth(500)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._result_lat: float | None = None
        self._result_lon: float | None = None
        self._setup_ui()
        if lat is not None and lon is not None:
            self._start_input.setText(format_coords(lat, lon, CoordFormat.DMM))

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        mono = QFont()
        mono.setFamily("monospace")
        mono.setPointSize(10)

        # ── Start koordinat ───────────────────────────────────────────────────
        start_group = QGroupBox(tr("proj_start_group"))
        start_layout = QVBoxLayout(start_group)

        hint = QLabel(tr("proj_start_hint"))
        hint.setStyleSheet(hint_style())
        hint.setWordWrap(True)
        start_layout.addWidget(hint)

        self._start_input = QLineEdit()
        self._start_input.setPlaceholderText(tr("coord_conv_placeholder"))
        self._start_input.setFont(mono)
        self._start_input.textChanged.connect(self._on_input_changed)
        start_layout.addWidget(self._start_input)

        self._start_error = QLabel("")
        self._start_error.setStyleSheet("color: #c62828; font-size: 10px;")
        start_layout.addWidget(self._start_error)

        layout.addWidget(start_group)

        # ── Retning og afstand ────────────────────────────────────────────────
        params_group = QGroupBox(tr("proj_params_group"))
        params_form = QFormLayout(params_group)

        self._bearing = QDoubleSpinBox()
        self._bearing.setRange(0.0, 359.999)
        self._bearing.setDecimals(3)
        self._bearing.setSingleStep(1.0)
        self._bearing.setSuffix("°")
        self._bearing.valueChanged.connect(self._on_input_changed)
        params_form.addRow(tr("proj_bearing_label"), self._bearing)

        dist_row = QHBoxLayout()
        self._distance = QDoubleSpinBox()
        self._distance.setRange(0.0, 999_999.0)
        self._distance.setDecimals(1)
        self._distance.setSingleStep(10.0)
        self._distance.valueChanged.connect(self._on_input_changed)
        dist_row.addWidget(self._distance)

        self._dist_unit_lbl = QLabel()
        dist_row.addWidget(self._dist_unit_lbl)
        dist_row.addStretch()

        dist_container = QFrame()
        dist_container.setLayout(dist_row)
        params_form.addRow(tr("dist_distance_label"), dist_container)

        layout.addWidget(params_group)

        # ── Resultat ──────────────────────────────────────────────────────────
        result_group = QGroupBox(tr("proj_result_group"))
        result_form = QFormLayout(result_group)
        result_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._dmm_row = self._make_result_row(mono)
        self._dms_row = self._make_result_row(mono)
        self._dd_row  = self._make_result_row(mono)

        result_form.addRow("DMM:", self._dmm_row[0])
        result_form.addRow("DMS:", self._dms_row[0])
        result_form.addRow("DD:",  self._dd_row[0])

        layout.addWidget(result_group)

        # ── Maps knapper ──────────────────────────────────────────────────────
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

        # Sæt enhed-label
        self._update_unit_label()

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

    def _update_unit_label(self) -> None:
        if get_settings().use_miles:
            self._dist_unit_lbl.setText(tr("proj_unit_ft"))
            self._distance.setSuffix(" ft")
        else:
            self._dist_unit_lbl.setText(tr("proj_unit_m"))
            self._distance.setSuffix(" m")

    def _on_input_changed(self, *_) -> None:
        text = self._start_input.text().strip()
        result = parse_coords(text) if text else None

        if text and not result:
            self._start_error.setText(tr("coord_conv_parse_error"))
            self._clear_results()
            return

        self._start_error.setText("")

        if result and self._distance.value() > 0:
            lat, lon = result
            bearing = self._bearing.value()
            dist_m = self._distance.value()
            if get_settings().use_miles:
                dist_m = dist_m * 0.3048   # feet → metres
            r_lat, r_lon = _project(lat, lon, bearing, dist_m)
            self._result_lat = r_lat
            self._result_lon = r_lon
            self._update_results(r_lat, r_lon)
        else:
            self._clear_results()

    def _update_results(self, lat: float, lon: float) -> None:
        self._dmm_row[1].setText(format_coords(lat, lon, CoordFormat.DMM))
        self._dms_row[1].setText(format_coords(lat, lon, CoordFormat.DMS))
        self._dd_row[1].setText(format_coords(lat, lon, CoordFormat.DD))
        for _, _, btn in (self._dmm_row, self._dms_row, self._dd_row):
            btn.setEnabled(True)
        self._osm_btn.setEnabled(True)
        self._gmaps_btn.setEnabled(True)

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
            import webbrowser
            webbrowser.open(
                f"https://www.openstreetmap.org/?mlat={self._result_lat}"
                f"&mlon={self._result_lon}&zoom=16"
            )

    def _open_gmaps(self) -> None:
        if self._result_lat is not None:
            import webbrowser
            webbrowser.open(
                f"https://www.google.com/maps?q={self._result_lat},{self._result_lon}"
            )

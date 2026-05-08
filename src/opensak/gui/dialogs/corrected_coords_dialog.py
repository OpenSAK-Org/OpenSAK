"""
src/opensak/gui/dialogs/corrected_coords_dialog.py

Dialog til at indtaste eller redigere korrigerede koordinater for en mystery cache.
Understøtter DMM, DMS og DD format via coords-parseren.

Viser originale cache-koordinater og de korrigerede koordinater i alle formater
med kopierknapper.
"""

from __future__ import annotations
from typing import Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QPushButton, QDialogButtonBox, QFrame, QApplication
)
from PySide6.QtGui import QFont

from opensak.lang import tr
from opensak.coords import format_coords, parse_coords
from opensak.gui.settings import get_settings
from opensak.utils.types import GcCode, CoordFormat


class CorrectedCoordsDialog(QDialog):
    """
    Dialog til at indtaste korrigerede koordinater.
    Accepterer DMM, DMS eller DD format.
    Viser originale og korrigerede koordinater i alle formater med kopierknapper.
    """

    def __init__(
        self,
        gc_code: GcCode,
        orig_lat: Optional[float] = None,
        orig_lon: Optional[float] = None,
        corrected_lat: Optional[float] = None,
        corrected_lon: Optional[float] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._gc_code = gc_code
        self._orig_lat = orig_lat
        self._orig_lon = orig_lon
        self._lat: Optional[float] = None
        self._lon: Optional[float] = None
        self.setWindowTitle(tr("corrected_dialog_title"))
        self.setMinimumWidth(480)
        self._setup_ui(corrected_lat, corrected_lon)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def _make_coords_panel(self, lat: float, lon: float) -> QFrame:
        """Byg et panel med koordinater i alle 3 formater + kopierknapper."""
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        grid = QGridLayout(panel)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(4)
        grid.setColumnStretch(1, 1)

        formats = [
            ("DMM", CoordFormat.DMM),
            ("DMS", CoordFormat.DMS),
            ("DD",  CoordFormat.DD),
        ]

        for row, (label, fmt) in enumerate(formats):
            coord_str = format_coords(lat, lon, fmt)

            lbl = QLabel(f"<b>{label}</b>")
            lbl.setFixedWidth(36)
            grid.addWidget(lbl, row, 0)

            val = QLineEdit(coord_str)
            val.setReadOnly(True)
            val.setStyleSheet("background: transparent; border: none; font-family: monospace;")
            grid.addWidget(val, row, 1)

            btn = QPushButton(tr("coord_conv_copy_btn"))
            btn.setFixedWidth(70)
            btn.setToolTip(tr("corrected_dialog_copy_tooltip"))
            btn.clicked.connect(lambda checked=False, s=coord_str: self._copy(s))
            grid.addWidget(btn, row, 2)

        return panel

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)

    # ── UI setup ──────────────────────────────────────────────────────────────

    def _setup_ui(
        self, current_lat: Optional[float], current_lon: Optional[float]
    ) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Overskrift
        title = QLabel(tr("corrected_dialog_heading", gc_code=self._gc_code))
        font = QFont()
        font.setBold(True)
        font.setPointSize(11)
        title.setFont(font)
        layout.addWidget(title)

        layout.addWidget(self._make_separator())

        # ── Originale koordinater ─────────────────────────────────────────────
        if self._orig_lat is not None and self._orig_lon is not None:
            orig_lbl = QLabel(tr("corrected_dialog_original"))
            orig_lbl.setStyleSheet("font-weight: bold; font-size: 10px; color: gray;")
            layout.addWidget(orig_lbl)
            layout.addWidget(self._make_coords_panel(self._orig_lat, self._orig_lon))
            layout.addWidget(self._make_separator())

        # ── Input felt ────────────────────────────────────────────────────────
        input_frame = QFrame()
        input_frame.setFrameShape(QFrame.Shape.StyledPanel)
        input_layout = QVBoxLayout(input_frame)
        input_layout.setContentsMargins(8, 8, 8, 8)
        input_layout.setSpacing(4)

        input_label = QLabel(tr("corrected_dialog_input_label"))
        input_layout.addWidget(input_label)

        self._input = QLineEdit()
        self._input.setPlaceholderText(tr("coord_conv_placeholder"))

        if current_lat is not None and current_lon is not None:
            fmt = get_settings().coord_format
            self._input.setText(format_coords(current_lat, current_lon, fmt))

        input_layout.addWidget(self._input)

        self._error_lbl = QLabel("")
        self._error_lbl.setStyleSheet("color: #c62828; font-size: 10px;")
        self._error_lbl.setWordWrap(True)
        input_layout.addWidget(self._error_lbl)

        layout.addWidget(input_frame)

        # ── Korrigerede koordinater (vises når input er gyldigt) ──────────────
        self._corrected_lbl = QLabel(tr("corrected_dialog_corrected"))
        self._corrected_lbl.setStyleSheet("font-weight: bold; font-size: 10px; color: gray;")
        self._corrected_lbl.setVisible(False)
        layout.addWidget(self._corrected_lbl)

        self._corrected_panel_container = QVBoxLayout()
        self._corrected_panel_container.setContentsMargins(0, 0, 0, 0)
        self._corrected_panel_widget: Optional[QFrame] = None
        layout.addLayout(self._corrected_panel_container)

        # ── Knapper ───────────────────────────────────────────────────────────
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        self._ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setEnabled(False)
        layout.addWidget(btn_box)

        self._input.textChanged.connect(self._on_input_changed)
        if self._input.text():
            self._on_input_changed(self._input.text())

    # ── Input handling ────────────────────────────────────────────────────────

    def _on_input_changed(self, text: str) -> None:
        """Parse koordinat-input og opdater korrigeret koordinat-panel."""
        text = text.strip()
        if not text:
            self._error_lbl.setText("")
            self._ok_btn.setEnabled(False)
            self._lat = None
            self._lon = None
            self._set_corrected_panel(None, None)
            return

        try:
            lat, lon = parse_coords(text)
            self._lat = lat
            self._lon = lon
            self._error_lbl.setText("")
            self._ok_btn.setEnabled(True)
            self._set_corrected_panel(lat, lon)
        except (ValueError, TypeError):
            self._lat = None
            self._lon = None
            self._error_lbl.setText(tr("coord_conv_parse_error"))
            self._ok_btn.setEnabled(False)
            self._set_corrected_panel(None, None)

    def _set_corrected_panel(self, lat: Optional[float], lon: Optional[float]) -> None:
        """Vis eller skjul korrigeret koordinat-panel."""
        # Fjern gammelt panel hvis det findes
        if self._corrected_panel_widget is not None:
            self._corrected_panel_container.removeWidget(self._corrected_panel_widget)
            self._corrected_panel_widget.deleteLater()
            self._corrected_panel_widget = None

        if lat is not None and lon is not None:
            self._corrected_lbl.setVisible(True)
            self._corrected_panel_widget = self._make_coords_panel(lat, lon)
            self._corrected_panel_container.addWidget(self._corrected_panel_widget)
            self.adjustSize()
        else:
            self._corrected_lbl.setVisible(False)
            self.adjustSize()

    def _on_accept(self) -> None:
        if self._lat is not None and self._lon is not None:
            self.accept()

    def get_coords(self) -> Tuple[Optional[float], Optional[float]]:
        """Returner de parsede koordinater."""
        return self._lat, self._lon

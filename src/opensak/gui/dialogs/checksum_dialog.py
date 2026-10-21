"""
src/opensak/gui/dialogs/checksum_dialog.py — Coordinate digit checksum tool.

Extracts all digits from a coordinate string and shows:
  - Sum of all digits
  - Sum per hemisphere part (N/S part and E/W part separately)
  - The individual digits found

Example: N55 47.250 E012 25.000
  Digits: 5,5,4,7,2,5,0,0,1,2,2,5,0,0,0
  N/S sum: 5+5+4+7+2+5+0 = 28
  E/W sum: 0+1+2+2+5+0+0+0 = 10
  Total:   38
"""

from __future__ import annotations
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox,
    QDialogButtonBox, QFrame, QApplication
)
from PySide6.QtGui import QFont

from opensak.coords import format_coords, parse_coords
from opensak.utils.types import CoordFormat
from opensak.lang import tr
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.gui.theme import hint_style


def _extract_digits(text: str) -> list[int]:
    """Return all decimal digits found in *text* as a list of ints."""
    return [int(ch) for ch in text if ch.isdigit()]


def _split_hemispheres(coord_str: str) -> tuple[str, str]:
    """
    Split a coordinate string into the lat part and the lon part.
    Works for DMM/DMS (N/S ... E/W ...) and DD (lat, lon).
    Returns (lat_part, lon_part) as strings, or (full, "") if unsplit.
    """
    # DMM/DMS: split on E or W (the lon hemisphere letter)
    m = re.search(r'[EeWw]', coord_str)
    if m:
        return coord_str[:m.start()], coord_str[m.start():]
    # DD: split on comma or space
    parts = re.split(r'[,\s]+', coord_str.strip(), maxsplit=1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return coord_str, ""


class ChecksumDialog(QDialog):
    """Digit checksum calculator for geocaching coordinates."""

    def __init__(self, lat: float | None = None, lon: float | None = None, parent=None):
        super().__init__(parent)
        clamp_dialog_height_to_screen(self, parent)
        self.setWindowTitle(tr("chk_title"))
        self.setMinimumWidth(460)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._setup_ui()
        if lat is not None and lon is not None:
            self._input.setText(format_coords(lat, lon, CoordFormat.DMM))

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        mono = QFont()
        mono.setFamily("monospace")
        mono.setPointSize(10)

        # ── Input ─────────────────────────────────────────────────────────────
        in_group = QGroupBox(tr("chk_input_group"))
        in_layout = QVBoxLayout(in_group)

        hint = QLabel(tr("chk_input_hint"))
        hint.setStyleSheet(hint_style())
        hint.setWordWrap(True)
        in_layout.addWidget(hint)

        self._input = QLineEdit()
        self._input.setPlaceholderText(tr("coord_conv_placeholder"))
        self._input.setFont(mono)
        self._input.textChanged.connect(self._calculate)
        in_layout.addWidget(self._input)

        layout.addWidget(in_group)

        # ── Resultater ────────────────────────────────────────────────────────
        res_group = QGroupBox(tr("dist_result_group"))
        res_form = QFormLayout(res_group)
        res_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        big_font = QFont()
        big_font.setPointSize(14)
        big_font.setBold(True)

        self._total_lbl = QLabel("—")
        self._total_lbl.setFont(big_font)
        self._total_lbl.setStyleSheet("color: #1565c0;")
        res_form.addRow(tr("chk_total_label"), self._total_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        res_form.addRow(sep)

        self._ns_lbl = QLabel("—")
        self._ns_lbl.setFont(mono)
        res_form.addRow(tr("chk_ns_label"), self._ns_lbl)

        self._ew_lbl = QLabel("—")
        self._ew_lbl.setFont(mono)
        res_form.addRow(tr("chk_ew_label"), self._ew_lbl)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        res_form.addRow(sep2)

        self._digits_lbl = QLabel("—")
        self._digits_lbl.setFont(mono)
        self._digits_lbl.setWordWrap(True)
        res_form.addRow(tr("chk_digits_label"), self._digits_lbl)

        layout.addWidget(res_group)

        # ── Luk ───────────────────────────────────────────────────────────────
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _calculate(self, text: str) -> None:
        text = text.strip()
        if not text:
            self._clear()
            return

        all_digits = _extract_digits(text)
        if not all_digits:
            self._clear()
            return

        ns_part, ew_part = _split_hemispheres(text)
        ns_digits = _extract_digits(ns_part)
        ew_digits = _extract_digits(ew_part)

        total = sum(all_digits)
        ns_sum = sum(ns_digits)
        ew_sum = sum(ew_digits)

        self._total_lbl.setText(str(total))

        if ew_part:
            ns_str = " + ".join(str(d) for d in ns_digits)
            self._ns_lbl.setText(f"{ns_str} = {ns_sum}")
            ew_str = " + ".join(str(d) for d in ew_digits)
            self._ew_lbl.setText(f"{ew_str} = {ew_sum}")
        else:
            self._ns_lbl.setText("—")
            self._ew_lbl.setText("—")

        digit_str = "  ".join(str(d) for d in all_digits)
        self._digits_lbl.setText(digit_str)

    def _clear(self) -> None:
        self._total_lbl.setText("—")
        self._ns_lbl.setText("—")
        self._ew_lbl.setText("—")
        self._digits_lbl.setText("—")

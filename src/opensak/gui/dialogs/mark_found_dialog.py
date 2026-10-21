"""
src/opensak/gui/dialogs/mark_found_dialog.py

Dialog til at markere en cache som fundet (issue #649).

Erstatter den tidligere stille _toggle_found()-flip (kun cache.found = True,
ingen dato, intet log) med en rigtig dialog: brugeren vælger en fundet-dato
(default: i dag), og caller (cache_table.py) opretter et matchende Log-objekt
("Found it"/"Attended" afhængigt af cache-type) samt sætter cache.found_date —
så et manuelt markeret fund rundtripper korrekt gennem GPX-eksport/import,
i stedet for at forsvinde (ingen groundspeak:log ved eksport, GSAK genkender
derfor ikke fundet ved reimport).
"""

from __future__ import annotations
from datetime import date
from typing import Optional

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLabel, QDateEdit, QDialogButtonBox,
)
from PySide6.QtGui import QFont

from opensak.gui.date_format import apply_date_format
from opensak.lang import tr
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.gui.theme import hint_style
from opensak.utils.types import GcCode


class MarkFoundDialog(QDialog):
    """Dialog til at vælge fundet-dato ved manuel "Mark as Found"."""

    def __init__(
        self, gc_code: GcCode, cache_name: str,
        current_date: Optional[date] = None, parent=None,
    ):
        super().__init__(parent)
        clamp_dialog_height_to_screen(self, parent)
        self._gc_code = gc_code
        self._date: Optional[date] = None
        self._initial_date = current_date
        self.setWindowTitle(tr("mark_found_dialog_title"))
        self.setMinimumWidth(360)
        self._setup_ui(cache_name)

    def _setup_ui(self, cache_name: str) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel(tr("mark_found_dialog_heading", gc_code=self._gc_code, name=cache_name))
        title.setWordWrap(True)
        font = QFont()
        font.setBold(True)
        font.setPointSize(11)
        title.setFont(font)
        layout.addWidget(title)

        hint = QLabel(tr("mark_found_dialog_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(hint_style())
        layout.addWidget(hint)

        form = QFormLayout()
        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        apply_date_format(self._date_edit)  # issue #1043
        initial = (
            QDate(self._initial_date.year, self._initial_date.month, self._initial_date.day)
            if self._initial_date is not None else QDate.currentDate()
        )
        self._date_edit.setDate(initial)
        self._date_edit.setMaximumDate(QDate.currentDate())
        form.addRow(tr("mark_found_dialog_date_label"), self._date_edit)
        layout.addLayout(form)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_accept(self) -> None:
        qd = self._date_edit.date()
        self._date = date(qd.year(), qd.month(), qd.day())
        self.accept()

    def get_date(self) -> Optional[date]:
        """Returnér den valgte fundet-dato (None hvis dialogen blev afvist)."""
        return self._date

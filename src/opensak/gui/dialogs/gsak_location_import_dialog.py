"""
src/opensak/gui/dialogs/gsak_location_import_dialog.py — review GSAK's user
locations before they are added to OpenSAK's location list (issue #1001).

Opened by the GSAK import (``gsak_import_dialog.py``) once ``gsak.db3`` is
available. Every line of GSAK's list is shown with its validation result:
valid locations are ticked, invalid ones are listed with the reason but
can't be ticked. Names that already exist in OpenSAK are flagged; whether
those are overwritten or skipped is asked once when importing.

Parsing and saving live in ``opensak.importer.gsak_location_importer``.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)

from opensak.coords import format_coords
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.gui.settings import get_settings
from opensak.importer.gsak_location_importer import (
    ERROR_BAD_COORD, ERROR_DUPLICATE, ERROR_NO_COORD, ERROR_NO_NAME, ERROR_RESERVED,
    GsakLocation, LocationImportResult, apply_locations, existing_location_names,
)
from opensak.lang import tr

# Table columns
COL_LINE, COL_NAME, COL_COORD, COL_STATUS = range(4)

_ERROR_KEYS = {
    ERROR_NO_NAME:   "gsak_location_import_error_no_name",
    ERROR_NO_COORD:  "gsak_location_import_error_no_coord",
    ERROR_BAD_COORD: "gsak_location_import_error_bad_coord",
    ERROR_RESERVED:  "gsak_location_import_error_reserved",
    ERROR_DUPLICATE: "gsak_location_import_error_duplicate",
}


def location_error_text(loc: GsakLocation) -> str:
    """Why *loc* can't be imported, in the user's language."""
    return tr(_ERROR_KEYS[loc.error]) if loc.error else ""


class GsakLocationImportDialog(QDialog):
    """Checkable preview of GSAK's user locations; Import saves the ticked ones."""

    def __init__(self, locations: list[GsakLocation], parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("gsak_location_import_dialog_title"))
        self.setMinimumWidth(640)
        self.setMinimumHeight(420)
        clamp_dialog_height_to_screen(self, parent)
        self._locations = locations
        self._existing = existing_location_names()
        self.result_data: Optional[LocationImportResult] = None
        self._setup_ui()
        self._fill_table()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        header = QHBoxLayout()
        invalid = sum(1 for loc in self._locations if not loc.valid)
        self._found_label = QLabel(tr(
            "gsak_location_import_found",
            count=len(self._locations), invalid=invalid,
        ))
        header.addWidget(self._found_label, stretch=1)
        self._all_btn = QPushButton(tr("gsak_filter_import_select_all"))
        self._all_btn.clicked.connect(lambda: self._check_all(True))
        header.addWidget(self._all_btn)
        self._none_btn = QPushButton(tr("gsak_filter_import_select_none"))
        self._none_btn.clicked.connect(lambda: self._check_all(False))
        header.addWidget(self._none_btn)
        layout.addLayout(header)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels([
            tr("gsak_location_import_col_line"), tr("col_name"),
            tr("detail_coords"), tr("gsak_import_col_status"),
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(COL_LINE, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(COL_COORD, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        self._table.itemChanged.connect(lambda _item: self._update_import_button())
        layout.addWidget(self._table, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._import_btn = QPushButton(tr("import_start"))
        self._import_btn.clicked.connect(self._import)
        btn_row.addWidget(self._import_btn)
        self._cancel_btn = QPushButton(tr("cancel"))
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

    def _fill_table(self) -> None:
        fmt = get_settings().coord_format
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._locations))
        for row, loc in enumerate(self._locations):
            line_item = QTableWidgetItem(str(loc.line_no))
            line_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            line_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._table.setItem(row, COL_LINE, line_item)

            name_item = QTableWidgetItem(loc.name)
            name_item.setData(Qt.ItemDataRole.UserRole, loc)
            if loc.valid:
                name_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                )
                name_item.setCheckState(Qt.CheckState.Checked)
            else:
                # Not importable: shown for the record, never ticked.
                name_item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._table.setItem(row, COL_NAME, name_item)

            if loc.valid and loc.lat is not None and loc.lon is not None:
                coord = format_coords(loc.lat, loc.lon, fmt)
            else:
                coord = loc.coord_text
            coord_item = QTableWidgetItem(coord)
            coord_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            coord_item.setToolTip(loc.text)
            self._table.setItem(row, COL_COORD, coord_item)

            if not loc.valid:
                status = "✗  " + location_error_text(loc)
            elif loc.name in self._existing:
                status = tr("file_locations_col_exists")
            else:
                status = tr("gsak_location_import_status_new")
            status_item = QTableWidgetItem(status)
            status_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if not loc.valid:
                status_item.setForeground(QColor("#c62828"))   # as settings_hp_coord_error
            self._table.setItem(row, COL_STATUS, status_item)
        self._table.blockSignals(False)
        self._update_import_button()

    # ── Selection ────────────────────────────────────────────────────────────

    def _name_item(self, row: int) -> QTableWidgetItem:
        item = self._table.item(row, COL_NAME)
        assert item is not None   # every row gets one in _fill_table()
        return item

    def _check_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._table.blockSignals(True)
        for row in range(self._table.rowCount()):
            item = self._name_item(row)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(state)
        self._table.blockSignals(False)
        self._update_import_button()

    def checked_locations(self) -> list[GsakLocation]:
        return [
            self._name_item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self._table.rowCount())
            if self._name_item(row).flags() & Qt.ItemFlag.ItemIsUserCheckable
            and self._name_item(row).checkState() == Qt.CheckState.Checked
        ]

    def _update_import_button(self) -> None:
        self._import_btn.setEnabled(bool(self.checked_locations()))

    # ── Import ───────────────────────────────────────────────────────────────

    def _confirm_existing(self, chosen: list[GsakLocation]) -> Optional[bool]:
        """Ask once what to do with names that already exist.

        Returns True to overwrite, False to skip them, None when cancelled.
        """
        existing = [loc.name for loc in chosen if loc.name in self._existing]
        if not existing:
            return False
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(tr("gsak_location_import_existing_title"))
        box.setText(tr(
            "gsak_location_import_existing_body",
            names="\n".join(f"  • {name}" for name in existing),
        ))
        overwrite_btn = box.addButton(tr("gsak_import_existing_overwrite"),
                                      QMessageBox.ButtonRole.DestructiveRole)
        skip_btn = box.addButton(tr("gsak_import_existing_skip"),
                                 QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(skip_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is overwrite_btn:
            return True
        if clicked is skip_btn:
            return False
        return None

    def _import(self) -> None:
        chosen = self.checked_locations()
        if not chosen:
            return
        overwrite = self._confirm_existing(chosen)
        if overwrite is None:
            return
        self.result_data = apply_locations(chosen, overwrite=overwrite)
        self.accept()

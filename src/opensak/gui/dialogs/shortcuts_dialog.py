from __future__ import annotations
from typing import TYPE_CHECKING
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QDialogButtonBox, QPushButton,
    QAbstractItemView, QKeySequenceEdit,
)
from PySide6.QtGui import QKeySequence
from PySide6.QtCore import Qt
from opensak.lang import tr

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget
    from PySide6.QtGui import QAction

# Default shortcut strings, keyed by settings_key.
_DEFAULTS: dict[str, str] = {
    "manage_databases":  "Ctrl+D",
    "import":            "Ctrl+I",
    "quit":              "Ctrl+Q",
    "add_cache":         "Ctrl+N",
    "edit_cache":        "Ctrl+E",
    "delete_cache":      "Delete",
    "refresh":           "F5",
    "filter":            "Ctrl+F",
    "settings":          "Ctrl+,",
    "gps_export":        "Ctrl+G",
    "trip_planner":      "Ctrl+T",
    "coord_converter":   "Ctrl+K",
    "projection":        "Ctrl+P",
}


class ShortcutsDialog(QDialog):
    def __init__(
        self,
        registry: list[tuple[str, str, list[QAction]]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("shortcuts_dialog_title"))
        self.setMinimumWidth(400)
        self._registry = registry
        self._editors: list[QKeySequenceEdit] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self._table = QTableWidget(len(self._registry), 2)
        self._table.setHorizontalHeaderLabels([
            tr("shortcuts_col_action"),
            tr("shortcuts_col_shortcut"),
        ])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        for row, (key, label_key, actions) in enumerate(self._registry):
            name_item = QTableWidgetItem(tr(label_key))
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(row, 0, name_item)

            current = actions[0].shortcut().toString(
                QKeySequence.SequenceFormat.NativeText
            )
            editor = QKeySequenceEdit(QKeySequence(current))
            self._editors.append(editor)
            self._table.setCellWidget(row, 1, editor)

        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton(tr("shortcuts_reset_all"))
        reset_btn.clicked.connect(self._reset_all)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _reset_all(self) -> None:
        for row, (key, _label, _actions) in enumerate(self._registry):
            default = _DEFAULTS.get(key, "")
            editor = self._editors[row]
            editor.setKeySequence(QKeySequence(default))

    def get_shortcuts(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for row, (key, _label, _actions) in enumerate(self._registry):
            seq = self._editors[row].keySequence()
            result[key] = seq.toString(QKeySequence.SequenceFormat.PortableText)
        return result

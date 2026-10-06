"""
src/opensak/gui/dialogs/found_databases_dialog.py — pick which databases found
in the database folder to add to the list (issue #985).

Shared by the Welcome Wizard and Manage Databases → Scan database folder.
Every database is ticked by default; nothing is added unless the user clicks
Add.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget,
)

from opensak.db.discover import FoundDatabase
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.lang import tr


class FoundDatabasesDialog(QDialog):
    """A ticked list of found databases, with Add and Not now."""

    def __init__(
        self,
        found: Sequence[FoundDatabase],
        folder: Path,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("db_found_title"))
        self.setMinimumWidth(460)
        clamp_dialog_height_to_screen(self, parent)

        layout = QVBoxLayout(self)
        intro = QLabel(tr("db_found_intro", count=len(found), folder=str(folder)))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._list = QListWidget()
        for db in found:
            item = QListWidgetItem(tr(
                "db_found_item",
                name=db.name,
                size=f"{db.size_bytes / (1024 * 1024):.1f}",
                date=db.modified.strftime("%Y-%m-%d"),
            ))
            item.setData(Qt.ItemDataRole.UserRole, db.path)
            item.setToolTip(str(db.path))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self._list.addItem(item)
        layout.addWidget(self._list)

        buttons = QDialogButtonBox()
        self._add_btn: QPushButton = buttons.addButton(
            tr("db_found_add"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.addButton(tr("exit_backup_not_now"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._list.itemChanged.connect(self._update_add_button)
        layout.addWidget(buttons)

    def selected_paths(self) -> list[Path]:
        """Paths of the ticked databases, in list order."""
        paths = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                paths.append(Path(item.data(Qt.ItemDataRole.UserRole)))
        return paths

    def _update_add_button(self, *_args) -> None:
        self._add_btn.setEnabled(bool(self.selected_paths()))


def ask_which_to_add(
    found: Sequence[FoundDatabase], folder: Path, parent: Optional[QWidget] = None,
) -> list[Path]:
    """Show the dialog; the paths to add, or [] for Not now / nothing ticked."""
    dlg = FoundDatabasesDialog(found, folder, parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return []
    return dlg.selected_paths()

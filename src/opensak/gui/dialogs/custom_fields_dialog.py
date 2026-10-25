"""
src/opensak/gui/dialogs/custom_fields_dialog.py — define the custom fields
(#1028, #126) of the active database.

Tools → Custom fields… lists the database's fields with their type, default,
scope and where the definition came from, and adds, changes and deletes
them (db/custom_fields.py does the work):

* **Local** fields belong to this database; **global** ones are defined for
  every database (their master copy is in the app settings).
* Renaming keeps the values. Changing the type converts the stored values
  after the user agreed; values that don't fit are lost.
* The display expression is SQL the cache list shows instead of the
  value (filters and sorting keep the value); it is checked when saved
  (custom_fields.check_expression()).
* Deleting keeps the column and its values ("deleted", shown greyed) until
  *Remove data…* drops them for good — the same rule as everywhere in
  custom fields: nothing is dropped without asking.

Only with the companion-db feature flag (the menu entry is hidden otherwise).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from opensak.db import custom_fields
from opensak.db.custom_fields import Field
from opensak.gui.custom_field_format import type_label
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.gui.theme import hint_style
from opensak.lang import tr

logger = logging.getLogger(__name__)

# Table columns
_COL_NAME, _COL_TYPE, _COL_DEFAULT, _COL_SCOPE, _COL_ORIGIN, _COL_EXPRESSION = range(6)

# Errors a definition change can end in; shown to the user as they are.
_ERRORS = (custom_fields.CustomFieldError, custom_fields.CompanionError, sqlite3.Error, OSError)


class FieldEditDialog(QDialog):
    """Name, type, default and (for a new field) scope of one field."""

    def __init__(self, parent: Optional[QWidget] = None, field: Optional[Field] = None):
        super().__init__(parent)
        self._field = field
        self.setWindowTitle(tr("custom_field_edit_title") if field else tr("custom_field_add_title"))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(field.name if field else "")
        self.name.setMaxLength(custom_fields.MAX_NAME_LENGTH)
        form.addRow(tr("custom_field_name"), self.name)
        self.type = QComboBox()
        for type_ in custom_fields.TYPES:
            self.type.addItem(type_label(type_), type_)
        if field:
            self.type.setCurrentIndex(max(self.type.findData(field.type), 0))
        form.addRow(tr("custom_field_type"), self.type)
        self.default = QLineEdit((field.default or "") if field else "")
        self.default.setPlaceholderText(tr("custom_field_default_placeholder"))
        form.addRow(tr("custom_field_default"), self.default)
        self.scope = QComboBox()
        self.scope.addItem(tr("custom_scope_local"), custom_fields.SCOPE_LOCAL)
        self.scope.addItem(tr("custom_scope_global"), custom_fields.SCOPE_GLOBAL)
        if field:
            self.scope.setCurrentIndex(max(self.scope.findData(field.scope), 0))
            self.scope.setEnabled(False)   # can't move between local and global
            self.scope.setToolTip(tr("custom_field_scope_fixed"))
        form.addRow(tr("custom_field_scope"), self.scope)
        self.expression = QLineEdit((field.expression or "") if field else "")
        self.expression.setPlaceholderText(tr("custom_field_expression_placeholder"))
        self.expression.setMinimumWidth(360)
        form.addRow(tr("custom_field_expression"), self.expression)
        layout.addLayout(form)
        for key in ("custom_field_name_hint", "custom_field_expression_hint"):
            hint = QLabel(tr(key))
            hint.setWordWrap(True)
            hint.setStyleSheet(hint_style())
            layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str, Optional[str], str, Optional[str]]:
        """(name, type, default as stored, scope, display expression or None)."""
        type_ = self.type.currentData()
        text = self.default.text().strip()
        default: Optional[str] = text or None
        if default is not None and type_ == custom_fields.TYPE_BOOLEAN:
            default = "1" if custom_fields.convert(type_, text)[0] else "0"
        expression = self.expression.text().strip() or None
        return self.name.text().strip(), type_, default, self.scope.currentData(), expression

    def _accept(self) -> None:
        name, type_, default, _scope, expression = self.values()
        try:
            if self._field is None or name != self._field.name:
                custom_fields.check_name(name)
            probe = Field(field_id="", name=name, type=type_)
            custom_fields.parse_value(probe, self.default.text().strip())
        except custom_fields.CustomFieldError as exc:
            QMessageBox.warning(self, tr("warning"), str(exc))
            return
        if expression is not None:
            # As it will be shown: an imported GSAK field keeps its origin,
            # so its expression is still read as GSAK SQL.
            probe = Field(
                field_id=self._field.field_id if self._field else "", name=name, type=type_,
                default=default, expression=expression,
                origin=self._field.origin if self._field else custom_fields.ORIGIN_OPENSAK,
            )
            try:
                error = custom_fields.check_active_expression(probe)
            except _ERRORS as exc:
                error = str(exc)
            if error is not None:
                QMessageBox.warning(self, tr("warning"),
                                    tr("custom_expression_invalid", error=error))
                self.expression.setFocus()
                return
        self.accept()


class CustomFieldsDialog(QDialog):
    """The custom fields of the active database (see the module docstring)."""

    # (old name, new name) of every field renamed while the dialog was open
    fields_changed = Signal(list)

    def __init__(self, db_path: Path, db_name: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._db_path = Path(db_path)
        self._changed = False
        self._renames: list[tuple[str, str]] = []
        self.setWindowTitle(tr("custom_fields_title", name=db_name))
        self.resize(760, 420)
        clamp_dialog_height_to_screen(self, parent)

        layout = QVBoxLayout(self)
        intro = QLabel(tr("custom_fields_intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            tr("custom_field_name").rstrip(":"), tr("custom_field_type").rstrip(":"),
            tr("custom_field_default").rstrip(":"), tr("custom_field_scope").rstrip(":"),
            tr("custom_col_origin"), tr("custom_col_expression"),
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_EXPRESSION, QHeaderView.ResizeMode.Stretch)
        self._table.itemSelectionChanged.connect(self._update_buttons)
        self._table.doubleClicked.connect(lambda _index: self._edit())
        layout.addWidget(self._table)

        buttons = QHBoxLayout()
        self._add_btn = QPushButton(tr("custom_btn_add"))
        self._add_btn.clicked.connect(self._add)
        self._edit_btn = QPushButton(tr("custom_btn_edit"))
        self._edit_btn.clicked.connect(self._edit)
        self._delete_btn = QPushButton(tr("custom_btn_delete"))
        self._delete_btn.clicked.connect(self._delete)
        self._drop_btn = QPushButton(tr("custom_btn_drop"))
        self._drop_btn.setToolTip(tr("custom_tip_drop"))
        self._drop_btn.clicked.connect(self._drop)
        for button in (self._add_btn, self._edit_btn, self._delete_btn, self._drop_btn):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        buttons.addStretch()
        close = QPushButton(tr("close"))
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        self._fields: list[Field] = []
        self._reload()

    # ── Table ────────────────────────────────────────────────────────────────

    def _reload(self, select: Optional[str] = None) -> None:
        try:
            effective = custom_fields.fields(self._db_path)
            stored = custom_fields.stored_fields(self._db_path, include_orphaned=True)
        except _ERRORS as exc:
            QMessageBox.warning(self, tr("warning"), str(exc))
            effective, stored = [], []
        orphaned = [f for f in stored if f.orphaned]
        self._fields = [*effective, *orphaned]
        self._table.setRowCount(len(self._fields))
        for row, f in enumerate(self._fields):
            scope = tr("custom_scope_global") if f.scope == custom_fields.SCOPE_GLOBAL \
                else tr("custom_scope_local")
            origin = "GSAK" if f.origin == custom_fields.ORIGIN_GSAK else "OpenSAK"
            name = f.name if not f.orphaned else tr("custom_field_deleted", name=f.name)
            cells = (name, type_label(f.type), f.default or "", scope, origin, f.expression or "")
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if f.orphaned:
                    item.setForeground(Qt.GlobalColor.gray)
                if col == _COL_EXPRESSION and f.expression:
                    names = [x.name for x in effective]
                    active = custom_fields.display_expression(f, names) is not None
                    item.setToolTip(tr("custom_tip_expression_active") if active
                                    else tr("custom_tip_expression_inactive"))
                self._table.setItem(row, col, item)
            if select is not None and f.name.lower() == select.lower() and not f.orphaned:
                self._table.selectRow(row)
        self._update_buttons()

    def _current(self) -> Optional[Field]:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        return self._fields[row] if 0 <= row < len(self._fields) else None

    def _update_buttons(self) -> None:
        current = self._current()
        self._edit_btn.setEnabled(current is not None and not current.orphaned)
        self._delete_btn.setEnabled(current is not None and not current.orphaned)
        self._drop_btn.setEnabled(current is not None and current.orphaned)

    # ── Actions ──────────────────────────────────────────────────────────────

    def _add(self) -> None:
        dlg = FieldEditDialog(self)
        if not dlg.exec():
            return
        name, type_, default, scope, expression = dlg.values()
        try:
            custom_fields.define_field(self._db_path, name, type_, default=default, scope=scope,
                                       expression=expression)
        except _ERRORS as exc:
            QMessageBox.warning(self, tr("warning"), str(exc))
            return
        self._changed = True
        self._reload(select=name)

    def _edit(self) -> None:
        current = self._current()
        if current is None or current.orphaned:
            return
        dlg = FieldEditDialog(self, current)
        if not dlg.exec():
            return
        name, type_, default, _scope, expression = dlg.values()
        try:
            if type_ != current.type:
                reply = QMessageBox.question(
                    self, tr("custom_type_change_title"),
                    tr("custom_type_change_msg", name=current.name,
                       old=type_label(current.type), new=type_label(type_)),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                custom_fields.change_type(self._db_path, current.field_id, type_)
            if name != current.name:
                custom_fields.rename_field(self._db_path, current.field_id, name)
                self._renames.append((current.name, name))
            if default != current.default:
                custom_fields.update_definition(self._db_path, current.field_id, default=default)
            if expression != (current.expression or None):
                custom_fields.update_definition(self._db_path, current.field_id,
                                                expression=expression)
        except _ERRORS as exc:
            QMessageBox.warning(self, tr("warning"), str(exc))
        self._changed = True
        self._reload(select=name)

    def _delete(self) -> None:
        current = self._current()
        if current is None:
            return
        key = "custom_delete_global_msg" if current.scope == custom_fields.SCOPE_GLOBAL \
            else "custom_delete_msg"
        reply = QMessageBox.question(
            self, tr("custom_delete_title"), tr(key, name=current.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            custom_fields.delete_field(self._db_path, current.field_id)
        except _ERRORS as exc:
            QMessageBox.warning(self, tr("warning"), str(exc))
        self._changed = True
        self._reload()

    def _drop(self) -> None:
        current = self._current()
        if current is None or not current.orphaned:
            return
        reply = QMessageBox.question(
            self, tr("custom_drop_title"), tr("custom_drop_msg", name=current.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            custom_fields.drop_field(self._db_path, current.field_id)
        except _ERRORS as exc:
            QMessageBox.warning(self, tr("warning"), str(exc))
        self._changed = True
        self._reload()

    def done(self, result: int) -> None:
        if self._changed:
            self.fields_changed.emit(list(self._renames))
        super().done(result)

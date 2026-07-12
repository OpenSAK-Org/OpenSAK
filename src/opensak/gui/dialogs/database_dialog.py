"""
src/opensak/gui/dialogs/database_dialog.py — Database manager dialog.
"""

from __future__ import annotations
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem,
    QLineEdit, QFileDialog, QMessageBox,
    QGroupBox, QFormLayout, QDialogButtonBox,
    QSizePolicy
)

from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.db.manager import DatabaseManager, DatabaseInfo, get_db_manager
from opensak.lang import tr


class NewDatabaseDialog(QDialog):
    """Dialog til at oprette en ny database."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("db_new_title"))
        self.setMinimumWidth(380)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText(tr("db_name_placeholder"))
        self._name_edit.textChanged.connect(self._update_path_preview)
        form.addRow(tr("db_name_label"), self._name_edit)

        layout.addLayout(form)
        info_text = tr("db_new_info").replace("\n", "<br>")
        layout.addWidget(QLabel(f"<small style='color:gray'>{info_text}</small>"))

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText(tr("db_default_path"))
        self._path_edit.setReadOnly(True)
        path_row.addWidget(self._path_edit)
        browse_btn = QPushButton(tr("gps_browse"))
        browse_btn.setMaximumWidth(80)
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._custom_path: Path | None = None
        self._update_path_preview()

    def _browse(self) -> None:
        # Issue #562: brugte tidligere get_app_data_dir() (installations-
        # mappen) som startpunkt for "Browse" i stedet for den mappe
        # brugeren faktisk har konfigureret til databaser (Settings →
        # Advanced / velkomst-wizarden), så dialogen åbnede det forkerte
        # sted når de to mapper er forskellige.
        from opensak.settings_store import get_db_dir
        folder = QFileDialog.getExistingDirectory(
            self, tr("db_browse_title"),
            str(get_db_dir()),
            QFileDialog.Option.ShowDirsOnly,
        )
        if folder:
            self._custom_path = Path(folder)
            self._update_path_preview()

    def _update_path_preview(self) -> None:
        """Vis filsti i sti-feltet: kun mappe som default, fuld sti når navn er tastet."""
        # Issue #562: se _browse() ovenfor — samme rettelse for selve
        # preview-teksten, så den viste standard-sti matcher det sted
        # databasen faktisk oprettes (get_db_dir(), jf. manager.py's
        # _default_db_path()), ikke installationsmappen.
        from opensak.settings_store import get_db_dir
        name = self._name_edit.text().strip()
        folder = self._custom_path or get_db_dir()
        if name:
            self._path_edit.setText(str(folder / f"{name}.db"))
        else:
            self._path_edit.setText(str(folder))

    def _validate(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, tr("warning"), tr("db_name_required"))
            return
        self.accept()

    @property
    def name(self) -> str:
        return self._name_edit.text().strip()

    @property
    def custom_path(self) -> Path | None:
        """Returner fuld filsti (mappe + navn.db), eller None for default placering."""
        if self._custom_path is None:
            return None
        name = self._name_edit.text().strip()
        if not name:
            return self._custom_path  # _validate() fanger dette
        safe_name = "".join(
            c if c.isalnum() or c in "-_ " else "_" for c in name
        ).strip()
        return self._custom_path / f"{safe_name}.db"


class DatabaseManagerDialog(QDialog):
    """
    Fuld database manager dialog.
    Viser alle kendte databaser og lader brugeren skifte, oprette,
    omdøbe, kopiere og slette databaser.
    """

    database_switched = Signal(object)   # emits DatabaseInfo
    database_renamed = Signal(object)    # emits DatabaseInfo — issue #539

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("db_dialog_title"))
        self.setMinimumSize(560, 400)
        self._manager = get_db_manager()
        self._setup_ui()
        self._refresh_list()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)

        # ── Venstre: database liste ───────────────────────────────────────────
        left = QVBoxLayout()
        left.addWidget(QLabel(f"<b>{tr('db_list_label')}</b>"))

        self._list = QListWidget()
        self._list.setMinimumWidth(220)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        self._list.itemDoubleClicked.connect(self._switch_to_selected)
        left.addWidget(self._list)
        layout.addLayout(left)

        # ── Højre: detaljer + knapper ─────────────────────────────────────────
        right = QVBoxLayout()

        # Detaljer
        info_group = QGroupBox(tr("db_details_group"))
        info_form = QFormLayout(info_group)
        self._info_name  = QLabel("—")
        self._info_path  = QLabel("—")
        self._info_path.setWordWrap(True)
        self._info_size  = QLabel("—")
        self._info_mod   = QLabel("—")
        info_form.addRow(tr("db_name_label"),     self._info_name)
        info_form.addRow(tr("db_path_label"),      self._info_path)
        info_form.addRow(tr("db_size_label"), self._info_size)
        info_form.addRow(tr("db_modified_label"),   self._info_mod)
        right.addWidget(info_group)

        # Knapper
        btn_layout = QVBoxLayout()

        self._btn_switch = QPushButton(tr("db_switch_btn"))
        self._btn_switch.setEnabled(False)
        self._btn_switch.clicked.connect(self._switch_to_selected)
        btn_layout.addWidget(self._btn_switch)

        btn_layout.addSpacing(8)

        self._btn_new = QPushButton(tr("db_new_btn"))
        self._btn_new.clicked.connect(self._new_database)
        btn_layout.addWidget(self._btn_new)

        self._btn_open = QPushButton(tr("db_open_btn"))
        self._btn_open.clicked.connect(self._open_database)
        btn_layout.addWidget(self._btn_open)

        self._btn_copy = QPushButton(tr("db_copy_btn"))
        self._btn_copy.setEnabled(False)
        self._btn_copy.clicked.connect(self._copy_database)
        btn_layout.addWidget(self._btn_copy)

        self._btn_rename = QPushButton(tr("db_rename_btn"))
        self._btn_rename.setEnabled(False)
        self._btn_rename.clicked.connect(self._rename_database)
        btn_layout.addWidget(self._btn_rename)

        btn_layout.addSpacing(8)

        self._btn_remove = QPushButton(tr("db_remove_btn"))
        self._btn_remove.setEnabled(False)
        self._btn_remove.clicked.connect(self._remove_from_list)
        btn_layout.addWidget(self._btn_remove)

        self._btn_delete = QPushButton(tr("db_delete_btn"))
        self._btn_delete.setEnabled(False)
        self._btn_delete.setStyleSheet("color: #c62828;")
        self._btn_delete.clicked.connect(self._delete_database)
        btn_layout.addWidget(self._btn_delete)

        btn_layout.addStretch()

        close_btn = QPushButton(tr("close"))
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)

        right.addLayout(btn_layout)
        layout.addLayout(right)

    def _refresh_list(self, select_db: "DatabaseInfo | None" = None) -> None:
        # Husk hvilken der var valgt (eller brug select_db hvis angivet)
        current = select_db or self._selected_db()
        self._list.clear()
        active = self._manager.active
        select_item = None
        for db in self._manager.databases:
            item = QListWidgetItem(db.name)
            item.setData(Qt.ItemDataRole.UserRole, db)
            if db == active:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setText(f"{db.name}  ✓")
            if not db.exists:
                item.setForeground(Qt.GlobalColor.gray)
                item.setToolTip(tr("db_file_not_found"))
            self._list.addItem(item)
            if current and db.path == current.path:
                select_item = item
            elif active and db.path == active.path and select_item is None:
                # Fallback: pre-mark active DB in case current is gone
                select_item = item
        if select_item:
            self._list.setCurrentItem(select_item)

    def _selected_db(self) -> DatabaseInfo | None:
        item = self._list.currentItem()
        if item:
            return item.data(Qt.ItemDataRole.UserRole)
        return None

    def _on_selection_changed(self) -> None:
        db = self._selected_db()
        is_active = db == self._manager.active if db else False

        if db:
            self._info_name.setText(db.name)
            self._info_path.setText(str(db.path))
            self._info_size.setText(f"{db.size_mb:.2f} MB" if db.exists else tr("db_not_found"))
            self._info_mod.setText(
                db.modified.strftime("%d.%m.%Y %H:%M") if db.modified else "—"
            )
        else:
            for lbl in (self._info_name, self._info_path,
                        self._info_size, self._info_mod):
                lbl.setText("—")

        self._btn_switch.setEnabled(db is not None and not is_active and db.exists)
        self._btn_copy.setEnabled(db is not None and db.exists)
        self._btn_rename.setEnabled(db is not None)
        self._btn_remove.setEnabled(db is not None and not is_active)
        # Delete requires: something selected, not active, and file exists on disk
        self._btn_delete.setEnabled(db is not None and not is_active and db.exists)

    def _switch_to_selected(self, *_) -> None:
        db = self._selected_db()
        if not db or db == self._manager.active:
            return
        self._manager.switch_to(db)
        self._refresh_list(select_db=db)
        self.database_switched.emit(db)
        QMessageBox.information(
            self, tr("db_switched_title"),
            tr("db_switched_msg", name=db.name)
        )

    def _new_database(self) -> None:
        dlg = NewDatabaseDialog(self)
        if dlg.exec():
            try:
                db = self._manager.new_database(dlg.name, dlg.custom_path)
                # Sæt centerpoint til Home Location eller sidst aktive koordinat
                self._manager.switch_to(db)
                from opensak.gui.settings import get_settings
                get_settings().apply_default_center_for_new_db()
                self._refresh_list(select_db=db)
                self.database_switched.emit(db)
                QMessageBox.information(
                    self, tr("db_created_title"),
                    tr("db_created_msg", name=db.name)
                )
            except ValueError as e:
                QMessageBox.warning(self, tr("warning"), str(e))

    def _open_database(self) -> None:
        from opensak.config import get_app_data_dir
        path, _ = QFileDialog.getOpenFileName(
            self, tr("db_open_browse_title"),
            str(get_app_data_dir()),
            tr("db_file_filter")
        )
        if path:
            try:
                db = self._manager.open_database(Path(path))
                self._refresh_list()
                QMessageBox.information(
                    self, tr("db_opened_title"),
                    tr("db_opened_msg", name=db.name)
                )
            except Exception as e:
                QMessageBox.warning(self, tr("warning"), str(e))

    def _copy_database(self) -> None:
        db = self._selected_db()
        if not db:
            return
        name, ok = self._simple_input(
            tr("db_copy_title"),
            tr("db_copy_name_label"),
            f"{db.name} ({tr('db_copy_suffix')})"
        )
        if ok and name:
            try:
                new_db = self._manager.copy_database(db, name)
                self._refresh_list()
                QMessageBox.information(
                    self, tr("db_copied_title"),
                    tr("db_copied_msg", new_name=new_db.name, orig_name=db.name)
                )
            except Exception as e:
                QMessageBox.warning(self, tr("warning"), str(e))

    def _rename_database(self) -> None:
        db = self._selected_db()
        if not db:
            return
        name, ok = self._simple_input(tr("db_rename_title"), tr("db_rename_label"), db.name)
        if ok and name and name != db.name:
            try:
                self._manager.rename(db, name)
                self._refresh_list()
                # Issue #539: toolbar dropdown + window title show the active
                # database's name and don't otherwise get notified of a
                # rename (only of a switch) — without this they kept showing
                # the old name until the user switched databases.
                self.database_renamed.emit(db)
            except ValueError as e:
                QMessageBox.warning(self, tr("warning"), str(e))

    def _remove_from_list(self) -> None:
        db = self._selected_db()
        if not db:
            return
        reply = QMessageBox.question(
            self, tr("db_remove_title"),
            tr("db_remove_msg", name=db.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self._manager.remove_from_list(db)
                self._refresh_list()
            except ValueError as e:
                QMessageBox.warning(self, tr("warning"), str(e))

    def _delete_database(self) -> None:
        db = self._selected_db()
        if not db:
            return

        # Safety guard: active database must never be deleted
        if db == self._manager.active:
            QMessageBox.warning(
                self,
                tr("db_delete_confirm_title"),
                tr("db_delete_active_error", name=db.name),
            )
            return

        reply = QMessageBox.warning(
            self, tr("db_delete_confirm_title"),
            tr("db_delete_confirm_msg", name=db.name, path=db.path),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                empty_folder = self._manager.delete_database(db)
                self._refresh_list()
                # Tilbyd at slette mappen hvis den er tom
                if empty_folder is not None:
                    folder_reply = QMessageBox.question(
                        self,
                        tr("db_delete_folder_title"),
                        tr("db_delete_folder_msg", path=empty_folder),
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if folder_reply == QMessageBox.StandardButton.Yes:
                        try:
                            self._manager.delete_folder(empty_folder)
                        except OSError as e:
                            QMessageBox.warning(self, tr("warning"), str(e))
            except OSError as e:
                # Filer kunne ikke slettes, men db er fjernet fra listen
                self._refresh_list()
                QMessageBox.warning(self, tr("warning"), str(e))
            except ValueError as e:
                QMessageBox.warning(self, tr("warning"), str(e))

    def _simple_input(self, title: str, label: str,
                      default: str = "") -> tuple[str, bool]:
        """Simpel tekst-input dialog."""
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, title, label, text=default)
        return text.strip(), ok

"""
src/opensak/gui/dialogs/gsak_view_import_dialog.py — import saved GSAK
views as OpenSAK Column Views.

The sibling of ``gsak_filter_import_dialog.py``: a checkable list of the
views found in GSAK's settings database (``gsak.db3``), and a results log
listing the GSAK columns OpenSAK has no column for. A view is a few lines of
text, so the import runs right away instead of in a worker thread.

What each GSAK column becomes is decided by
``opensak.importer.gsak_view_importer``.
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog,
    QTextEdit, QCheckBox, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox,
)

from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.gui.settings import get_settings
from opensak.lang import tr
from opensak.gui.theme import hint_style

# How many per-view lines the results log shows before it stops listing them.
MAX_LISTED_VIEWS = 25


class GsakViewImportDialog(QDialog):
    """Dialog for importing GSAK's saved views (gsak.db3 or a .zip backup)."""

    import_completed = Signal()   # at least one Column View was written

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("gsak_view_import_dialog_title"))
        self.setMinimumWidth(560)
        self.setMinimumHeight(480)
        clamp_dialog_height_to_screen(self, parent)
        self._db3_path: Optional[Path] = None     # gsak.db3, unpacked if needed
        self._setup_ui()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel(tr("gsak_filter_import_select_file_label")))

        file_row = QHBoxLayout()
        self._file_label = QLabel("")
        self._file_label.setStyleSheet(hint_style(font_size=None))
        file_row.addWidget(self._file_label, stretch=1)
        self._browse_btn = QPushButton(tr("import_browse"))
        self._browse_btn.clicked.connect(self._browse)
        file_row.addWidget(self._browse_btn)
        layout.addLayout(file_row)

        # ── View list ────────────────────────────────────────────────────────
        list_header = QHBoxLayout()
        self._found_label = QLabel("")
        list_header.addWidget(self._found_label, stretch=1)
        self._all_btn = QPushButton(tr("gsak_filter_import_select_all"))
        self._all_btn.clicked.connect(lambda: self._check_visible(True))
        self._all_btn.setEnabled(False)
        list_header.addWidget(self._all_btn)
        self._none_btn = QPushButton(tr("gsak_filter_import_select_none"))
        self._none_btn.clicked.connect(lambda: self._check_visible(False))
        self._none_btn.setEnabled(False)
        list_header.addWidget(self._none_btn)
        layout.addLayout(list_header)

        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("gsak_view_import_search_placeholder"))
        self._search.textChanged.connect(self._apply_search)
        self._search.setEnabled(False)
        layout.addWidget(self._search)

        self._list = QListWidget()
        self._list.itemChanged.connect(lambda _item: self._update_import_button())
        layout.addWidget(self._list, stretch=1)

        self._overwrite_cb = QCheckBox(tr("gsak_view_import_overwrite"))
        self._overwrite_cb.setToolTip(tr("gsak_view_import_overwrite_tooltip"))
        layout.addWidget(self._overwrite_cb)

        # ── Import + Close row ───────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._import_btn = QPushButton(tr("import_start"))
        self._import_btn.setEnabled(False)
        self._import_btn.clicked.connect(self._start_import)
        btn_row.addWidget(self._import_btn)
        self._close_btn = QPushButton(tr("close"))
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText(tr("import_log_placeholder"))
        layout.addWidget(self._log, stretch=1)

    # ── File selection ───────────────────────────────────────────────────────

    def set_path(self, path: Path) -> None:
        """Select a file and list the views it holds."""
        from opensak.importer.gsak_filter_importer import (
            GsakFilterSourceError, find_gsak_filter_db,
        )
        from opensak.importer.gsak_view_importer import load_gsak_views

        self._file_label.setText(path.name)
        self._file_label.setToolTip(str(path))
        self._list.clear()
        self._db3_path = None

        try:
            db3_path = find_gsak_filter_db(path)
            rows = load_gsak_views(db3_path)
        except GsakFilterSourceError as exc:
            self._found_label.setText("")
            self._set_list_enabled(False)
            self._update_import_button()
            QMessageBox.critical(
                self, tr("gsak_view_import_dialog_title"),
                tr("gsak_view_import_no_views", name=path.name, error=str(exc)),
            )
            return

        self._db3_path = db3_path
        if not rows:
            self._found_label.setText(tr("gsak_view_import_found", count=0))
            self._set_list_enabled(False)
            self._update_import_button()
            QMessageBox.information(
                self, tr("gsak_view_import_dialog_title"),
                tr("gsak_view_import_none_found", name=path.name),
            )
            return

        for name, _data in rows:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self._list.addItem(item)
        self._found_label.setText(tr("gsak_view_import_found", count=len(rows)))
        self._set_list_enabled(True)
        self._update_import_button()

    def _browse(self) -> None:
        settings = get_settings()
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            tr("gsak_filter_import_browse_title"),
            settings.last_import_dir,
            tr("gsak_filter_import_file_filter"),
        )
        if path_str:
            path = Path(path_str)
            settings.last_import_dir = str(path.parent)
            self.set_path(path)

    # ── Selection helpers ────────────────────────────────────────────────────

    def _set_list_enabled(self, enabled: bool) -> None:
        self._search.setEnabled(enabled)
        self._all_btn.setEnabled(enabled)
        self._none_btn.setEnabled(enabled)

    def _items(self) -> list[QListWidgetItem]:
        return [item for i in range(self._list.count())
                if (item := self._list.item(i)) is not None]

    def _apply_search(self, text: str) -> None:
        needle = text.strip().lower()
        for item in self._items():
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _check_visible(self, checked: bool) -> None:
        """Tick/untick every view the search box currently shows."""
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for item in self._items():
            if not item.isHidden():
                item.setCheckState(state)
        self._update_import_button()

    def _selected_names(self) -> list[str]:
        return [item.text() for item in self._items()
                if item.checkState() == Qt.CheckState.Checked]

    def _update_import_button(self) -> None:
        self._import_btn.setEnabled(
            self._db3_path is not None and bool(self._selected_names())
        )

    # ── Import ───────────────────────────────────────────────────────────────

    def _start_import(self) -> None:
        from opensak.importer.gsak_view_importer import import_gsak_views

        if self._db3_path is None:
            return
        names = self._selected_names()
        if not names:
            return

        self._log.clear()
        try:
            result = import_gsak_views(
                self._db3_path, names=names, overwrite=self._overwrite_cb.isChecked(),
            )
        except Exception:
            import traceback
            self._log.setPlainText(f"{tr('import_failed')}\n{traceback.format_exc()}")
            return
        self._log.setPlainText(format_result(result))
        self._import_btn.setText(tr("import_again"))
        if result.written > 0:
            self.import_completed.emit()


def format_result(result) -> str:
    """Render a GsakViewImportResult as the dialog's results text."""
    lines = [
        tr("gsak_filter_import_complete"),
        f"  {tr('gsak_view_import_written'):<32} {result.written}",
    ]
    if result.skipped:
        lines.append(f"  {tr('gsak_filter_import_skipped'):<32} {result.skipped}")
    if result.failed:
        lines.append(f"  {tr('gsak_filter_import_failed'):<32} {result.failed}")

    ignored = [e for e in result.entries
               if e.written and e.conversion and e.conversion.ignored]
    if ignored:
        lines += ["", tr("gsak_view_import_ignored_header", count=len(ignored))]
        for entry in ignored[:MAX_LISTED_VIEWS]:
            lines.append(f"    {entry.name}: {', '.join(entry.conversion.ignored)}")
        if len(ignored) > MAX_LISTED_VIEWS:
            lines.append(f"    … {len(ignored) - MAX_LISTED_VIEWS}")

    skipped = [e for e in result.entries if e.skipped_existing]
    if skipped:
        lines += ["", tr("gsak_view_import_skipped_header", count=len(skipped))]
        for entry in skipped[:MAX_LISTED_VIEWS]:
            lines.append(f"    {entry.name}")
        if len(skipped) > MAX_LISTED_VIEWS:
            lines.append(f"    … {len(skipped) - MAX_LISTED_VIEWS}")

    failed = [e for e in result.entries if e.error]
    if failed:
        lines += ["", tr("gsak_view_import_failed_header", count=len(failed))]
        for entry in failed[:10]:
            lines.append(f"    {entry.name}: {entry.error}")

    return "\n".join(lines)

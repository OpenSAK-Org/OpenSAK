"""
src/opensak/gui/dialogs/gsak_filter_import_dialog.py — import saved GSAK
filters as OpenSAK filter profiles.

The sibling of ``gsak_import_dialog.py``: that one imports the caches out of
a GSAK cache database (``sqlite.db3``), this one imports the saved filters out
of GSAK's settings database (``gsak.db3``). Same worker/threading pattern,
plus a checkable list of the filters found in the file, since a GSAK install
easily holds a hundred of them.

What each GSAK criterion becomes — a native filter, SQL in the Where tab, or
a ``--`` comment there — is decided by
``opensak.importer.gsak_filter_importer``; this dialog reports the resulting
migration coverage once the import has run.
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QProgressBar,
    QTextEdit, QCheckBox, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox,
)

from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.gui.settings import get_settings
from opensak.lang import tr
from opensak.gui.theme import hint_style

# How many per-filter lines the results log shows before it stops listing them.
MAX_LISTED_FILTERS = 25


class GsakFilterImportWorker(QThread):
    """Imports the selected GSAK filters in a background thread."""
    result_ready = Signal(object)    # GsakFilterImportResult
    error        = Signal(str)       # error message
    progress     = Signal(int, int)  # (done, total)
    # Completion is reported via QThread.finished — see GsakImportWorker in
    # gsak_import_dialog.py for why run() never emits a custom "done" signal.

    def __init__(self, db3_path: Path, names: list[str], overwrite: bool):
        super().__init__()
        self.db3_path = db3_path
        self.names = names
        self.overwrite = overwrite

    def run(self) -> None:
        from opensak.importer.gsak_filter_importer import import_gsak_filters

        try:
            result = import_gsak_filters(
                self.db3_path,
                names=self.names,
                overwrite=self.overwrite,
                progress_cb=lambda done, total: self.progress.emit(done, total),
            )
            self.result_ready.emit(result)
        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())


class GsakFilterImportDialog(QDialog):
    """Dialog for importing GSAK's saved filters (gsak.db3 or a .zip backup)."""

    import_completed = Signal()   # at least one filter profile was written

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("gsak_filter_import_dialog_title"))
        self.setMinimumWidth(620)
        self.setMinimumHeight(560)
        # Issue #811: never grow taller than the screen. Both of the parts
        # that can get long here — the filter list and the results log —
        # scroll on their own, so no extra QScrollArea is needed.
        clamp_dialog_height_to_screen(self, parent)
        self._worker: Optional[GsakFilterImportWorker] = None
        self._selected_path: Optional[Path] = None
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

        # ── Filter list ──────────────────────────────────────────────────────
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
        self._search.setPlaceholderText(tr("gsak_filter_import_search_placeholder"))
        self._search.textChanged.connect(self._apply_search)
        self._search.setEnabled(False)
        layout.addWidget(self._search)

        self._list = QListWidget()
        self._list.itemChanged.connect(lambda _item: self._update_import_button())
        layout.addWidget(self._list, stretch=1)

        self._overwrite_cb = QCheckBox(tr("gsak_filter_import_overwrite"))
        self._overwrite_cb.setToolTip(tr("gsak_filter_import_overwrite_tooltip"))
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

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText(tr("import_log_placeholder"))
        layout.addWidget(self._log, stretch=1)

    # ── File selection ───────────────────────────────────────────────────────

    def set_path(self, path: Path) -> None:
        """Select a file and list the filters it holds."""
        from opensak.importer.gsak_filter_importer import (
            GsakFilterSourceError, find_gsak_filter_db, load_gsak_filters,
        )

        self._selected_path = path
        self._file_label.setText(path.name)
        self._file_label.setToolTip(str(path))
        self._list.clear()
        self._db3_path = None

        try:
            db3_path = find_gsak_filter_db(path)
            rows = load_gsak_filters(db3_path)
        except GsakFilterSourceError as exc:
            self._found_label.setText("")
            self._set_list_enabled(False)
            self._update_import_button()
            QMessageBox.critical(
                self, tr("gsak_filter_import_dialog_title"),
                tr("gsak_filter_import_no_filters", name=path.name, error=str(exc)),
            )
            return

        self._db3_path = db3_path
        if not rows:
            self._found_label.setText(tr("gsak_filter_import_found", count=0))
            self._set_list_enabled(False)
            self._update_import_button()
            QMessageBox.information(
                self, tr("gsak_filter_import_dialog_title"),
                tr("gsak_filter_import_none_found", name=path.name),
            )
            return

        for name, _data in rows:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self._list.addItem(item)
        self._found_label.setText(tr("gsak_filter_import_found", count=len(rows)))
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

    def _apply_search(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _check_visible(self, checked: bool) -> None:
        """Tick/untick every filter the search box currently shows."""
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self._list.count()):
            item = self._list.item(i)
            if not item.isHidden():
                item.setCheckState(state)
        self._update_import_button()

    def _selected_names(self) -> list[str]:
        return [
            self._list.item(i).text()
            for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _update_import_button(self) -> None:
        running = self._worker is not None and self._worker.isRunning()
        self._import_btn.setEnabled(
            not running and self._db3_path is not None and bool(self._selected_names())
        )

    # ── Import ───────────────────────────────────────────────────────────────

    def _start_import(self) -> None:
        if self._db3_path is None:
            return
        names = self._selected_names()
        if not names:
            return

        self._import_btn.setEnabled(False)
        self._browse_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, len(names))
        self._progress.setValue(0)
        self._log.clear()
        self._append_log(tr("gsak_filter_import_running", count=len(names)))

        self._worker = GsakFilterImportWorker(
            self._db3_path, names, self._overwrite_cb.isChecked()
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.result_ready.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_done)
        self._worker.start()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
        else:
            self._progress.setRange(0, 0)

    def _on_result(self, result) -> None:
        self._append_log(format_result(result))
        if result.written > 0:
            self.import_completed.emit()

    def _on_error(self, msg: str) -> None:
        self._append_log(f"{tr('import_failed')}\n{msg}")

    def _on_done(self) -> None:
        self._progress.setVisible(False)
        self._append_log(tr("gsak_import_done"))
        self._browse_btn.setEnabled(True)
        self._import_btn.setText(tr("import_again"))
        self._worker = None
        self._update_import_button()

    def closeEvent(self, event) -> None:
        try:
            if self._worker and self._worker.isRunning():
                self._worker.wait()
        except RuntimeError:
            pass
        self._worker = None
        super().closeEvent(event)

    # ── Log helpers ──────────────────────────────────────────────────────────

    def _append_log(self, text: str) -> None:
        current = self._log.toPlainText()
        separator = "\n" + ("─" * 40) + "\n" if current else ""
        self._log.setPlainText(current + separator + text)
        self._log.verticalScrollBar().setValue(
            self._log.verticalScrollBar().maximum()
        )


def format_result(result) -> str:
    """Render a GsakFilterImportResult as the dialog's results text.

    Kept a module-level function (rather than a method) so the statistics can
    be tested without a dialog — the numbers are the point of the feature.
    """
    def pct(value: float) -> str:
        return f"{value * 100:.1f} %"

    lines = [
        tr("gsak_filter_import_complete"),
        f"  {tr('gsak_filter_import_written'):<32} {result.written}",
    ]
    if result.skipped:
        lines.append(f"  {tr('gsak_filter_import_skipped'):<32} {result.skipped}")
    if result.failed:
        lines.append(f"  {tr('gsak_filter_import_failed'):<32} {result.failed}")

    lines += [
        "",
        tr("gsak_filter_import_stats_header"),
        f"  {tr('gsak_filter_import_conditions'):<32} {result.total_criteria}",
        f"  {tr('gsak_filter_import_as_filters'):<32} {result.native_criteria}",
        f"  {tr('gsak_filter_import_as_sql'):<32} {result.sql_criteria}",
        f"  {tr('gsak_filter_import_as_comments'):<32} {result.commented_criteria}",
        "",
        f"  {tr('gsak_filter_import_coverage'):<32} {pct(result.coverage)}",
        f"    {tr('gsak_filter_import_full'):<30} {result.fully_migrated}",
        f"    {tr('gsak_filter_import_partial'):<30} {result.partially_migrated}",
        f"    {tr('gsak_filter_import_nothing'):<30} {result.not_migrated}",
    ]

    incomplete = [
        e for e in result.entries
        if e.written and e.conversion and e.conversion.coverage < 1.0
    ]
    if incomplete:
        incomplete.sort(key=lambda e: (e.conversion.coverage, e.name.lower()))
        lines += ["", tr("gsak_filter_import_incomplete_header", count=len(incomplete))]
        for entry in incomplete[:MAX_LISTED_FILTERS]:
            lines.append(
                f"    {entry.name[:40]:<42} {pct(entry.conversion.coverage):>7}  "
                + tr("gsak_filter_import_left_in_comments",
                     count=entry.conversion.not_migrated)
            )
        if len(incomplete) > MAX_LISTED_FILTERS:
            lines.append(f"    … {len(incomplete) - MAX_LISTED_FILTERS}")
        lines.append(tr("gsak_filter_import_where_hint"))

    skipped = [e for e in result.entries if e.skipped_existing]
    if skipped:
        lines += ["", tr("gsak_filter_import_skipped_header", count=len(skipped))]
        for entry in skipped[:MAX_LISTED_FILTERS]:
            lines.append(f"    {entry.name}")
        if len(skipped) > MAX_LISTED_FILTERS:
            lines.append(f"    … {len(skipped) - MAX_LISTED_FILTERS}")

    failed = [e for e in result.entries if e.error]
    if failed:
        lines += ["", tr("gsak_filter_import_failed_header", count=len(failed))]
        for entry in failed[:10]:
            lines.append(f"    {entry.name}: {entry.error}")

    return "\n".join(lines)

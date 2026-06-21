"""
src/opensak/gui/dialogs/import_dialog.py — GPX / PQ zip import dialog.

Supports selecting and importing multiple files in one session.
Files are imported sequentially in a background thread.
"""

from __future__ import annotations
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QProgressBar,
    QTextEdit, QListWidget, QListWidgetItem,
    QAbstractItemView, QComboBox,
)

from opensak.gui.settings import get_settings
from opensak.lang import tr


class ImportWorker(QThread):
    """Imports a list of files sequentially in a background thread."""
    file_started  = Signal(int, str)     # (index, filename)
    file_finished = Signal(int, object)  # (index, ImportResult)
    file_error    = Signal(int, str)     # (index, error message)
    progress      = Signal(int)          # cache count within current file
    # Completion is reported via QThread.finished (emitted after run() returns
    # and isRunning() is already false), NOT a custom signal emitted from inside
    # run(). Emitting from run()'s tail let the dialog proceed — and tests tear
    # the dialog down — while the QThread was still finishing, which aborts the
    # process ("QThread: Destroyed while thread is still running", SIGABRT).

    def __init__(self, paths: list[Path], target_db_path: Path | None = None):
        super().__init__()
        self.paths = paths
        self.target_db_path = target_db_path  # None → use currently active DB

    def run(self) -> None:
        from opensak.db.database import get_session, init_db
        from opensak.db.manager import get_db_manager
        from opensak.importer import import_gpx, import_zip
        from opensak.utils.utils import get_import_type, ImportType

        # Switch to target DB if different from active
        manager = get_db_manager()
        original_path = manager.active_path
        switched = (
            self.target_db_path is not None
            and self.target_db_path != original_path
        )
        if switched:
            init_db(db_path=self.target_db_path)

        try:
            for i, path in enumerate(self.paths):
                self.file_started.emit(i, path.name)
                try:
                    import_type: ImportType = get_import_type(path)
                    importers: dict[ImportType, Callable] = {
                        ImportType.GPX: import_gpx,
                        ImportType.ZIP: import_zip,
                    }
                    import_func = importers[import_type]

                    with get_session() as session:
                        result = import_func(
                            path,
                            session,
                            progress_cb=self.progress.emit
                        )

                    self.file_finished.emit(i, result)

                except ValueError as e:
                    self.file_error.emit(i, str(e))
                except Exception:
                    import traceback
                    self.file_error.emit(i, traceback.format_exc())
        finally:
            # Always restore the original active DB
            if switched and original_path is not None:
                init_db(db_path=original_path)
        # No explicit completion signal — QThread.finished fires automatically
        # once this method returns, and the dialog reacts to that instead.


class ImportDialog(QDialog):
    """Dialog for importing one or more GPX or PQ zip files."""

    import_completed = Signal()   # emitted when at least one file imported successfully

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("import_dialog_title"))
        self.setMinimumWidth(540)
        self.setMinimumHeight(420)
        self._worker: ImportWorker | None = None
        self._geo_worker = None
        self._selected_paths: list[Path] = []
        self._any_success = False
        self._db_combo: QComboBox | None = None
        self._setup_ui()
        self._populate_db_combo()

    def _populate_db_combo(self) -> None:
        """Fill the database combo with all known databases; pre-select the active one."""
        if self._db_combo is None:
            return
        from opensak.db.manager import get_db_manager
        manager = get_db_manager()
        active_path = manager.active_path
        self._db_combo.clear()
        active_index = 0
        for i, db in enumerate(manager.databases):
            label = db.name
            if db.path == active_path:
                label += f"  {tr('import_target_db_active')}"
                active_index = i
            self._db_combo.addItem(label, userData=db.path)
        self._db_combo.setCurrentIndex(active_index)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── File selection ────────────────────────────────────────────────────
        file_lbl = QLabel(tr("import_select_files_label"))
        layout.addWidget(file_lbl)

        # ── Database selector ─────────────────────────────────────────────────
        db_row = QHBoxLayout()
        db_lbl = QLabel(tr("import_target_db_label"))
        db_row.addWidget(db_lbl)
        self._db_combo = QComboBox()
        self._db_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._db_combo.setMinimumWidth(180)
        db_row.addWidget(self._db_combo)
        db_row.addStretch()
        layout.addLayout(db_row)

        # File list widget
        self._file_list = QListWidget()
        self._file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._file_list.setMaximumHeight(130)
        self._file_list.setAlternatingRowColors(True)
        layout.addWidget(self._file_list)

        # Browse + Remove + Import + Close row
        btn_row = QHBoxLayout()

        self._browse_btn = QPushButton(tr("import_browse"))
        self._browse_btn.clicked.connect(self._browse)
        btn_row.addWidget(self._browse_btn)

        self._remove_btn = QPushButton(tr("import_remove_selected"))
        self._remove_btn.setEnabled(False)
        self._remove_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(self._remove_btn)

        btn_row.addStretch()

        self._import_btn = QPushButton(tr("import_start"))
        self._import_btn.setEnabled(False)
        self._import_btn.clicked.connect(self._start_import)
        btn_row.addWidget(self._import_btn)

        self._close_btn = QPushButton(tr("close"))
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)

        layout.addLayout(btn_row)

        # ── Progress ──────────────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)   # indeterminate
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # ── Result log ────────────────────────────────────────────────────────
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText(tr("import_log_placeholder"))
        layout.addWidget(self._log)

        # Connect selection change for remove button state
        self._file_list.itemSelectionChanged.connect(self._on_selection_changed)

    # ── File management ───────────────────────────────────────────────────────

    def add_files(self, paths: list[Path]) -> None:
        """Add files to the import list (used by drag & drop from MainWindow)."""
        existing_names = {p.name for p in self._selected_paths}
        for p in paths:
            if p.name not in existing_names:
                self._selected_paths.append(p)
                item = QListWidgetItem(f"⏳  {p.name}")
                item.setToolTip(str(p))
                self._file_list.addItem(item)
                existing_names.add(p.name)
        if self._selected_paths:
            self._import_btn.setEnabled(True)

    def _browse(self) -> None:
        settings = get_settings()
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            tr("import_browse_title"),
            settings.last_import_dir,
            tr("import_file_filter")
        )
        if paths:
            existing_names = {p.name for p in self._selected_paths}
            for path_str in paths:
                p = Path(path_str)
                if p.name not in existing_names:
                    self._selected_paths.append(p)
                    item = QListWidgetItem(f"⏳  {p.name}")
                    item.setToolTip(str(p))
                    self._file_list.addItem(item)
                    existing_names.add(p.name)

            if self._selected_paths:
                settings.last_import_dir = str(self._selected_paths[-1].parent)
                self._import_btn.setEnabled(True)

    def _remove_selected(self) -> None:
        selected_rows = sorted(
            [self._file_list.row(item) for item in self._file_list.selectedItems()],
            reverse=True
        )
        for row in selected_rows:
            self._file_list.takeItem(row)
            del self._selected_paths[row]

        self._import_btn.setEnabled(bool(self._selected_paths))
        self._remove_btn.setEnabled(False)

    def _on_selection_changed(self) -> None:
        self._remove_btn.setEnabled(bool(self._file_list.selectedItems()))

    # ── Import ────────────────────────────────────────────────────────────────

    def _start_import(self) -> None:
        if not self._selected_paths:
            return

        self._import_btn.setEnabled(False)
        self._browse_btn.setEnabled(False)
        self._remove_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._any_success = False
        self._log.clear()

        # Reset all list item icons to waiting
        for i in range(self._file_list.count()):
            item = self._file_list.item(i)
            item.setText(f"⏳  {self._selected_paths[i].name}")

        target_db_path = (
            self._db_combo.currentData()
            if self._db_combo is not None and self._db_combo.count() > 0
            else None
        )
        self._worker = ImportWorker(list(self._selected_paths), target_db_path=target_db_path)
        self._worker.file_started.connect(self._on_file_started)
        self._worker.file_finished.connect(self._on_file_finished)
        self._worker.file_error.connect(self._on_file_error)
        self._worker.progress.connect(self._on_progress)
        # React to QThread.finished (thread already stopped) rather than a signal
        # emitted from inside run(). Connect _on_all_done before deleteLater so it
        # runs first; both are queued, and by the time they run isRunning() is
        # false, so deleting the worker can never abort the process.
        self._worker.finished.connect(self._on_all_done)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_file_started(self, index: int, name: str) -> None:
        item = self._file_list.item(index)
        if item:
            item.setText(f"🔄  {name}")
        self._file_list.scrollToItem(self._file_list.item(index))
        self._append_log(tr("import_running_file", name=name))

    def _on_progress(self, count: int) -> None:
        if count < 0:
            self._replace_last_log_line(f"  {tr('import_saving')}")
        elif count % 100 == 0 and count > 0:
            self._replace_last_log_line(f"  {tr('import_progress', count=count)}")

    def _on_file_finished(self, index: int, result) -> None:
        path = self._selected_paths[index]
        item = self._file_list.item(index)
        if item:
            item.setText(f"✅  {path.name}")

        lines = [
            tr("import_complete", name=path.name),
            f"  {tr('import_new_caches'):<20} {result.created}",
            f"  {tr('import_updated'):<20} {result.updated}",
            f"  {tr('import_waypoints'):<20} {result.waypoints}",
            f"  {tr('import_skipped'):<20} {result.skipped}",
        ]
        if result.errors:
            lines.append(tr("import_errors_header", count=len(result.errors)))
            for e in result.errors[:5]:
                lines.append(f"    - {e}")

        self._append_log("\n".join(lines))

        if result.created > 0 or result.updated > 0:
            self._any_success = True

    def _on_file_error(self, index: int, msg: str) -> None:
        path = self._selected_paths[index]
        item = self._file_list.item(index)
        if item:
            item.setText(f"❌  {path.name}")
        self._append_log(f"{tr('import_failed')} {path.name}\n{msg}")

    def _on_all_done(self) -> None:
        self._progress.setVisible(False)

        total = self._file_list.count()
        self._append_log(tr("import_all_done", count=total))

        if self._any_success:
            self.import_completed.emit()

        from opensak.utils import flags
        if flags.update_location and self._any_success:
            self._start_geocoding()
        else:
            self._browse_btn.setEnabled(True)
            self._import_btn.setText(tr("import_again"))
            self._import_btn.setEnabled(True)

    def _start_geocoding(self) -> None:
        from opensak.gui.dialogs.update_location_dialog import (
            ReverseGeocodeWorker, _CacheRow
        )
        from opensak.db.database import get_session
        from opensak.db.models import Cache
        from sqlalchemy.orm import joinedload

        self._append_log(tr("import_geocode_running"))

        rows = []
        with get_session() as session:
            query = session.query(Cache).options(joinedload(Cache.user_note)).filter(
                (Cache.country == None) | (Cache.country == "") |
                (Cache.state   == None) | (Cache.state   == "") |
                (Cache.county  == None) | (Cache.county  == "")
            )
            for cache in query.all():
                lat, lon = cache.latitude, cache.longitude
                basis = "posted"
                if cache.user_note and cache.user_note.is_corrected:
                    clat = cache.user_note.corrected_lat
                    clon = cache.user_note.corrected_lon
                    if clat is not None and clon is not None:
                        lat, lon = clat, clon
                        basis = "corrected"
                if lat is not None and lon is not None:
                    rows.append(_CacheRow(gc_code=cache.gc_code, lat=lat, lon=lon, basis=basis))

        if not rows:
            self._finish_geocoding()
            return

        self._progress.setRange(0, 0)  # indeterminate — batch runs instantly
        self._progress.setVisible(True)
        self._geocode_rows = rows

        self._geo_worker = ReverseGeocodeWorker(rows)
        self._geo_worker.all_done.connect(self._on_geocode_done)
        self._geo_worker.cancelled.connect(self._on_geocode_done)
        self._geo_worker.finished.connect(self._geo_worker.deleteLater)
        self._geo_worker.start()

    def _on_geocode_done(self, result) -> None:
        self._progress.setVisible(False)
        self._append_log(tr(
            "update_loc_done",
            updated=result.updated,
            skipped=result.skipped,
            errors=result.errors,
        ))
        if result.updated > 0:
            self.import_completed.emit()
        self._finish_geocoding()

    def _finish_geocoding(self) -> None:
        self._browse_btn.setEnabled(True)
        self._import_btn.setText(tr("import_again"))
        self._import_btn.setEnabled(True)

    def closeEvent(self, event) -> None:
        try:
            if self._worker and self._worker.isRunning():
                self._worker.wait()
        except RuntimeError:
            pass
        self._worker = None
        try:
            if self._geo_worker and self._geo_worker.isRunning():
                self._geo_worker.request_cancel()
                self._geo_worker.wait(3000)
        except RuntimeError:
            pass
        self._geo_worker = None
        super().closeEvent(event)

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _append_log(self, text: str) -> None:
        current = self._log.toPlainText()
        separator = "\n" + ("─" * 40) + "\n" if current else ""
        self._log.setPlainText(current + separator + text)
        self._log.verticalScrollBar().setValue(
            self._log.verticalScrollBar().maximum()
        )

    def _replace_last_log_line(self, new_line: str) -> None:
        text = self._log.toPlainText()
        lines = text.rsplit("\n", 1)
        self._log.setPlainText(lines[0] + "\n" + new_line)

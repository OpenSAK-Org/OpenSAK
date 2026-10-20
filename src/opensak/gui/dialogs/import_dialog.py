"""
src/opensak/gui/dialogs/import_dialog.py — GPX / PQ zip import dialog.

Supports selecting and importing multiple files in one session, and
folders: every GPX / zip file directly in a folder is imported, looked up
when the import starts. Files are imported sequentially in a background
thread.

The target database and the file and folder list can be saved under a name
and loaded again (see opensak.importer.gpx_import_settings); the settings of the most
recent import are restored when the dialog opens.
"""

from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QProgressBar,
    QTextEdit, QListWidget, QListWidgetItem,
    QAbstractItemView, QComboBox,
)

from opensak.gui.settings import get_settings
from opensak.lang import tr
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.gui.dialogs.file_export_dialog import SavedSettingsDialog
from opensak.importer.gpx_import_settings import (
    GpxImportProfile, GpxImportSettings, folder_import_files, import_file,
)

if TYPE_CHECKING:
    from opensak.gui.dialogs.update_location_dialog import ReverseGeocodeWorker

class ImportWorker(QThread):
    """Imports a list of files sequentially in a background thread."""
    file_started  = Signal(int, str)     # (index, filename)
    file_finished = Signal(int, object)  # (index, ImportResult)
    file_error    = Signal(int, str)     # (index, error message)
    progress      = Signal(int)          # cache count within current file
    total         = Signal(int)          # total wpt count for current file (-1 = unknown)
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
        from opensak.db.database import get_session, session_for
        from opensak.db.manager import get_db_manager
        from opensak.importer import _count_wpts
        from opensak.utils.utils import get_import_type, ImportType

        # Another database than the active one gets a private session —
        # never swap the app-wide engine from this thread (see session_for).
        active_path = get_db_manager().active_path
        other_db = (
            self.target_db_path is not None
            and self.target_db_path != active_path
        )

        def _session():
            return session_for(self.target_db_path) if other_db else get_session()

        for i, path in enumerate(self.paths):
            self.file_started.emit(i, path.name)
            try:
                if get_import_type(path) == ImportType.GPX:
                    try:
                        self.total.emit(_count_wpts(path))
                    except Exception:
                        self.total.emit(-1)
                else:
                    self.total.emit(-1)
                with _session() as session:
                    result = import_file(path, session, progress_cb=self.progress.emit)

                self.file_finished.emit(i, result)

            except ValueError as e:
                self.file_error.emit(i, str(e))
            except Exception:
                import traceback
                self.file_error.emit(i, traceback.format_exc())
        # No explicit completion signal — QThread.finished fires automatically
        # once this method returns, and the dialog reacts to that instead.


class ImportDialog(SavedSettingsDialog):
    """Dialog for importing one or more GPX or PQ zip files.

    *paths* (e.g. files or folders dropped on the main window) replace
    the file and folder list of the restored "last used" settings; the
    target database is kept.

    _selected_paths holds the list entries (files and folders, one per
    row); the folders among them are in _folders. _run_paths are the files
    an import of these entries covers — the worker's indices refer to it —
    and _run_rows the list row each of them belongs to.
    """

    import_completed = Signal()   # emitted when at least one file imported successfully

    _profile_cls = GpxImportProfile
    _settings_tr_keys = {
        "label":         "import_settings_label",
        "save_title":    "import_settings_save_title",
        "overwrite_msg": "import_settings_overwrite_msg",
        "delete_msg":    "import_settings_delete_msg",
        "load_error":    "import_settings_load_error",
    }

    def __init__(self, parent=None, paths: list[Path] | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("import_dialog_title"))
        self.setMinimumWidth(540)
        self.setMinimumHeight(420)
        # Issue #811: defensive cap, consistent with the other dialogs
        # audited for the same certification finding. The file list is
        # already height-capped and the log is a QTextEdit (own internal
        # scrollbar), so no additional QScrollArea needed here.
        clamp_dialog_height_to_screen(self, parent)
        self._worker: ImportWorker | None = None
        self._geo_worker: ReverseGeocodeWorker | None = None
        self._selected_paths: list[Path] = []
        self._folders: set[Path] = set()
        self._run_paths: list[Path] = []
        self._run_rows: list[int] = []
        self._failed_rows: set[int] = set()
        self._any_success = False
        self._db_combo: QComboBox | None = None
        self._setup_ui()
        self._populate_db_combo()
        settings = GpxImportProfile.load_last_used()
        if paths is not None:
            settings.files = [str(p) for p in paths if not p.is_dir()]
            settings.folders = [str(p) for p in paths if p.is_dir()]
        self._apply_settings(settings)

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

        self._browse_folder_btn = QPushButton(tr("settings_folder_perm_add"))
        self._browse_folder_btn.setToolTip(tr("import_browse_folder_tip"))
        self._browse_folder_btn.clicked.connect(self._browse_folder)
        btn_row.addWidget(self._browse_folder_btn)

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
        self._close_btn.clicked.connect(self._on_close_clicked)
        btn_row.addWidget(self._close_btn)

        layout.addLayout(btn_row)

        # ── Saved settings ────────────────────────────────────────────────────
        self._settings_group = self._build_settings_group()
        layout.addWidget(self._settings_group)

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

    # ── Settings ──────────────────────────────────────────────────────────────

    def _collect_settings(self) -> GpxImportSettings:
        database = (
            self._db_combo.currentData()
            if self._db_combo is not None and self._db_combo.count() > 0
            else None
        )
        return GpxImportSettings(
            database=str(database) if database is not None else "",
            files=[str(p) for p in self._selected_paths if p not in self._folders],
            folders=[str(p) for p in self._selected_paths if p in self._folders],
        )

    def _apply_settings(self, settings: GpxImportSettings) -> None:
        if self._db_combo is not None and settings.database:
            for i in range(self._db_combo.count()):
                if str(self._db_combo.itemData(i)) == settings.database:
                    self._db_combo.setCurrentIndex(i)
                    break
        self._file_list.clear()
        self._selected_paths = []
        self._folders = set()
        self._update_run()
        self._remove_btn.setEnabled(False)
        # Files and folders that have been moved or deleted since are left out.
        files = [Path(f) for f in settings.files]
        folders = [Path(f) for f in settings.folders]
        existing_files = [p for p in files if p.is_file()]
        existing_folders = [p for p in folders if p.is_dir()]
        missing = len(files) + len(folders) - len(existing_files) - len(existing_folders)
        self._log.clear()
        if missing:
            self._append_log(tr("import_settings_missing_files", count=missing))
        self.add_files(existing_files)
        self.add_folders(existing_folders)

    # ── File management ───────────────────────────────────────────────────────

    def _filter_companion_wpts(self, new_paths: list[Path]) -> list[Path]:
        from opensak.importer import _is_companion_gpx

        gpx_in_new = [p for p in new_paths if p.suffix.lower() == ".gpx"]
        if not gpx_in_new:
            return new_paths

        # Identify which new GPX files are companion (waypoints-only) files.
        new_companions = {p for p in gpx_in_new if _is_companion_gpx(p)}
        if not new_companions:
            return new_paths

        # Only filter companions when at least one main cache GPX is present
        # (in the new batch or already in the list). If all files are companions,
        # the user is importing them standalone — keep all.
        has_main = any(p not in new_companions for p in gpx_in_new)
        if not has_main:
            has_main = any(
                p.suffix.lower() == ".gpx" and not _is_companion_gpx(p)
                for p in self._selected_paths
            )
        if not has_main:
            return new_paths

        return [p for p in new_paths if p not in new_companions]

    def add_files(self, paths: list[Path]) -> None:
        """Add files to the import list (used by drag & drop from MainWindow)."""
        paths = self._filter_companion_wpts(paths)
        existing_names = {
            p.name for p in self._selected_paths if p not in self._folders
        }
        for p in paths:
            if p.name not in existing_names:
                self._add_entry(p)
                existing_names.add(p.name)
        self._update_run()

    def add_folders(self, folders: list[Path]) -> None:
        """Add folders to the import list: each imports every GPX / zip file
        directly in it, looked up when the import starts."""
        for folder in folders:
            if folder not in self._folders:
                self._folders.add(folder)
                self._add_entry(folder)
        self._update_run()

    def _add_entry(self, path: Path) -> None:
        self._selected_paths.append(path)
        item = QListWidgetItem(self._entry_text("⏳", len(self._selected_paths) - 1))
        item.setToolTip(str(path))
        self._file_list.addItem(item)

    def _entry_text(self, icon: str, row: int) -> str:
        path = self._selected_paths[row]
        if path in self._folders:
            return f"{icon}  📁 {path.name or path}"
        return f"{icon}  {path.name}"

    def _set_row_icon(self, row: int, icon: str) -> None:
        item = self._file_list.item(row)
        if item:
            item.setText(self._entry_text(icon, row))

    def _update_run(self) -> None:
        """Work out the files an import of the current entries covers.

        Folders are looked up again on every call, so a later import picks
        up files added to them since. A file listed twice (on its own and
        in a folder) is imported once.
        """
        self._run_paths, self._run_rows = [], []
        seen: set[Path] = set()
        for row, entry in enumerate(self._selected_paths):
            files = folder_import_files(entry) if entry in self._folders else [entry]
            for f in files:
                if f not in seen:
                    seen.add(f)
                    self._run_paths.append(f)
                    self._run_rows.append(row)
        self._import_btn.setEnabled(bool(self._selected_paths))

    def _browse(self) -> None:
        settings = get_settings()
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            tr("import_browse_title"),
            settings.last_import_dir,
            tr("import_file_filter")
        )
        if paths:
            self.add_files([Path(s) for s in paths])
            if self._selected_paths:
                settings.last_import_dir = str(Path(paths[-1]).parent)

    def _browse_folder(self) -> None:
        settings = get_settings()
        folder = QFileDialog.getExistingDirectory(
            self, tr("import_browse_folder_title"), settings.last_import_dir,
        )
        if folder:
            self.add_folders([Path(folder)])
            settings.last_import_dir = str(Path(folder))

    def _remove_selected(self) -> None:
        selected_rows = sorted(
            [self._file_list.row(item) for item in self._file_list.selectedItems()],
            reverse=True
        )
        for row in selected_rows:
            self._file_list.takeItem(row)
            self._folders.discard(self._selected_paths.pop(row))

        self._update_run()
        self._remove_btn.setEnabled(False)

    def _on_selection_changed(self) -> None:
        self._remove_btn.setEnabled(bool(self._file_list.selectedItems()))

    # ── Import ────────────────────────────────────────────────────────────────

    def _start_import(self) -> None:
        if not self._selected_paths:
            return
        self._update_run()
        if not self._run_paths:
            self._log.clear()
            self._append_log(tr("import_folder_empty"))
            return

        self._import_btn.setEnabled(False)
        self._browse_btn.setEnabled(False)
        self._browse_folder_btn.setEnabled(False)
        self._remove_btn.setEnabled(False)
        # Loading other settings would replace the list being imported.
        self._settings_group.setEnabled(False)
        # Issue #802: the import step itself has no cancellation support
        # (ImportWorker.run() has no cancel flag to check between files),
        # so simply disable Close rather than pretending it can respond —
        # clicking it while enabled used to block the GUI thread on
        # closeEvent()'s synchronous worker.wait().
        self._close_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._any_success = False
        self._failed_rows = set()
        self._log.clear()

        # Reset all list item icons to waiting
        for row in range(self._file_list.count()):
            self._set_row_icon(row, "⏳")

        target_db_path = (
            self._db_combo.currentData()
            if self._db_combo is not None and self._db_combo.count() > 0
            else None
        )
        try:
            GpxImportProfile.save_last_used(self._collect_settings())
        except OSError:
            pass  # failing to remember the settings must not block the import
        self._worker = ImportWorker(list(self._run_paths), target_db_path=target_db_path)
        self._worker.file_started.connect(self._on_file_started)
        self._worker.file_finished.connect(self._on_file_finished)
        self._worker.file_error.connect(self._on_file_error)
        self._worker.progress.connect(self._on_progress)
        self._worker.total.connect(self._on_total)
        # React to QThread.finished (thread already stopped) rather than a signal
        # emitted from inside run(). Connect _on_all_done before deleteLater so it
        # runs first; both are queued, and by the time they run isRunning() is
        # false, so deleting the worker can never abort the process.
        self._worker.finished.connect(self._on_all_done)
        # Issue #802 follow-up: without this, self._worker kept pointing at
        # a QThread whose C++ object deleteLater() (connected right below)
        # had already destroyed — a later Close click's isRunning() check
        # then hit "RuntimeError: Internal C++ object already deleted."
        # _on_worker_finished only runs once run() has returned and
        # isRunning() is already false, so it's always safe here.
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_file_started(self, index: int, name: str) -> None:
        row = self._run_rows[index]
        self._set_row_icon(row, "🔄")
        self._file_list.scrollToItem(self._file_list.item(row))
        self._append_log(tr("import_running_file", name=name))
        self._progress.setRange(0, 0)  # indeterminate while pre-counting

    def _on_total(self, total: int) -> None:
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(0)
        else:
            self._progress.setRange(0, 0)

    def _on_progress(self, count: int) -> None:
        if count < 0:
            self._replace_last_log_line(f"  {tr('import_saving')}")
        elif count % 100 == 0 and count > 0:
            self._replace_last_log_line(f"  {tr('import_progress', count=count)}")
        if count >= 0 and self._progress.maximum() > 0:
            self._progress.setValue(count)

    def _on_file_finished(self, index: int, result) -> None:
        path = self._run_paths[index]
        row = self._run_rows[index]
        # A folder stays marked failed when one of its files failed.
        if row not in self._failed_rows:
            self._set_row_icon(row, "✅")

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
        path = self._run_paths[index]
        row = self._run_rows[index]
        self._failed_rows.add(row)
        self._set_row_icon(row, "❌")
        self._append_log(f"{tr('import_failed')} {path.name}\n{msg}")

    def _on_all_done(self) -> None:
        self._progress.setVisible(False)

        self._append_log(tr("import_all_done", count=len(self._run_paths)))

        if self._any_success:
            self.import_completed.emit()

        from opensak.utils import flags
        if flags.reverse_geocoding and self._any_success:
            self._start_geocoding()
        else:
            self._browse_btn.setEnabled(True)
            self._browse_folder_btn.setEnabled(True)
            self._settings_group.setEnabled(True)
            self._import_btn.setText(tr("import_again"))
            self._import_btn.setEnabled(True)
            self._close_btn.setEnabled(True)

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

        # Issue #802: the geocode step *can* be cancelled responsively
        # (ReverseGeocodeWorker checks a flag between rows), so relabel
        # Close to Cancel and keep it enabled/clickable — _on_close_clicked()
        # routes the click to request_cancel() instead of closing.
        self._close_btn.setText(tr("cancel"))
        self._close_btn.setEnabled(True)

        # parent=self (issue #802, matching #801's fix in
        # update_location_dialog.py): keeps the C++ object owned by the
        # dialog, so Python refcounting can never delete a running thread
        # on its own.
        self._geo_worker = ReverseGeocodeWorker(rows, parent=self)
        self._geo_worker.all_done.connect(self._on_geocode_done)
        self._geo_worker.cancelled.connect(self._on_geocode_done)
        self._geo_worker.finished.connect(self._on_geo_worker_finished)
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
        self._browse_folder_btn.setEnabled(True)
        self._settings_group.setEnabled(True)
        self._import_btn.setText(tr("import_again"))
        self._import_btn.setEnabled(True)
        # Issue #802: revert Close back from its "Cancel" state.
        self._close_btn.setText(tr("close"))
        self._close_btn.setEnabled(True)

    def _on_worker_finished(self) -> None:
        """run() has returned and the thread has stopped — only now is it
        safe to drop our reference. Mirrors update_location_dialog.py's
        ReverseGeocodeWorker handling (#801/#802)."""
        self._worker = None

    def _on_geo_worker_finished(self) -> None:
        """See _on_worker_finished() above — same reasoning, for the
        reverse-geocode step's worker."""
        self._geo_worker = None

    def _on_close_clicked(self) -> None:
        """
        Issue #802: the Close button used to stay enabled throughout an
        import (and the follow-up "Looking up missing location data" step),
        and clicking it mid-operation blocked the GUI thread on
        closeEvent()'s synchronous worker.wait() — a freeze that looked and
        felt like a hang or crash on a large import.

        The import step itself has no cancellation support, so the button
        is simply disabled while it runs (see _start_import()) and this
        branch shouldn't normally be reachable then — guarded defensively
        regardless. The geocode step *can* be cancelled responsively
        (ReverseGeocodeWorker checks a flag between rows), so while it's
        running the button is relabelled "Cancel" and routed here instead
        of closing: request_cancel() just sets that flag and returns
        immediately, the actual stop is reported asynchronously via the
        cancelled signal → _on_geocode_done() → _finish_geocoding().
        """
        cancelled_geocode = False
        try:
            if self._geo_worker is not None and self._geo_worker.isRunning():
                self._geo_worker.request_cancel()
                cancelled_geocode = True
        except RuntimeError:
            # Belt-and-suspenders alongside the _on_*_finished nulling
            # above (#802 follow-up): the C++ QThread object can in theory
            # already be gone by the time this runs. Treat that the same
            # as "not running" rather than crashing.
            self._geo_worker = None
        if cancelled_geocode:
            self._close_btn.setEnabled(False)
            return

        try:
            if self._worker is not None and self._worker.isRunning():
                return
        except RuntimeError:
            self._worker = None

        self.accept()

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
                self._geo_worker.wait()
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

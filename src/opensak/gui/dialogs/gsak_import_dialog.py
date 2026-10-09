"""
src/opensak/gui/dialogs/gsak_import_dialog.py — GSAK direct database import dialog.

Session 4 of #469: the GUI wrapper around ``import_gsak_db()``. Mirrors
``import_dialog.py``'s worker/threading pattern (background QThread,
progress signal, log widget) and adds a one-time confirmation step (#472)
when the source database contains personal notes with embedded local images
that can't be carried over.

A GSAK backup .zip holds one folder per GSAK database (``<name>/sqlite.db3``)
plus GSAK's settings database ``gsak.db3``. Every database found is listed
and the user picks which ones to import; each goes into the OpenSAK database
named after its GSAK folder (editable), which is created when it doesn't
exist yet. An existing OpenSAK database is only touched after the user
confirmed it — overwritten (emptied first) or merged into. When the backup
also contains ``gsak.db3`` the saved filters can be migrated afterwards via
the regular GSAK filter import dialog, and the user locations (#1001) via a
preview of GSAK's location list. A single ``.db3`` file (or a zip with just
one database) is simply a list with one entry.

Only the selected databases are unpacked, into one temp folder that is
removed once the import (and the optional gsak.db3 migrations) is done.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QProgressBar,
    QTextEdit, QComboBox, QMessageBox, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)

from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.gui.settings import get_settings
from opensak.lang import tr
from opensak.gui.theme import hint_style

logger = logging.getLogger(__name__)

# Table columns
COL_GSAK, COL_SIZE, COL_TARGET, COL_STATUS = range(4)

# What happens to the target OpenSAK database of a job
MODE_NEW = "new"          # created by this import
MODE_MERGE = "merge"      # existing — upsert into it (the pre-backup behaviour)
MODE_REPLACE = "replace"  # existing — emptied first, then imported

# What a proposed OpenSAK database name resolves to (see _target_state)
TARGET_NEW = "new"            # nothing there yet — will be created
TARGET_EXISTING = "existing"  # a database in OpenSAK's list
TARGET_ORPHAN = "orphan"      # a valid database file that isn't in the list
TARGET_BLOCKED = "blocked"    # some other file sits on the path the name maps to


class GsakImportJob:
    """One GSAK database → one OpenSAK database."""

    def __init__(self, gsak_name: str, target_name: str, mode: str,
                 member: Optional[str] = None, db3_path: Optional[Path] = None,
                 target_path: Optional[Path] = None, size: int = 0,
                 register: bool = False):
        self.gsak_name = gsak_name
        self.target_name = target_name
        self.mode = mode
        self.member = member          # zip member still to unpack
        self.db3_path = db3_path      # file to import (set once unpacked)
        self.target_path = target_path
        self.size = size
        self.replace = False          # set on the first job of a MODE_REPLACE target
        self.register = register      # target is an orphan file: add it to the list first
        self.unpacked = False         # db3_path is a temp copy to delete afterwards


class GsakImportWorker(QThread):
    """Imports a single GSAK database in a background thread."""
    result_ready = Signal(object)   # GsakImportResult
    error        = Signal(str)      # error message
    progress     = Signal(int, int)  # (done, total)
    cleared      = Signal(int)      # caches removed before an overwrite
    # Completion is reported via QThread.finished (see ImportWorker in
    # import_dialog.py for the rationale — never emit a custom "done" signal
    # from inside run() itself).

    def __init__(self, db3_path: Path, target_db_path: Path | None = None,
                 replace: bool = False, update_distances: bool = True):
        super().__init__()
        self.db3_path = db3_path
        self.target_db_path = target_db_path  # None → use currently active DB
        self.replace = replace                # empty the target DB first
        # False when a later job of the same run imports into the same
        # target: that one recalculates, once for all of them.
        self.update_distances = update_distances

    def run(self) -> None:
        from opensak.db.database import get_session, session_for
        from opensak.db.manager import get_db_manager
        from opensak.importer.gsak_importer import (
            clear_opensak_cache_data, import_gsak_db,
        )

        # Another database than the active one gets a private session —
        # never swap the app-wide engine from this thread (see session_for).
        active_path = get_db_manager().active_path
        target_path = self.target_db_path or active_path
        other_db = (
            self.target_db_path is not None
            and self.target_db_path != active_path
        )

        try:
            with (session_for(self.target_db_path)
                  if other_db and self.target_db_path is not None
                  else get_session()) as session:
                if self.replace:
                    self.cleared.emit(clear_opensak_cache_data(session))
                result = import_gsak_db(
                    self.db3_path, session,
                    progress_cb=lambda done, total: self.progress.emit(done, total),
                )
            self.result_ready.emit(result)
            if self.update_distances:
                # Busy mode — the import's own progress sits at 100% meanwhile.
                self.progress.emit(0, 0)
                self._update_distances(target_path)
            else:
                self._invalidate_distances(target_path)
        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())

    @staticmethod
    def _update_distances(db_path: Path | None) -> None:
        """Bring the imported database's distances up to date while still in
        the background, so neither the refresh after the import nor a later
        switch to that database has to recalculate them on the GUI thread
        (both still check, as a safety net). Works on *db_path* through its own
        session when that isn't the active database, and its home point and
        the centre used are read from and stored in that file, not in the
        active database's settings. A failure
        here only means that check recalculates later — the import itself
        succeeded.
        """
        if db_path is None:
            return
        try:
            from opensak.db.database import distances_up_to_date, recalculate_distances
            lat, lon = get_settings().home_for_db_file(db_path)
            if lat and lon and not distances_up_to_date(lat, lon, db_path=db_path):
                recalculate_distances(lat, lon, db_path=db_path)
        except Exception:
            logger.warning("GSAK import: could not update distances for %s",
                           db_path, exc_info=True)

    @staticmethod
    def _invalidate_distances(db_path: Path | None) -> None:
        """Forget the centre *db_path*'s distances were calculated for, so
        distances_up_to_date() reports them stale. The target's last job
        then recalculates; should it never run (skipped, failed, cancelled),
        the check after the import or on switching to that database does.
        """
        if db_path is None:
            return
        try:
            from opensak.db import db_settings
            db_settings.write_file(db_path, {
                "dist_calc_lat": None,
                "dist_calc_lon": None,
                "dist_calc_method": None,
            })
        except Exception:
            logger.warning("GSAK import: could not invalidate distances for %s",
                           db_path, exc_info=True)


class GsakExtractWorker(QThread):
    """Unpacks the selected members of a GSAK backup .zip in the background.

    ``items`` is a list of ``(key, member, dest_dir)``; ``extracted`` maps each
    key to the unpacked file once all of them are done.
    """
    extracting = Signal(str)          # zip member being unpacked
    progress   = Signal(int, int)     # (KiB done, KiB total) over all members
    extracted  = Signal(object)       # {key: Path}
    error      = Signal(str)

    def __init__(self, zip_path: Path, items: list[tuple[str, str, Path]], total_bytes: int):
        super().__init__()
        self.zip_path = zip_path
        self.items = items
        self.total_bytes = max(total_bytes, 1)

    def run(self) -> None:
        from opensak.importer.gsak_importer import extract_gsak_member

        total_kib = max(self.total_bytes // 1024, 1)
        done_before = 0
        paths: dict[str, Path] = {}
        try:
            for key, member, dest_dir in self.items:
                self.extracting.emit(member)
                base = done_before

                def _cb(written: int, base=base) -> None:
                    self.progress.emit(min((base + written) // 1024, total_kib), total_kib)

                path = extract_gsak_member(self.zip_path, member, dest_dir, progress_cb=_cb)
                done_before += path.stat().st_size
                paths[key] = path
            self.extracted.emit(paths)
        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())


def _format_size(size: int) -> str:
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.1f} GB"
    if size >= 1024 ** 2:
        return f"{size / 1024 ** 2:.1f} MB"
    return f"{max(size, 0) / 1024:.0f} KB"


class GsakImportDialog(QDialog):
    """Dialog for importing GSAK databases (.zip backup or a single .db3 file)."""

    import_completed  = Signal()   # the import created/updated at least one cache
    databases_changed = Signal()   # at least one OpenSAK database was created
    filters_imported  = Signal()   # the follow-up filter import wrote profiles
    locations_imported = Signal()  # the follow-up location import changed the list

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("gsak_import_dialog_title"))
        self.setMinimumWidth(680)
        self.setMinimumHeight(520)
        clamp_dialog_height_to_screen(self, parent)
        self._worker: Optional[QThread] = None
        self._selected_path: Path | None = None
        self._contents = None                   # GsakBackupContents
        self._jobs: list[GsakImportJob] = []
        self._job_index = 0
        self._temp_dir: Optional[Path] = None
        self._extracted: Optional[dict] = None      # what the extract worker unpacked
        self._wants_filters = False             # migrate gsak.db3's filters at the end
        self._wants_locations = False           # migrate gsak.db3's user locations at the end
        self._stage_settings = False            # the running extraction is gsak.db3
        self._images_confirmed = False          # #472 warning answered with Continue
        self._changed = False                   # any cache created/updated this run
        self._setup_ui()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        file_lbl = QLabel(tr("gsak_import_select_file_label"))
        layout.addWidget(file_lbl)

        file_row = QHBoxLayout()
        self._file_label = QLabel("")
        self._file_label.setStyleSheet(hint_style(font_size=None))
        file_row.addWidget(self._file_label, stretch=1)
        self._browse_btn = QPushButton(tr("import_browse"))
        self._browse_btn.clicked.connect(self._browse)
        file_row.addWidget(self._browse_btn)
        layout.addLayout(file_row)

        # ── Database list ────────────────────────────────────────────────────
        list_header = QHBoxLayout()
        self._found_label = QLabel("")
        list_header.addWidget(self._found_label, stretch=1)
        self._all_btn = QPushButton(tr("gsak_filter_import_select_all"))
        self._all_btn.clicked.connect(lambda: self._check_all(True))
        list_header.addWidget(self._all_btn)
        self._none_btn = QPushButton(tr("gsak_filter_import_select_none"))
        self._none_btn.clicked.connect(lambda: self._check_all(False))
        list_header.addWidget(self._none_btn)
        layout.addLayout(list_header)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels([
            tr("gsak_import_col_gsak_db"), tr("file_locations_col_size"),
            tr("gsak_import_col_target"), tr("gsak_import_col_status"),
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(COL_GSAK, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_SIZE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_TARGET, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        self._table.itemChanged.connect(lambda _item: self._update_import_button())
        layout.addWidget(self._table, stretch=1)

        self._filters_cb = QCheckBox(tr("gsak_import_filters_checkbox"))
        self._filters_cb.setVisible(False)
        self._filters_cb.toggled.connect(lambda _on: self._update_import_button())
        layout.addWidget(self._filters_cb)

        self._locations_cb = QCheckBox(tr("gsak_import_locations_checkbox"))
        self._locations_cb.setVisible(False)
        self._locations_cb.toggled.connect(lambda _on: self._update_import_button())
        layout.addWidget(self._locations_cb)

        # Import + Close row
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

        # ── Progress ──────────────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # ── Result log ────────────────────────────────────────────────────────
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText(tr("import_log_placeholder"))
        layout.addWidget(self._log, stretch=1)

        self._set_list_enabled(False)

    # ── File selection ───────────────────────────────────────────────────────

    def set_path(self, path: Path) -> None:
        """Select a file (used by drag & drop from MainWindow) and list the
        GSAK databases it holds."""
        from opensak.importer.gsak_importer import list_gsak_backup

        self._selected_path = path
        self._file_label.setText(path.name)
        self._file_label.setToolTip(str(path))
        self._table.setRowCount(0)
        self._contents = None
        for cb in (self._filters_cb, self._locations_cb):
            cb.setVisible(False)
            cb.setChecked(False)
        self._found_label.setText("")

        try:
            contents = list_gsak_backup(path)
        except Exception as exc:  # not a zip, unreadable, …
            self._set_list_enabled(False)
            self._update_import_button()
            QMessageBox.critical(
                self, tr("gsak_import_dialog_title"),
                f"{tr('gsak_import_no_db3_found', name=path.name)}\n\n{exc}",
            )
            return

        if not contents.databases and not contents.has_settings_db:
            self._set_list_enabled(False)
            self._update_import_button()
            QMessageBox.critical(
                self, tr("gsak_import_dialog_title"),
                tr("gsak_import_no_db3_found", name=path.name),
            )
            return

        self._contents = contents
        self._fill_table(contents.databases)
        self._found_label.setText(
            tr("gsak_import_databases_found", count=len(contents.databases))
        )
        if contents.has_settings_db:
            for cb in (self._filters_cb, self._locations_cb):
                cb.setVisible(True)
                cb.setChecked(True)
        self._set_list_enabled(bool(contents.databases))
        self._update_import_button()

    def _fill_table(self, databases) -> None:
        from opensak.db.manager import get_db_manager

        known = [db.name for db in get_db_manager().databases]
        self._table.blockSignals(True)
        self._table.setRowCount(len(databases))
        for row, entry in enumerate(databases):
            name_item = QTableWidgetItem(entry.name)
            name_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            name_item.setCheckState(Qt.CheckState.Checked)
            name_item.setData(Qt.ItemDataRole.UserRole, entry)
            self._table.setItem(row, COL_GSAK, name_item)

            size_item = QTableWidgetItem(_format_size(entry.size))
            size_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            size_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._table.setItem(row, COL_SIZE, size_item)

            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(known)
            combo.setCurrentText(self._free_name(entry.name))
            combo.currentTextChanged.connect(
                lambda _text, r=row: self._on_target_changed(r)
            )
            self._table.setCellWidget(row, COL_TARGET, combo)

            status_item = QTableWidgetItem("")
            status_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(row, COL_STATUS, status_item)
            self._update_status(row)
        self._table.blockSignals(False)

    def _browse(self) -> None:
        settings = get_settings()
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            tr("gsak_import_browse_title"),
            settings.last_import_dir,
            tr("gsak_import_file_filter"),
        )
        if path_str:
            path = Path(path_str)
            settings.last_import_dir = str(path.parent)
            self.set_path(path)

    # ── Selection helpers ────────────────────────────────────────────────────

    def _set_list_enabled(self, enabled: bool) -> None:
        self._table.setEnabled(enabled)
        self._all_btn.setEnabled(enabled)
        self._none_btn.setEnabled(enabled)

    def _check_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self._table.rowCount()):
            self._gsak_item(row).setCheckState(state)
        self._update_import_button()

    def _gsak_item(self, row: int) -> QTableWidgetItem:
        item = self._table.item(row, COL_GSAK)
        assert item is not None   # every row gets one in _fill_table()
        return item

    def _target_name(self, row: int) -> str:
        combo = self._table.cellWidget(row, COL_TARGET)
        return combo.currentText().strip() if isinstance(combo, QComboBox) else ""

    @staticmethod
    def _find_existing(name: str):
        """The known OpenSAK database called *name* (case-insensitive — the
        file name is derived from it, and Windows file names ignore case)."""
        from opensak.db.manager import get_db_manager

        wanted = name.lower()
        for db in get_db_manager().databases:
            if db.name.lower() == wanted:
                return db
        return None

    @classmethod
    def _target_state(cls, name: str):
        """Resolve an OpenSAK database name: ``(TARGET_*, DatabaseInfo | Path)``.

        A name that isn't in the database list can still collide with a file
        on disk (new_database() derives the path from the name and refuses
        to reuse a file) — e.g. a stale ``Default.db`` left behind. A valid
        database file is offered as an existing target; anything else blocks
        the name.
        """
        from opensak.db.manager import get_db_manager

        existing = cls._find_existing(name)
        if existing is not None:
            return TARGET_EXISTING, existing
        manager = get_db_manager()
        path = manager.default_path_for(name)
        for db in manager.databases:
            if db.path == path:
                return TARGET_EXISTING, db
        if path.exists():
            if manager.is_valid_database_file(path):
                return TARGET_ORPHAN, path
            return TARGET_BLOCKED, path
        return TARGET_NEW, path

    @classmethod
    def _free_name(cls, name: str) -> str:
        """*name*, or ``name-2``, ``name-3``, … when a stray file blocks it."""
        candidate, n = name, 2
        while cls._target_state(candidate)[0] == TARGET_BLOCKED:
            candidate = f"{name}-{n}"
            n += 1
        return candidate

    def _update_status(self, row: int) -> None:
        item = self._table.item(row, COL_STATUS)
        if item is None:
            return
        name = self._target_name(row)
        if not name:
            item.setText("")
            item.setToolTip("")
            return
        state, where = self._target_state(name)
        if state == TARGET_BLOCKED:
            item.setText(tr("gsak_import_status_blocked"))
        elif state in (TARGET_EXISTING, TARGET_ORPHAN):
            item.setText(tr("file_locations_col_exists"))
        else:
            item.setText(tr("gsak_import_status_new"))
        item.setToolTip(str(getattr(where, "path", where)))

    def _on_target_changed(self, row: int) -> None:
        self._update_status(row)
        self._update_import_button()

    def _checked_rows(self) -> list[int]:
        return [
            row for row in range(self._table.rowCount())
            if self._gsak_item(row).checkState() == Qt.CheckState.Checked
        ]

    @staticmethod
    def _wanted(cb: QCheckBox) -> bool:
        return not cb.isHidden() and cb.isChecked()

    def _update_import_button(self) -> None:
        running = self._worker is not None and self._worker.isRunning()
        wants_settings = self._wanted(self._filters_cb) or self._wanted(self._locations_cb)
        self._import_btn.setEnabled(
            not running and self._contents is not None
            and (bool(self._checked_rows()) or wants_settings)
        )

    def _set_busy(self, busy: bool) -> None:
        self._browse_btn.setEnabled(not busy)
        self._import_btn.setEnabled(not busy)
        self._filters_cb.setEnabled(not busy)
        self._locations_cb.setEnabled(not busy)
        self._set_list_enabled(not busy and self._table.rowCount() > 0)
        self._progress.setVisible(busy)
        if busy:
            self._progress.setRange(0, 0)

    # ── Import ────────────────────────────────────────────────────────────────

    def _collect_jobs(self) -> Optional[list[GsakImportJob]]:
        """Build one job per ticked row; None when a row can't be imported
        (no target name, or a stray file blocks it)."""
        jobs: list[GsakImportJob] = []
        for row in self._checked_rows():
            entry = self._gsak_item(row).data(Qt.ItemDataRole.UserRole)
            target = self._target_name(row)
            if not target:
                QMessageBox.warning(
                    self, tr("gsak_import_dialog_title"),
                    tr("gsak_import_empty_target", name=entry.name),
                )
                return None
            state, where = self._target_state(target)
            if state == TARGET_BLOCKED:
                QMessageBox.warning(
                    self, tr("gsak_import_dialog_title"),
                    tr("gsak_import_blocked_target", name=target, path=str(where)),
                )
                return None
            job = GsakImportJob(
                gsak_name=entry.name,
                target_name=where.name if state == TARGET_EXISTING else target,
                mode=MODE_NEW if state == TARGET_NEW else MODE_MERGE,
                member=entry.member,
                db3_path=entry.path,
                size=entry.size,
                register=state == TARGET_ORPHAN,
            )
            if state == TARGET_EXISTING:
                job.target_path = where.path
            elif state == TARGET_ORPHAN:
                job.target_path = where
            jobs.append(job)
        return jobs

    def _confirm_existing(self, jobs: list[GsakImportJob]) -> Optional[list[GsakImportJob]]:
        """Ask once what to do with targets that already exist.

        Returns the jobs to run (existing ones marked replace/merge or
        dropped), or None when the user cancelled.
        """
        existing = sorted({j.target_name for j in jobs if j.mode != MODE_NEW}, key=str.lower)
        if not existing:
            return jobs

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(tr("gsak_import_existing_title"))
        box.setText(tr(
            "gsak_import_existing_body",
            names="\n".join(f"  • {name}" for name in existing),
        ))
        overwrite_btn = box.addButton(tr("gsak_import_existing_overwrite"),
                                      QMessageBox.ButtonRole.DestructiveRole)
        merge_btn = box.addButton(tr("gsak_import_existing_merge"),
                                  QMessageBox.ButtonRole.AcceptRole)
        skip_btn = box.addButton(tr("gsak_import_existing_skip"),
                                 QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(skip_btn)
        box.exec()
        clicked = box.clickedButton()

        if clicked is overwrite_btn:
            cleared: set[str] = set()
            for job in jobs:
                if job.mode != MODE_NEW:
                    job.mode = MODE_REPLACE
                    # Several GSAK databases may go into the same target:
                    # only the first one empties it.
                    job.replace = job.target_name not in cleared
                    cleared.add(job.target_name)
            return jobs
        if clicked is merge_btn:
            return jobs
        if clicked is skip_btn:
            return [j for j in jobs if j.mode == MODE_NEW]
        return None

    # ── Run ──────────────────────────────────────────────────────────────────
    #
    # One database at a time: unpack it (zip only) → #472 pre-scan → create or
    # register the target → import → delete the unpacked copy. So the temp
    # folder never holds more than one database (a full backup is several
    # GB), nothing is created for a database that is skipped, and one failing
    # database is logged and skipped instead of aborting the rest. The user
    # locations and saved filters (gsak.db3) come last.

    def _start_import(self) -> None:
        if self._selected_path is None or self._contents is None:
            return

        jobs = self._collect_jobs()
        if jobs is None:
            return
        jobs = self._confirm_existing(jobs)
        if jobs is None:
            return
        wants_filters = self._wanted(self._filters_cb)
        wants_locations = self._wanted(self._locations_cb)
        if not jobs and not wants_filters and not wants_locations:
            return

        self._jobs = jobs
        self._job_index = 0
        self._changed = False
        self._images_confirmed = False
        self._wants_filters = wants_filters
        self._wants_locations = wants_locations
        self._log.clear()
        self._set_busy(True)
        if self._contents.is_zip:
            self._temp_dir = Path(tempfile.mkdtemp(prefix="gsak_import_"))
        self._run_next_job()

    def _current_job(self) -> Optional[GsakImportJob]:
        if 0 <= self._job_index < len(self._jobs):
            return self._jobs[self._job_index]
        return None

    def _run_next_job(self) -> None:
        job = self._current_job()
        if job is None:
            self._start_settings_stage()
            return
        if job.db3_path is None:
            assert job.member is not None and self._temp_dir is not None
            self._start_extract(job.member, self._temp_dir / f"{self._job_index:02d}", job.size)
            return
        self._import_current_job()

    # ── Unpacking ────────────────────────────────────────────────────────────

    def _start_extract(self, member: str, dest_dir: Path, size: int) -> None:
        assert self._selected_path is not None
        self._append_log(tr("gsak_import_extracting", name=member))
        worker = GsakExtractWorker(self._selected_path, [("file", member, dest_dir)], size)
        worker.extracting.connect(
            lambda name: self._progress.setFormat(f"{name}  %p%")
        )
        worker.progress.connect(self._on_progress)
        worker.extracted.connect(self._on_extracted)
        worker.error.connect(self._on_error)
        # The next step starts from QThread.finished, never from a signal
        # emitted inside run(): the thread must have ended completely before
        # its worker object is let go of.
        worker.finished.connect(self._on_extract_done)
        self._extracted = None
        self._worker = worker
        worker.start()

    def _retire_worker(self) -> None:
        """Let go of the finished worker — only once its thread has really
        ended (destroying a QThread that is still winding down hangs Qt)."""
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        try:
            worker.wait()
            worker.deleteLater()
        except RuntimeError:
            pass

    def _on_extracted(self, paths: dict) -> None:
        self._extracted = paths

    def _on_extract_done(self) -> None:
        self._retire_worker()
        self._progress.setFormat("%p%")
        path = (self._extracted or {}).get("file")
        self._extracted = None

        if self._stage_settings:
            self._stage_settings = False
            if path is not None:
                self._run_settings_imports(path)
            self._finish()
            return

        job = self._current_job()
        if job is None:              # the dialog is closing
            self._finish()
            return
        if path is None:             # unpacking failed (already logged)
            self._skip_job(job)
            return
        job.db3_path = path
        job.unpacked = True
        self._import_current_job()

    # ── Importing ────────────────────────────────────────────────────────────

    def _import_current_job(self) -> None:
        job = self._current_job()
        assert job is not None and job.db3_path is not None

        verdict = self._confirm_embedded_images(job.db3_path)
        if verdict == "cancel":
            self._append_log(tr("gsak_import_cancelled"))
            self._discard_unpacked(job)
            self._jobs = []
            self._finish()
            return
        if verdict == "skip" or not self._prepare_target(job):
            self._skip_job(job)
            return

        self._progress.setRange(0, 0)
        self._append_log(tr("gsak_import_running",
                            name=f"{job.gsak_name} → {job.target_name}"))
        later = self._jobs[self._job_index + 1:]
        worker = GsakImportWorker(
            job.db3_path, target_db_path=job.target_path, replace=job.replace,
            update_distances=all(j.target_name != job.target_name for j in later),
        )
        worker.progress.connect(self._on_progress)
        worker.cleared.connect(
            lambda count, name=job.target_name:
                self._append_log(tr("gsak_import_cleared", count=count, name=name))
        )
        worker.result_ready.connect(self._on_result)
        worker.error.connect(self._on_error)
        worker.finished.connect(self._on_job_done)
        self._worker = worker
        worker.start()

    def _confirm_embedded_images(self, db3_path: Path) -> str:
        """Issue #472: warn about embedded local images in notes.

        Returns "continue", "skip" (this database) or "cancel" (the whole
        run). Once the user chose to continue, later databases aren't asked
        about again.
        """
        if self._images_confirmed:
            return "continue"
        from opensak.importer.gsak_importer import scan_gsak_notes_for_embedded_images

        scan = scan_gsak_notes_for_embedded_images(db3_path)
        if not scan["affected_notes"]:
            return "continue"

        box = QMessageBox(self)
        box.setWindowTitle(tr("gsak_prescan_title"))
        box.setText(tr("gsak_prescan_body",
                       notes=scan["affected_notes"], images=scan["total_images"]))
        continue_btn = box.addButton(tr("gsak_prescan_continue"), QMessageBox.ButtonRole.AcceptRole)
        skip_btn = None
        if len(self._jobs) > 1:
            skip_btn = box.addButton(tr("gsak_import_existing_skip"),
                                     QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(continue_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is continue_btn:
            self._images_confirmed = True
            return "continue"
        if skip_btn is not None and clicked is skip_btn:
            return "skip"
        return "cancel"

    def _prepare_target(self, job: GsakImportJob) -> bool:
        """Create (or add to the list) the job's OpenSAK database — in the
        GUI thread, the database manager and settings aren't thread-safe.
        Failures are logged; the caller skips the job."""
        from opensak.db.manager import get_db_manager

        manager = get_db_manager()
        try:
            if job.mode == MODE_NEW:
                # An earlier job of this run may already have created it.
                info = self._find_existing(job.target_name)
                if info is None:
                    info = manager.new_database(job.target_name)
                    self._append_log(tr("gsak_import_created", name=info.name))
                    self.databases_changed.emit()
                job.target_path = info.path
            elif job.register:
                assert job.target_path is not None
                info = manager.open_database(job.target_path)
                job.target_name = info.name
                job.register = False
                self.databases_changed.emit()
        except Exception as exc:
            self._append_log(tr("gsak_import_create_failed",
                                name=job.target_name, error=str(exc)))
            return False
        return True

    def _skip_job(self, job: GsakImportJob) -> None:
        self._append_log(tr("gsak_import_skipped_db", name=job.gsak_name))
        self._discard_unpacked(job)
        self._job_index += 1
        self._run_next_job()

    def _discard_unpacked(self, job: GsakImportJob) -> None:
        """Delete a job's unpacked copy as soon as it's no longer needed."""
        if not job.unpacked or job.db3_path is None:
            return
        import gc
        gc.collect()   # drop stray sqlite handles so Windows lets go of the file
        shutil.rmtree(job.db3_path.parent, ignore_errors=True)
        job.db3_path = None
        job.unpacked = False

    def _on_job_done(self) -> None:
        self._retire_worker()
        job = self._current_job()
        if job is not None:
            self._discard_unpacked(job)
        self._job_index += 1
        self._run_next_job()

    # ── gsak.db3: user locations and saved filters ───────────────────────────

    def _start_settings_stage(self) -> None:
        contents = self._contents
        if not (self._wants_filters or self._wants_locations) or contents is None:
            self._finish()
            return
        if contents.settings_member is not None and self._temp_dir is not None:
            self._stage_settings = True
            self._start_extract(contents.settings_member, self._temp_dir / "settings", 0)
            return
        if contents.settings_path is not None:
            self._run_settings_imports(contents.settings_path)
        self._finish()

    def _run_settings_imports(self, db3_path: Path) -> None:
        """Run the gsak.db3 migrations that were asked for — one unpacked
        copy serves both."""
        wants_locations, self._wants_locations = self._wants_locations, False
        wants_filters, self._wants_filters = self._wants_filters, False
        if wants_locations:
            self._run_location_import(db3_path)
        if wants_filters:
            self._run_filter_import(db3_path)

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
        else:
            self._progress.setRange(0, 0)

    def _current_job_label(self) -> str:
        if 0 <= self._job_index < len(self._jobs):
            job = self._jobs[self._job_index]
            return f"{job.gsak_name} → {job.target_name}"
        return self._selected_path.name if self._selected_path else ""

    def _on_result(self, result) -> None:
        lines = [
            tr("import_complete", name=self._current_job_label()),
            f"  {tr('import_new_caches'):<28} {result.created}",
            f"  {tr('import_updated'):<28} {result.updated}",
            f"  {tr('import_waypoints'):<28} {result.waypoints}",
            f"  {tr('gsak_import_attributes'):<28} {result.attributes}",
            f"  {tr('gsak_import_logs'):<28} {result.logs}",
            f"  {tr('gsak_import_notes'):<28} {result.notes}",
            f"  {tr('gsak_import_note_images'):<28} {result.note_images_replaced}",
            f"  {tr('gsak_import_trackables'):<28} {result.trackables}",
            f"  {tr('corrected_dialog_corrected'):<28} {result.corrected}",
            f"  {tr('import_skipped'):<28} {result.skipped}",
        ]
        if result.warnings:
            lines.append(tr("gsak_import_warnings_header", count=len(result.warnings)))
            for w in result.warnings[:10]:
                lines.append(f"    - {w}")
        if result.errors:
            lines.append(tr("import_errors_header", count=len(result.errors)))
            for e in result.errors[:5]:
                lines.append(f"    - {e}")

        self._append_log("\n".join(lines))

        if result.created > 0 or result.updated > 0:
            self._changed = True

    def _on_error(self, msg: str) -> None:
        self._append_log(f"{tr('import_failed')}\n{msg}")

    def _finish(self) -> None:
        """The run is over (done or aborted): clean up and re-enable the dialog."""
        self._retire_worker()
        self._progress.setVisible(False)
        self._progress.setFormat("%p%")
        if self._changed:
            # Once per run, not per database — the main window recalculates
            # distances and reloads the list on every emit.
            self._changed = False
            self.import_completed.emit()
        self._cleanup_temp()
        self._append_log(tr("gsak_import_done"))
        self._set_busy(False)
        self._import_btn.setText(tr("import_again"))
        self._update_import_button()

    def _run_location_import(self, db3_path: Path) -> None:
        """Issue #1001: preview GSAK's user locations and save the chosen ones."""
        from opensak.gui.dialogs.gsak_location_import_dialog import (
            GsakLocationImportDialog, location_error_text,
        )
        from opensak.importer.gsak_location_importer import (
            GsakLocationSourceError, load_gsak_locations, parse_gsak_locations,
        )

        try:
            locations = parse_gsak_locations(load_gsak_locations(db3_path))
        except GsakLocationSourceError as exc:
            self._append_log(tr("gsak_location_import_failed", error=str(exc)))
            return
        if not locations:
            self._append_log(tr("gsak_location_import_none"))
            return

        dlg = GsakLocationImportDialog(locations, self)
        dlg.exec()
        result = dlg.result_data
        if result is None:
            self._append_log(tr("gsak_location_import_cancelled"))
            return

        lines = [tr("gsak_location_import_result", added=len(result.added),
                    updated=len(result.updated), skipped=len(result.skipped))]
        invalid = [loc for loc in locations if not loc.valid]
        if invalid:
            lines.append(tr("gsak_location_import_invalid_header", count=len(invalid)))
            for loc in invalid:
                lines.append("    - " + tr("gsak_location_import_invalid_line",
                                           line=loc.line_no, text=loc.text,
                                           error=location_error_text(loc)))
        self._append_log("\n".join(lines))
        if result.added or result.updated:
            self.locations_imported.emit()

    def _run_filter_import(self, db3_path: Path) -> None:
        """Hand gsak.db3 to the regular GSAK filter import dialog."""
        from opensak.gui.dialogs.gsak_filter_import_dialog import GsakFilterImportDialog

        dlg = GsakFilterImportDialog(self)
        dlg.import_completed.connect(self.filters_imported)
        dlg.set_path(db3_path)
        dlg.exec()

    def _cleanup_temp(self) -> None:
        if self._temp_dir is None:
            return
        import gc
        gc.collect()   # drop stray sqlite handles so Windows lets go of the files
        shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir = None

    def _abort_pending(self) -> None:
        """Closing mid-run: let the running worker finish, start nothing new."""
        self._jobs = []
        self._wants_filters = False
        self._wants_locations = False
        self._stage_settings = False
        self._extracted = None
        try:
            if self._worker and self._worker.isRunning():
                self._worker.wait()
        except RuntimeError:
            pass

    def closeEvent(self, event) -> None:
        self._abort_pending()
        self._cleanup_temp()
        super().closeEvent(event)

    def done(self, result: int) -> None:
        # accept()/reject() (Close button, Esc) bypass closeEvent.
        self._abort_pending()
        self._cleanup_temp()
        super().done(result)

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _append_log(self, text: str) -> None:
        current = self._log.toPlainText()
        separator = "\n" + ("─" * 40) + "\n" if current else ""
        self._log.setPlainText(current + separator + text)
        self._log.verticalScrollBar().setValue(
            self._log.verticalScrollBar().maximum()
        )

"""
src/opensak/gui/dialogs/database_maintenance_dialog.py — Maintenance of one
database, opened from the Database Manager (#1048, #1149 phase 5).

For the selected database and its companion database (#1034) together:

* **Info** — size, -wal size, free space inside the file, schema and SQLite
  version, row counts; read when the dialog opens.
* **Check** — quick_check (or integrity_check, "Thorough") and
  foreign_key_check; for the companion also its db_uuid, registry and
  orphan rows. A damaged companion can be moved aside, never the database.
* **Optimize** — refresh the query planner's statistics.
* **Checkpoint** — write the WAL back and empty the -wal file.
* **Compact…** — VACUUM, only when it pays off, after a disk space check and
  optionally a backup set.

While the ``companion-db`` feature flag is on, a "Companion data" section
lists the companion's tables per owner (kind, rows, estimated size) and
offers delete rows, drop table, reassign a missing owner and clean up.
OpenSAK's own tables (custom fields) are listed but not changed here.

Every step runs in a QThread; the dialog shows a busy bar meanwhile. The
work itself is in the functions below the dialog (run_info(), run_check(),
…), which return plain results, so they can be tested without threads.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from opensak.db import companion, maintenance
from opensak.gui.dialogs.storage_locations_dialog import format_size
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.lang import tr
from opensak.utils import flags

logger = logging.getLogger(__name__)

# Compact offers a backup first, ticked from this database size on (#1048).
BACKUP_DEFAULT_BYTES = 100 * 1024 * 1024
# A companion larger than this, or than twice its database, gets a warning
# (plans/companion-database.md 2.7).
COMPANION_WARN_BYTES = 1024 ** 3
# Check messages listed per file; the rest is in the log.
_MAX_PROBLEMS = 20


# ── Results ───────────────────────────────────────────────────────────────────

@dataclass
class InfoResult:
    main: Optional[maintenance.FileInfo] = None
    main_error: Optional[str] = None
    companion: Optional[companion.CompanionInfo] = None
    companion_error: Optional[str] = None


@dataclass
class CheckOutcome:
    main: Optional[maintenance.CheckReport] = None
    main_error: Optional[str] = None
    companion: Optional[companion.CheckResult] = None
    companion_error: Optional[str] = None


@dataclass
class CompactOutcome:
    results: list[maintenance.VacuumResult] = field(default_factory=list)
    backup_path: Optional[Path] = None


# ── The work (worker thread) ──────────────────────────────────────────────────

Step = Callable[[str], None]


def _db_label() -> str:
    return tr("maint_db_group")


def _companion_label() -> str:
    return tr("companion_notice_title")


def _with_companion() -> bool:
    return bool(flags.companion_db)


def run_info(db_path: Path, step: Step) -> InfoResult:
    result = InfoResult()
    step(tr("maint_step_info", file=_db_label()))
    try:
        result.main = maintenance.file_info(db_path)
    except Exception as exc:
        logger.warning("maintenance: info of %s failed", db_path, exc_info=True)
        result.main_error = str(exc)
    if _with_companion():
        step(tr("maint_step_info", file=_companion_label()))
        try:
            result.companion = companion.info(db_path)
        except Exception as exc:
            logger.warning("maintenance: info of the companion of %s failed", db_path,
                           exc_info=True)
            result.companion_error = str(exc)
    return result


def run_check(db_path: Path, thorough: bool, step: Step) -> CheckOutcome:
    outcome = CheckOutcome()
    step(tr("maint_step_check", file=_db_label()))
    try:
        outcome.main = maintenance.check_file(db_path, thorough)
    except Exception as exc:
        logger.warning("maintenance: check of %s failed", db_path, exc_info=True)
        outcome.main_error = str(exc)
    if _with_companion() and companion.exists(db_path):
        step(tr("maint_step_check", file=_companion_label()))
        try:
            outcome.companion = companion.check(db_path, thorough)
        except Exception as exc:
            logger.warning("maintenance: check of the companion of %s failed", db_path,
                           exc_info=True)
            outcome.companion_error = str(exc)
    return outcome


def run_optimize(db_path: Path, step: Step) -> list[str]:
    lines = []
    step(tr("maint_step_optimize", file=_db_label()))
    lines.append(_optimize_line(maintenance.optimize_file(db_path), _db_label()))
    if _with_companion() and companion.exists(db_path):
        step(tr("maint_step_optimize", file=_companion_label()))
        lines.append(_optimize_line(companion.optimize(db_path), _companion_label()))
    return lines


def run_checkpoint(db_path: Path, step: Step) -> list[str]:
    lines = []
    files = [(_db_label(), Path(db_path), lambda: maintenance.checkpoint_file(db_path))]
    if _with_companion() and companion.exists(db_path):
        files.append((_companion_label(), companion.companion_path(db_path),
                      lambda: companion.checkpoint(db_path)))
    for label, path, run in files:
        step(tr("maint_step_checkpoint", file=label))
        wal = Path(f"{path}-wal")
        before = _size(wal)
        if run():
            lines.append(tr("maint_checkpoint_done", file=label,
                            before=format_size(before), after=format_size(_size(wal))))
        else:
            lines.append(tr("maint_checkpoint_busy", file=label))
    return lines


def compact_targets(info: InfoResult) -> list[tuple[str, Path, maintenance.PageStats]]:
    """The files Compact would rewrite — those where it pays off (the
    dialog's thresholds) — as (label, path, page stats)."""
    targets = []
    if info.main is not None and _worthwhile(info.main.stats):
        targets.append((_db_label(), info.main.path, info.main.stats))
    if info.companion is not None:
        stats = _companion_stats(info.companion)
        if _worthwhile(stats):
            targets.append((_companion_label(), info.companion.path, stats))
    return targets


def run_compact(db: Any, targets: list[tuple[str, Path, maintenance.PageStats]],
                backup: bool, step: Step) -> CompactOutcome:
    """Back up *db* (when *backup*), then VACUUM each target. The caller has
    released the database's connections. A failed backup raises
    BackupError, and then nothing is compacted."""
    outcome = CompactOutcome()
    if backup:
        from opensak.backup.backupset import KIND_MANUAL, write_backup_set

        step(tr("maint_step_backup"))
        outcome.backup_path = write_backup_set([db], KIND_MANUAL).backup_set.path
    for label, path, _stats in targets:
        step(tr("maint_step_compact", file=label))
        outcome.results.append(maintenance.vacuum_file(
            path,
            min_bytes=maintenance.MANUAL_MIN_RECLAIM_BYTES,
            min_fraction=maintenance.MANUAL_MIN_RECLAIM_FRACTION,
        ))
    # #1048: statistics again afterwards.
    step(tr("maint_step_optimize", file=_db_label()))
    maintenance.optimize_file(db.path)
    if _with_companion() and companion.exists(db.path):
        companion.optimize(db.path)
    return outcome


def _worthwhile(stats: maintenance.PageStats) -> bool:
    return maintenance.is_worthwhile(
        stats, maintenance.MANUAL_MIN_RECLAIM_BYTES, maintenance.MANUAL_MIN_RECLAIM_FRACTION)


def _companion_stats(info: companion.CompanionInfo) -> maintenance.PageStats:
    # The companion's info has bytes; the page size is irrelevant for the
    # thresholds and the space check, so count in 1-byte pages.
    return maintenance.PageStats(page_size=1, page_count=info.file_bytes,
                                 freelist_count=info.free_bytes)


def _optimize_line(ok: bool, label: str) -> str:
    return tr("maint_optimize_done" if ok else "maint_optimize_failed", file=label)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ── Result text ───────────────────────────────────────────────────────────────

def check_lines(outcome: CheckOutcome) -> list[str]:
    lines: list[str] = []
    db, comp = _db_label(), _companion_label()
    if outcome.main_error:
        lines.append(tr("maint_failed", file=db, error=outcome.main_error))
    elif outcome.main is not None:
        lines += _report_lines(db, outcome.main.problems, outcome.main.foreign_key_problems)
    if outcome.companion_error:
        lines.append(tr("maint_failed", file=comp, error=outcome.companion_error))
    elif outcome.companion is not None:
        c = outcome.companion
        lines += _report_lines(comp, c.problems, c.foreign_key_problems, c.ok)
        if c.uuid_mismatch:
            lines.append(tr("maint_check_uuid", file=comp))
        for table, count in sorted(c.orphans.items()):
            lines.append(tr("maint_check_orphans", file=comp, count=count, table=table))
        if c.missing:
            lines.append(tr("maint_check_missing", file=comp, tables=", ".join(c.missing)))
        if c.unregistered:
            lines.append(tr("maint_check_unregistered", file=comp,
                            tables=", ".join(c.unregistered)))
        if c.orphans or c.missing:
            lines.append(tr("maint_check_cleanup_hint"))
    return lines


def _report_lines(label: str, problems: tuple[str, ...], fk: int,
                  ok: Optional[bool] = None) -> list[str]:
    if ok is None:
        ok = not (problems or fk)
    if problems:
        lines = [tr("maint_check_problems", file=label, count=len(problems))]
        lines += [f"  {p}" for p in problems[:_MAX_PROBLEMS]]
        if len(problems) > _MAX_PROBLEMS:
            lines.append("  …")
        return lines
    if fk:
        return [tr("maint_check_fk", file=label, count=fk)]
    return [tr("maint_check_ok", file=label)] if ok else []


def compact_lines(outcome: CompactOutcome) -> list[str]:
    lines = []
    if outcome.backup_path is not None:
        lines.append(tr("maint_backup_done", path=str(outcome.backup_path)))
    before = after = 0
    for result in outcome.results:
        label = (_companion_label() if companion.is_companion_file(result.db_path)
                 else _db_label())
        before += result.size_before
        after += result.size_after
        if result.status == maintenance.DONE:
            lines.append(tr(
                "maint_compact_done", file=label,
                before=format_size(result.size_before), after=format_size(result.size_after),
                saved=format_size(max(result.size_before - result.size_after, 0)),
                seconds=f"{result.seconds:.1f}",
            ))
        elif result.status == maintenance.NOT_WORTHWHILE:
            lines.append(tr("maint_compact_skipped", file=label,
                            free=format_size(result.reclaimable)))
        elif result.status == maintenance.LOW_DISK_SPACE:
            lines.append(tr("maint_compact_low_space", file=label))
        else:
            lines.append(tr("maint_failed", file=label, error=result.error or result.status))
    if len(outcome.results) > 1:
        lines.append(tr("maint_compact_total", before=format_size(before),
                        after=format_size(after)))
    return lines


def owner_text(table: companion.TableInfo) -> str:
    if table.owned_by_opensak:
        return "OpenSAK"  # i18n: ignore — product name
    if table.owner is None:
        return tr("maint_owner_unregistered")
    name = table.owner_name or Path(table.owner).stem
    return tr("maint_owner_missing", name=name) if table.owner_missing else name


def kind_text(table: companion.TableInfo) -> str:
    if table.type == "view":
        return tr("maint_kind_view")
    return tr("maint_kind_cache") if table.kind == companion.KIND_CACHE else tr("maint_kind_free")


# ── Worker ────────────────────────────────────────────────────────────────────

class MaintenanceWorker(QThread):
    """Runs one maintenance job, a callable taking a step callback."""

    step = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, job: Callable[[Step], object], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._job = job

    def run(self) -> None:
        try:
            result = self._job(self.step.emit)
        except Exception as exc:
            logger.warning("maintenance job failed", exc_info=True)
            self.failed.emit(str(exc) or type(exc).__name__)
            return
        self.succeeded.emit(result)


# ── Dialog ────────────────────────────────────────────────────────────────────

class DatabaseMaintenanceDialog(QDialog):
    """Maintenance of the database *db* (an entry of the database list)."""

    def __init__(self, db: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._db = db
        self._info = InfoResult()
        self._worker: Optional[MaintenanceWorker] = None
        self.setWindowTitle(tr("maint_title", name=db.name))
        self.setMinimumSize(640, 480)
        self._setup_ui()
        clamp_dialog_height_to_screen(self, parent)
        self.refresh()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        db_group = QGroupBox(tr("maint_db_group"))
        form = QFormLayout(db_group)
        self._file_size = QLabel("—")
        self._wal_size = QLabel("—")
        self._free = QLabel("—")
        self._versions = QLabel("—")
        self._rows = QLabel("—")
        self._rows.setWordWrap(True)
        form.addRow(tr("maint_file_size"), self._file_size)
        form.addRow(tr("maint_wal_size"), self._wal_size)
        form.addRow(tr("maint_free"), self._free)
        form.addRow(tr("maint_versions"), self._versions)
        form.addRow(tr("maint_rows"), self._rows)
        layout.addWidget(db_group)

        self._companion_group: Optional[QGroupBox] = None
        if _with_companion():
            layout.addWidget(self._build_companion_group(), 1)

        actions = QHBoxLayout()
        self._btn_check = QPushButton(tr("maint_btn_check"))
        self._btn_check.clicked.connect(self._check)
        self._thorough = QCheckBox(tr("maint_thorough"))
        self._thorough.setToolTip(tr("maint_tip_thorough"))
        self._btn_optimize = QPushButton(tr("maint_btn_optimize"))
        self._btn_optimize.setToolTip(tr("maint_tip_optimize"))
        self._btn_optimize.clicked.connect(self._optimize)
        self._btn_checkpoint = QPushButton(tr("maint_btn_checkpoint"))
        self._btn_checkpoint.setToolTip(tr("maint_tip_checkpoint"))
        self._btn_checkpoint.clicked.connect(self._checkpoint)
        self._btn_compact = QPushButton(tr("maint_btn_compact"))
        self._btn_compact.setToolTip(tr("maint_tip_compact"))
        self._btn_compact.clicked.connect(self._compact)
        for w in (self._btn_check, self._thorough, self._btn_optimize,
                  self._btn_checkpoint, self._btn_compact):
            actions.addWidget(w)
        actions.addStretch()
        layout.addLayout(actions)

        busy_row = QHBoxLayout()
        self._busy = QProgressBar()
        self._busy.setRange(0, 0)
        self._busy.setMaximumWidth(160)
        self._busy.setVisible(False)
        self._step = QLabel("")
        busy_row.addWidget(self._busy)
        busy_row.addWidget(self._step, 1)
        layout.addLayout(busy_row)

        layout.addWidget(QLabel(f"<b>{tr('dist_result_group')}</b>"))
        self._results = QPlainTextEdit()
        self._results.setReadOnly(True)
        self._results.setMinimumHeight(90)
        layout.addWidget(self._results, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._action_buttons: list[QWidget] = [
            self._btn_check, self._thorough, self._btn_optimize,
            self._btn_checkpoint, self._btn_compact,
        ]

    def _build_companion_group(self) -> QGroupBox:
        group = QGroupBox(tr("maint_companion_group"))
        self._companion_group = group
        box = QVBoxLayout(group)
        self._companion_summary = QLabel("—")
        self._companion_summary.setWordWrap(True)
        self._companion_summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self._companion_summary)
        self._companion_warning = QLabel("")
        self._companion_warning.setWordWrap(True)
        self._companion_warning.setStyleSheet("color: #c62828;")
        self._companion_warning.setVisible(False)
        box.addWidget(self._companion_warning)

        self._tables = QTreeWidget()
        self._tables.setRootIsDecorated(False)
        self._tables.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tables.setHeaderLabels([
            tr("maint_col_table"), tr("maint_col_owner"), tr("maint_col_kind"),
            tr("maint_col_rows"), tr("maint_col_size"),
        ])
        header = self._tables.header()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._tables.itemSelectionChanged.connect(self._update_table_buttons)
        box.addWidget(self._tables, 1)

        row = QHBoxLayout()
        self._btn_clear = QPushButton(tr("maint_btn_clear"))
        self._btn_clear.clicked.connect(self._clear_table)
        self._btn_drop = QPushButton(tr("maint_btn_drop"))
        self._btn_drop.setStyleSheet("QPushButton:enabled { color: #c62828; }")
        self._btn_drop.clicked.connect(self._drop_table)
        self._btn_reassign = QPushButton(tr("maint_btn_reassign"))
        self._btn_reassign.clicked.connect(self._reassign_owner)
        self._btn_cleanup = QPushButton(tr("maint_btn_cleanup"))
        self._btn_cleanup.setToolTip(tr("maint_tip_cleanup"))
        self._btn_cleanup.clicked.connect(self._cleanup)
        for b in (self._btn_clear, self._btn_drop, self._btn_reassign):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(self._btn_cleanup)
        box.addLayout(row)
        return group

    # ── Info ──────────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Read Info again (also after every step that changes something)."""
        self._run(lambda step: run_info(self._db.path, step), self._show_info)

    def _show_info(self, info: InfoResult) -> None:
        self._info = info
        main = info.main
        if main is None:
            for label in (self._file_size, self._wal_size, self._free, self._versions, self._rows):
                label.setText("—")
            if info.main_error:
                self._append([tr("maint_failed", file=_db_label(), error=info.main_error)])
        else:
            self._file_size.setText(format_size(main.file_bytes))
            self._wal_size.setText(format_size(main.wal_bytes))
            self._free.setText(tr("maint_free_value", size=format_size(main.stats.free_bytes),
                                  percent=f"{main.free_fraction * 100:.0f}"))
            self._versions.setText(tr("maint_versions_value", schema=main.schema_version,
                                      sqlite=main.sqlite_version))
            self._rows.setText(tr("maint_rows_value", **{
                name: f"{main.rows.get(name, 0):,}" for name in maintenance.COUNTED_TABLES}))
        if self._companion_group is not None:
            self._show_companion(info)
        self._set_busy(False)

    def _show_companion(self, info: InfoResult) -> None:
        self._tables.clear()
        comp = info.companion
        self._companion_warning.setVisible(False)
        if comp is None:
            self._companion_summary.setText(
                tr("maint_failed", file=_companion_label(), error=info.companion_error)
                if info.companion_error else tr("maint_companion_none"))
            self._update_table_buttons()
            return
        self._companion_summary.setText(tr(
            "maint_companion_summary", path=str(comp.path), size=format_size(comp.file_bytes),
            wal=format_size(comp.wal_bytes), free=format_size(comp.free_bytes),
            schema=comp.schema_version, tables=len(comp.tables),
        ))
        main_bytes = info.main.file_bytes if info.main is not None else 0
        if comp.file_bytes > COMPANION_WARN_BYTES or (main_bytes and comp.file_bytes > 2 * main_bytes):
            self._companion_warning.setText(
                tr("maint_companion_large", size=format_size(comp.file_bytes)))
            self._companion_warning.setVisible(True)
        for table in comp.tables:
            item = QTreeWidgetItem([
                table.name, owner_text(table), kind_text(table),
                f"{table.rows:,}" if table.type == "table" else "",
                f"≈ {format_size(table.est_bytes)}" if table.type == "table" else "",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, table)
            item.setTextAlignment(3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setTextAlignment(4, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if table.owner and not table.owned_by_opensak:
                item.setToolTip(1, table.owner)
            if table.description:
                item.setToolTip(0, table.description)
            if table.owned_by_opensak:
                item.setToolTip(2, tr("maint_tip_opensak_table"))
            self._tables.addTopLevelItem(item)
        self._update_table_buttons()

    def selected_table(self) -> Optional[companion.TableInfo]:
        if self._companion_group is None:
            return None
        items = self._tables.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None

    def _update_table_buttons(self) -> None:
        if self._companion_group is None:
            return
        busy = self.is_running()
        table = self.selected_table()
        changeable = table is not None and not table.owned_by_opensak and not busy
        self._btn_clear.setEnabled(changeable and table is not None and table.type == "table")
        self._btn_drop.setEnabled(changeable)
        self._btn_reassign.setEnabled(
            changeable and table is not None and (table.owner is None or table.owner_missing))
        self._btn_cleanup.setEnabled(not busy and self._info.companion is not None)
        tip = tr("maint_tip_opensak_table") if table is not None and table.owned_by_opensak else ""
        for b in (self._btn_clear, self._btn_drop, self._btn_reassign):
            b.setToolTip(tip)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _check(self) -> None:
        thorough = self._thorough.isChecked()
        self._run(lambda step: run_check(self._db.path, thorough, step), self._on_checked)

    def _on_checked(self, outcome: CheckOutcome) -> None:
        self._set_busy(False)
        self._append(check_lines(outcome))
        if outcome.main is not None and outcome.main.problems:
            QMessageBox.warning(self, tr("maint_btn_check"), tr("maint_check_main_failed"))
        elif outcome.companion is not None and outcome.companion.problems:
            self._offer_move_aside(outcome.companion.problems[0])
        if outcome.companion is not None and (outcome.companion.orphans
                                              or outcome.companion.missing):
            self.refresh()

    def _offer_move_aside(self, reason: str) -> None:
        reply = QMessageBox.warning(
            self, tr("maint_btn_check"), tr("maint_check_companion_damaged"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from opensak.db.database import release_connections

        release_connections(self._db.path)
        if companion.move_aside(self._db.path, reason):
            companion.take_notices()   # told here; the main window needn't repeat it
            self._append([tr("maint_moved_aside", file=_companion_label())])
        else:
            self._append([tr("maint_move_aside_failed", file=_companion_label())])
        self.refresh()

    def _optimize(self) -> None:
        self._run(lambda step: run_optimize(self._db.path, step), self._lines_then_refresh)

    def _checkpoint(self) -> None:
        self._run(lambda step: run_checkpoint(self._db.path, step), self._lines_then_refresh)

    def _compact(self) -> None:
        info = self._info
        if info.main is None:
            return
        targets = compact_targets(info)
        if not targets:
            free = info.main.stats.free_bytes + (info.companion.free_bytes if info.companion else 0)
            QMessageBox.information(self, tr("maint_compact_confirm_title"), tr(
                "maint_compact_nothing", free=format_size(free),
                percent=f"{info.main.free_fraction * 100:.0f}"))
            return
        # Room for the larger file: they are compacted one after the other.
        label, path, stats = max(targets, key=lambda t: t[2].used_bytes)
        space = maintenance.check_free_space(path, stats)
        if not space.ok:
            folder, needed, free_disk = space.shortages[0]
            QMessageBox.warning(self, tr("maint_compact_confirm_title"), tr(
                "maint_compact_no_space", file=label, needed=format_size(needed),
                folder=str(folder), free=format_size(free_disk)))
            return
        backup = self._confirm_compact(sum(t[2].free_bytes for t in targets), space.needed)
        if backup is None:
            return
        from opensak.db.database import release_connections

        release_connections(self._db.path)
        self._run(lambda step: run_compact(self._db, targets, backup, step), self._on_compacted,
                  failed=self._on_compact_failed)

    def _confirm_compact(self, gain: int, needed: int) -> Optional[bool]:
        """Ask before Compact; None = cancelled, else whether to back up first."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("maint_compact_confirm_title"))
        box.setText(tr("maint_compact_confirm", gain=format_size(gain),
                       needed=format_size(needed)))
        check = QCheckBox(tr("maint_compact_backup"))
        check.setChecked(self._info.main is not None
                         and self._info.main.file_bytes >= BACKUP_DEFAULT_BYTES)
        box.setCheckBox(check)
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not box.button(QMessageBox.StandardButton.Ok):
            return None
        return check.isChecked()

    def _on_compacted(self, outcome: CompactOutcome) -> None:
        if outcome.backup_path is not None:
            try:
                from opensak.backup.exit_state import record_backup_state
                record_backup_state([self._db])
            except Exception:
                logger.warning("maintenance: could not record the backup state", exc_info=True)
        self._lines_then_refresh(compact_lines(outcome))

    def _on_compact_failed(self, error: str) -> None:
        self._set_busy(False)
        self._append([tr("maint_backup_failed", error=error)])
        self.refresh()

    # ── Companion data ────────────────────────────────────────────────────────

    def _clear_table(self) -> None:
        table = self.selected_table()
        if table is None or not self._confirm(tr(
                "maint_clear_confirm", rows=f"{table.rows:,}", table=table.name,
                owner=owner_text(table))):
            return
        name = table.name

        def job(_step: Step) -> list[str]:
            rows = companion.clear_table(self._db.path, name)
            return [tr("maint_cleared", table=name, rows=f"{rows:,}")]
        self._run(job, self._lines_then_refresh)

    def _drop_table(self) -> None:
        table = self.selected_table()
        if table is None or not self._confirm(tr(
                "maint_drop_confirm", rows=f"{table.rows:,}", table=table.name,
                owner=owner_text(table))):
            return
        name = table.name

        def job(_step: Step) -> list[str]:
            companion.drop_table(self._db.path, name)
            return [tr("maint_dropped", table=name)]
        self._run(job, self._lines_then_refresh)

    def _reassign_owner(self) -> None:
        table = self.selected_table()
        if table is None:
            return
        dlg = ReassignOwnerDialog(table.name, self)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.macro_path is None:
            return
        from opensak.macro.db_access import macro_key
        from opensak.macro.metadata import read_metadata

        path = dlg.macro_path
        owner_name = read_metadata(path).name or path.stem
        name = table.name

        def job(_step: Step) -> list[str]:
            companion.reassign_owner(self._db.path, name, macro_key(path), owner_name)
            return [tr("maint_reassigned", table=name, owner=owner_name)]
        self._run(job, self._lines_then_refresh)

    def _cleanup(self) -> None:
        def job(_step: Step) -> list[str]:
            result = companion.cleanup(self._db.path)
            if not result.total:
                return [tr("maint_cleanup_nothing", file=_companion_label())]
            return [tr("maint_cleanup_done", file=_companion_label(),
                       orphans=sum(result.orphans.values()),
                       registry=len(result.registry_rows))]
        self._run(job, self._lines_then_refresh)

    def _confirm(self, text: str) -> bool:
        return QMessageBox.warning(
            self, tr("maint_companion_group"), text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    # ── Running jobs ──────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _run(self, job: Callable[[Step], object], done: Callable[[Any], None],
             failed: Optional[Callable[[str], None]] = None) -> None:
        if self.is_running():
            return
        self._set_busy(True)
        worker = MaintenanceWorker(job, self)
        worker.step.connect(self._step.setText)
        worker.succeeded.connect(done)
        worker.failed.connect(failed or self._on_failed)
        self._worker = worker
        worker.start()

    def _on_failed(self, error: str) -> None:
        self._set_busy(False)
        self._append([tr("maint_failed", file=self._db.name, error=error)])
        self.refresh()

    def _lines_then_refresh(self, lines: list[str]) -> None:
        self._set_busy(False)
        self._append(lines)
        self.refresh()

    def _set_busy(self, busy: bool) -> None:
        self._busy.setVisible(busy)
        if not busy:
            self._step.setText("")
        for w in self._action_buttons:
            w.setEnabled(not busy)
        if busy and self._companion_group is not None:
            for b in (self._btn_clear, self._btn_drop, self._btn_reassign, self._btn_cleanup):
                b.setEnabled(False)
        elif not busy:
            self._update_table_buttons()

    def _append(self, lines: list[str]) -> None:
        if not lines:
            return
        stamp = time.strftime("%H:%M:%S")
        self._results.appendPlainText("\n".join(f"{stamp}  {line}" for line in lines))

    def reject(self) -> None:
        # A running step (VACUUM in particular) can't be interrupted safely.
        if self.is_running():
            return
        super().reject()

    def wait_idle(self, msecs: int = 60000) -> None:
        """Block until the running step is done (for tests and closing)."""
        if self._worker is not None:
            self._worker.wait(msecs)


class ReassignOwnerDialog(QDialog):
    """Pick the macro that becomes the owner of a companion table: one of
    the macros in the macro folder, or any .lua file."""

    def __init__(self, table: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        from opensak.config import get_macros_dir
        from opensak.macro.metadata import discover_macros

        self.setWindowTitle(tr("maint_reassign_title"))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("maint_reassign_label", table=table)))
        row = QHBoxLayout()
        self._combo = QComboBox()
        for macro in discover_macros(get_macros_dir()):
            self._combo.addItem(f"{macro.name or macro.path.stem}  ({macro.path.name})",
                                str(macro.path))
        row.addWidget(self._combo, 1)
        browse = QPushButton(tr("gps_browse"))
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        layout.addWidget(buttons)
        self._combo.currentIndexChanged.connect(self._update_ok)
        self._update_ok()

    @property
    def macro_path(self) -> Optional[Path]:
        data = self._combo.currentData()
        return Path(data) if data else None

    def _browse(self) -> None:
        from opensak.config import get_macros_dir

        path, _ = QFileDialog.getOpenFileName(
            self, tr("maint_reassign_title"), str(get_macros_dir()), tr("maint_reassign_filter"))
        if not path:
            return
        index = self._combo.findData(str(Path(path)))
        if index < 0:
            self._combo.addItem(Path(path).name, str(Path(path)))
            index = self._combo.count() - 1
        self._combo.setCurrentIndex(index)

    def _update_ok(self) -> None:
        if self._ok is not None:
            self._ok.setEnabled(self.macro_path is not None)

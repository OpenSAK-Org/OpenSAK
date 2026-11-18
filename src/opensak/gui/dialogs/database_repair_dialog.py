"""
src/opensak/gui/dialogs/database_repair_dialog.py — Repair… in the
Maintenance dialog (#1051): check the data of a database and fix
what can be fixed safely, like GSAK's Repair/Defrag.

1. Scan (read only, in the background): every check of db/repair.py with
   its number of problems and a few example GC codes.
2. The user ticks what to fix. Report-only checks can't be ticked;
   "Dates that can't be read" starts unticked, since clearing loses the
   value. "Make a backup first" is ticked.
3. Repair: backup set, the fixes in one transaction, Optimize, and Compact
   when more than a quarter of the file is free (as GSAK does). Then a
   summary, and the scan runs again.

Every problem row shows a few affected caches with the field and its stored
value. "Copy list" (also Ctrl+C and the context menu) copies them for the
selected rows, or all rows, as tab-separated text, so report-only problems
can be found and fixed by hand.

A damaged file (quick_check fails) can't be repaired by SQLite; the dialog
points to File → Restore from backup… instead.

Works on the active database (its connections are released first, as for
Compact) and on any other database in the list. The work is in run_scan()
and run_repair(), which return plain results, so they can be tested
without threads.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QMenu, QPlainTextEdit, QProgressBar, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from opensak.db import companion, maintenance, repair
from opensak.gui.dialogs.database_maintenance_dialog import (
    CompactOutcome, MaintenanceWorker, Step, compact_lines,
)
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.lang import tr
from opensak.utils import flags

logger = logging.getLogger(__name__)

# Compact after a repair when at least this share of the file is free (GSAK).
COMPACT_FRACTION = 0.25
# The finding key of a row in the tree.
KEY_ROLE = Qt.ItemDataRole.UserRole
# Damage messages listed; the rest is in the log.
_MAX_DAMAGE_LINES = 20
# Affected rows in an examples cell's tooltip.
_TOOLTIP_DETAILS = 20

# Each check's name, as a translation key.
_CHECK_TEXT = {
    repair.ORPHANS: "repair_chk_orphans",
    repair.PARENT_LINK: "repair_chk_parent_link",
    repair.WAYPOINT_PARENT: "repair_chk_waypoint_parent",
    repair.CORRECTED_FLAG: "repair_chk_corrected_flag",
    repair.COMPANION_ORPHANS: "repair_chk_companion_orphans",
    repair.COORDS: "repair_chk_coords",
    repair.CORRECTED_COORDS: "repair_chk_corrected_coords",
    repair.RATINGS: "repair_chk_ratings",
    repair.IDENTITY: "repair_chk_identity",
    repair.DATE_FORMAT: "repair_chk_date_format",
    repair.DATE_UNREADABLE: "repair_chk_date_unreadable",
    repair.DATE_RANGE: "repair_chk_date_range",
    repair.DATE_PLACEHOLDER: "repair_chk_date_placeholder",
    repair.TOTALS: "repair_chk_totals",
    repair.FOUND_MARK: "repair_chk_found_mark",
}
# The fix each fixable check makes, as a translation key.
_FIX_TEXT = {
    repair.ORPHANS: "repair_fix_delete",
    repair.WAYPOINT_PARENT: "repair_fix_set_parent",
    repair.CORRECTED_FLAG: "repair_fix_clear_flag",
    repair.COMPANION_ORPHANS: "repair_fix_delete",
    repair.DATE_FORMAT: "repair_fix_convert",
    repair.DATE_UNREADABLE: "repair_fix_clear",
    repair.DATE_PLACEHOLDER: "repair_fix_clear",
    repair.TOTALS: "repair_fix_recalculate",
    repair.FOUND_MARK: "repair_fix_mark_found",
}
# Fixable checks that are not ticked after a scan: their fix loses data (an
# ambiguous date like 03/05/2024 becomes empty), so the user opts in after
# looking at the list.
_UNTICKED = frozenset({repair.DATE_UNREADABLE})


@dataclass
class RepairOutcome:
    result: repair.RepairResult
    backup_path: Optional[Path] = None
    compact: Optional[maintenance.VacuumResult] = None


# ── The work (worker thread) ──────────────────────────────────────────────────

def _with_companion(db_path: Path) -> bool:
    return bool(flags.companion_db) and companion.exists(db_path)


def _account() -> repair.Account:
    """The account in Settings, for the checks of the user's own logs."""
    from opensak.gui.settings import get_settings

    settings = get_settings()
    return settings.gc_finder_id, settings.gc_username


def run_scan(db_path: Path, step: Step) -> repair.ScanResult:
    step(tr("repair_step_scan"))
    return repair.scan(db_path, with_companion=_with_companion(db_path), account=_account())


def run_repair(db: Any, keys: set[str], backup: bool, step: Step) -> RepairOutcome:
    """Back up *db* (when *backup*), fix *keys*, then Optimize and Compact
    when it pays off. The caller has released the database's connections.
    A failed backup raises BackupError, and then nothing is changed."""
    backup_path = None
    if backup:
        from opensak.backup.backupset import KIND_MANUAL, write_backup_set

        step(tr("maint_step_backup"))
        backup_path = write_backup_set([db], KIND_MANUAL).backup_set.path
    step(tr("repair_step_fix"))
    outcome = RepairOutcome(repair.repair(db.path, keys, account=_account()), backup_path)
    step(tr("maint_step_optimize", file=tr("maint_db_group")))
    maintenance.optimize_file(db.path)
    stats = maintenance.file_info(db.path).stats
    if maintenance.is_worthwhile(stats, maintenance.MANUAL_MIN_RECLAIM_BYTES, COMPACT_FRACTION):
        step(tr("maint_step_compact", file=tr("maint_db_group")))
        # vacuum() checks the free disk space itself.
        outcome.compact = maintenance.vacuum_file(
            db.path, min_bytes=maintenance.MANUAL_MIN_RECLAIM_BYTES,
            min_fraction=COMPACT_FRACTION)
        if outcome.compact.ran:
            maintenance.optimize_file(db.path)
    return outcome


# ── Text ──────────────────────────────────────────────────────────────────────

def check_label(finding: repair.Finding) -> str:
    key = _CHECK_TEXT[finding.check]
    return tr(key, table=finding.table) if finding.table else tr(key)


def _label_for_key(key: str) -> str:
    check, _, table = key.partition(":")
    return check_label(repair.Finding(check, 0, True, table=table or None))


def repair_lines(outcome: RepairOutcome) -> list[str]:
    lines = []
    if outcome.backup_path is not None:
        lines.append(tr("maint_backup_done", path=str(outcome.backup_path)))
    if not outcome.result.fixed:
        lines.append(tr("repair_nothing_fixed"))
    for key, count in outcome.result.fixed.items():
        lines.append(tr("repair_fixed", count=f"{count:,}", check=_label_for_key(key)))
    if outcome.compact is not None:
        lines += compact_lines(CompactOutcome(results=[outcome.compact]))
    return lines


def copy_text(findings: list[repair.Finding]) -> str:
    """The affected rows of *findings* as tab-separated text with a header:
    check, GC code, field, value. A finding without details (orphans, whose
    cache is gone) gets one line with its count; one with more rows than
    kept says how many are not listed."""
    lines = ["\t".join([tr("repair_col_check"), tr("col_gc_code"), tr("repair_col_field"),
                        tr("repair_col_value")])]
    for finding in findings:
        label = check_label(finding)
        if not finding.details:
            lines.append("\t".join([label, "", "", tr("repair_copy_no_code",
                                                      count=f"{finding.count:,}")]))
            continue
        for d in finding.details:
            lines.append("\t".join([label, d.gc_code, d.field, d.value]))
        if finding.count > len(finding.details):
            lines.append("\t".join([label, "", "", tr(
                "repair_copy_more", count=f"{finding.count - len(finding.details):,}")]))
    return "\n".join(lines) + "\n"


# ── Dialog ────────────────────────────────────────────────────────────────────

class DatabaseRepairDialog(QDialog):
    """Repair of the database *db* (an entry of the database list)."""

    def __init__(self, db: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._db = db
        self.scan_result: Optional[repair.ScanResult] = None
        self._worker: Optional[MaintenanceWorker] = None
        self.setWindowTitle(tr("repair_title", name=db.name))
        self.setMinimumSize(720, 460)
        self._setup_ui()
        clamp_dialog_height_to_screen(self, parent)
        self._scan()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(tr("repair_intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self._tree = QTreeWidget()
        self._tree.setRootIsDecorated(False)
        self._tree.setHeaderLabels([
            tr("repair_col_check"), tr("repair_col_count"), tr("repair_col_fix"),
            tr("repair_col_examples"),
        ])
        header = self._tree.header()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for col in (1, 2, 3):
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.itemChanged.connect(lambda *_: self._update_buttons())
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._tree_menu)
        copy = QShortcut(QKeySequence(QKeySequence.StandardKey.Copy), self._tree)
        copy.setContext(Qt.ShortcutContext.WidgetShortcut)
        copy.activated.connect(self.copy_list)
        layout.addWidget(self._tree, 2)

        row = QHBoxLayout()
        self._backup = QCheckBox(tr("maint_compact_backup"))
        self._backup.setChecked(True)
        self._btn_copy = QPushButton(tr("repair_btn_copy"))
        self._btn_copy.setToolTip(tr("repair_tip_copy"))
        self._btn_copy.clicked.connect(self.copy_list)
        self._btn_rescan = QPushButton(tr("repair_btn_rescan"))
        self._btn_rescan.clicked.connect(self._scan)
        self._btn_repair = QPushButton(tr("repair_btn_repair"))
        self._btn_repair.clicked.connect(self._repair)
        row.addWidget(self._backup)
        row.addStretch()
        row.addWidget(self._btn_copy)
        row.addWidget(self._btn_rescan)
        row.addWidget(self._btn_repair)
        layout.addLayout(row)

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
        self._results.setMinimumHeight(80)
        layout.addWidget(self._results, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ── Scan ──────────────────────────────────────────────────────────────────

    def _scan(self) -> None:
        self.scan_result = None
        self._run(lambda step: run_scan(self._db.path, step), self._show_scan)

    def _show_scan(self, result: repair.ScanResult) -> None:
        self._tree.blockSignals(True)
        self._tree.clear()
        for finding in result.problems:
            item = QTreeWidgetItem([
                check_label(finding), f"{finding.count:,}",
                tr(_FIX_TEXT[finding.check]) if finding.fixable else tr("repair_report_only"),
                "; ".join(finding.examples) + (" …" if finding.count > len(finding.examples)
                                               and finding.examples else ""),
            ])
            item.setData(0, KEY_ROLE, finding.key)
            if finding.details:
                tip = [d.text for d in finding.details[:_TOOLTIP_DETAILS]]
                if finding.count > len(tip):
                    tip.append(tr("repair_copy_more", count=f"{finding.count - len(tip):,}"))
                item.setToolTip(3, "\n".join(tip + ["", tr("repair_tip_copy")]))
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if finding.fixable:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Unchecked if finding.check in _UNTICKED
                                   else Qt.CheckState.Checked)
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self._tree.addTopLevelItem(item)
        self._tree.blockSignals(False)

        if result.damaged:
            self._summary.setText(f"{tr('repair_damaged')} {tr('maint_check_main_failed')}")
            self._append(list(result.damaged[:_MAX_DAMAGE_LINES]))
        elif not result.problems:
            self._summary.setText(tr("repair_scan_clean"))
        else:
            self._summary.setText(tr("repair_scan_summary", problems=len(result.problems),
                                     fixable=len(result.fixable)))
        self.scan_result = result
        self._set_busy(False)

    def checked_keys(self) -> set[str]:
        keys = set()
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            if (item is not None and item.flags() & Qt.ItemFlag.ItemIsUserCheckable
                    and item.checkState(0) == Qt.CheckState.Checked):
                keys.add(str(item.data(0, KEY_ROLE)))
        return keys

    # ── Copy ──────────────────────────────────────────────────────────────────

    def _findings_to_copy(self) -> list[repair.Finding]:
        """The findings of the selected rows, or all problems."""
        if self.scan_result is None:
            return []
        problems = list(self.scan_result.problems)
        selected = {str(item.data(0, KEY_ROLE)) for item in self._tree.selectedItems()}
        return [f for f in problems if f.key in selected] if selected else problems

    def copy_list(self) -> None:
        findings = self._findings_to_copy()
        if not findings:
            return
        text = copy_text(findings)
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self._append([tr("repair_copied", count=f"{text.count(chr(10)) - 1:,}")])

    def _tree_menu(self, pos: QPoint) -> None:
        if self.scan_result is None or not self.scan_result.problems:
            return
        menu = QMenu(self)
        menu.addAction(tr("repair_btn_copy"), self.copy_list)
        viewport = self._tree.viewport()
        menu.exec(viewport.mapToGlobal(pos) if viewport is not None else pos)

    # ── Repair ────────────────────────────────────────────────────────────────

    def _repair(self) -> None:
        keys = self.checked_keys()
        if not keys or self.is_running():
            return
        backup = self._backup.isChecked()
        from opensak.db.database import release_connections

        release_connections(self._db.path)
        self._run(lambda step: run_repair(self._db, keys, backup, step), self._on_repaired,
                  failed=self._on_repair_failed)

    def _on_repaired(self, outcome: RepairOutcome) -> None:
        if outcome.backup_path is not None:
            try:
                from opensak.backup.exit_state import record_backup_state
                record_backup_state([self._db])
            except Exception:
                logger.warning("repair: could not record the backup state", exc_info=True)
        self._set_busy(False)
        self._append(repair_lines(outcome))
        self._rescan_when_done()

    def _on_repair_failed(self, error: str) -> None:
        self._set_busy(False)
        self._append([tr("repair_failed", error=error)])
        self._rescan_when_done()

    def _rescan_when_done(self) -> None:
        # Called from the worker's last signal: its thread is about to end,
        # and _run() starts nothing while it still runs.
        self.wait_idle()
        self._scan()

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
        self._summary.setText("")
        self.scan_result = repair.ScanResult(Path(self._db.path))

    def _set_busy(self, busy: bool) -> None:
        self._busy.setVisible(busy)
        if not busy:
            self._step.setText("")
        self._tree.setEnabled(not busy)
        self._backup.setEnabled(not busy)
        self._btn_rescan.setEnabled(not busy)
        self._btn_copy.setEnabled(not busy and self.scan_result is not None
                                  and bool(self.scan_result.problems))
        self._btn_repair.setEnabled(not busy and bool(self.checked_keys()))

    def _update_buttons(self) -> None:
        self._btn_repair.setEnabled(not self.is_running() and bool(self.checked_keys()))

    def _append(self, lines: list[str]) -> None:
        if not lines:
            return
        stamp = time.strftime("%H:%M:%S")
        self._results.appendPlainText("\n".join(f"{stamp}  {line}" for line in lines))

    def reject(self) -> None:
        # A running repair or VACUUM can't be interrupted safely.
        if self.is_running():
            return
        super().reject()

    def wait_idle(self, msecs: int = 60000) -> None:
        """Block until the running step is done (for tests and closing)."""
        if self._worker is not None:
            self._worker.wait(msecs)

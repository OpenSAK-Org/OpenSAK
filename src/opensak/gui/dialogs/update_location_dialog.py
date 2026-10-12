"""
src/opensak/gui/dialogs/update_location_dialog.py — Update cache locations dialog.

Offline reverse geocoding via the boundary polygon engine (TerritoryResolver).
Fills country / state / county and writes provenance columns for every resolved cache.

Always opened as the same dialog; entry point controls the default scope:
  - Via menu (gc_codes=None): "Only caches with missing location data" is default.
  - Via right-click (gc_codes list): "Only this cache" is default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QTextEdit,
    QGroupBox, QRadioButton, QCheckBox,
)

from opensak.lang import tr
from opensak.logger import get_logger
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen

log = get_logger("gui.update_location_dialog")


# ── Data types ────────────────────────────────────────────────────────────────

class _CacheRow:
    __slots__ = ("gc_code", "lat", "lon", "basis")

    def __init__(self, gc_code: str, lat: float, lon: float, basis: str = "posted") -> None:
        self.gc_code = gc_code
        self.lat = lat
        self.lon = lon
        self.basis = basis


@dataclass
class UpdateLocationResult:
    updated: int = 0
    skipped: int = 0
    errors:  int = 0
    error_msgs: list[str] = field(default_factory=list)


# ── Worker ────────────────────────────────────────────────────────────────────

class ReverseGeocodeWorker(QThread):
    """Offline geocoding via the boundary polygon engine.

    Resolves country / state / county for each row and writes all four
    provenance columns (location_source, location_basis, location_updated,
    location_dataset).  No network, no rate limit.

    Two-phase execution:
      1. Parallel resolve — ThreadPoolExecutor, one BoundaryStore per thread.
      2. Bulk write — single IN query loads all caches; one transaction commits all.
    """

    row_done  = Signal(str, str)  # (gc_code, log_line)
    all_done  = Signal(object)    # UpdateLocationResult
    cancelled = Signal(object)    # UpdateLocationResult

    def __init__(self, rows: list[_CacheRow], *, parent=None):
        super().__init__(parent)
        self._rows = rows
        self._cancel = False

    def request_cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        import os
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from opensak.geo.store import BoundaryStore, ensure_baseline_seeded
        from opensak.geo.boundaries import TerritoryResolver
        from opensak.db.database import get_session
        from opensak.db.models import Cache

        result = UpdateLocationResult()

        if self._cancel:
            self.cancelled.emit(result)
            return

        # First-run baseline seed (copy from the PyInstaller bundle, or
        # download from OpenSAK-Data if nothing's bundled) happens here rather
        # than at app startup — this already runs on a QThread, per this
        # feature's own "long work never blocks the main thread" rule, and
        # the fallback download path can take a while over the network.
        ensure_baseline_seeded()

        check = BoundaryStore()
        if not check.available():
            result.errors += 1
            self.row_done.emit("", tr("update_loc_no_boundaries"))
            self.all_done.emit(result)
            return

        dataset = check.dataset_version()
        check.close()
        now = datetime.now(timezone.utc)

        # Phase 0 — pre-fetch phase (#722): collect every distinct county
        # pack this batch will need (via the same R-Tree bbox candidates the
        # resolver itself uses) and download them all in parallel, with real
        # progress, before the per-row resolve starts. Without this, a
        # county pack missing locally but needed by many caches in the
        # batch would otherwise retry a slow on-demand fetch (up to
        # packs.DOWNLOAD_TIMEOUT seconds) inside _resolve_layer for every
        # single cache that needs it — the original #722 hang. This is a
        # best-effort optimization: any failure here just falls back to the
        # existing on-demand fetch in store.py, so it's wrapped defensively
        # rather than aborting the whole geocode run over it.
        try:
            from opensak.geo import packs as _packs_mod

            probe = BoundaryStore()
            needed_packs: set[str] = set()
            for row in self._rows:
                if self._cancel:
                    break
                for region_id in probe.candidates("county", row.lat, row.lon):
                    region = probe.region("county", region_id)
                    if region is not None:
                        needed_packs.add(region.pack)
            counties_dir = probe.data_dir / "counties"
            probe.close()

            if needed_packs and not self._cancel:
                def _prefetch_progress(done: int, total: int) -> None:
                    if total:
                        self.row_done.emit("", tr(
                            "update_loc_prefetch_progress", done=done, total=total
                        ))

                _packs_mod.fetch_packs(
                    sorted(needed_packs), counties_dir, progress_cb=_prefetch_progress
                )
        except Exception as exc:
            log.debug("county pre-fetch phase failed, falling back to on-demand fetch: %s", exc)

        if self._cancel:
            self.cancelled.emit(result)
            return

        # Phase 1 — parallel resolve.
        # _shared_packs is injected into every thread's BoundaryStore so GeoJSON
        # packs are loaded from disk at most once across all threads. Each thread
        # still gets its own SQLite connection (not thread-safe to share). Dict
        # writes under the GIL are atomic; the only race is two threads loading
        # the same missing pack simultaneously — harmless, second write wins.
        import threading
        _shared_packs: dict = {}
        _tls = threading.local()

        def _resolve_one(row: _CacheRow):
            if self._cancel:
                return None
            if not hasattr(_tls, "resolver"):
                s = BoundaryStore()
                s._packs = _shared_packs
                _tls.store = s
                _tls.resolver = TerritoryResolver(s)
            loc = _tls.resolver.resolve(row.lat, row.lon)
            return row, loc

        workers = min(4, os.cpu_count() or 1)
        resolved = []
        cancelled = False

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_resolve_one, row): row for row in self._rows}
            for future in as_completed(futures):
                if self._cancel:
                    cancelled = True
                    break
                pair = future.result()
                if pair is None:
                    continue
                row, loc = pair
                self.row_done.emit(row.gc_code, tr(
                    "update_loc_row",
                    gc_code=row.gc_code,
                    country=loc.country or "–",
                    state=loc.state or "–",
                    county=loc.county or "–",
                ))
                resolved.append((row, loc))

        if cancelled or self._cancel:
            self.cancelled.emit(result)
            return

        # Phase 2 — bulk write in one transaction, no per-row SELECT. The IN query
        # is chunked to stay under SQLite's bound-parameter limit (large DBs).
        resolved_map = {row.gc_code: (row, loc) for row, loc in resolved}
        gc_codes = list(resolved_map)
        try:
            with get_session() as session:
                for i in range(0, len(gc_codes), 500):
                    chunk = gc_codes[i:i + 500]
                    caches = (
                        session.query(Cache)
                        .filter(Cache.gc_code.in_(chunk))
                        .all()
                    )
                    for cache in caches:
                        row, loc = resolved_map[cache.gc_code]
                        cache.country          = loc.country
                        cache.state            = loc.state
                        cache.county           = loc.county
                        cache.location_source  = "boundary"
                        cache.location_basis   = row.basis
                        cache.location_updated = now
                        cache.location_dataset = dataset
                        result.updated += 1
        except Exception as exc:
            result.errors += 1
            self.row_done.emit("", tr("update_loc_row_error", gc_code="batch", msg=str(exc)))

        if self._cancel:
            self.cancelled.emit(result)
            return

        self.all_done.emit(result)


# ── Dialog ────────────────────────────────────────────────────────────────────

class UpdateLocationDialog(QDialog):
    """Dialog to update cache locations via offline reverse geocoding.

    The same dialog is used from the menu and from the right-click context menu.
    When gc_codes is None (menu): "Only caches with missing location data" is default.
    When gc_codes is a list (right-click): "Only this cache" is default.
    """

    location_updated = Signal()

    def __init__(self, parent=None, *, gc_codes: list[str] | None = None):
        super().__init__(parent)
        self._gc_codes = gc_codes
        self._from_context_menu = gc_codes is not None
        self.setWindowTitle(tr("update_loc_title"))
        self.setMinimumWidth(460)
        self.setMinimumHeight(420)
        # Issue #815 (follow-up to #811): defensive cap, consistent with the
        # other dialogs audited for the same certification finding. The log
        # is a QTextEdit (own internal scrollbar) and has no stretch factor
        # forcing it taller than its content, so it simply shrinks to fit
        # the cap — no additional QScrollArea needed here, same reasoning as
        # import_dialog.py.
        clamp_dialog_height_to_screen(self, parent)
        self._worker: ReverseGeocodeWorker | None = None
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self._info_label = QLabel(tr("update_loc_info"))
        self._info_label.setWordWrap(True)
        self._info_label.setStyleSheet("color: palette(mid);")
        layout.addWidget(self._info_label)

        # ── Section 1: Scope ──────────────────────────────────────────────────
        self._scope_box = QGroupBox(tr("update_loc_scope_group"))
        scope_layout = QVBoxLayout(self._scope_box)

        self._rb_this = QRadioButton(tr("update_loc_scope_this"))
        self._rb_missing = QRadioButton(tr("update_loc_scope_missing"))
        self._rb_all = QRadioButton(tr("update_loc_scope_all"))

        scope_layout.addWidget(self._rb_this)
        scope_layout.addWidget(self._rb_missing)
        scope_layout.addWidget(self._rb_all)
        layout.addWidget(self._scope_box)

        if self._from_context_menu:
            self._rb_this.setChecked(True)
        else:
            self._rb_this.setEnabled(False)
            self._rb_missing.setChecked(True)

        # ── Section 2: Lookup options ─────────────────────────────────────────
        self._lookup_box = QGroupBox(tr("update_loc_lookup_group"))
        lookup_layout = QVBoxLayout(self._lookup_box)

        # Default to posted coordinates (False); user may opt into corrected.
        self._cb_corrected = QCheckBox(tr("update_loc_use_corrected"))
        self._cb_corrected.setChecked(False)
        lookup_layout.addWidget(self._cb_corrected)

        layout.addWidget(self._lookup_box)

        # ── Progress ──────────────────────────────────────────────────────────
        self._progress_label = QLabel("")
        self._progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._progress_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress_resolved = 0
        layout.addWidget(self._progress)

        # ── Log ───────────────────────────────────────────────────────────────
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText(tr("update_loc_log_placeholder"))
        layout.addWidget(self._log)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        self._start_btn = QPushButton(tr("update_loc_start_btn"))
        self._start_btn.clicked.connect(self._start)
        btn_row.addWidget(self._start_btn)

        self._cancel_btn = QPushButton(tr("cancel"))
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._request_cancel)
        btn_row.addWidget(self._cancel_btn)

        btn_row.addStretch()

        self._close_btn = QPushButton(tr("close"))
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)

        layout.addLayout(btn_row)

    # ── Row building ──────────────────────────────────────────────────────────

    def _build_rows(self) -> list[_CacheRow]:
        from opensak.db.database import get_session
        from opensak.db.models import Cache
        from sqlalchemy.orm import joinedload

        use_corrected = self._cb_corrected.isChecked()

        rows: list[_CacheRow] = []
        with get_session() as session:
            query = session.query(Cache)
            if use_corrected:
                query = query.options(joinedload(Cache.user_note))

            if self._rb_this.isChecked() and self._gc_codes is not None:
                # #1041: chunked to stay under SQLite's bound-parameter limit
                # when the current filter holds a large database.
                codes = list(self._gc_codes)
                caches = []
                for i in range(0, len(codes), 500):
                    caches.extend(
                        query.filter(Cache.gc_code.in_(codes[i:i + 500])).all()
                    )
            elif self._rb_missing.isChecked():
                caches = query.filter(
                    (Cache.country == None) | (Cache.country == "") |
                    (Cache.state   == None) | (Cache.state   == "") |
                    (Cache.county  == None) | (Cache.county  == "")
                ).all()
            else:
                caches = query.all()  # _rb_all: no filter

            for cache in caches:
                lat, lon = cache.latitude, cache.longitude
                basis = "posted"
                if use_corrected and cache.user_note and cache.user_note.is_corrected:
                    clat = cache.user_note.corrected_lat
                    clon = cache.user_note.corrected_lon
                    if clat is not None and clon is not None:
                        lat, lon = clat, clon
                        basis = "corrected"
                if lat is None or lon is None:
                    continue
                rows.append(_CacheRow(gc_code=cache.gc_code, lat=lat, lon=lon, basis=basis))

        return rows

    # ── Actions ───────────────────────────────────────────────────────────────

    def _start(self) -> None:
        rows = self._build_rows()
        if not rows:
            self._progress_label.setText(tr("update_loc_nothing_to_do"))
            return

        self._set_controls_enabled(False)

        self._progress.setRange(0, len(rows))
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._progress_resolved = 0
        self._log.clear()
        self._progress_label.setText(tr("update_loc_running", total=len(rows)))

        self._worker = ReverseGeocodeWorker(rows, parent=self)
        self._worker.row_done.connect(self._on_row_done)
        self._worker.all_done.connect(self._on_done)
        self._worker.cancelled.connect(self._on_cancelled)
        # Forget the worker on QThread.finished (emitted after run() has
        # returned and isRunning() is already false) — NOT from the slots
        # driven by all_done/cancelled, which are emitted from inside run()
        # while the thread is still alive. Dropping the last reference there
        # left run()'s own stack frame holding it, so the QThread was
        # destroyed from inside its own run() and Qt aborted the process
        # ("QThread: Destroyed while thread '' is still running"). Same rule
        # as ImportWorker in import_dialog.py. parent=self additionally keeps
        # the C++ object owned by the dialog, so Python refcounting can never
        # delete a running thread on its own. _on_worker_finished is connected
        # before deleteLater so it runs first; both are queued.
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _request_cancel(self) -> None:
        if self._worker:
            self._worker.request_cancel()
        self._cancel_btn.setEnabled(False)

    # ── Worker signals ────────────────────────────────────────────────────────

    def _on_row_done(self, gc_code: str, line: str) -> None:
        self._log.append(line)
        if gc_code:  # empty gc_code = error banner, not a resolved row
            self._progress_resolved += 1
            self._progress.setValue(self._progress_resolved)

    def _on_done(self, result: UpdateLocationResult) -> None:
        self.location_updated.emit()
        self._finalize()
        self._progress_label.setText(tr(
            "update_loc_done",
            updated=result.updated,
            skipped=result.skipped,
            errors=result.errors,
        ))

    def _on_cancelled(self, result: UpdateLocationResult) -> None:
        self._finalize()
        self._progress_label.setText(tr("update_loc_cancelled", updated=result.updated))
        if result.updated > 0:
            self.location_updated.emit()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._start_btn.setEnabled(enabled)
        self._scope_box.setEnabled(enabled)
        self._lookup_box.setEnabled(enabled)
        self._cancel_btn.setEnabled(not enabled)
        self._close_btn.setEnabled(enabled)

    def _finalize(self) -> None:
        self._progress.setVisible(False)
        self._set_controls_enabled(True)
        if not self._from_context_menu:
            self._rb_this.setEnabled(False)

    def _on_worker_finished(self) -> None:
        """run() has returned and the thread has stopped — only now is it safe
        to drop our reference. The worker deletes itself via deleteLater
        (connected after this slot), so closeEvent never sees a dangling
        C++ object either."""
        self._worker = None

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
            self._worker.wait()
        super().closeEvent(event)

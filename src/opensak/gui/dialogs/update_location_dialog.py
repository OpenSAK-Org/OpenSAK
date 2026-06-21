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
        from opensak.geo.store import BoundaryStore
        from opensak.geo.boundaries import TerritoryResolver
        from opensak.db.database import get_session
        from opensak.db.models import Cache

        result = UpdateLocationResult()

        if self._cancel:
            self.cancelled.emit(result)
            return

        store = BoundaryStore()
        if not store.available():
            result.errors += 1
            self.row_done.emit("", tr("update_loc_no_boundaries"))
            self.all_done.emit(result)
            return

        resolver = TerritoryResolver(store)
        dataset = store.dataset_version()
        now = datetime.now(timezone.utc)

        try:
            with get_session() as session:
                for row in self._rows:
                    if self._cancel:
                        self.cancelled.emit(result)
                        return
                    loc = resolver.resolve(row.lat, row.lon)
                    cache = session.query(Cache).filter_by(gc_code=row.gc_code).first()
                    if cache:
                        cache.country          = loc.country
                        cache.state            = loc.state
                        cache.county           = loc.county
                        cache.location_source  = "boundary"
                        cache.location_basis   = row.basis
                        cache.location_updated = now
                        cache.location_dataset = dataset
                        result.updated += 1
                        line = tr(
                            "update_loc_row",
                            gc_code=row.gc_code,
                            country=loc.country or "–",
                            state=loc.state or "–",
                            county=loc.county or "–",
                        )
                        self.row_done.emit(row.gc_code, line)
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
                query = query.filter(Cache.gc_code.in_(self._gc_codes))
            elif self._rb_missing.isChecked():
                query = query.filter(
                    (Cache.country == None) | (Cache.country == "") |
                    (Cache.state   == None) | (Cache.state   == "") |
                    (Cache.county  == None) | (Cache.county  == "")
                )
            # _rb_all: no filter

            for cache in query.all():
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

        self._progress.setRange(0, 0)
        self._progress.setVisible(True)
        self._log.clear()
        self._progress_label.setText(tr("update_loc_running", total=len(rows)))

        self._worker = ReverseGeocodeWorker(rows)
        self._worker.row_done.connect(self._on_row_done)
        self._worker.all_done.connect(self._on_done)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.start()

    def _request_cancel(self) -> None:
        if self._worker:
            self._worker.request_cancel()
        self._cancel_btn.setEnabled(False)

    # ── Worker signals ────────────────────────────────────────────────────────

    def _on_row_done(self, _gc_code: str, line: str) -> None:
        self._log.append(line)

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

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
            self._worker.wait(3000)
        super().closeEvent(event)

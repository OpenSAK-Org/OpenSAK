# src/opensak/gui/dialogs/boundary_packs_dialog.py — Download and update boundary pack dialogs.

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QDialogButtonBox,
)

from opensak.lang import tr


# ── Workers ───────────────────────────────────────────────────────────────────

class _DownloadAllWorker(QThread):
    progress = Signal(int, int)   # (done, total)
    finished = Signal(int)        # downloaded count

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel = False

    def request_cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        from opensak.geo.store import default_data_dir
        from opensak.geo import packs

        data_dir = default_data_dir()
        downloaded = 0

        def _cb(done: int, total: int) -> None:
            nonlocal downloaded
            downloaded = done
            self.progress.emit(done, total)

        if not self._cancel:
            downloaded = packs.fetch_all(data_dir, progress_cb=_cb)
        self.finished.emit(downloaded)


class _CheckUpdateWorker(QThread):
    check_done  = Signal(bool, object)   # (newer_available, manifest_or_None)
    apply_done  = Signal(int)            # count of updated files
    file_updated = Signal(str)           # each file as it's updated

    def __init__(self, *, apply: bool = False, manifest: dict | None = None, parent=None):
        super().__init__(parent)
        self._apply = apply
        self._manifest = manifest

    def run(self) -> None:
        from opensak.geo.store import default_data_dir
        from opensak.geo import packs

        data_dir = default_data_dir()

        if not self._apply:
            newer, manifest = packs.check_update(data_dir, force=True)
            self.check_done.emit(newer, manifest)
        else:
            assert self._manifest is not None
            updated = packs.apply_update(data_dir, self._manifest, progress_cb=self.file_updated.emit)
            self.apply_done.emit(len(updated))


# ── Download-all dialog ───────────────────────────────────────────────────────

class BoundaryDownloadDialog(QDialog):
    """Progress dialog for downloading all county packs from OpenSAK-Data."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("boundary_dl_title"))
        self.setMinimumWidth(400)
        self._worker: _DownloadAllWorker | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        info = QLabel(tr("boundary_dl_info"))
        info.setWordWrap(True)
        info.setStyleSheet("color: palette(mid);")
        layout.addWidget(info)

        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)
        self._bar.setVisible(False)
        layout.addWidget(self._bar)

        btns = QHBoxLayout()
        self._start_btn = QPushButton(tr("boundary_dl_start"))
        self._start_btn.setDefault(True)
        self._start_btn.clicked.connect(self._start)

        self._cancel_btn = QPushButton(tr("cancel"))
        self._cancel_btn.clicked.connect(self._on_cancel)

        btns.addStretch()
        btns.addWidget(self._start_btn)
        btns.addWidget(self._cancel_btn)
        layout.addLayout(btns)

    def _start(self) -> None:
        self._start_btn.setEnabled(False)
        self._bar.setVisible(True)
        self._status.setText(tr("boundary_dl_running", done=0, total=0))
        self._worker = _DownloadAllWorker(self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_done)
        self._worker.start()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._bar.setRange(0, total)
            self._bar.setValue(done)
        self._status.setText(tr("boundary_dl_running", done=done, total=total))

    def _on_done(self, downloaded: int) -> None:
        self._bar.setVisible(False)
        if downloaded == 0:
            self._status.setText(tr("boundary_dl_none"))
        else:
            self._status.setText(tr("boundary_dl_done", downloaded=downloaded))
        self._cancel_btn.setText(tr("btn_close"))
        self._worker = None

    def _on_cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
            self._status.setText(tr("boundary_dl_cancelled"))
            self._worker.wait(2000)
            self._worker = None
        self.close()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
            self._worker.wait(2000)
        super().closeEvent(event)


# ── Check-for-updates dialog ──────────────────────────────────────────────────

class BoundaryCheckDialog(QDialog):
    """Checks for boundary data updates and optionally applies them."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("boundary_check_title"))
        self.setMinimumWidth(380)
        self._worker: _CheckUpdateWorker | None = None
        self._manifest: dict | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self._status = QLabel(tr("boundary_check_checking"))
        self._status.setWordWrap(True)
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)
        layout.addWidget(self._bar)

        self._apply_btn = QPushButton(tr("boundary_check_apply"))
        self._apply_btn.setVisible(False)
        self._apply_btn.clicked.connect(self._apply_update)

        self._close_btn = QPushButton(tr("close"))
        self._close_btn.setEnabled(False)
        self._close_btn.clicked.connect(self.close)

        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(self._apply_btn)
        btns.addWidget(self._close_btn)
        layout.addLayout(btns)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._run_check()

    def _run_check(self) -> None:
        self._worker = _CheckUpdateWorker(apply=False, parent=self)
        self._worker.check_done.connect(self._on_check_done)
        self._worker.start()

    def _on_check_done(self, newer: bool, manifest: object) -> None:
        self._bar.setVisible(False)
        self._close_btn.setEnabled(True)
        self._worker = None

        if not newer:
            self._status.setText(
                tr("boundary_check_failed") if manifest is None
                else tr("boundary_check_uptodate")
            )
            return

        self._manifest = manifest  # type: ignore[assignment]
        version = str((self._manifest or {}).get("dataset_version", ""))
        self._status.setText(tr("boundary_check_available", version=version))
        self._apply_btn.setVisible(True)
        self._apply_btn.setDefault(True)

    def _apply_update(self) -> None:
        self._apply_btn.setVisible(False)
        self._close_btn.setEnabled(False)
        self._bar.setVisible(True)
        self._bar.setRange(0, 0)
        self._status.setText(tr("boundary_check_applying"))

        self._worker = _CheckUpdateWorker(apply=True, manifest=self._manifest, parent=self)
        self._worker.apply_done.connect(self._on_apply_done)
        self._worker.start()

    def _on_apply_done(self, count: int) -> None:
        self._bar.setVisible(False)
        self._close_btn.setEnabled(True)
        self._status.setText(tr("boundary_check_done", count=count))
        self._worker = None

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._worker and self._worker.isRunning():
            self._worker.wait(2000)
        super().closeEvent(event)

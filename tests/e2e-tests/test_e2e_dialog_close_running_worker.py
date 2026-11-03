# tests/e2e-tests/test_e2e_dialog_close_running_worker.py
#
# Regression test for #1265: pressing Escape (or Close, or the window's
# close button) in a dialog while its background worker ran killed OpenSAK
# with
#   QThread: Destroyed while thread '' is still running
# The dialogs are opened with exec() as locals of MainWindow, so a closed
# dialog is deleted right away, together with its still-running QThread.
# Escape calls reject() and never reaches closeEvent(), so the closeEvent()
# guards some dialogs had didn't help. Reproduced on v1.22.0-beta.12 with
# the GPX import and KML export dialogs.
#
# The dialogs' own workers need files, devices or a network, so a stand-in
# that runs until the test releases it takes their place. Where the job can
# be cancelled, closing cancels it instead (the cancellation itself is in
# tests/unit-tests/test_cancel_long_jobs_1265.py).

import threading
from dataclasses import dataclass
from typing import Callable

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QThread, QTimer, Qt
from PySide6.QtTest import QTest

from opensak.utils.cancel import CancelFlag


class _BlockingWorker(QThread):
    """Stands in for a dialog's worker: runs until the test releases it.
    Has both ways the real workers are cancelled: a CancelFlag (`cancel`)
    and request_cancel()."""

    def __init__(self, gate: threading.Event, parent=None):
        super().__init__(parent)
        self._gate = gate
        self.cancel = CancelFlag()
        self._requested = False

    def request_cancel(self) -> None:
        self._requested = True

    @property
    def cancel_requested(self) -> bool:
        return self._requested or self.cancel.is_set()

    def run(self) -> None:
        self._gate.wait(10)


class _Workers:
    def __init__(self) -> None:
        self.gate = threading.Event()
        self.started: list[_BlockingWorker] = []

    def start(self, dialog, attr: str, parent=None) -> _BlockingWorker:
        """Put a running stand-in worker where *dialog* keeps its own."""
        worker = _BlockingWorker(self.gate, parent)
        setattr(dialog, attr, worker)
        self.started.append(worker)
        worker.start()
        return worker

    def release(self) -> None:
        self.gate.set()


@pytest.fixture
def workers(qtbot):
    # Depends on qtbot so it is torn down first: every worker has finished
    # before qtbot deletes the dialogs, also when an assertion failed.
    w = _Workers()
    yield w
    w.release()
    for worker in w.started:
        try:
            worker.wait(10000)
        except RuntimeError:   # already deleted by deleteLater()
            pass


@dataclass
class _Case:
    make: Callable
    attr: str = "_worker"
    cancels: bool = False   # Escape cancels the job instead of being ignored


def _import(parent, _mp):
    from opensak.gui.dialogs.import_dialog import ImportDialog
    return ImportDialog(parent)


def _kml(parent, _mp):
    from opensak.gui.dialogs.kml_export_dialog import KmlExportDialog
    return KmlExportDialog([], parent=parent)


def _file_export(parent, _mp):
    from opensak.gui.dialogs.file_export_dialog import FileExportDialog
    return FileExportDialog([], parent=parent)


def _poi_export(parent, _mp):
    from opensak.gui.dialogs.poi_export_dialog import PoiExportDialog
    return PoiExportDialog([], parent=parent)


def _gps(parent, mp):
    from opensak.gui.dialogs.gps_dialog import GpsExportDialog
    mp.setattr("opensak.gps.garmin.find_garmin_devices", lambda: [])
    return GpsExportDialog(parent, caches=[])


def _found(parent, _mp):
    from opensak.gui.dialogs.found_dialog import FoundUpdaterDialog
    return FoundUpdaterDialog(parent)


def _move(parent, _mp):
    from opensak.gui.dialogs.move_caches_dialog import MoveCachesDialog
    return MoveCachesDialog(parent, None, [], [])


def _update_location(parent, _mp):
    from opensak.gui.dialogs.update_location_dialog import UpdateLocationDialog
    return UpdateLocationDialog(parent)


def _pq_email_check(parent, _mp):
    from opensak.gui.dialogs.pq_email_check_dialog import PQEmailCheckDialog
    return PQEmailCheckDialog(parent)


def _gsak_filter_import(parent, _mp):
    from opensak.gui.dialogs.gsak_filter_import_dialog import GsakFilterImportDialog
    return GsakFilterImportDialog(parent)


def _boundary_download(parent, _mp):
    from opensak.gui.dialogs.boundary_packs_dialog import BoundaryDownloadDialog
    return BoundaryDownloadDialog(parent)


def _boundary_check(parent, mp):
    from opensak.gui.dialogs.boundary_packs_dialog import BoundaryCheckDialog
    # It starts a real (network) check when shown; the test supplies one.
    mp.setattr(BoundaryCheckDialog, "_run_check", lambda self: None)
    return BoundaryCheckDialog(parent)


CASES = {
    "gpx-import": _Case(_import, cancels=True),
    "gpx-import-location-lookup": _Case(_import, attr="_geo_worker", cancels=True),
    "kml-export": _Case(_kml, cancels=True),
    "file-export": _Case(_file_export, cancels=True),
    "poi-export": _Case(_poi_export, cancels=True),
    "send-to-gps": _Case(_gps, cancels=True),
    # Deleting the old files can't be stopped; the export after it is skipped.
    "send-to-gps-delete-old": _Case(_gps, attr="_delete_worker"),
    "update-found": _Case(_found),
    "move-copy-caches": _Case(_move, cancels=True),
    "update-location": _Case(_update_location, cancels=True),
    "pq-email-check": _Case(_pq_email_check),
    "gsak-filter-import": _Case(_gsak_filter_import),
    "boundary-download": _Case(_boundary_download, cancels=True),
    "boundary-check": _Case(_boundary_check),
}


@pytest.mark.parametrize("name", list(CASES))
def test_dialog_stays_open_while_its_worker_runs(name, empty_window, qtbot, workers, monkeypatch):
    case = CASES[name]
    dlg = case.make(empty_window, monkeypatch)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    worker = workers.start(dlg, case.attr)

    QTest.keyClick(dlg, Qt.Key.Key_Escape)
    assert dlg.isVisible(), "Escape closed the dialog while its worker ran"
    dlg.close()   # the window's close button
    assert dlg.isVisible(), "the close button closed the dialog while its worker ran"
    assert worker.isRunning()
    assert worker.cancel_requested is case.cancels

    workers.release()
    qtbot.waitUntil(lambda: not worker.isRunning())
    QTest.keyClick(dlg, Qt.Key.Key_Escape)
    assert not dlg.isVisible(), "Escape no longer closes the dialog once the worker is done"


def test_import_escape_during_import_like_main_window(empty_window, qtbot, workers):
    """The reported case, opened the way MainWindow._open_import_dialog()
    does: exec() in a function whose local is the dialog. Before the fix,
    Escape ended exec() with the import still running."""
    from opensak.gui.dialogs.import_dialog import ImportDialog

    seen: dict[str, bool] = {}

    def open_import_dialog() -> QThread:
        dlg = ImportDialog(empty_window)
        worker = workers.start(dlg, "_worker")

        def escape_then_release():
            QTest.keyClick(dlg, Qt.Key.Key_Escape)
            seen["open_after_escape"] = dlg.isVisible()
            workers.release()
            poll.start(20)

        def escape_once_done():
            if worker.isRunning():
                return
            poll.stop()
            QTest.keyClick(dlg, Qt.Key.Key_Escape)

        poll = QTimer(dlg)
        poll.timeout.connect(escape_once_done)
        QTimer.singleShot(200, escape_then_release)
        dlg.exec()
        # Checked before returning: the return deletes the dialog, and before
        # the fix the still-running worker with it, which aborts the process.
        assert not worker.isRunning(), "exec() returned while the import ran (#1265)"
        return worker

    worker = open_import_dialog()
    assert seen["open_after_escape"] is True
    assert worker.isFinished()


def test_settings_hands_a_running_worker_to_the_main_window(empty_window, qtbot, workers):
    """OK closes Settings too, so it can't stay open: a connection test still
    running moves to the main window and outlives the dialog."""
    from opensak.gui.dialogs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(empty_window)
    dlg.show()
    qtbot.waitExposed(dlg)
    worker = workers.start(dlg, "_pq_email_worker", parent=dlg)
    destroyed: list[bool] = []
    worker.destroyed.connect(lambda *_: destroyed.append(True))

    dlg.reject()   # Cancel; OK and Escape end in done() the same way
    assert not dlg.isVisible()
    assert worker.parent() is empty_window

    dlg.deleteLater()   # what happens once exec() has returned
    qtbot.wait(50)
    assert worker.isRunning()

    workers.release()
    qtbot.waitUntil(lambda: bool(destroyed))

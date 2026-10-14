# tests/e2e-tests/test_e2e_refresh_after_close.py — a refresh result that
# arrives after the main window is gone must be dropped, not delivered.
#
# Since #958, widgets left by a test are deleted at teardown. A RefreshWorker
# started just before a window was closed then reported back after the window
# had been deleted, and its result slot ran on the dead window in the next
# test ("Internal C++ object (CacheTableView) already deleted"). The same can
# happen in the app when a window closes while a slow query is still running.

import threading

import pytest

pytest.importorskip("pytestqt")

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent

from tests.data import make_fake_manager, seed_standard_caches, wait_for_refresh


def test_result_after_the_window_is_deleted_is_dropped(qtbot, tmp_path, monkeypatch):
    import opensak.db.manager as mgr_module
    from opensak.db.database import init_db
    from opensak.gui import mainwindow as mw
    from opensak.gui.refresh_worker import RefreshWorker
    from opensak.lang import load_language

    load_language("en")
    db_path = tmp_path / "E2ETest.db"
    init_db(db_path=db_path)
    seed_standard_caches(tmp_path)
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path))

    window = mw.MainWindow()
    window.show()
    qtbot.waitExposed(window)
    window._refresh_cache_list()
    wait_for_refresh(window)

    # Hold the next worker inside run() until the window is gone.
    release = threading.Event()
    original_run = RefreshWorker.run

    def held_run(self):
        release.wait(10)
        original_run(self)

    monkeypatch.setattr(RefreshWorker, "run", held_run)
    window._refresh_cache_list()
    worker = window._active_refresh_workers[-1]

    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(window)

    release.set()
    assert worker.wait(10000)
    # Deliver whatever the worker queued. pytest-qt fails the test if a slot
    # raises here.
    qtbot.wait(100)
    mgr_module._manager = None

    # The dropped result never restored the wait cursor it was paired with;
    # don't leave it on the stack for later tests.
    from PySide6.QtWidgets import QApplication
    while QApplication.overrideCursor() is not None:
        QApplication.restoreOverrideCursor()

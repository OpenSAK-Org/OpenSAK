"""tests/unit-tests/test_delete_feedback_645.py — feedback while deleting (#645).

"Delete all caches in filter" and "Delete flagged caches" delete on the GUI
thread (about 2 s for 60,000 caches). While they do, the window shows a wait
cursor and "Deleting N caches…" in the status bar, and the cursor is always
restored, also when the delete fails.
"""

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402


@pytest.fixture(autouse=True)
def _english():
    from opensak.lang import load_language
    load_language("en")


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    import opensak.db.manager as mgr_module
    from opensak.db.database import get_session, init_db
    from opensak.db.models import Cache
    from opensak.gui.mainwindow import MainWindow
    from tests.data import make_fake_manager

    monkeypatch.setattr(MainWindow, "_check_update_background", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_setup_complete", lambda self: None)
    db_path = tmp_path / "delete_645.db"
    init_db(db_path=db_path)
    with get_session() as s:
        for i in range(5):
            s.add(Cache(gc_code=f"GC64{i}", name=f"Cache {i}", cache_type="Traditional Cache",
                        latitude=55.0 + i / 100, longitude=12.0, user_flag=i < 2))
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="Delete645"))
    win = MainWindow()
    qtbot.addWidget(win)
    win._refresh_cache_list()
    qtbot.waitUntil(lambda: win._cache_table._model.rowCount() == 5)
    qtbot.waitUntil(lambda: not win._active_refresh_workers)
    yield win
    win.close()
    mgr_module._manager = None


def _cache_codes() -> set[str]:
    from opensak.db.database import get_session
    from opensak.db.models import Cache
    with get_session() as s:
        return {c.gc_code for c in s.query(Cache)}


@pytest.fixture
def seen(monkeypatch, window):
    """What the window showed while delete_caches() ran."""
    import opensak.db.cache_delete as cache_delete

    real = cache_delete.delete_caches
    state: dict = {}

    def spy(session, gc_codes):
        cursor = QApplication.overrideCursor()
        state["cursor"] = cursor.shape() if cursor is not None else None
        state["message"] = window._statusbar.currentMessage()
        return real(session, gc_codes)

    monkeypatch.setattr(cache_delete, "delete_caches", spy)
    return state


@pytest.fixture
def answer_yes(monkeypatch):
    from opensak.gui.icon import OpenSAKMessageBox
    monkeypatch.setattr(OpenSAKMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))


def test_delete_filtered_shows_wait_cursor_and_message(window, seen, answer_yes, qtbot):
    window._delete_filtered_caches()
    assert seen["cursor"] == Qt.CursorShape.WaitCursor
    assert seen["message"] == "Deleting 5 caches…"
    assert _cache_codes() == set()
    qtbot.waitUntil(lambda: not window._active_refresh_workers)
    assert QApplication.overrideCursor() is None                # restored
    assert window._statusbar.currentMessage() == "5 caches deleted"


def test_delete_flagged_shows_wait_cursor_and_message(window, seen, answer_yes, qtbot):
    window._delete_flagged_caches()
    assert seen["cursor"] == Qt.CursorShape.WaitCursor
    assert seen["message"] == "Deleting 2 caches…"
    assert _cache_codes() == {"GC642", "GC643", "GC644"}
    qtbot.waitUntil(lambda: not window._active_refresh_workers)
    assert QApplication.overrideCursor() is None


def test_cursor_is_restored_when_the_delete_fails(window, monkeypatch):
    import opensak.db.cache_delete as cache_delete

    def boom(session, gc_codes):
        raise RuntimeError("disk full")

    monkeypatch.setattr(cache_delete, "delete_caches", boom)
    with pytest.raises(RuntimeError):
        window._bulk_delete_caches(["GC640"])
    assert QApplication.overrideCursor() is None
    assert len(_cache_codes()) == 5


def test_no_answer_deletes_nothing_and_shows_nothing(window, seen, monkeypatch):
    from opensak.gui.icon import OpenSAKMessageBox
    monkeypatch.setattr(OpenSAKMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    window._delete_filtered_caches()
    assert seen == {}
    assert len(_cache_codes()) == 5
    assert QApplication.overrideCursor() is None

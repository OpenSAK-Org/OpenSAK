"""tests/unit-tests/test_empty_filter_after_bulk_action_1315.py — #1315.

With a "User flag = set" filter active, User Flags → Clear all flags left an
empty cache list and nothing explained why. A bulk action (clearing flags,
deleting or moving caches) that leaves the active filter with no caches now
asks "No caches match the active filter any more. Clear the filter?".
Ordinary refreshes never ask.
"""

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QMessageBox  # noqa: E402


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
    db_path = tmp_path / "empty_filter_1315.db"
    init_db(db_path=db_path)
    with get_session() as s:
        for i in range(5):
            s.add(Cache(gc_code=f"GC13{i}", name=f"Cache {i}", cache_type="Traditional Cache",
                        latitude=55.0 + i / 100, longitude=12.0, user_flag=i < 2))
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="Empty1315"))
    win = MainWindow()
    qtbot.addWidget(win)
    _refresh(qtbot, win)
    assert win._cache_table.row_count() == 5
    yield win
    win.close()
    mgr_module._manager = None


def _refresh(qtbot, win, **kwargs):
    win._refresh_cache_list(**kwargs)
    qtbot.waitUntil(lambda: not win._active_refresh_workers)


def _apply_flag_filter(qtbot, win):
    from opensak.filters.engine import FilterSet, UserFlagFilter

    fs = FilterSet()
    fs.add(UserFlagFilter(True))
    win._current_filterset = fs
    win._set_clear_filter_active(True)
    _refresh(qtbot, win)
    assert win._cache_table.row_count() == 2


@pytest.fixture
def asked(monkeypatch):
    """Records each question; answers with whatever ``asked["answer"]`` is."""
    from opensak.gui.icon import OpenSAKMessageBox

    state: dict = {"titles": [], "texts": [], "answer": QMessageBox.StandardButton.Yes}

    def question(parent, title, text, *args, **kwargs):
        state["titles"].append(title)
        state["texts"].append(text)
        return state["answer"]

    monkeypatch.setattr(OpenSAKMessageBox, "question", staticmethod(question))
    return state


def _empty_filter_questions(state) -> int:
    from opensak.lang import tr
    return state["texts"].count(tr("filter_now_empty_msg"))


def test_clear_all_flags_offers_to_clear_the_filter(qtbot, window, asked):
    _apply_flag_filter(qtbot, window)
    window._clear_all_flags()          # first question: confirm; Yes
    qtbot.waitUntil(lambda: not window._active_refresh_workers)
    assert _empty_filter_questions(asked) == 1
    # Yes → the filter is cleared and every cache is shown again.
    assert not window._has_active_filter()
    assert window._cache_table.row_count() == 5


def test_no_keeps_the_filter_and_the_empty_list(qtbot, window, monkeypatch):
    from opensak.gui.icon import OpenSAKMessageBox
    from opensak.lang import tr

    texts: list[str] = []
    answers = iter([QMessageBox.StandardButton.Yes,   # confirm Clear all flags
                    QMessageBox.StandardButton.No])   # keep the filter

    def question(parent, title, text, *args, **kwargs):
        texts.append(text)
        return next(answers)

    monkeypatch.setattr(OpenSAKMessageBox, "question", staticmethod(question))
    _apply_flag_filter(qtbot, window)
    window._clear_all_flags()
    qtbot.waitUntil(lambda: not window._active_refresh_workers)
    assert texts.count(tr("filter_now_empty_msg")) == 1
    assert window._has_active_filter()
    assert window._cache_table.row_count() == 0


def test_clear_flag_for_filter_offers_too(qtbot, window, asked):
    _apply_flag_filter(qtbot, window)
    window._set_flag_for_filtered(False)
    qtbot.waitUntil(lambda: not window._active_refresh_workers)
    assert _empty_filter_questions(asked) == 1


def test_delete_filtered_caches_offers_too(qtbot, window, asked):
    _apply_flag_filter(qtbot, window)
    window._delete_filtered_caches()
    qtbot.waitUntil(lambda: not window._active_refresh_workers)
    assert _empty_filter_questions(asked) == 1
    assert window._cache_table.row_count() == 3   # the unflagged ones


def test_no_question_when_caches_still_match(qtbot, window, asked):
    _apply_flag_filter(qtbot, window)
    window._set_flag_for_filtered(True)   # already flagged; list unchanged
    qtbot.waitUntil(lambda: not window._active_refresh_workers)
    assert _empty_filter_questions(asked) == 0
    assert window._cache_table.row_count() == 2


def test_no_question_without_a_filter(qtbot, window, asked):
    window._delete_filtered_caches()     # deletes the whole database
    qtbot.waitUntil(lambda: not window._active_refresh_workers)
    assert window._cache_table.row_count() == 0
    assert _empty_filter_questions(asked) == 0


def test_ordinary_refresh_never_asks(qtbot, window, asked):
    _apply_flag_filter(qtbot, window)
    from opensak.db.database import get_session
    from opensak.db.models import Cache
    with get_session() as s:
        s.query(Cache).update({Cache.user_flag: False})
    _refresh(qtbot, window)
    assert window._cache_table.row_count() == 0
    assert _empty_filter_questions(asked) == 0

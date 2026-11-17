"""tests/unit-tests/test_macro_filter_refresh_1325.py — #1325.

A macro calling opensak.clear_filter() and then opensak.filter{} showed the
filtered list at once, but the full list that the clear had started loading
in the background arrived afterwards and replaced it. The grid showed every
cache while the filter (and the red clear button) stayed active. It happened
only sometimes: opensak.progress lets queued events run, so a background load
that finished during the macro's loop arrived early and did no harm.

Now filling the table directly makes every background load still running
stale, so its result is discarded.
"""

import pytest

pytest.importorskip("pytestqt")

from opensak.filters.engine import FilterSet, GcCodeFilter  # noqa: E402


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
    db_path = tmp_path / "macro_filter_1325.db"
    init_db(db_path=db_path)
    with get_session() as s:
        for i in range(4):
            s.add(Cache(gc_code=f"GC135{i}", name=f"Cache {i}", cache_type="Traditional Cache",
                        latitude=55.0 + i / 100, longitude=12.0,
                        difficulty=1.0, terrain=1.0, found=i == 0))
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="Filter1325"))
    win = MainWindow()
    qtbot.addWidget(win)
    win._refresh_cache_list()
    qtbot.waitUntil(lambda: not win._active_refresh_workers)
    yield win
    win.close()
    mgr_module._manager = None


def _one_cache() -> FilterSet:
    return FilterSet().add(GcCodeFilter("GC1351", op="in_list"))


def _finish_background_loads(window, qtbot) -> None:
    """What happens once the macro returns and the event loop runs again."""
    qtbot.waitUntil(lambda: not window._active_refresh_workers)
    qtbot.wait(50)   # let queued result signals be delivered


@pytest.mark.parametrize("load_done_before_filter", [True, False],
                         ids=["load-finished", "load-running"])
def test_filter_after_clear_filter_is_not_replaced(window, qtbot, load_done_before_filter):
    # The macro runs on the GUI thread: no events are processed in between.
    window.clear_filter()
    if load_done_before_filter:
        for worker in list(window._active_refresh_workers):
            worker.wait()   # the query is done, its result is still queued
    assert window.apply_filter(_one_cache(), "macro") == 1
    assert window._cache_table.row_count() == 1

    _finish_background_loads(window, qtbot)

    assert window._cache_table.row_count() == 1
    assert [c.gc_code for c in window._cache_table.get_all_caches()] == ["GC1351"]
    assert window._count_lbl.text() == "1 cache"
    assert window._btn_clear_filter.isEnabled()


def test_status_bar_click_after_a_running_load_is_not_replaced(window, qtbot):
    window._refresh_cache_list()   # e.g. typing in the search box
    window._filter_by_status("found")
    assert window._cache_table.row_count() == 1

    _finish_background_loads(window, qtbot)

    assert window._cache_table.row_count() == 1


def test_clear_filter_alone_still_shows_every_cache(window, qtbot):
    assert window.apply_filter(_one_cache(), "macro") == 1
    window.clear_filter()

    _finish_background_loads(window, qtbot)

    assert window._cache_table.row_count() == 4
    assert not window._btn_clear_filter.isEnabled()


def test_refresh_after_filter_still_applies(window, qtbot):
    """A reload requested after the filter is shown is not discarded."""
    assert window.apply_filter(_one_cache(), "macro") == 1
    window._refresh_cache_list()

    _finish_background_loads(window, qtbot)

    assert window._cache_table.row_count() == 1
    assert window._refresh_started_at == {}

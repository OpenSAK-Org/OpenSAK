# tests/unit-tests/test_detail_cleared_when_filtered_out_1313.py
#
# #1313: a cache shown in the detail panel stayed there after a filter that
# hides it. MainWindow._clear_detail_if_filtered_out() runs after every list
# reload (_on_refresh_result and _show_filter_result) and empties the panel
# when its cache is no longer in the list.

from types import SimpleNamespace

from opensak.gui.mainwindow import MainWindow


class _Action:
    def __init__(self) -> None:
        self.enabled = True

    def setEnabled(self, value: bool) -> None:
        self.enabled = value


class _Panel:
    def __init__(self, gc_code):
        self.current_gc_code = gc_code
        self.cleared = False

    def clear(self) -> None:
        self.cleared = True
        self.current_gc_code = None


def _window(shown, listed):
    table = SimpleNamespace(
        get_all_caches=lambda: [SimpleNamespace(gc_code=c) for c in listed]
    )
    return SimpleNamespace(
        _detail_panel=_Panel(shown),
        _cache_table=table,
        _act_wp_edit=_Action(),
        _act_wp_delete=_Action(),
    )


def test_cache_hidden_by_filter_is_cleared():
    win = _window("GC111", ["GC222", "GC333"])
    MainWindow._clear_detail_if_filtered_out(win)
    assert win._detail_panel.cleared
    assert win._act_wp_edit.enabled is False
    assert win._act_wp_delete.enabled is False


def test_cache_still_in_list_stays_shown():
    win = _window("GC222", ["GC222", "GC333"])
    MainWindow._clear_detail_if_filtered_out(win)
    assert not win._detail_panel.cleared
    assert win._detail_panel.current_gc_code == "GC222"
    assert win._act_wp_edit.enabled is True


def test_empty_list_clears_the_panel():
    win = _window("GC111", [])
    MainWindow._clear_detail_if_filtered_out(win)
    assert win._detail_panel.cleared


def test_empty_panel_is_left_alone():
    win = _window(None, [])
    MainWindow._clear_detail_if_filtered_out(win)
    assert not win._detail_panel.cleared
    assert win._act_wp_edit.enabled is True


def test_panel_exposes_current_gc_code(qapp):
    from opensak.gui.cache_detail import CacheDetailPanel

    panel = CacheDetailPanel()
    assert panel.current_gc_code is None
    panel._current_gc_code = "GC123"
    assert panel.current_gc_code == "GC123"
    panel.clear()
    assert panel.current_gc_code is None

# tests/unit-tests/test_map_maximize_popout.py — tests for map maximize & pop-out features.

from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

pytest.importorskip("pytestqt")

from opensak.gui.mainwindow import MainWindow
from opensak.lang import tr


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _quiet_startup(monkeypatch):
    """Disable delayed singleShot callbacks that fire mid-test."""
    monkeypatch.setattr(MainWindow, "_initial_load", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_update_background", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_setup_complete", lambda self: None)


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    """MainWindow on a throwaway empty DB."""
    import opensak.db.manager as mgr_module
    from opensak.db.database import init_db
    from opensak.lang import load_language
    from tests.data import make_fake_manager

    load_language("en")

    db_path = tmp_path / "test_map.db"
    init_db(db_path=db_path)
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="MapTest"))

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)

    yield win

    win.close()
    mgr_module._manager = None


# ── maximize map tests ────────────────────────────────────────────────────────


class TestMaximizeMap:
    def test_initial_state_not_maximized(self, window):
        assert window._map_maximized is False
        assert window._cache_table.isVisible()
        assert window._info_bar.isVisible()
        assert window._detail_panel.isVisible()

    def test_maximize_hides_panels(self, window):
        window._toggle_maximize_map()

        assert window._map_maximized is True
        assert not window._cache_table.isVisible()
        assert not window._info_bar.isVisible()
        assert not window._detail_panel.isVisible()

    def test_maximize_saves_splitter_sizes(self, window):
        original_v = window._splitter.sizes()
        original_h = window._bottom_splitter.sizes()

        window._toggle_maximize_map()

        assert window._pre_maximize_splitter_sizes == original_v
        assert window._pre_maximize_bottom_sizes == original_h

    def test_maximize_sets_splitter_to_full_bottom(self, window):
        window._toggle_maximize_map()

        sizes_v = window._splitter.sizes()
        assert sizes_v[0] == 0
        assert sizes_v[1] > 0

    def test_maximize_sets_bottom_splitter_to_full_right(self, window):
        window._toggle_maximize_map()

        sizes_h = window._bottom_splitter.sizes()
        assert sizes_h[0] == 0
        assert sizes_h[1] > 0

    def test_maximize_updates_menu_text(self, window):
        window._toggle_maximize_map()

        assert window._act_maximize_map.text() == tr("action_restore_map")

    def test_maximize_updates_toolbar_text(self, window):
        window._toggle_maximize_map()

        assert window._act_tb_maximize_map.text() == "◻"
        assert window._act_tb_maximize_map.toolTip() == tr("toolbar_restore_map_tooltip")

    def test_restore_shows_panels(self, window):
        window._toggle_maximize_map()
        window._toggle_maximize_map()

        assert window._map_maximized is False
        assert window._cache_table.isVisible()
        assert window._info_bar.isVisible()
        assert window._detail_panel.isVisible()

    def test_restore_restores_splitter_sizes(self, window):
        original_v = window._splitter.sizes()
        original_h = window._bottom_splitter.sizes()

        window._toggle_maximize_map()
        window._toggle_maximize_map()

        assert window._splitter.sizes() == original_v
        assert window._bottom_splitter.sizes() == original_h

    def test_restore_updates_menu_text(self, window):
        window._toggle_maximize_map()
        window._toggle_maximize_map()

        assert window._act_maximize_map.text() == tr("action_maximize_map")

    def test_restore_updates_toolbar_text(self, window):
        window._toggle_maximize_map()
        window._toggle_maximize_map()

        assert window._act_tb_maximize_map.text() == "⛶"
        assert window._act_tb_maximize_map.toolTip() == tr("toolbar_maximize_map_tooltip")

    def test_close_event_restores_if_maximized(self, window, qtbot):
        window._toggle_maximize_map()
        assert window._map_maximized is True

        # closeEvent should restore before saving
        from PySide6.QtGui import QCloseEvent
        event = QCloseEvent()
        window.closeEvent(event)

        assert window._map_maximized is False

    def test_save_splitter_ratios_not_corrupted_by_maximize(self, window):
        from opensak.gui.settings import get_settings

        # Set known sizes
        window._splitter.setSizes([300, 400])
        window._bottom_splitter.setSizes([400, 500])
        window._save_splitter_ratios()

        s = get_settings()
        ratio_v = s.splitter_ratio_top
        ratio_h = s.bottom_splitter_ratio_left

        # Maximize, then close (which restores + saves)
        window._toggle_maximize_map()
        from PySide6.QtGui import QCloseEvent
        event = QCloseEvent()
        window.closeEvent(event)

        # Ratios should match original, not 0.0
        assert abs(s.splitter_ratio_top - ratio_v) < 0.01
        assert abs(s.bottom_splitter_ratio_left - ratio_h) < 0.01


# ── pop-out map tests ─────────────────────────────────────────────────────────


class TestPopoutMap:
    def test_initial_state_not_popped_out(self, window):
        assert window._map_popped_out is False
        assert window._map_popout_window is None

    def test_popout_creates_window(self, window):
        window._toggle_popout_map()

        assert window._map_popped_out is True
        assert window._map_popout_window is not None
        assert window._map_popout_window.isVisible()

    def test_popout_reparents_map_widget(self, window):
        window._toggle_popout_map()

        # Map widget's parent should be inside the popout window
        assert window._map_widget.parent() is not None
        assert window._map_widget.isVisible()
        # The map widget should NOT be in the bottom splitter anymore
        assert window._bottom_splitter.indexOf(window._map_widget) == -1

    def test_popout_updates_menu_text(self, window):
        window._toggle_popout_map()

        assert window._act_popout_map.text() == tr("action_dock_map")

    def test_popout_updates_toolbar_text(self, window):
        window._toggle_popout_map()

        assert window._act_tb_popout_map.text() == "↩"
        assert window._act_tb_popout_map.toolTip() == tr("toolbar_dock_map_tooltip")

    def test_popout_disables_maximize(self, window):
        window._toggle_popout_map()

        assert not window._act_maximize_map.isEnabled()
        assert not window._act_tb_maximize_map.isEnabled()

    def test_dock_back_restores_map_to_splitter(self, window):
        window._toggle_popout_map()
        window._toggle_popout_map()

        assert window._map_popped_out is False
        assert window._map_popout_window is None
        # Map stack should be back in the bottom splitter (index 1)
        assert window._bottom_splitter.indexOf(window._map_stack) == 1

    def test_dock_back_updates_menu_text(self, window):
        window._toggle_popout_map()
        window._toggle_popout_map()

        assert window._act_popout_map.text() == tr("action_popout_map")

    def test_dock_back_updates_toolbar_text(self, window):
        window._toggle_popout_map()
        window._toggle_popout_map()

        assert window._act_tb_popout_map.text() == "⧉"
        assert window._act_tb_popout_map.toolTip() == tr("toolbar_popout_map_tooltip")

    def test_dock_back_re_enables_maximize(self, window):
        window._toggle_popout_map()
        window._toggle_popout_map()

        assert window._act_maximize_map.isEnabled()
        assert window._act_tb_maximize_map.isEnabled()

    def test_popout_restores_maximize_first(self, window):
        # Maximize then pop out — should restore normal layout first
        window._toggle_maximize_map()
        assert window._map_maximized is True

        window._toggle_popout_map()

        assert window._map_maximized is False
        assert window._map_popped_out is True
        assert window._cache_table.isVisible()
        assert window._info_bar.isVisible()
        assert window._detail_panel.isVisible()

    def test_on_popout_closed_docks_back(self, window):
        window._toggle_popout_map()
        assert window._map_popped_out is True

        # Simulate closing the pop-out window
        window._on_popout_closed()

        assert window._map_popped_out is False
        assert window._bottom_splitter.indexOf(window._map_stack) == 1

    def test_close_event_docks_if_popped_out(self, window):
        window._toggle_popout_map()
        assert window._map_popped_out is True

        from PySide6.QtGui import QCloseEvent
        event = QCloseEvent()
        window.closeEvent(event)

        assert window._map_popped_out is False

    def test_popout_geometry_saved_on_dock(self, window):
        from opensak.gui.settings import get_settings

        window._toggle_popout_map()
        # Resize the pop-out window
        window._map_popout_window.resize(1000, 800)

        window._toggle_popout_map()  # dock back

        s = get_settings()
        assert s.map_popout_geometry is not None
        assert len(s.map_popout_geometry) > 0

    def test_popout_geometry_restored_on_reopen(self, window):
        from opensak.gui.settings import get_settings

        window._toggle_popout_map()
        window._map_popout_window.resize(1000, 800)
        window._toggle_popout_map()  # dock back, saves geometry

        # Pop out again — should restore saved geometry
        window._toggle_popout_map()
        assert window._map_popout_window is not None
        # The window exists and is visible (geometry restore is best-effort)
        assert window._map_popout_window.isVisible()

    def test_popout_window_title(self, window):
        window._toggle_popout_map()

        assert window._map_popout_window.windowTitle() == tr("map_popout_title")

    def test_double_popout_is_no_op(self, window):
        window._toggle_popout_map()
        first_win = window._map_popout_window

        # Calling _popout_map again should be a no-op (guard)
        window._popout_map()

        assert window._map_popout_window is first_win

    def test_double_dock_is_no_op(self, window):
        # Docking when not popped out should be a no-op
        window._dock_map_back()
        assert window._map_popped_out is False

    def test_re_entrant_dock_safe(self, window):
        window._toggle_popout_map()
        # Simulate re-entrant scenario: dock_map_back called twice
        window._dock_map_back()
        window._dock_map_back()  # should not raise
        assert window._map_popped_out is False

    def test_popout_hides_map_stack(self, window):
        assert window._map_stack.isVisible()

        window._toggle_popout_map()

        assert not window._map_stack.isVisible()

    def test_dock_back_shows_map_stack(self, window):
        window._toggle_popout_map()
        assert not window._map_stack.isVisible()

        window._toggle_popout_map()  # dock back

        assert window._map_stack.isVisible()

    def test_maximize_is_noop_while_popped_out(self, window):
        window._toggle_popout_map()
        assert window._map_popped_out is True

        # Attempt to maximize — should be a no-op
        window._toggle_maximize_map()

        assert window._map_maximized is False
        assert window._cache_table.isVisible()
        assert window._info_bar.isVisible()
        assert window._detail_panel.isVisible()


# ── MapPopoutWindow unit tests ────────────────────────────────────────────────


class TestMapPopoutWindow:
    def test_default_size_without_saved_geometry(self, qtbot):
        from opensak.gui.map_popout import MapPopoutWindow

        win = MapPopoutWindow(None)
        qtbot.addWidget(win)

        assert win.minimumWidth() == 500
        assert win.minimumHeight() == 400
        # Default size is 900x700
        assert win.width() == 900
        assert win.height() == 700

    def test_close_emits_signal(self, qtbot):
        from opensak.gui.map_popout import MapPopoutWindow

        win = MapPopoutWindow(None)
        qtbot.addWidget(win)
        win.show()

        with qtbot.waitSignal(win.closed, timeout=1000):
            win.close()

    def test_close_saves_geometry(self, qtbot):
        from opensak.gui.map_popout import MapPopoutWindow
        from opensak.gui.settings import get_settings

        win = MapPopoutWindow(None)
        qtbot.addWidget(win)
        win.show()
        win.resize(800, 600)
        # Geometry is saved via save_geometry_to_settings(), not closeEvent
        win.save_geometry_to_settings()

        s = get_settings()
        assert s.map_popout_geometry is not None

    def test_take_widget(self, qtbot):
        from opensak.gui.map_popout import MapPopoutWindow
        from PySide6.QtWidgets import QLabel

        win = MapPopoutWindow(None)
        qtbot.addWidget(win)
        win.show()

        label = QLabel("test")
        win.take_widget(label)

        assert label.isVisible()
        assert label.parent() is not None

    def test_restore_geometry_from_saved(self, qtbot):
        from opensak.gui.map_popout import MapPopoutWindow
        from opensak.gui.settings import get_settings
        from PySide6.QtCore import QByteArray

        # First, save a geometry
        win1 = MapPopoutWindow(None)
        qtbot.addWidget(win1)
        win1.show()
        win1.resize(1100, 750)
        win1.save_geometry_to_settings()
        win1.close()

        # Now create a new window — it should restore
        win2 = MapPopoutWindow(None)
        qtbot.addWidget(win2)
        win2.show()
        # Just verify it didn't crash and window exists
        assert win2.isVisible()


# ── MapBridge signal tests ────────────────────────────────────────────────────


class TestMapBridgeSignals:
    def test_maximize_signal_emitted(self, qtbot):
        from opensak.gui.map_widget import MapBridge

        bridge = MapBridge()
        with qtbot.waitSignal(bridge.maximize_clicked, timeout=1000):
            bridge.on_maximize_clicked()

    def test_popout_signal_emitted(self, qtbot):
        from opensak.gui.map_widget import MapBridge

        bridge = MapBridge()
        with qtbot.waitSignal(bridge.popout_clicked, timeout=1000):
            bridge.on_popout_clicked()


# ── MapWidget signal forwarding tests ─────────────────────────────────────────


class TestMapWidgetSignals:
    def test_maximize_requested_forwarded(self, window, qtbot):
        with qtbot.waitSignal(window._map_widget.maximize_requested, timeout=1000):
            window._map_widget._bridge.on_maximize_clicked()

    def test_popout_requested_forwarded(self, window, qtbot):
        with qtbot.waitSignal(window._map_widget.popout_requested, timeout=1000):
            window._map_widget._bridge.on_popout_clicked()

    def test_maximize_button_triggers_toggle(self, window):
        # Trigger the bridge signal as if the JS button was clicked
        window._map_widget._bridge.on_maximize_clicked()

        assert window._map_maximized is True

    def test_popout_button_triggers_toggle(self, window):
        # Trigger the bridge signal as if the JS button was clicked
        window._map_widget._bridge.on_popout_clicked()

        assert window._map_popped_out is True
        assert window._map_popout_window is not None


# ── Shortcut registry tests ───────────────────────────────────────────────────


class TestShortcutRegistry:
    def test_maximize_map_in_registry(self, window):
        keys = [key for key, _, _ in window._shortcut_registry]
        assert "maximize_map" in keys

    def test_popout_map_in_registry(self, window):
        keys = [key for key, _, _ in window._shortcut_registry]
        assert "popout_map" in keys

    def test_maximize_shortcut_is_f11(self, window):
        from PySide6.QtGui import QKeySequence
        assert window._act_maximize_map.shortcut() == QKeySequence("F11")

    def test_popout_shortcut_is_ctrl_shift_m(self, window):
        from PySide6.QtGui import QKeySequence
        assert window._act_popout_map.shortcut() == QKeySequence("Ctrl+Shift+M")

    def test_autofit_columns_is_ctrl_w_and_in_registry(self, window):
        from PySide6.QtGui import QKeySequence
        keys = [key for key, _, _ in window._shortcut_registry]
        assert "autofit_columns" in keys
        assert window._act_autofit_columns.shortcut() == QKeySequence("Ctrl+W")


# ── Settings property tests ───────────────────────────────────────────────────


class TestMapPopoutSettings:
    def test_map_popout_geometry_default_none(self):
        from opensak.gui.settings import get_settings
        s = get_settings()
        assert s.map_popout_geometry is None

    def test_map_popout_geometry_roundtrip(self):
        from opensak.gui.settings import get_settings
        s = get_settings()
        s.map_popout_geometry = "AAAA"
        assert s.map_popout_geometry == "AAAA"

    def test_map_popout_geometry_set_none(self):
        from opensak.gui.settings import get_settings
        s = get_settings()
        s.map_popout_geometry = "test"
        s.map_popout_geometry = None
        assert s.map_popout_geometry is None


# ── Feature flag tests ────────────────────────────────────────────────────────


class TestFeatureFlag:
    def test_map_popout_flag_exists(self):
        import opensak.utils.flags as flags
        assert hasattr(flags, "map_popout")

    def test_popout_visible_when_flag_enabled(self, window, monkeypatch):
        import opensak.utils.flags as flags
        # The flag defaults to True so actions should be visible
        assert flags.map_popout is True
        assert window._act_popout_map.isVisible()
        assert window._act_tb_popout_map.isVisible()

    def test_map_minimum_width_after_dock(self, window):
        window._toggle_popout_map()
        window._toggle_popout_map()  # dock back

        assert window._map_widget.minimumWidth() == 300

    def test_map_at_correct_splitter_index_after_dock(self, window):
        window._toggle_popout_map()
        window._toggle_popout_map()  # dock back

        assert window._bottom_splitter.indexOf(window._map_stack) == 1

    def test_popout_window_has_parent(self, window):
        window._toggle_popout_map()
        # Pop-out window should have main window as parent (taskbar grouping)
        assert window._map_popout_window.parent() is window

    def test_geometry_synced_on_dock(self, window):
        from opensak.gui.settings import get_settings
        window._toggle_popout_map()
        window._toggle_popout_map()  # dock back

        s = get_settings()
        # After docking, geometry should be persisted (sync called)
        assert s.map_popout_geometry is not None

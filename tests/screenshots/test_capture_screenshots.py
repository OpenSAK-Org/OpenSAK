# tests/screenshots/test_capture_screenshots.py — capture marketing/doc screenshots.
#
# NOT part of the normal test sweep. Run explicitly:
#
#   xvfb-run -a --server-args="-screen 0 1440x1024x24" pytest tests/screenshots -v
#
# (or just `pytest tests/screenshots -v` if you already have a display, e.g. a
# local desktop session). PNGs land in site/assets/screenshots/ by default —
# override the destination with OPENSAK_SCREENSHOT_DIR.
#
# `pytest -v tests/` does NOT pick these up automatically — see norecursedirs
# in pyproject.toml.

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

pytestmark = pytest.mark.gui


def _grab(widget, path: Path) -> None:
    pixmap = widget.grab()
    ok = pixmap.save(str(path), "PNG")
    assert ok, f"Failed to save screenshot to {path}"


def test_main_window(qtbot, demo_window, screenshot_dir):
    # Select a cache so the detail panel + map aren't just empty placeholders.
    model = demo_window._cache_table._model
    for row in range(demo_window._cache_table.row_count()):
        cache = model.cache_at(row)
        if cache is not None and cache.gc_code == "GC1A001":
            demo_window._cache_table.selectRow(row)
            break
    qtbot.wait(2500)  # let the map tiles + icons finish rendering
    _grab(demo_window, screenshot_dir / "main-window.png")


def test_filter_dialog(qtbot, demo_window, screenshot_dir):
    from opensak.gui.dialogs.filter_dialog import FilterDialog

    dlg = FilterDialog(demo_window, None, "")
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    qtbot.wait(150)
    _grab(dlg, screenshot_dir / "filter-dialog-general.png")

    dlg._tabs.setCurrentIndex(3)  # Attributes tab
    qtbot.wait(150)
    _grab(dlg, screenshot_dir / "filter-dialog-attributes.png")
    dlg.close()


def test_cache_detail_hint(qtbot, demo_window, screenshot_dir):
    # GC1A002 ("The Hollow Oak") has a ROT13-encoded hint — pick it explicitly
    # rather than relying on row 0, since list order isn't guaranteed.
    model = demo_window._cache_table._model
    target_row = None
    for row in range(demo_window._cache_table.row_count()):
        cache = model.cache_at(row)
        if cache is not None and cache.gc_code == "GC1A002":
            target_row = row
            break
    assert target_row is not None, "Demo cache GC1A002 not found in list"

    demo_window._cache_table.selectRow(target_row)
    qtbot.wait(200)

    panel = demo_window._detail_panel
    panel._tabs.setCurrentIndex(1)  # Hint tab
    qtbot.wait(100)
    panel._decode_btn.click()
    qtbot.wait(100)
    _grab(panel, screenshot_dir / "cache-detail-hint.png")


def test_settings_dialog(qtbot, demo_window, screenshot_dir):
    from opensak.gui.dialogs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(demo_window)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    qtbot.wait(150)
    _grab(dlg, screenshot_dir / "settings-general.png")
    dlg.close()


def test_gps_export_dialog(qtbot, demo_window, screenshot_dir):
    from opensak.gui.dialogs.gps_dialog import GpsExportDialog

    caches = [
        demo_window._cache_table._model.cache_at(i)
        for i in range(demo_window._cache_table.row_count())
    ]
    caches = [c for c in caches if c is not None]

    dlg = GpsExportDialog(demo_window, caches=caches)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    qtbot.wait(150)
    _grab(dlg, screenshot_dir / "gps-export.png")
    dlg.close()


def test_column_chooser_dialog(qtbot, demo_window, screenshot_dir):
    from opensak.gui.dialogs.column_dialog import ColumnChooserDialog

    dlg = ColumnChooserDialog(demo_window)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    qtbot.wait(150)
    _grab(dlg, screenshot_dir / "column-chooser.png")
    dlg.close()

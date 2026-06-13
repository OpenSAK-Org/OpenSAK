"""tests/e2e-tests/test_e2e_trip_planner.py — trip planner dialog scenarios."""

import pytest

pytest.importorskip("pytestqt")


# ── Dialog open / structure ────────────────────────────────────────────────────


def test_trip_planner_opens_from_main_window(seeded_window, qtbot):
    """Triggering _open_trip_planner shows a non-modal TripPlannerDialog."""
    window = seeded_window

    window._open_trip_planner()
    qtbot.wait(100)

    dlg = window._trip_planner_win
    assert dlg is not None
    assert dlg.isVisible()

    dlg.close()
    qtbot.wait(50)


def test_trip_planner_has_radius_and_route_tabs(seeded_window, qtbot):
    """The dialog exposes two tabs: Radius and Route."""
    window = seeded_window
    window._open_trip_planner()
    qtbot.wait(100)

    dlg = window._trip_planner_win
    assert dlg._tabs.count() == 2

    dlg.close()


def test_trip_planner_receives_all_caches(seeded_window, qtbot):
    """The trip planner is initialised with the full cache list from the table (4 seeded caches)."""
    window = seeded_window
    window._open_trip_planner()
    qtbot.wait(100)

    dlg = window._trip_planner_win
    assert len(dlg._all_caches) == 4

    dlg.close()


def test_trip_planner_route_tab_switches(seeded_window, qtbot):
    """Switching to the Route tab does not crash."""
    window = seeded_window
    window._open_trip_planner()
    qtbot.wait(100)

    dlg = window._trip_planner_win
    dlg._tabs.setCurrentIndex(1)
    qtbot.wait(50)

    assert dlg._tabs.currentIndex() == 1
    dlg.close()

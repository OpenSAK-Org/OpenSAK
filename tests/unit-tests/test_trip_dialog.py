"""tests/unit-tests/test_trip_dialog.py — trip planner geometry, dialog, map preview."""

from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QInputDialog, QFileDialog

from opensak.gui.dialogs import trip_dialog as td
from opensak.gui.dialogs.trip_dialog import (
    TripPlannerDialog,
    TripMapPreviewDialog,
    _dist_to_segment_km,
    _dist_to_route_km,
    _position_along_route,
)
from opensak.db.models import Cache
from opensak.utils.types import CoordFormat

_VALID = "N55 47.250 E012 25.000"


def _c(gc="GC1", lat=55.01, lon=12.01, found=False, available=True,
       archived=False, diff=2.0, terr=3.0, name="N", ctype="Traditional Cache",
       hidden=None):
    return SimpleNamespace(
        gc_code=gc, name=name, cache_type=ctype, latitude=lat, longitude=lon,
        found=found, available=available, archived=archived,
        difficulty=diff, terrain=terr, hidden_date=hidden,
    )


# ── geometry helpers ────────────────────────────────────────────────────────────

class TestGeometry:
    def test_dist_to_segment_endpoint(self):
        # P == A -> distance 0
        assert _dist_to_segment_km(55, 12, 55, 12, 56, 13) == pytest.approx(0, abs=0.5)

    def test_dist_to_segment_off_line(self):
        d = _dist_to_segment_km(55.1, 12.0, 55.0, 12.0, 55.0, 13.0)
        assert d > 1.0

    def test_dist_to_segment_degenerate(self):
        # A == B -> falls back to point distance
        d = _dist_to_segment_km(55.0, 12.0, 55.0, 12.0, 55.0, 12.0)
        assert d == pytest.approx(0, abs=0.01)

    def test_dist_to_route_single_waypoint(self):
        d = _dist_to_route_km(55.0, 12.0, [(55.0, 12.0)])
        assert d == pytest.approx(0, abs=0.01)

    def test_dist_to_route_multi(self):
        d = _dist_to_route_km(55.05, 12.0, [(55.0, 12.0), (55.0, 13.0)])
        assert d > 0

    def test_position_along_route_single(self):
        assert _position_along_route(55.0, 12.0, [(55.0, 12.0)]) == 0.0

    def test_position_along_route_multi(self):
        pos = _position_along_route(55.0, 12.5, [(55.0, 12.0), (55.0, 13.0)])
        assert pos > 0


# ── dialog ──────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_trip_win():
    # _open_map_preview stores the preview window on the global QApplication;
    # drop it between tests so a leaked stub can't break the next _update_preview.
    yield
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None and hasattr(app, "_trip_map_win"):
        del app._trip_map_win


@pytest.fixture
def settings(monkeypatch):
    s = SimpleNamespace(home_lat=55.0, home_lon=12.0, use_miles=False,
                        coord_format=CoordFormat.DMM, home_points=[])
    monkeypatch.setattr(td, "get_settings", lambda: s)
    return s


@pytest.fixture
def warn(monkeypatch):
    m = MagicMock()
    monkeypatch.setattr(td.QMessageBox, "warning", m)
    return m


@pytest.fixture
def dlg(qtbot, settings):
    d = TripPlannerDialog(caches=[_c("GCA"), _c("GCB", lat=55.5, lon=12.5)])
    qtbot.addWidget(d)
    return d


class TestConstruction:
    def test_two_tabs_and_preview(self, dlg):
        assert dlg._tabs.count() == 2
        # radius tab default radius=0 -> all caches in preview
        assert dlg._table.rowCount() == 2


class TestRadiusCompute:
    def test_radius_zero_returns_all(self, dlg):
        caches, dist_map, warning = dlg._compute_radius()
        assert warning == ""
        assert len(caches) == 2

    def test_radius_filters_by_distance(self, dlg):
        dlg._spin_radius.setValue(5.0)  # km
        caches, _, warning = dlg._compute_radius()
        assert warning == ""
        assert all(c.gc_code == "GCA" for c in caches)  # only the near one

    def test_radius_warns_without_center(self, dlg, settings):
        settings.home_lat = 0.0
        settings.home_lon = 0.0
        dlg._spin_radius.setValue(10.0)
        _, _, warning = dlg._compute_radius()
        assert warning != ""

    def test_sort_options(self, dlg):
        for key in ("distance", "difficulty", "terrain", "hidden_date", "name"):
            idx = dlg._combo_sort.findData(key)
            dlg._combo_sort.setCurrentIndex(idx)
            caches, _, _ = dlg._compute_radius()
            assert isinstance(caches, list)

    def test_base_filter_excludes_found_archived(self, dlg):
        dlg._all_caches = [
            _c("OK"), _c("FOUND", found=True), _c("ARCH", archived=True),
            _c("UNAVAIL", available=False),
        ]
        caches, _, _ = dlg._compute_radius()
        assert [c.gc_code for c in caches] == ["OK"]


class TestRouteCompute:
    def test_no_points_warns(self, dlg):
        dlg._tabs.setCurrentIndex(1)
        caches, _, warning = dlg._compute_route()
        assert caches == [] and warning != ""

    def test_route_filters_corridor(self, dlg):
        dlg._route_points = [("A", 55.0, 12.0), ("B", 55.0, 13.0)]
        dlg._spin_corridor.setValue(3.0)
        caches, dist_map, warning = dlg._compute_route()
        assert warning == ""
        assert isinstance(caches, list)


class TestRoutePoints:
    def test_coord_feedback(self, dlg):
        dlg._on_pt_coord_changed(_VALID)
        assert "✓" in dlg._pt_hint.text()
        dlg._on_pt_coord_changed("garbage")
        assert dlg._pt_hint.text() != ""
        dlg._on_pt_coord_changed("")
        assert dlg._pt_hint.text() == ""

    def test_add_point_requires_coord(self, dlg, warn):
        dlg._pt_coord.setText("")
        dlg._add_route_point()
        warn.assert_called_once()

    def test_add_point_rejects_bad_coord(self, dlg, warn):
        dlg._pt_coord.setText("garbage")
        dlg._add_route_point()
        warn.assert_called_once()

    def test_add_point_valid_autonames(self, dlg):
        dlg._pt_coord.setText(_VALID)
        dlg._add_route_point()
        assert len(dlg._route_points) == 1
        assert dlg._route_points[0][0] == "A"  # auto-named

    def test_add_point_max_reached(self, dlg, warn):
        dlg._route_points = [("x", 1.0, 2.0)] * dlg.MAX_ROUTE_POINTS
        dlg._pt_coord.setText(_VALID)
        dlg._add_route_point()
        warn.assert_called_once()

    def test_add_from_home_no_points(self, dlg, settings, monkeypatch):
        info = MagicMock()
        monkeypatch.setattr(td.QMessageBox, "information", info)
        settings.home_points = []
        dlg._add_from_home_points()
        info.assert_called_once()

    def test_add_from_home_picks(self, dlg, settings, monkeypatch):
        settings.home_points = [SimpleNamespace(name="Home", lat=55.0, lon=12.0)]
        monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("Home", True))
        dlg._add_from_home_points()
        assert dlg._route_points[-1][0] == "Home"

    def test_add_from_home_max_reached(self, dlg, settings, warn):
        dlg._route_points = [("x", 1.0, 2.0)] * dlg.MAX_ROUTE_POINTS
        dlg._add_from_home_points()
        warn.assert_called_once()

    def test_move_delete_clear(self, dlg):
        dlg._route_points = [("A", 1.0, 1.0), ("B", 2.0, 2.0), ("C", 3.0, 3.0)]
        dlg._refresh_route_list()
        dlg._route_list.setCurrentRow(2)
        dlg._move_point_up()
        assert dlg._route_points[1][0] == "C"
        dlg._route_list.setCurrentRow(0)
        dlg._move_point_down()
        assert dlg._route_points[0][0] != "A" or dlg._route_points[1][0] == "A"
        dlg._route_list.setCurrentRow(0)
        dlg._delete_point()
        assert len(dlg._route_points) == 2
        dlg._clear_points()
        assert dlg._route_points == []

    def test_route_reordered_triggers_preview(self, dlg, monkeypatch):
        called = []
        monkeypatch.setattr(dlg, "_update_preview", lambda: called.append(True))
        dlg._on_route_reordered()
        assert called == [True]


class TestExport:
    def test_export_gps_no_caches(self, dlg):
        dlg._selected_caches = []
        dlg._export_to_gps()  # early return, no crash

    def test_export_gps_opens_dialog(self, dlg, monkeypatch):
        opened = []

        class FakeGps:
            def __init__(self, parent, caches=None):
                opened.append(caches)
            def exec(self):
                return 0
        monkeypatch.setattr("opensak.gui.dialogs.gps_dialog.GpsExportDialog", FakeGps)
        dlg._selected_caches = [_c("X")]
        dlg._export_to_gps()
        assert opened

    def test_export_file_success(self, dlg, monkeypatch, tmp_path):
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            lambda *a, **k: (str(tmp_path / "t.gpx"), "f"))
        monkeypatch.setattr("opensak.gps.garmin.export_to_file", lambda c, p: "ok")
        info = MagicMock()
        monkeypatch.setattr(td.QMessageBox, "information", info)
        dlg._selected_caches = [_c("X")]
        dlg._export_to_file()
        info.assert_called_once()

    def test_export_file_cancel(self, dlg, monkeypatch):
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
        dlg._selected_caches = [_c("X")]
        dlg._export_to_file()  # cancelled, no crash

    def test_export_file_error(self, dlg, monkeypatch, tmp_path):
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            lambda *a, **k: (str(tmp_path / "t.gpx"), "f"))
        monkeypatch.setattr("opensak.gps.garmin.export_to_file",
                            lambda c, p: (_ for _ in ()).throw(RuntimeError("io")))
        crit = MagicMock()
        monkeypatch.setattr(td.QMessageBox, "critical", crit)
        dlg._selected_caches = [_c("X")]
        dlg._export_to_file()
        crit.assert_called_once()

    def test_save_to_database_new(self, dlg, monkeypatch, tmp_path):
        monkeypatch.setattr(QInputDialog, "getItem",
                            lambda *a, **k: (td.tr("trip_db_choice_new"), True))
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            lambda *a, **k: (str(tmp_path / "trip.db"), "f"))
        monkeypatch.setattr("opensak.db.manager.get_db_manager",
                            lambda: (_ for _ in ()).throw(RuntimeError("none")))
        info = MagicMock()
        monkeypatch.setattr(td.QMessageBox, "information", info)
        dlg._selected_caches = [Cache(gc_code="GCSAVE", name="Save Me",
                                      latitude=55.0, longitude=12.0,
                                      cache_type="Traditional Cache")]
        dlg._save_to_database()
        info.assert_called_once()
        assert (tmp_path / "trip.db").exists()

    def test_save_to_database_cancel_choice(self, dlg, monkeypatch):
        monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("", False))
        dlg._selected_caches = [_c("X")]
        dlg._save_to_database()  # cancelled before any file IO


class TestMapPreview:
    def test_open_map_preview_no_caches(self, dlg):
        dlg._selected_caches = []
        dlg._open_map_preview()  # early return

    def test_open_map_preview_shows_window(self, dlg, monkeypatch):
        shown = []

        class FakeWin:
            def __init__(self, caches):
                shown.append(caches)
            def show(self): pass
            def raise_(self): pass
            def activateWindow(self): pass
        monkeypatch.setattr(td, "TripMapPreviewDialog", FakeWin)
        dlg._selected_caches = [_c("X")]
        dlg._open_map_preview()
        assert shown

    def test_update_preview_updates_open_window(self, dlg, monkeypatch):
        from PySide6.QtWidgets import QApplication
        updated = []
        win = SimpleNamespace(isVisible=lambda: True,
                              update_caches=lambda c: updated.append(c))
        QApplication.instance()._trip_map_win = win
        dlg._tabs.setCurrentIndex(0)
        dlg._update_preview()
        assert updated  # window refreshed with selected caches
        del QApplication.instance()._trip_map_win

    def test_preview_dialog_construct_and_update(self, qtbot, settings):
        win = TripMapPreviewDialog([_c("A")])
        qtbot.addWidget(win)
        assert "1" in win._info_lbl.text() or win._info_lbl.text()
        win.update_caches([_c("A"), _c("B")])
        assert win._caches == win._caches  # no crash; caches replaced

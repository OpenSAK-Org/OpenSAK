# tests/unit-tests/test_waypoint_dialog.py — add/edit cache & custom-waypoint dialog.

from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QDialog

from opensak.gui.dialogs import waypoint_dialog as wpd
from opensak.gui.dialogs.waypoint_dialog import WaypointDialog
from opensak.utils.types import CoordFormat

_VALID = "N55 47.250 E012 25.000"


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    m = MagicMock()
    m.coord_format = CoordFormat.DMM
    monkeypatch.setattr(wpd, "get_settings", lambda: m)
    return m


@pytest.fixture
def warn(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(wpd.QMessageBox, "warning", mock)
    return mock


def _cache(gc_code="GC12345", cache_type="Traditional Cache"):
    return SimpleNamespace(
        gc_code=gc_code, name="Edit Me", cache_type=cache_type, container="Small",
        parent_gc_code=None, difficulty=2.5, terrain=3.0,
        latitude=55.0, longitude=12.0, placed_by="Owner", country="Denmark",
        state="Zealand", short_description="s", long_description="l",
        encoded_hints="hint", available=True, archived=False, premium_only=False,
        locked=False,
        found=True, dnf=False, first_to_find=False,
    )


# ── construction / mode ───────────────────────────────────────────────────────

class TestConstruction:
    def test_add_mode_defaults_to_geocache(self, qtbot):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        assert dlg._is_custom is False
        assert dlg._gc_code.isVisible() or not dlg.isVisible()

    def test_edit_geocache_populates_and_locks_mode(self, qtbot):
        dlg = WaypointDialog(cache=_cache())
        qtbot.addWidget(dlg)
        assert dlg._is_edit is True
        assert dlg._gc_code.text() == "GC12345"
        assert dlg._name.text() == "Edit Me"
        assert dlg._radio_geocache.isEnabled() is False

    def test_edit_custom_when_gc_not_gc_prefixed(self, qtbot):
        dlg = WaypointDialog(cache=_cache(gc_code="CW001", cache_type="Parking Area"))
        qtbot.addWidget(dlg)
        assert dlg._is_custom is True
        assert dlg._cw_id.text() == "CW001"

    def test_mode_switch_to_custom(self, qtbot):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._radio_custom.setChecked(True)
        dlg._on_mode_changed(None)
        assert dlg._is_custom is True


# ── input feedback ────────────────────────────────────────────────────────────

class TestInputFeedback:
    def test_coord_valid(self, qtbot):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._coord_input.setText(_VALID)
        assert dlg._parsed_lat is not None
        assert "✓" in dlg._coord_feedback.text()

    def test_coord_invalid(self, qtbot):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._coord_input.setText("nonsense")
        assert dlg._parsed_lat is None
        assert dlg._coord_feedback.text() != ""

    def test_coord_cleared(self, qtbot):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._coord_input.setText(_VALID)
        dlg._coord_input.setText("")
        assert dlg._parsed_lat is None
        assert dlg._coord_feedback.text() == ""

    def test_parent_gc_valid_and_invalid(self, qtbot):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._parent_gc.setText("ABC")
        assert dlg._parent_gc_feedback.text() != ""
        dlg._parent_gc.setText("GC999")
        assert dlg._parent_gc_feedback.text() == ""
        dlg._parent_gc.setText("")
        assert dlg._parent_gc_feedback.text() == ""


# ── validation ────────────────────────────────────────────────────────────────

class TestValidation:
    def test_name_required(self, qtbot, warn):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._validate_and_accept()
        warn.assert_called_once()
        assert dlg.result() != QDialog.DialogCode.Accepted

    def test_bad_coord_blocks_accept(self, qtbot, warn):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._name.setText("X")
        dlg._coord_input.setText("garbage")
        dlg._validate_and_accept()
        warn.assert_called_once()

    def test_geocache_gc_required(self, qtbot, warn):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._name.setText("X")
        dlg._validate_and_accept()
        warn.assert_called_once()

    def test_geocache_gc_invalid(self, qtbot, warn):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._name.setText("X")
        dlg._gc_code.setText("XYZ123")
        dlg._validate_and_accept()
        warn.assert_called_once()

    def test_geocache_invalid_dt(self, qtbot, warn):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._name.setText("X")
        dlg._gc_code.setText("GC123")
        dlg._difficulty.setValue(1.3)  # not a valid 0.5-step value
        dlg._validate_and_accept()
        warn.assert_called_once()

    def test_geocache_invalid_terrain(self, qtbot, warn):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._name.setText("X")
        dlg._gc_code.setText("GC123")
        dlg._difficulty.setValue(1.5)  # valid
        dlg._terrain.setValue(1.3)     # invalid -> terrain branch
        dlg._validate_and_accept()
        warn.assert_called_once()

    def test_geocache_valid_accepts(self, qtbot, warn):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._name.setText("Good")
        dlg._gc_code.setText("GC123")
        dlg._coord_input.setText(_VALID)
        dlg._validate_and_accept()
        warn.assert_not_called()
        assert dlg.result() == QDialog.DialogCode.Accepted

    def test_custom_invalid_parent(self, qtbot, warn):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._radio_custom.setChecked(True)
        dlg._on_mode_changed(None)
        dlg._name.setText("WP")
        dlg._parent_gc.setText("ABC")
        dlg._validate_and_accept()
        warn.assert_called_once()

    def test_custom_valid_accepts(self, qtbot, warn):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._radio_custom.setChecked(True)
        dlg._on_mode_changed(None)
        dlg._name.setText("WP")
        dlg._parent_gc.setText("GC999")
        dlg._validate_and_accept()
        warn.assert_not_called()
        assert dlg.result() == QDialog.DialogCode.Accepted


# ── data extraction ───────────────────────────────────────────────────────────

class TestGetData:
    def test_geocache_data(self, qtbot):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._name.setText("My Cache")
        dlg._gc_code.setText("gc123")
        dlg._coord_input.setText(_VALID)
        dlg._found.setChecked(True)
        data = dlg.get_data()
        assert data["gc_code"] == "GC123"
        assert data["name"] == "My Cache"
        assert data["found"] is True
        assert data["latitude"] is not None
        assert data["parent_gc_code"] is None

    def test_locked_checkbox_populates_and_roundtrips(self, qtbot):
        # Issue #202: editing an already-locked cache shows the checkbox
        # checked, and toggling it off is reflected in get_data().
        c = _cache()
        c.locked = True
        dlg = WaypointDialog(cache=c)
        qtbot.addWidget(dlg)
        assert dlg._locked.isChecked() is True
        data = dlg.get_data()
        assert data["locked"] is True

        dlg._locked.setChecked(False)
        data = dlg.get_data()
        assert data["locked"] is False

    def test_locked_defaults_unchecked_for_new_cache(self, qtbot):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        assert dlg._locked.isChecked() is False
        data = dlg.get_data()
        assert data["locked"] is False

    def test_custom_data(self, qtbot):
        dlg = WaypointDialog(next_cw_id="CW042")
        qtbot.addWidget(dlg)
        dlg._radio_custom.setChecked(True)
        dlg._on_mode_changed(None)
        dlg._name.setText("Parking")
        dlg._parent_gc.setText("gc999")
        data = dlg.get_data()
        assert data["gc_code"] == "CW042"
        assert data["parent_gc_code"] == "GC999"
        assert data["difficulty"] is None
        assert data["container"] is None

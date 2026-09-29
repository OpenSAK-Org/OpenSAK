# tests/unit-tests/test_corrected_coords_dialog.py — corrected-coords entry dialog.

import pytest
from unittest.mock import MagicMock

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QApplication, QDialog

from opensak.gui.dialogs import corrected_coords_dialog as ccd
from opensak.gui.dialogs.corrected_coords_dialog import CorrectedCoordsDialog
from opensak.utils.types import CoordFormat

_VALID = "N55 47.250 E012 25.000"


@pytest.fixture
def settings(monkeypatch):
    m = MagicMock()
    m.coord_format = CoordFormat.DMM
    monkeypatch.setattr(ccd, "get_settings", lambda: m)
    return m


class TestCorrectedCoordsDialog:
    def test_builds_with_no_coords(self, qtbot, settings):
        dlg = CorrectedCoordsDialog("GC123")
        qtbot.addWidget(dlg)
        assert dlg.get_coords() == (None, None)
        assert dlg._ok_btn.isEnabled() is False

    def test_original_panel_built(self, qtbot, settings):
        dlg = CorrectedCoordsDialog("GC123", orig_lat=55.0, orig_lon=12.0)
        qtbot.addWidget(dlg)
        assert dlg._ok_btn.isEnabled() is False

    def test_prefilled_corrected_enables_ok(self, qtbot, settings):
        dlg = CorrectedCoordsDialog("GC123", corrected_lat=55.0, corrected_lon=12.0)
        qtbot.addWidget(dlg)
        lat, lon = dlg.get_coords()
        assert lat == pytest.approx(55.0, abs=1e-3)
        assert dlg._ok_btn.isEnabled() is True

    def test_valid_input_enables_ok_and_sets_coords(self, qtbot, settings):
        dlg = CorrectedCoordsDialog("GC123")
        qtbot.addWidget(dlg)
        dlg._input.setText(_VALID)
        lat, lon = dlg.get_coords()
        assert lat is not None and lon is not None
        assert dlg._ok_btn.isEnabled() is True

    def test_invalid_input_shows_error(self, qtbot, settings):
        dlg = CorrectedCoordsDialog("GC123")
        qtbot.addWidget(dlg)
        dlg._input.setText("garbage")
        assert dlg.get_coords() == (None, None)
        assert dlg._ok_btn.isEnabled() is False
        assert dlg._error_lbl.text() != ""

    def test_clearing_input_resets(self, qtbot, settings):
        dlg = CorrectedCoordsDialog("GC123")
        qtbot.addWidget(dlg)
        dlg._input.setText(_VALID)
        dlg._input.setText("")
        assert dlg.get_coords() == (None, None)
        assert dlg._ok_btn.isEnabled() is False

    def test_accept_with_valid_coords(self, qtbot, settings):
        dlg = CorrectedCoordsDialog("GC123")
        qtbot.addWidget(dlg)
        dlg._input.setText(_VALID)
        dlg._on_accept()
        assert dlg.result() == QDialog.DialogCode.Accepted

    def test_accept_without_coords_does_nothing(self, qtbot, settings):
        dlg = CorrectedCoordsDialog("GC123")
        qtbot.addWidget(dlg)
        dlg._on_accept()
        assert dlg.result() != QDialog.DialogCode.Accepted

    def test_copy_sets_clipboard(self, qtbot, settings):
        dlg = CorrectedCoordsDialog("GC123")
        qtbot.addWidget(dlg)
        dlg._copy("hello world")
        assert QApplication.clipboard().text() == "hello world"


class TestOffsetFromOriginal:
    # Distance & bearing from the original to the corrected coordinates.

    @pytest.fixture(autouse=True)
    def english(self):
        from opensak.lang import current_language, load_language
        prev = current_language()
        load_language("en")
        yield
        load_language(prev)

    def _dlg(self, qtbot, **kwargs):
        dlg = CorrectedCoordsDialog("GC123", orig_lat=47.0, orig_lon=8.0, **kwargs)
        qtbot.addWidget(dlg)
        return dlg

    def test_shown_for_valid_input(self, qtbot, settings):
        settings.use_miles = False
        dlg = self._dlg(qtbot)
        dlg._input.setText("47.01, 8.0")  # ~1.112 km due north
        assert dlg._offset_lbl.isVisibleTo(dlg)
        text = dlg._offset_lbl.text()
        assert "1.112 km" in text
        assert "0° N" in text

    def test_bearing_east(self, qtbot, settings):
        settings.use_miles = False
        dlg = self._dlg(qtbot)
        dlg._input.setText("47.0, 8.001")  # ~76 m due east
        assert "90° E" in dlg._offset_lbl.text()
        assert " m" in dlg._offset_lbl.text()

    def test_miles_setting(self, qtbot, settings):
        settings.use_miles = True
        dlg = self._dlg(qtbot)
        dlg._input.setText("47.01, 8.0")
        assert "mi" in dlg._offset_lbl.text()
        assert "km" not in dlg._offset_lbl.text()

    def test_same_point_has_no_bearing(self, qtbot, settings):
        settings.use_miles = False
        dlg = self._dlg(qtbot)
        dlg._input.setText("47.0, 8.0")
        assert "0.0 m" in dlg._offset_lbl.text()
        assert "—" in dlg._offset_lbl.text()

    def test_prefilled_shows_offset(self, qtbot, settings):
        settings.use_miles = False
        dlg = self._dlg(qtbot, corrected_lat=47.01, corrected_lon=8.0)
        assert dlg._offset_lbl.isVisibleTo(dlg)

    @pytest.mark.parametrize("text", ["", "not a coordinate"])
    def test_hidden_for_empty_or_invalid_input(self, qtbot, settings, text):
        settings.use_miles = False
        dlg = self._dlg(qtbot)
        dlg._input.setText("47.01, 8.0")
        dlg._input.setText(text)
        assert not dlg._offset_lbl.isVisibleTo(dlg)

    def test_hidden_without_original_coords(self, qtbot, settings):
        settings.use_miles = False
        dlg = CorrectedCoordsDialog("GC123")
        qtbot.addWidget(dlg)
        dlg._input.setText("47.01, 8.0")
        assert not dlg._offset_lbl.isVisibleTo(dlg)


class TestRemoveButton:
    def test_hidden_without_existing_corrected(self, qtbot, settings):
        dlg = CorrectedCoordsDialog("GC123", orig_lat=47.0, orig_lon=8.0)
        qtbot.addWidget(dlg)
        assert not dlg._remove_btn.isVisibleTo(dlg)

    def test_shown_with_existing_corrected(self, qtbot, settings):
        dlg = CorrectedCoordsDialog(
            "GC123", orig_lat=47.0, orig_lon=8.0,
            corrected_lat=47.01, corrected_lon=8.0,
        )
        qtbot.addWidget(dlg)
        assert dlg._remove_btn.isVisibleTo(dlg)

    def test_remove_accepts_with_no_coords(self, qtbot, settings):
        dlg = CorrectedCoordsDialog(
            "GC123", corrected_lat=47.01, corrected_lon=8.0,
        )
        qtbot.addWidget(dlg)
        assert dlg.get_coords() != (None, None)
        dlg._remove_btn.click()
        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.get_coords() == (None, None)

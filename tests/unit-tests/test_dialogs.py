"""tests/unit-tests/test_dialogs.py — pure-logic dialog tests (requires pytest-qt)."""

import pytest
from unittest.mock import MagicMock, patch

pytest.importorskip("pytestqt")

from opensak.gui.dialogs.checksum_dialog import (
    ChecksumDialog,
    _extract_digits,
    _split_hemispheres,
)
from opensak.gui.dialogs.coord_converter_dialog import CoordConverterDialog
from opensak.gui.dialogs.distance_bearing_dialog import DistanceBearingDialog
from opensak.gui.dialogs.midpoint_dialog import MidpointDialog
from opensak.gui.dialogs.projection_dialog import ProjectionDialog


# ── Shared fixture ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def stub_settings():
    """Prevent real QSettings access in dialogs that call get_settings()."""
    mock = MagicMock()
    mock.use_miles = False
    with (
        patch(
            "opensak.gui.dialogs.distance_bearing_dialog.get_settings",
            return_value=mock,
        ),
        patch(
            "opensak.gui.dialogs.projection_dialog.get_settings",
            return_value=mock,
        ),
    ):
        yield mock


# ── Pure-logic helpers (no Qt needed) ────────────────────────────────────────


class TestExtractDigits:
    def test_extracts_digits_from_coord_string(self):
        assert _extract_digits("N55 47.250") == [5, 5, 4, 7, 2, 5, 0]

    def test_empty_string_returns_empty_list(self):
        assert _extract_digits("") == []

    def test_no_digits_returns_empty_list(self):
        assert _extract_digits("NE") == []

    def test_all_digits_in_mixed_string(self):
        assert _extract_digits("abc123def456") == [1, 2, 3, 4, 5, 6]

    def test_leading_zeros_preserved(self):
        assert _extract_digits("E012") == [0, 1, 2]


class TestSplitHemispheres:
    def test_splits_on_uppercase_e(self):
        ns, ew = _split_hemispheres("N55 47.250 E012 25.000")
        assert ns.startswith("N")
        assert ew.startswith("E")

    def test_splits_on_uppercase_w(self):
        _, ew = _split_hemispheres("N55 47.250 W012 25.000")
        assert ew.startswith("W")

    def test_dd_format_splits_on_comma(self):
        ns, ew = _split_hemispheres("55.7875, 12.4167")
        assert "55" in ns
        assert "12" in ew

    def test_single_value_returns_full_and_empty(self):
        ns, ew = _split_hemispheres("55.7875")
        assert ns == "55.7875"
        assert ew == ""


# ── CoordConverterDialog ──────────────────────────────────────────────────────


@pytest.mark.gui
class TestCoordConverterDialog:
    def test_opens_without_prefill(self, qtbot):
        dlg = CoordConverterDialog()
        qtbot.addWidget(dlg)
        assert dlg._input.text() == ""
        assert dlg._dmm_row[1].text() == ""

    def test_prefill_populates_input(self, qtbot):
        dlg = CoordConverterDialog(lat=55.0, lon=12.0)
        qtbot.addWidget(dlg)
        assert "55" in dlg._input.text()

    def test_valid_input_fills_all_output_formats(self, qtbot):
        dlg = CoordConverterDialog()
        qtbot.addWidget(dlg)
        dlg._input.setText("N55 47.250 E012 25.000")
        assert dlg._dmm_row[1].text() != ""
        assert dlg._dms_row[1].text() != ""
        assert dlg._dd_row[1].text() != ""

    def test_invalid_input_clears_outputs_and_shows_error(self, qtbot):
        dlg = CoordConverterDialog(lat=55.0, lon=12.0)
        qtbot.addWidget(dlg)
        dlg._input.setText("not a coordinate")
        assert dlg._dmm_row[1].text() == ""
        assert dlg._error_lbl.text() != ""

    def test_copy_buttons_enabled_after_valid_input(self, qtbot):
        dlg = CoordConverterDialog()
        qtbot.addWidget(dlg)
        dlg._input.setText("N55 47.250 E012 25.000")
        for _, _, btn in (dlg._dmm_row, dlg._dms_row, dlg._dd_row):
            assert btn.isEnabled()

    def test_map_buttons_enabled_after_valid_input(self, qtbot):
        dlg = CoordConverterDialog()
        qtbot.addWidget(dlg)
        dlg._input.setText("N55 47.250 E012 25.000")
        assert dlg._osm_btn.isEnabled()
        assert dlg._gmaps_btn.isEnabled()

    def test_map_buttons_disabled_on_empty_input(self, qtbot):
        dlg = CoordConverterDialog()
        qtbot.addWidget(dlg)
        assert not dlg._osm_btn.isEnabled()
        assert not dlg._gmaps_btn.isEnabled()

    def test_error_cleared_on_empty_input(self, qtbot):
        dlg = CoordConverterDialog()
        qtbot.addWidget(dlg)
        dlg._input.setText("bad")
        dlg._input.clear()
        assert dlg._error_lbl.text() == ""


# ── ChecksumDialog ────────────────────────────────────────────────────────────


@pytest.mark.gui
class TestChecksumDialog:
    def test_opens_with_blank_results(self, qtbot):
        dlg = ChecksumDialog()
        qtbot.addWidget(dlg)
        assert dlg._total_lbl.text() == "—"

    def test_prefill_populates_input(self, qtbot):
        dlg = ChecksumDialog(lat=55.0, lon=12.0)
        qtbot.addWidget(dlg)
        assert dlg._input.text() != ""

    def test_correct_total_for_known_coordinate(self, qtbot):
        # N55 47.250 E012 25.000:
        # digits: 5,5,4,7,2,5,0, 0,1,2,2,5,0,0,0  →  sum = 38
        dlg = ChecksumDialog()
        qtbot.addWidget(dlg)
        dlg._input.setText("N55 47.250 E012 25.000")
        assert dlg._total_lbl.text() == "38"

    def test_ns_and_ew_sums_populated(self, qtbot):
        dlg = ChecksumDialog()
        qtbot.addWidget(dlg)
        dlg._input.setText("N55 47.250 E012 25.000")
        assert dlg._ns_lbl.text() != "—"
        assert dlg._ew_lbl.text() != "—"

    def test_digits_label_populated(self, qtbot):
        dlg = ChecksumDialog()
        qtbot.addWidget(dlg)
        dlg._input.setText("N55 47.250 E012 25.000")
        assert dlg._digits_lbl.text() != "—"

    def test_clears_on_empty_input(self, qtbot):
        dlg = ChecksumDialog()
        qtbot.addWidget(dlg)
        dlg._input.setText("N55 47.250 E012 25.000")
        dlg._input.clear()
        assert dlg._total_lbl.text() == "—"


# ── DistanceBearingDialog ─────────────────────────────────────────────────────


@pytest.mark.gui
class TestDistanceBearingDialog:
    def test_opens_with_blank_results(self, qtbot):
        dlg = DistanceBearingDialog()
        qtbot.addWidget(dlg)
        assert dlg._dist_lbl.text() == "—"

    def test_one_input_leaves_results_blank(self, qtbot):
        dlg = DistanceBearingDialog()
        qtbot.addWidget(dlg)
        dlg._input_a.setText("N55 47.250 E012 25.000")
        assert dlg._dist_lbl.text() == "—"

    def test_two_inputs_produce_distance(self, qtbot):
        dlg = DistanceBearingDialog()
        qtbot.addWidget(dlg)
        dlg._input_a.setText("N55 47.250 E012 25.000")
        dlg._input_b.setText("N55 48.000 E012 25.000")
        assert dlg._dist_lbl.text() != "—"

    def test_forward_bearing_contains_degree_symbol(self, qtbot):
        dlg = DistanceBearingDialog()
        qtbot.addWidget(dlg)
        dlg._input_a.setText("N55 47.250 E012 25.000")
        dlg._input_b.setText("N55 48.000 E012 25.000")
        assert "°" in dlg._fwd_lbl.text()

    def test_reverse_bearing_populated(self, qtbot):
        dlg = DistanceBearingDialog()
        qtbot.addWidget(dlg)
        dlg._input_a.setText("N55 47.250 E012 25.000")
        dlg._input_b.setText("N55 48.000 E012 25.000")
        assert dlg._rev_lbl.text() != "—"

    def test_same_point_shows_zero_distance(self, qtbot):
        dlg = DistanceBearingDialog()
        qtbot.addWidget(dlg)
        coord = "N55 47.250 E012 25.000"
        dlg._input_a.setText(coord)
        dlg._input_b.setText(coord)
        assert "0" in dlg._dist_lbl.text()

    def test_invalid_input_shows_error(self, qtbot):
        dlg = DistanceBearingDialog()
        qtbot.addWidget(dlg)
        dlg._input_a.setText("bad input")
        assert dlg._error_a.text() != ""


# ── MidpointDialog ────────────────────────────────────────────────────────────


@pytest.mark.gui
class TestMidpointDialog:
    def test_opens_with_blank_results(self, qtbot):
        dlg = MidpointDialog()
        qtbot.addWidget(dlg)
        assert dlg._dmm_row[1].text() == ""

    def test_one_input_leaves_results_blank(self, qtbot):
        dlg = MidpointDialog()
        qtbot.addWidget(dlg)
        dlg._input_a.setText("N55 47.250 E012 25.000")
        assert dlg._dmm_row[1].text() == ""

    def test_two_inputs_populate_all_formats(self, qtbot):
        dlg = MidpointDialog()
        qtbot.addWidget(dlg)
        dlg._input_a.setText("N55 47.250 E012 25.000")
        dlg._input_b.setText("N55 48.000 E012 26.000")
        assert dlg._dmm_row[1].text() != ""
        assert dlg._dms_row[1].text() != ""
        assert dlg._dd_row[1].text() != ""

    def test_midpoint_of_same_point_equals_input(self, qtbot):
        dlg = MidpointDialog()
        qtbot.addWidget(dlg)
        coord = "N55 47.250 E012 25.000"
        dlg._input_a.setText(coord)
        dlg._input_b.setText(coord)
        assert "55" in dlg._dd_row[1].text()

    def test_map_buttons_enabled_after_calculation(self, qtbot):
        dlg = MidpointDialog()
        qtbot.addWidget(dlg)
        dlg._input_a.setText("N55 47.250 E012 25.000")
        dlg._input_b.setText("N55 48.000 E012 26.000")
        assert dlg._osm_btn.isEnabled()
        assert dlg._gmaps_btn.isEnabled()

    def test_copy_buttons_enabled_after_calculation(self, qtbot):
        dlg = MidpointDialog()
        qtbot.addWidget(dlg)
        dlg._input_a.setText("N55 47.250 E012 25.000")
        dlg._input_b.setText("N55 48.000 E012 26.000")
        for _, _, btn in (dlg._dmm_row, dlg._dms_row, dlg._dd_row):
            assert btn.isEnabled()


# ── ProjectionDialog ──────────────────────────────────────────────────────────


@pytest.mark.gui
class TestProjectionDialog:
    def test_opens_with_blank_results(self, qtbot):
        dlg = ProjectionDialog()
        qtbot.addWidget(dlg)
        assert dlg._dmm_row[1].text() == ""

    def test_zero_distance_leaves_results_blank(self, qtbot):
        dlg = ProjectionDialog()
        qtbot.addWidget(dlg)
        dlg._start_input.setText("N55 47.250 E012 25.000")
        # distance spinbox defaults to 0.0
        assert dlg._dmm_row[1].text() == ""

    def test_valid_inputs_produce_result(self, qtbot):
        dlg = ProjectionDialog()
        qtbot.addWidget(dlg)
        dlg._start_input.setText("N55 47.250 E012 25.000")
        dlg._bearing.setValue(90.0)
        dlg._distance.setValue(1000.0)
        assert dlg._dmm_row[1].text() != ""
        assert dlg._dms_row[1].text() != ""
        assert dlg._dd_row[1].text() != ""

    def test_invalid_start_shows_error(self, qtbot):
        dlg = ProjectionDialog()
        qtbot.addWidget(dlg)
        dlg._start_input.setText("bad input")
        assert dlg._start_error.text() != ""

    def test_map_buttons_enabled_after_projection(self, qtbot):
        dlg = ProjectionDialog()
        qtbot.addWidget(dlg)
        dlg._start_input.setText("N55 47.250 E012 25.000")
        dlg._bearing.setValue(0.0)
        dlg._distance.setValue(500.0)
        assert dlg._osm_btn.isEnabled()
        assert dlg._gmaps_btn.isEnabled()

    def test_northward_projection_increases_latitude(self, qtbot):
        dlg = ProjectionDialog()
        qtbot.addWidget(dlg)
        dlg._start_input.setText("N55 47.250 E012 25.000")
        dlg._bearing.setValue(0.0)    # North
        dlg._distance.setValue(1000.0)
        result_dd = dlg._dd_row[1].text()
        # Result latitude should be greater than 55.787... degrees
        lat_str = result_dd.split(",")[0].strip()
        assert float(lat_str) > 55.787

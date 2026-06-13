"""tests/e2e-tests/test_e2e_tools.py — tool dialog scenarios (outputs update live on input)."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("pytestqt")


@contextmanager
def _patch_settings(module_path: str):
    """Patch get_settings() for dialogs that read use_miles on construction."""
    settings = MagicMock()
    settings.use_miles = False
    with patch(f"{module_path}.get_settings", return_value=settings):
        yield


# ── CoordConverterDialog ───────────────────────────────────────────────────────


def test_coord_converter_prefilled_populates_all_outputs(qtbot):
    """When constructed with lat/lon all three output rows are non-empty."""
    from opensak.gui.dialogs.coord_converter_dialog import CoordConverterDialog

    dlg = CoordConverterDialog(lat=55.6761, lon=12.5683)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)

    assert dlg._dd_row[1].text() != ""
    assert dlg._dmm_row[1].text() != ""
    assert dlg._dms_row[1].text() != ""


def test_coord_converter_live_update_on_typing(qtbot):
    """Typing a valid coordinate string into the input populates the outputs."""
    from opensak.gui.dialogs.coord_converter_dialog import CoordConverterDialog

    dlg = CoordConverterDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)

    dlg._input.setText("N 55° 40.566 E 012° 34.100")
    qtbot.wait(50)

    assert dlg._dmm_row[1].text() != ""
    assert "55" in dlg._dd_row[1].text()


def test_coord_converter_invalid_clears_outputs_and_shows_error(qtbot):
    """Invalid text empties the output fields and shows an error label."""
    from opensak.gui.dialogs.coord_converter_dialog import CoordConverterDialog

    dlg = CoordConverterDialog(lat=55.0, lon=12.0)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)

    dlg._input.setText("not a coordinate")
    qtbot.wait(50)

    assert dlg._dd_row[1].text() == ""
    assert dlg._error_lbl.text() != ""


def test_coord_converter_dd_round_trip(qtbot):
    """The DD output round-trips back to the original coordinates."""
    from opensak.gui.dialogs.coord_converter_dialog import CoordConverterDialog
    from opensak.coords import parse_coords

    lat_in, lon_in = 55.6761, 12.5683
    dlg = CoordConverterDialog(lat=lat_in, lon=lon_in)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)

    dd_text = dlg._dd_row[1].text()
    lat_out, lon_out = parse_coords(dd_text)

    assert abs(lat_out - lat_in) < 0.0001
    assert abs(lon_out - lon_in) < 0.0001


# ── ChecksumDialog ─────────────────────────────────────────────────────────────


def test_checksum_dialog_prefilled_shows_total(qtbot):
    """ChecksumDialog opened with coords immediately shows a digit-sum total."""
    from opensak.gui.dialogs.checksum_dialog import ChecksumDialog

    dlg = ChecksumDialog(lat=55.6761, lon=12.5683)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)

    assert dlg._total_lbl.text() not in ("", "—")


def test_checksum_dialog_opens_without_coords(qtbot):
    """ChecksumDialog opens cleanly when no coords are supplied."""
    from opensak.gui.dialogs.checksum_dialog import ChecksumDialog

    dlg = ChecksumDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    assert dlg._total_lbl is not None


# ── ProjectionDialog ───────────────────────────────────────────────────────────


def test_projection_dialog_computes_result(qtbot):
    """Entering a start coord plus bearing/distance produces a non-None result."""
    with _patch_settings("opensak.gui.dialogs.projection_dialog"):
        from opensak.gui.dialogs.projection_dialog import ProjectionDialog

        dlg = ProjectionDialog(lat=55.6761, lon=12.5683)
        qtbot.addWidget(dlg)
        dlg.show()
        qtbot.waitExposed(dlg)

        dlg._bearing.setValue(90.0)
        dlg._distance.setValue(1000.0)
        qtbot.wait(50)

        assert dlg._result_lat is not None
        assert dlg._result_lon is not None
        assert dlg._dmm_row[1].text() != ""


def test_projection_result_is_east_of_origin(qtbot):
    """A bearing of 90° (East) must produce a result with a higher longitude."""
    with _patch_settings("opensak.gui.dialogs.projection_dialog"):
        from opensak.gui.dialogs.projection_dialog import ProjectionDialog

        dlg = ProjectionDialog(lat=55.0, lon=12.0)
        qtbot.addWidget(dlg)
        dlg.show()
        qtbot.waitExposed(dlg)

        dlg._bearing.setValue(90.0)
        dlg._distance.setValue(10_000.0)
        qtbot.wait(50)

        assert dlg._result_lon is not None
        assert dlg._result_lon > 12.0


# ── MidpointDialog ─────────────────────────────────────────────────────────────


def test_midpoint_dialog_computes_midpoint(qtbot):
    """Two valid coordinates produce a midpoint between them."""
    from opensak.gui.dialogs.midpoint_dialog import MidpointDialog

    dlg = MidpointDialog(lat=55.0, lon=12.0)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)

    dlg._input_b.setText("N 56° 00.000 E 013° 00.000")
    qtbot.wait(50)

    assert dlg._result_lat is not None
    assert dlg._result_lon is not None
    assert 55.0 < dlg._result_lat < 56.0
    assert dlg._dmm_row[1].text() != ""


def test_midpoint_dialog_invalid_b_clears_result(qtbot):
    """Entering invalid text for point B clears the result."""
    from opensak.gui.dialogs.midpoint_dialog import MidpointDialog

    dlg = MidpointDialog(lat=55.0, lon=12.0)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)

    dlg._input_b.setText("N 56° 00.000 E 013° 00.000")
    qtbot.wait(30)
    dlg._input_b.setText("garbage")
    qtbot.wait(30)

    assert dlg._result_lat is None


# ── DistanceBearingDialog ──────────────────────────────────────────────────────


def test_distance_bearing_dialog_computes_distance(qtbot):
    """Two valid points produce a non-empty, non-placeholder distance label."""
    with _patch_settings("opensak.gui.dialogs.distance_bearing_dialog"):
        from opensak.gui.dialogs.distance_bearing_dialog import DistanceBearingDialog

        dlg = DistanceBearingDialog(lat=55.0, lon=12.0)
        qtbot.addWidget(dlg)
        dlg.show()
        qtbot.waitExposed(dlg)

        dlg._input_b.setText("N 56° 00.000 E 013° 00.000")
        qtbot.wait(50)

        dist_text = dlg._dist_lbl.text()
        assert dist_text not in ("", "—")
        assert any(c.isdigit() for c in dist_text)


def test_distance_bearing_same_point_is_zero(qtbot):
    """Distance from a point to itself is effectively zero."""
    with _patch_settings("opensak.gui.dialogs.distance_bearing_dialog"):
        from opensak.gui.dialogs.distance_bearing_dialog import DistanceBearingDialog

        dlg = DistanceBearingDialog(lat=55.0, lon=12.0)
        qtbot.addWidget(dlg)
        dlg.show()
        qtbot.waitExposed(dlg)

        dlg._input_b.setText("N 55° 00.000 E 012° 00.000")
        qtbot.wait(50)

        dist_text = dlg._dist_lbl.text()
        assert dist_text not in ("", "—")
        assert "0" in dist_text

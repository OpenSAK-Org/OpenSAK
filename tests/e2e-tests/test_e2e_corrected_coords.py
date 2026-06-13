"""tests/e2e-tests/test_e2e_corrected_coords.py — corrected coordinates scenarios."""

import pytest

pytest.importorskip("pytestqt")


def _auto_accept_dialog(lat: float, lon: float):
    """Return a CorrectedCoordsDialog subclass that auto-accepts with fixed coords."""
    from opensak.gui.dialogs.corrected_coords_dialog import CorrectedCoordsDialog

    class _D(CorrectedCoordsDialog):
        def exec(self):
            self._lat = lat
            self._lon = lon
            return True

        def get_coords(self):
            return self._lat, self._lon

    return _D


# ── CorrectedCoordsDialog unit-level ──────────────────────────────────────────


def test_valid_dmm_input_enables_ok_and_parses(qtbot):
    """Entering valid DMM coordinates enables OK and stores correct lat/lon."""
    from opensak.gui.dialogs.corrected_coords_dialog import CorrectedCoordsDialog

    dlg = CorrectedCoordsDialog(gc_code="GC12345")
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)

    dlg._input.setText("N 55° 40.566 E 012° 34.100")
    qtbot.wait(50)

    assert dlg._ok_btn.isEnabled()
    lat, lon = dlg.get_coords()
    assert lat is not None and lon is not None
    assert abs(lat - 55.676) < 0.01
    assert abs(lon - 12.568) < 0.01


def test_valid_dd_input_enables_ok(qtbot):
    """Decimal-degrees input is also accepted."""
    from opensak.gui.dialogs.corrected_coords_dialog import CorrectedCoordsDialog

    dlg = CorrectedCoordsDialog(gc_code="GC99999")
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)

    dlg._input.setText("55.6761, 12.5683")
    qtbot.wait(50)

    assert dlg._ok_btn.isEnabled()
    lat, lon = dlg.get_coords()
    assert lat is not None
    assert abs(lat - 55.6761) < 0.001


def test_invalid_input_keeps_ok_disabled(qtbot):
    """Garbage text must leave the OK button disabled."""
    from opensak.gui.dialogs.corrected_coords_dialog import CorrectedCoordsDialog

    dlg = CorrectedCoordsDialog(gc_code="GC12345")
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)

    dlg._input.setText("not a coordinate at all")
    qtbot.wait(50)

    assert not dlg._ok_btn.isEnabled()
    lat, lon = dlg.get_coords()
    assert lat is None and lon is None


def test_empty_input_keeps_ok_disabled(qtbot):
    """An empty field must leave OK disabled."""
    from opensak.gui.dialogs.corrected_coords_dialog import CorrectedCoordsDialog

    dlg = CorrectedCoordsDialog(gc_code="GC12345")
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)

    dlg._input.setText("")
    qtbot.wait(50)

    assert not dlg._ok_btn.isEnabled()


def test_prefilled_with_existing_coords(qtbot):
    """Dialog pre-fills when existing corrected coords are passed."""
    from opensak.gui.dialogs.corrected_coords_dialog import CorrectedCoordsDialog

    dlg = CorrectedCoordsDialog(
        gc_code="GC12345",
        corrected_lat=55.6761,
        corrected_lon=12.5683,
    )
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)

    assert dlg._input.text() != ""
    assert dlg._ok_btn.isEnabled()


# ── End-to-end: save through detail panel ─────────────────────────────────────


def _select_cache(window, qtbot, gc_code: str) -> None:
    table = window._cache_table
    model = table.model()
    for row in range(model.rowCount()):
        cache = model.cache_at(row)
        if cache and cache.gc_code == gc_code:
            table.setCurrentIndex(model.index(row, 0))
            qtbot.wait(150)
            return
    pytest.fail(f"{gc_code} not found in table")


def test_save_corrected_coords_persists_to_db(seeded_window, qtbot, monkeypatch):
    """
    Saving corrected coords via the detail panel writes the values to the
    database and updates the UI.
    """
    from opensak.db.database import get_session
    from opensak.db.models import Cache as CacheModel

    window = seeded_window
    _select_cache(window, qtbot, "GC12345")

    monkeypatch.setattr(
        "opensak.gui.dialogs.corrected_coords_dialog.CorrectedCoordsDialog",
        _auto_accept_dialog(55.0, 12.0),
    )

    window._detail_panel._edit_corrected_coords()
    qtbot.wait(100)

    with get_session() as session:
        row = session.query(CacheModel).filter_by(gc_code="GC12345").first()
        note = row.user_note if row else None

    assert note is not None
    assert note.corrected_lat is not None
    assert abs(note.corrected_lat - 55.0) < 0.001
    assert abs(note.corrected_lon - 12.0) < 0.001


def test_clear_corrected_coords_removes_from_db(seeded_window, qtbot, monkeypatch):
    """
    After corrected coords are saved, clearing them via the detail panel sets
    the stored values back to None.
    """
    from opensak.db.database import get_session
    from opensak.db.models import Cache as CacheModel

    window = seeded_window
    _select_cache(window, qtbot, "GC12345")

    monkeypatch.setattr(
        "opensak.gui.dialogs.corrected_coords_dialog.CorrectedCoordsDialog",
        _auto_accept_dialog(55.0, 12.0),
    )

    window._detail_panel._edit_corrected_coords()
    qtbot.wait(100)

    window._detail_panel._clear_corrected_coords()
    qtbot.wait(100)

    with get_session() as session:
        row = session.query(CacheModel).filter_by(gc_code="GC12345").first()
        note = row.user_note if row else None

    if note is not None:
        assert note.corrected_lat is None
        assert note.corrected_lon is None

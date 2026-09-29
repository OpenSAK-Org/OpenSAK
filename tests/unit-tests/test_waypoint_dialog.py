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


# ── extra cache fields ────────────────────────────────────────────────────────

class TestExtraFields:
    def test_extra_fields_populate_and_roundtrip(self, qtbot):
        from datetime import datetime
        c = _cache()
        c.owner_name = "Real Owner"
        c.hidden_date = datetime(2020, 5, 17, 10, 30)
        c.county = "Kreis"
        c.elevation = 512.0
        c.short_desc_html = True
        c.long_desc_html = False
        c.user_flag = True
        c.watch = True
        c.user_data_1 = "u1"
        c.user_data_4 = "u4"
        c.gc_note = "gc note"
        dlg = WaypointDialog(cache=c)
        qtbot.addWidget(dlg)
        data = dlg.get_data()
        assert data["owner_name"] == "Real Owner"
        # Time of day preserved while the date itself is unchanged
        assert data["hidden_date"] == datetime(2020, 5, 17, 10, 30)
        assert data["county"] == "Kreis"
        assert data["elevation"] == 512.0
        assert data["short_desc_html"] is True
        assert data["long_desc_html"] is False
        assert data["user_flag"] is True
        assert data["watch"] is True
        assert data["user_data_1"] == "u1"
        assert data["user_data_2"] is None
        assert data["user_data_4"] == "u4"
        assert data["gc_note"] == "gc note"

    def test_missing_optional_attrs_default_to_empty(self, qtbot):
        dlg = WaypointDialog(cache=_cache())
        qtbot.addWidget(dlg)
        data = dlg.get_data()
        assert data["hidden_date"] is None
        assert data["elevation"] is None
        assert data["user_flag"] is False

    def test_elevation_zero_is_kept(self, qtbot):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._elevation.setText("0")
        assert dlg.get_data()["elevation"] == 0.0

    def test_elevation_accepts_decimal_comma(self, qtbot):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._elevation.setText("12,5")
        assert dlg.get_data()["elevation"] == 12.5

    def test_invalid_elevation_blocks_accept(self, qtbot, warn):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._name.setText("Good")
        dlg._gc_code.setText("GC123")
        dlg._elevation.setText("high")
        dlg._validate_and_accept()
        warn.assert_called_once()
        assert dlg.result() != QDialog.DialogCode.Accepted


# ── found/dnf ↔ date sync ─────────────────────────────────────────────────────

class TestFlagDateSync:
    def test_ticking_found_sets_today(self, qtbot):
        from datetime import date
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._found.setChecked(True)
        assert dlg.get_data()["found_date"].date() == date.today()

    def test_unticking_found_clears_date(self, qtbot):
        from datetime import datetime
        c = _cache()
        c.found_date = datetime(2021, 1, 2, 8, 0)
        dlg = WaypointDialog(cache=c)
        qtbot.addWidget(dlg)
        assert dlg.get_data()["found_date"] == datetime(2021, 1, 2, 8, 0)
        dlg._found.setChecked(False)
        data = dlg.get_data()
        assert data["found"] is False
        assert data["found_date"] is None

    def test_populate_keeps_existing_state(self, qtbot):
        # found without a date stays that way on open (no silent "today")
        dlg = WaypointDialog(cache=_cache())
        qtbot.addWidget(dlg)
        assert dlg.get_data()["found_date"] is None

    def test_dnf_sync(self, qtbot):
        dlg = WaypointDialog()
        qtbot.addWidget(dlg)
        dlg._dnf.setChecked(True)
        assert dlg.get_data()["dnf_date"] is not None
        dlg._dnf.setChecked(False)
        assert dlg.get_data()["dnf_date"] is None


# ── user note / corrected coordinates ─────────────────────────────────────────

class TestUserNote:
    def test_populates_note_and_corrected(self, qtbot):
        c = _cache()
        c.user_note = SimpleNamespace(
            note="my note", corrected_lat=55.5, corrected_lon=12.5, is_corrected=True)
        dlg = WaypointDialog(cache=c)
        qtbot.addWidget(dlg)
        assert dlg.get_user_note_data() == {
            "note": "my note", "corrected_lat": 55.5, "corrected_lon": 12.5}
        assert dlg._corr_clear_btn.isEnabled()

    def test_clear_corrected(self, qtbot):
        c = _cache()
        c.user_note = SimpleNamespace(
            note=None, corrected_lat=55.5, corrected_lon=12.5, is_corrected=True)
        dlg = WaypointDialog(cache=c)
        qtbot.addWidget(dlg)
        dlg._corr_clear_btn.click()
        assert dlg.get_user_note_data()["corrected_lat"] is None
        assert not dlg._corr_clear_btn.isEnabled()

    def test_edit_corrected_via_dialog(self, qtbot, monkeypatch):
        from opensak.gui.dialogs import corrected_coords_dialog as ccd
        monkeypatch.setattr(ccd.CorrectedCoordsDialog, "exec", lambda self: True)
        monkeypatch.setattr(ccd.CorrectedCoordsDialog, "get_coords", lambda self: (1.5, 2.5))
        dlg = WaypointDialog(cache=_cache())
        qtbot.addWidget(dlg)
        dlg._corr_edit_btn.click()
        assert dlg.get_user_note_data()["corrected_lat"] == 1.5
        assert dlg.get_user_note_data()["corrected_lon"] == 2.5


# ── child waypoints ───────────────────────────────────────────────────────────

def _wp(id=1, prefix="PK", wp_code="PK12345"):
    return SimpleNamespace(
        id=id, prefix=prefix, wp_type="Parking Area", name="Parking",
        description=None, comment=None, latitude=55.1, longitude=12.1,
        wp_code=wp_code, url=None, wp_date=None, wp_flag=False,
    )


def _wp_dict(**overrides):
    d = {k: v for k, v in vars(_wp()).items() if k != "id"}
    d.update(overrides)
    return d


class TestChildWaypoints:
    def test_waypoints_listed(self, qtbot):
        c = _cache()
        c.waypoints = [_wp(), _wp(id=2, prefix="FN", wp_code="FN12345")]
        dlg = WaypointDialog(cache=c)
        qtbot.addWidget(dlg)
        assert dlg._wp_table.rowCount() == 2
        assert dlg._wp_table.item(1, 0).text() == "FN"

    def test_waypoints_tab_hidden_for_custom(self, qtbot):
        dlg = WaypointDialog(cache=_cache(gc_code="CW001", cache_type="Parking Area"))
        qtbot.addWidget(dlg)
        assert not dlg._tabs.isTabVisible(dlg._waypoints_tab_idx)
        assert dlg.get_waypoints_data() is None

    def test_add_edit_delete(self, qtbot, monkeypatch):
        c = _cache()
        c.waypoints = [_wp()]
        dlg = WaypointDialog(cache=c)
        qtbot.addWidget(dlg)

        new = _wp_dict(prefix="S1", wp_type="Stage", wp_code=None)
        monkeypatch.setattr(wpd.ChildWaypointDialog, "exec", lambda self: True)
        monkeypatch.setattr(wpd.ChildWaypointDialog, "get_data", lambda self: dict(new))
        dlg._add_child_wp()
        wps = dlg.get_waypoints_data()
        assert [w["prefix"] for w in wps] == ["PK", "S1"]
        assert wps[1]["id"] is None

        # edit keeps the DB id of the edited row
        dlg._wp_table.selectRow(0)
        dlg._edit_child_wp()
        assert dlg.get_waypoints_data()[0]["id"] == 1
        assert dlg.get_waypoints_data()[0]["prefix"] == "S1"

        dlg._wp_table.selectRow(0)
        dlg._delete_child_wp()
        assert len(dlg.get_waypoints_data()) == 1

    def test_duplicate_wp_code_rejected(self, qtbot, monkeypatch, warn):
        c = _cache()
        c.waypoints = [_wp(), _wp(id=2, prefix="FN", wp_code="FN12345")]
        dlg = WaypointDialog(cache=c)
        qtbot.addWidget(dlg)
        results = iter([True, False])   # OK with a duplicate code, then Cancel
        monkeypatch.setattr(wpd.ChildWaypointDialog, "exec", lambda self: next(results))
        monkeypatch.setattr(wpd.ChildWaypointDialog, "get_data",
                            lambda self: _wp_dict(prefix="XX", wp_code="FN12345"))
        dlg._add_child_wp()
        warn.assert_called_once()
        assert len(dlg.get_waypoints_data()) == 2


class TestChildWaypointDialog:
    def test_prefix_required(self, qtbot, warn):
        dlg = wpd.ChildWaypointDialog()
        qtbot.addWidget(dlg)
        dlg._validate_and_accept()
        warn.assert_called_once()

    def test_known_prefix_suggests_type(self, qtbot):
        dlg = wpd.ChildWaypointDialog()
        qtbot.addWidget(dlg)
        qtbot.keyClicks(dlg._prefix, "fn")
        assert dlg._wp_type.currentText() == "Final Location"

    def test_roundtrip(self, qtbot):
        dlg = wpd.ChildWaypointDialog(wp=_wp_dict())
        qtbot.addWidget(dlg)
        data = dlg.get_data()
        assert data["prefix"] == "PK"
        assert data["wp_type"] == "Parking Area"
        assert abs(data["latitude"] - 55.1) < 1e-6
        assert data["wp_code"] == "PK12345"

    def test_bad_coords_blocked(self, qtbot, warn):
        dlg = wpd.ChildWaypointDialog()
        qtbot.addWidget(dlg)
        dlg._prefix.setText("PK")
        dlg._coord_input.setText("garbage")
        dlg._validate_and_accept()
        warn.assert_called_once()


# ── save_related against a real DB ────────────────────────────────────────────

class TestSaveRelated:
    def test_user_note_and_waypoints_persisted(self, qtbot, db_session, make_cache):
        from opensak.db.models import Cache, UserNote, Waypoint
        cache = make_cache()
        cache.waypoints = [Waypoint(prefix="PK", wp_type="Parking Area", wp_code="PK1"),
                           Waypoint(prefix="FN", wp_type="Final Location", wp_code="FN1")]
        db_session.add(cache)
        db_session.commit()

        dlg = WaypointDialog(cache=cache)
        qtbot.addWidget(dlg)
        dlg._user_note.setPlainText("my note")
        dlg._set_corrected(55.5, 12.5)
        # drop FN1 and add a new waypoint that reuses its wp_code
        dlg._waypoints = [w for w in dlg._waypoints if w["prefix"] == "PK"]
        dlg._waypoints.append({"id": None, **_wp_dict(prefix="S1", wp_code="FN1")})
        dlg.save_related(db_session, cache)
        db_session.commit()

        note = db_session.query(UserNote).filter_by(cache_id=cache.id).one()
        assert note.note == "my note"
        assert note.is_corrected is True
        prefixes = sorted(w.prefix for w in db_session.query(Waypoint).filter_by(cache_id=cache.id))
        assert prefixes == ["PK", "S1"]
        new = db_session.query(Waypoint).filter_by(prefix="S1").one()
        assert new.created_by_user is True
        assert new.parent_gc_code == cache.gc_code
        assert db_session.get(Cache, cache.id).waypoint_count == 2

    def test_no_empty_user_note_created(self, qtbot, db_session, make_cache):
        from opensak.db.models import UserNote
        cache = make_cache()
        db_session.add(cache)
        db_session.commit()
        dlg = WaypointDialog(cache=cache)
        qtbot.addWidget(dlg)
        dlg.save_related(db_session, cache)
        db_session.commit()
        assert db_session.query(UserNote).count() == 0

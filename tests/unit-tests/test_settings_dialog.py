# tests/unit-tests/test_settings_dialog.py — settings dialog (temp-backed AppSettings).

import pytest
from unittest.mock import MagicMock

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QDialog, QMessageBox

from opensak.gui.dialogs import settings_dialog as sd
from opensak.gui.dialogs.settings_dialog import (
    SettingsDialog,
    _OAuthWorker,
    _ProfileWorker,
)
from opensak.gui.settings import AppSettings, HomePoint

_VALID = "N55 47.250 E012 25.000"


@pytest.fixture
def settings(monkeypatch):
    # isolate_settings_store (autouse) har allerede sat en frisk store.
    # Vi returnerer blot en AppSettings-instans og patcher get_settings.
    from opensak.gui.settings import AppSettings
    s = AppSettings()
    monkeypatch.setattr(sd, "get_settings", lambda: s)
    monkeypatch.setattr("opensak.gui.settings.get_settings", lambda: s)

    monkeypatch.setattr("opensak.db.manager.get_db_manager",
                        lambda: (_ for _ in ()).throw(RuntimeError("no manager in test")))
    monkeypatch.setattr("opensak.api.geocaching.is_logged_in", lambda: False)
    return s


@pytest.fixture
def dlg(qtbot, settings):
    d = SettingsDialog()
    qtbot.addWidget(d)
    return d


# ── workers ───────────────────────────────────────────────────────────────────

class TestWorkers:
    def test_oauth_success(self, monkeypatch):
        monkeypatch.setattr("opensak.api.geocaching.start_oauth_flow", lambda: {"access_token": "T"})
        w = _OAuthWorker()
        got = []
        w.success.connect(got.append)
        w.run()
        assert got == [{"access_token": "T"}]

    def test_oauth_no_token_emits_error(self, monkeypatch):
        monkeypatch.setattr("opensak.api.geocaching.start_oauth_flow", lambda: None)
        w = _OAuthWorker()
        errs = []
        w.error.connect(errs.append)
        w.run()
        assert errs

    def test_oauth_exception_emits_error(self, monkeypatch):
        monkeypatch.setattr(
            "opensak.api.geocaching.start_oauth_flow",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        w = _OAuthWorker()
        errs = []
        w.error.connect(errs.append)
        w.run()
        assert errs and "boom" in errs[0]

    def test_profile_success(self, monkeypatch):
        monkeypatch.setattr("opensak.api.geocaching.get_user_profile", lambda: {"username": "bob"})
        w = _ProfileWorker()
        got = []
        w.success.connect(got.append)
        w.run()
        assert got == [{"username": "bob"}]

    def test_profile_none_emits_error(self, monkeypatch):
        monkeypatch.setattr("opensak.api.geocaching.get_user_profile", lambda: None)
        w = _ProfileWorker()
        errs = []
        w.error.connect(errs.append)
        w.run()
        assert errs


# ── construction (covers the three tab builders + _load) ──────────────────────

class TestConstruction:
    def test_builds_three_tabs(self, dlg):
        assert dlg._tabs.count() == 3

    def test_load_reflects_settings(self, qtbot, settings):
        settings.gc_username = "preset"
        settings.use_miles = True
        d = SettingsDialog()
        qtbot.addWidget(d)
        assert d._gc_username.text() == "preset"
        assert d._unit_combo.currentData() is True


# ── coordinate / home-location feedback ───────────────────────────────────────

class TestCoordFeedback:
    def test_coord_valid(self, dlg):
        dlg._on_coord_changed(_VALID)
        assert "✓" in dlg._coord_hint.text()

    def test_coord_invalid(self, dlg):
        dlg._on_coord_changed("garbage")
        assert dlg._coord_hint.text() != ""

    def test_coord_empty(self, dlg):
        dlg._on_coord_changed("")
        assert dlg._coord_hint.text() == ""

    def test_home_loc_valid(self, dlg):
        dlg._on_home_loc_changed(_VALID)
        assert "✓" in dlg._home_loc_hint.text()

    def test_home_loc_invalid(self, dlg):
        dlg._on_home_loc_changed("garbage")
        assert dlg._home_loc_hint.text() != ""

    def test_home_loc_empty(self, dlg):
        dlg._on_home_loc_changed("")
        assert dlg._home_loc_hint.text() == ""


# ── home points ───────────────────────────────────────────────────────────────

class TestHomePoints:
    def test_add_requires_name(self, dlg, monkeypatch):
        warn = MagicMock()
        monkeypatch.setattr(sd.QMessageBox, "warning", warn)
        dlg._new_name.setText("")
        dlg._new_coord.setText(_VALID)
        dlg._add_point()
        warn.assert_called_once()

    def test_add_requires_coord(self, dlg, monkeypatch):
        warn = MagicMock()
        monkeypatch.setattr(sd.QMessageBox, "warning", warn)
        dlg._new_name.setText("Home")
        dlg._new_coord.setText("")
        dlg._add_point()
        warn.assert_called_once()

    def test_add_rejects_bad_coord(self, dlg, monkeypatch):
        warn = MagicMock()
        monkeypatch.setattr(sd.QMessageBox, "warning", warn)
        dlg._new_name.setText("Home")
        dlg._new_coord.setText("garbage")
        dlg._add_point()
        warn.assert_called_once()

    def test_add_valid_point(self, dlg, settings):
        dlg._new_name.setText("Work")
        dlg._new_coord.setText(_VALID)
        dlg._add_point()
        names = [p.name for p in settings.home_points]
        assert "Work" in names
        assert dlg._new_name.text() == ""  # cleared after add

    def test_edit_then_rename(self, dlg, settings):
        settings.add_or_update_home_point(HomePoint("Old", 55.0, 12.0))
        dlg._reload_points_table()
        # select the row for "Old"
        for row in range(dlg._points_table.rowCount()):
            if "Old" in dlg._points_table.item(row, 0).text():
                dlg._points_table.setCurrentCell(row, 0)
                break
        dlg._edit_point()
        assert dlg._editing_original_name == "Old"
        dlg._new_name.setText("Renamed")
        dlg._new_coord.setText(_VALID)
        dlg._add_point()
        names = [p.name for p in settings.home_points]
        assert "Renamed" in names
        assert "Old" not in names

    def test_delete_point(self, dlg, settings, monkeypatch):
        settings.add_or_update_home_point(HomePoint("Trash", 55.0, 12.0))
        dlg._reload_points_table()
        for row in range(dlg._points_table.rowCount()):
            if "Trash" in dlg._points_table.item(row, 0).text():
                dlg._points_table.setCurrentCell(row, 0)
                break
        monkeypatch.setattr(
            sd.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
        )
        dlg._delete_point()
        assert "Trash" not in [p.name for p in settings.home_points]

    def test_point_selection_toggles_buttons(self, dlg, settings):
        settings.add_or_update_home_point(HomePoint("P1", 55.0, 12.0))
        dlg._reload_points_table()
        dlg._points_table.setCurrentCell(0, 0)
        dlg._on_point_selected()
        assert dlg._btn_edit.isEnabled() is True

    def test_save_home_location_valid(self, dlg, settings):
        dlg._gc_home_location.setText(_VALID)
        dlg._save_home_location()
        assert settings.gc_home_location == _VALID

    def test_save_home_location_empty_clears(self, dlg, settings):
        settings.gc_home_location = _VALID
        dlg._gc_home_location.setText("")
        dlg._save_home_location()
        assert settings.gc_home_location == ""

    def test_save_home_location_invalid_warns_and_keeps(self, dlg, settings, monkeypatch):
        settings.gc_home_location = _VALID
        warn = MagicMock()
        monkeypatch.setattr("opensak.gui.icon.OpenSAKMessageBox.warning", warn)
        dlg._gc_home_location.setText("garbage")
        dlg._save_home_location()
        warn.assert_called_once()
        assert settings.gc_home_location == _VALID  # unchanged


# ── theme ─────────────────────────────────────────────────────────────────────

class TestTheme:
    def test_theme_changed_updates_preview(self, dlg):
        dlg._on_theme_changed()
        assert "background-color" in dlg._theme_preview.styleSheet()


# ── geocaching login/logout/profile ───────────────────────────────────────────

class TestGeocaching:
    def test_login_without_client_id_shows_info(self, dlg, monkeypatch):
        info = MagicMock()
        monkeypatch.setattr(sd.QMessageBox, "information", info)
        monkeypatch.setattr("opensak.api.geocaching.GC_CLIENT_ID", "")
        dlg._on_gc_login()
        info.assert_called_once()

    def test_login_success_then_profile(self, dlg, monkeypatch):
        monkeypatch.setattr(dlg, "_on_gc_refresh_profile", MagicMock())
        dlg._on_gc_login_success({"access_token": "T"})
        assert dlg._gc_logout_btn.isEnabled() is True

    def test_login_error(self, dlg, monkeypatch):
        monkeypatch.setattr(sd.QMessageBox, "warning", MagicMock())
        dlg._on_gc_login_error("nope")
        assert dlg._gc_login_btn.isEnabled() is True

    def test_logout_confirmed(self, dlg, monkeypatch):
        monkeypatch.setattr(
            sd.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
        )
        logout = MagicMock()
        monkeypatch.setattr("opensak.api.geocaching.logout", logout)
        dlg._on_gc_logout()
        logout.assert_called_once()
        assert dlg._gc_logout_btn.isEnabled() is False

    def test_logout_declined(self, dlg, monkeypatch):
        monkeypatch.setattr(
            sd.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
        )
        logout = MagicMock()
        monkeypatch.setattr("opensak.api.geocaching.logout", logout)
        dlg._on_gc_logout()
        logout.assert_not_called()

    def test_profile_loaded(self, dlg):
        dlg._on_profile_loaded({"username": "alice", "findCount": 42})
        assert dlg._gc_username_label.text() == "alice"

    def test_profile_error(self, dlg):
        dlg._on_profile_error("bad")
        assert dlg._gc_refresh_btn.isEnabled() is True

    def test_refresh_status_logged_in(self, qtbot, settings, monkeypatch):
        monkeypatch.setattr("opensak.api.geocaching.is_logged_in", lambda: True)

        class FakeWorker:
            def __init__(self, parent=None):
                self.success = MagicMock()
                self.error = MagicMock()

            def start(self):
                pass

        monkeypatch.setattr(sd, "_ProfileWorker", FakeWorker)
        d = SettingsDialog()
        qtbot.addWidget(d)
        assert d._gc_logout_btn.isEnabled() is True


# ── save ──────────────────────────────────────────────────────────────────────

class TestSave:
    def test_save_persists_and_accepts(self, dlg, settings):
        dlg._gc_username.setText("tester")
        dlg._unit_combo.setCurrentIndex(dlg._unit_combo.findData(True))
        dlg._save()
        assert settings.gc_username == "tester"
        assert settings.use_miles is True
        assert dlg.result() == QDialog.DialogCode.Accepted

    def test_save_keeps_existing_home_when_invalid(self, dlg, settings):
        settings.gc_home_location = _VALID
        dlg._gc_home_location.setText("garbage")
        dlg._save()
        assert settings.gc_home_location == _VALID  # invalid ignored

    def test_save_clears_home_when_empty(self, dlg, settings):
        settings.gc_home_location = _VALID
        dlg._gc_home_location.setText("")
        dlg._save()
        assert settings.gc_home_location == ""

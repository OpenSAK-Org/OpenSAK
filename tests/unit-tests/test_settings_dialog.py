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

    def test_imap_test_worker_success(self, monkeypatch):
        from opensak.gui.dialogs.settings_dialog import _ImapTestWorker
        from opensak.email.connection import ImapConfig
        monkeypatch.setattr(
            "opensak.email.connection.check_connection",
            lambda config, password, timeout=10.0: None,
        )
        config = ImapConfig(host="imap.example.com", port=993, use_ssl=True, username="alice")
        w = _ImapTestWorker(config, "pw")
        got = []
        w.success.connect(lambda: got.append(True))
        w.run()
        assert got == [True]

    def test_imap_test_worker_auth_error(self, monkeypatch):
        from opensak.gui.dialogs.settings_dialog import _ImapTestWorker
        from opensak.email.connection import ImapAuthError, ImapConfig

        def _raise(*a, **kw):
            raise ImapAuthError("bad login")

        monkeypatch.setattr("opensak.email.connection.check_connection", _raise)
        config = ImapConfig(host="imap.example.com", port=993, use_ssl=True, username="alice")
        w = _ImapTestWorker(config, "wrong")
        errs = []
        w.error.connect(lambda kind, detail: errs.append((kind, detail)))
        w.run()
        assert errs == [("auth", "bad login")]

    def test_imap_test_worker_network_error(self, monkeypatch):
        from opensak.gui.dialogs.settings_dialog import _ImapTestWorker
        from opensak.email.connection import ImapConfig, ImapNetworkError

        def _raise(*a, **kw):
            raise ImapNetworkError("no route")

        monkeypatch.setattr("opensak.email.connection.check_connection", _raise)
        config = ImapConfig(host="imap.example.com", port=993, use_ssl=True, username="alice")
        w = _ImapTestWorker(config, "pw")
        errs = []
        w.error.connect(lambda kind, detail: errs.append((kind, detail)))
        w.run()
        assert errs == [("network", "no route")]

    def test_imap_test_worker_other_error(self, monkeypatch):
        from opensak.gui.dialogs.settings_dialog import _ImapTestWorker
        from opensak.email.connection import ImapConfig

        def _raise(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr("opensak.email.connection.check_connection", _raise)
        config = ImapConfig(host="imap.example.com", port=993, use_ssl=True, username="alice")
        w = _ImapTestWorker(config, "pw")
        errs = []
        w.error.connect(lambda kind, detail: errs.append((kind, detail)))
        w.run()
        assert errs == [("other", "boom")]


# ── construction (covers the three tab builders + _load) ──────────────────────

class TestConstruction:
    def test_builds_five_tabs(self, dlg):
        # General, Map (#638), Geocaching.com, PQ Email (#443), Advanced.
        assert dlg._tabs.count() == 5

    def test_load_reflects_settings(self, qtbot, settings):
        settings.gc_username = "preset"
        settings.use_miles = True
        d = SettingsDialog()
        qtbot.addWidget(d)
        assert d._gc_username.text() == "preset"
        assert d._unit_combo.currentData() is True

    def test_map_enabled_defaults_to_checked(self, dlg):
        # map_enabled defaults to True (opt-out, not opt-in) — see #638.
        assert dlg._map_enabled_cb.isChecked() is True

    def test_map_enabled_loads_false_from_settings(self, qtbot, settings):
        settings.map_enabled = False
        d = SettingsDialog()
        qtbot.addWidget(d)
        assert d._map_enabled_cb.isChecked() is False

    def test_map_enabled_saves_on_accept(self, qtbot, settings):
        d = SettingsDialog()
        qtbot.addWidget(d)
        d._map_enabled_cb.setChecked(False)
        d._save()
        assert settings.map_enabled is False

    def test_map_max_caches_defaults_to_2000(self, dlg):
        # #639 — benchmarked default, see settings.py's docstring.
        assert dlg._map_max_caches.value() == 2000

    def test_map_max_caches_loads_from_settings(self, qtbot, settings):
        settings.map_max_caches = 500
        d = SettingsDialog()
        qtbot.addWidget(d)
        assert d._map_max_caches.value() == 500

    def test_map_max_caches_zero_shows_unlimited_text(self, qtbot, settings):
        from opensak.lang import tr
        settings.map_max_caches = 0
        d = SettingsDialog()
        qtbot.addWidget(d)
        assert d._map_max_caches.value() == 0
        assert d._map_max_caches.text() == tr("settings_map_unlimited")

    def test_map_max_caches_saves_on_accept(self, qtbot, settings):
        d = SettingsDialog()
        qtbot.addWidget(d)
        d._map_max_caches.setValue(1500)
        d._save()
        assert settings.map_max_caches == 1500

    def test_map_max_caches_negative_input_clamped_to_zero(self, qtbot, settings):
        # Settings.map_max_caches itself also clamps (defense in depth) —
        # the spinbox's own setRange(0, ...) should already prevent this in
        # practice, but confirm the setter is safe regardless.
        settings.map_max_caches = -5
        assert settings.map_max_caches == 0


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

    def test_default_decode_hints_round_trip(self, dlg, settings):
        # Issue #499: checkbox loads existing settings on open, and its
        # state is saved back on accept.
        assert settings.default_decode_hints is False  # default
        assert dlg._decode_hints_cb.isChecked() is False

        dlg._decode_hints_cb.setChecked(True)
        dlg._save()
        assert settings.default_decode_hints is True

    def test_notify_about_betas_round_trip(self, dlg, settings):
        assert settings.notify_about_betas is False  # default
        assert dlg._notify_betas_cb.isChecked() is False

        dlg._notify_betas_cb.setChecked(True)
        dlg._save()
        assert settings.notify_about_betas is True

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


# ── PQ Email tab (issue #443) ─────────────────────────────────────────────────

class TestPQEmailTab:
    def test_defaults(self, dlg):
        from opensak.email.connection import DEFAULT_IMAP_SSL_PORT
        assert dlg._pq_email_host.text() == ""
        assert dlg._pq_email_port.value() == DEFAULT_IMAP_SSL_PORT
        assert dlg._pq_email_ssl_cb.isChecked() is True
        assert dlg._pq_email_username.text() == ""
        assert dlg._pq_email_password.text() == ""
        assert dlg._pq_email_password_hint.text() == ""

    def test_loads_existing_settings(self, qtbot, settings):
        settings.pq_email_host = "imap.example.com"
        settings.pq_email_port = 143
        settings.pq_email_use_ssl = False
        settings.pq_email_username = "alice"
        d = SettingsDialog()
        qtbot.addWidget(d)
        assert d._pq_email_host.text() == "imap.example.com"
        assert d._pq_email_port.value() == 143
        assert d._pq_email_ssl_cb.isChecked() is False
        assert d._pq_email_username.text() == "alice"
        # Password field itself is never pre-filled from keyring.
        assert d._pq_email_password.text() == ""

    def test_password_hint_shown_when_one_is_saved(self, qtbot, settings, monkeypatch):
        settings.pq_email_username = "alice"
        monkeypatch.setattr(
            "opensak.email.credentials.get_password", lambda u: "s3cret"
        )
        d = SettingsDialog()
        qtbot.addWidget(d)
        assert d._pq_email_password_hint.text() != ""

    def test_no_password_hint_when_none_saved(self, qtbot, settings, monkeypatch):
        settings.pq_email_username = "alice"
        monkeypatch.setattr(
            "opensak.email.credentials.get_password", lambda u: None
        )
        d = SettingsDialog()
        qtbot.addWidget(d)
        assert d._pq_email_password_hint.text() == ""

    def test_save_persists_connection_fields(self, dlg, settings):
        dlg._pq_email_host.setText("imap.example.com")
        dlg._pq_email_port.setValue(143)
        dlg._pq_email_ssl_cb.setChecked(False)
        dlg._pq_email_username.setText("alice")
        dlg._save()
        assert settings.pq_email_host == "imap.example.com"
        assert settings.pq_email_port == 143
        assert settings.pq_email_use_ssl is False
        assert settings.pq_email_username == "alice"

    def test_save_with_empty_password_does_not_touch_keyring(self, dlg, settings, monkeypatch):
        called = []
        monkeypatch.setattr(
            "opensak.email.credentials.set_password",
            lambda u, p: called.append((u, p)),
        )
        dlg._pq_email_username.setText("alice")
        dlg._pq_email_password.setText("")
        dlg._save()
        assert called == []

    def test_save_with_new_password_stores_it_via_keyring(self, dlg, settings, monkeypatch):
        called = []
        monkeypatch.setattr(
            "opensak.email.credentials.set_password",
            lambda u, p: called.append((u, p)),
        )
        dlg._pq_email_username.setText("alice")
        dlg._pq_email_password.setText("new-pw")
        dlg._save()
        assert called == [("alice", "new-pw")]

    def test_ssl_toggle_flips_default_port(self, dlg):
        from opensak.email.connection import DEFAULT_IMAP_PORT, DEFAULT_IMAP_SSL_PORT
        assert dlg._pq_email_port.value() == DEFAULT_IMAP_SSL_PORT
        dlg._pq_email_ssl_cb.setChecked(False)
        assert dlg._pq_email_port.value() == DEFAULT_IMAP_PORT
        dlg._pq_email_ssl_cb.setChecked(True)
        assert dlg._pq_email_port.value() == DEFAULT_IMAP_SSL_PORT

    def test_ssl_toggle_does_not_override_custom_port(self, dlg):
        # A deliberately-chosen non-standard port must survive toggling
        # SSL on/off — only the two well-known defaults get swapped.
        dlg._pq_email_port.setValue(2525)
        dlg._pq_email_ssl_cb.setChecked(False)
        assert dlg._pq_email_port.value() == 2525

    def test_test_button_warns_on_missing_host_or_username(self, dlg, monkeypatch):
        warned = []
        monkeypatch.setattr(
            "opensak.gui.dialogs.settings_dialog.QMessageBox.warning",
            lambda *a, **kw: warned.append(a),
        )
        dlg._pq_email_host.setText("")
        dlg._pq_email_username.setText("")
        dlg._on_pq_email_test()
        assert warned

    def test_test_button_warns_on_missing_password(self, dlg, monkeypatch):
        monkeypatch.setattr(
            "opensak.email.credentials.get_password", lambda u: None
        )
        warned = []
        monkeypatch.setattr(
            "opensak.gui.dialogs.settings_dialog.QMessageBox.warning",
            lambda *a, **kw: warned.append(a),
        )
        dlg._pq_email_host.setText("imap.example.com")
        dlg._pq_email_username.setText("alice")
        dlg._pq_email_password.setText("")
        dlg._on_pq_email_test()
        assert warned

    def test_test_error_kinds_map_to_distinct_messages(self, dlg):
        from opensak.lang import load_language
        load_language("en")
        dlg._on_pq_email_test_error("auth", "bad creds")
        auth_msg = dlg._pq_email_status.text()
        dlg._on_pq_email_test_error("network", "no route")
        network_msg = dlg._pq_email_status.text()
        dlg._on_pq_email_test_error("other", "??")
        other_msg = dlg._pq_email_status.text()
        assert "bad creds" in auth_msg
        assert "no route" in network_msg
        assert "??" in other_msg
        assert len({auth_msg, network_msg, other_msg}) == 3

    def test_test_success_handler_sets_success_message(self, dlg):
        from opensak.lang import tr
        dlg._on_pq_email_test_success()
        assert dlg._pq_email_status.text() == tr("pq_email_test_success")
        assert dlg._pq_email_test_btn.isEnabled() is True

    def test_test_button_starts_worker_with_typed_credentials(self, dlg, monkeypatch):
        # Verify _on_pq_email_test wires up and starts a worker with the
        # exact field values, without actually running a real QThread
        # (see TestWorkers._ImapTestWorker tests below for the worker's
        # own success/error-path behaviour, tested via .run() directly —
        # same convention as _OAuthWorker/_ProfileWorker above).
        started = []
        monkeypatch.setattr(
            "opensak.gui.dialogs.settings_dialog._ImapTestWorker.start",
            lambda self: started.append(self),
        )
        dlg._pq_email_host.setText("imap.example.com")
        dlg._pq_email_username.setText("alice")
        dlg._pq_email_password.setText("pw")
        dlg._on_pq_email_test()
        assert len(started) == 1
        worker = started[0]
        assert worker._config.host == "imap.example.com"
        assert worker._config.username == "alice"
        assert worker._password == "pw"
        assert dlg._pq_email_test_btn.isEnabled() is False

# tests/unit-tests/test_offline_mode_1361.py — #1361 (part 1a): Offline mode.
# Settings → General → Offline mode, or OPENSAK_OFFLINE=1, makes
# net.offline_detected() True for the whole session, so every automatic
# request is skipped from the start. Requests the user starts that need the
# network (Geocaching.com API, PQ e-mail) fail at once with a clear message;
# Help → Check for updates asks first.

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from opensak import net


def _no_network(*_a, **_k):
    raise AssertionError("no request may be made in Offline mode")


@pytest.fixture
def offline():
    net.set_offline_mode(True)


# ── net ───────────────────────────────────────────────────────────────────


class TestNet:
    def test_off_by_default(self):
        assert not net.offline_mode()
        assert not net.offline_detected()

    def test_setting_turns_it_on(self, offline):
        assert net.offline_mode()
        assert net.offline_detected()

    @pytest.mark.parametrize("value, on", [
        ("1", True), ("true", True), ("YES", True), (" on ", True),
        ("0", False), ("", False), ("off", False),
    ])
    def test_env_var(self, monkeypatch, value, on):
        monkeypatch.setenv(net.OFFLINE_ENV, value)
        assert net.offline_mode() is on
        assert net.offline_detected() is on

    def test_a_success_does_not_clear_it(self, offline):
        net.note_success()
        assert net.offline_detected()

    def test_no_retry_after_the_interval(self, offline, monkeypatch):
        # Unlike a detected failure, Offline mode doesn't expire.
        import time
        later = time.monotonic() + net.OFFLINE_RETRY_SECONDS + 1
        monkeypatch.setattr(time, "monotonic", lambda: later)
        assert net.offline_detected()

    def test_turning_it_off_restores_normal_detection(self, offline):
        net.set_offline_mode(False)
        assert not net.offline_detected()

    def test_ensure_online(self, offline):
        from opensak.lang import tr
        with pytest.raises(net.OfflineModeError) as info:
            net.ensure_online()
        assert str(info.value) == tr("offline_mode_blocked")
        net.set_offline_mode(False)
        net.ensure_online()


class TestSetting:
    def test_stored_and_applied(self):
        from opensak.gui.settings import AppSettings
        s = AppSettings()
        assert s.offline_mode is False
        s.offline_mode = True
        assert s.offline_mode is True
        assert net.offline_mode()
        s.offline_mode = False
        assert not net.offline_mode()


# ── Automatic requests are skipped ────────────────────────────────────────


class TestAutomaticRequests:
    def test_boundary_packs_not_downloaded(self, offline, tmp_path: Path, monkeypatch):
        import opensak.geo.packs as packs
        monkeypatch.setattr("urllib.request.urlopen", _no_network)
        assert packs.fetch_packs(["a.geojson"], tmp_path) == 0

    def test_boundary_baseline_not_downloaded(self, offline, tmp_path: Path, monkeypatch):
        import opensak.geo.packs as packs
        from opensak.geo import store
        monkeypatch.setattr(store, "_frozen_bundle_dir", lambda: None)
        monkeypatch.setattr(packs, "fetch_baseline", _no_network)
        store.ensure_baseline_seeded(tmp_path)

    def test_background_update_check_skipped(self, offline):
        pytest.importorskip("pytestqt")
        from opensak.gui.mainwindow import MainWindow
        with patch("opensak.msix.is_msix_packaged", return_value=False), \
             patch("opensak.gui.mainwindow.UpdateCheckWorker") as worker_cls, \
             patch("opensak.gui.settings.get_settings") as settings:
            settings.return_value.updates_check_enabled = True
            MainWindow._check_update_background(MagicMock())
        worker_cls.assert_not_called()

    def test_macro_index_check_skipped(self, offline, monkeypatch):
        pytest.importorskip("pytestqt")
        from opensak.gui.mainwindow import MainWindow
        from opensak.macro import repository as repo
        from opensak.utils import flags
        monkeypatch.setattr(flags, "lua_macros", True)
        monkeypatch.setattr(repo, "startup_check_due", lambda: True)
        with patch("opensak.gui.dialogs.macro_repository.start_task") as start:
            MainWindow._check_macro_updates_background(MagicMock())
        start.assert_not_called()


# ── Help → Check for updates asks first ───────────────────────────────────


class TestManualUpdateCheck:
    def _run(self, answer):
        pytest.importorskip("pytestqt")
        from PySide6.QtWidgets import QMessageBox
        from opensak.gui.mainwindow import MainWindow
        with patch("opensak.gui.mainwindow.UpdateCheckWorker") as worker_cls, \
             patch("opensak.gui.mainwindow.QMessageBox.question",
                   return_value=answer) as question:
            MainWindow._check_update_manual(MagicMock())
        return worker_cls, question, QMessageBox

    def test_no_means_no_request(self, offline):
        from PySide6.QtWidgets import QMessageBox
        worker_cls, question, _ = self._run(QMessageBox.StandardButton.No)
        question.assert_called_once()
        worker_cls.assert_not_called()

    def test_yes_checks(self, offline):
        from PySide6.QtWidgets import QMessageBox
        worker_cls, _, _ = self._run(QMessageBox.StandardButton.Yes)
        worker_cls.assert_called_once()

    def test_not_asked_when_online(self):
        from PySide6.QtWidgets import QMessageBox
        worker_cls, question, _ = self._run(QMessageBox.StandardButton.No)
        question.assert_not_called()
        worker_cls.assert_called_once()


# ── Requests that need the network fail at once ───────────────────────────


class TestFailFast:
    def test_api_call(self, offline, monkeypatch):
        from opensak.api import geocaching
        monkeypatch.setattr(geocaching, "GC_CLIENT_ID", "test-client")
        monkeypatch.setattr("urllib.request.urlopen", _no_network)
        monkeypatch.setattr(geocaching, "get_valid_token", _no_network)
        with pytest.raises(net.OfflineModeError):
            geocaching.get_user_profile()

    def test_oauth_login(self, offline, monkeypatch):
        from opensak.api import geocaching
        monkeypatch.setattr(geocaching, "GC_CLIENT_ID", "test-client")
        monkeypatch.setattr(geocaching, "_CallbackServer", _no_network)
        monkeypatch.setattr("webbrowser.open", _no_network)
        with pytest.raises(net.OfflineModeError):
            geocaching.start_oauth_flow()

    def test_imap_connect(self, offline, monkeypatch):
        import imaplib
        from opensak.email.connection import ImapConfig, check_connection
        monkeypatch.setattr(imaplib, "IMAP4_SSL", _no_network)
        monkeypatch.setattr(imaplib, "IMAP4", _no_network)
        with pytest.raises(net.OfflineModeError):
            check_connection(ImapConfig("imap.example.test", 993, True, "u"), "pw")

    def test_imap_error_is_not_a_network_error(self, offline):
        # The dialogs show it as it is, not as "could not reach the server".
        from opensak.email.connection import ImapConnectionError
        assert not issubclass(net.OfflineModeError, ImapConnectionError)


# ── Settings dialog ───────────────────────────────────────────────────────


@pytest.fixture
def settings_dlg(qtbot, monkeypatch):
    from opensak.gui.dialogs import settings_dialog as sd
    from opensak.gui.settings import AppSettings
    s = AppSettings()
    monkeypatch.setattr(sd, "get_settings", lambda: s)
    monkeypatch.setattr("opensak.gui.settings.get_settings", lambda: s)
    monkeypatch.setattr("opensak.db.manager.get_db_manager",
                        lambda: (_ for _ in ()).throw(RuntimeError("no manager in test")))
    monkeypatch.setattr("opensak.api.geocaching.is_logged_in", lambda: False)

    def _make():
        d = sd.SettingsDialog()
        qtbot.addWidget(d)
        return d
    return s, _make


class TestSettingsDialog:
    def test_checkbox_round_trip(self, settings_dlg):
        s, make = settings_dlg
        d = make()
        assert not d._offline_mode_cb.isChecked()
        d._offline_mode_cb.setChecked(True)
        d._save()
        assert s.offline_mode and net.offline_mode()
        assert make()._offline_mode_cb.isChecked()

    def test_env_var_locks_the_checkbox(self, settings_dlg, monkeypatch):
        s, make = settings_dlg
        monkeypatch.setenv(net.OFFLINE_ENV, "1")
        d = make()
        assert d._offline_mode_cb.isChecked() and not d._offline_mode_cb.isEnabled()
        from opensak.lang import tr
        assert d._offline_mode_hint.text() == tr("settings_offline_mode_env_hint",
                                                 name=net.OFFLINE_ENV)
        d._save()
        assert s.offline_mode is False   # the stored setting is left alone

    def test_profile_not_fetched_on_open(self, settings_dlg, monkeypatch, offline):
        _s, make = settings_dlg
        from opensak.gui.dialogs import settings_dialog as sd
        monkeypatch.setattr("opensak.api.geocaching.is_logged_in", lambda: True)
        fetched: list = []
        monkeypatch.setattr(sd.SettingsDialog, "_on_gc_refresh_profile",
                            lambda self: fetched.append(self))
        d = make()
        d._refresh_gc_status_on_open()
        assert fetched == []
        from opensak.lang import tr
        assert d._gc_status_label.text() == tr("gc_status_offline_mode")

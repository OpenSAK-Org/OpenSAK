# tests/unit-tests/test_pq_email_check_dialog.py — "Check for PQ Email"
# dialog (issue #443, session 2). No real mailbox, worker threads are
# either mocked out or tested via their handler methods directly (same
# convention as test_settings_dialog.py's TestWorkers).

import pytest

pytest.importorskip("pytestqt")

from opensak.gui.dialogs.pq_email_check_dialog import (
    PQEmailCheckDialog,
    PQEmailCheckWorker,
)
from opensak.email.connection import ImapAuthError, ImapConfig, ImapNetworkError
from opensak.email.service import PQImportOutcome, PQScanResult
from opensak.gui.settings import AppSettings


@pytest.fixture
def settings(monkeypatch):
    s = AppSettings()
    import opensak.gui.dialogs.pq_email_check_dialog as mod
    monkeypatch.setattr(mod, "get_settings", lambda: s)
    monkeypatch.setattr("opensak.gui.settings.get_settings", lambda: s)
    return s


@pytest.fixture
def dlg(qtbot, settings):
    d = PQEmailCheckDialog()
    qtbot.addWidget(d)
    d.show()
    return d


class TestConfiguredState:
    def test_not_configured_by_default(self, dlg):
        assert dlg._not_configured_label.isVisible() is True
        assert dlg._check_btn.isEnabled() is False

    def test_configured_when_host_and_username_set(self, qtbot, settings):
        settings.pq_email_host = "imap.example.com"
        settings.pq_email_username = "alice"
        d = PQEmailCheckDialog()
        qtbot.addWidget(d)
        assert d._not_configured_label.isVisible() is False
        assert d._check_btn.isEnabled() is True

    def test_missing_username_still_counts_as_not_configured(self, qtbot, settings):
        settings.pq_email_host = "imap.example.com"
        d = PQEmailCheckDialog()
        qtbot.addWidget(d)
        assert d._check_btn.isEnabled() is False

    def test_delete_checkbox_reflects_saved_setting(self, qtbot, settings):
        settings.pq_email_delete_after_import = True
        d = PQEmailCheckDialog()
        qtbot.addWidget(d)
        assert d._delete_cb.isChecked() is True

    def test_only_unseen_checkbox_defaults_to_checked(self, dlg):
        # Matches GSAK's "Only check new messages" default-checked behaviour.
        assert dlg._only_unseen_cb.isChecked() is True

    def test_only_unseen_checkbox_reflects_saved_setting(self, qtbot, settings):
        settings.pq_email_only_unseen = False
        d = PQEmailCheckDialog()
        qtbot.addWidget(d)
        assert d._only_unseen_cb.isChecked() is False


class TestOpenSettings:
    def test_switches_to_pq_email_tab(self, dlg, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.settings_dialog.SettingsDialog.exec",
            lambda self: None,
        )
        monkeypatch.setattr(
            "opensak.db.manager.get_db_manager",
            lambda: (_ for _ in ()).throw(RuntimeError("no manager in test")),
        )
        seen_index = []
        import opensak.gui.dialogs.settings_dialog as sd
        orig_init = sd.SettingsDialog.__init__

        def _patched_init(self, parent=None):
            orig_init(self, parent)
            orig_set = self._tabs.setCurrentIndex

            def _record(i):
                seen_index.append(i)
                return orig_set(i)
            self._tabs.setCurrentIndex = _record

        monkeypatch.setattr(sd.SettingsDialog, "__init__", _patched_init)
        dlg._open_settings()
        assert seen_index == [3]

    def test_refreshes_configured_state_after_returning(self, dlg, settings, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.settings_dialog.SettingsDialog.exec",
            lambda self: None,
        )
        # Simulate the user having configured the account while Settings was open.
        def _fake_exec(self):
            settings.pq_email_host = "imap.example.com"
            settings.pq_email_username = "alice"
        monkeypatch.setattr(
            "opensak.gui.dialogs.settings_dialog.SettingsDialog.exec", _fake_exec
        )
        assert dlg._check_btn.isEnabled() is False
        dlg._open_settings()
        assert dlg._check_btn.isEnabled() is True


class TestStartCheck:
    def test_warns_when_no_password_saved(self, qtbot, settings, monkeypatch):
        settings.pq_email_host = "imap.example.com"
        settings.pq_email_username = "alice"
        d = PQEmailCheckDialog()
        qtbot.addWidget(d)
        monkeypatch.setattr("opensak.email.credentials.get_password", lambda u: None)
        warned = []
        monkeypatch.setattr(
            "opensak.gui.dialogs.pq_email_check_dialog.QMessageBox.warning",
            lambda *a, **kw: warned.append(a),
        )
        d._start_check()
        assert warned

    def test_starts_worker_with_settings_derived_config(self, qtbot, settings, monkeypatch):
        settings.pq_email_host = "imap.example.com"
        settings.pq_email_port = 993
        settings.pq_email_use_ssl = True
        settings.pq_email_username = "alice"
        d = PQEmailCheckDialog()
        qtbot.addWidget(d)
        monkeypatch.setattr("opensak.email.credentials.get_password", lambda u: "s3cret")
        started = []
        monkeypatch.setattr(
            "opensak.gui.dialogs.pq_email_check_dialog.PQEmailCheckWorker.start",
            lambda self: started.append(self),
        )
        d._delete_cb.setChecked(True)
        d._only_unseen_cb.setChecked(False)
        d._start_check()
        assert len(started) == 1
        worker = started[0]
        assert worker._config.host == "imap.example.com"
        assert worker._config.username == "alice"
        assert worker._password == "s3cret"
        assert worker._delete_after_import is True
        assert worker._only_unseen is False
        assert d._check_btn.isEnabled() is False

    def test_saves_delete_preference_on_start(self, qtbot, settings, monkeypatch):
        settings.pq_email_host = "imap.example.com"
        settings.pq_email_username = "alice"
        d = PQEmailCheckDialog()
        qtbot.addWidget(d)
        monkeypatch.setattr("opensak.email.credentials.get_password", lambda u: "pw")
        monkeypatch.setattr(
            "opensak.gui.dialogs.pq_email_check_dialog.PQEmailCheckWorker.start",
            lambda self: None,
        )
        d._delete_cb.setChecked(True)
        d._start_check()
        assert settings.pq_email_delete_after_import is True

    def test_saves_only_unseen_preference_on_start(self, qtbot, settings, monkeypatch):
        settings.pq_email_host = "imap.example.com"
        settings.pq_email_username = "alice"
        d = PQEmailCheckDialog()
        qtbot.addWidget(d)
        monkeypatch.setattr("opensak.email.credentials.get_password", lambda u: "pw")
        monkeypatch.setattr(
            "opensak.gui.dialogs.pq_email_check_dialog.PQEmailCheckWorker.start",
            lambda self: None,
        )
        d._only_unseen_cb.setChecked(False)
        d._start_check()
        assert settings.pq_email_only_unseen is False


class TestResultHandling:
    def test_no_outcomes_shows_no_new_mail_message(self, dlg):
        from opensak.lang import tr
        dlg._on_result(PQScanResult(outcomes=[]))
        assert tr("pq_check_no_new_mail") in dlg._log.toPlainText()

    def test_successful_outcome_emits_import_completed(self, dlg, qtbot):
        from opensak.lang import load_language
        load_language("en")
        outcome = PQImportOutcome(
            zip_filename="North.zip", pq_name="North", matched_db_name="North",
            imported=True, created=5,
        )
        with qtbot.waitSignal(dlg.import_completed, timeout=1000):
            dlg._on_result(PQScanResult(outcomes=[outcome]))
        assert "North" in dlg._log.toPlainText()

    def test_failed_outcome_does_not_emit_import_completed(self, dlg):
        from opensak.lang import load_language
        load_language("en")
        outcome = PQImportOutcome(
            zip_filename="Bad.zip", pq_name="Bad", matched_db_name="North",
            imported=False, errors=["No .gpx file found in zip"],
        )
        received = []
        dlg.import_completed.connect(lambda: received.append(True))
        dlg._on_result(PQScanResult(outcomes=[outcome]))
        assert received == []
        assert "No .gpx file found in zip" in dlg._log.toPlainText()

    def test_error_kinds_produce_distinct_log_messages(self, dlg):
        from opensak.lang import load_language
        load_language("en")
        dlg._on_error("auth", "bad creds")
        auth_text = dlg._log.toPlainText()
        dlg._log.clear()
        dlg._on_error("network", "no route")
        network_text = dlg._log.toPlainText()
        assert "bad creds" in auth_text
        assert "no route" in network_text
        assert auth_text != network_text

    def test_on_done_reenables_buttons(self, dlg):
        dlg._check_btn.setEnabled(False)
        dlg._open_settings_btn.setEnabled(False)
        dlg._progress.setVisible(True)
        dlg._on_done()
        assert dlg._check_btn.isEnabled() is True
        assert dlg._open_settings_btn.isEnabled() is True
        assert dlg._progress.isVisible() is False


class TestWorker:
    def test_success_emits_result(self, monkeypatch):
        result = PQScanResult(outcomes=[])
        monkeypatch.setattr(
            "opensak.email.service.scan_and_import", lambda *a, **kw: result
        )
        config = ImapConfig(host="imap.example.com", port=993, use_ssl=True, username="alice")
        w = PQEmailCheckWorker(config, "pw", False, True)
        got = []
        w.result_ready.connect(got.append)
        w.run()
        assert got == [result]

    def test_auth_error_emits_error_signal(self, monkeypatch):
        def _raise(*a, **kw):
            raise ImapAuthError("bad login")
        monkeypatch.setattr("opensak.email.service.scan_and_import", _raise)
        config = ImapConfig(host="imap.example.com", port=993, use_ssl=True, username="alice")
        w = PQEmailCheckWorker(config, "wrong", False, True)
        errs = []
        w.error.connect(lambda kind, detail: errs.append((kind, detail)))
        w.run()
        assert errs == [("auth", "bad login")]

    def test_network_error_emits_error_signal(self, monkeypatch):
        def _raise(*a, **kw):
            raise ImapNetworkError("no route")
        monkeypatch.setattr("opensak.email.service.scan_and_import", _raise)
        config = ImapConfig(host="imap.example.com", port=993, use_ssl=True, username="alice")
        w = PQEmailCheckWorker(config, "pw", False, True)
        errs = []
        w.error.connect(lambda kind, detail: errs.append((kind, detail)))
        w.run()
        assert errs == [("network", "no route")]

# tests/unit-tests/test_msix_update_check_874.py — issue #874.
#
# The built-in update checker didn't distinguish installation sources on
# Windows: a Microsoft Store/MSIX install got the exact same "new version
# available, download here" popup as a portable/manual .exe user — even
# though the Store already updates the app automatically in the background
# (a real user hit this at every startup, see #883). Two behaviours are
# fixed and covered here:
#   1. _check_update_background() must not even start a check for MSIX
#      builds — the Store handles that silently.
#   2. _on_update_available(manual=True) must show a plain info message
#      (no "Download"/"Skip"/"Later" buttons) for MSIX builds instead of
#      the normal interactive dialog, since there's nothing the user
#      should do about it themselves.
# Non-MSIX behaviour (existing portable/.exe and AppImage flows) must be
# unchanged — covered by the "not packaged" tests below.

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("pytestqt")

from opensak.gui.mainwindow import MainWindow


@pytest.fixture(autouse=True)
def _quiet_startup(monkeypatch):
    # Keep window construction cheap/deterministic. _check_update_background
    # is deliberately NOT stubbed here (unlike the similar fixture in
    # test_previous_log_menu_737.py) — it's what this file tests. Leaving
    # it real is safe: __init__ only schedules it via
    # QTimer.singleShot(5000, ...), so it never actually fires during a
    # fast unit test unless a test calls it explicitly.
    monkeypatch.setattr(MainWindow, "_initial_load", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_setup_complete", lambda self: None)


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    import opensak.db.manager as mgr_module
    from opensak.db.database import init_db
    from opensak.lang import load_language
    from tests.data import make_fake_manager

    load_language("en")

    db_path = tmp_path / "test_msix_update_874.db"
    init_db(db_path=db_path)
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="MsixUpdateTest"))

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)

    yield win

    win.close()
    mgr_module._manager = None


# ── _check_update_background: MSIX builds must skip the check entirely ────

class TestCheckUpdateBackgroundMsixSkip:
    def test_msix_packaged_never_starts_a_worker(self, window):
        with patch("opensak.msix.is_msix_packaged", return_value=True), \
             patch("opensak.gui.mainwindow.UpdateCheckWorker") as mock_worker_cls, \
             patch("opensak.gui.settings.get_settings") as mock_settings:
            mock_settings.return_value.updates_check_enabled = True
            # Call the REAL method — the autouse fixture only stubs it on
            # the class for __init__-time startup, not for this explicit call.
            MainWindow._check_update_background(window)
        mock_worker_cls.assert_not_called()

    def test_not_msix_packaged_still_starts_a_worker_as_before(self, window):
        """Regression guard: the #874 fix must not affect non-MSIX builds."""
        with patch("opensak.msix.is_msix_packaged", return_value=False), \
             patch("opensak.gui.mainwindow.UpdateCheckWorker") as mock_worker_cls, \
             patch("opensak.gui.settings.get_settings") as mock_settings:
            mock_settings.return_value.updates_check_enabled = True
            mock_settings.return_value.notify_about_betas = False
            mock_instance = MagicMock()
            mock_worker_cls.return_value = mock_instance
            MainWindow._check_update_background(window)
        mock_worker_cls.assert_called_once()
        mock_instance.start.assert_called_once()

    def test_disabled_setting_wins_regardless_of_msix(self, window):
        """Existing behaviour: the user's own opt-out is checked first."""
        with patch("opensak.msix.is_msix_packaged", return_value=False), \
             patch("opensak.gui.mainwindow.UpdateCheckWorker") as mock_worker_cls, \
             patch("opensak.gui.settings.get_settings") as mock_settings:
            mock_settings.return_value.updates_check_enabled = False
            MainWindow._check_update_background(window)
        mock_worker_cls.assert_not_called()


# ── _on_update_available: manual check on MSIX shows a plain info box ─────

class TestOnUpdateAvailableMsix:
    def test_manual_check_on_msix_shows_info_only_no_action_buttons(self, window):
        # Patch only QMessageBox.information (a static method on the real
        # class) — NOT the QMessageBox class itself, which would also
        # shadow this same patched attribute and make the assertion
        # meaningless. The interactive dialog path would call
        # QMessageBox(self).exec(), which we don't reach here at all
        # (code returns right after .information()), so there's nothing
        # else to construct/patch to prove the branch was taken.
        with patch("opensak.msix.is_msix_packaged", return_value=True), \
             patch("opensak.gui.mainwindow.QMessageBox.information") as mock_info:
            MainWindow._on_update_available(
                window, "v1.20.0", "https://example.invalid/releases", False, manual=True
            )
        mock_info.assert_called_once()
        args, kwargs = mock_info.call_args
        assert args[0] is window
        assert args[1] == "Update handled by Microsoft Store"

    def test_background_path_is_unreachable_for_msix_because_check_is_skipped(
        self, window
    ):
        """
        Documents the actual invariant #874 relies on: MSIX builds never
        reach _on_update_available() via the background path at all,
        because _check_update_background() returns early (see the class
        above). manual=False + is_msix_packaged() is therefore not a
        real-world combination — this test just pins current behaviour
        (falls through to the normal dialog) so a future refactor can't
        silently change it without a test noticing.
        """
        with patch("opensak.msix.is_msix_packaged", return_value=True), \
             patch("opensak.gui.mainwindow.QMessageBox.information") as mock_info, \
             patch("opensak.gui.settings.get_settings") as mock_settings:
            mock_settings.return_value.updates_skipped_version = None
            fake_msg = MagicMock()
            fake_msg.clickedButton.return_value = None
            with patch("opensak.gui.mainwindow.QMessageBox", return_value=fake_msg):
                MainWindow._on_update_available(
                    window, "v1.20.0", "https://example.invalid/releases", False, manual=False
                )
        mock_info.assert_not_called()
        fake_msg.exec.assert_called_once()

    def test_manual_check_when_not_msix_shows_normal_interactive_dialog(self, window):
        """Regression guard: non-MSIX manual checks are unaffected by #874."""
        with patch("opensak.msix.is_msix_packaged", return_value=False), \
             patch("opensak.appimage.is_running_as_appimage", return_value=False), \
             patch("opensak.gui.mainwindow.QMessageBox.information") as mock_info:
            fake_msg = MagicMock()
            fake_msg.clickedButton.return_value = None
            with patch("opensak.gui.mainwindow.QMessageBox", return_value=fake_msg):
                MainWindow._on_update_available(
                    window, "v1.20.0", "https://example.invalid/releases", False, manual=True
                )
        mock_info.assert_not_called()
        fake_msg.exec.assert_called_once()
        fake_msg.addButton.assert_called()

# tests/unit-tests/test_self_update_wiring_572.py — issue #572.
#
# Covers the mainwindow.py wiring on top of updater.SelfUpdateWorker
# (already fully tested in isolation in test_self_update_572.py): which
# primary button _on_update_available() offers on which platform, and
# that _start_self_download_update() correctly drives the progress
# dialog and shows the right completion/error message.

import sys
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("pytestqt")

from opensak.gui.mainwindow import MainWindow


@pytest.fixture(autouse=True)
def _quiet_startup(monkeypatch):
    monkeypatch.setattr(MainWindow, "_initial_load", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_update_background", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_setup_complete", lambda self: None)


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    import opensak.db.manager as mgr_module
    from opensak.db.database import init_db
    from opensak.lang import load_language
    from tests.data import make_fake_manager

    load_language("en")

    db_path = tmp_path / "test_self_update_wiring_572.db"
    init_db(db_path=db_path)
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="SelfUpdateWiringTest"))

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)

    yield win

    win.close()
    mgr_module._manager = None


def _run_dialog_and_click_primary(window):
    """
    Runs _on_update_available(manual=True) with QMessageBox.exec()
    stubbed to auto-click whichever button was added first (the primary
    action) — mirrors how a real user clicking the primary button drives
    the rest of the method.
    """
    fake_msg = MagicMock()
    buttons: list = []

    def _add_button(text, role):
        b = MagicMock(name=f"button:{text}")
        buttons.append(b)
        return b

    fake_msg.addButton.side_effect = _add_button
    fake_msg.clickedButton.side_effect = lambda: buttons[0]  # primary is added first

    with patch("opensak.gui.mainwindow.QMessageBox", return_value=fake_msg):
        MainWindow._on_update_available(
            window, "v1.20.0", "https://example.invalid/releases", False, manual=True
        )
    return fake_msg


class TestPrimaryButtonChoicePerPlatform:
    def test_windows_offers_download_and_install(self, window):
        with patch("opensak.msix.is_msix_packaged", return_value=False), \
             patch("opensak.appimage.is_running_as_appimage", return_value=False), \
             patch.object(sys, "platform", "win32"), \
             patch.object(MainWindow, "_start_self_download_update") as mock_start:
            fake_msg = _run_dialog_and_click_primary(window)
        primary_text = fake_msg.addButton.call_args_list[0][0][0]
        assert primary_text == "Download & Install"
        mock_start.assert_called_once_with("v1.20.0")

    def test_macos_offers_download_and_install(self, window):
        with patch("opensak.msix.is_msix_packaged", return_value=False), \
             patch("opensak.appimage.is_running_as_appimage", return_value=False), \
             patch.object(sys, "platform", "darwin"), \
             patch.object(MainWindow, "_start_self_download_update") as mock_start:
            fake_msg = _run_dialog_and_click_primary(window)
        primary_text = fake_msg.addButton.call_args_list[0][0][0]
        assert primary_text == "Download & Install"
        mock_start.assert_called_once_with("v1.20.0")

    def test_appimage_integrated_still_takes_priority_over_self_download(self, window):
        """
        Structurally, is_running_as_appimage() only returns True on
        Linux, so this can't collide with sys.platform in ("win32",
        "darwin") in practice — but pin the *order* of the checks
        explicitly so a future refactor can't silently invert it.
        """
        with patch("opensak.msix.is_msix_packaged", return_value=False), \
             patch("opensak.appimage.is_running_as_appimage", return_value=True), \
             patch("opensak.appimage.is_appimage_integrated", return_value=True), \
             patch.object(sys, "platform", "linux"), \
             patch.object(MainWindow, "_start_appimage_self_update") as mock_appimage_start, \
             patch.object(MainWindow, "_start_self_download_update") as mock_download_start:
            fake_msg = _run_dialog_and_click_primary(window)
        primary_text = fake_msg.addButton.call_args_list[0][0][0]
        assert primary_text == "Upgrade now"
        mock_appimage_start.assert_called_once()
        mock_download_start.assert_not_called()

    def test_plain_linux_without_appimage_falls_back_to_open_releases(self, window):
        with patch("opensak.msix.is_msix_packaged", return_value=False), \
             patch("opensak.appimage.is_running_as_appimage", return_value=False), \
             patch.object(sys, "platform", "linux"), \
             patch("webbrowser.open") as mock_webopen:
            fake_msg = _run_dialog_and_click_primary(window)
        primary_text = fake_msg.addButton.call_args_list[0][0][0]
        assert primary_text == "Download new version"
        mock_webopen.assert_called_once_with("https://example.invalid/releases")


class TestStartSelfDownloadUpdate:
    def test_progress_signal_updates_dialog(self, window):
        with patch("opensak.updater.SelfUpdateWorker") as mock_worker_cls:
            mock_instance = MagicMock()
            mock_worker_cls.return_value = mock_instance
            with patch("opensak.gui.mainwindow.QProgressDialog") as mock_progress_cls:
                mock_progress = MagicMock()
                mock_progress_cls.return_value = mock_progress
                MainWindow._start_self_download_update(window, "v1.20.0")

                # Grab the connected progress slot and call it directly.
                progress_slot = mock_instance.progress.connect.call_args[0][0]
                progress_slot(50, 200)

        mock_progress.setValue.assert_called_with(25)

    def test_finished_ok_shows_done_message(self, window):
        with patch("opensak.updater.SelfUpdateWorker") as mock_worker_cls, \
             patch("opensak.gui.mainwindow.QMessageBox.information") as mock_info:
            mock_instance = MagicMock()
            mock_worker_cls.return_value = mock_instance
            MainWindow._start_self_download_update(window, "v1.20.0")
            ok_slot = mock_instance.finished_ok.connect.call_args[0][0]
            ok_slot("/tmp/OpenSAK-v1.20.0-Windows.zip")
        mock_info.assert_called_once()

    def test_finished_error_maps_known_code_to_friendly_message(self, window):
        with patch("opensak.updater.SelfUpdateWorker") as mock_worker_cls, \
             patch("opensak.gui.mainwindow.QMessageBox.warning") as mock_warning:
            mock_instance = MagicMock()
            mock_worker_cls.return_value = mock_instance
            MainWindow._start_self_download_update(window, "v1.20.0")
            error_slot = mock_instance.finished_error.connect.call_args[0][0]
            error_slot("checksum_mismatch")
        mock_warning.assert_called_once()
        args, kwargs = mock_warning.call_args
        assert "checksum verification" in args[2]

    def test_finished_error_passes_through_unknown_code_verbatim(self, window):
        with patch("opensak.updater.SelfUpdateWorker") as mock_worker_cls, \
             patch("opensak.gui.mainwindow.QMessageBox.warning") as mock_warning:
            mock_instance = MagicMock()
            mock_worker_cls.return_value = mock_instance
            MainWindow._start_self_download_update(window, "v1.20.0")
            error_slot = mock_instance.finished_error.connect.call_args[0][0]
            error_slot("[Errno 2] some raw OSError text")
        args, kwargs = mock_warning.call_args
        assert "[Errno 2] some raw OSError text" in args[2]

"""
tests/unit-tests/test_wizard_cancel_1288.py — issue #1288.

"Skip setup" was the only visible way out of the Welcome Wizard, but it saved
the defaults and marked the wizard as done, so it never came back. Closing the
window did something else: nothing saved, wizard shown again. Now:

* first run: Cancel, the close button and Esc ask, then quit OpenSAK without
  saving; the wizard shows again next time. "Use default folders" saves the
  defaults and continues.
* run again from Settings: Cancel just closes; "Use default folders" is hidden.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from opensak import settings_store as ss
from opensak.gui.dialogs import welcome_wizard as ww
from opensak.lang import load_language, tr


@pytest.fixture(autouse=True)
def _language():
    load_language("en")


@pytest.fixture(autouse=True)
def _isolated_bootstrap(tmp_path, monkeypatch):
    bootstrap = tmp_path / "bootstrap.json"
    monkeypatch.setattr(ss, "_bootstrap_path", lambda: bootstrap)
    monkeypatch.setattr(ss, "_store", None)
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    ss.set_install_dir(install_dir)
    return bootstrap


@pytest.fixture
def make(qtbot):
    def _make(first_run: bool) -> ww.WelcomeWizard:
        w = ww.WelcomeWizard(first_run=first_run)
        qtbot.addWidget(w)
        return w
    return _make


@pytest.fixture
def answer(monkeypatch):
    """Answer the 'Quit OpenSAK?' question; records how often it was asked."""
    asked: list[bool] = []

    def _set(quit_: bool):
        def _confirm(self):
            asked.append(True)
            return quit_
        monkeypatch.setattr(ww.WelcomeWizard, "_confirm_quit", _confirm)
        return asked
    return _set


class TestButtons:
    def test_first_run_has_cancel_and_use_defaults(self, make):
        w = make(first_run=True)
        assert w._cancel_btn.text() == tr("wizard_cancel")
        assert w._skip_btn.text() == "Use default folders"
        assert not w._skip_btn.isHidden()
        assert "Run setup wizard again" in w._skip_btn.toolTip()

    def test_run_again_hides_use_defaults(self, make):
        w = make(first_run=False)
        assert w._skip_btn.isHidden()
        assert not w._cancel_btn.isHidden()


class TestFirstRun:
    def test_cancel_asks_then_quits_without_saving(self, make, answer):
        asked = answer(True)
        w = make(first_run=True)
        w._cancel_btn.click()
        assert asked == [True]
        assert w.result() == QDialog.DialogCode.Rejected
        assert ss.is_first_run() is True      # wizard shows again next time

    def test_continue_setup_keeps_the_wizard_open(self, make, answer, qtbot):
        asked = answer(False)
        w = make(first_run=True)
        w.show()
        w._cancel_btn.click()
        assert asked == [True]
        assert w.isVisible()

    def test_close_button_and_esc_behave_like_cancel(self, make, answer, qtbot):
        asked = answer(False)
        w = make(first_run=True)
        w.show()
        w.close()
        qtbot.keyClick(w, Qt.Key.Key_Escape)
        assert asked == [True, True]
        assert w.isVisible()

    def test_use_default_folders_saves_and_continues(self, make, answer):
        asked = answer(True)
        w = make(first_run=True)
        w._skip_btn.click()
        assert asked == []
        assert w.result() == QDialog.DialogCode.Accepted
        assert ss.is_first_run() is False


class TestRunAgainFromSettings:
    def test_cancel_closes_without_asking(self, make, answer):
        asked = answer(True)
        w = make(first_run=False)
        w._cancel_btn.click()
        assert asked == []
        assert w.result() == QDialog.DialogCode.Rejected


class TestTexts:
    def test_folder_is_called_settings_folder(self):
        assert tr("wizard_install_dir_title") == "Settings Folder"
        assert "installation" not in tr("settings_install_dir_label").lower()
        assert "installation" not in tr("settings_run_wizard_restart_notice").lower()

    def test_tooltip_names_the_button_in_every_language(self):
        from opensak.lang import AVAILABLE_LANGUAGES
        for code in AVAILABLE_LANGUAGES:
            load_language(code)
            assert tr("settings_run_wizard_button") in tr(
                "wizard_skip_tooltip", button=tr("settings_run_wizard_button")), code
        load_language("en")


class TestStartup:
    def test_app_quits_when_the_first_run_wizard_is_cancelled(self):
        import inspect
        from opensak import app
        src = inspect.getsource(app)
        assert "WelcomeWizard(first_run=True)" in src
        assert "if wizard.exec() != QDialog.DialogCode.Accepted:" in src

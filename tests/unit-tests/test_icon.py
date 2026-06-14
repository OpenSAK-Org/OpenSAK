"""tests/unit-tests/test_icon.py — app icon loader + OpenSAKMessageBox."""

import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMessageBox

from opensak.gui import icon as ic

ASSETS = Path(__file__).resolve().parents[2] / "assets" / "icons"


# ── _icon_dir ─────────────────────────────────────────────────────────────────

class TestIconDir:
    def test_pyinstaller_bundle(self, monkeypatch):
        monkeypatch.setattr(ic.sys, "_MEIPASS", "/fake/bundle", raising=False)
        assert ic._icon_dir() == Path("/fake/bundle") / "assets" / "icons"

    def test_source_repo(self, monkeypatch):
        monkeypatch.delattr(ic.sys, "_MEIPASS", raising=False)
        assert ic._icon_dir() == ASSETS

    def test_fallback_to_module_dir(self, monkeypatch):
        monkeypatch.delattr(ic.sys, "_MEIPASS", raising=False)
        monkeypatch.setattr(ic.Path, "exists", lambda self: False)
        got = ic._icon_dir()
        assert got == Path(ic.__file__).resolve().parent


# ── get_app_icon ──────────────────────────────────────────────────────────────

class TestGetAppIcon:
    def test_loads_pngs_from_assets(self, qapp):
        result = ic.get_app_icon()
        assert isinstance(result, QIcon)
        assert not result.isNull()

    def test_win32_ico_branch(self, qapp, monkeypatch, tmp_path):
        shutil.copy(ASSETS / "opensak.png", tmp_path / "opensak.ico")
        monkeypatch.setattr(ic, "_icon_dir", lambda: tmp_path)
        monkeypatch.setattr(ic.sys, "platform", "win32")
        assert not ic.get_app_icon().isNull()

    def test_darwin_icns_branch(self, qapp, monkeypatch, tmp_path):
        shutil.copy(ASSETS / "opensak.png", tmp_path / "opensak.icns")
        monkeypatch.setattr(ic, "_icon_dir", lambda: tmp_path)
        monkeypatch.setattr(ic.sys, "platform", "darwin")
        assert not ic.get_app_icon().isNull()

    def test_named_png_loop_adds_pixmaps(self, qapp, monkeypatch, tmp_path):
        shutil.copy(ASSETS / "opensak_16.png", tmp_path / "opensak_16.png")
        shutil.copy(ASSETS / "opensak.png", tmp_path / "opensak.png")
        monkeypatch.setattr(ic, "_icon_dir", lambda: tmp_path)
        monkeypatch.setattr(ic.sys, "platform", "linux")
        assert not ic.get_app_icon().isNull()

    def test_fallback_glob_branch(self, qapp, monkeypatch, tmp_path):
        shutil.copy(ASSETS / "opensak.png", tmp_path / "opensak_weird.png")
        monkeypatch.setattr(ic, "_icon_dir", lambda: tmp_path)
        monkeypatch.setattr(ic.sys, "platform", "linux")
        assert not ic.get_app_icon().isNull()

    def test_no_icons_returns_empty(self, qapp, monkeypatch, tmp_path):
        monkeypatch.setattr(ic, "_icon_dir", lambda: tmp_path)
        monkeypatch.setattr(ic.sys, "platform", "linux")
        assert ic.get_app_icon().isNull()


# ── set_taskbar_icon ──────────────────────────────────────────────────────────

def test_set_taskbar_icon(qapp):
    captured = []

    class FakeWin:
        def setWindowIcon(self, i):
            captured.append(i)

    ic.set_taskbar_icon(FakeWin())
    assert len(captured) == 1
    assert isinstance(captured[0], QIcon)


# ── OpenSAKMessageBox ─────────────────────────────────────────────────────────

class TestMessageBox:
    @pytest.fixture(autouse=True)
    def _no_exec(self, monkeypatch):
        monkeypatch.setattr(ic.QMessageBox, "exec",
                            lambda self: QMessageBox.StandardButton.Ok)

    def test_init_sets_window_icon(self, qapp):
        box = ic.OpenSAKMessageBox()
        assert not box.windowIcon().isNull()

    def test_information(self, qapp):
        assert ic.OpenSAKMessageBox.information(None, "T", "msg") == \
            QMessageBox.StandardButton.Ok

    def test_warning(self, qapp):
        assert ic.OpenSAKMessageBox.warning(None, "T", "msg") == \
            QMessageBox.StandardButton.Ok

    def test_critical(self, qapp):
        assert ic.OpenSAKMessageBox.critical(None, "T", "msg") == \
            QMessageBox.StandardButton.Ok

    def test_question(self, qapp):
        assert ic.OpenSAKMessageBox.question(None, "T", "msg") == \
            QMessageBox.StandardButton.Ok

    def test_about(self, qapp):
        ic.OpenSAKMessageBox.about(None, "T", "msg")  # returns None, no crash

    def test_default_button_set(self, qapp):
        ic.OpenSAKMessageBox.information(
            None, "T", "msg",
            default_button=QMessageBox.StandardButton.Ok)

# tests/unit-tests/test_theme.py — cross-platform theme management.

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from opensak.gui import theme


# ── palettes ───────────────────────────────────────────────────────────────────

def test_light_palette_window_is_light():
    p = theme._light_palette()
    assert isinstance(p, QPalette)
    assert p.color(QPalette.ColorRole.Window).lightness() > 200


def test_dark_palette_window_is_dark():
    p = theme._dark_palette()
    assert isinstance(p, QPalette)
    assert p.color(QPalette.ColorRole.Window).lightness() < 100


# ── system dark detection ───────────────────────────────────────────────────────

class TestSystemIsDark:
    def test_macos_dark(self, monkeypatch):
        monkeypatch.setattr(theme.platform, "system", lambda: "Darwin")
        import subprocess
        from types import SimpleNamespace
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="Dark\n")
        )
        assert theme._system_is_dark() is True

    def test_macos_light(self, monkeypatch):
        monkeypatch.setattr(theme.platform, "system", lambda: "Darwin")
        import subprocess
        from types import SimpleNamespace
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="")
        )
        assert theme._system_is_dark() is False

    def test_macos_subprocess_failure(self, monkeypatch):
        monkeypatch.setattr(theme.platform, "system", lambda: "Darwin")
        import subprocess
        def boom(*a, **k):
            raise OSError("no defaults")
        monkeypatch.setattr(subprocess, "run", boom)
        assert theme._system_is_dark() is False

    def test_linux_gtk_theme_dark(self, monkeypatch):
        monkeypatch.setattr(theme.platform, "system", lambda: "Linux")
        monkeypatch.setenv("GTK_THEME", "Adwaita-dark")
        assert theme._system_is_dark() is True

    def test_linux_portal_dark(self, monkeypatch):
        monkeypatch.setattr(theme.platform, "system", lambda: "Linux")
        monkeypatch.delenv("GTK_THEME", raising=False)
        monkeypatch.delenv("DESKTOP_SESSION", raising=False)
        import subprocess
        from types import SimpleNamespace
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: SimpleNamespace(stdout="(<<uint32 1>>,)"),
        )
        assert theme._system_is_dark() is True

    def test_linux_default_light(self, monkeypatch):
        monkeypatch.setattr(theme.platform, "system", lambda: "Linux")
        monkeypatch.delenv("GTK_THEME", raising=False)
        monkeypatch.delenv("DESKTOP_SESSION", raising=False)
        import subprocess
        def boom(*a, **k):
            raise OSError("no gdbus")
        monkeypatch.setattr(subprocess, "run", boom)
        assert theme._system_is_dark() is False


# ── default font ────────────────────────────────────────────────────────────────

class TestDefaultFont:
    def test_macos(self, monkeypatch):
        monkeypatch.setattr(theme.platform, "system", lambda: "Darwin")
        assert theme._default_font().family() == "SF Pro Text"

    def test_windows(self, monkeypatch):
        monkeypatch.setattr(theme.platform, "system", lambda: "Windows")
        assert theme._default_font().family() == "Segoe UI"

    def test_linux(self, monkeypatch):
        monkeypatch.setattr(theme.platform, "system", lambda: "Linux")
        assert theme._default_font().family() == "Ubuntu"


# ── apply_theme ─────────────────────────────────────────────────────────────────

class TestApplyTheme:
    def test_light(self, qapp):
        theme.apply_theme(qapp, "light")
        assert qapp.style().objectName().lower() == "fusion"
        assert qapp.palette().color(QPalette.ColorRole.Window).lightness() > 200

    def test_dark(self, qapp):
        theme.apply_theme(qapp, "dark")
        assert qapp.palette().color(QPalette.ColorRole.Window).lightness() < 100

    def test_auto_uses_system(self, qapp, monkeypatch):
        monkeypatch.setattr(theme, "_system_is_dark", lambda: True)
        theme.apply_theme(qapp, "auto")
        assert qapp.palette().color(QPalette.ColorRole.Window).lightness() < 100

    def test_none_reads_settings(self, qapp, monkeypatch):
        from types import SimpleNamespace
        monkeypatch.setattr(
            "opensak.gui.settings.get_settings",
            lambda: SimpleNamespace(theme="light"),
        )
        theme.apply_theme(qapp, None)
        assert qapp.palette().color(QPalette.ColorRole.Window).lightness() > 200

    def test_none_settings_failure_falls_back_auto(self, qapp, monkeypatch):
        def boom():
            raise RuntimeError("no settings")
        monkeypatch.setattr("opensak.gui.settings.get_settings", boom)
        monkeypatch.setattr(theme, "_system_is_dark", lambda: False)
        theme.apply_theme(qapp, None)  # should not raise
        assert qapp.palette().color(QPalette.ColorRole.Window).lightness() > 200

    def test_repaints_existing_top_level_widgets(self, qtbot, qapp):
        from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
        w = QWidget()
        QVBoxLayout(w).addWidget(QLabel("child"))
        qtbot.addWidget(w)
        theme.apply_theme(qapp, "dark")
        assert w.palette().color(QPalette.ColorRole.Window).lightness() < 100

    def test_palette_stylesheet_refs_reapplied_on_theme_switch(self, qtbot, qapp):
        # Regression for #325: palette() refs in stylesheets were not re-evaluated
        # after a theme switch because update() alone does not re-parse the stylesheet.
        from PySide6.QtWidgets import QWidget, QLabel
        w = QWidget()
        lbl = QLabel("stats", w)
        lbl.setStyleSheet("color: palette(text); font-size: 11px;")
        w.setStyleSheet("QWidget { background-color: palette(window); }")
        qtbot.addWidget(w)
        w.show()

        theme.apply_theme(qapp, "dark")
        assert w.palette().color(QPalette.ColorRole.Window).lightness() < 100
        assert "palette(window)" in w.styleSheet()

        theme.apply_theme(qapp, "light")
        assert w.palette().color(QPalette.ColorRole.Window).lightness() > 200
        # stylesheet must survive re-application intact
        assert "palette(window)" in w.styleSheet()
        assert "palette(text)" in lbl.styleSheet()


# ── effective_theme ─────────────────────────────────────────────────────────────

class TestEffectiveTheme:
    def test_explicit_light(self):
        assert theme.effective_theme("light") == "light"

    def test_explicit_dark(self):
        assert theme.effective_theme("dark") == "dark"

    def test_invalid_defaults_light(self):
        assert theme.effective_theme("weird") == "light"

    def test_auto_dark(self, monkeypatch):
        monkeypatch.setattr(theme, "_system_is_dark", lambda: True)
        assert theme.effective_theme("auto") == "dark"

    def test_auto_light(self, monkeypatch):
        monkeypatch.setattr(theme, "_system_is_dark", lambda: False)
        assert theme.effective_theme("auto") == "light"

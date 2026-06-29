"""
src/opensak/gui/theme.py — Cross-platform theme management for OpenSAK.

Ensures a consistent look on Linux, Windows and macOS by forcing Qt's
Fusion style as the base and overlaying a Light or Dark palette on top.

Usage:
    from opensak.gui.theme import apply_theme
    apply_theme(app)           # call once, right after QApplication is created
    apply_theme(app, "dark")   # switch at runtime
"""

from __future__ import annotations

import platform
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication


# ── Palette definitions ───────────────────────────────────────────────────────

def _light_palette():
    from PySide6.QtGui import QPalette, QColor
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(0xF5F5F5))
    p.setColor(QPalette.ColorRole.WindowText,      QColor(0x1A1A1A))
    p.setColor(QPalette.ColorRole.Base,            QColor(0xFFFFFF))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(0xECECEC))
    p.setColor(QPalette.ColorRole.Text,            QColor(0x1A1A1A))
    p.setColor(QPalette.ColorRole.Button,          QColor(0xE8E8E8))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(0x1A1A1A))
    p.setColor(QPalette.ColorRole.BrightText,      QColor(0xFFFFFF))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(0x2196F3))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(0xFFFFFF))
    p.setColor(QPalette.ColorRole.Link,            QColor(0x1565C0))
    p.setColor(QPalette.ColorRole.LinkVisited,     QColor(0x6A1B9A))
    p.setColor(QPalette.ColorRole.ToolTipBase,     QColor(0xFFFDE7))
    p.setColor(QPalette.ColorRole.ToolTipText,     QColor(0x1A1A1A))
    p.setColor(QPalette.ColorRole.Mid,             QColor(0xBDBDBD))
    p.setColor(QPalette.ColorRole.Midlight,        QColor(0xE0E0E0))
    p.setColor(QPalette.ColorRole.Dark,            QColor(0x9E9E9E))
    p.setColor(QPalette.ColorRole.Shadow,          QColor(0x757575))
    # Disabled colours
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(0xA0A0A0))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor(0xA0A0A0))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(0xA0A0A0))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight,  QColor(0xBDBDBD))
    return p


def _dark_palette():
    from PySide6.QtGui import QPalette, QColor
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(0x2B2B2B))
    p.setColor(QPalette.ColorRole.WindowText,      QColor(0xE8E8E8))
    p.setColor(QPalette.ColorRole.Base,            QColor(0x1E1E1E))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(0x252525))
    p.setColor(QPalette.ColorRole.Text,            QColor(0xE8E8E8))
    p.setColor(QPalette.ColorRole.Button,          QColor(0x3C3C3C))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(0xE8E8E8))
    p.setColor(QPalette.ColorRole.BrightText,      QColor(0xFFFFFF))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(0x1976D2))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(0xFFFFFF))
    p.setColor(QPalette.ColorRole.Link,            QColor(0x64B5F6))
    p.setColor(QPalette.ColorRole.LinkVisited,     QColor(0xCE93D8))
    p.setColor(QPalette.ColorRole.ToolTipBase,     QColor(0x3C3C3C))
    p.setColor(QPalette.ColorRole.ToolTipText,     QColor(0xE8E8E8))
    p.setColor(QPalette.ColorRole.Mid,             QColor(0x555555))
    p.setColor(QPalette.ColorRole.Midlight,        QColor(0x444444))
    p.setColor(QPalette.ColorRole.Dark,            QColor(0x222222))
    p.setColor(QPalette.ColorRole.Shadow,          QColor(0x111111))
    # Disabled colours
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(0x666666))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor(0x666666))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(0x666666))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight,  QColor(0x444444))
    return p


# ── System dark-mode detection ────────────────────────────────────────────────

def _system_is_dark() -> bool:
    """
    Return True if the OS is currently in dark mode.
    Works on macOS 10.14+, Windows 10+, and modern Linux desktops.
    """
    os_name = platform.system()

    if os_name == "Darwin":
        try:
            import subprocess
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout.strip().lower() == "dark"
        except Exception:
            return False

    if os_name == "Windows":
        try:
            import winreg
            sub = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub)  # type: ignore[attr-defined]
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")  # type: ignore[attr-defined]
            return value == 0
        except Exception:
            return False

    # Linux — try several environment variables / portals
    try:
        # Check GTK theme name
        gtk_theme = (
            __import__("os").environ.get("GTK_THEME", "")
            or __import__("os").environ.get("DESKTOP_SESSION", "")
        ).lower()
        if "dark" in gtk_theme:
            return True
    except Exception:
        pass

    try:
        # Try org.freedesktop.appearance color-scheme via dbus
        import subprocess
        result = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.freedesktop.portal.Desktop",
                "--object-path", "/org/freedesktop/portal/desktop",
                "--method", "org.freedesktop.portal.Settings.Read",
                "org.freedesktop.appearance", "color-scheme",
            ],
            capture_output=True, text=True, timeout=2,
        )
        # Returns "(<<uint32 1>>,)" for dark, "(<<uint32 2>>,)" for light
        if "1" in result.stdout:
            return True
    except Exception:
        pass

    return False


# ── Font defaults per OS ──────────────────────────────────────────────────────

def _default_font():
    from PySide6.QtGui import QFont
    os_name = platform.system()
    if os_name == "Darwin":
        return QFont("SF Pro Text", 13)
    if os_name == "Windows":
        return QFont("Segoe UI", 10)
    # Linux — Ubuntu/GNOME default, falls back to system sans-serif
    return QFont("Ubuntu", 10)


# ── Public API ────────────────────────────────────────────────────────────────

THEME_AUTO  = "auto"
THEME_LIGHT = "light"
THEME_DARK  = "dark"

VALID_THEMES = (THEME_AUTO, THEME_LIGHT, THEME_DARK)


def apply_theme(app: "QApplication", theme: str | None = None) -> None:
    """
    Apply Fusion style + matching palette to *app*.

    Parameters
    ----------
    app:
        The running QApplication instance.
    theme:
        One of ``"auto"``, ``"light"``, ``"dark"``.
        When *None* the saved preference is read from AppSettings.
    """
    if theme is None:
        try:
            from opensak.gui.settings import get_settings
            theme = get_settings().theme
        except Exception:
            theme = THEME_AUTO

    # Always use Fusion — consistent baseline across all OS
    app.setStyle("Fusion")

    # Apply platform-appropriate font
    app.setFont(_default_font())

    # Resolve "auto"
    if theme == THEME_AUTO:
        dark = _system_is_dark()
    else:
        dark = theme == THEME_DARK

    palette = _dark_palette() if dark else _light_palette()
    app.setPalette(palette)

    from PySide6.QtWidgets import QWidget

    # Qt does not always propagate a new palette to already-visible widgets
    # automatically — we need to poke each top-level window so it repaints.
    # Widgets with palette() references in their stylesheets also need
    # setStyleSheet() re-called to force re-evaluation of those references;
    # update() alone repaints but does not re-parse the cached stylesheet.
    for widget in app.topLevelWidgets():
        widget.setPalette(palette)
        if widget.styleSheet():
            widget.setStyleSheet(widget.styleSheet())
        widget.update()
        for child in widget.findChildren(QWidget):
            child.setPalette(palette)
            if child.styleSheet():
                child.setStyleSheet(child.styleSheet())
            child.update()


def effective_theme(theme: str) -> str:
    """Return the resolved theme (``"light"`` or ``"dark"``) for *theme*."""
    if theme == THEME_AUTO:
        return "dark" if _system_is_dark() else "light"
    return theme if theme in (THEME_LIGHT, THEME_DARK) else "light"

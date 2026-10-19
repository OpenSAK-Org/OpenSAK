"""
src/opensak/gui/dialogs/widgets.py — Delte, genbrugelige dialog-widgets.

Indeholder små UI-komponenter der bruges i flere dialoger, så de kun
implementeres og vedligeholdes ét sted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QLineEdit, QPushButton,
    QSizePolicy, QWidget,
)

from opensak.lang import tr


def clamp_dialog_height_to_screen(
    dialog: QDialog, parent: QWidget | None = None, max_fraction: float = 0.9
) -> None:
    """
    Cap a dialog's maximum height to a fraction of its screen's available
    height, so it can never grow taller than the screen (issue #811).

    Without this, a QDialog with no height constraint sizes itself to fit
    all of its content — fine on a normal desktop monitor, but on a
    high-resolution display at high DPI scaling (e.g. 2560x1600 @ 200%,
    ~1280x800 effective logical pixels — the exact case Microsoft Store
    certification caught on 31 Aug 2026, see #811) that can exceed the
    screen's available height, cutting off content at the bottom with no
    way to reach it.

    This only sets a ceiling — it does not resize or reposition the
    dialog. Pairing it with a QScrollArea around the dialog's content (see
    settings_dialog.py's tabs for the pattern) is what actually lets
    content beyond the cap be reached, by scrolling instead of just
    getting clipped. A dialog whose content doesn't scroll will still be
    silently clipped once capped — audit before relying on this alone.

    Call this once, early in __init__ (before or after building the UI —
    it only affects the maximum, not the initial size).

    filter_dialog.py's #580 fix uses a related but distinct pattern
    (resize + center to a *fraction* of screen size as the dialog's
    *initial* size, and re-derives the screen from the parent to respect
    multi-monitor setups) — kept separate here rather than merged into
    this helper, since that dialog's need (a sensible initial size) and
    this one (a hard ceiling on an otherwise-unconstrained sizeHint) are
    different problems that happen to both start from screen geometry.
    """
    screen = parent.screen() if parent is not None else None
    if screen is None:
        screen = QApplication.primaryScreen()
    if screen:
        dialog.setMaximumHeight(int(screen.availableGeometry().height() * max_fraction))



# ── Remembered dialog size/position (issue #1030) ────────────────────────────

_GEOMETRY_KEY_PREFIX = "dialog_geometry."


def remember_dialog_geometry(dialog: QDialog, key: str) -> None:
    """
    Restore *dialog*'s last size, position and maximized state, and save
    them again whenever it closes (issue #1030).

    Stored as plain numbers under ``dialog_geometry.<key>`` in opensak.json
    rather than Qt's opaque saveGeometry() blob, so the restore can be
    checked against the screens that exist *now*. A remembered geometry is
    never trusted blindly (GeePa67's point on #1030): a laptop that was on a
    big external monitor last time may be on its own small screen today.

    - The size is capped to the available area of the screen it lands on,
      so the dialog never opens bigger than the screen, and any maximum
      size the dialog already has (e.g. clamp_dialog_height_to_screen)
      still applies.
    - The position is kept only if the remembered window's centre is on a
      screen that is connected now, and is then nudged back inside that
      screen's available area, so the title bar can always be reached.
    - If that screen is gone, only the (capped) size is restored and Qt
      places the dialog as usual, centred over its parent.

    Call this once at the end of __init__, after the content is built and
    after any clamp_dialog_height_to_screen() call.
    """
    from opensak.settings_store import get_store

    store_key = _GEOMETRY_KEY_PREFIX + key
    _apply_saved_geometry(dialog, get_store().get(store_key))

    def _save(_result: int = 0) -> None:
        snapshot = _geometry_snapshot(dialog, get_store().get(store_key))
        if snapshot is not None:
            get_store().set(store_key, snapshot)

    dialog.finished.connect(_save)


def _geometry_snapshot(dialog: QDialog, previous: Any) -> dict[str, Any] | None:
    """The values to store for *dialog* as it closes."""
    if dialog.isMaximized():
        # A maximized window's own geometry is just "the whole screen" —
        # keep the last normal size/position so un-maximizing next time
        # goes back to something sensible.
        if not isinstance(previous, dict):
            return {"maximized": True}
        return {**previous, "maximized": True}
    frame = dialog.frameGeometry()
    return {
        "x": frame.x(),
        "y": frame.y(),
        "w": dialog.width(),
        "h": dialog.height(),
        "maximized": False,
    }


def _apply_saved_geometry(dialog: QDialog, data: Any) -> None:
    """Apply a stored geometry dict, clamped to the screens connected now."""
    if not isinstance(data, dict):
        return
    try:
        w, h = int(data["w"]), int(data["h"])
    except (KeyError, TypeError, ValueError):
        w = h = 0
    try:
        x, y = int(data["x"]), int(data["y"])
        has_pos = True
    except (KeyError, TypeError, ValueError):
        x = y = 0
        has_pos = False

    if w > 0 and h > 0:
        # Which screen was the dialog on? Judge by its centre; if that
        # monitor isn't connected any more, fall back to the parent's
        # screen (or the primary one) and drop the old position.
        screen = (
            QGuiApplication.screenAt(QPoint(x + w // 2, y + h // 2))
            if has_pos else None
        )
        if screen is None:
            has_pos = False
            parent = dialog.parentWidget()
            screen = parent.screen() if parent is not None else None
            if screen is None:
                screen = QApplication.primaryScreen()

        if screen is not None:
            avail = screen.availableGeometry()
            w = min(w, avail.width())
            h = min(h, avail.height())
            dialog.resize(w, h)
            if has_pos:
                # Use the size the dialog really got (min/max sizes may
                # have adjusted it) and keep the top-left — where the title
                # bar is — on screen.
                real_w, real_h = dialog.width(), dialog.height()
                x = max(avail.left(), min(x, avail.right() + 1 - real_w))
                y = max(avail.top(), min(y, avail.bottom() + 1 - real_h))
                dialog.move(x, y)

    if data.get("maximized") is True:
        dialog.setWindowState(dialog.windowState() | Qt.WindowState.WindowMaximized)


class DirRow(QWidget):
    """
    En linje med en read-only sti og en Gennemse-knap.

    Bruges af velkomst-wizarden (#210) og Settings → Advanced til at vise
    og vælge installations- og database-mapper.
    """

    def __init__(self, path: Path, parent=None, browsable: bool = True):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._edit = QLineEdit(str(path))
        self._edit.setReadOnly(True)
        lay.addWidget(self._edit)
        if browsable:
            self._btn = QPushButton(tr("wizard_browse"))
            # #927: size to the translated label instead of a hardcoded width,
            # so longer translations are never clipped.
            self._btn.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
            )
            lay.addWidget(self._btn)
            self._btn.clicked.connect(self._browse)

    def _browse(self) -> None:
        current = Path(self._edit.text())
        chosen = QFileDialog.getExistingDirectory(
            self,
            tr("wizard_choose_dir"),
            str(current),
            QFileDialog.Option.ShowDirsOnly,
        )
        if chosen:
            # Issue #609: QFileDialog returnerer altid stien med forward
            # slashes, uanset platform — vis den native (backslash på
            # Windows) i stedet for at sætte Qt-stien rå ind i feltet.
            self._edit.setText(str(Path(chosen)))

    @property
    def path(self) -> Path:
        return Path(self._edit.text())

    def set_path(self, path: Path) -> None:
        """Opdater den viste sti programmatisk (fx ved annullering/reset)."""
        self._edit.setText(str(path))

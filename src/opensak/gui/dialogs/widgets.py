"""
src/opensak/gui/dialogs/widgets.py — Delte, genbrugelige dialog-widgets.

Indeholder små UI-komponenter der bruges i flere dialoger, så de kun
implementeres og vedligeholdes ét sted.
"""

from __future__ import annotations

from pathlib import Path

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

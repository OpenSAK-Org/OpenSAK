"""
src/opensak/gui/dialogs/widgets.py — Delte, genbrugelige dialog-widgets.

Indeholder små UI-komponenter der bruges i flere dialoger, så de kun
implementeres og vedligeholdes ét sted.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QPushButton, QWidget

from opensak.lang import tr


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
            self._btn.setFixedWidth(100)
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
            self._edit.setText(chosen)

    @property
    def path(self) -> Path:
        return Path(self._edit.text())

    def set_path(self, path: Path) -> None:
        """Opdater den viste sti programmatisk (fx ved annullering/reset)."""
        self._edit.setText(str(path))

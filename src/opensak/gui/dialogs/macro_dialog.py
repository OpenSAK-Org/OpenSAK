"""
src/opensak/gui/dialogs/macro_dialog.py — Lua macro editor/runner (proof of concept).

Non-modal, so the cache list behind it can be watched while a macro changes
the filter. The script runs synchronously on the GUI thread; the runtime's
instruction limit guards against endless loops.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QPlainTextEdit, QPushButton,
    QSplitter, QVBoxLayout,
)

from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.lang import tr
from opensak.macro import MacroError, MacroHost, MacroRuntime

EXAMPLE_MACRO = """\
-- OpenSAK macro (Lua) — proof of concept
-- opensak.filter{...}, opensak.filter_profile(name), opensak.clear_filter(),
-- opensak.count(), opensak.profiles(), print(...)
-- opensak.set_corrected(code, lat, lon | "N47 22.123 E008 32.456"),
-- opensak.clear_corrected(code), opensak.read_csv(path [, sep]),
-- opensak.confirm(message)

local n = opensak.filter{
    type       = {"Traditional", "Multi-cache"},
    difficulty = {1, 2.5},
    found      = false,
    label      = "Easy unfound",
}

if n == 0 then
    print("No caches match — filter not applied")
else
    print("Selected " .. n .. " caches")
end
"""


class MacroDialog(QDialog):
    """Small editor with Open… / Run and an output pane."""

    def __init__(self, host: MacroHost, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("macro_title"))
        self.resize(640, 520)
        clamp_dialog_height_to_screen(self, parent)
        # Non-modal editor window: give it an explicit close (and min/max)
        # button — the inherited dialog flags left Windows without a working ✕.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self._runtime = MacroRuntime(host, output=self._append_output)
        self._chunk_name = "macro"
        self._base_dir: Path | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        self._editor = QPlainTextEdit()
        self._editor.setFont(mono)
        self._editor.setPlainText(EXAMPLE_MACRO)

        self._output = QPlainTextEdit()
        self._output.setFont(mono)
        self._output.setReadOnly(True)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._editor)
        splitter.addWidget(self._output)
        splitter.setSizes([380, 140])
        layout.addWidget(splitter)

        buttons = QHBoxLayout()
        btn_open = QPushButton(tr("macro_btn_open"))
        btn_open.clicked.connect(self._open_file)
        buttons.addWidget(btn_open)
        buttons.addStretch()
        self._btn_run = QPushButton(tr("macro_btn_run"))
        self._btn_run.setDefault(True)
        self._btn_run.clicked.connect(self._run)
        buttons.addWidget(self._btn_run)
        btn_close = QPushButton(tr("close"))
        btn_close.clicked.connect(self.close)
        buttons.addWidget(btn_close)
        layout.addLayout(buttons)

    def _open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("macro_open_title"), "", "Lua (*.lua);;* (*)"
        )
        if not path:
            return
        self._editor.setPlainText(Path(path).read_text(encoding="utf-8"))
        self._chunk_name = Path(path).name
        self._base_dir = Path(path).parent

    def _append_output(self, text: str) -> None:
        self._output.appendPlainText(text)

    def _run(self) -> None:
        self._output.clear()
        self._btn_run.setEnabled(False)
        try:
            self._runtime.run(
                self._editor.toPlainText(),
                chunk_name=self._chunk_name,
                base_dir=self._base_dir,
            )
            self._append_output(tr("macro_done"))
        except MacroError as exc:
            self._append_output(tr("macro_error", msg=str(exc)))
        finally:
            self._btn_run.setEnabled(True)

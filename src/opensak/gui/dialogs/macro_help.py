"""
src/opensak/gui/dialogs/macro_help.py — the Lua API reference inside OpenSAK.

Shows the same Markdown as docs/macros/api.md, rendered from the API
registry at runtime (render_api_markdown), so the help always matches the
running build and needs no extra files in the installer. A contents list
jumps to each heading; in-page links ("#opensakfilter") work too, web links
open in the browser, and a link to an example macro opens it in the editor.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QSplitter,
    QTextBrowser, QToolButton, QVBoxLayout,
)

from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.lang import tr


def _norm(text: str) -> str:
    """Heading text and link anchor reduced to the same form, e.g.
    "opensak.read_csv" and "#opensakreadcsv" → "opensakreadcsv"."""
    return re.sub(r"[^0-9a-z]", "", text.lower())


class MacroHelpDialog(QDialog):
    """Non-modal reference window; emits open_example(name) for links to
    a shipped example macro."""

    open_example = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("macro_help_title"))
        self.resize(820, 640)
        clamp_dialog_height_to_screen(self, parent)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("macro_help_search"))
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(lambda: self.find_text(self.search.text()))
        search_row.addWidget(self.search)
        btn_next = QToolButton()
        btn_next.setText("▼")
        btn_next.setToolTip(tr("macro_act_find_next"))
        btn_next.clicked.connect(lambda: self.find_text(self.search.text()))
        search_row.addWidget(btn_next)
        layout.addLayout(search_row)

        self.contents = QListWidget()
        self.contents.setToolTip(tr("macro_help_contents"))
        self.contents.itemActivated.connect(self._on_contents)
        self.contents.itemClicked.connect(self._on_contents)

        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self._on_link)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.contents)
        splitter.addWidget(self.browser)
        splitter.setSizes([200, 620])
        layout.addWidget(splitter)

        self._load()

    def _load(self) -> None:
        from opensak.macro.api_docs import render_api_markdown
        from opensak.macro.examples import bundled_examples_dir

        self.browser.setMarkdown(render_api_markdown(bundled_examples_dir()))
        self.contents.clear()
        block = self.browser.document().begin()
        while block.isValid():
            level = block.blockFormat().headingLevel()
            if level in (2, 3):
                indent = "    " if level == 3 else ""
                item = QListWidgetItem(indent + block.text())
                item.setData(Qt.ItemDataRole.UserRole, block.blockNumber())
                self.contents.addItem(item)
            block = block.next()

    def headings(self) -> list[str]:
        return [self.contents.item(i).text().strip() for i in range(self.contents.count())]

    def scroll_to_block(self, number: int) -> None:
        doc = self.browser.document()
        block = doc.findBlockByNumber(number)
        self.browser.setTextCursor(QTextCursor(block))
        top = doc.documentLayout().blockBoundingRect(block).top()
        self.browser.verticalScrollBar().setValue(int(top))

    def scroll_to_heading(self, anchor: str) -> bool:
        """Jump to the heading whose text matches *anchor* ("#opensakfilter")."""
        wanted = _norm(anchor)
        block = self.browser.document().begin()
        while block.isValid():
            if block.blockFormat().headingLevel() and _norm(block.text()) == wanted:
                self.scroll_to_block(block.blockNumber())
                return True
            block = block.next()
        return False

    def find_text(self, text: str) -> bool:
        """Select the next occurrence of *text*, wrapping around."""
        if not text:
            return False
        if self.browser.find(text):
            return True
        self.browser.moveCursor(QTextCursor.MoveOperation.Start)
        return self.browser.find(text, QTextDocument.FindFlag(0))

    def _on_contents(self, item: QListWidgetItem) -> None:
        self.scroll_to_block(item.data(Qt.ItemDataRole.UserRole))

    def _on_link(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
            return
        if not url.path() and url.fragment():
            self.scroll_to_heading(url.fragment())
            return
        name = url.path().rsplit("/", 1)[-1]
        from opensak.macro.examples import list_examples
        if name.endswith(".lua") and name in list_examples():
            self.open_example.emit(name)

"""
src/opensak/gui/dialogs/macro_editor.py — editor widgets for Lua macros.

  * LuaHighlighter — syntax colouring (keywords, strings, comments, numbers,
    standard functions and the opensak.* API), also for multi-line
    [[strings]] and --[[comments]]
  * CodeEditor     — QPlainTextEdit with line numbers, current-line and
    error-line highlight, auto-indent, Tab/Shift+Tab on selected lines and
    toggling "--" comments
  * FindReplaceBar — inline find / replace below the editor
"""

from __future__ import annotations

import re

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QKeyEvent, QPainter, QPalette,
    QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextDocument, QTextFormat,
)
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QTextEdit,
    QToolButton, QWidget,
)

from opensak.lang import tr

INDENT = "    "

_KEYWORDS = frozenset(
    "and break do else elseif end false for function goto if in local nil not "
    "or repeat return then true until while".split()
)
_BUILTINS = frozenset(
    "assert error ipairs next pairs pcall print rawequal rawget rawlen rawset "
    "select setmetatable getmetatable tonumber tostring type xpcall "
    "string table math os coroutine utf8".split()
)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(
    r"0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?"
)
_LONG_OPEN = re.compile(r"\[(=*)\[")
# A line after which the next one is indented one level deeper.
_OPENS_BLOCK = re.compile(
    r"(\bthen|\bdo|\belse|\brepeat|\bfunction\b.*\)|\{|\()\s*(--.*)?$"
)

# Block states for multi-line constructs: 0 = none, otherwise
# 1 + kind * 100 + level, kind 0 = long comment, 1 = long string.
_COMMENT, _STRING = 0, 1


def _is_dark(widget: QWidget) -> bool:
    return widget.palette().color(QPalette.ColorRole.Base).lightness() < 128


def _fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    f.setFontItalic(italic)
    return f


class LuaHighlighter(QSyntaxHighlighter):
    """Lua 5.4 syntax colouring; colours follow the light or dark theme."""

    def __init__(self, document: QTextDocument, dark: bool = False):
        super().__init__(document)
        if dark:
            colors = ("#569cd6", "#c586c0", "#4ec9b0", "#ce9178", "#6a9955", "#b5cea8")
        else:
            colors = ("#0000c0", "#7a3e9d", "#00796b", "#a31515", "#3f7f3f", "#098658")
        keyword, builtin, api, string, comment, number = colors
        self.formats = {
            "keyword": _fmt(keyword, bold=True),
            "builtin": _fmt(builtin),
            "api": _fmt(api, bold=True),
            "string": _fmt(string),
            "comment": _fmt(comment, italic=True),
            "number": _fmt(number),
        }

    def _long(self, text: str, start: int, body: int, level: int, kind: int) -> int:
        """Format a long comment/string opened at *start* whose body begins
        at *body*; return where scanning continues (len(text) if it runs on
        into the next line)."""
        close = "]" + "=" * level + "]"
        end = text.find(close, body)
        fmt = self.formats["comment" if kind == _COMMENT else "string"]
        if end < 0:
            self.setFormat(start, len(text) - start, fmt)
            self.setCurrentBlockState(1 + kind * 100 + level)
            return len(text)
        stop = end + len(close)
        self.setFormat(start, stop - start, fmt)
        return stop

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt API)
        self.setCurrentBlockState(0)
        n = len(text)
        i = 0
        prev = self.previousBlockState()
        if prev > 0:
            kind, level = divmod(prev - 1, 100)
            i = self._long(text, 0, 0, level, kind)
        while i < n:
            c = text[i]
            if text.startswith("--", i):
                m = _LONG_OPEN.match(text, i + 2)
                if m:
                    i = self._long(text, i, m.end(), len(m.group(1)), _COMMENT)
                    continue
                self.setFormat(i, n - i, self.formats["comment"])
                return
            if c == "[":
                m = _LONG_OPEN.match(text, i)
                if m:
                    i = self._long(text, i, m.end(), len(m.group(1)), _STRING)
                    continue
            if c in "\"'":
                j = i + 1
                while j < n and text[j] != c:
                    j += 2 if text[j] == "\\" else 1
                stop = min(j + 1, n)
                self.setFormat(i, stop - i, self.formats["string"])
                i = stop
                continue
            if c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit()):
                m = _NUMBER.match(text, i)
                if m:
                    self.setFormat(i, m.end() - i, self.formats["number"])
                    i = m.end()
                    continue
            m = _IDENT.match(text, i)
            if m:
                word = m.group()
                if word == "opensak":
                    stop = m.end()
                    member = _IDENT.match(text, stop + 1) if text[stop:stop + 1] == "." else None
                    if member:
                        stop = member.end()
                    self.setFormat(i, stop - i, self.formats["api"])
                    i = stop
                    continue
                if word in _KEYWORDS:
                    self.setFormat(i, len(word), self.formats["keyword"])
                elif word in _BUILTINS:
                    self.setFormat(i, len(word), self.formats["builtin"])
                i = m.end()
                continue
            i += 1


class _LineNumberArea(QWidget):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._editor.line_number_width(), 0)

    def paintEvent(self, event) -> None:  # noqa: N802
        self._editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    """Plain-text editor for Lua with line numbers and editing helpers."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * len(INDENT))
        self.highlighter = LuaHighlighter(self.document(), dark=_is_dark(self))
        self._error_line: int | None = None
        self._numbers = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_margin)
        self.updateRequest.connect(self._update_numbers)
        self.cursorPositionChanged.connect(self._update_selections)
        self.textChanged.connect(self.clear_error_line)
        self._update_margin()
        self._update_selections()

    # -- Line numbers ----------------------------------------------------------

    def line_number_width(self) -> int:
        digits = max(3, len(str(self.blockCount())))
        return 10 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_margin(self, *_args) -> None:
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)

    def _update_numbers(self, rect: QRect, dy: int) -> None:
        if dy:
            self._numbers.scroll(0, dy)
        else:
            self._numbers.update(0, rect.y(), self._numbers.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_margin()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._numbers.setGeometry(QRect(cr.left(), cr.top(), self.line_number_width(), cr.height()))

    def paint_line_numbers(self, event) -> None:
        painter = QPainter(self._numbers)
        pal = self.palette()
        painter.fillRect(event.rect(), pal.color(QPalette.ColorRole.AlternateBase))
        current = self.textCursor().blockNumber()
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible():
                number = block.blockNumber()
                role = QPalette.ColorRole.Text if number == current else QPalette.ColorRole.PlaceholderText
                painter.setPen(pal.color(role))
                painter.drawText(0, top, self._numbers.width() - 5, height,
                                 Qt.AlignmentFlag.AlignRight, str(number + 1))
            top += round(self.blockBoundingRect(block).height())
            block = block.next()

    # -- Highlights ------------------------------------------------------------

    def _line_selection(self, line: int, color: QColor) -> QTextEdit.ExtraSelection:
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(color)
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        sel.cursor = QTextCursor(self.document().findBlockByNumber(line))
        sel.cursor.clearSelection()
        return sel

    def _update_selections(self) -> None:
        dark = _is_dark(self)
        selections = [self._line_selection(
            self.textCursor().blockNumber(),
            QColor("#2a2d2e") if dark else QColor("#f2f6fc"),
        )]
        if self._error_line is not None:
            selections.append(self._line_selection(
                self._error_line, QColor("#5a1d1d") if dark else QColor("#fbd5d5")
            ))
        self.setExtraSelections(selections)
        self._numbers.update()

    def mark_error_line(self, line: int) -> None:
        """Highlight 1-based *line* (a Lua error) and move the cursor there."""
        if not 1 <= line <= self.blockCount():
            return
        self.go_to_line(line)
        self._error_line = line - 1
        self._update_selections()

    def clear_error_line(self) -> None:
        if self._error_line is not None:
            self._error_line = None
            self._update_selections()

    def error_line(self) -> int | None:
        return None if self._error_line is None else self._error_line + 1

    def go_to_line(self, line: int) -> None:
        block = self.document().findBlockByNumber(max(0, line - 1))
        cursor = QTextCursor(block)
        self.setTextCursor(cursor)
        self.centerCursor()
        self.setFocus()

    # -- Editing helpers -------------------------------------------------------

    def _selected_blocks(self):
        cursor = self.textCursor()
        doc = self.document()
        first = doc.findBlock(cursor.selectionStart())
        last = doc.findBlock(cursor.selectionEnd())
        # A selection ending at the start of a line does not include it.
        if cursor.hasSelection() and cursor.selectionEnd() == last.position() and last != first:
            last = last.previous()
        block = first
        while True:
            yield block
            if block == last:
                break
            block = block.next()

    def _edit_lines(self, change) -> None:
        """Apply *change(text) -> text* to every selected line as one undo
        step, keeping the selection on whole lines."""
        blocks = list(self._selected_blocks())
        cursor = self.textCursor()
        cursor.beginEditBlock()
        for block in blocks:
            old = block.text()
            new = change(old)
            if new != old:
                c = QTextCursor(block)
                c.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                c.insertText(new)
        cursor.endEditBlock()
        if len(blocks) > 1:
            sel = QTextCursor(blocks[0])
            sel.setPosition(blocks[-1].position() + blocks[-1].length() - 1,
                            QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(sel)

    def indent_lines(self) -> None:
        self._edit_lines(lambda t: INDENT + t if t.strip() else t)

    def dedent_lines(self) -> None:
        def dedent(text: str) -> str:
            strip = len(text) - len(text.lstrip(" "))
            if text.startswith("\t"):
                return text[1:]
            return text[min(strip, len(INDENT)):]
        self._edit_lines(dedent)

    def toggle_comment(self) -> None:
        """Comment out the selected lines with "-- ", or uncomment them if
        every non-empty one already starts with "--"."""
        lines = [b.text() for b in self._selected_blocks()]
        filled = [t for t in lines if t.strip()]
        if not filled:
            return
        if all(t.lstrip().startswith("--") for t in filled):
            def change(text: str) -> str:
                body = text.lstrip()
                if not body.startswith("--"):
                    return text
                rest = body[3:] if body.startswith("-- ") else body[2:]
                return text[: len(text) - len(body)] + rest
        else:
            indent = min(len(t) - len(t.lstrip()) for t in filled)

            def change(text: str) -> str:
                return text[:indent] + "-- " + text[indent:] if text.strip() else text
        self._edit_lines(change)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        mods = event.modifiers()
        cursor = self.textCursor()
        if key == Qt.Key.Key_Tab and not mods & Qt.KeyboardModifier.ControlModifier:
            if cursor.hasSelection() and \
                    self.document().findBlock(cursor.selectionStart()) != \
                    self.document().findBlock(cursor.selectionEnd()):
                self.indent_lines()
            else:
                column = cursor.positionInBlock()
                cursor.insertText(" " * (len(INDENT) - column % len(INDENT)))
            return
        if key == Qt.Key.Key_Backtab:
            self.dedent_lines()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and mods in (
            Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier
        ):
            line = cursor.block().text()[: cursor.positionInBlock()]
            indent = line[: len(line) - len(line.lstrip())]
            if _OPENS_BLOCK.search(line.rstrip()):
                indent += INDENT
            cursor.insertText("\n" + indent)
            self.ensureCursorVisible()
            return
        super().keyPressEvent(event)


class FindReplaceBar(QWidget):
    """Find (and optionally replace) in a CodeEditor. Enter = next match,
    Shift+Enter = previous, Esc closes the bar."""

    closed = Signal()

    def __init__(self, editor: CodeEditor, parent: QWidget | None = None):
        super().__init__(parent)
        self._editor = editor
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)

        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText(tr("macro_find_placeholder"))
        self.find_edit.setClearButtonEnabled(True)
        self.find_edit.textEdited.connect(self._find_as_you_type)
        self.find_edit.installEventFilter(self)
        layout.addWidget(self.find_edit, 2)

        self.btn_prev = self._tool("▲", tr("macro_act_find_prev"), lambda: self.find_next(False))
        self.btn_next = self._tool("▼", tr("macro_act_find_next"), lambda: self.find_next(True))
        layout.addWidget(self.btn_prev)
        layout.addWidget(self.btn_next)

        self.case = QCheckBox(tr("macro_find_case"))
        self.word = QCheckBox(tr("macro_find_word"))
        layout.addWidget(self.case)
        layout.addWidget(self.word)

        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText(tr("macro_replace_placeholder"))
        self.replace_edit.installEventFilter(self)
        layout.addWidget(self.replace_edit, 2)
        self.btn_replace = self._tool(tr("macro_replace_one"), "", self.replace)
        self.btn_replace_all = self._tool(tr("macro_replace_all"), "", self.replace_all)
        layout.addWidget(self.btn_replace)
        layout.addWidget(self.btn_replace_all)

        self.status = QLabel()
        layout.addWidget(self.status)
        layout.addStretch()
        layout.addWidget(self._tool("✕", tr("close"), self.close_bar))
        self._replace_widgets = (self.replace_edit, self.btn_replace, self.btn_replace_all)

    def _tool(self, text: str, tip: str, slot) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        if tip:
            btn.setToolTip(tip)
        btn.setAutoRaise(True)
        btn.clicked.connect(slot)
        return btn

    def open_bar(self, replace: bool) -> None:
        for w in self._replace_widgets:
            w.setVisible(replace)
        selected = self._editor.textCursor().selectedText()
        if selected and " " not in selected:      # not across lines
            self.find_edit.setText(selected)
        self.status.clear()
        self.show()
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def close_bar(self) -> None:
        self.hide()
        self._editor.setFocus()
        self.closed.emit()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == event.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.close_bar()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if obj is self.replace_edit:
                    self.replace()
                else:
                    self.find_next(not event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                return True
        return super().eventFilter(obj, event)

    def _flags(self, forward: bool = True) -> QTextDocument.FindFlag:
        flags = QTextDocument.FindFlag(0)
        if not forward:
            flags |= QTextDocument.FindFlag.FindBackward
        if self.case.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if self.word.isChecked():
            flags |= QTextDocument.FindFlag.FindWholeWords
        return flags

    def _find_as_you_type(self) -> None:
        cursor = self._editor.textCursor()
        cursor.setPosition(cursor.selectionStart())
        self._editor.setTextCursor(cursor)
        self.find_next(True)

    def find_next(self, forward: bool = True) -> bool:
        """Select the next (or previous) match, wrapping around the end."""
        text = self.find_edit.text()
        self.status.clear()
        if not text:
            return False
        doc = self._editor.document()
        found = doc.find(text, self._editor.textCursor(), self._flags(forward))
        if found.isNull():
            start = QTextCursor(doc)
            if not forward:
                start.movePosition(QTextCursor.MoveOperation.End)
            found = doc.find(text, start, self._flags(forward))
        if found.isNull():
            self.status.setText(tr("macro_find_none"))
            return False
        self._editor.setTextCursor(found)
        return True

    def _selection_matches(self) -> bool:
        selected = self._editor.textCursor().selectedText()
        wanted = self.find_edit.text()
        if self.case.isChecked():
            return selected == wanted
        return selected.lower() == wanted.lower()

    def replace(self) -> None:
        """Replace the selected match, then select the next one."""
        if not self.find_edit.text():
            return
        if self._selection_matches():
            self._editor.textCursor().insertText(self.replace_edit.text())
        self.find_next(True)

    def replace_all(self) -> int:
        text = self.find_edit.text()
        if not text:
            return 0
        doc = self._editor.document()
        count = 0
        edit = QTextCursor(doc)
        edit.beginEditBlock()
        cursor = doc.find(text, 0, self._flags())
        while not cursor.isNull():
            cursor.insertText(self.replace_edit.text())
            count += 1
            cursor = doc.find(text, cursor, self._flags())
        edit.endEditBlock()
        self.status.setText(tr("macro_replaced", n=count) if count else tr("macro_find_none"))
        return count

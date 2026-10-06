"""
src/opensak/gui/dialogs/macro_dialog.py — Lua macro editor and runner.

A small editor for writing and maintaining macros: New / Open / Open recent
(last 10) / Save / Save as, Find and Replace, Go to line, toggle comments,
Run and Save & Run, and the Lua API reference (F1). The window title shows
the file name and a "*" for unsaved changes; closing, opening or starting a
new macro asks before discarding them. When a run fails, the line named in
the Lua error is highlighted.

Non-modal, so the cache list behind it can be watched while a macro changes
the filter. The script runs synchronously on the GUI thread; the runtime's
instruction limit guards against endless loops.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QInputDialog, QLabel, QMenu, QMenuBar, QMessageBox,
    QPlainTextEdit, QSplitter, QStatusBar, QStyle, QToolBar, QToolButton,
    QVBoxLayout, QWidget,
)

from opensak.gui.dialogs.macro_editor import CodeEditor, FindReplaceBar
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.lang import tr
from opensak.macro import FolderApproval, MacroError, MacroHost, MacroRuntime

if TYPE_CHECKING:
    from opensak.gui.dialogs.macro_help import MacroHelpDialog

EXAMPLE_MACRO = """\
-- OpenSAK macro (Lua)
-- F1 opens the API reference, F5 runs the macro.

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

NEW_MACRO = """\
-- New macro — describe what it does
-- F1 opens the API reference, F5 runs the macro.

"""

_LUA_FILTER = "Lua (*.lua);;* (*)"


class MacroDialog(QDialog):
    """Editor for Lua macros with a menu bar, toolbar, find/replace bar,
    output pane and status bar."""

    def __init__(self, host: MacroHost, parent=None):
        super().__init__(parent)
        self.resize(820, 640)
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
        self._path: Path | None = None
        self._help: MacroHelpDialog | None = None
        self._setup_ui()
        self._editor.document().modificationChanged.connect(self.setWindowModified)
        self._set_text(EXAMPLE_MACRO, None)
        # Reopen the macro worked on last.
        from opensak.macro.recent import recent_macros
        recent = recent_macros()
        if recent:
            self.open_path(recent[0], ask=False)

    # -- UI --------------------------------------------------------------------

    def _action(self, text: str, slot, shortcut=None, icon=None) -> QAction:
        act = QAction(text, self)
        if shortcut is not None:
            act.setShortcut(QKeySequence(shortcut))
            native = act.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            # Drop mnemonic markers ("&File"), keep a literal "&&" as "&".
            plain = text.replace("&&", "\0").replace("&", "").replace("\0", "&")
            act.setToolTip(f"{plain} ({native})")
        if icon is not None:
            act.setIcon(self.style().standardIcon(icon))
        act.triggered.connect(slot)
        return act

    def _setup_ui(self) -> None:
        sp = QStyle.StandardPixmap
        key = QKeySequence.StandardKey
        self._editor = CodeEditor()
        self._find_bar = FindReplaceBar(self._editor)
        self._find_bar.hide()

        self._act_new = self._action(tr("macro_act_new"), self._new, key.New, sp.SP_FileIcon)
        self._act_open = self._action(tr("macro_btn_open"), self._open_file, key.Open,
                                      sp.SP_DialogOpenButton)
        self._act_save = self._action(tr("save"), self._save, key.Save,
                                      sp.SP_DialogSaveButton)
        self._act_save_as = self._action(tr("macro_act_save_as"), self._save_as, "Ctrl+Shift+S")
        act_open_folder = self._action(tr("macro_act_open_folder"), self._open_macros_folder,
                                       icon=sp.SP_DirOpenIcon)
        act_close = self._action(tr("close"), self.close)

        act_undo = self._action(tr("macro_act_undo"), self._editor.undo, key.Undo)
        act_redo = self._action(tr("macro_act_redo"), self._editor.redo, key.Redo)
        self._editor.undoAvailable.connect(act_undo.setEnabled)
        self._editor.redoAvailable.connect(act_redo.setEnabled)
        act_undo.setEnabled(False)
        act_redo.setEnabled(False)
        self._act_find = self._action(tr("macro_act_find"), lambda: self._find_bar.open_bar(False),
                                      key.Find, sp.SP_FileDialogContentsView)
        self._act_replace = self._action(tr("macro_act_replace"),
                                         lambda: self._find_bar.open_bar(True), "Ctrl+H")
        act_next = self._action(tr("macro_act_find_next"),
                                lambda: self._find_bar.find_next(True), "F3")
        act_prev = self._action(tr("macro_act_find_prev"),
                                lambda: self._find_bar.find_next(False), "Shift+F3")
        act_goto = self._action(tr("macro_act_goto"), self._go_to_line, "Ctrl+G")
        act_comment = self._action(tr("macro_act_comment"), self._editor.toggle_comment, "Ctrl+/")

        self._act_run = self._action(tr("macro_btn_run"), self._run, "F5", sp.SP_MediaPlay)
        self._act_save_run = self._action(tr("macro_act_save_run"), self._save_and_run, "Ctrl+F5")
        self._act_help = self._action(tr("macro_act_help"), self._show_help, "F1",
                                      sp.SP_DialogHelpButton)

        self._recent_menu = QMenu(tr("macro_act_recent"), self)
        self._recent_menu.aboutToShow.connect(self._fill_recent_menu)

        menubar = QMenuBar(self)
        file_menu = menubar.addMenu(tr("macro_menu_file"))
        file_menu.addActions([self._act_new, self._act_open])
        file_menu.addMenu(self._recent_menu)
        file_menu.addSeparator()
        file_menu.addActions([self._act_save, self._act_save_as])
        file_menu.addSeparator()
        file_menu.addAction(act_open_folder)
        file_menu.addSeparator()
        file_menu.addAction(act_close)
        edit_menu = menubar.addMenu(tr("macro_menu_edit"))
        edit_menu.addActions([act_undo, act_redo])
        edit_menu.addSeparator()
        edit_menu.addActions([self._act_find, self._act_replace, act_next, act_prev])
        edit_menu.addSeparator()
        edit_menu.addActions([act_goto, act_comment])
        run_menu = menubar.addMenu(tr("macro_menu_run"))
        run_menu.addActions([self._act_run, self._act_save_run])
        help_menu = menubar.addMenu(tr("macro_menu_help"))
        help_menu.addAction(self._act_help)

        toolbar = QToolBar()
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.addActions([self._act_new, self._act_open])
        btn_recent = QToolButton()
        btn_recent.setText(tr("macro_act_recent"))
        btn_recent.setMenu(self._recent_menu)
        btn_recent.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        toolbar.addWidget(btn_recent)
        toolbar.addActions([self._act_save, self._act_save_as])
        toolbar.addSeparator()
        toolbar.addActions([self._act_run, self._act_save_run])
        toolbar.addSeparator()
        toolbar.addActions([self._act_find, self._act_replace])
        toolbar.addSeparator()
        toolbar.addAction(self._act_help)
        self._btn_run = toolbar.widgetForAction(self._act_run)

        self._output = QPlainTextEdit()
        self._output.setFont(self._editor.font())
        self._output.setReadOnly(True)

        editor_box = QWidget()
        editor_layout = QVBoxLayout(editor_box)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)
        editor_layout.addWidget(self._editor)
        editor_layout.addWidget(self._find_bar)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(editor_box)
        splitter.addWidget(self._output)
        splitter.setSizes([460, 140])

        self._status = QStatusBar()
        self._status.setSizeGripEnabled(False)
        self._path_label = QLabel()
        self._pos_label = QLabel()
        self._status.addWidget(self._path_label, 1)
        self._status.addPermanentWidget(self._pos_label)
        self._editor.cursorPositionChanged.connect(self._update_position)

        layout = QVBoxLayout(self)
        layout.setMenuBar(menubar)
        layout.addWidget(toolbar)
        layout.addWidget(splitter, 1)
        layout.addWidget(self._status)
        self._update_position()

    # -- State -----------------------------------------------------------------

    @property
    def path(self) -> Path | None:
        """The file being edited, None for an unsaved new macro."""
        return self._path

    def _set_text(self, text: str, path: Path | None) -> None:
        self._editor.setPlainText(text)
        self._editor.document().setModified(False)
        self._output.clear()
        self._set_path(path)

    def _set_path(self, path: Path | None) -> None:
        self._path = path
        name = path.name if path else tr("macro_untitled")
        self.setWindowTitle(f"{name}[*] — {tr('macro_title')}")
        self.setWindowModified(self._editor.document().isModified())
        self._path_label.setText(str(path) if path else "")

    def _update_position(self) -> None:
        cursor = self._editor.textCursor()
        self._pos_label.setText(tr("macro_status_pos", line=cursor.blockNumber() + 1,
                                   col=cursor.positionInBlock() + 1))

    def _start_dir(self) -> Path:
        from opensak.config import get_macros_dir
        return self._path.parent if self._path else get_macros_dir()

    # -- File actions ----------------------------------------------------------

    def _maybe_save(self) -> bool:
        """True if the current text may be replaced: unchanged, saved, or
        the user chose to discard the changes."""
        if not self._editor.document().isModified():
            return True
        name = self._path.name if self._path else tr("macro_untitled")
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(
            self, tr("macro_unsaved_title"), tr("macro_unsaved_msg", name=name),
            buttons.Save | buttons.Discard | buttons.Cancel, buttons.Save,
        )
        if answer == buttons.Save:
            return self._save()
        return answer == buttons.Discard

    def _new(self) -> None:
        if self._maybe_save():
            self._set_text(NEW_MACRO, None)
            self._editor.moveCursor(QTextCursor.MoveOperation.End)
            self._editor.setFocus()

    def _open_file(self) -> None:
        if not self._maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, tr("macro_open_title"), str(self._start_dir()), _LUA_FILTER
        )
        if path:
            self.open_path(Path(path), ask=False)

    def open_path(self, path: Path, ask: bool = True) -> bool:
        """Load *path* into the editor (asking first about unsaved changes
        when *ask*); relative paths in the macro then resolve against its
        folder. Returns False if nothing was opened."""
        from opensak.macro.recent import add_recent_macro, remove_recent_macro
        if ask and not self._maybe_save():
            return False
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            remove_recent_macro(path)
            QMessageBox.warning(self, tr("macro_title"),
                                tr("macro_open_error", path=str(path), msg=str(exc)))
            return False
        self._set_text(text, path)
        add_recent_macro(path)
        return True

    def _write(self, path: Path) -> bool:
        from opensak.macro.recent import add_recent_macro
        try:
            path.write_text(self._editor.toPlainText(), encoding="utf-8", newline="\n")
        except OSError as exc:
            QMessageBox.warning(self, tr("macro_title"),
                                tr("macro_save_error", path=str(path), msg=str(exc)))
            return False
        self._editor.document().setModified(False)
        self._set_path(path)
        add_recent_macro(path)
        self._status.showMessage(tr("macro_saved", path=path.name), 3000)
        return True

    def _save(self) -> bool:
        if self._path is None:
            return self._save_as()
        return self._write(self._path)

    def _save_as(self) -> bool:
        start = self._path or self._start_dir() / "macro.lua"
        chosen, _ = QFileDialog.getSaveFileName(
            self, tr("macro_save_title"), str(start), _LUA_FILTER
        )
        if not chosen:
            return False
        path = Path(chosen)
        if not path.suffix:
            path = path.with_suffix(".lua")
        return self._write(path)

    def _fill_recent_menu(self) -> None:
        from opensak.macro.recent import clear_recent_macros, recent_macros
        menu = self._recent_menu
        menu.clear()
        paths = recent_macros()
        for i, path in enumerate(paths, start=1):
            act = menu.addAction(f"&{i % 10}  {path.name}")
            act.setToolTip(str(path))
            act.setStatusTip(str(path))
            act.triggered.connect(lambda _=False, p=path: self.open_path(p))
        if not paths:
            menu.addAction(tr("macro_recent_empty")).setEnabled(False)
            return
        menu.setToolTipsVisible(True)
        menu.addSeparator()
        menu.addAction(tr("macro_recent_clear"), clear_recent_macros)

    def _open_macros_folder(self) -> None:
        from opensak.config import get_macros_dir
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_macros_dir())))

    # -- Edit actions ----------------------------------------------------------

    def _go_to_line(self) -> None:
        count = self._editor.blockCount()
        line, ok = QInputDialog.getInt(
            self, tr("macro_goto_title"), tr("macro_goto_label", n=count),
            self._editor.textCursor().blockNumber() + 1, 1, count,
        )
        if ok:
            self._editor.go_to_line(line)

    # -- Run -------------------------------------------------------------------

    def _append_output(self, text: str) -> None:
        self._output.appendPlainText(text)

    def _chunk_name(self) -> str:
        return self._path.name if self._path else "macro"

    def _run(self) -> None:
        self._output.clear()
        self._editor.clear_error_line()
        self._act_run.setEnabled(False)
        self._act_save_run.setEnabled(False)
        try:
            self._runtime.run(
                self._editor.toPlainText(),
                chunk_name=self._chunk_name(),
                base_dir=self._path.parent if self._path else None,
            )
            self._append_output(tr("macro_done"))
        except MacroError as exc:
            self._append_output(tr("macro_error", msg=str(exc)))
            line = self.error_line(str(exc))
            if line is not None:
                self._editor.mark_error_line(line)
        finally:
            self._act_run.setEnabled(True)
            self._act_save_run.setEnabled(True)

    def error_line(self, message: str) -> int | None:
        """The line of this macro named in a Lua error ("name.lua:12: ...")."""
        m = re.search(rf"(?<![\w.]){re.escape(self._chunk_name())}:(\d+):", message)
        return int(m.group(1)) if m else None

    def _save_and_run(self) -> None:
        if self._save():
            self._run()

    # -- Help ------------------------------------------------------------------

    def _show_help(self) -> None:
        from opensak.gui.dialogs.macro_help import MacroHelpDialog
        if self._help is None:
            self._help = MacroHelpDialog(self)
            self._help.open_example.connect(self._open_example)
        self._help.show()
        self._help.raise_()
        self._help.activateWindow()

    def _open_example(self, name: str) -> None:
        from opensak.macro.examples import install_example
        try:
            path = install_example(name)
        except OSError as exc:
            QMessageBox.warning(self, tr("macro_title"),
                                tr("macro_example_error", name=name, msg=str(exc)))
            return
        if self.open_path(path):
            self.raise_()
            self.activateWindow()

    # -- Window ----------------------------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # Esc closes the find bar; it must not close the editor window.
        if event.key() == Qt.Key.Key_Escape:
            if self._find_bar.isVisible():
                self._find_bar.close_bar()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._maybe_save():
            event.accept()
        else:
            event.ignore()


def ask_folder_approval(parent, target: Path, folder: Path, write: bool) -> FolderApproval:
    """OpenSAK's question when a macro needs *target* outside its permitted
    folders: allow *folder* this time only, always, or deny (the default,
    also on Escape). The paths come from the macro, so they are shown as
    plain text."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(tr("macro_access_title"))
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(tr(
        "macro_access_write" if write else "macro_access_read",
        path=str(target), folder=str(folder),
    ))
    box.setInformativeText(tr("macro_access_hint"))
    btn_once = box.addButton(tr("macro_access_once"), QMessageBox.ButtonRole.AcceptRole)
    btn_always = box.addButton(tr("macro_access_always"), QMessageBox.ButtonRole.AcceptRole)
    btn_deny = box.addButton(tr("macro_access_deny"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(btn_deny)
    box.setEscapeButton(btn_deny)
    box.exec()
    clicked = box.clickedButton()
    if clicked is btn_once:
        return FolderApproval.ONCE
    if clicked is btn_always:
        return FolderApproval.ALWAYS
    return FolderApproval.DENY


def choose_file_for_macro(
    parent, title: str, file_filter: str, save: bool, start_dir: Path
) -> Path | None:
    """OpenSAK's file dialog for opensak.choose_file(). The caption always
    says that a macro asks, whatever *title* the macro passes."""
    caption = tr("macro_choose_save" if save else "macro_choose_open")
    if title.strip():
        caption = tr("macro_choose_caption", caption=caption, title=title.strip())
    pick = QFileDialog.getSaveFileName if save else QFileDialog.getOpenFileName
    path, _ = pick(parent, caption, str(start_dir), file_filter or tr("macro_choose_all_files"))
    return Path(path) if path else None

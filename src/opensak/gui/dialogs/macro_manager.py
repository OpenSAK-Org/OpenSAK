"""
src/opensak/gui/dialogs/macro_manager.py — the Macro manager (#938 Phase 3).

Lists the macros in the user's macros folder and its subfolders with what
their metadata header says (see opensak.macro.metadata): name, description,
version, author, required API version, and the keyboard shortcut the user
gave them here. A macro that cannot be read, or asks for a newer macro API
than this OpenSAK has, is marked in the list. The search box (Ctrl+F)
narrows the list to the macros containing every word typed.

Run runs the selected macro and shows its output and errors in the pane
below; a double-click or Enter in the list asks first, so a stray click
cannot start a macro that changes the database; Edit opens it in the macro editor, at
the error line when its last run here failed. Shortcut… gives the macro a
key that runs it from the main window, refusing keys OpenSAK already uses.
The list is read again on Reload and whenever the window becomes active,
so macros saved from an external editor show up without asking.

The main window runs macro shortcuts through run_path() as well, so their
output is here too when the window is closed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QKeySequenceEdit,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QStyle, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.lang import tr
from opensak.macro import MacroError, MacroHost, MacroRuntime
from opensak.macro.metadata import MacroInfo, discover_macros, read_metadata
from opensak.macro.runtime import API_VERSION

# Columns of the macro list.
COL_NAME, COL_DESCRIPTION, COL_VERSION, COL_AUTHOR, COL_API, COL_SHORTCUT, COL_FOLDER = range(7)

_PATH_ROLE = Qt.ItemDataRole.UserRole

# Keys a macro shortcut may use without Ctrl, Alt or Meta: F1–F35. A plain
# letter would run the macro while the user types in the cache list.
_FUNCTION_KEY = re.compile(r"^(?:Shift\+)?F\d{1,2}$")

TakenShortcuts = Callable[[Path], dict[str, str]]


def shortcut_allowed(sequence: str) -> bool:
    """True if *sequence* (portable text) may run a macro: one key with
    Ctrl, Alt or Meta, or a function key."""
    if not sequence or "," in sequence:
        return False
    parts = sequence.split("+")
    if any(mod in parts[:-1] for mod in ("Ctrl", "Alt", "Meta")):
        return True
    return bool(_FUNCTION_KEY.match(sequence))


class MacroManagerDialog(QDialog):
    """Overview of the local macros, with Run / Edit / Shortcut… / Open
    folder / Reload and an output pane.

    *taken_shortcuts(path)* returns the shortcuts already in use (portable
    text → what uses it), leaving out the one of macro *path*;
    *shortcuts_changed()* is called after a shortcut was set or removed;
    *edit_macro(path, error)* opens *path* in the editor, showing *error*
    (a Lua error message) when not None.
    """

    def __init__(
        self,
        host: MacroHost,
        parent=None,
        taken_shortcuts: Optional[TakenShortcuts] = None,
        shortcuts_changed: Optional[Callable[[], None]] = None,
        edit_macro: Optional[Callable[[Path, Optional[str]], None]] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("macro_mgr_title"))
        self.resize(900, 560)
        clamp_dialog_height_to_screen(self, parent)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self._runtime = MacroRuntime(host, output=self._append_output)
        self._taken_shortcuts = taken_shortcuts or (lambda _path: {})
        self._shortcuts_changed = shortcuts_changed or (lambda: None)
        self._edit_macro = edit_macro or (lambda _path, _error: None)
        self._macros: list[MacroInfo] = []
        # The last failed run: (macro, Lua error message), for Edit.
        self._last_error: tuple[Path, str] | None = None
        self._setup_ui()
        self.reload()

    # -- UI --------------------------------------------------------------------

    def _setup_ui(self) -> None:
        sp = QStyle.StandardPixmap
        self._tree = QTreeWidget()
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setHeaderLabels([
            tr("col_name"), tr("detail_tab_desc"),
            tr("macro_mgr_col_version"), tr("macro_mgr_col_author"),
            tr("macro_mgr_col_api"), tr("shortcuts_col_shortcut"),
            tr("settings_folder_perm_col_folder"),
        ])
        self._tree.setSortingEnabled(True)
        self._tree.sortByColumn(COL_NAME, Qt.SortOrder.AscendingOrder)
        header = self._tree.header()
        header.setStretchLastSection(False)
        for col in range(self._tree.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_DESCRIPTION, QHeaderView.ResizeMode.Stretch)
        self._tree.itemSelectionChanged.connect(self._update_buttons)
        self._tree.itemActivated.connect(lambda *_: self._run_activated())

        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("macro_mgr_search"))
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_search)
        self._search.returnPressed.connect(self._focus_first_match)
        act_find = QAction(self)
        act_find.setShortcut(QKeySequence.StandardKey.Find)
        act_find.triggered.connect(lambda: (self._search.setFocus(), self._search.selectAll()))
        self.addAction(act_find)

        self._empty = QLabel(tr("macro_mgr_empty"))
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)

        self._path_label = QLabel()
        self._path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        def button(text: str, slot, icon=None) -> QPushButton:
            btn = QPushButton(text)
            if icon is not None:
                btn.setIcon(self.style().standardIcon(icon))
            btn.setAutoDefault(False)
            btn.clicked.connect(slot)
            return btn

        self._btn_run = button(tr("macro_btn_run"), self._run_selected)
        self._btn_edit = button(tr("edit"), self._edit_selected)
        self._btn_shortcut = button(tr("macro_mgr_shortcut"), self._set_shortcut)
        btn_folder = button(tr("macro_act_open_folder"), self._open_folder, sp.SP_DirOpenIcon)
        btn_reload = button(tr("macro_disk_reload"), self.reload, sp.SP_BrowserReload)
        btn_close = button(tr("close"), self.close)

        buttons = QHBoxLayout()
        for btn in (self._btn_run, self._btn_edit, self._btn_shortcut):
            buttons.addWidget(btn)
        buttons.addStretch(1)
        for btn in (btn_folder, btn_reload, btn_close):
            buttons.addWidget(btn)

        list_box = QWidget()
        list_layout = QVBoxLayout(list_box)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.addWidget(self._search)
        list_layout.addWidget(self._tree, 1)
        list_layout.addWidget(self._empty, 1)
        list_layout.addWidget(self._path_label)

        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setPlaceholderText(tr("macro_mgr_output_hint"))

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(list_box)
        splitter.addWidget(self._output)
        splitter.setSizes([360, 160])

        layout = QVBoxLayout(self)
        layout.addWidget(splitter, 1)
        layout.addLayout(buttons)

    # -- List ------------------------------------------------------------------

    @property
    def macros(self) -> list[MacroInfo]:
        """The macros shown, in folder order."""
        return list(self._macros)

    def reload(self) -> None:
        """Read the macros folder again, keeping the selected macro."""
        from opensak.macro.permissions import macros_dir
        from opensak.macro.shortcuts import macro_shortcut

        selected = self.selected_path()
        root = macros_dir()
        self._macros = discover_macros(root)
        warn = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        self._tree.setSortingEnabled(False)
        self._tree.clear()
        for info in self._macros:
            folder = info.path.parent.relative_to(root).as_posix()
            item = QTreeWidgetItem([
                info.name, info.error or info.description, info.version, info.author,
                info.requires_api, _native(macro_shortcut(info.path)),
                "" if folder == "." else folder,
            ])
            item.setData(COL_NAME, _PATH_ROLE, str(info.path))
            item.setToolTip(COL_NAME, str(info.path))
            item.setToolTip(COL_DESCRIPTION, info.error or info.description)
            if info.error:
                item.setIcon(COL_NAME, warn)
                item.setToolTip(COL_NAME, tr("macro_mgr_unreadable", msg=info.error))
            elif info.needs_newer_api(API_VERSION):
                item.setIcon(COL_API, warn)
                item.setToolTip(COL_API, tr("macro_mgr_api_newer", needed=info.requires_api,
                                            have=API_VERSION))
            self._tree.addTopLevelItem(item)
            if selected is not None and info.path == selected:
                item.setSelected(True)
                self._tree.setCurrentItem(item)
        self._tree.setSortingEnabled(True)
        has_macros = bool(self._macros)
        self._tree.setVisible(has_macros)
        self._search.setVisible(has_macros)
        self._empty.setVisible(not has_macros)
        self._apply_search()

    def selected_path(self) -> Path | None:
        items = self._tree.selectedItems()
        return Path(items[0].data(COL_NAME, _PATH_ROLE)) if items else None

    def select_path(self, path: Path) -> bool:
        """Select the macro *path* in the list; False if it is not listed."""
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            if Path(item.data(COL_NAME, _PATH_ROLE)) == Path(path):
                if item.isHidden():
                    self._search.clear()
                self._tree.setCurrentItem(item)
                return True
        return False

    def _apply_search(self) -> None:
        """Show only the macros whose name, description, author, version,
        shortcut, folder or file name contain every word typed in the
        search box (case-insensitive). A hidden macro is deselected."""
        words = self._search.text().casefold().split()
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            text = " ".join(
                [item.text(c) for c in range(self._tree.columnCount())]
                + [Path(item.data(COL_NAME, _PATH_ROLE)).name]
            ).casefold()
            hidden = not all(w in text for w in words)
            item.setHidden(hidden)
            if hidden and item.isSelected():
                item.setSelected(False)
        self._update_buttons()

    def _focus_first_match(self) -> None:
        """Enter in the search box: select the first macro shown."""
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            if not item.isHidden():
                self._tree.setCurrentItem(item)
                self._tree.setFocus()
                return

    def _update_buttons(self) -> None:
        path = self.selected_path()
        for btn in (self._btn_run, self._btn_edit, self._btn_shortcut):
            btn.setEnabled(path is not None)
        self._path_label.setText(str(path) if path else "")

    def _open_folder(self) -> None:
        from opensak.config import get_macros_dir
        folder = get_macros_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # -- Run -------------------------------------------------------------------

    def _append_output(self, text: str) -> None:
        self._output.appendPlainText(text)

    def _run_selected(self) -> None:
        path = self.selected_path()
        if path is not None:
            self.run_path(path)

    def _run_activated(self) -> None:
        """Double-click or Enter on a macro: run it after asking (No is
        the default, also on Escape)."""
        path = self.selected_path()
        if path is None:
            return
        info = next((m for m in self._macros if m.path == path), None)
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(
            self, tr("macro_mgr_title"),
            tr("macro_mgr_run_confirm", name=info.name if info else path.stem),
            buttons.Yes | buttons.No, buttons.No,
        )
        if answer == buttons.Yes:
            self.run_path(path)

    def run_path(self, path: Path, parent=None) -> Optional[str]:
        """Run the macro file *path*, its output going to the output pane.
        Returns the error message if it failed, None if it ran (or the user
        chose not to run a macro asking for a newer API). Questions go on
        top of *parent*, this window by default."""

        parent = parent or self
        path = Path(path)
        info = read_metadata(path)
        if not info.error and info.needs_newer_api(API_VERSION):
            buttons = QMessageBox.StandardButton
            answer = QMessageBox.question(
                parent, tr("macro_mgr_title"),
                tr("macro_mgr_api_newer_run", name=info.name, needed=info.requires_api,
                   have=API_VERSION),
                buttons.Yes | buttons.No, buttons.No,
            )
            if answer != buttons.Yes:
                return None
        self._output.clear()
        self._append_output(tr("macro_mgr_running", name=info.name, path=str(path)))
        self._btn_run.setEnabled(False)
        try:
            source = path.read_text(encoding="utf-8")
            code = self._runtime.run(source, chunk_name=path.name, base_dir=path.parent,
                                     macro_path=path)
        except (OSError, UnicodeDecodeError) as exc:
            error = tr("macro_open_error", path=str(path), msg=str(exc))
            self._append_output(error)
            self._last_error = None
            return error
        except MacroError as exc:
            self._append_output(tr("macro_error", msg=str(exc)))
            self._last_error = (path, str(exc))
            return str(exc)
        finally:
            self._update_buttons()
        self._append_output(tr("macro_done_code", code=code) if code else tr("macro_done"))
        if self._last_error and self._last_error[0] == path:
            self._last_error = None
        return None

    # -- Edit ------------------------------------------------------------------

    def _edit_selected(self) -> None:
        path = self.selected_path()
        if path is None:
            return
        error = self._last_error[1] if self._last_error and self._last_error[0] == path else None
        self._edit_macro(path, error)

    # -- Shortcuts -------------------------------------------------------------

    def _set_shortcut(self) -> None:
        from opensak.macro.shortcuts import macro_shortcut, set_macro_shortcut

        path = self.selected_path()
        if path is None:
            return
        info = next((m for m in self._macros if m.path == path), None)
        name = info.name if info else path.stem
        dlg = MacroShortcutDialog(name, macro_shortcut(path), self._taken_shortcuts(path), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        set_macro_shortcut(path, dlg.sequence())
        self._shortcuts_changed()
        self.reload()

    # -- Window ----------------------------------------------------------------

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        # Macros saved from the editor or another program meanwhile.
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            QTimer.singleShot(0, self.reload)


class MacroShortcutDialog(QDialog):
    """Pick the key that runs macro *name*. *taken* maps the shortcuts
    already in use (portable text) to what uses them; OK is refused for
    those and for keys that need a modifier."""

    def __init__(self, name: str, current: str, taken: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("macro_mgr_shortcut_title"))
        self._taken = taken
        self._edit = QKeySequenceEdit(QKeySequence(current))
        self._edit.setMaximumSequenceLength(1)
        self._edit.setClearButtonEnabled(True)
        self._edit.keySequenceChanged.connect(self._validate)
        self._problem = QLabel()
        self._problem.setWordWrap(True)
        self._problem.setStyleSheet("color: #c0392b;")
        prompt = QLabel(tr("macro_mgr_shortcut_prompt", name=name))
        prompt.setTextFormat(Qt.TextFormat.PlainText)
        prompt.setWordWrap(True)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(prompt)
        layout.addWidget(self._edit)
        layout.addWidget(self._problem)
        layout.addWidget(QLabel(tr("macro_mgr_shortcut_hint")))
        layout.addWidget(self._buttons)
        self._validate()

    def sequence(self) -> str:
        """The chosen key in portable text, "" for none."""
        return self._edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)

    def problem(self) -> str:
        """Why the chosen key cannot be used, "" if it can."""
        seq = self.sequence()
        if not seq:
            return ""
        if seq in self._taken:
            return tr("macro_mgr_shortcut_taken", keys=_native(seq), what=self._taken[seq])
        if not shortcut_allowed(seq):
            return tr("macro_mgr_shortcut_modifier")
        return ""

    def _validate(self, *_args) -> None:
        problem = self.problem()
        self._problem.setText(problem)
        self._problem.setVisible(bool(problem))
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not problem)


def _native(sequence: str) -> str:
    """Portable key sequence text as the platform writes it ("⌘1" on macOS)."""
    if not sequence:
        return ""
    return QKeySequence(sequence).toString(QKeySequence.SequenceFormat.NativeText)

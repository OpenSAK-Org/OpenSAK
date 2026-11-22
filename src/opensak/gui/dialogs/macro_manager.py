"""
src/opensak/gui/dialogs/macro_manager.py — the Macro manager (#938 Phase 3).

Lists the macros in the user's macros folder and its subfolders with what
their metadata header says (see opensak.macro.metadata): name, description,
version, author, required API version, declared permissions, and the
keyboard shortcut the user gave them here. A macro that cannot be read,
asks for a newer macro API than this OpenSAK has, or declares permissions
this OpenSAK does not know (or a site macros may never contact), is marked
in the list. The permissions are only shown: OpenSAK asks for every write,
folder and web site approval whatever the header says. The search box
(Ctrl+F) narrows the list to the macros containing every word typed.

Run runs the selected macro and shows its output and errors in the pane
below; a double-click or Enter in the list asks first, so a stray click
cannot start a macro that changes the database; Edit opens it in the macro editor, at
the error line when its last run here failed. Shortcut… gives the macro a
key that runs it from the main window, refusing keys OpenSAK already uses.
The "On toolbar" check box puts a button for the macro on the Macros
toolbar of the main window (#1271).
The list is read again on Reload and whenever the window becomes active,
so macros saved from an external editor show up without asking.

The main window runs macro shortcuts through run_path() as well, so their
output is here too when the window is closed.

The OpenSAK-Macros button (#1342) checks the shared macro repository for
updates, updates macros installed from it, and browses and installs the
macros published there; the OpenSAK-Macros column shows where each local
macro stands (see opensak.macro.repository). The right-click menu of a
macro has Update, Remove and Stop tracking / Track again. Downloads run in
the background (macro_repository.start_task()); nothing is installed,
updated or removed without asking.

Whether Shift is held is read when the user starts the macro (shift_held()),
before any question, and passed on for opensak.shift_key() (#1038).
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QKeySequenceEdit,
    QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QStyle, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.lang import tr
from opensak.macro import MacroError, MacroHost, MacroRuntime
from opensak.macro.db_access import read_macro_source
from opensak.macro.metadata import NO_PERMISSIONS, MacroInfo, discover_macros, read_metadata
from opensak.macro.runtime import API_VERSION

# Columns of the macro list.
(COL_NAME, COL_DESCRIPTION, COL_VERSION, COL_AUTHOR, COL_API, COL_PERMISSIONS, COL_SHORTCUT,
 COL_TOOLBAR, COL_FOLDER, COL_HEADLESS, COL_REPO) = range(11)

_PATH_ROLE = Qt.ItemDataRole.UserRole

# Keys a macro shortcut may use without Ctrl, Alt or Meta: F1–F35. A plain
# letter would run the macro while the user types in the cache list.
_FUNCTION_KEY = re.compile(r"^(?:Shift\+)?F\d{1,2}$")

TakenShortcuts = Callable[[Path], dict[str, str]]


def shift_held() -> bool:
    """Shift is held down right now — for opensak.shift_key() (#1038).
    The keyboard itself, not the last event: a menu or button click may
    come without one."""
    return bool(QApplication.queryKeyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)


def shortcut_allowed(sequence: str) -> bool:
    """True if *sequence* (portable text) may run a macro: one key with
    Ctrl, Alt or Meta, or a function key."""
    if not sequence or "," in sequence:
        return False
    parts = sequence.split("+")
    if any(mod in parts[:-1] for mod in ("Ctrl", "Alt", "Meta")):
        return True
    return bool(_FUNCTION_KEY.match(sequence))


def _empty_text() -> str:
    """The empty-list text, ending with a link to OpenSAK-Macros (#1173)."""
    import html

    from opensak.macro.metadata import MACROS_REPO_URL

    link = f'<a href="{html.escape(MACROS_REPO_URL)}">OpenSAK-Macros</a>'
    text = html.escape(tr("macro_mgr_empty")) + "\n\n" + html.escape(
        tr("macro_mgr_empty_shared", link="\x00")
    ).replace("\x00", link)
    return text.replace("\n", "<br>")


class MacroManagerDialog(QDialog):
    """Overview of the local macros, with Run / Edit / Shortcut… / Open
    folder / Reload and an output pane.

    *taken_shortcuts(path)* returns the shortcuts already in use (portable
    text → what uses it), leaving out the one of macro *path*;
    *shortcuts_changed()* is called after a shortcut was set or removed;
    *toolbar_changed()* after a macro was put on or taken off the toolbar;
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
        toolbar_changed: Optional[Callable[[], None]] = None,
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
        self._toolbar_changed = toolbar_changed or (lambda: None)
        self._edit_macro = edit_macro or (lambda _path, _error: None)
        self._macros: list[MacroInfo] = []
        # Where each local macro stands against OpenSAK-Macros (#1342), once
        # an index was fetched this session.
        self._statuses: dict[Path, Any] = {}
        self._busy = False
        self._browse: Optional[QDialog] = None
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
            tr("macro_mgr_col_api"), tr("macro_mgr_col_permissions"),
            tr("shortcuts_col_shortcut"), tr("macro_mgr_col_toolbar"),
            tr("settings_folder_perm_col_folder"), tr("macro_mgr_col_headless"),
            tr("macro_repo_button"),
        ])
        self._tree.setSortingEnabled(True)
        self._tree.sortByColumn(COL_NAME, Qt.SortOrder.AscendingOrder)
        header = self._tree.header()
        header.setStretchLastSection(False)
        for col in range(self._tree.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_DESCRIPTION, QHeaderView.ResizeMode.Stretch)
        # The OpenSAK-Macros status goes next to the version it compares
        # with, not off screen to the right (the logical index stays).
        header.moveSection(header.visualIndex(COL_REPO), header.visualIndex(COL_VERSION) + 1)
        self._tree.itemSelectionChanged.connect(self._update_buttons)
        self._tree.itemActivated.connect(lambda *_: self._run_activated())
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._context_menu)
        header_item = self._tree.headerItem()
        if header_item is not None:
            header_item.setToolTip(COL_TOOLBAR, tr("macro_mgr_toolbar_tip"))

        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("macro_mgr_search"))
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_search)
        self._search.returnPressed.connect(self._focus_first_match)
        act_find = QAction(self)
        act_find.setShortcut(QKeySequence.StandardKey.Find)
        act_find.triggered.connect(self._focus_search)
        self.addAction(act_find)

        self._empty = QLabel(_empty_text())
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        # #1173: the text links to the shared macro repository.
        self._empty.setTextFormat(Qt.TextFormat.RichText)
        self._empty.setOpenExternalLinks(True)

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
        # #1342: the shared macro repository.
        self._btn_repo = QPushButton(tr("macro_repo_button"))
        self._btn_repo.setAutoDefault(False)
        self._repo_menu = QMenu(self._btn_repo)
        self._act_check = self._repo_menu.addAction(tr("macro_repo_check"))
        self._act_check.triggered.connect(lambda: self.check_updates())
        self._act_update_all = self._repo_menu.addAction(tr("macro_repo_update_all"))
        self._act_update_all.triggered.connect(self.update_all)
        self._repo_menu.addSeparator()
        self._act_browse = self._repo_menu.addAction(tr("macro_repo_browse"))
        self._act_browse.triggered.connect(self.open_browse)
        self._repo_menu.aboutToShow.connect(self._update_repo_menu)
        self._btn_repo.setMenu(self._repo_menu)
        btn_folder = button(tr("macro_act_open_folder"), self._open_folder, sp.SP_DirOpenIcon)
        btn_reload = button(tr("macro_disk_reload"), self.reload, sp.SP_BrowserReload)
        btn_close = button(tr("close"), self.close)

        buttons = QHBoxLayout()
        for btn in (self._btn_run, self._btn_edit, self._btn_shortcut, self._btn_repo):
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
        from opensak.macro.toolbar import on_toolbar

        selected = self.selected_path()
        root = macros_dir()
        self._macros = discover_macros(root)
        self._match_index()
        warn = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        self._tree.setSortingEnabled(False)
        self._tree.blockSignals(True)       # no itemChanged while filling
        self._tree.clear()
        for info in self._macros:
            folder = info.path.parent.relative_to(root).as_posix()
            item = QTreeWidgetItem([
                info.name, info.error or info.description, info.version, info.author,
                info.requires_api, permissions_text(info),
                _native(macro_shortcut(info.path)), "", "" if folder == "." else folder,
                "" if info.error else "yes" if info.runs_headless else "no",
            ])
            item.setData(COL_NAME, _PATH_ROLE, str(info.path))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(COL_TOOLBAR, Qt.CheckState.Checked if on_toolbar(info.path)
                               else Qt.CheckState.Unchecked)
            item.setToolTip(COL_TOOLBAR, tr("macro_mgr_toolbar_tip"))
            item.setToolTip(COL_NAME, str(info.path))
            item.setToolTip(COL_DESCRIPTION, info.error or info.description)
            if info.error:
                item.setIcon(COL_NAME, warn)
                item.setToolTip(COL_NAME, tr("macro_mgr_unreadable", msg=info.error))
            elif info.needs_newer_api(API_VERSION):
                item.setIcon(COL_API, warn)
                item.setToolTip(COL_API, tr("macro_mgr_api_newer", needed=info.requires_api,
                                            have=API_VERSION))
            status = self._statuses.get(info.path)
            if status is not None:
                self._show_status(item, status, warn)
            if not info.error:
                item.setToolTip(COL_PERMISSIONS, permissions_tooltip(info))
                if info.unknown_permissions or info.invalid_hosts or info.blocked_hosts:
                    item.setIcon(COL_PERMISSIONS, warn)
                if info.deprecation_lines:
                    # Deprecated header keys (#1185), with the API note if any
                    item.setIcon(COL_API, warn)
                    item.setToolTip(COL_API, "\n".join(filter(None, [
                        item.toolTip(COL_API), tr("macro_mgr_deprecated_keys"),
                        *info.deprecation_lines])))
            self._tree.addTopLevelItem(item)
            if selected is not None and info.path == selected:
                item.setSelected(True)
                self._tree.setCurrentItem(item)
        self._tree.blockSignals(False)
        self._tree.setSortingEnabled(True)
        has_macros = bool(self._macros)
        self._tree.setVisible(has_macros)
        self._search.setVisible(has_macros)
        self._empty.setVisible(not has_macros)
        self._show_update_count()
        self._apply_search()

    def _show_update_count(self) -> None:
        """The OpenSAK-Macros button says how many updates there are."""
        from opensak.macro import repository as repo

        count = len(repo.updates(self._statuses))
        text = tr("macro_repo_button")
        self._btn_repo.setText(f"{text} ({count})" if count else text)
        font = self._btn_repo.font()
        font.setBold(bool(count))
        self._btn_repo.setFont(font)

    def selected_path(self) -> Path | None:
        items = self._tree.selectedItems()
        return Path(items[0].data(COL_NAME, _PATH_ROLE)) if items else None

    def _items(self) -> list[QTreeWidgetItem]:
        """The rows of the list, in the order shown."""
        tree = self._tree
        return [item for i in range(tree.topLevelItemCount())
                if (item := tree.topLevelItem(i)) is not None]

    def _focus_search(self) -> None:
        """Ctrl+F: put the cursor in the search box, its text selected."""
        self._search.setFocus()
        self._search.selectAll()

    def select_path(self, path: Path) -> bool:
        """Select the macro *path* in the list; False if it is not listed."""
        for item in self._items():
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
        for item in self._items():
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
        for item in self._items():
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
            self.run_path(path, shift=shift_held())

    def _run_activated(self) -> None:
        """Double-click or Enter on a macro: run it after asking (No is
        the default, also on Escape)."""
        path = self.selected_path()
        if path is None:
            return
        shift = shift_held()        # before the question: Shift may be let go
        info = next((m for m in self._macros if m.path == path), None)
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(
            self, tr("macro_mgr_title"),
            tr("macro_mgr_run_confirm", name=info.name if info else path.stem),
            buttons.Yes | buttons.No, buttons.No,
        )
        if answer == buttons.Yes:
            self.run_path(path, shift=shift)

    def run_path(self, path: Path, parent=None, shift: bool = False) -> Optional[str]:
        """Run the macro file *path*, its output going to the output pane.
        Returns the error message if it failed, None if it ran (or the user
        chose not to run a macro asking for a newer API). Questions go on
        top of *parent*, this window by default. *shift*: Shift was held
        when the user started it (opensak.shift_key())."""

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
            source = read_macro_source(path)
            code = self._runtime.run(source, chunk_name=path.name, base_dir=path.parent,
                                     macro_path=path, shift=shift)
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

    # -- Toolbar (#1271) -------------------------------------------------------

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """The "On toolbar" check box: put the macro on the main window's
        Macros toolbar, as its last button, or take it off."""
        from opensak.macro.toolbar import on_toolbar, set_on_toolbar

        if column != COL_TOOLBAR:
            return
        path = Path(item.data(COL_NAME, _PATH_ROLE))
        checked = item.checkState(COL_TOOLBAR) == Qt.CheckState.Checked
        if checked == on_toolbar(path):
            return
        set_on_toolbar(path, checked)
        self._toolbar_changed()

    # -- OpenSAK-Macros (#1342) -----------------------------------------------

    @property
    def statuses(self) -> dict[Path, Any]:
        """{macro file: repository.MacroStatus} after an index was fetched."""
        return dict(self._statuses)

    def _match_index(self) -> None:
        from opensak.macro import repository as repo

        index = repo.last_index()
        self._statuses = {} if index is None else repo.match_macros(
            self._macros, index, API_VERSION)

    def _show_status(self, item: QTreeWidgetItem, status, warn) -> None:
        from opensak.gui.dialogs.macro_repository import status_text, status_tooltip
        from opensak.macro import repository as repo

        item.setText(COL_REPO, status_text(status))
        item.setToolTip(COL_REPO, status_tooltip(status))
        if status.state in (repo.WITHDRAWN, repo.CHANGED, repo.NEEDS_NEWER_API):
            item.setIcon(COL_REPO, warn)
        elif status.state == repo.UPDATE:
            item.setIcon(COL_REPO, self.style().standardIcon(
                QStyle.StandardPixmap.SP_ArrowUp))

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self._btn_repo.setEnabled(not busy)
        if message:
            self._append_output(message)

    def _update_repo_menu(self) -> None:
        from opensak.macro import repository as repo

        count = len(repo.updates(self._statuses))
        self._act_update_all.setText(tr("macro_repo_update_all_n", count=count) if count
                                     else tr("macro_repo_update_all"))
        self._act_update_all.setEnabled(count > 0 and not self._busy)
        self._act_check.setEnabled(not self._busy)
        self._act_browse.setEnabled(not self._busy)

    def check_updates(self, then: Optional[Callable[[], None]] = None) -> None:
        """Fetch the index in the background, then show the statuses and
        offer to remove withdrawn macros; *then* runs after a successful
        fetch (Browse fetches first when there is no index yet)."""
        from opensak.gui.dialogs.macro_repository import start_task
        from opensak.macro import repository as repo

        if self._busy:
            return
        self._set_busy(True, tr("macro_repo_checking"))

        def done(_index) -> None:
            self._set_busy(False)
            repo.note_checked()
            self.reload()
            self._report_check()
            if then is not None:
                then()
            else:
                self._offer_withdrawn_removal()

        def failed(message: str) -> None:
            self._set_busy(False, tr("macro_repo_check_failed", msg=message))
            QMessageBox.warning(self, tr("macro_repo_button"),
                                tr("macro_repo_check_failed", msg=message))

        start_task(self, repo.fetch_index, done, failed)

    def _report_check(self) -> None:
        from opensak.macro import repository as repo

        updates = repo.updates(self._statuses)
        withdrawn = repo.withdrawn_macros(self._statuses)
        if not updates and not withdrawn:
            self._append_output(tr("macro_repo_all_up_to_date"))
            return
        if updates:
            self._append_output(tr("macro_repo_updates_found", count=len(updates)))
            self._list_statuses(updates)
        if withdrawn:
            self._append_output(tr("macro_repo_withdrawn_found", count=len(withdrawn)))
            self._list_statuses(withdrawn)

    def _list_statuses(self, statuses: dict[Path, Any]) -> None:
        """One line per macro under a check's summary, sorted by name, so
        the result can be read without scrolling to the column."""
        from opensak.gui.dialogs.macro_repository import status_text

        def name(path: Path) -> str:
            info = self._info(path)
            return info.name if info else path.stem

        for path in sorted(statuses, key=lambda p: name(p).casefold()):
            self._append_output(f"  • {name(path)}: {status_text(statuses[path])}")

    def _offer_withdrawn_removal(self) -> None:
        """After a check the user started: offer to remove each withdrawn
        macro. Keeping it is fine; it stays marked."""
        from opensak.macro import repository as repo

        for path in list(repo.withdrawn_macros(self._statuses)):
            self.remove_macro(path, withdrawn=True)

    def _info(self, path: Path) -> Optional[MacroInfo]:
        return next((m for m in self._macros if m.path == path), None)

    def update_selected(self) -> None:
        path = self.selected_path()
        if path is not None:
            self.update_macros([path])

    def update_all(self) -> None:
        from opensak.macro import repository as repo
        self.update_macros(list(repo.updates(self._statuses)))

    def update_from_browse(self, macro_id: str) -> None:
        """Update… in the Browse dialog: the installed copies of *macro_id*,
        asking on top of that dialog."""
        paths = [p for p, s in self._statuses.items() if s.id == macro_id and s.can_update]
        self.update_macros(paths, self._browse)

    def update_macros(self, paths: list[Path], parent: Optional[QWidget] = None) -> None:
        """Update the macros *paths* one after another, asking for each
        (on top of *parent*, this window by default)."""
        from opensak.gui.dialogs.macro_repository import confirm_install, start_task
        from opensak.macro import repository as repo

        if self._busy:
            return
        queue = list(paths)

        def next_one() -> None:
            while queue:
                path = queue.pop(0)
                status = self._statuses.get(path)
                if status is None or not status.can_update:
                    continue
                macro = status.macro
                folder, only_lua = repo.update_folder(status, path)
                names = [macro.lua] if only_lua else [f.name for f in macro.files]
                targets = [path if n == macro.lua else folder / n for n in names]
                replaced = [t for t in targets if t.exists()]
                if not confirm_install(parent or self, macro, targets, replaced,
                                       local=self._info(path),
                                       from_version=status.version,
                                       changed=status.state == repo.CHANGED):
                    continue
                self._set_busy(True, tr("macro_repo_downloading", name=macro.name))

                def done(contents, macro=macro, folder=folder, status=status,
                         path=path) -> None:
                    self._set_busy(False)
                    try:
                        repo.install(macro, contents, folder, status.record, lua_target=path)
                    except (OSError, repo.RepositoryError) as exc:
                        self._install_failed(macro.name, str(exc))
                    else:
                        self._append_output(tr("macro_repo_updated", name=macro.name,
                                               version=macro.version))
                    self.reload()
                    self._reload_browse()
                    next_one()

                def failed(message: str, name=macro.name) -> None:
                    self._set_busy(False)
                    self._install_failed(name, message)
                    next_one()

                start_task(self, functools.partial(repo.download_files, macro, only_lua),
                           done, failed)
                return

        next_one()

    def _install_failed(self, name: str, message: str) -> None:
        text = tr("macro_repo_install_failed", name=name, msg=message)
        self._append_output(text)
        QMessageBox.warning(self, tr("macro_repo_button"), text)

    def install_macro(self, macro_id: str, parent: Optional[QWidget] = None) -> bool:
        """Install the published macro *macro_id* into <macros folder>/<id>/
        after asking; the download runs in the background. True if it was
        started (the list is reloaded when it is done)."""
        from opensak.gui.dialogs.macro_repository import confirm_install, start_task
        from opensak.macro import repository as repo

        index = repo.last_index()
        macro = index.macros.get(macro_id) if index else None
        if macro is None or self._busy:
            return False
        folder = repo.install_folder(macro.id)
        targets = [folder / f.name for f in macro.files]
        replaced = [t for t in targets if t.exists()]
        record = repo.install_records().get(macro.id)
        foreign = folder if folder.exists() and record is None and any(folder.iterdir()) \
            else None
        if not confirm_install(parent or self, macro, targets, replaced,
                               foreign_folder=foreign):
            return False
        self._set_busy(True, tr("macro_repo_downloading", name=macro.name))

        def done(contents) -> None:
            self._set_busy(False)
            try:
                repo.install(macro, contents, folder, record)
            except (OSError, repo.RepositoryError) as exc:
                self._install_failed(macro.name, str(exc))
            else:
                self._append_output(tr("macro_repo_installed_msg", name=macro.name,
                                       version=macro.version, folder=str(folder)))
            self.reload()
            self._reload_browse()

        def failed(message: str) -> None:
            self._set_busy(False)
            self._install_failed(macro.name, message)

        start_task(self, lambda: repo.download_files(macro), done, failed)
        return True

    def _reload_browse(self) -> None:
        if self._browse is not None:
            self._browse.reload()   # type: ignore[attr-defined]

    def installed_from_index(self) -> dict[str, Any]:
        """{id: status} of the local macros from the index."""
        return {s.id: s for s in self._statuses.values() if s.from_index and s.id}

    def open_browse(self) -> None:
        """The Browse dialog; fetches the index first if there is none yet."""
        from opensak.gui.dialogs.macro_repository import MacroBrowseDialog
        from opensak.macro import repository as repo

        index = repo.last_index()
        if index is None:
            self.check_updates(then=self.open_browse)
            return
        if self._browse is None:
            self._browse = MacroBrowseDialog(
                index, API_VERSION, self.installed_from_index,
                lambda macro_id: self.install_macro(macro_id, self._browse),
                self.update_from_browse, self)
        self._browse.show()
        self._browse.raise_()
        self._browse.activateWindow()

    def remove_macro(self, path: Path, withdrawn: bool = False) -> bool:
        """Remove macro *path* after asking (its data only if ticked)."""
        from opensak.gui.dialogs.macro_repository import confirm_remove
        from opensak.macro import repository as repo

        info = self._info(path)
        name = info.name if info else path.stem
        delete_data = confirm_remove(self, name, path, withdrawn)
        if delete_data is None:
            return False
        try:
            result = repo.remove(path, delete_data)
        except OSError as exc:
            self._append_output(tr("macro_repo_remove_failed", name=name, msg=str(exc)))
            return False
        self._append_output(tr("macro_repo_removed", name=name))
        for kept in result.kept:
            self._append_output(tr("macro_repo_kept", file=str(kept)))
        for kept in result.kept_data:
            self._append_output(tr("macro_repo_kept_data", file=str(kept)))
        self.reload()
        return True

    def set_tracking(self, path: Path, tracked: bool) -> None:
        from opensak.macro import repository as repo
        repo.set_tracked(path, tracked)
        self.reload()

    def _context_menu(self, pos: QPoint) -> None:
        from opensak.macro import repository as repo

        item = self._tree.itemAt(pos)
        if item is None:
            return
        path = Path(item.data(COL_NAME, _PATH_ROLE))
        status = self._statuses.get(path)
        menu = QMenu(self)
        if status is not None and status.can_update:
            act = menu.addAction(tr("macro_repo_update_one"))
            act.triggered.connect(lambda: self.update_macros([path]))
        if status is not None and status.from_index:
            act = menu.addAction(tr("macro_repo_remove_one"))
            act.triggered.connect(
                lambda: self.remove_macro(path, status.state == repo.WITHDRAWN))
            act = menu.addAction(tr("macro_repo_stop_tracking"))
            act.triggered.connect(lambda: self.set_tracking(path, False))
        elif status is not None and status.state == repo.UNTRACKED:
            act = menu.addAction(tr("macro_repo_track_again"))
            act.triggered.connect(lambda: self.set_tracking(path, True))
        if menu.isEmpty():
            return
        viewport = self._tree.viewport()
        menu.exec(viewport.mapToGlobal(pos) if viewport is not None else pos)

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


# The tooltip line of each permission (metadata.PERMISSIONS).
_PERMISSION_KEYS = {
    "write": "macro_mgr_perm_write",
    "read-files": "macro_mgr_perm_read_files",
    "write-files": "macro_mgr_perm_write_files",
    "open": "macro_mgr_perm_open",
    "read-clipboard": "macro_mgr_perm_read_clipboard",
    "run-macros": "macro_mgr_perm_run_macros",
}


def permissions_text(info: MacroInfo) -> str:
    """The Permissions column: the declared words and hosts as written in
    the header ("write, open, network: api.example.com"), "none" if the
    macro declares none, "" if it does not declare its permissions."""
    parts = [*info.permissions, *info.unknown_permissions]
    hosts = info.network + info.invalid_hosts
    if hosts:
        parts.append("network: " + ", ".join(hosts))
    if not parts:
        return NO_PERMISSIONS if info.permissions_declared else ""
    return ", ".join(parts)


def permissions_tooltip(info: MacroInfo) -> str:
    """What each declared permission means, and the problems of the
    declaration, one per line."""
    return "\n".join([*declaration_lines(info), "", tr("macro_mgr_perm_info")])


def declaration_lines(info: MacroInfo) -> list[str]:
    """What each declared permission means, and the problems of the
    declaration (or that there is none), one line each."""
    lines = [tr(_PERMISSION_KEYS[p]) for p in info.permissions]
    if info.network:
        lines.append(tr("macro_mgr_perm_network", hosts=", ".join(info.network)))
    if info.unknown_permissions:
        lines.append(tr("macro_mgr_perm_unknown", names=", ".join(info.unknown_permissions)))
    if info.invalid_hosts:
        lines.append(tr("macro_mgr_perm_invalid_hosts", hosts=", ".join(info.invalid_hosts)))
    if info.blocked_hosts:
        lines.append(tr("macro_mgr_perm_blocked", hosts=", ".join(info.blocked_hosts)))
    if not lines:
        lines.append(tr("macro_mgr_perm_none" if info.permissions_declared
                        else "macro_mgr_perm_undeclared"))
    return lines


def ask_declarations(
    parent, macro: Path, info: MacroInfo, added: tuple[str, ...], first: bool,
) -> bool:
    """OpenSAK's notice before the macro file *macro* first runs with a
    declaration (declarations.py): what its header says it does, and with
    *first* False what it declares now that the user has not seen. True
    to run it (the default), False on Cancel or Escape. The texts come
    from the macro, so they are shown as plain text."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(tr("macro_decl_title"))
    box.setTextFormat(Qt.TextFormat.PlainText)
    lines = "\n".join(f"\u2022 {line}" for line in declaration_lines(info))
    box.setText(tr("macro_decl_msg" if first else "macro_decl_msg_more",
                   name=info.name, path=str(macro), lines=lines))
    hints = [] if first else [tr("macro_write_changed_added", added=", ".join(added))]
    hints.append(tr("macro_decl_hint"))
    box.setInformativeText("\n\n".join(hints))
    btn_run = box.addButton(tr("macro_decl_run"), QMessageBox.ButtonRole.AcceptRole)
    btn_cancel = box.addButton(tr("cancel"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(btn_run)
    box.setEscapeButton(btn_cancel)
    box.exec()
    return box.clickedButton() is btn_run


def _native(sequence: str) -> str:
    """Portable key sequence text as the platform writes it ("⌘1" on macOS)."""
    if not sequence:
        return ""
    return QKeySequence(sequence).toString(QKeySequence.SequenceFormat.NativeText)

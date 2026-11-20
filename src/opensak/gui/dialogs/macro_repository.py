"""
src/opensak/gui/dialogs/macro_repository.py — the GUI side of macros from
OpenSAK-Macros (#1342): the status texts of the Macro manager's
OpenSAK-Macros column, the questions before installing, updating and
removing a macro, the Browse dialog, and the background task the downloads
run in (start_task()).

The logic lives in opensak.macro.repository; nothing is installed or
removed without the user confirming it here.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSplitter, QTextBrowser, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.lang import tr
from opensak.macro import repository as repo
from opensak.macro.metadata import MacroInfo


# ── Background task ─────────────────────────────────────────────────────────

class RepositoryTask(QThread):
    """Runs *fn* off the GUI thread; emits succeeded(result) or failed(message)."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], Any], parent: Optional[QObject] = None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
        except repo.RepositoryError as exc:
            self.failed.emit(str(exc))
        except OSError as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)


def start_task(parent: QObject, fn: Callable[[], Any], on_done: Callable[[Any], None],
               on_error: Callable[[str], None]) -> Optional[RepositoryTask]:
    """Run *fn* in a RepositoryTask, calling *on_done* or *on_error* on the
    GUI thread. The task is kept alive by *parent* until it finished.
    (Tests replace this with a function that runs *fn* at once.)"""
    task = RepositoryTask(fn, parent)
    task.succeeded.connect(on_done)
    task.failed.connect(on_error)
    task.finished.connect(task.deleteLater)
    task.start()
    return task


# ── Texts ───────────────────────────────────────────────────────────────────

def status_text(status: repo.MacroStatus) -> str:
    """The OpenSAK-Macros column of the Macro manager."""
    state = status.state
    if state == repo.UP_TO_DATE:
        return tr("macro_repo_up_to_date", version=status.version)
    if state == repo.UPDATE:
        return tr("macro_repo_update", old=status.version or "?", new=status.latest)
    if state == repo.NEEDS_NEWER_API:
        api = status.macro.requires_api if status.macro else "?"
        return tr("macro_repo_needs_api", new=status.latest, api=api)
    if state == repo.WITHDRAWN:
        return tr("macro_repo_withdrawn")
    if state == repo.CHANGED:
        return tr("macro_repo_changed", version=status.version or "?")
    if state == repo.UNTRACKED:
        return tr("macro_repo_untracked")
    return ""


def status_tooltip(status: repo.MacroStatus) -> str:
    """What the status means, and the changes since the installed version."""
    lines: list[str] = []
    if status.state == repo.WITHDRAWN and status.withdrawn is not None:
        lines.append(tr("macro_repo_withdrawn_tip", name=status.withdrawn.name,
                        date=status.withdrawn.withdrawn[:10]))
    elif status.state == repo.CHANGED:
        lines.append(tr("macro_repo_changed_tip"))
    elif status.state == repo.UNTRACKED:
        lines.append(tr("macro_repo_untracked_tip"))
    elif status.state == repo.NEEDS_NEWER_API:
        lines.append(tr("macro_repo_needs_api_tip"))
    if status.macro is not None and status.state in (repo.UPDATE, repo.NEEDS_NEWER_API,
                                                     repo.CHANGED):
        changes = status.macro.changes_since(status.version)
        if changes:
            lines.append(tr("macro_repo_changes"))
            lines += [f"• {v}: {t}" for v, t in changes]
    return "\n".join(lines)


def _permission_lines(declared: tuple[str, ...]) -> list[str]:
    from opensak.gui.dialogs.macro_manager import _PERMISSION_KEYS

    lines: list[str] = []
    hosts = [d.split(":", 1)[1] for d in declared if d.startswith("network:")]
    for d in declared:
        if d.startswith("network:"):
            continue
        lines.append(tr(_PERMISSION_KEYS[d]) if d in _PERMISSION_KEYS
                     else tr("macro_mgr_perm_unknown", names=d))
    if hosts:
        lines.append(tr("macro_mgr_perm_network", hosts=", ".join(hosts)))
    return lines


def index_permission_lines(macro: repo.IndexMacro) -> list[str]:
    """What the published version of *macro* declares, one line each."""
    if not macro.permissions_declared:
        return [tr("macro_mgr_perm_undeclared")]
    return _permission_lines(macro.declared) or [tr("macro_mgr_perm_none")]


def added_declarations(local: Optional[MacroInfo], macro: repo.IndexMacro) -> tuple[str, ...]:
    """What the published *macro* declares that the local copy does not
    (everything if the local copy declares nothing or there is none)."""
    if local is None or local.error or not local.permissions_declared:
        return macro.declared
    before = set(local.declared)
    return tuple(d for d in macro.declared if d not in before)


def _bullets(lines: list[str]) -> str:
    return "\n".join(f"• {line}" for line in lines)


def _shown(path: Path) -> str:
    from opensak.macro.permissions import macros_dir
    try:
        return path.relative_to(macros_dir()).as_posix()
    except ValueError:
        return str(path)


def confirm_install(
    parent: Optional[QWidget], macro: repo.IndexMacro, targets: list[Path], replaced: list[Path],
    *, local: Optional[MacroInfo] = None, from_version: str = "", changed: bool = False,
    foreign_folder: Optional[Path] = None,
) -> bool:
    """Ask before installing (*local* None) or updating *macro*: its changes,
    its new permissions and web sites, and the files written (*targets*)
    and replaced. *changed*: the local copy was edited since it was
    installed. *foreign_folder*: the folder exists but was not installed
    from OpenSAK-Macros. The texts come from the index, so plain text."""
    update = local is not None
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning if changed or foreign_folder
                else QMessageBox.Icon.Question)
    box.setWindowTitle(tr("macro_repo_update_title" if update else "macro_repo_install_title"))
    box.setTextFormat(Qt.TextFormat.PlainText)
    if update:
        box.setText(tr("macro_repo_update_msg", name=macro.name, old=from_version or "?",
                       new=macro.version))
    else:
        box.setText(tr("macro_repo_install_msg", name=macro.name, version=macro.version,
                       author=macro.author or "?"))
    parts: list[str] = []
    changes = macro.changes_since(from_version) if update else macro.changelog[:1]
    if changes:
        parts.append(tr("macro_repo_changes") + "\n"
                     + _bullets([f"{v}: {t}" for v, t in changes]))
    if update:
        added = added_declarations(local, macro)
        if added:
            parts.append(tr("macro_repo_new_permissions") + "\n"
                         + _bullets(_permission_lines(added)))
    else:
        parts.append(tr("macro_repo_permissions") + "\n"
                     + _bullets(index_permission_lines(macro)))
    replaced_set = set(replaced)
    parts.append(tr("macro_repo_files") + "\n" + _bullets([
        tr("macro_repo_file_replaced", file=_shown(t)) if t in replaced_set else _shown(t)
        for t in targets]))
    if changed:
        parts.append(tr("macro_repo_changed_warning"))
    if foreign_folder is not None:
        parts.append(tr("macro_repo_folder_exists", folder=_shown(foreign_folder)))
    if replaced:
        parts.append(tr("macro_repo_previous_hint"))
    parts.append(tr("macro_repo_approval_hint"))
    box.setInformativeText("\n\n".join(parts))
    btn_ok = box.addButton(tr("macro_repo_update_btn" if update else "macro_repo_install_btn"),
                           QMessageBox.ButtonRole.AcceptRole)
    btn_cancel = box.addButton(tr("cancel"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(btn_cancel if changed or foreign_folder else btn_ok)
    box.setEscapeButton(btn_cancel)
    box.exec()
    return box.clickedButton() is btn_ok


def confirm_remove(parent: Optional[QWidget], name: str, lua: Path,
                   withdrawn: bool = False) -> Optional[bool]:
    """Ask before removing the macro *lua*. None on Cancel, else whether
    its companion database is deleted too (a separate check box, off)."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(tr("macro_repo_remove_title"))
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(tr("macro_repo_remove_withdrawn_msg" if withdrawn else "macro_repo_remove_msg",
                   name=name, path=_shown(lua)))
    box.setInformativeText(tr("macro_repo_remove_hint"))
    data = repo.companion_db(lua)
    check: Optional[QCheckBox] = None
    if data.exists():
        check = QCheckBox(tr("macro_repo_remove_data", file=data.name))
        box.setCheckBox(check)
    btn_remove = box.addButton(tr("settings_folder_perm_remove"), QMessageBox.ButtonRole.DestructiveRole)
    btn_keep = box.addButton(tr("macro_repo_keep_btn" if withdrawn else "cancel"),
                             QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(btn_keep)
    box.setEscapeButton(btn_keep)
    box.exec()
    if box.clickedButton() is not btn_remove:
        return None
    return bool(check is not None and check.isChecked())


# ── Browse ──────────────────────────────────────────────────────────────────

(B_NAME, B_VERSION, B_AUTHOR, B_API, B_PERMISSIONS, B_STATUS) = range(6)
_ID_ROLE = Qt.ItemDataRole.UserRole


class MacroBrowseDialog(QDialog):
    """Every macro in the index, with its details, and Install.

    *installed()* returns {id: status} of the local macros that came from
    the index; *install(macro_id)* installs one (asking first) and returns
    True when it did, after which the list is refreshed.
    """

    def __init__(self, index: repo.MacroIndex, api_version: int,
                 installed: Callable[[], dict[str, repo.MacroStatus]],
                 install: Callable[[str], bool], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(tr("macro_repo_browse_title"))
        self.resize(860, 520)
        clamp_dialog_height_to_screen(self, parent)
        self._index = index
        self._api = api_version
        self._installed = installed
        self._install = install

        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("macro_repo_browse_search"))
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_search)

        self._tree = QTreeWidget()
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setHeaderLabels([
            tr("col_name"), tr("macro_mgr_col_version"), tr("macro_mgr_col_author"),
            tr("macro_mgr_col_api"), tr("macro_mgr_col_permissions"), tr("macro_repo_button"),
        ])
        self._tree.setSortingEnabled(True)
        self._tree.sortByColumn(B_NAME, Qt.SortOrder.AscendingOrder)
        header = self._tree.header()
        for col in range(self._tree.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(B_NAME, QHeaderView.ResizeMode.Stretch)
        self._tree.itemSelectionChanged.connect(self._show_details)

        self._details = QTextBrowser()
        self._details.setOpenExternalLinks(True)

        self._btn_install = QPushButton(tr("macro_repo_install_btn"))
        self._btn_install.clicked.connect(self._install_selected)
        self._btn_page = QPushButton(tr("macro_repo_open_page"))
        self._btn_page.clicked.connect(self._open_page)
        btn_close = QPushButton(tr("close"))
        btn_close.clicked.connect(self.close)
        for btn in (self._btn_install, self._btn_page, btn_close):
            btn.setAutoDefault(False)
        buttons = QHBoxLayout()
        buttons.addWidget(self._btn_install)
        buttons.addWidget(self._btn_page)
        buttons.addStretch(1)
        buttons.addWidget(btn_close)

        self._source = QLabel(tr("macro_repo_browse_source", commit=index.commit[:8],
                                 date=index.generated[:10]))
        self._source.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._tree)
        splitter.addWidget(self._details)
        splitter.setSizes([300, 180])
        layout = QVBoxLayout(self)
        layout.addWidget(self._search)
        layout.addWidget(splitter, 1)
        layout.addWidget(self._source)
        layout.addLayout(buttons)
        self.reload()

    def _status(self, macro: repo.IndexMacro,
                installed: dict[str, repo.MacroStatus]) -> str:
        status = installed.get(macro.id)
        if status is not None:
            return status_text(status) or tr("macro_repo_installed")
        if macro.needs_newer_api(self._api):
            return tr("macro_repo_needs_api_short", api=macro.requires_api)
        return tr("macro_repo_not_installed")

    def reload(self) -> None:
        selected = self.selected_id()
        installed = self._installed()
        self._installed_now = installed
        self._tree.setSortingEnabled(False)
        self._tree.clear()
        for macro in self._index.macros.values():
            perms = ", ".join([*macro.permissions,
                               *(["network: " + ", ".join(macro.network)] if macro.network
                                 else [])])
            item = QTreeWidgetItem([
                macro.name, macro.version, macro.author,
                "" if macro.requires_api is None else str(macro.requires_api), perms,
                self._status(macro, installed),
            ])
            item.setData(B_NAME, _ID_ROLE, macro.id)
            item.setToolTip(B_NAME, macro.description)
            self._tree.addTopLevelItem(item)
            if macro.id == selected:
                self._tree.setCurrentItem(item)
        self._tree.setSortingEnabled(True)
        self._apply_search()
        self._show_details()

    def selected_id(self) -> Optional[str]:
        items = self._tree.selectedItems() if hasattr(self, "_tree") else []
        return items[0].data(B_NAME, _ID_ROLE) if items else None

    def select(self, macro_id: str) -> bool:
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            if item is not None and item.data(B_NAME, _ID_ROLE) == macro_id:
                self._tree.setCurrentItem(item)
                return True
        return False

    def _apply_search(self) -> None:
        words = self._search.text().casefold().split()
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            if item is None:
                continue
            macro = self._index.macros.get(item.data(B_NAME, _ID_ROLE))
            extra = [macro.description, macro.id] if macro else []
            text = " ".join([item.text(c) for c in range(self._tree.columnCount())]
                            + extra).casefold()
            item.setHidden(not all(w in text for w in words))

    def _show_details(self) -> None:
        macro = self._index.macros.get(self.selected_id() or "")
        installed = getattr(self, "_installed_now", {})
        self._btn_page.setEnabled(bool(macro and macro.page))
        self._btn_install.setEnabled(bool(
            macro and macro.id not in installed and not macro.needs_newer_api(self._api)
            and macro.lua_file is not None))
        if macro is None:
            self._details.clear()
            return
        esc = html.escape
        parts = [f"<h3>{esc(macro.name)} {esc(macro.version)}</h3>"]
        if macro.author:
            parts.append(f"<p>{esc(tr('macro_repo_by', author=macro.author))}</p>")
        if macro.description:
            parts.append(f"<p>{esc(macro.description)}</p>")
        if macro.needs_newer_api(self._api):
            parts.append(f"<p><b>{esc(tr('macro_mgr_api_newer', needed=macro.requires_api, have=self._api))}</b></p>")
        parts.append(f"<p><b>{esc(tr('macro_repo_permissions'))}</b></p><ul>"
                     + "".join(f"<li>{esc(line)}</li>" for line in index_permission_lines(macro))
                     + "</ul>")
        if macro.changelog:
            parts.append(f"<p><b>{esc(tr('macro_repo_changes'))}</b></p><ul>"
                         + "".join(f"<li>{esc(v)}: {esc(t)}</li>" for v, t in macro.changelog)
                         + "</ul>")
        if macro.page:
            parts.append(f'<p><a href="{esc(macro.page)}">{esc(tr("macro_repo_open_page"))}</a></p>')
        self._details.setHtml("".join(parts))

    def _install_selected(self) -> None:
        macro_id = self.selected_id()
        if macro_id and self._install(macro_id):
            self.reload()

    def _open_page(self) -> None:
        macro = self._index.macros.get(self.selected_id() or "")
        if macro and macro.page:
            QDesktopServices.openUrl(QUrl(macro.page))

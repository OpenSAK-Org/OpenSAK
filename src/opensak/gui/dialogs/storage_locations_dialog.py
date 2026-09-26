"""
src/opensak/gui/dialogs/storage_locations_dialog.py — Help → "OpenSAK File
Locations…" (issue #907).

Viser ALLE steder OpenSAK gemmer data, så brugeren selv kan finde, tage
backup af eller rydde op i dem — fx rester efter en afinstallation, ved
flytning til en ny maskine, eller ved en fejlrapport ("Copy all").

Bygger udelukkende på paths.get_all_storage_locations() (issue #906), så
listen her og det, afinstallationens purge faktisk sletter, aldrig kan
glide fra hinanden. Dialogen er bevidst skrivebeskyttet — sletning hører
til i afinstallationen / en senere "Reset OpenSAK"-funktion.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.lang import tr
from opensak.paths import LocationKind, StorageLocation, get_all_storage_locations

# Beskrivelse pr. slags — skrevet som literals, så test_no_unused_keys
# kan se at nøglerne bruges.
_KIND_KEYS: dict[LocationKind, str] = {
    LocationKind.INSTALL_DIR: "file_locations_kind_install_dir",
    LocationKind.BOOTSTRAP_DIR: "file_locations_kind_bootstrap_dir",
    LocationKind.DATABASE_DIR: "file_locations_kind_database_dir",
    LocationKind.EXTERNAL_DATABASE: "file_locations_kind_external_database",
    LocationKind.QT_SETTINGS: "file_locations_kind_qt_settings",
    LocationKind.KEYRING_ENTRY: "file_locations_kind_keyring_entry",
    LocationKind.APPIMAGE_BINARY: "file_locations_kind_appimage_binary",
    LocationKind.APPIMAGE_DESKTOP_FILE: "file_locations_kind_appimage_desktop_file",
    LocationKind.APPIMAGE_ICON: "file_locations_kind_appimage_icon",
}

_NOT_APPLICABLE = "—"


# ── Hjælpefunktioner (uden Qt-widgets, direkte testbare) ─────────────────────

def describe(loc: StorageLocation) -> str:
    return tr(_KIND_KEYS[loc.kind])


def display_location(loc: StorageLocation) -> str:
    """Den placering brugeren skal se — for MSIX den fysiske sti (#820)."""
    if loc.kind is LocationKind.KEYRING_ENTRY:
        from opensak.email.credentials import _SERVICE_NAME
        return f"{_SERVICE_NAME} / {loc.location}"
    if loc.is_path:
        from opensak.msix import resolve_physical_appdata_path
        return str(resolve_physical_appdata_path(Path(loc.location)))
    return loc.location


def path_size(path: Path) -> int | None:
    """Samlet størrelse i bytes for en fil eller mappe; None hvis den mangler."""
    try:
        if path.is_file():
            return path.stat().st_size
        if not path.is_dir():
            return None
    except OSError:
        return None
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return total


def format_size(size: int | None) -> str:
    if size is None:
        return _NOT_APPLICABLE
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            break
    return f"{value:.1f} {unit}"


def exists_text(loc: StorageLocation) -> str:
    if not loc.is_path:
        return _NOT_APPLICABLE
    return tr("yes") if Path(display_location(loc)).exists() else tr("no")


def folder_to_open(loc: StorageLocation) -> Path | None:
    """Mappen "Open folder" skal åbne: stien selv for mapper, forælderen for filer."""
    if not loc.is_path:
        return None
    path = Path(display_location(loc))
    if path.is_dir():
        return path
    if path.parent.is_dir():
        return path.parent
    return None


def build_report(locations: list[StorageLocation]) -> str:
    """Ren tekst til "Copy all" — beregnet til fejlrapporter/support."""
    from opensak import __version__

    lines = [
        f"{tr('file_locations_title')} — OpenSAK {__version__} "
        f"({platform.system()} {platform.release()})",
        "",
    ]
    for loc in locations:
        size = format_size(path_size(Path(display_location(loc)))) if loc.is_path else _NOT_APPLICABLE
        lines.append(f"{describe(loc)}:")
        lines.append(f"    {display_location(loc)}  [{exists_text(loc)}, {size}]")
    return "\n".join(lines) + "\n"


# ── Dialog ────────────────────────────────────────────────────────────────────

class StorageLocationsDialog(QDialog):
    COL_CONTENTS, COL_LOCATION, COL_EXISTS, COL_SIZE, COL_OPEN = range(5)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("file_locations_title"))
        self.resize(900, 420)
        clamp_dialog_height_to_screen(self, parent)

        self._locations = get_all_storage_locations()

        layout = QVBoxLayout(self)

        intro = QLabel(tr("file_locations_intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._table = QTableWidget(len(self._locations), 5, self)
        self._table.setHorizontalHeaderLabels([
            tr("file_locations_col_contents"),
            tr("file_locations_col_location"),
            tr("file_locations_col_exists"),
            tr("file_locations_col_size"),
            "",
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(self.COL_CONTENTS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_LOCATION, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_EXISTS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_SIZE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_OPEN, QHeaderView.ResizeMode.ResizeToContents)

        for row, loc in enumerate(self._locations):
            self._fill_row(row, loc)

        layout.addWidget(self._table)

        buttons = QDialogButtonBox(self)
        self._copy_btn = QPushButton(tr("file_locations_copy_all"))
        self._copy_btn.clicked.connect(self._copy_all)
        buttons.addButton(self._copy_btn, QDialogButtonBox.ButtonRole.ActionRole)
        close_btn = QPushButton(tr("close"))
        buttons.addButton(close_btn, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _fill_row(self, row: int, loc: StorageLocation) -> None:
        location = display_location(loc)
        size = format_size(path_size(Path(location))) if loc.is_path else _NOT_APPLICABLE

        location_item = QTableWidgetItem(location)
        location_item.setToolTip(location)
        self._table.setItem(row, self.COL_CONTENTS, QTableWidgetItem(describe(loc)))
        self._table.setItem(row, self.COL_LOCATION, location_item)
        self._table.setItem(row, self.COL_EXISTS, QTableWidgetItem(exists_text(loc)))
        self._table.setItem(row, self.COL_SIZE, QTableWidgetItem(size))

        folder = folder_to_open(loc)
        if folder is not None:
            btn = QPushButton(tr("file_locations_open_folder"))
            btn.clicked.connect(lambda _checked=False, f=folder: self._open_folder(f))
            self._table.setCellWidget(row, self.COL_OPEN, btn)

    @staticmethod
    def _open_folder(folder: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _copy_all(self) -> None:
        QApplication.clipboard().setText(build_report(self._locations))

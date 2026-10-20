"""
src/opensak/gui/dialogs/backup_folder_check.py — the one check every place
that chooses a backup folder uses (issue #1032).

The Welcome Wizard, Settings, "Back up now…" and the backup-on-exit prompt
all call confirm_backup_folder(), so they refuse and warn about exactly the
same folders with exactly the same texts:

- refused: a folder validate_backup_dir() rejects (OpenSAK's install or
  database folder itself, or one of the folders a backup copies);
- warned: another folder inside the install or database folder — fine for
  backups, but deleted if the user ever chooses to remove all OpenSAK data
  when uninstalling. The user picks "Use this folder" or "Choose another",
  and is asked only once per folder.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QWidget

from opensak.backup.backupset import (
    BackupError, backup_dir_inside_opensak, validate_backup_dir,
)
from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.lang import tr

# The folder inside OpenSAK's folders the user last said yes to, so the
# warning isn't repeated for it in the next dialog (or the next start).
CONFIRMED_KEY = "backup.inside_confirmed"


def confirm_backup_folder(
    parent: Optional[QWidget],
    folder: Path,
    *,
    title: str,
    install_dir: Optional[Path] = None,
    db_dir: Optional[Path] = None,
) -> bool:
    """
    True if *folder* may be used as the backup folder.

    Shows why when it is refused, and asks first when it is inside the
    install or database folder. *install_dir* and *db_dir* are the folders
    to check against when they are being changed in the same dialog and
    aren't saved yet (#986); otherwise the ones in use now.
    """
    folder = Path(folder)
    try:
        validate_backup_dir(folder, install_dir=install_dir, db_dir=db_dir)
    except BackupError:
        QMessageBox.warning(parent, title, tr("backup_folder_invalid"))
        return False

    inside = backup_dir_inside_opensak(folder, install_dir=install_dir, db_dir=db_dir)
    if inside is None or _already_confirmed(folder):
        return True
    if not _ask_use_inside(parent, title, folder, inside):
        return False
    _remember_confirmed(folder)
    return True


def _ask_use_inside(parent: Optional[QWidget], title: str, folder: Path, inside: Path) -> bool:
    """Ask "Use this folder" / "Choose another"; True for the first."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(tr("backup_folder_inside_warn", path=str(folder), folder=str(inside)))
    use_btn = box.addButton(tr("backup_folder_use_anyway"), QMessageBox.ButtonRole.AcceptRole)
    other_btn = box.addButton(
        tr("backup_folder_choose_another"), QMessageBox.ButtonRole.RejectRole
    )
    box.setDefaultButton(other_btn)
    box.exec()
    return box.clickedButton() is use_btn


def _key(folder: Path) -> str:
    try:
        return str(folder.expanduser().resolve())
    except OSError:
        return str(folder.expanduser().absolute())


def _already_confirmed(folder: Path) -> bool:
    from opensak.settings_store import get_store
    return get_store().get(CONFIRMED_KEY) == _key(folder)


def _remember_confirmed(folder: Path) -> None:
    from opensak.settings_store import get_store
    get_store().set(CONFIRMED_KEY, _key(folder))

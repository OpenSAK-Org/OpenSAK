"""
src/opensak/gui/dialogs/uninstall_choice_dialog.py — Fælles "hvad skal
fjernes?"-valg for in-app afinstallation (issue #859).

Udskilt fra appimage_uninstall_dialog.py (#837), så Linux (AppImage) og
macOS stiller præcis det samme spørgsmål med de samme tekster og den samme
ekstra bekræftelse ved det destruktive valg.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.lang import tr


def ask_uninstall_choice(parent: QWidget | None) -> bool | None:
    """
    Spørg om kun programmet skal fjernes, eller program og alle data.

    Returnerer False for "kun programmet", True for "program og alle data"
    (først efter en ekstra, eksplicit bekræftelse, hvor standardknappen er
    Nej), og None hvis brugeren annullerer på noget tidspunkt.
    """
    msg = QMessageBox(parent)
    msg.setWindowTitle(tr("appimage_uninstall_title"))
    msg.setText(tr("appimage_uninstall_msg"))
    msg.setIcon(QMessageBox.Icon.Warning)

    btn_program_only = msg.addButton(
        tr("appimage_uninstall_btn_program_only"), QMessageBox.ButtonRole.AcceptRole
    )
    btn_purge = msg.addButton(
        tr("appimage_uninstall_btn_purge"), QMessageBox.ButtonRole.DestructiveRole
    )
    msg.addButton(tr("cancel"), QMessageBox.ButtonRole.RejectRole)
    msg.exec()

    clicked = msg.clickedButton()
    if clicked not in (btn_program_only, btn_purge):
        return None
    if clicked == btn_program_only:
        return False

    # Ekstra, eksplicit bekræftelse for det destruktive valg — sletning af
    # alle caches/databaser/indstillinger kan ikke fortrydes.
    confirm = QMessageBox.warning(
        parent,
        tr("appimage_uninstall_purge_confirm_title"),
        tr("appimage_uninstall_purge_confirm_msg"),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if confirm != QMessageBox.StandardButton.Yes:
        return None
    return True

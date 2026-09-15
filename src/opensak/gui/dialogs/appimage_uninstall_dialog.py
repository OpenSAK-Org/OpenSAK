"""
src/opensak/gui/dialogs/appimage_uninstall_dialog.py — In-app afinstaller
for AppImage-brugere (issue #837, Step C i epic #824).

Viser bekræftelsesdialogen med remove/purge-valget fra §4.3 i
designdokumentet, og udfører selve afinstallationen ved bekræftelse.
Ingen effekt for kilde-install-brugere eller på Windows/macOS.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from opensak import appimage
from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.lang import tr
from opensak.logger import get_logger

log = get_logger("appimage_uninstall_dialog")


def confirm_and_uninstall(parent: QWidget | None) -> bool:
    """
    Vis bekræftelsesdialogen og udfør afinstallationen hvis brugeren
    bekræfter. Returnerer True hvis afinstallationen lykkedes (kalderen
    bør derefter lukke applikationen) — ellers False (annulleret, eller
    fejlet — fejlbesked er allerede vist).
    """
    if not appimage.is_running_as_appimage() or not appimage.is_appimage_integrated():
        return False

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
        return False

    purge = clicked == btn_purge

    if purge:
        # Ekstra, eksplicit bekræftelse for det destruktive valg —
        # sletning af alle caches/databaser/indstillinger kan ikke fortrydes.
        confirm = QMessageBox.warning(
            parent,
            tr("appimage_uninstall_purge_confirm_title"),
            tr("appimage_uninstall_purge_confirm_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return False

    result = appimage.uninstall_appimage(purge_data=purge)
    if not result.success:
        log.warning("Afinstallation fejlede fra dialog: %s", result.error)
        QMessageBox.warning(
            parent,
            tr("appimage_uninstall_error_title"),
            tr("appimage_uninstall_error_msg", error=result.error or ""),
        )
        return False

    log.debug("AppImage afinstalleret via dialog (purge=%s)", purge)
    QMessageBox.information(
        parent,
        tr("appimage_uninstall_done_title"),
        tr("appimage_uninstall_done_msg"),
    )
    return True

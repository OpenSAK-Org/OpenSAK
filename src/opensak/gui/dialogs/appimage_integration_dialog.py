"""
src/opensak/gui/dialogs/appimage_integration_dialog.py — Førstegangs-
integrationsprompt for AppImage-brugere (issue #835, Step A i epic #824).

Viser (hvis relevant, se appimage.should_prompt_for_integration())
engangs-dialogen "Installer OpenSAK i din programmenu?" ved opstart, og
udfører selve integrationen ved "Ja". Ingen effekt på Windows/macOS eller
ved kildekørsel — se appimage.py's docstring for detektionsmønsteret.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from opensak import appimage
from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.lang import tr
from opensak.logger import get_logger

log = get_logger("appimage_integration_dialog")


def maybe_prompt_for_integration(parent: QWidget | None) -> None:
    """
    Kald ved opstart (efter hovedvinduet er klar). No-op hvis der ikke er
    noget at spørge om — se appimage.should_prompt_for_integration().
    """
    if not appimage.should_prompt_for_integration():
        return

    msg = QMessageBox(parent)
    msg.setWindowTitle(tr("appimage_integrate_title"))
    msg.setText(tr("appimage_integrate_msg"))
    msg.setIcon(QMessageBox.Icon.Question)

    btn_yes = msg.addButton(
        tr("appimage_integrate_btn_yes"), QMessageBox.ButtonRole.AcceptRole
    )
    msg.addButton(
        tr("appimage_integrate_btn_no"), QMessageBox.ButtonRole.RejectRole
    )
    btn_dont_ask = msg.addButton(
        tr("appimage_integrate_btn_dont_ask"), QMessageBox.ButtonRole.DestructiveRole
    )
    msg.exec()

    clicked = msg.clickedButton()
    if clicked == btn_yes:
        _integrate_with_feedback(parent)
    elif clicked == btn_dont_ask:
        appimage.decline_integration(remember=True)
    # "Nej tak" (RejectRole) og lukning af dialogen: intet gemmes,
    # dialogen vises igen ved næste opstart.


def _integrate_with_feedback(parent: QWidget | None) -> None:
    """Udfør integrationen og vis resultatet (succes eller fejlbesked)."""
    result = appimage.integrate_appimage()
    if result.success:
        log.debug("AppImage integreret via førstegangs-dialog: %s", result.installed_path)
        QMessageBox.information(
            parent,
            tr("appimage_integrate_success_title"),
            tr("appimage_integrate_success_msg"),
        )
    else:
        log.warning("AppImage-integration fejlede fra dialog: %s", result.error)
        QMessageBox.warning(
            parent,
            tr("appimage_integrate_error_title"),
            tr("appimage_integrate_error_msg", error=result.error or ""),
        )

"""
src/opensak/gui/dialogs/macos_uninstall_dialog.py — In-app afinstaller
for macOS (issue #859, Step 4 i epic #824).

Samme valg som på Linux (uninstall_choice_dialog.py): "fjern kun
programmet" eller "fjern program og alle data". Programmet flyttes til
papirkurven; se macos_uninstall.py for rækkefølgen og hvorfor den er vigtig.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from opensak import macos_uninstall
from opensak.gui.dialogs.uninstall_choice_dialog import ask_uninstall_choice
from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.lang import tr
from opensak.logger import get_logger

log = get_logger("macos_uninstall_dialog")


def _show_blocked(parent: QWidget | None, blocker: macos_uninstall.UninstallBlocker,
                  folder: str) -> None:
    if blocker is macos_uninstall.UninstallBlocker.TRANSIENT_LOCATION:
        text = tr("macos_uninstall_blocked_transient_msg")
    else:
        text = tr("macos_uninstall_blocked_not_writable_msg", folder=folder)
    QMessageBox.information(parent, tr("macos_uninstall_blocked_title"), text)


def confirm_and_uninstall(parent: QWidget | None) -> bool:
    """
    Vis bekræftelsesdialogen og afinstallér OpenSAK hvis brugeren bekræfter.

    Returnerer True når kalderen skal lukke applikationen STRAKS og uden
    yderligere UI: enten er bundlen flyttet til papirkurven, eller data er
    slettet (så den kørende app ikke længere har noget at arbejde med).
    Returnerer False ved annullering eller når intet er ændret.
    """
    bundle = macos_uninstall.running_bundle()
    if bundle is None:
        return False

    # Tjek FØR brugeren spørges og før noget slettes — vi må aldrig slette
    # data og derefter opdage, at programmet ikke kan fjernes.
    blocker = macos_uninstall.check_can_uninstall(bundle)
    if blocker is not None:
        log.debug("macOS-afinstallation blokeret (%s): %s", blocker.value, bundle)
        _show_blocked(parent, blocker, str(bundle.parent))
        return False

    purge = ask_uninstall_choice(parent)
    if purge is None:
        return False

    if purge:
        # Mens bundlen stadig er intakt — purge_user_data() indlæser bl.a.
        # keyring-modulet dovent fra bundlen.
        from opensak.paths import purge_user_data

        purge_user_data()

    # Sidste besked FØR flytningen: bagefter må der ikke vises mere UI,
    # fordi PyInstaller kan have brug for at indlæse filer fra bundlen.
    QMessageBox.information(
        parent,
        tr("appimage_uninstall_done_title"),
        tr("macos_uninstall_closing_msg"),
    )

    try:
        macos_uninstall.move_bundle_to_trash(bundle)
    except OSError as exc:
        # Bundlen er urørt, så det er sikkert at vise en fejldialog.
        log.warning("Kunne ikke flytte OpenSAK til papirkurven: %s", exc)
        QMessageBox.warning(
            parent,
            tr("appimage_uninstall_error_title"),
            tr("macos_uninstall_trash_error_msg", error=str(exc)),
        )
        # Er data allerede slettet, skal appen lukke alligevel.
        return purge

    log.debug("OpenSAK afinstalleret på macOS (purge=%s)", purge)
    return True

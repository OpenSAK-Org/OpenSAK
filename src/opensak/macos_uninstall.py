"""
src/opensak/macos_uninstall.py — In-app afinstallation på macOS
(issue #859, Step 4 i epic #824).

Samme model som AppImage-afinstallationen på Linux (#837): brugeren vælger i
OpenSAK selv mellem "fjern kun programmet" og "fjern program og alle data".
Erstatter det oprindeligt foreslåede, separate .command-script, fordi:

  - data-sletningen genbruger paths.purge_user_data() (#906), som er den
    eneste kilde til sandhed om, hvor OpenSAK gemmer data (brugervalgt
    installations-/databasemappe, QSettings-plist, PQ Email-kodeordet i
    Keychain). Et shell-script ville ikke kunne se det meste af det.
  - koden ligger i den allerede signerede/notariserede .app, så der er
    ingen ekstra signerings- eller notariseringsrunde.

Programmet flyttes til papirkurven (ikke slettet), så selve
programfjernelsen kan fortrydes. Kun data slettes permanent, og kun efter
dobbelt bekræftelse i dialogen.

Rækkefølgen er vigtig — se macos_uninstall_dialog.confirm_and_uninstall():
  1. check_can_uninstall() FØR brugeren spørges og FØR data slettes, så vi
     aldrig sletter data og derefter ikke kan fjerne programmet.
  2. Valgfri purge_user_data() mens bundlen stadig er intakt (keyring-
     modulet m.fl. kan stadig indlæses fra bundlen).
  3. move_bundle_to_trash() som det ALLERSIDSTE skridt, lige før quit().
     PyInstaller indlæser moduler dovent fra .app-bundlen, så efter
     flytningen må der ikke ske mere end at lukke — samme princip som
     #893's selv-installation.

Ingen effekt på Windows/Linux eller ved kildekørsel: running_bundle()
returnerer None, og Indstillinger viser så ikke knappen.
"""

from __future__ import annotations

import os
import sys
from enum import Enum
from pathlib import Path

from opensak.logger import get_logger

log = get_logger("macos_uninstall")


class UninstallBlocker(str, Enum):
    """Hvorfor OpenSAK ikke kan fjerne sig selv fra den aktuelle placering."""
    TRANSIENT_LOCATION = "transient_location"
    NOT_WRITABLE = "not_writable"


def running_bundle() -> Path | None:
    """
    Den .app-bundle den kørende OpenSAK ligger i — kun på macOS og kun når
    appen kører frosset (PyInstaller). None ellers.

    Genbruger updater._running_app_bundle() (#893), så afinstallation og
    selv-opdatering altid er enige om, hvilken bundle "OpenSAK" er.
    """
    if sys.platform != "darwin":
        return None
    from opensak.updater import _running_app_bundle

    return _running_app_bundle()


def is_supported() -> bool:
    """True når in-app-afinstallation giver mening (macOS, frosset .app)."""
    return running_bundle() is not None


def check_can_uninstall(bundle: Path) -> UninstallBlocker | None:
    """
    Returnér None hvis *bundle* kan flyttes til papirkurven, ellers årsagen.

    - TRANSIENT_LOCATION: appen kører direkte fra en monteret DMG eller en
      Gatekeeper "App Translocation"-kopi. Der er intet installeret at
      fjerne — brugeren skal trække appen fra Programmer til papirkurven.
    - NOT_WRITABLE: brugeren har ikke skriveadgang til mappen (typisk en
      standardbruger og /Applications). Finder kan klare det med en
      administratoradgangskode; det kan vi ikke.
    """
    from opensak.updater import _is_transient_location

    if _is_transient_location(bundle):
        return UninstallBlocker.TRANSIENT_LOCATION
    if not os.access(bundle.parent, os.W_OK) or not os.access(bundle, os.W_OK):
        return UninstallBlocker.NOT_WRITABLE
    return None


def _is_safe_bundle_path(bundle: Path) -> bool:
    """
    Sikkerhedsvagt — samme ånd som paths._is_unsafe_to_delete() (#837/#906):
    flyt kun noget der rent faktisk er en .app-bundle, og aldrig
    filsystemroden, hjemmemappen eller en mappe der indeholder den.
    """
    try:
        resolved = bundle.resolve()
        home = Path.home().resolve()
    except OSError:
        return False
    if resolved.suffix != ".app" or not resolved.is_dir() or bundle.is_symlink():
        return False
    return not (
        resolved == home
        or resolved == Path(resolved.anchor)
        or home.is_relative_to(resolved)
    )


def _qt_move_to_trash(path: Path) -> bool:
    """
    Flyt *path* til papirkurven via Qt (NSFileManager.trashItemAtURL på
    macOS). Egen funktion så tests kan erstatte den.

    PySide6's type-stub siger (bool, str), men runtime har returneret en
    ren bool — begge former håndteres.
    """
    from PySide6.QtCore import QFile

    result = QFile.moveToTrash(str(path))
    if isinstance(result, tuple):
        return bool(result[0])
    return bool(result)


def move_bundle_to_trash(bundle: Path) -> None:
    """
    Flyt *bundle* til papirkurven. Kaster OSError hvis det ikke lykkes —
    i så fald er bundlen urørt, og det er sikkert at vise en fejldialog.

    Efter et vellykket kald må kalderen ikke gøre andet end at lukke
    applikationen (se modul-docstringen).
    """
    if not _is_safe_bundle_path(bundle):
        raise OSError(f"refusing to remove unexpected path: {bundle}")
    if not _qt_move_to_trash(bundle) or bundle.exists():
        raise OSError(f"could not move {bundle} to the Trash")
    log.debug("OpenSAK-bundle flyttet til papirkurven: %s", bundle)

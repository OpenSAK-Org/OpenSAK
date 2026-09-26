"""
src/opensak/paths.py — samlet oversigt over ALLE steder OpenSAK gemmer
data (issue #906), og oprydning af dem ved afinstallation med data-sletning.

Baggrund: _purge_user_data() i appimage.py havde sin egen hårdkodede liste
(kun install_dir + db_dir), og glemte derfor bl.a. bootstrap-mappen
(~/.config/opensak/bootstrap.json), den gamle QSettings-fil og PQ Email-
kodeordet i OS-keyringen. Denne fil er nu den ENESTE kilde til sandhed —
både afinstallationens purge og den kommende "OpenSAK File Locations…"-
dialog (issue #907) bruger get_all_storage_locations(), så det der vises
for brugeren og det der faktisk slettes aldrig kan glide fra hinanden.

Tilføjer du et nyt sted, hvor OpenSAK skriver data, SKAL det med her.

Bemærk om QtWebEngine: verificeret (PySide6 6.11) at både kortets profil
(map_widget.py) og default-profilen (beskrivelsespanelet i cache_detail.py)
er off-the-record — der skrives intet WebEngine-data til disken, så der er
ingen WebEngine-mapper at liste eller rydde op i.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from opensak.logger import get_logger

log = get_logger("paths")

# Samme navne som app.py sætter via setOrganizationName/setApplicationName,
# og som mainwindow.py (tastaturgenveje) og settings_store.py (migration)
# bruger til QSettings.
_QT_ORGANIZATION = "OpenSAK Project"
_QT_APPLICATION = "OpenSAK"


class LocationKind(str, Enum):
    """Hvilken slags lagringssted en StorageLocation beskriver."""
    BOOTSTRAP_DIR = "bootstrap_dir"
    INSTALL_DIR = "install_dir"
    DATABASE_DIR = "database_dir"
    EXTERNAL_DATABASE = "external_database"
    QT_SETTINGS = "qt_settings"
    KEYRING_ENTRY = "keyring_entry"
    APPIMAGE_BINARY = "appimage_binary"
    APPIMAGE_DESKTOP_FILE = "appimage_desktop_file"
    APPIMAGE_ICON = "appimage_icon"


@dataclass(frozen=True)
class StorageLocation:
    """
    Ét sted hvor OpenSAK gemmer noget.

    location:          filsti, Windows-registreringsnøgle, eller (for
                       KEYRING_ENTRY) brugernavnet som kodeordet er gemt under.
    is_path:           True hvis `location` er en filsystem-sti.
    removed_on_purge:  True hvis stedet slettes ved "afinstallér + slet data".
                       False for program-/integrationsfiler (dem fjerner
                       afinstallationen selv, uanset valg) og for databaser
                       brugeren har åbnet fra andre steder (fx et eksternt
                       drev) — dem sletter vi aldrig automatisk.
    """
    kind: LocationKind
    location: str
    is_path: bool = True
    removed_on_purge: bool = True


# ── QSettings ─────────────────────────────────────────────────────────────────

def qsettings_location() -> str:
    """
    Returner den faktiske placering af OpenSAKs QSettings-data.

    Spørger Qt selv (QSettings.fileName()) i stedet for at gætte, da stien
    er platform-specifik: en .conf-fil på Linux, en .plist på macOS og en
    registreringsnøgle (\\HKEY_CURRENT_USER\\Software\\...) på Windows.
    Kræver ingen QApplication-instans.

    tests/conftest.py patcher denne funktion til en tmp-sti, fordi Qt
    cacher sin config-mappe pr. proces og derfor ikke respekterer
    testernes monkeypatchede XDG_CONFIG_HOME.
    """
    from PySide6.QtCore import QSettings
    return QSettings(_QT_ORGANIZATION, _QT_APPLICATION).fileName()


def _is_registry_location(location: str) -> bool:
    return location.upper().startswith("\\HKEY_")


# ── Samling af alle steder ────────────────────────────────────────────────────

def _is_inside(path: Path, parent: Path) -> bool:
    try:
        return path.resolve().is_relative_to(parent.resolve())
    except OSError:
        return False


def _external_databases(covered_dirs: list[Path]) -> list[Path]:
    """Registrerede databaser der ligger UDEN FOR de kendte OpenSAK-mapper."""
    from opensak import settings_store

    db_list = settings_store.get_store().get("databases.list", []) or []
    result: list[Path] = []
    for entry in db_list:
        raw = entry.get("path") if isinstance(entry, dict) else None
        if not raw:
            continue
        p = Path(raw)
        if any(_is_inside(p, d) for d in covered_dirs):
            continue
        if p not in result:
            result.append(p)
    return result


def _appimage_locations() -> list[StorageLocation]:
    """AppImage-integrationsfiler — kun på Linux, og kun dem der findes."""
    if not sys.platform.startswith("linux"):
        return []
    from opensak import appimage

    candidates: list[tuple[LocationKind, Path]] = [
        (LocationKind.APPIMAGE_BINARY, appimage.get_integrated_appimage_path()),
        (LocationKind.APPIMAGE_DESKTOP_FILE, appimage.get_desktop_file_path()),
    ]
    candidates += [(LocationKind.APPIMAGE_ICON, p) for p in appimage.get_icon_paths()]
    return [
        StorageLocation(kind, str(p), removed_on_purge=False)
        for kind, p in candidates
        if p.exists()
    ]


def get_all_storage_locations() -> list[StorageLocation]:
    """
    Returner ALLE steder OpenSAK gemmer data for den aktuelle bruger og
    platform. Rækkefølgen er den, en bruger naturligt vil læse den i.
    """
    from opensak import settings_store

    install_dir = settings_store.get_install_dir()
    bootstrap_dir = settings_store._bootstrap_path().parent

    # databases.dir læses direkte fra store — get_db_dir() opretter mappen
    # som sideeffekt, og en oversigt/oprydning må ikke skabe nye mapper.
    raw_db_dir = settings_store.get_store().get("databases.dir")
    db_dir = Path(raw_db_dir) if raw_db_dir else None

    locations: list[StorageLocation] = [
        StorageLocation(LocationKind.BOOTSTRAP_DIR, str(bootstrap_dir)),
        StorageLocation(LocationKind.INSTALL_DIR, str(install_dir)),
    ]
    covered = [bootstrap_dir, install_dir]

    if db_dir is not None and not _is_inside(db_dir, install_dir):
        locations.append(StorageLocation(LocationKind.DATABASE_DIR, str(db_dir)))
        covered.append(db_dir)

    locations += [
        StorageLocation(LocationKind.EXTERNAL_DATABASE, str(p), removed_on_purge=False)
        for p in _external_databases(covered)
    ]

    qs = qsettings_location()
    locations.append(
        StorageLocation(LocationKind.QT_SETTINGS, qs, is_path=not _is_registry_location(qs))
    )

    username = str(settings_store.get_store().get("pq_email.username", "") or "")
    if username:
        locations.append(
            StorageLocation(LocationKind.KEYRING_ENTRY, username, is_path=False)
        )

    locations += _appimage_locations()

    # Windows (ikke-MSIX): bootstrap- og install-mappen er den SAMME
    # (%APPDATA%\opensak) — vis den kun én gang.
    unique: list[StorageLocation] = []
    seen: set[tuple[bool, str]] = set()
    for loc in locations:
        key = (loc.is_path, os.path.normcase(loc.location))
        if key not in seen:
            seen.add(key)
            unique.append(loc)
    return unique


# ── Oprydning ─────────────────────────────────────────────────────────────────

def _is_unsafe_to_delete(path: Path) -> bool:
    """
    Sikkerhedstjek — slet aldrig hjemmemappen, filsystemroden eller en
    mappe der INDEHOLDER hjemmemappen (fx /home), uanset hvad en
    fejlkonfigureret sti måtte pege på.
    """
    try:
        resolved = path.resolve()
        home = Path.home().resolve()
    except OSError:
        return True
    return (
        resolved == home
        or resolved == Path(resolved.anchor)
        or home.is_relative_to(resolved)
    )


def _remove_path(path: Path) -> None:
    if _is_unsafe_to_delete(path):
        log.warning("Springer over mistænkelig sti ved data-oprydning: %s", path)
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists() or path.is_symlink():
        path.unlink(missing_ok=True)


def _remove_qsettings(loc: StorageLocation) -> None:
    if not loc.is_path:
        # Windows-registreringsdatabasen — kan kun ryddes via Qt selv.
        from PySide6.QtCore import QSettings
        s = QSettings(_QT_ORGANIZATION, _QT_APPLICATION)
        s.clear()
        s.sync()
        return
    conf = Path(loc.location)
    _remove_path(conf)
    # "OpenSAK Project"-mappen fjernes kun hvis den nu er tom.
    try:
        conf.parent.rmdir()
    except OSError:
        pass


def _remove_keyring_entry(username: str) -> None:
    try:
        from opensak.email import credentials
    except ImportError:
        return
    if not credentials.delete_password(username):
        log.warning("Kunne ikke slette PQ Email-kodeord fra OS-keyring")


def purge_user_data() -> None:
    """
    Slet alle OpenSAK-brugerdata (alle steder med removed_on_purge=True).

    Alle steder samles FØR noget slettes: install_dir læses fra
    bootstrap.json, og keyring-brugernavnet fra opensak.json — begge er
    væk, når først mapperne er slettet.
    """
    locations = [loc for loc in get_all_storage_locations() if loc.removed_on_purge]

    for loc in locations:
        if loc.kind is LocationKind.KEYRING_ENTRY:
            _remove_keyring_entry(loc.location)
        elif loc.kind is LocationKind.QT_SETTINGS:
            _remove_qsettings(loc)
        elif loc.is_path:
            _remove_path(Path(loc.location))

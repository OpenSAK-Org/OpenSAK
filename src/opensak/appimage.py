"""
src/opensak/appimage.py — Linux AppImage-detektion og selv-integration
(issue #835, Step A i epic #824).

Erstatter den oprindeligt planlagte "uninstall.sh + AppImageUpdate"-tilgang
med selv-integration/-opdatering/-afinstallation implementeret direkte i
OpenSAK (Python), så brugeren aldrig skal åbne en terminal efter det
indledende "gør filen eksekverbar"-trin. Se
design-linux-appimage-selvadministration.md for den fulde arkitekturbaggrund.

Dette modul dækker KUN §4.1 (selv-integration ved første kørsel):
  - is_running_as_appimage() / get_appimage_path(): detektion, baseret på
    miljøvariablen $APPIMAGE som AppImage-runtiden selv sætter. Samme
    mønster som is_msix_packaged() i msix.py — graceful degradation
    (False/None) på alle andre platforme og ved kildekørsel.
  - should_prompt_for_integration(): beslutter om førstegangs-dialogen skal
    vises, inkl. stille "adoption" hvis AppImageLauncher allerede har
    integreret appen (undgår dobbelt-integration/generende gentaget dialog).
  - integrate_appimage(): selve integrationen — kopiér AppImage-filen,
    generér .desktop-fil, installér ikon, gem status i settings_store.

Selv-opdatering (§4.2) og in-app afinstaller (§4.3) er separate,
efterfølgende issues (B og C) der bygger oven på den sti/status dette
modul gemmer — ikke del af dette modul.

Linux-specifikt, men alle offentlige funktioner degraderer gracefully
(False/None/no-op) på Windows/macOS og ved almindelig kildekørsel, så de
er sikre at kalde ubetinget fra platform-uafhængig kode (app.py,
mainwindow.py, settings_dialog.py).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from opensak.logger import get_logger
from opensak.settings_store import get_store

log = get_logger("appimage")

# Fast, XDG-korrekt placering af den integrerede kopi — i $PATH på de
# fleste distroer, bruger-lokal (intet sudo/root krævet). Se §7 punkt 1
# i designdokumentet for hvorfor ~/Applications/ blev fravalgt (det er en
# macOS-konvention, ikke en Linux-en).
_INTEGRATED_FILENAME = "OpenSAK.AppImage"
_DESKTOP_FILENAME = "opensak.desktop"

# Settings-nøgler (gemt i opensak.json via settings_store.SettingsStore)
_KEY_INTEGRATED = "appimage.integrated"
_KEY_INSTALL_PATH = "appimage.install_path"
_KEY_DECLINED_PERMANENTLY = "appimage.integration_declined"


@dataclass
class IntegrationResult:
    """Resultat af et integrate_appimage()-kald."""
    success: bool
    installed_path: Path | None = None
    error: str | None = None


@dataclass
class UninstallResult:
    """Resultat af et uninstall_appimage()-kald."""
    success: bool
    error: str | None = None


# ── Detektion ────────────────────────────────────────────────────────────────

def is_running_as_appimage() -> bool:
    """
    Returner True hvis processen kører som en AppImage.

    AppImage-runtiden sætter selv miljøvariablen $APPIMAGE til den fulde
    sti til den kørende .AppImage-fil — det er den officielle, pålidelige
    detektionsmekanisme (analogt til is_msix_packaged() for Windows).
    Returnerer False ved almindelig kildekørsel og på Windows/macOS.
    """
    return get_appimage_path() is not None


def get_appimage_path() -> Path | None:
    """
    Returner stien til den kørende .AppImage-fil, eller None hvis processen
    ikke kører som AppImage (eller filen af en eller anden grund ikke
    findes længere, fx afmonteret undervejs).
    """
    value = os.environ.get("APPIMAGE")
    if not value:
        return None
    path = Path(value)
    return path if path.exists() else None


def get_appdir_path() -> Path | None:
    """
    Returner $APPDIR (den mount-punkt-sti AppImage-runtiden tilbyder mens
    processen kører, hvorfra bundlede assets som ikoner kan læses), eller
    None hvis ikke sat/ikke en mappe.
    """
    value = os.environ.get("APPDIR")
    if not value:
        return None
    path = Path(value)
    return path if path.is_dir() else None


def get_integrated_appimage_path() -> Path:
    """Den faste sti den integrerede AppImage-kopi lægges/findes på."""
    return Path.home() / ".local" / "bin" / _INTEGRATED_FILENAME


# ── Status (gemt via settings_store) ────────────────────────────────────────

def is_appimage_integrated() -> bool:
    """True hvis OpenSAK tidligere er blevet integreret (af os selv, eller
    stille "adopteret" fordi AppImageLauncher allerede havde gjort det)."""
    return bool(get_store().get(_KEY_INTEGRATED, False))


def get_integration_install_path() -> Path | None:
    """
    Den sti vi selv gemte ved integration — bruges af senere
    selv-opdaterings-/afinstaller-logik (Issue B/C) i stedet for at gætte.
    """
    raw = get_store().get(_KEY_INSTALL_PATH)
    return Path(raw) if raw else None


def _integration_declined_permanently() -> bool:
    return bool(get_store().get(_KEY_DECLINED_PERMANENTLY, False))


def decline_integration(*, remember: bool) -> None:
    """
    Kaldes når brugeren afviser førstegangsdialogen.

    remember=True  ("Spørg ikke igen") — sæt permanent flag, dialogen
                    vises ikke igen (kan stadig trigges manuelt fra
                    Indstillinger → Avanceret, se settings_dialog.py).
    remember=False ("Nej tak") — intet gemmes, dialogen vises igen ved
                    næste opstart.
    """
    if remember:
        get_store().set(_KEY_DECLINED_PERMANENTLY, True)


def _has_appimagelauncher_desktop_entry() -> bool:
    """
    Tjek om en .desktop-fil med `X-AppImage-Identifier` (AppImageLaunchers
    egen markør) allerede findes og ser ud til at pege på OpenSAK.

    Kun et defensivt "spring over"-tjek (§7 punkt 2 i designdokumentet) —
    ikke fuld gensidig interop med AppImageLauncher. Falsk negativ (vi
    overser en eksisterende AppImageLauncher-integration) betyder blot at
    brugeren får vores egen dialog én gang ekstra; falsk positiv er ikke
    realistisk da vi kræver "opensak" i indholdet.
    """
    apps_dir = Path.home() / ".local" / "share" / "applications"
    if not apps_dir.is_dir():
        return False
    try:
        desktop_files = list(apps_dir.glob("*.desktop"))
    except OSError:
        return False
    for f in desktop_files:
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "X-AppImage-Identifier" in content and "opensak" in content.lower():
            return True
    return False


def should_prompt_for_integration() -> bool:
    """
    Kaldes ved opstart. Returnerer True hvis førstegangs-dialogen
    ("Installer OpenSAK i din programmenu?") skal vises.

    Håndterer den stille AppImageLauncher-adoption internt: hvis en
    eksisterende AppImageLauncher-integration opdages, sættes vores eget
    flag uden at vise dialogen.
    """
    if not is_running_as_appimage():
        return False
    if is_appimage_integrated():
        return False
    if _has_appimagelauncher_desktop_entry():
        log.debug("Eksisterende AppImageLauncher-integration fundet — adopterer stille")
        get_store().set(_KEY_INTEGRATED, True)
        return False
    if _integration_declined_permanently():
        return False
    return True


# ── Selve integrationen ──────────────────────────────────────────────────────

def _find_bundled_icon() -> Path | None:
    """
    Find det ikon der er bundlet inde i den kørende AppImage.

    make_appdir.py (CI-buildet) bundler kun én størrelse (256x256) på to
    mulige stier — der findes IKKE et fuldt sæt størrelser som
    scripts/install_icon_linux.sh's kildeinstallations-variant håndterer.
    """
    appdir = get_appdir_path()
    if appdir is None:
        return None
    candidates = [
        appdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "opensak.png",
        appdir / "opensak.png",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _install_icons(icon_src: Path) -> None:
    """
    Installér ikonet i XDG-stierne — Python-port af
    scripts/install_icon_linux.sh's kernelogik (kun 256x256, se
    _find_bundled_icon()). Ikon-cache-opdatering er best-effort og må
    aldrig fejle selve integrationen.
    """
    hicolor_dir = Path.home() / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    hicolor_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon_src, hicolor_dir / "opensak.png")

    pixmaps_dir = Path.home() / ".local" / "share" / "pixmaps"
    pixmaps_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon_src, pixmaps_dir / "opensak.png")

    for cmd in (
        ["gtk-update-icon-cache", "-f", "-t",
         str(Path.home() / ".local" / "share" / "icons" / "hicolor")],
        ["xdg-icon-resource", "forceupdate"],
    ):
        try:
            subprocess.run(cmd, check=False, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            # Samme "best-effort" holdning som det oprindelige shell-script
            # (`command -v ... &>/dev/null`) — værktøjet er måske ikke
            # installeret, det må ikke stoppe selve integrationen.
            pass


def _install_desktop_file(target_path: Path) -> Path:
    """Generér ~/.local/share/applications/opensak.desktop pegende på target_path."""
    apps_dir = Path.home() / ".local" / "share" / "applications"
    apps_dir.mkdir(parents=True, exist_ok=True)
    desktop_path = apps_dir / _DESKTOP_FILENAME

    content = "\n".join([
        "[Desktop Entry]",
        "Name=OpenSAK",
        "Comment=Open Source geocaching management tool",
        f'Exec="{target_path}"',
        "Icon=opensak",
        "Type=Application",
        "Categories=Utility;Science;",
        "Terminal=false",
        "",
    ])
    desktop_path.write_text(content, encoding="utf-8")

    # Nogle desktopmiljøer (Nautilus m.fl.) kræver eksekverbar-bit før en
    # ny .desktop-fil vises som "trusted" i menuen uden ekstra klik.
    mode = desktop_path.stat().st_mode
    os.chmod(desktop_path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return desktop_path


def integrate_appimage() -> IntegrationResult:
    """
    Udfør selve integrationen: kopiér den kørende AppImage til
    ~/.local/bin/OpenSAK.AppImage, generér .desktop-fil og installér ikon.

    Gemmer status + install-sti i settings_store ved succes, så
    selv-opdaterings- og afinstaller-logik (Issue B/C) kender den
    PRÆCISE placering uden at skulle gætte.
    """
    source = get_appimage_path()
    if source is None:
        return IntegrationResult(success=False, error="not_running_as_appimage")

    target = get_integrated_appimage_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

        mode = target.stat().st_mode
        os.chmod(target, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        _install_desktop_file(target)

        icon_src = _find_bundled_icon()
        if icon_src is not None:
            _install_icons(icon_src)
        else:
            log.debug("Intet bundlet ikon fundet ($APPDIR ikke sat/tomt) — springer over")

    except OSError as exc:
        log.warning("AppImage-integration fejlede: %s", exc)
        return IntegrationResult(success=False, error=str(exc))

    get_store().set_many({
        _KEY_INTEGRATED: True,
        _KEY_INSTALL_PATH: str(target),
    })
    log.debug("AppImage integreret til %s", target)
    return IntegrationResult(success=True, installed_path=target)


# ── In-app afinstaller (issue #837, Step C i epic #824) ─────────────────────

def _remove_desktop_file() -> None:
    desktop_path = Path.home() / ".local" / "share" / "applications" / _DESKTOP_FILENAME
    desktop_path.unlink(missing_ok=True)


def _remove_icons() -> None:
    """Fjern præcis de ikon-filer integrate_appimage() selv installerede."""
    hicolor_path = (
        Path.home() / ".local" / "share" / "icons" / "hicolor"
        / "256x256" / "apps" / "opensak.png"
    )
    hicolor_path.unlink(missing_ok=True)
    pixmap_path = Path.home() / ".local" / "share" / "pixmaps" / "opensak.png"
    pixmap_path.unlink(missing_ok=True)


def _reset_integration_flags() -> None:
    """
    Nulstil integrations-status i settings_store.

    Kaldes UANSET remove/purge-valg: selve integrationsartefakterne
    (.desktop-fil, ikoner, den kopierede AppImage-fil) fjernes altid, så en
    senere geninstalleret/genintegreret AppImage skal kunne blive korrekt
    tilbudt integration igen (should_prompt_for_integration()) i stedet
    for at forblive tavs pga. et forældet "allerede integreret"-flag.

    VIGTIGT — rækkefølge: skal kaldes FØR en eventuel _purge_user_data(),
    da get_store() (via get_install_dir()) selv genopretter
    installations-mappen hvis den mangler. Kaldes den efter en fuld purge,
    ville det utilsigtet efterlade en tom, "genoprettet" installations-
    mappe med kun disse to nøgler i.
    """
    store = get_store()
    store.set_many({
        _KEY_INTEGRATED: False,
        _KEY_DECLINED_PERMANENTLY: False,
    })
    store.delete(_KEY_INSTALL_PATH)


def _purge_user_data() -> None:
    """
    Slet OpenSAKs installations- og database-mapper (settings, databases).

    Bruger settings_store.get_install_dir()/get_db_dir() — de PRÆCISE
    stier OpenSAK selv bruger (inkl. en evt. brugertilpasset database-
    placering fra velkomst-wizarden), i stedet for at gætte stier selv
    (det var netop svagheden ved en ekstern bash-uninstaller, se §4.3 i
    designdokumentet).
    """
    from opensak.settings_store import get_db_dir, get_install_dir

    home = Path.home().resolve()
    dirs_to_remove: set[Path] = set()
    for getter in (get_install_dir, get_db_dir):
        try:
            d = getter().resolve()
        except OSError:
            continue
        # Sikkerhedstjek — slet aldrig hjemmemappen eller filsystemroden
        # selv, uanset hvad en fejlkonfigureret sti måtte pege på.
        if d == home or d == Path(d.anchor):
            log.warning("Springer over mistænkelig sti ved data-oprydning: %s", d)
            continue
        dirs_to_remove.add(d)

    for d in dirs_to_remove:
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)


def uninstall_appimage(*, purge_data: bool) -> UninstallResult:
    """
    Fjern OpenSAK fra programmenuen, og valgfrit alle data.

    Rækkefølge (se §4.3 i designdokumentet):
      1. .desktop-fil og ikoner fjernes.
      2. Integrations-flag nulstilles.
      3. Ved purge_data=True: installations- og database-mapperne slettes.
      4. Den integrerede AppImage-kopi slettes SIDST — processen kører
         selv fra denne fil. Linux tillader at slette en åben fils inode
         uden at afbryde den kørende proces (samme princip som den
         atomiske udskiftning i selv-opdateringen, appimage.py's søster-
         funktionalitet i updater.py).
    """
    try:
        _remove_desktop_file()
        _remove_icons()
        _reset_integration_flags()

        if purge_data:
            _purge_user_data()

        get_integrated_appimage_path().unlink(missing_ok=True)

    except OSError as exc:
        log.warning("AppImage-afinstallation fejlede: %s", exc)
        return UninstallResult(success=False, error=str(exc))

    log.debug("AppImage afinstalleret (purge_data=%s)", purge_data)
    return UninstallResult(success=True)


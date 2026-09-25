"""
src/opensak/updater.py — Version check mod GitHub Releases API.

Tjekker i baggrunden om der er en ny version af OpenSAK tilgængelig.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from urllib.error import URLError

from PySide6.QtCore import QThread, Signal

from opensak.logger import get_logger
from opensak.net import SSL_CONTEXT, build_ssl_context

log = get_logger("updater")


# Issue #901: den certifi-baserede SSL-kontekst bor nu i opensak.net, så alle
# HTTPS-kald deler den. De gamle navne bevares som aliaser.
_build_ssl_context = build_ssl_context
_SSL_CONTEXT = SSL_CONTEXT

GITHUB_API_URL          = "https://api.github.com/repos/OpenSAK-Org/opensak/releases/latest"
GITHUB_API_ALL_URL      = "https://api.github.com/repos/OpenSAK-Org/opensak/releases"
RELEASES_PAGE   = "https://github.com/OpenSAK-Org/opensak/releases/latest"
REQUEST_TIMEOUT = 10  # sekunder
MAX_RELEASES_TO_SCAN = 20  # antal releases vi henter for at finde nyeste beta


def _is_prerelease_tag(tag: str) -> bool:
    """Returner True hvis tag'et har et semver pre-release suffiks (-beta, -alpha, -rc)."""
    cleaned = tag.lstrip("v").strip()
    return "-" in cleaned


def _parse_version(tag: str) -> tuple[int, int, int, int]:
    """
    Konverter en version-tag til en sammenlignelig tuple.

    Understøtter semver pre-release suffikser (-beta.N, -alpha.N, -rc.N):
      'v1.14.0'         → (1, 14, 0, 9999)   # stabil — højeste 4. element
      'v1.14.0-beta.1'  → (1, 14, 0, 1)      # beta.1 < beta.2 < ... < stabil
      'v1.14.0-beta.2'  → (1, 14, 0, 2)
      '1.11.4'          → (1, 11, 4, 9999)
      'garbage'         → (0, 0, 0, 0)       # sentinel for ikke-parsbare tags

    Dette sikrer at en stabil release altid sammenlignes som nyere end en
    pre-release af samme grundnummer, og at pre-release-numre (beta.1 vs
    beta.2) sammenlignes korrekt i stedet for at falde tilbage til (0,)
    og dermed altid blive opfattet som ældre end alt andet.
    """
    cleaned = tag.lstrip("v").strip()
    base_part, _, pre_part = cleaned.partition("-")

    try:
        base = tuple(int(x) for x in base_part.split("."))
        if len(base) != 3:
            return (0, 0, 0, 0)
    except ValueError:
        return (0, 0, 0, 0)

    if not pre_part:
        # Stabil release — altid "nyere" end en pre-release af samme grundnummer.
        pre_number = 9999
    else:
        # Forventet format: "beta.1", "alpha.2", "rc.3" osv.
        _, _, num_str = pre_part.partition(".")
        try:
            pre_number = int(num_str)
        except ValueError:
            pre_number = 0

    return (base[0], base[1], base[2], pre_number)


def fetch_latest_release() -> dict | None:
    """
    Hent seneste STABILE release fra GitHub API.

    GitHub's /releases/latest endpoint ignorerer automatisk alle
    pre-releases (beta/alpha/rc) — det er en sikker standardopførsel,
    så stabile (main) brugere aldrig utilsigtet bliver tilbudt en beta.

    Returnerer dict med keys 'tag_name', 'html_url', 'name' eller None ved fejl.
    """
    log.debug("Henter seneste release fra %s", GITHUB_API_URL)
    try:
        req = urllib.request.Request(
            GITHUB_API_URL,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "OpenSAK-version-check"},
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=_SSL_CONTEXT) as resp:
            data = json.load(resp)
        release = {
            "tag_name": data.get("tag_name", ""),
            "html_url": data.get("html_url", RELEASES_PAGE),
            "name":     data.get("name", ""),
        }
        log.debug("Seneste release: %s", release["tag_name"])
        return release
    except (URLError, OSError, json.JSONDecodeError, KeyError) as exc:
        log.debug("Kunne ikke hente seneste release: %s", exc)
        return None


def fetch_latest_prerelease() -> dict | None:
    """
    Hent seneste PRE-RELEASE (beta/alpha/rc) fra GitHub API.

    Kun relevant for brugere der allerede kører en beta — main-brugere
    rammer aldrig denne funktion. Henter listen over alle releases og
    sammenligner ALLE markeret som pre-release med _parse_version(), så den
    rigtige "højeste" version vælges uanset rækkefølgen GitHub returnerer
    dem i.

    GitHub's /releases liste-endpoint sorterer efter commit-datoen på det
    commit tagget peger på — IKKE efter hvornår release'en faktisk blev
    oprettet/publiceret. Det betyder den nyeste beta ikke er garanteret at
    stå først i listen (oplevet i praksis: beta.9 stod før beta.10). At
    bare tage data[0] med prerelease=True ville derfor kunne tilbyde en
    ældre beta som "nyeste".

    Returnerer dict med keys 'tag_name', 'html_url', 'name' eller None ved
    fejl eller hvis ingen pre-release findes blandt de seneste releases.
    """
    log.debug("Henter alle releases fra %s for at finde seneste beta", GITHUB_API_ALL_URL)
    try:
        req = urllib.request.Request(
            f"{GITHUB_API_ALL_URL}?per_page={MAX_RELEASES_TO_SCAN}",
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "OpenSAK-version-check"},
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=_SSL_CONTEXT) as resp:
            data = json.load(resp)
        if not isinstance(data, list):
            return None

        best_release: dict | None = None
        best_version = (0, 0, 0, 0)
        for entry in data:
            if not entry.get("prerelease"):
                continue
            tag = entry.get("tag_name", "")
            version = _parse_version(tag)
            if best_release is None or version > best_version:
                best_version = version
                best_release = {
                    "tag_name": tag,
                    "html_url": entry.get("html_url", RELEASES_PAGE),
                    "name":     entry.get("name", ""),
                }

        if best_release:
            log.debug("Seneste beta-release: %s", best_release["tag_name"])
        else:
            log.debug("Ingen pre-release fundet blandt de seneste %d releases", MAX_RELEASES_TO_SCAN)
        return best_release
    except (URLError, OSError, json.JSONDecodeError, KeyError) as exc:
        log.debug("Kunne ikke hente beta-releases: %s", exc)
        return None


class UpdateCheckWorker(QThread):
    """
    Baggrundsthread der tjekker for nye versioner.

    Hvis den nuværende version selv er en pre-release (beta/alpha/rc),
    ELLER hvis brugeren har slået "Notify me about beta releases" til i
    Settings (kun relevant for main-brugere, se `include_prereleases`),
    tjekkes der BÅDE mod listen af alle releases (for en nyere beta) OG
    mod den seneste stabile release — og den objektivt højeste af de to
    tilbydes. En almindelig main-bruger uden dette slået til rammer aldrig
    denne sti og ser kun stabile opdateringer, som hidtil.

    Signals:
        update_available(latest_tag, release_url, is_prerelease):
            Ny version fundet — nyere end den installerede.
            is_prerelease er True hvis den fundne version selv er en beta.
        check_done():
            Tjekket er færdigt (uanset resultat).
    """

    update_available = Signal(str, str, bool)   # (tag, url, is_prerelease)
    check_done       = Signal()

    def __init__(self, current_version: str, parent=None, *, include_prereleases: bool = False):
        super().__init__(parent)
        self._current = current_version
        self._include_prereleases = include_prereleases

    def run(self) -> None:
        log.debug("Starter version-tjek (nuværende: %s)", self._current)
        try:
            running_prerelease = _is_prerelease_tag(self._current)
            if running_prerelease or self._include_prereleases:
                # Tjek BÅDE for en nyere beta OG for en nyere stabil release
                # og tilbyd hvad end der objektivt er den højeste version.
                # Uden det tidligere kun kunne opdage en nyere beta —
                # aldrig at der var kommet en stabil release, selvom den jo
                # per definition er nyere end enhver beta af samme eller
                # ældre grundnummer. En beta-tester ville derfor aldrig få
                # den venlige "der er kommet en stabil version"-besked.
                #
                # include_prereleases udvider dette til også at gælde
                # main-brugere der eksplicit har bedt om at høre om betas
                # (Settings → "Notify me about beta releases").
                log.debug("Tjekker for nyere beta og nyere stabil release")
                candidates = [
                    r for r in (fetch_latest_release(), fetch_latest_prerelease())
                    if r is not None
                ]
                release = max(
                    candidates, key=lambda r: _parse_version(r["tag_name"]), default=None
                ) if candidates else None
            else:
                release = fetch_latest_release()

            if release:
                latest_tag = release["tag_name"]
                if _parse_version(latest_tag) > _parse_version(self._current):
                    is_pre = _is_prerelease_tag(latest_tag)
                    log.debug("Ny version fundet: %s > %s (pre-release: %s)",
                              latest_tag, self._current, is_pre)
                    self.update_available.emit(latest_tag, release["html_url"], is_pre)
                else:
                    log.debug("Ingen ny version (%s <= %s)", latest_tag, self._current)
        finally:
            self.check_done.emit()


# ── AppImage selv-opdatering (issue #836, Step B i epic #824) ───────────────
#
# Bevidst adskilt fra UpdateCheckWorker/fetch_latest_*() ovenfor: signaturen
# på update_available-signalet (tag, url, is_prerelease) rører vi ikke, for
# ikke at risikere regressioner i den eksisterende, grundigt testede
# tjek-logik. I stedet slås asset-URL'en op i en helt separat, dedikeret
# baggrundstråd — kun kaldt når brugeren rent faktisk klikker "Opgrader" i
# mainwindow.py's opdateringsdialog, og kun for AppImage-integrerede
# brugere (appimage.is_running_as_appimage() + is_appimage_integrated()).
#
# Ingen zsync/AppImageUpdate-afhængighed (§4.2 i designdokumentet) — fuld
# download hver gang. Ingen download-progress (kun ubestemt spinner, §7
# punkt 4) og ingen execv-genstart (§7 punkt 5) — bevidste, simple valg for
# v1, se designdokumentets §7.

GITHUB_API_RELEASE_BY_TAG_URL = "https://api.github.com/repos/OpenSAK-Org/opensak/releases/tags/{tag}"

# AppImage-runtiden sætter selv $APPIMAGE, men den bekræfter aldrig et
# tilhørende innehold — vi validerer derfor den downloadede fils
# ELF-magic-bytes før vi lader den erstatte en virkende installation (samme
# forsigtighedsprincip som #828 bruger til SQLite-filer).
_ELF_MAGIC = b"\x7fELF"


def fetch_release_by_tag(tag: str) -> dict | None:
    """
    Hent én specifik release (inkl. dens asset-liste) fra GitHub API ved tag.

    Et separat, letvægts API-kald — bruges kun når selv-opdatering rent
    faktisk startes, så det almindelige version-tjek (fetch_latest_release/
    fetch_latest_prerelease ovenfor) ikke skal bære asset-data rundt for
    brugere der aldrig får brug for det.
    """
    url = GITHUB_API_RELEASE_BY_TAG_URL.format(tag=tag)
    log.debug("Henter release-detaljer for tag %s", tag)
    try:
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "OpenSAK-version-check"},
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=_SSL_CONTEXT) as resp:
            data = json.load(resp)
        return {
            "tag_name": data.get("tag_name", tag),
            "assets": data.get("assets", []) or [],
        }
    except (URLError, OSError, json.JSONDecodeError, KeyError) as exc:
        log.debug("Kunne ikke hente release-detaljer for %s: %s", tag, exc)
        return None


def find_linux_appimage_asset_url(tag: str) -> str | None:
    """
    Find download-URL'en for Linux AppImage-asset'et i en given release.

    Matcher navnemønsteret build.yml rent faktisk bruger:
    'OpenSAK-<tag>-Linux-x86_64.AppImage' (verificeret mod .github/workflows/
    build.yml). Returnerer None hvis release'en ikke findes, eller hvis den
    (endnu) ikke har et Linux-asset — fx en release der stadig bygger, eller
    hvor Linux-buildet fejlede (build.yml's tar.gz-fallback ved AppImage-
    fejl, se §3 i designdokumentet).
    """
    release = fetch_release_by_tag(tag)
    if release is None:
        return None
    expected_name = f"OpenSAK-{tag}-Linux-x86_64.AppImage"
    for asset in release["assets"]:
        if asset.get("name") == expected_name:
            return asset.get("browser_download_url")
    log.debug("Intet asset ved navn %s fundet i release %s", expected_name, tag)
    return None


# ── Windows/macOS selv-download (issue #572) ────────────────────────────────
#
# Til forskel fra Linux (AppImageUpdateWorker ovenfor, som atomisk kan
# erstatte en kørende fils inode) understøtter hverken Windows eller macOS
# at erstatte et kørende program på samme måde — Windows låser en kørende
# .exe, og macOS-installation er drag-to-Applications, ikke en enkelt fil.
# Denne sektion downloader i stedet det korrekte platform-specifikke asset,
# verificerer dets SHA256-checksum, og "åbner" det for brugeren (samme
# sidste skridt som ved et manuelt download) — se SelfUpdateWorker's
# docstring. På Windows er det stadig sådan.
#
# Issue #893: på macOS installerer vi nu selv — at udskifte en kørende
# .app-bundle på disken er sikkert (det er præcis hvad Sparkle gør); #572's
# forsigtighed byggede på en Windows-analogi der ikke holder her. Se
# install_macos_update() nedenfor. Kun hvis det fejler (fx en skrivebeskyttet
# /Applications på en administreret Mac) falder vi tilbage til det manuelle
# flow — nu med DMG'en lagt i ~/Downloads, hvor brugeren kan finde den.

def find_windows_asset_url(tag: str) -> str | None:
    """
    Find download-URL'en for Windows-asset'et i en given release.

    Matcher navnemønsteret build.yml rent faktisk bruger:
    'OpenSAK-<tag>-Windows.zip' (verificeret mod
    .github/workflows/build.yml).
    """
    release = fetch_release_by_tag(tag)
    if release is None:
        return None
    expected_name = windows_asset_name(tag)
    for asset in release["assets"]:
        if asset.get("name") == expected_name:
            return asset.get("browser_download_url")
    log.debug("Intet asset ved navn %s fundet i release %s", expected_name, tag)
    return None


def windows_asset_name(tag: str) -> str:
    """Filnavnet build.yml giver Windows-asset'et for et givet tag."""
    return f"OpenSAK-{tag}-Windows.zip"


def macos_arch_suffix() -> str:
    """
    Returner 'arm64' eller 'x86_64' ud fra platform.machine().

    build.yml bygger separate .dmg-filer for Apple Silicon og Intel — dette
    afgør hvilken af de to den kørende Mac faktisk skal bruge.
    """
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return "x86_64"


def macos_asset_name(tag: str) -> str:
    """Filnavnet build.yml giver macOS-asset'et for et givet tag, arkitektur-korrekt."""
    return f"OpenSAK-{tag}-macOS-{macos_arch_suffix()}.dmg"


def find_macos_asset_url(tag: str) -> str | None:
    """
    Find download-URL'en for det arkitektur-korrekte macOS .dmg-asset i en
    given release — matcher build.yml's
    'OpenSAK-<tag>-macOS-<arm64|x86_64>.dmg'-navnemønster.
    """
    release = fetch_release_by_tag(tag)
    if release is None:
        return None
    expected_name = macos_asset_name(tag)
    for asset in release["assets"]:
        if asset.get("name") == expected_name:
            return asset.get("browser_download_url")
    log.debug("Intet asset ved navn %s fundet i release %s", expected_name, tag)
    return None


SHA256SUMS_ASSET_NAME = "SHA256SUMS.txt"


def fetch_checksums(tag: str) -> dict[str, str] | None:
    """
    Hent og parse SHA256SUMS.txt fra en release.

    Filen forventes i standard `sha256sum`-format: "<hex-digest>  <filnavn>"
    pr. linje (én linje pr. asset — genereret af create-release-jobbet i
    build.yml). Returnerer None hvis release'en ikke findes, eller den
    (endnu) ikke har en SHA256SUMS.txt — fx en ældre release fra før dette
    blev tilføjet.
    """
    release = fetch_release_by_tag(tag)
    if release is None:
        return None

    checksums_url = None
    for asset in release["assets"]:
        if asset.get("name") == SHA256SUMS_ASSET_NAME:
            checksums_url = asset.get("browser_download_url")
            break
    if checksums_url is None:
        log.debug("Ingen %s fundet i release %s", SHA256SUMS_ASSET_NAME, tag)
        return None

    try:
        req = urllib.request.Request(
            checksums_url, headers={"User-Agent": "OpenSAK-self-update"}
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=_SSL_CONTEXT) as resp:
            text = resp.read().decode("utf-8")
    except (URLError, OSError, UnicodeDecodeError) as exc:
        log.debug("Kunne ikke hente/læse %s for %s: %s", SHA256SUMS_ASSET_NAME, tag, exc)
        return None

    checksums: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, filename = parts
        # sha256sum-formatet kan prefixe filnavnet med '*' (binær-mode)
        filename = filename.lstrip("*").strip()
        checksums[filename] = digest.lower()
    return checksums


def _sha256_of_file(path: Path) -> str:
    """SHA256-hex-digest af en fil, læst i chunks (ikke hele filen i RAM)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ── macOS-installation (issue #893) ──────────────────────────────────────────

MACOS_APP_NAME = "OpenSAK.app"
_MACOS_DEFAULT_TARGET = Path("/Applications") / MACOS_APP_NAME


class MacInstallError(Exception):
    """Automatisk macOS-installation kunne ikke gennemføres (issue #893)."""


def _running_app_bundle() -> Path | None:
    """
    Stien til den .app-bundle den kørende OpenSAK ligger i, eller None når
    vi ikke kører som en frosset (PyInstaller) app — fx fra kildekoden.
    """
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    for parent in exe.parents:
        if parent.suffix == ".app":
            return parent
    return None


def _is_transient_location(bundle: Path) -> bool:
    """
    True når bundlen ikke ligger et sted, man kan installere oven i: direkte
    fra en monteret DMG (/Volumes/...) eller en Gatekeeper "App Translocation"-
    kopi (en skrivebeskyttet, tilfældig sti macOS bruger for apps startet fra
    en download-placering).
    """
    text = bundle.as_posix()
    return text.startswith("/Volumes/") or "/AppTranslocation/" in text


def macos_install_target() -> Path:
    """
    Hvor opdateringen skal installeres: der hvor den kørende app ligger, så
    en bruger der har OpenSAK i fx ~/Applications ikke får en ekstra kopi i
    /Applications. /Applications/OpenSAK.app bruges når placeringen ikke kan
    afgøres eller er midlertidig (se _is_transient_location).
    """
    bundle = _running_app_bundle()
    if bundle is not None and not _is_transient_location(bundle):
        return bundle
    return _MACOS_DEFAULT_TARGET


def _hdiutil_attach(dmg: Path) -> Path:
    """
    Montér DMG'en usynligt (-nobrowse: intet Finder-vindue, ingen ikon på
    skrivebordet) og returnér mountpunktet.

    hdiutil vælger selv et ledigt mountpunkt ("/Volumes/OpenSAK 1" osv.), så
    en efterladt montering fra et tidligere mislykket forsøg giver ingen
    navnekonflikt — vi læser bare det faktiske punkt ud af -plist-outputtet.
    """
    result = subprocess.run(
        ["hdiutil", "attach", "-nobrowse", "-noautoopen", "-plist", str(dmg)],
        capture_output=True, check=True, timeout=120,
    )
    out = result.stdout
    # hdiutil kan skrive tekst før selve plist'en — start ved XML-headeren.
    start = out.find(b"<?xml")
    try:
        data = plistlib.loads(out[start:] if start >= 0 else out)
    except Exception as exc:  # InvalidFileException, ValueError, ExpatError, ...
        raise MacInstallError(f"could not read hdiutil output: {exc}") from exc
    if not isinstance(data, dict):
        raise MacInstallError("unexpected hdiutil output")
    for entity in data.get("system-entities", []):
        mount_point = entity.get("mount-point")
        if mount_point:
            return Path(mount_point)
    raise MacInstallError("hdiutil reported no mount point")


def _hdiutil_detach(mount_point: Path) -> None:
    """Afmontér — med -force som fallback. Kaster aldrig: kaldes fra finally."""
    for args in (["hdiutil", "detach", str(mount_point)],
                 ["hdiutil", "detach", "-force", str(mount_point)]):
        try:
            subprocess.run(args, capture_output=True, check=True, timeout=60)
            return
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.debug("hdiutil detach fejlede (%s): %s", args, exc)
    log.warning("Kunne ikke afmontere %s", mount_point)


def _find_app_in_volume(mount_point: Path) -> Path:
    """Find .app-bundlen på den monterede DMG (ikke Applications-genvejen)."""
    preferred = mount_point / MACOS_APP_NAME
    if preferred.is_dir():
        return preferred
    candidates = sorted(
        p for p in mount_point.glob("*.app") if p.is_dir() and not p.is_symlink()
    )
    if candidates:
        return candidates[0]
    raise MacInstallError(f"no .app bundle found in {mount_point}")


def _replace_app_bundle(source_app: Path, target: Path) -> None:
    """
    Kopiér *source_app* ind som *target* uden nogensinde at efterlade en
    halvkopieret app:

    1. ditto til en skjult '.<navn>.new' ved siden af målet — ditto er
       macOS' anbefalede værktøj til .app-bundles (bevarer symlinks,
       udvidede attributter og kodesignaturen); shutil.copytree er ikke
       pålidelig nok til det.
    2. Den gamle bundle omdøbes til '.<navn>.old', den nye til målnavnet
       (samme mappe, så begge omdøbninger er atomiske).
    3. Den gamle slettes først til sidst. Fejler omdøbningen, rulles tilbage.

    Kaster PermissionError før noget kopieres, hvis mappen ikke er skrivbar.
    """
    parent = target.parent
    if not os.access(parent, os.W_OK) or (target.exists() and not os.access(target, os.W_OK)):
        raise PermissionError(f"{parent} is not writable")

    staged = parent / f".{target.name}.new"
    old = parent / f".{target.name}.old"
    for leftover in (staged, old):
        shutil.rmtree(leftover, ignore_errors=True)

    try:
        subprocess.run(
            ["ditto", str(source_app), str(staged)],
            capture_output=True, check=True, timeout=600,
        )
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise

    had_old = target.exists()
    if had_old:
        os.rename(target, old)
    try:
        os.rename(staged, target)
    except OSError:
        if had_old:
            os.rename(old, target)
        shutil.rmtree(staged, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)


def install_macos_update(dmg: Path, target: Path) -> Path:
    """
    Installér .app'en fra *dmg* som *target* (issue #893): montér usynligt,
    kopiér sikkert, afmontér altid igen. Returnerer den installerede sti.

    Kaster MacInstallError, OSError (inkl. PermissionError),
    subprocess.CalledProcessError eller subprocess.TimeoutExpired ved fejl —
    SelfUpdateWorker falder så tilbage til det manuelle flow.
    """
    mount_point = _hdiutil_attach(dmg)
    try:
        source_app = _find_app_in_volume(mount_point)
        _replace_app_bundle(source_app, target)
    finally:
        _hdiutil_detach(mount_point)
    return target


def _macos_downloads_dir() -> Path:
    """Brugerens Downloads-mappe (egen funktion så tests kan omdirigere den)."""
    return Path.home() / "Downloads"


def _move_to_downloads(path: Path) -> Path:
    """
    Flyt den downloadede fil til ~/Downloads, med ' (1)', ' (2)' ... ved
    navnekonflikt, og returnér den nye sti — så brugeren kan finde (og selv
    slette) den, i stedet for at den forsvinder i /private/var/folders.
    """
    downloads = _macos_downloads_dir()
    downloads.mkdir(parents=True, exist_ok=True)
    dest = downloads / path.name
    n = 1
    while dest.exists():
        dest = downloads / f"{path.stem} ({n}){path.suffix}"
        n += 1
    shutil.move(str(path), str(dest))
    return dest


class SelfUpdateWorker(QThread):
    """
    Baggrundsthread der downloader og verificerer den korrekte
    platform-specifikke Windows/macOS-asset for en given release (issue
    #572), og derefter:

    - Windows: "åbner" den for brugeren i Explorer (den kørende .exe er
      låst og kan ikke erstattes).
    - macOS (issue #893): installerer den selv via install_macos_update()
      og emitter `installed`. Fejler det, flyttes DMG'en til ~/Downloads,
      åbnes for brugeren, og `finished_ok` emittes med den nye sti.

    Signals:
        progress(downloaded, total):  Fremskridt under download, i bytes.
                                       `total` er 0 hvis serveren ikke
                                       sender Content-Length.
        installed(app_path):          macOS: ny version installeret —
                                       OpenSAK skal genstartes.
        finished_ok(opened_path):     Download + åbning gennemført (Windows,
                                       eller macOS-fallback til manuel
                                       installation).
        finished_error(error_code):   "unsupported_platform" | "asset_not_found" |
                                       "checksum_unavailable" | "checksum_mismatch",
                                       eller en rå OSError/URLError-strengbesked
                                       for netværks-/filsystemfejl.
    """

    progress       = Signal(int, int)   # (downloaded_bytes, total_bytes)
    installed      = Signal(str)
    finished_ok    = Signal(str)
    finished_error = Signal(str)

    def __init__(self, tag: str, parent=None):
        super().__init__(parent)
        self._tag = tag

    def run(self) -> None:
        # mypy antager (korrekt, når selve mypy-kørslen sker på Linux) at
        # `sys.platform == "win32"`/`"darwin"` er statisk uopnåelige grene
        # her, og udelader dem derfor fra sin definitiv-tildelings-analyse
        # — prædeklarér variablerne så "Name not defined" ikke opstår.
        download_url: str | None
        asset_name: str
        if sys.platform == "win32":
            download_url = find_windows_asset_url(self._tag)
            asset_name = windows_asset_name(self._tag)
        elif sys.platform == "darwin":
            download_url = find_macos_asset_url(self._tag)
            asset_name = macos_asset_name(self._tag)
        else:
            self.finished_error.emit("unsupported_platform")
            return

        if download_url is None:
            self.finished_error.emit("asset_not_found")
            return

        checksums = fetch_checksums(self._tag)
        if not checksums or asset_name not in checksums:
            self.finished_error.emit("checksum_unavailable")
            return
        expected_digest = checksums[asset_name]

        tmp_dir = Path(tempfile.mkdtemp(prefix="opensak-update-"))
        downloaded_path = tmp_dir / asset_name
        try:
            log.debug("Downloader selv-opdatering fra %s", download_url)
            req = urllib.request.Request(
                download_url, headers={"User-Agent": "OpenSAK-self-update"}
            )
            with urllib.request.urlopen(req, timeout=120, context=_SSL_CONTEXT) as resp:
                total = int(resp.headers.get("Content-Length", 0) or 0)
                downloaded = 0
                with open(downloaded_path, "wb") as out:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        out.write(chunk)
                        downloaded += len(chunk)
                        self.progress.emit(downloaded, total)

            actual_digest = _sha256_of_file(downloaded_path)
            if actual_digest.lower() != expected_digest.lower():
                log.warning(
                    "Checksum-mismatch for %s: forventede %s, fik %s",
                    asset_name, expected_digest, actual_digest,
                )
                self.finished_error.emit("checksum_mismatch")
                return

            if sys.platform == "darwin":
                self._finish_macos(downloaded_path, tmp_dir)
                return

            self._reveal(downloaded_path)
            log.debug("Selv-opdatering downloadet og åbnet: %s", downloaded_path)
            self.finished_ok.emit(str(downloaded_path))

        except (URLError, OSError) as exc:
            log.warning("Selv-opdatering fejlede: %s", exc)
            self.finished_error.emit(str(exc))

    def _finish_macos(self, dmg: Path, tmp_dir: Path) -> None:
        """
        Issue #893: installér DMG'ens .app automatisk. Ved succes slettes
        download-mappen (og dermed DMG'en); ved fejl flyttes DMG'en til
        ~/Downloads og åbnes, så brugeren kan fuldføre manuelt og selv
        finde filen bagefter.
        """
        target = macos_install_target()
        try:
            installed_at = install_macos_update(dmg, target)
        except (MacInstallError, OSError,
                subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.warning("Automatisk macOS-installation til %s fejlede: %s", target, exc)
            saved = _move_to_downloads(dmg)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self._reveal(saved)
            self.finished_ok.emit(str(saved))
            return
        shutil.rmtree(tmp_dir, ignore_errors=True)
        log.debug("Selv-opdatering installeret: %s", installed_at)
        self.installed.emit(str(installed_at))

    def _reveal(self, path: Path) -> None:
        """
        "Åbn" den downloadede fil for brugeren.

        Splittet ud som egen metode udelukkende for testbarhed — tests
        monkeypatcher denne i stedet for rent faktisk at åbne et
        Explorer/Finder-vindue.
        """
        import subprocess
        if sys.platform == "win32":
            # /select fremhæver filen i en åben Explorer-mappe, i stedet
            # for at forsøge at åbne/udpakke .zip'en direkte.
            subprocess.run(["explorer", f"/select,{path}"])
        elif sys.platform == "darwin":
            # "open" på en .dmg monterer den og åbner et Finder-vindue —
            # samme resultat som et manuelt dobbeltklik.
            subprocess.run(["open", str(path)])


class AppImageUpdateWorker(QThread):
    """
    Baggrundsthread der downloader en ny AppImage-version og udskifter den
    integrerede kopi atomisk.

    Flow: find asset-URL for tag'et → download til en midlertidig fil i
    SAMME mappe som målet (så os.replace() garanteret er atomisk — samme
    filsystem) → valider ELF-magic-bytes → sæt eksekverbar-bit → os.replace()
    ind over den integrerede kopi. Linux tillader at overskrive en kørende
    fils inode, så den kørende proces fortsætter uberørt på den gamle
    version indtil den lukkes (se §4.2 i designdokumentet).

    Signals:
        finished_ok(installed_path):    Opdatering gennemført.
        finished_error(error_message):  Opdatering fejlede (netværk, intet
                                         Linux-asset i release'en, korrupt
                                         download, eller filsystemfejl).
    """

    finished_ok    = Signal(str)
    finished_error = Signal(str)

    def __init__(self, tag: str, parent=None):
        super().__init__(parent)
        self._tag = tag

    def run(self) -> None:
        from opensak import appimage

        target = appimage.get_integrated_appimage_path()
        tmp_path: Path | None = None
        try:
            download_url = find_linux_appimage_asset_url(self._tag)
            if download_url is None:
                self.finished_error.emit("asset_not_found")
                return

            fd, tmp_name = tempfile.mkstemp(
                dir=str(target.parent), prefix=".opensak-update-", suffix=".AppImage.part"
            )
            os.close(fd)
            tmp_path = Path(tmp_name)

            log.debug("Downloader AppImage-opdatering fra %s", download_url)
            req = urllib.request.Request(
                download_url, headers={"User-Agent": "OpenSAK-self-update"}
            )
            with urllib.request.urlopen(req, timeout=120, context=_SSL_CONTEXT) as resp, \
                    open(tmp_path, "wb") as out:
                shutil.copyfileobj(resp, out)

            with open(tmp_path, "rb") as f:
                magic = f.read(4)
            if magic != _ELF_MAGIC:
                log.warning("Downloadet fil har ikke ELF-magic-bytes — forkastes")
                self.finished_error.emit("invalid_download")
                return

            mode = tmp_path.stat().st_mode
            os.chmod(tmp_path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            os.replace(tmp_path, target)
            tmp_path = None  # allerede flyttet — skal ikke ryddes op i finally
            log.debug("AppImage opdateret til %s", target)
            self.finished_ok.emit(str(target))

        except (URLError, OSError) as exc:
            log.warning("AppImage-selvopdatering fejlede: %s", exc)
            self.finished_error.emit(str(exc))
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass


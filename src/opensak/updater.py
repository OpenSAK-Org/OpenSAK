"""
src/opensak/updater.py — Version check mod GitHub Releases API.

Tjekker i baggrunden om der er en ny version af OpenSAK tilgængelig.
"""

from __future__ import annotations

import json
import os
import shutil
import ssl
import stat
import tempfile
import urllib.request
from pathlib import Path
from urllib.error import URLError

from PySide6.QtCore import QThread, Signal

from opensak.logger import get_logger

log = get_logger("updater")


def _build_ssl_context() -> ssl.SSLContext:
    """
    Byg en SSL-kontekst der eksplicit bruger certifi's certifikat-bundt.

    Uden dette kan HTTPS-kald fejle med CERTIFICATE_VERIFY_FAILED i en
    PyInstaller-bundlet .exe på Windows, fordi Python's standard SSL-
    verifikation falder tilbage til systemets certifikat-store, som ikke
    altid er korrekt tilgængelig i en bundlet kontekst. certifi's
    cacert.pem bundles eksplicit med .spec-filen og bruges her i stedet
    for at stole på systemets opslag.

    Falder tilbage til Python's standard SSL-kontekst hvis certifi af en
    eller anden grund ikke er tilgængeligt — bedre at forsøge med
    systemets certifikater end at crashe helt.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        log.debug("certifi ikke tilgængeligt — falder tilbage til systemets SSL-kontekst")
        return ssl.create_default_context()


_SSL_CONTEXT = _build_ssl_context()

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


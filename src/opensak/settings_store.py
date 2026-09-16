"""
src/opensak/settings_store.py — JSON-baseret settings-motor til OpenSAK.

Arkitektur (issue #209):
  bootstrap.json  — platform-standard sti, peger på installations-mappen
  opensak.json    — i installations-mappen, indeholder alle settings

Bootstrap-stier:
  Linux:   ~/.config/opensak/bootstrap.json
  Windows: %APPDATA%\\opensak\\bootstrap.json
  macOS:   ~/Library/Application Support/opensak/bootstrap.json

Al state gemmes i én JSON-fil: <install_dir>/opensak.json
Filen skrives atomisk (temp-fil + rename) så den aldrig korrupteres.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Issue #870: diagnostik for macOS-migreringen (#825/#867). Tavs medmindre
# "settings_migration"-flaget er aktivt i debug_flags.py.
from opensak.logger import get_logger

_log = get_logger("settings_migration")


# ── Bootstrap-sti ─────────────────────────────────────────────────────────────

def _bootstrap_path() -> Path:
    """
    Returner platform-korrekt sti til bootstrap.json.

    Issue #825: `os.name` er `"posix"` på BÅDE Linux og macOS, så en
    macOS-specifik gren skal tjekkes FØR den generelle posix-gren via
    `sys.platform == "darwin"` — ellers rammer macOS fejlagtigt
    Linux-stien (~/.config), som den gjorde før denne fix.
    """
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "posix":
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
    else:
        base = Path.home() / ".config"
    return base / "opensak" / "bootstrap.json"


def _default_install_dir() -> Path:
    """
    Standard installations-mappe — bruges hvis bootstrap ikke findes.

    Issue #825: se `_bootstrap_path()` — samme `sys.platform == "darwin"`
    tjek er nødvendigt her af samme årsag.

    Issue #820: på Windows, hvis processen kører MSIX-pakket (Desktop
    Bridge), virtualiserer Windows fejlagtigt-usynligt skrivninger til
    %AppData% til en per-pakke-mappe, som brugeren ikke kan finde via
    Explorer. Documents-mappen bliver IKKE virtualiseret på samme måde,
    så nye MSIX-installationer får den som standard i stedet. Berører kun
    NYE installationer — eksisterende brugeres allerede-satte install_dir
    (i bootstrap.json) ændres ikke af dette; se database_dialog.py for
    visning af den faktiske fysiske sti for eksisterende data.
    """
    if os.name == "nt":
        from opensak.msix import is_msix_packaged
        if is_msix_packaged():
            return Path.home() / "Documents" / "opensak"
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "posix":
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    else:
        base = Path.home()
    return base / "opensak"


# ── Legacy macOS-stier (issue #825) ─────────────────────────────────────────
#
# Før denne fix brugte macOS fejlagtigt den generelle posix-gren, dvs. de
# samme stier som Linux. Disse to funktioner genskaber PRÆCIS den gamle
# (forkerte) logik, udelukkende til brug i migrate_macos_default_paths()
# nedenfor, så eksisterende macOS-brugeres data kan findes og flyttes.
# Skal IKKE bruges andre steder — al ny kode skal bruge _bootstrap_path()/
# _default_install_dir() ovenfor.

def _legacy_macos_bootstrap_path() -> Path:
    """Den (forkerte) sti macOS brugte til bootstrap.json før issue #825."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "opensak" / "bootstrap.json"


def _legacy_macos_default_install_dir() -> Path:
    """Den (forkerte) standard-installationsmappe macOS brugte før issue #825."""
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "opensak"


# ── Bootstrap læs/skriv ───────────────────────────────────────────────────────

def get_install_dir() -> Path:
    """
    Returner installations-mappen.

    Læser bootstrap.json hvis den eksisterer, ellers bruges standard-stien.
    Mappen oprettes automatisk.
    """
    bp = _bootstrap_path()
    if bp.exists():
        try:
            data = json.loads(bp.read_text(encoding="utf-8"))
            p = Path(data["install_dir"])
            p.mkdir(parents=True, exist_ok=True)
            return p
        except (KeyError, json.JSONDecodeError, OSError):
            pass
    # Fallback til standard
    d = _default_install_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def set_install_dir(path: Path) -> None:
    """
    Gem installations-mappen i bootstrap.json.

    Bruges ved velkomst-wizard (#210) når brugeren vælger mappe.
    """
    bp = _bootstrap_path()
    bp.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if bp.exists():
        try:
            data = json.loads(bp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    data["install_dir"] = str(path)
    _atomic_write(bp, data)


# ── Atomisk JSON-skrivning ────────────────────────────────────────────────────

def _atomic_write(path: Path, data: dict) -> None:
    """Skriv JSON atomisk (temp-fil + rename) — aldrig korruption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, prefix=".opensak_tmp_"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        # Atomisk rename — virker på alle platforme.
        #
        # Issue #574: på Windows kan denne fejle med "WinError 5 Access is
        # denied" hvis opensak.json et kort øjeblik holdes åben af noget
        # udenfor OpenSAK selv — antivirus-realtidsscanning, Windows Search-
        # indeksering, eller roaming-profil-/OneDrive-synkronisering af
        # AppData\Roaming, hvor filen ligger. Set især lige efter en
        # genstart eller en opdatering, hvor sådanne processer typisk griber
        # fat i nye/ændrede filer først. Fejlen er forbigående — appen
        # virkede fint igen ved simpelthen at prøve forfra — så et par
        # korte retry-forsøg her er langt bedre end at lade HELE appen
        # crashe på den allerførste settings-skrivning ved opstart.
        last_err: OSError | None = None
        for attempt in range(5):
            try:
                Path(tmp_path).replace(path)
                return
            except OSError as e:
                last_err = e
                if attempt < 4:
                    time.sleep(0.1 * (attempt + 1))
        assert last_err is not None
        raise last_err
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── Settings Store ────────────────────────────────────────────────────────────

class SettingsStore:
    """
    JSON-baseret settings-motor.

    Al state gemmes i <install_dir>/opensak.json som én flad dict
    med prik-separerede nøgler: "display.theme", "user.gc_username" osv.

    Bruger lazy-load: filen indlæses ved første tilgang, ikke ved import.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] | None = None   # None = ikke indlæst endnu
        self._path: Path | None = None

    def _settings_path(self) -> Path:
        """Returner (og cache) stien til opensak.json."""
        if self._path is None:
            self._path = get_install_dir() / "opensak.json"
        return self._path

    def _load(self) -> None:
        """Indlæs opensak.json — kaldes automatisk ved første tilgang."""
        if self._data is not None:
            return
        p = self._settings_path()
        if p.exists():
            try:
                self._data = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(self._data, dict):
                    self._data = {}
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def get(self, key: str, default: Any = None) -> Any:
        """Hent en settings-værdi. Returnerer `default` hvis nøglen ikke findes."""
        self._load()
        return self._data.get(key, default)  # type: ignore[union-attr]

    def set(self, key: str, value: Any) -> None:
        """Gem en settings-værdi og skriv til disk."""
        self._load()
        self._data[key] = value  # type: ignore[index]
        self._flush()

    def set_many(self, updates: dict[str, Any]) -> None:
        """Gem flere nøgler på én gang (én diskskrivning)."""
        self._load()
        self._data.update(updates)  # type: ignore[union-attr]
        self._flush()

    def delete(self, key: str) -> None:
        """Slet en nøgle."""
        self._load()
        self._data.pop(key, None)  # type: ignore[union-attr]
        self._flush()

    def get_section(self, prefix: str) -> dict[str, Any]:
        """
        Returner alle nøgler der starter med `prefix.` som en dict
        uden prefix-delen.

        Eksempel: get_section("sort") returnerer {"Default.db/field": "name", ...}
        """
        self._load()
        result = {}
        search = prefix + "."
        for k, v in self._data.items():  # type: ignore[union-attr]
            if k.startswith(search):
                result[k[len(search):]] = v
        return result

    def _flush(self) -> None:
        """Skriv data til disk atomisk."""
        import base64

        def _make_serializable(obj):
            """Konvertér ikke-JSON-serialiserbare typer rekursivt."""
            if isinstance(obj, dict):
                return {k: _make_serializable(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_make_serializable(v) for v in obj]
            # QByteArray og andre bytes-lignende typer
            if isinstance(obj, (bytes, bytearray)):
                return base64.b64encode(obj).decode()
            # Native JSON-typer skal IKKE forsøges konverteret til bytes —
            # bytes(True)/bytes(False) fortolker booleans som heltal (0/1
            # antal nul-bytes) og ville fejlagtigt korrumpere dem til base64.
            if obj is None or isinstance(obj, (bool, int, float, str)):
                return obj
            try:
                # Fang QByteArray fra PySide6 som har __bytes__
                b = bytes(obj)
                return base64.b64encode(b).decode()
            except (TypeError, ValueError):
                pass
            return obj

        safe_data = _make_serializable(self._data)
        _atomic_write(self._settings_path(), safe_data)  # type: ignore[arg-type]

    def sync(self) -> None:
        """Eksplicit flush — bruges som drop-in for QSettings.sync()."""
        if self._data is not None:
            self._flush()

    def invalidate_path_cache(self) -> None:
        """
        Nulstil den cachede installations-sti og indlæste data.

        Kaldes hvis install_dir ændres under kørsel (wizard #210).
        """
        self._path = None
        self._data = None


# ── Migration fra QSettings ───────────────────────────────────────────────────

def repair_corrupted_bool_keys(store: SettingsStore) -> None:
    """
    Reparér settings-nøgler der blev korrumperet af en tidligere bug i
    _flush() (booleans blev fejlagtigt base64-kodet til 'AA==' / 'AQ==').

    Idempotent og billig — sikkert at kalde ved hver opstart. Påvirker kun
    de specifikke boolean-nøgler der var ramt af buggen; rører ikke andre
    værdier.

    Vigtigst: "updates.check_enabled" skal være True for nye 1.14.x-brugere
    fra starten, så de aktivt skal vælge at slå auto-update-check fra,
    ikke omvendt.
    """
    base64_to_bool = {"AA==": False, "AQ==": True}
    bool_keys = [
        "updates.check_enabled",
        "display.use_miles",
        "location.nominatim_enabled",
    ]
    fixes: dict[str, Any] = {}
    for key in bool_keys:
        val = store.get(key)
        if isinstance(val, str) and val in base64_to_bool:
            fixes[key] = base64_to_bool[val]
    if fixes:
        store.set_many(fixes)


def migrate_from_qsettings(store: SettingsStore) -> bool:
    """
    Én-gangs migration af data fra QSettings til opensak.json.

    Returnerer True hvis migration blev udført, False hvis ikke nødvendig
    (enten allerede migreret eller ingen QSettings-data).

    Gemmer migrerings-flag i opensak.json så det kun sker én gang.
    """
    if store.get("_migrated_from_qsettings", False):
        return False

    try:
        from PySide6.QtCore import QSettings
        qs = QSettings("OpenSAK Project", "OpenSAK")
        all_keys = qs.allKeys()
        if not all_keys:
            store.set("_migrated_from_qsettings", True)
            return False

        updates: dict[str, Any] = {"_migrated_from_qsettings": True}

        # Mapping fra QSettings nøgler → opensak.json nøgler
        # (kun nøgler vi kender og ønsker at migrere)
        key_map = {
            # Bruger
            "user/gc_username":         "user.gc_username",
            "user/gc_finder_id":        "user.gc_finder_id",
            "user/gc_home_location":    "user.gc_home_location",
            # Display
            "display/theme":            "display.theme",
            "display/use_miles":        "display.use_miles",
            "display/coord_format":     "display.coord_format",
            "display/map_provider":     "display.map_provider",
            # Sprog
            "language":                 "app.language",
            # Søgning
            "search/min_chars":         "search.min_chars",
            "search/debounce_ms":       "search.debounce_ms",
            # Nominatim
            "location/nominatim_enabled": "location.nominatim_enabled",
            # Opdateringer
            "updates/check_enabled":    "updates.check_enabled",
            "updates/skipped_version":  "updates.skipped_version",
            # Stier
            "paths/last_import_dir":    "paths.last_import_dir",
            # Kolonner
            "columns/visible":          "columns.visible",
            "columns/widths":           "columns.widths",
            # Hjemmepunkter
            "homepoints/list":          "homepoints.list",
            "homepoints/active_name":   "homepoints.active_name",
        }

        for qs_key, json_key in key_map.items():
            val = qs.value(qs_key)
            if val is not None:
                updates[json_key] = val

        # Vindues-geometri og -state (binær data som bytes → base64 streng)
        import base64
        for qs_key, json_key in [
            ("window/geometry",               "window.geometry"),
            ("window/state",                  "window.state"),
            ("window/splitter_state",         "window.splitter_state"),
            ("window/bottom_splitter_state",  "window.bottom_splitter_state"),
        ]:
            val = qs.value(qs_key)
            if val is not None:
                if isinstance(val, (bytes, bytearray)):
                    updates[json_key] = base64.b64encode(bytes(val)).decode()
                else:
                    updates[json_key] = val

        # Numeriske window-ratios
        for qs_key, json_key in [
            ("window/splitter_ratio_top",       "window.splitter_ratio_top"),
            ("window/bottom_splitter_ratio_left", "window.bottom_splitter_ratio_left"),
        ]:
            val = qs.value(qs_key)
            if val is not None:
                try:
                    updates[json_key] = float(val)
                except (TypeError, ValueError):
                    pass

        # Per-database nøgler: db_<path>/home_lat osv.
        # og sort/<path>/field osv.
        for qs_key in all_keys:
            if qs_key.startswith("db_") or qs_key.startswith("sort/"):
                val = qs.value(qs_key)
                if val is not None:
                    safe_key = "qs." + qs_key.replace("/", ".")
                    # Konvertér QByteArray og andre binære typer til base64
                    try:
                        if isinstance(val, (bytes, bytearray)) or hasattr(val, "__bytes__"):
                            import base64
                            val = base64.b64encode(bytes(val)).decode()
                        # Tjek at værdien er JSON-serialiserbar
                        import json as _json
                        _json.dumps(val)
                        updates[safe_key] = val
                    except (TypeError, ValueError):
                        pass  # spring ikke-serialiserbare værdier over

        # Databases-array fra QSettings
        count = qs.beginReadArray("databases")
        if count > 0:
            db_list = []
            for i in range(count):
                qs.setArrayIndex(i)
                name = qs.value("name")
                path = qs.value("path")
                if name and path:
                    db_list.append({"name": name, "path": path})
            qs.endArray()
            if db_list:
                updates["databases.list"] = db_list
        else:
            qs.endArray()

        active_db = qs.value("active_database")
        if active_db:
            updates["databases.active"] = active_db

        store.set_many(updates)
        print(f"[settings] Migrerede {len(updates)-1} nøgler fra QSettings → opensak.json")
        return True

    except Exception as e:
        print(f"[settings] Migration fra QSettings fejlede: {e}")
        store.set("_migrated_from_qsettings", True)
        return False


def _rewrite_stale_install_dir_paths(
    json_path: Path, old_prefix: Path, new_prefix: Path
) -> None:
    """
    Ret absolutte sti-strenge i en flyttet opensak.json, der stadig peger
    på den gamle installations-mappe efter migrate_macos_default_paths()
    har flyttet selve filerne.

    Baggrund: migrate_macos_default_paths() flytter kun filerne fysisk —
    den rører ikke opensak.json's eget INDHOLD. Men databases.list[].path,
    databases.active og databases.dir er absolutte sti-strenge gemt i
    netop den fil, og DatabaseManager._migrate_path() genkender kun den
    ældre "/geocacher/"-mappe-omdøbning, ikke denne macOS-migrering. Uden
    denne rettelse leder DatabaseManager derfor efter sin aktive database
    på en sti der ikke længere findes, og opretter stiltiende en frisk,
    tom database der i stedet for at finde brugerens rigtige (og fuldt
    intakte) data ved siden af.

    Best-effort: hvis filen ikke findes eller ikke er gyldig JSON, gøres
    intet — dette må aldrig kunne forhindre selve fil-migreringen i at
    have fuldført korrekt.
    """
    if not json_path.exists():
        _log.debug("_rewrite_stale_install_dir_paths: %s findes ikke — intet at rette",
                    json_path)
        return
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _log.debug("_rewrite_stale_install_dir_paths: kunne ikke læse/parse "
                    "%s (%s) — springer over", json_path, exc)
        return
    if not isinstance(data, dict):
        _log.debug("_rewrite_stale_install_dir_paths: %s indeholder ikke et "
                    "JSON-objekt — springer over", json_path)
        return

    old_str = str(old_prefix)
    new_str = str(new_prefix)
    changed = False
    rewritten: list[str] = []

    def _rewrite(value: Any, field: str) -> Any:
        nonlocal changed
        if isinstance(value, str) and value.startswith(old_str):
            changed = True
            new_value = new_str + value[len(old_str):]
            rewritten.append(f"{field}: {value!r} -> {new_value!r}")
            return new_value
        return value

    db_list = data.get("databases.list")
    if isinstance(db_list, list):
        for i, entry in enumerate(db_list):
            if isinstance(entry, dict) and "path" in entry:
                entry["path"] = _rewrite(entry["path"], f"databases.list[{i}].path")

    if "databases.active" in data:
        data["databases.active"] = _rewrite(data["databases.active"], "databases.active")

    if "databases.dir" in data:
        data["databases.dir"] = _rewrite(data["databases.dir"], "databases.dir")

    if changed:
        _log.debug("_rewrite_stale_install_dir_paths: retter %d felt(er) i %s: %s",
                    len(rewritten), json_path, "; ".join(rewritten))
        try:
            _atomic_write(json_path, data)
        except OSError as exc:
            print(f"[settings] macOS-migration: kunne ikke genskrive "
                  f"stale stier i {json_path}: {exc}")
            _log.debug("_rewrite_stale_install_dir_paths: _atomic_write af %s "
                        "fejlede: %s", json_path, exc)
    else:
        _log.debug("_rewrite_stale_install_dir_paths: ingen stale stier fundet i %s "
                    "(prefix %s)", json_path, old_str)


_PRE_MIGRATION_BACKUP_NAME = "opensak.json.pre-825-migration-backup"


def _backup_opensak_json_in_place(source_dir: Path) -> None:
    """
    Efterlad en permanent kopi af `opensak.json` i `source_dir`, FØR
    migreringen rører noget som helst.

    Baggrund: et rigtigt brugertilfælde (Mike, sep. 2026) viste at selve
    filflytningen kan lykkes perfekt, og det færdigmigrerede opensak.json
    alligevel efterfølgende gå tabt af en helt anden, endnu ikke fuldt
    forstået årsag (formentlig en efterfølgende kørsel der ikke fandt
    filen på det forventede tidspunkt). Uden en backup er brugerens
    user.gc_username, hjemme-koordinater m.fl. i så fald definitivt væk —
    kun bekræftet gendannet i praksis, fordi brugeren tilfældigvis havde
    en Time Machine-backup af netop denne skjulte sti.

    Denne funktion garanterer at en kopi altid bliver liggende, urørt, i
    den ORIGINALE mappe — uafhængigt af om selve migreringen, eller noget
    efter den, går galt. Filen flyttes/slettes ALDRIG af oprydnings-
    logikken bagefter, netop fordi den gør mappen ikke-tom.

    Kun `opensak.json` (typisk << 1 MB) sikkerhedskopieres — IKKE
    databasefiler, som kan være mange GB og allerede håndteres af den
    normale flytte-logik.

    Best-effort: fejler stille (ingen exception) hvis kilden mangler,
    allerede er sikkerhedskopieret, eller kopiering af en eller anden
    grund ikke lykkes — dette må aldrig kunne forhindre selve
    migreringen i at fortsætte.
    """
    source = source_dir / "opensak.json"
    if not source.exists():
        _log.debug("_backup_opensak_json_in_place: %s findes ikke — intet at "
                    "sikkerhedskopiere", source)
        return
    backup = source_dir / _PRE_MIGRATION_BACKUP_NAME
    if backup.exists():
        _log.debug("_backup_opensak_json_in_place: backup findes allerede "
                    "(%s) — springer over", backup)
        return  # allerede sikkerhedskopieret (fx ved en tidligere, afbrudt kørsel)
    try:
        shutil.copy2(str(source), str(backup))
        _log.debug("_backup_opensak_json_in_place: backup taget: %s -> %s "
                    "(%d bytes)", source, backup, backup.stat().st_size)
    except OSError as exc:
        print(f"[settings] macOS-migration: kunne ikke tage backup af "
              f"{source}: {exc}")
        _log.debug("_backup_opensak_json_in_place: kopiering af %s fejlede: %s",
                    source, exc)


def migrate_macos_default_paths() -> bool:
    """
    Én-gangs migration af eksisterende macOS-brugeres data fra den
    fejlagtige posix-sti til den korrekte macOS-sti (issue #825).

    Før denne fix brugte macOS fejlagtigt Linux-stierne (~/.config og
    ~/.local/share) i stedet for ~/Library/Application Support, fordi
    `os.name` er "posix" på begge platforme. Denne funktion flytter
    eksisterende brugeres data til den korrekte placering.

    Ingen effekt på Windows/Linux — returnerer altid False med det samme
    på andre platforme end macOS.

    Idempotent: hvis den nye bootstrap.json allerede findes (enten fordi
    migration allerede er kørt, eller fordi det er en frisk installation
    der aldrig ramte den gamle sti), gøres intet. Rører aldrig en
    destination der allerede har data — ved kollision bevares begge
    steder uændret i stedet for at overskrive noget.

    Hvis den gamle bootstrap.json peger på en brugervalgt installations-
    mappe (via velkomst-wizarden, issue #210) i stedet for standard-stien,
    flyttes KUN selve bootstrap.json-filens placering — den brugervalgte
    mappes indhold er ikke ramt af denne bug og røres ikke.

    Returnerer True hvis noget blev migreret, False ellers.
    """
    if sys.platform != "darwin":
        return False

    new_bootstrap = _bootstrap_path()
    if new_bootstrap.exists():
        _log.debug("migrate_macos_default_paths: %s findes allerede — "
                    "intet at migrere", new_bootstrap)
        return False  # allerede migreret, eller frisk install på korrekt sti

    old_bootstrap = _legacy_macos_bootstrap_path()
    old_default_install = _legacy_macos_default_install_dir()

    _log.debug(
        "migrate_macos_default_paths: start — old_bootstrap=%s (exists=%s), "
        "old_default_install=%s (exists=%s)",
        old_bootstrap, old_bootstrap.exists(),
        old_default_install, old_default_install.exists(),
    )

    if not old_bootstrap.exists() and not old_default_install.exists():
        _log.debug("migrate_macos_default_paths: hverken gammel bootstrap "
                    "eller gammel install-mappe findes — helt frisk install")
        return False  # intet at migrere — helt frisk installation

    migrated_something = False

    # Find den FAKTISKE install_dir fra den gamle bootstrap, hvis den
    # findes — det er ikke nødvendigvis standard-stien, hvis brugeren har
    # valgt en anden mappe via velkomst-wizarden.
    actual_install_dir = old_default_install
    if old_bootstrap.exists():
        try:
            data = json.loads(old_bootstrap.read_text(encoding="utf-8"))
            candidate = data.get("install_dir")
            if candidate:
                actual_install_dir = Path(candidate)
        except (json.JSONDecodeError, OSError) as exc:
            _log.debug("migrate_macos_default_paths: kunne ikke læse gammel "
                        "bootstrap %s (%s) — bruger standard-sti", old_bootstrap, exc)

    if actual_install_dir == old_default_install:
        _log.debug("migrate_macos_default_paths: standard-sti-gren "
                    "(actual_install_dir == old_default_install = %s)",
                    old_default_install)
        # Standard-sti — flyt selve indholdet (opensak.json, databaser osv.)
        # til den nye standard-sti. Samme "best-effort, spring kollisioner
        # over"-mønster som _move_remaining_install_dir_contents() i
        # velkomst-wizarden (issue #562).
        new_install_dir = _default_install_dir()
        if old_default_install.exists():
            _backup_opensak_json_in_place(old_default_install)
            new_install_dir.mkdir(parents=True, exist_ok=True)
            try:
                entries = list(old_default_install.iterdir())
            except OSError as exc:
                _log.debug("migrate_macos_default_paths: kunne ikke liste "
                            "%s: %s", old_default_install, exc)
                entries = []
            _log.debug("migrate_macos_default_paths: indhold fundet i %s: %s "
                        "(backup-filen '%s' flyttes ikke, bliver liggende i "
                        "den gamle mappe)", old_default_install,
                        [e.name for e in entries], _PRE_MIGRATION_BACKUP_NAME)
            for entry in entries:
                if entry.name == _PRE_MIGRATION_BACKUP_NAME:
                    continue  # skal blive liggende urørt i den GAMLE mappe
                target = new_install_dir / entry.name
                if target.exists():
                    _log.debug("migrate_macos_default_paths: kollision på %s "
                                "— springer over, rører intet", target)
                    continue  # kollision — rør det ikke, behold begge som de er
                try:
                    shutil.move(str(entry), str(target))
                    migrated_something = True
                    _log.debug("migrate_macos_default_paths: flyttede %s -> %s",
                                entry, target)
                except OSError as exc:
                    print(f"[settings] macOS-migration: kunne ikke flytte "
                          f"{entry} → {target}: {exc}")
                    _log.debug("migrate_macos_default_paths: flytning af %s "
                                "-> %s fejlede: %s", entry, target, exc)
            try:
                if not any(old_default_install.iterdir()):
                    old_default_install.rmdir()
                    _log.debug("migrate_macos_default_paths: %s var tom og "
                                "blev fjernet", old_default_install)
            except OSError:
                pass

            # Issue #867: databases.list/.active/.dir i opensak.json
            # indeholder absolutte stier under den gamle mappe, som
            # ovenstående filflytning ikke selv retter — uden dette leder
            # DatabaseManager efter databasen på en sti der ikke længere
            # findes, og opretter en tom database i stedet.
            _rewrite_stale_install_dir_paths(
                new_install_dir / "opensak.json",
                old_default_install,
                new_install_dir,
            )
    else:
        # Brugervalgt mappe — indholdet er ikke ramt af bug'en, kun
        # bootstrap.json's egen (forkerte) placering skal rettes.
        _log.debug("migrate_macos_default_paths: brugervalgt install-mappe-gren "
                    "— actual_install_dir=%s (rører ikke indholdet)",
                    actual_install_dir)
        new_install_dir = actual_install_dir
        _backup_opensak_json_in_place(actual_install_dir)

    # Skriv bootstrap.json på den nye, korrekte sti, pegende på den
    # (evt. flyttede) installationsmappe.
    _atomic_write(new_bootstrap, {"install_dir": str(new_install_dir)})
    migrated_something = True

    # Ryd op i den gamle bootstrap.json/mappe hvis den nu er tom.
    if old_bootstrap.exists():
        try:
            old_bootstrap.unlink()
        except OSError:
            pass
    try:
        if old_bootstrap.parent.exists() and not any(old_bootstrap.parent.iterdir()):
            old_bootstrap.parent.rmdir()
    except OSError:
        pass

    if migrated_something:
        print(f"[settings] macOS-sti migreret: {old_default_install} → "
              f"{new_install_dir}")

    _log.debug(
        "migrate_macos_default_paths: færdig — migrated_something=%s, "
        "new_install_dir=%s, new_bootstrap=%s (findes=%s), indhold i "
        "new_install_dir=%s",
        migrated_something, new_install_dir, new_bootstrap, new_bootstrap.exists(),
        sorted(p.name for p in new_install_dir.iterdir()) if new_install_dir.exists() else None,
    )

    return migrated_something


def get_db_dir() -> Path:
    """
    Returner den mappe hvor nye databaser oprettes som standard.

    Læses fra opensak.json ("databases.dir").
    Falder tilbage til install_dir hvis ikke sat.
    """
    store = get_store()
    d = store.get("databases.dir")
    if d:
        p = Path(d)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return get_install_dir()


def is_first_run() -> bool:
    """
    Returner True hvis wizarden aldrig er gennemført.

    Tjekker om _wizard_completed er sat i opensak.json.
    Bruges af app.py til at beslutte om wizard skal vises.
    """
    return not get_store().get("_wizard_completed", False)


def mark_wizard_completed() -> None:
    """Markér at wizard er gennemført (bruges ved migration fra ældre installation)."""
    get_store().set("_wizard_completed", True)


# ── Modul-niveau singleton ────────────────────────────────────────────────────

_store: SettingsStore | None = None


def get_store() -> SettingsStore:
    """Returner den globale SettingsStore-instans (lazy-initialiseret)."""
    global _store
    if _store is None:
        _store = SettingsStore()
    return _store


def reset_store() -> None:
    """
    Nulstil singleton — bruges af tests og af wizard (#210) efter
    installation i ny mappe.
    """
    global _store
    _store = None

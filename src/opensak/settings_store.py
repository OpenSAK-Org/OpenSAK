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
import tempfile
from pathlib import Path
from typing import Any


# ── Bootstrap-sti ─────────────────────────────────────────────────────────────

def _bootstrap_path() -> Path:
    """Returner platform-korrekt sti til bootstrap.json."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif os.name == "posix":
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
    else:
        base = Path.home() / ".config"
    return base / "opensak" / "bootstrap.json"


def _default_install_dir() -> Path:
    """Standard installations-mappe — bruges hvis bootstrap ikke findes."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif os.name == "posix":
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    else:
        base = Path.home()
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
        # Atomisk rename — virker på alle platforme
        Path(tmp_path).replace(path)
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

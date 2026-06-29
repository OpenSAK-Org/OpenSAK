"""
config.py — Application configuration and path management.
All paths use pathlib.Path so they work identically on Linux and Windows.

Fra version 1.14.0 (issue #209) bruges settings_store til at finde
installations-mappen. De øvrige hjælpefunktioner her er beregnet til
afledte stier (logs, imports, tokens) der altid er relative til install-mappen.
"""
from pathlib import Path
import os


def get_app_data_dir() -> Path:
    """
    Return the installations-mappen (install_dir).

    Delegerer til settings_store.get_install_dir() som læser bootstrap.json.
    Opretter mappen automatisk hvis den ikke eksisterer.

    Compat: andre moduler importerer denne funktion — den virker stadig.
    """
    from opensak.settings_store import get_install_dir
    return get_install_dir()


def get_db_path() -> Path:
    """Return the full path to the legacy SQLite database file (pre-1.14.0)."""
    return get_app_data_dir() / "opensak.db"


def get_gpx_import_dir() -> Path:
    """Return (and create if needed) the default GPX import directory."""
    d = get_app_data_dir() / "imports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_log_path() -> Path:
    """Return the path to the application log file."""
    return get_app_data_dir() / "opensak.log"


def get_gc_token_path() -> Path:
    """
    Return the path to the Geocaching.com OAuth token file.
    File is stored with chmod 600 (only owner can read).
    """
    return get_app_data_dir() / "gc_token.json"


# ── Language ──────────────────────────────────────────────────────────────────

def get_language() -> str:
    """
    Return the saved language code.
    Default: 'en' (English) for new installations.

    Læser fra settings_store (opensak.json) siden 1.14.0.
    Falder tilbage til preferences.json for migration af ældre installationer.
    """
    from opensak.settings_store import get_store
    store = get_store()

    # Forsøg først den nye store
    lang = store.get("app.language")
    if lang:
        return str(lang)

    # Migration: læs fra gammel preferences.json
    import json
    prefs_file = get_app_data_dir() / "preferences.json"
    if prefs_file.exists():
        try:
            data = json.loads(prefs_file.read_text(encoding="utf-8"))
            legacy_lang = data.get("language", "en")
            # Migrer til ny store med det samme
            store.set("app.language", legacy_lang)
            return legacy_lang
        except (json.JSONDecodeError, OSError):
            pass

    return "en"


def set_language(lang_code: str) -> None:
    """Save the language code to disk via settings_store."""
    from opensak.settings_store import get_store
    get_store().set("app.language", lang_code)


# ── Convenience summary (useful for debug / startup banner) ──────────────────

def print_config() -> None:
    print(f"  App data dir : {get_app_data_dir()}")
    print(f"  Database     : {get_db_path()}")
    print(f"  GPX imports  : {get_gpx_import_dir()}")
    print(f"  Log file     : {get_log_path()}")
    print(f"  GC token     : {get_gc_token_path()}")
    print(f"  Language     : {get_language()}")


if __name__ == "__main__":
    print("OpenSAK configuration paths:")
    print_config()

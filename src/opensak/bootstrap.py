"""
src/opensak/bootstrap.py — the start-up steps that don't need a GUI.

Shared by the GUI start-up (app.main) and the headless commands
(`opensak import …`, #937), so both use the same settings, database list,
language and log file. Nothing here imports QtWidgets or opens a window;
app.main() runs these steps in its own order between the GUI-only ones
(theme, splash, welcome wizard).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def setup_logging(rotate: bool = True) -> Path:
    """Start logging to OpenSAK's log file; returns its path.

    *rotate*: keep the previous session's log as opensak.log.previous and
    start a new one, as the GUI does. A headless run appends to the current
    log instead, so it never moves away the log of an OpenSAK that is open
    (or the one of the last session the user may still want to report).
    """
    from opensak.logger import setup_logging as _setup
    return _setup(rotate=rotate)


def migrate_paths() -> None:
    """macOS path migration (#825). Must run before anything reads the
    settings — see app.main() for why (#878)."""
    from opensak.settings_store import migrate_macos_default_paths
    migrate_macos_default_paths()


def apply_pending_settings_restore() -> Any:
    """#987: a settings restore chosen in the last GUI run, applied now,
    before anything reads the settings. GUI only: a headless run may start
    while OpenSAK is open, which would overwrite the restored settings on
    exit."""
    from opensak.backup.settings_restore import apply_pending_settings_restore as _apply
    return _apply()


def migrate_settings() -> None:
    """One-time QSettings → opensak.json migration (#209) and the repair of
    boolean values a former bug corrupted."""
    from opensak.settings_store import (
        get_store, mark_wizard_completed, migrate_from_qsettings,
        repair_corrupted_bool_keys,
    )
    did_migrate = migrate_from_qsettings(get_store())
    # Reparér evt. boolean-værdier korrumperet af en tidligere bug i
    # _flush() — kører altid, uafhængigt af om migration var nødvendig,
    # så også brugere af tidligere 1.14.0-beta-builds får det rettet.
    repair_corrupted_bool_keys(get_store())
    # Eksisterende installation (migreret fra QSettings) → wizard er ikke nødvendig
    if did_migrate:
        mark_wizard_completed()


def load_language() -> str:
    """Load the user's language for tr(); returns its code."""
    from opensak.config import get_language
    from opensak.lang import load_language as _load
    code = get_language()
    _load(code)
    return code


def migrate_legacy_db() -> None:
    """
    Migrer gammel opensak.db til Default.db.

    Scenarier:
    - opensak.db eksisterer, Default.db ikke → omdøb
    - Begge eksisterer → slet den tomme Default.db, behold opensak.db
    - Kun Default.db → ingenting at gøre
    """
    from opensak.config import get_app_data_dir
    app_dir = get_app_data_dir()
    legacy = app_dir / "opensak.db"
    default = app_dir / "Default.db"

    if legacy.exists() and not default.exists():
        # Simpel migration
        legacy.rename(default)
        logger.info("Migrated %s → %s", legacy.name, default.name)

    elif legacy.exists() and default.exists():
        # Begge eksisterer — tjek hvilken der er størst (har data)
        legacy_size = legacy.stat().st_size
        default_size = default.stat().st_size
        if legacy_size > default_size:
            # opensak.db har data, Default.db er tom — erstat
            default.unlink()
            # Slet også WAL/SHM filer for Default hvis de findes
            for ext in [".db-shm", ".db-wal"]:
                p = app_dir / f"Default{ext}"
                if p.exists():
                    p.unlink()
            legacy.rename(default)
            logger.info("Migrated %s → %s (replaced empty Default.db)",
                        legacy.name, default.name)
        else:
            # Default.db har data — slet den tomme opensak.db
            legacy.unlink()
            for ext in [".db-shm", ".db-wal"]:
                p = app_dir / f"opensak{ext}"
                if p.exists():
                    p.unlink()
            logger.info("Deleted empty %s", legacy.name)


def init_headless() -> Path:
    """Everything a headless command needs before it touches a database:
    the log, settings, language and the legacy database name. Opens no
    database — the command opens the one it works on. Returns the log path.
    """
    log_path = setup_logging(rotate=False)
    from opensak import __version__
    logger.info("headless: start, version=%s", __version__)
    migrate_paths()
    migrate_settings()
    load_language()
    migrate_legacy_db()
    return log_path

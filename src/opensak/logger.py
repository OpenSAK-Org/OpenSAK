"""
src/opensak/logger.py — Central logging-opsætning til OpenSAK.

Issue #232: lightweight, always-on debug logging system.

  - Altid aktiveret — ingen brugerhandling nødvendig.
  - Nulstilles ved hver opstart (mode="w") så filen aldrig vokser
    ubegrænset fra session til session.
  - Roterer ved 1 MB med 1 backup (RotatingFileHandler) — fanger også
    langvarige sessioner uden at logfilen vokser uendeligt.
  - Per-modul kontrol via debug_flags.py — ingen kodeændringer nødvendige
    for at slå debug til/fra for et modul.

Loggen ligger i install_dir (samme sted som opensak.json), så den følger
brugerens valg fra velkomst-wizarden (#210) i stedet for en fast sti.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_MAX_BYTES = 1 * 1024 * 1024  # 1 MB
_BACKUP_COUNT = 1

_initialized = False


def setup_logging() -> Path:
    """
    Initialiser logging-systemet. Kaldes én gang ved opstart fra app.py.

    Returnerer stien til logfilen.

    Idempotent: gentagne kald (fx i tests) gør ikke noget hvis allerede
    initialiseret, og returnerer blot den eksisterende sti.
    """
    global _initialized

    from opensak.config import get_log_path
    log_path = get_log_path()

    if _initialized:
        return log_path

    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Nulstil loggen eksplicit ved hver opstart (issue #232 krav).
    # RotatingFileHandler's mode="w" trunkerer ikke altid en eksisterende
    # fil pålideligt på tværs af Python-versioner, så vi sletter den selv
    # før handleren oprettes — det er den robuste tilgang.
    try:
        log_path.unlink(missing_ok=True)
    except OSError:
        pass

    root_logger = logging.getLogger("opensak")
    root_logger.setLevel(logging.DEBUG)

    # RotatingFileHandler roterer derefter ved 1 MB hvis sessionen er lang.
    handler = RotatingFileHandler(
        log_path,
        mode="a",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    _initialized = True
    return log_path


def get_logger(module: str) -> logging.Logger:
    """
    Returner en logger for det givne modul.

    Brug: log = get_logger("updater")
          log.debug("Tjekker for ny version...")

    Hvis modulets debug-flag (i debug_flags.py) er False, sættes loggeren
    til kun at logge WARNING og højere — debug()/info() bliver tavse uden
    at kalderen behøver tjekke flaget selv.
    """
    from opensak.debug_flags import is_debug_enabled

    logger = logging.getLogger(f"opensak.{module}")
    logger.setLevel(logging.DEBUG if is_debug_enabled(module) else logging.WARNING)
    return logger


def reset_logging() -> None:
    """
    Nulstil initialiserings-state — bruges af tests for at sikre isolation
    mellem testkørsler.
    """
    global _initialized
    _initialized = False
    root_logger = logging.getLogger("opensak")
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)
        h.close()

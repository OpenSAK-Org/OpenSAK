"""
src/opensak/logger.py — Central logging-opsætning til OpenSAK.

Issue #232: lightweight, always-on debug logging system.

  - Altid aktiveret — ingen brugerhandling nødvendig.
  - Roterer ved hver opstart (issue #737) — forrige sessions fulde log
    gemmes som opensak.log.previous i stedet for at blive slettet, så en
    session der endte i et crash/hæng stadig kan hentes efter genstart.
    Kun den ene seneste tidligere session bevares (samme #232-mål om
    aldrig at vokse ubegrænset — se setup_logging()).
  - Roterer også ved 1 MB med 1 backup (RotatingFileHandler) — fanger
    også langvarige sessioner uden at logfilen vokser uendeligt.
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


def setup_logging(rotate: bool = True) -> Path:
    """
    Initialiser logging-systemet. Kaldes én gang ved opstart fra app.py.

    Returnerer stien til logfilen.

    *rotate* (default): start a new log and keep the previous session's as
    opensak.log.previous (#737). A headless run (#937) passes False and
    appends to the current log, so it never moves away the log of an
    OpenSAK that is open, or of the session before it.

    Idempotent: gentagne kald (fx i tests) gør ikke noget hvis allerede
    initialiseret, og returnerer blot den eksisterende sti.
    """
    global _initialized

    from opensak.config import get_log_path, get_previous_log_path
    log_path = get_log_path()

    if _initialized:
        return log_path

    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Issue #737: bevar forrige sessions log i stedet for at slette den —
    # gør den nuværende opensak.log til opensak.log.previous (overskriver
    # en evt. ældre .previous-fil, så kun den seneste forrige session
    # bevares). replace() overskriver atomisk på tværs af platforme
    # (Windows inkl.), i modsætning til rename()/Path.rename() som fejler
    # hvis målfilen allerede findes på Windows.
    try:
        if rotate and log_path.exists():
            log_path.replace(get_previous_log_path())
    except OSError:
        # Best-effort — hvis rotation fejler (fx låst fil), falder vi
        # tilbage til den gamle adfærd: slet, så den nye session ikke
        # skriver oven på forældede rester.
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

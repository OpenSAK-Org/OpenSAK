"""
src/opensak/logger.py — Central logging setup for OpenSAK.

Issue #232: lightweight, always-on debug logging system.

  - Always enabled — no user action required.
  - Rotates at every startup (issue #737) — the previous session's full log
    is saved as opensak.log.previous instead of being deleted, so a session
    that ended in a crash/hang can still be retrieved after restart.
    Only the single most recent previous session is kept (same #232 goal of
    never growing without bound — see setup_logging()).
  - Also rotates at 1 MB with 1 backup (RotatingFileHandler) — catches
    long-running sessions without the log file growing endlessly.
  - Per-module control via debug_flags.py — no code changes required to turn
    debug on/off for a module.

The log lives in install_dir (the same place as opensak.json), so it follows
the user's choice from the welcome wizard (#210) instead of a fixed path.
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
    Initialize the logging system. Called once at startup from app.py.

    Returns the log file path.


    *rotate* (default): start a new log and keep the previous session's as
    opensak.log.previous (#737). A headless run (#937) passes False and
    appends to the current log, so it never moves away the log of an
    OpenSAK that is open, or of the session before it.

    Idempotent: gentagne kald (fx i tests) gør ikke noget hvis allerede
    initialiseret, og returnerer blot den eksisterende sti.

    Idempotent: repeated calls (for example in tests) do nothing if already
    initialized and simply return the existing path.

    """
    global _initialized

    from opensak.config import get_log_path, get_previous_log_path
    log_path = get_log_path()

    if _initialized:
        return log_path

    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Issue #737: keep the previous session's log instead of deleting it —
    # rename the current opensak.log to opensak.log.previous (overwriting any
    # older .previous file, so only the most recent previous session is kept).
    # replace() is atomic across platforms (including Windows), unlike
    # rename()/Path.rename(), which fails if the target already exists on
    # Windows.
    try:
        if rotate and log_path.exists():
            log_path.replace(get_previous_log_path())
    except OSError:
        # Best-effort — if rotation fails (for example, because the file is
        # locked), fall back to the old behavior: delete it so the new session
        # does not write on top of stale leftovers.
        try:
            log_path.unlink(missing_ok=True)
        except OSError:
            pass

    root_logger = logging.getLogger("opensak")
    root_logger.setLevel(logging.DEBUG)

    # RotatingFileHandler rotates after 1 MB if the session is long.
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
    Return a logger for the given module.

    Usage: log = get_logger("updater")
           log.debug("Checking for a new version...")

    If the module's debug flag (in debug_flags.py) is False, the logger is set
    to only log WARNING and above — debug()/info() become silent without the
    caller needing to check the flag itself.
    """
    from opensak.debug_flags import is_debug_enabled

    logger = logging.getLogger(f"opensak.{module}")
    logger.setLevel(logging.DEBUG if is_debug_enabled(module) else logging.WARNING)
    return logger


def reset_logging() -> None:
    """
    Reset the initialization state — used by tests to ensure isolation between
    test runs.
    """
    global _initialized
    _initialized = False
    root_logger = logging.getLogger("opensak")
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)
        h.close()

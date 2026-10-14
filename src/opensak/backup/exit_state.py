"""
src/opensak/backup/exit_state.py — what "back up on exit" needs to decide (#959).

Part of the backup epic (#942); design: docs/architecture/backup.md
("Backup flows → On exit").

When OpenSAK closes it offers a backup, but only when something changed since
the last one. After every successful backup — manual (#953) or on exit — the
current state is recorded in ``backup.last_state`` (opensak.json):

* per database in the database list: size and modification time of the
  ``.db`` file, and of the ``-wal`` file when it holds anything;
* per settings folder (filters/, column_views/): the number of entries and
  the newest modification time.

opensak.json itself is left out: OpenSAK writes the window geometry to it on
every exit.

**Why an empty WAL is ignored, and why the state is recorded again after
close.** When the last connection to a database closes, SQLite checkpoints
the WAL into the ``.db`` file, which changes its size and modification time
even though the content is the same; the next start then creates an empty
``-wal`` with a new modification time. Comparing raw file stats would
therefore ask again on every exit after a session that wrote anything.
So an empty or missing ``-wal`` counts as "no WAL", and when the state was
clean at close (nothing changed, or a backup was just made), the app records
it once more after the database engine has been disposed
(finalize_after_close(), called from app.py). The content is the same; only
the file layout after the checkpoint is new.

This module has no UI.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# ── Settings keys and values ──────────────────────────────────────────────────

ON_EXIT_KEY = "backup.on_exit"
LAST_STATE_KEY = "backup.last_state"

ON_EXIT_ASK = "ask"
ON_EXIT_ALWAYS = "always"
ON_EXIT_NEVER = "never"
ON_EXIT_CHOICES = (ON_EXIT_ASK, ON_EXIT_ALWAYS, ON_EXIT_NEVER)
DEFAULT_ON_EXIT = ON_EXIT_ASK

# Settings folders (in the install folder) whose changes count. icons/ is
# backed up too, but isn't something a user edits in a session.
_WATCHED_SETTINGS_DIRS = ("filters", "column_views")

_STATE_FORMAT = 1

# ── Process-wide flags ────────────────────────────────────────────────────────

# Set when OpenSAK closes itself and must not offer a backup: an in-app
# update replaced the app on disk, an uninstall removed it (and maybe its
# data), or the OS session is ending. Also set for the whole test suite by
# tests/conftest.py, so closing a MainWindow in a test never opens a prompt.
_suppressed = False

# Set by the close flow when the state was clean at close (see module
# docstring); read by finalize_after_close().
_clean_on_close = False


# ── On-exit setting ───────────────────────────────────────────────────────────

def get_on_exit() -> str:
    """Ask / Always / Never; anything unknown reads as the default (Ask)."""
    from opensak.settings_store import get_store
    value = get_store().get(ON_EXIT_KEY, DEFAULT_ON_EXIT)
    return value if value in ON_EXIT_CHOICES else DEFAULT_ON_EXIT


def set_on_exit(value: str) -> None:
    """Store the on-exit choice (raises ValueError for an unknown value)."""
    from opensak.settings_store import get_store
    if value not in ON_EXIT_CHOICES:
        raise ValueError(f"unknown backup.on_exit value: {value!r}")
    get_store().set(ON_EXIT_KEY, value)


# ── Suppression ───────────────────────────────────────────────────────────────

def suppress_exit_backup() -> None:
    """Don't offer a backup when the window closes from now on (this run)."""
    global _suppressed
    _suppressed = True


def is_exit_backup_suppressed() -> bool:
    return _suppressed


# ── State ─────────────────────────────────────────────────────────────────────

def current_state(databases: Optional[Iterable[Any]] = None) -> dict[str, Any]:
    """
    The state of the databases in *databases* (default: the database list)
    and of the watched settings folders, in the JSON form stored in
    ``backup.last_state``.
    """
    if databases is None:
        databases = _listed_databases()
    return {
        "format": _STATE_FORMAT,
        "databases": {
            _key(db.path): _database_fingerprint(Path(db.path))
            for db in databases
        },
        "settings": _settings_fingerprint(),
    }


def has_changes_since_backup(databases: Optional[Iterable[Any]] = None) -> bool:
    """
    True when something a backup would protect changed since the last
    backup — or when no backup state has been recorded yet. A database
    added to or removed from the list counts as a change.
    """
    from opensak.settings_store import get_store
    last = get_store().get(LAST_STATE_KEY)
    if not isinstance(last, dict) or last.get("format") != _STATE_FORMAT:
        return True
    return current_state(databases) != last


def record_backup_state(backed_up: Optional[Iterable[Any]] = None) -> None:
    """
    Record the current state as backed up. Call on the GUI thread after a
    successful backup.

    *backed_up* is the databases the backup contained; None means all of
    them (a full backup). Databases left out keep their previously recorded
    state — or stay unrecorded — so a change to one of them still counts at
    the next exit; a database whose file is missing is recorded as it is,
    since no backup can include it. Settings are always part of a backup set.
    """
    from opensak.settings_store import get_store

    now = current_state()
    if backed_up is not None:
        included = {_key(db.path) for db in backed_up}
        last = get_store().get(LAST_STATE_KEY)
        old = (
            last.get("databases", {})
            if isinstance(last, dict) and last.get("format") == _STATE_FORMAT
            else {}
        )
        if not isinstance(old, dict):
            old = {}
        merged: dict[str, Any] = {}
        for key, fingerprint in now["databases"].items():
            if key in included or fingerprint["db"] is None:
                # A missing file can't be backed up, so it never counts.
                merged[key] = fingerprint
            elif key in old:
                merged[key] = old[key]
            # else: never backed up — leave it out, so it reads as changed
        now["databases"] = merged
    get_store().set(LAST_STATE_KEY, now)


# ── Close ─────────────────────────────────────────────────────────────────────

def mark_clean_on_close() -> None:
    """The window is closing with nothing left unbacked-up (see docstring)."""
    global _clean_on_close
    _clean_on_close = True


def finalize_after_close() -> None:
    """
    Called by app.py once the event loop has ended. When the state was
    clean at close, dispose the database engine — SQLite checkpoints the
    WAL — and record the state again, so the checkpoint doesn't count as a
    change at the next exit. Never raises: at worst the next exit asks once
    more than needed.
    """
    global _clean_on_close
    if not _clean_on_close:
        return
    _clean_on_close = False
    try:
        from opensak.db.database import dispose_engine
        dispose_engine()
        record_backup_state()
    except Exception:
        logger.warning("backup: could not refresh the backup state after close",
                       exc_info=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _listed_databases() -> list[Any]:
    from opensak.db.manager import get_db_manager
    return list(get_db_manager().databases)


def _key(path: Any) -> str:
    """A stable key for a database path (absolute, as listed)."""
    return str(Path(path))


def _file_stat(path: Path) -> Optional[list[int]]:
    try:
        st = path.stat()
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


def _database_fingerprint(path: Path) -> dict[str, Any]:
    """
    ``{"db": [size, mtime_ns] | None, "wal": [size, mtime_ns] | None}``.
    An empty or missing ``-wal`` is None: it holds nothing, and SQLite
    creates and removes it as connections open and close.
    """
    wal = _file_stat(path.with_name(path.name + "-wal"))
    if wal is not None and wal[0] == 0:
        wal = None
    return {"db": _file_stat(path), "wal": wal}


def _settings_fingerprint() -> dict[str, Any]:
    """Per watched folder: [number of entries, newest mtime_ns] or None."""
    from opensak.settings_store import get_install_dir
    install = get_install_dir()
    return {name: _folder_fingerprint(install / name)
            for name in _WATCHED_SETTINGS_DIRS}


def _folder_fingerprint(folder: Path) -> Optional[list[int]]:
    """
    The folder's own mtime counts too, so deleting or renaming a file — which
    changes the folder, not any remaining file — is noticed.
    """
    try:
        newest = folder.stat().st_mtime_ns
    except OSError:
        return None
    count = 0
    for root, dirs, files in os.walk(folder):
        for name in dirs + files:
            count += 1
            try:
                newest = max(newest, os.stat(os.path.join(root, name)).st_mtime_ns)
            except OSError:
                pass
    return [count, newest]

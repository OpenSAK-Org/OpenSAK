"""
src/opensak/db/ownership.py — caches.is_owner from the user's account (#1140).

A cache is the user's own when its owner matches the geocaching.com account
set in Settings: the numeric owner ID equals ``gc_finder_id``, or the owner
name equals ``gc_username`` (normalised the same way as everywhere else, see
normalize_geocacher_name()). Either signal is enough — the same rule
count_own_found_logs() uses for the user's own logs.

The GPX importer sets is_owner per cache as it imports, the edit dialog
when a cache is added or edited, and macro writes when they change the
owner (macro/cache_write.py). When the account
changes in Settings, recalculate_is_owner_everywhere() brings every
registered database in line, replacing earlier values — including the ones
the GSAK importer took from GSAK's own IsOwner — so the column always
follows the current account. With no account configured at all nothing is
recalculated: there is nothing to compare against, and wiping imported
values would only lose information.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from opensak.logger import get_logger
from opensak.utils.utils import normalize_geocacher_name

_log = get_logger("ownership")


def owner_matches(
    owner_id: Optional[str],
    owner_name: Optional[str],
    gc_finder_id: Optional[str],
    gc_username: Optional[str],
) -> bool:
    """True if a cache with this owner belongs to the configured account."""
    finder_id = (gc_finder_id or "").strip()
    if finder_id and (owner_id or "").strip() == finder_id:
        return True
    username = normalize_geocacher_name(gc_username)
    return bool(username) and normalize_geocacher_name(owner_name) == username


def account_configured(gc_finder_id: Optional[str], gc_username: Optional[str]) -> bool:
    return bool((gc_finder_id or "").strip() or normalize_geocacher_name(gc_username))


def update_is_owner(cache) -> None:
    """Set *cache*.is_owner from its current owner and the account in
    Settings (GPX import, the edit dialog). Without an account there is
    nothing to compare against: leave what is there."""
    from opensak.gui.settings import get_settings

    settings = get_settings()
    if account_configured(settings.gc_finder_id, settings.gc_username):
        cache.is_owner = owner_matches(
            cache.owner_id, cache.owner_name, settings.gc_finder_id, settings.gc_username
        )


def recalculate_is_owner(conn: Connection, gc_finder_id: Optional[str],
                         gc_username: Optional[str]) -> int:
    """Set caches.is_owner from the account in one database; returns the
    number of caches that changed. Does nothing without an account, or on a
    database that has no is_owner column yet (it gets it, and this
    calculation, from migration 28 when it is next opened). The caller
    commits."""
    if not account_configured(gc_finder_id, gc_username):
        return 0
    columns = {r[1] for r in conn.execute(text("PRAGMA table_info(caches)"))}
    if "is_owner" not in columns:
        return 0
    changes = [
        {"id": cid, "v": int(want)}
        for cid, owner_id, owner_name, current in conn.execute(
            text("SELECT id, owner_id, owner_name, is_owner FROM caches")
        )
        if bool(current) != (want := owner_matches(owner_id, owner_name,
                                                   gc_finder_id, gc_username))
    ]
    if changes:
        conn.execute(text("UPDATE caches SET is_owner = :v WHERE id = :id"), changes)
    return len(changes)


def recalculate_is_owner_everywhere(
    paths: Iterable[Path], gc_finder_id: Optional[str], gc_username: Optional[str],
) -> dict[Path, int]:
    """recalculate_is_owner() in each database file in *paths*. A database
    that can't be opened or written (missing, on a removed drive, locked) is
    skipped and logged; returns the number of changed caches per database."""
    result: dict[Path, int] = {}
    if not account_configured(gc_finder_id, gc_username):
        return result
    for path in paths:
        path = Path(path)
        if not path.is_file():
            continue
        engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 30})
        try:
            with engine.begin() as conn:
                result[path] = recalculate_is_owner(conn, gc_finder_id, gc_username)
        except Exception:
            _log.warning("is_owner: could not recalculate %s", path, exc_info=True)
        finally:
            engine.dispose()
    return result


def account_changed(old_finder_id: Optional[str], old_username: Optional[str]) -> dict[Path, int]:
    """Call after the account in Settings may have changed: when it did,
    recalculate is_owner in every registered database. Returns the changes
    per database (empty when the account is unchanged or not set)."""
    from opensak.db.manager import get_db_manager
    from opensak.gui.settings import get_settings

    s = get_settings()
    same = (
        (old_finder_id or "").strip() == (s.gc_finder_id or "").strip()
        and normalize_geocacher_name(old_username) == normalize_geocacher_name(s.gc_username)
    )
    if same:
        return {}
    # Never let this stop Settings from saving: the next GPX import still
    # sets is_owner per cache, and migration 28 covers unmigrated databases.
    try:
        paths = [db.path for db in get_db_manager().databases]
        result = recalculate_is_owner_everywhere(paths, s.gc_finder_id, s.gc_username)
    except Exception:
        _log.warning("is_owner: recalculation after account change failed", exc_info=True)
        return {}
    _log.info("is_owner recalculated after account change: %s",
              {p.name: n for p, n in result.items()})
    return result

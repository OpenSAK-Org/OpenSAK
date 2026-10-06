"""
src/opensak/db/discover.py — find OpenSAK databases in a folder that are not
in the database list yet (issue #985).

OpenSAK keeps an explicit database list and never scans the database folder
on its own: a database the user removed from the list must not come back by
itself. This module is only used when the user asks for it — the Welcome
Wizard after choosing a database folder, and Manage Databases → Scan database
folder.

Every candidate is opened read-only with plain sqlite3: no SQLAlchemy engine,
no migration and no new sidecar files. Only the top level of the folder is
scanned, so the pre-migration copies in its ``backups/`` subfolder (#549) are
never offered.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

DB_SUFFIX = ".db"
# Name part of a #549 pre-migration copy, in case one ends up at the top level.
_PRE_MIGRATION_MARK = ".pre-migration-schema"


@dataclass(frozen=True)
class FoundDatabase:
    """An OpenSAK database file that isn't in the database list."""

    path: Path
    size_bytes: int
    modified: datetime

    @property
    def name(self) -> str:
        """The name it gets in the list (DatabaseManager.open_database())."""
        return self.path.stem


def find_unregistered_databases(
    folder: Path,
    registered: Iterable[Path],
    *,
    schema_version: Optional[int] = None,
) -> list[FoundDatabase]:
    """
    OpenSAK databases directly in *folder* whose path is not in *registered*,
    sorted by name. Files that aren't OpenSAK databases, or whose schema is
    newer than this OpenSAK (*schema_version*, default the current one), are
    left out. A missing or unreadable folder gives an empty list.
    """
    if schema_version is None:
        from opensak.db.database import SCHEMA_VERSION
        schema_version = SCHEMA_VERSION

    known = {_resolved(Path(p)) for p in registered}
    try:
        candidates = sorted(Path(folder).iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []

    found: list[FoundDatabase] = []
    for path in candidates:
        if not _looks_like_a_database_file(path):
            continue
        if _resolved(path) in known:
            continue
        version = opensak_schema_version(path)
        if version is None:
            logger.info("discover: %s is not an OpenSAK database — skipped", path.name)
            continue
        if version > schema_version:
            logger.info(
                "discover: %s has schema %s, newer than this OpenSAK (%s) — skipped",
                path.name, version, schema_version,
            )
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        found.append(FoundDatabase(
            path=path,
            size_bytes=stat.st_size,
            modified=datetime.fromtimestamp(stat.st_mtime),
        ))
    return found


def opensak_schema_version(path: Path) -> Optional[int]:
    """
    The schema version (``PRAGMA user_version``) of the OpenSAK database at
    *path*, or None if it isn't one: not SQLite, unreadable, or no ``caches``
    table with OpenSAK's ``gc_code`` column (a GSAK database also has a
    Caches table, but calls the column Code).
    """
    try:
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except (sqlite3.Error, OSError, ValueError):
        return None
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(caches)")}
        if "gc_code" not in columns:
            return None
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _looks_like_a_database_file(path: Path) -> bool:
    name = path.name
    return (
        path.suffix.lower() == DB_SUFFIX
        and not name.startswith(".")
        and _PRE_MIGRATION_MARK not in name
        and path.is_file()
    )


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()

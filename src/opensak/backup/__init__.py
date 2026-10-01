"""
src/opensak/backup — Backup support for OpenSAK databases (roadmap item 1, #942).

Phase A starts with the snapshot core (#943): a consistent, self-contained
copy of a database that is safe to take while the database is open and in
use. Every copy/backup path is meant to go through snapshot_database().
"""

from opensak.backup.snapshot import SnapshotError, snapshot_database

__all__ = ["SnapshotError", "snapshot_database"]

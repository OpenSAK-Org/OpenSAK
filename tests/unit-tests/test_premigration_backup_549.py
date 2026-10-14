"""
tests/unit-tests/test_premigration_backup_549.py — one-time backup before a
schema migration (issue #549).

The "old database" in these tests is a current database whose
PRAGMA user_version has been rewound, which is how the existing migration
tests in test_db.py simulate a database from an earlier OpenSAK build.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from opensak.backup import premigration
from opensak.backup.premigration import (
    BACKUP_DIRNAME,
    backup_before_migration,
    backup_path_for,
    pending_schema_version,
    take_notices,
)
from opensak.db.database import SCHEMA_VERSION, _migrated_paths, dispose_engine, init_db

OLD = SCHEMA_VERSION - 1


def _user_version(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _tables(path: Path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def _make_old_db(path: Path, version: int = OLD) -> Path:
    """A real OpenSAK database, rewound to look like it's at *version*."""
    _migrated_paths.discard(path)
    init_db(db_path=path)
    dispose_engine(path)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    finally:
        conn.close()
    _migrated_paths.discard(path)
    take_notices()  # a fresh database never queues one, but be explicit
    return path


def _open(path: Path) -> None:
    _migrated_paths.discard(path)
    init_db(db_path=path)
    dispose_engine(path)


# ── pending_schema_version ────────────────────────────────────────────────────

class TestPendingSchemaVersion:
    def test_missing_file(self, tmp_path):
        assert pending_schema_version(tmp_path / "nope.db", SCHEMA_VERSION) is None
        assert not (tmp_path / "nope.db").exists()  # must not create it

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.db"
        f.touch()
        assert pending_schema_version(f, SCHEMA_VERSION) is None

    def test_database_without_tables(self, tmp_path):
        f = tmp_path / "bare.db"
        conn = sqlite3.connect(str(f))
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
        conn.close()
        assert pending_schema_version(f, SCHEMA_VERSION) is None

    def test_behind_current_schema(self, tmp_path):
        f = _make_old_db(tmp_path / "old.db")
        assert pending_schema_version(f, SCHEMA_VERSION) == OLD

    def test_pre_gate_database_with_user_version_zero(self, tmp_path):
        f = _make_old_db(tmp_path / "zero.db", version=0)
        assert pending_schema_version(f, SCHEMA_VERSION) == 0

    def test_already_current(self, tmp_path):
        f = _make_old_db(tmp_path / "cur.db", version=SCHEMA_VERSION)
        assert pending_schema_version(f, SCHEMA_VERSION) is None

    def test_newer_than_this_build(self, tmp_path):
        f = _make_old_db(tmp_path / "newer.db", version=SCHEMA_VERSION + 5)
        assert pending_schema_version(f, SCHEMA_VERSION) is None

    def test_not_a_database(self, tmp_path):
        f = tmp_path / "junk.db"
        f.write_bytes(b"not a database" * 100)
        assert pending_schema_version(f, SCHEMA_VERSION) is None


def test_backup_path_naming(tmp_path):
    assert backup_path_for(tmp_path / "My caches.db", 21) == (
        tmp_path / BACKUP_DIRNAME / "My caches.pre-migration-schema21.db"
    )


# ── init_db integration ──────────────────────────────────────────────────────

class TestInitDbMakesBackup:
    def test_backup_is_made_before_migrating(self, tmp_path):
        db = _make_old_db(tmp_path / "Main.db")
        _open(db)

        backup = backup_path_for(db, OLD)
        assert backup.is_file()
        assert _user_version(backup) == OLD           # the untouched original
        assert _user_version(db) == SCHEMA_VERSION    # the migrated database

    def test_backup_is_taken_before_create_all(self, tmp_path):
        # create_all() adds missing tables to an old database before the
        # migrations run — the backup must not already contain them.
        db = _make_old_db(tmp_path / "Main.db")
        conn = sqlite3.connect(str(db))
        conn.execute("DROP TABLE trackables")
        conn.commit()
        conn.close()

        _open(db)

        assert "trackables" in _tables(db)
        assert "trackables" not in _tables(backup_path_for(db, OLD))

    def test_backup_has_no_sidecars_and_queues_a_notice(self, tmp_path):
        db = _make_old_db(tmp_path / "Main.db")
        _open(db)

        backups = tmp_path / BACKUP_DIRNAME
        assert sorted(p.name for p in backups.iterdir()) == [
            f"Main.pre-migration-schema{OLD}.db"
        ]
        notices = take_notices()
        assert len(notices) == 1
        assert notices[0].db_path == db
        assert notices[0].backup_path == backup_path_for(db, OLD)
        assert notices[0].from_version == OLD
        assert take_notices() == []  # taking them clears the queue

    def test_new_database_gets_no_backup(self, tmp_path):
        _open(tmp_path / "Fresh.db")
        assert not (tmp_path / BACKUP_DIRNAME).exists()
        assert take_notices() == []

    def test_up_to_date_database_gets_no_backup(self, tmp_path):
        db = _make_old_db(tmp_path / "Main.db")
        _open(db)
        take_notices()
        (backup_path_for(db, OLD)).unlink()

        _open(db)  # second launch: already at SCHEMA_VERSION
        assert list((tmp_path / BACKUP_DIRNAME).iterdir()) == []
        assert take_notices() == []

    def test_existing_backup_is_kept(self, tmp_path):
        # e.g. an earlier attempt made the backup, then the migration failed
        # part way — the first backup is the cleanest and must not be replaced.
        db = _make_old_db(tmp_path / "Main.db")
        backup = backup_path_for(db, OLD)
        backup.parent.mkdir()
        backup.write_bytes(b"first backup")

        _open(db)

        assert backup.read_bytes() == b"first backup"
        assert _user_version(db) == SCHEMA_VERSION
        assert take_notices() == []


# ── best-effort: never blocks the migration ──────────────────────────────────

class TestBestEffort:
    def test_failed_backup_does_not_block_migration(self, tmp_path, caplog):
        db = _make_old_db(tmp_path / "Main.db")
        with patch.object(premigration, "snapshot_database",
                          side_effect=OSError("disk on fire")):
            with caplog.at_level(logging.WARNING, logger=premigration.__name__):
                _open(db)

        assert _user_version(db) == SCHEMA_VERSION
        assert take_notices() == []
        assert "migrating without a backup" in caplog.text

    def test_not_enough_disk_space_skips_backup(self, tmp_path, caplog):
        db = _make_old_db(tmp_path / "Main.db")
        usage = MagicMock(free=1024)
        with patch.object(premigration.shutil, "disk_usage", return_value=usage):
            with caplog.at_level(logging.WARNING, logger=premigration.__name__):
                _open(db)

        assert not backup_path_for(db, OLD).exists()
        assert _user_version(db) == SCHEMA_VERSION
        assert take_notices() == []
        assert "not enough free disk space" in caplog.text

    def test_backup_before_migration_never_raises(self, tmp_path):
        db = _make_old_db(tmp_path / "Main.db")
        with patch.object(premigration, "pending_schema_version",
                          side_effect=RuntimeError("unexpected")):
            assert backup_before_migration(db, SCHEMA_VERSION) is None


# ── GUI notice ────────────────────────────────────────────────────────────────

class TestMainWindowNotice:
    def test_no_notice_no_dialog(self, qapp):
        from opensak.gui import mainwindow
        with patch.object(mainwindow, "QMessageBox") as box:
            mainwindow.MainWindow.show_premigration_backup_notices(MagicMock())
        box.information.assert_not_called()

    def test_notice_shows_dialog_with_backup_path(self, qapp, tmp_path):
        from opensak.gui import mainwindow
        from opensak.lang import tr

        db = _make_old_db(tmp_path / "Main.db")
        _open(db)
        with patch.object(mainwindow, "QMessageBox") as box:
            mainwindow.MainWindow.show_premigration_backup_notices(MagicMock())
            mainwindow.MainWindow.show_premigration_backup_notices(MagicMock())

        box.information.assert_called_once()  # shown once, then cleared
        _parent, title, text = box.information.call_args.args
        assert title == tr("premigration_backup_title")
        assert text == tr(
            "premigration_backup_msg",
            file="Main.db", path=str(backup_path_for(db, OLD)),
        )

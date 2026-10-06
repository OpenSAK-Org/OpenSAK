"""
tests/unit-tests/test_premigration_backup_folder_988.py — pre-migration
backups go into the backup folder when one is set (issue #988, part of #942).

The "old database" is a current database with PRAGMA user_version rewound,
the same way test_premigration_backup_549.py does it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from opensak.backup import premigration
from opensak.backup.backupset import BACKUP_DIR_KEY, list_backup_sets, rotate
from opensak.backup.premigration import (
    BACKUP_DIRNAME,
    BACKUP_FOLDER_SUBDIR,
    backup_before_migration,
    backup_folder_path_for,
    backup_path_for,
    take_notices,
)
from opensak.db import db_settings
from opensak.db.database import SCHEMA_VERSION, _migrated_paths, dispose_engine, init_db
from opensak.settings_store import get_db_dir, get_store

OLD = SCHEMA_VERSION - 1


def _user_version(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _make_old_db(path: Path, version: int = OLD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
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
    take_notices()
    return path


def _open(path: Path) -> None:
    _migrated_paths.discard(path)
    init_db(db_path=path)
    dispose_engine(path)


def _give_uuid(path: Path) -> str:
    """Bind-time setup (#659) that gives a database its db_uuid."""
    db_settings.ensure_seeded(path, path.stem)
    return db_settings.read_file(path)[db_settings.UUID_KEY]


@pytest.fixture
def backup_folder(tmp_path):
    folder = tmp_path / "usb" / "OpenSAK Backups"
    folder.mkdir(parents=True)
    get_store().set(BACKUP_DIR_KEY, str(folder))
    return folder


class TestWhereTheCopyGoes:
    def test_into_the_backup_folder_when_one_is_set(self, tmp_path, backup_folder):
        db = _make_old_db(tmp_path / "dbs" / "Main.db")
        uuid = _give_uuid(db)
        _open(db)

        target = backup_folder_path_for(db, OLD, backup_folder)
        assert target.parent == backup_folder / BACKUP_FOLDER_SUBDIR
        assert target.name == f"Main.{uuid[:8]}.pre-migration-schema{OLD}.db"
        assert target.is_file()
        assert _user_version(target) == OLD
        assert not (db.parent / BACKUP_DIRNAME).exists()

        notices = take_notices()
        assert [n.backup_path for n in notices] == [target]

    def test_next_to_the_database_when_none_is_set(self, tmp_path):
        assert get_store().get(BACKUP_DIR_KEY) is None
        db = _make_old_db(tmp_path / "Main.db")
        _open(db)
        assert backup_path_for(db, OLD).is_file()

    def test_next_to_the_database_when_the_folder_is_not_connected(self, tmp_path):
        get_store().set(BACKUP_DIR_KEY, str(tmp_path / "USB-not-mounted" / "Backups"))
        db = _make_old_db(tmp_path / "Main.db")
        _open(db)
        assert backup_path_for(db, OLD).is_file()
        assert _user_version(db) == SCHEMA_VERSION  # the migration went ahead
        assert not (tmp_path / "USB-not-mounted").exists()

    def test_next_to_the_database_when_the_folder_is_unsuitable(self, tmp_path):
        # A folder inside the database folder is refused for backups (#952).
        unsuitable = get_db_dir() / "backups-here"
        unsuitable.mkdir(parents=True)
        get_store().set(BACKUP_DIR_KEY, str(unsuitable))
        db = _make_old_db(tmp_path / "Main.db")
        _open(db)
        assert backup_path_for(db, OLD).is_file()
        assert not (unsuitable / BACKUP_FOLDER_SUBDIR).exists()


class TestNames:
    def test_same_file_name_in_two_folders_gets_two_copies(self, tmp_path, backup_folder):
        a = _make_old_db(tmp_path / "a" / "Default.db")
        b = _make_old_db(tmp_path / "b" / "Default.db")
        _give_uuid(a)
        _open(a)
        _open(b)
        copies = sorted((backup_folder / BACKUP_FOLDER_SUBDIR).iterdir())
        assert len(copies) == 2
        assert copies[0].name != copies[1].name

    def test_database_without_a_uuid_gets_a_stable_path_hash(self, tmp_path, backup_folder):
        # A database from before #659 has no settings table and no db_uuid.
        db = _make_old_db(tmp_path / "Old.db")
        assert db_settings.read_file(db) == {}
        first = backup_folder_path_for(db, OLD, backup_folder)
        assert first == backup_folder_path_for(db, OLD, backup_folder)
        assert first.name.startswith("Old.") and len(first.name.split(".")[1]) == 8


class TestExistingCopy:
    def test_a_copy_next_to_the_database_is_not_made_twice(self, tmp_path):
        db = _make_old_db(tmp_path / "Main.db")
        beside = backup_path_for(db, OLD)
        beside.parent.mkdir()
        beside.write_bytes(b"earlier copy")

        folder = tmp_path / "Backups"
        folder.mkdir()
        get_store().set(BACKUP_DIR_KEY, str(folder))

        assert backup_before_migration(db, SCHEMA_VERSION) is None
        assert beside.read_bytes() == b"earlier copy"
        assert not (folder / BACKUP_FOLDER_SUBDIR).exists()

    def test_a_copy_in_the_backup_folder_is_not_made_twice(self, tmp_path, backup_folder):
        db = _make_old_db(tmp_path / "Main.db")
        target = backup_folder_path_for(db, OLD, backup_folder)
        target.parent.mkdir()
        target.write_bytes(b"earlier copy")
        assert backup_before_migration(db, SCHEMA_VERSION) is None
        assert target.read_bytes() == b"earlier copy"


class TestFreeSpaceAndRotation:
    def test_free_space_is_checked_on_the_backup_folder_disk(
        self, tmp_path, backup_folder, monkeypatch
    ):
        checked: list[Path] = []
        real = premigration.shutil.disk_usage

        def spy(path):
            checked.append(Path(path))
            return real(path)

        monkeypatch.setattr(premigration.shutil, "disk_usage", spy)
        db = _make_old_db(tmp_path / "dbs" / "Main.db")
        backup_before_migration(db, SCHEMA_VERSION)
        # pre-migration/ doesn't exist yet: its nearest existing parent counts.
        assert checked == [backup_folder]

    def test_rotation_never_touches_pre_migration(self, tmp_path, backup_folder):
        db = _make_old_db(tmp_path / "Main.db")
        _open(db)
        copy = backup_folder_path_for(db, OLD, backup_folder)
        assert copy.is_file()
        assert list_backup_sets(backup_folder) == []
        rotate(backup_folder, keep=1)
        assert copy.is_file()

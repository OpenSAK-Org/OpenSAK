"""
tests/unit-tests/test_backup_staging_1002.py — compressed backups stage
their databases outside the backup folder (issue #1002).

Reported on #942: with the backup folder inside OneDrive, a compressed backup
left an empty ``<set>.partial`` staging folder behind, and OneDrive started
uploading every staged database copy. Staging now happens in the install
folder's backup-staging folder, and removing it is retried.
"""

from __future__ import annotations

import logging
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from opensak.backup import backupset
from opensak.backup.backupset import (
    KIND_MANUAL,
    STAGING_DIRNAME,
    NotEnoughSpaceError,
    cleanup_staging,
    write_backup_set,
)
from opensak.config import get_app_data_dir

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)


def _make_db(path: Path, rows: int = 500) -> SimpleNamespace:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE caches (code TEXT PRIMARY KEY, notes TEXT)")
    conn.executemany(
        "INSERT INTO caches VALUES (?, ?)",
        [(f"GC{i:05d}", "A traditional cache near the old oak tree. " * 5)
         for i in range(rows)],
    )
    conn.commit()
    conn.close()
    return SimpleNamespace(name=path.stem, path=path)


@pytest.fixture
def dbs(tmp_path: Path) -> list[SimpleNamespace]:
    return [_make_db(tmp_path / "dbs" / "Default.db"),
            _make_db(tmp_path / "dbs" / "Denmark.db", rows=200)]


@pytest.fixture
def backups(tmp_path: Path) -> Path:
    return tmp_path / "backups"


@pytest.fixture
def staging() -> Path:
    return get_app_data_dir() / STAGING_DIRNAME


def _leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


# ── Where staging happens ─────────────────────────────────────────────────────

class TestStagingLocation:
    def test_databases_are_staged_outside_the_backup_folder(
        self, dbs, backups, staging, monkeypatch,
    ):
        staged_in: list[Path] = []
        real = backupset._zip_file

        def spy(zf, path, arcname, report):
            staged_in.append(path.parent.parent)   # <root>/<set>.partial/databases/x.db
            real(zf, path, arcname, report)

        monkeypatch.setattr(backupset, "_zip_file", spy)
        result = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW,
                                  compress=True)

        assert len(staged_in) == 2
        for set_partial in staged_in:
            assert set_partial.parent == staging
            assert not set_partial.is_relative_to(backups)
        # Only the finished zip ever lands in the backup folder …
        assert _leftovers(backups) == [result.backup_set.path.name]
        # … and the staging folder is left empty.
        assert _leftovers(staging) == []
        with zipfile.ZipFile(result.backup_set.path) as zf:
            assert zf.testzip() is None

    def test_cancel_leaves_nothing_in_either_folder(self, dbs, backups, staging):
        class Stop(Exception):
            pass

        def cancel(name, fraction, overall):
            if name == "Denmark":
                raise Stop()

        with pytest.raises(Stop):
            write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW,
                             compress=True, progress=cancel)
        assert _leftovers(backups) == []
        assert _leftovers(staging) == []

    def test_folder_sets_are_still_built_in_the_backup_folder(
        self, dbs, backups, staging,
    ):
        # A folder set is renamed into place, which is atomic only within
        # the backup folder — unchanged by #1002.
        result = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW,
                                  compress=False)
        assert result.backup_set.path.parent == backups
        assert _leftovers(staging) == []

    def test_falls_back_to_the_backup_folder_if_staging_is_unusable(
        self, dbs, backups, tmp_path, monkeypatch,
    ):
        blocker = tmp_path / "not-a-folder"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setattr(backupset, "_staging_dir", lambda: blocker / STAGING_DIRNAME)
        result = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW,
                                  compress=True)
        assert _leftovers(backups) == [result.backup_set.path.name]


# ── Leftovers ─────────────────────────────────────────────────────────────────

class TestLeftovers:
    def test_leftover_staging_folder_is_removed_at_the_next_backup(
        self, dbs, backups, staging,
    ):
        leftover = staging / "OpenSAK-backup-2026-10-06_1842-manual.partial"
        (leftover / "databases").mkdir(parents=True)
        stranger = staging / "keep-me"
        stranger.mkdir()

        write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW, compress=False)

        assert not leftover.exists()
        assert stranger.is_dir()

    def test_cleanup_staging_without_a_staging_folder(self, staging):
        assert not staging.exists()
        assert cleanup_staging() == []


# ── Removing the staging folder ───────────────────────────────────────────────

class TestRemoveTree:
    def test_removal_is_retried(self, tmp_path, monkeypatch):
        target = tmp_path / "OpenSAK-backup-2026-10-07_0900-manual.partial"
        target.mkdir()
        real_rmtree = backupset.shutil.rmtree
        calls: list[Path] = []

        def flaky(path, ignore_errors=False):
            calls.append(path)
            if len(calls) < 3:
                return            # held by a sync client: nothing removed
            real_rmtree(path, ignore_errors=ignore_errors)

        monkeypatch.setattr(backupset.shutil, "rmtree", flaky)
        monkeypatch.setattr(backupset.time, "sleep", lambda s: None)

        assert backupset._remove_tree(target) is True
        assert len(calls) == 3
        assert not target.exists()

    def test_gives_up_with_a_warning(self, tmp_path, monkeypatch, caplog):
        target = tmp_path / "OpenSAK-backup-2026-10-07_0900-manual.partial"
        target.mkdir()
        monkeypatch.setattr(backupset.shutil, "rmtree", lambda *a, **k: None)
        monkeypatch.setattr(backupset.time, "sleep", lambda s: None)

        with caplog.at_level(logging.WARNING, logger=backupset.logger.name):
            assert backupset._remove_tree(target) is False
        assert target.exists()
        assert "next backup" in caplog.text


# ── Free space ────────────────────────────────────────────────────────────────

class TestFreeSpace:
    def test_separate_volumes_are_checked_separately(
        self, dbs, backups, staging, monkeypatch,
    ):
        # Staging needs the largest database; the backup folder needs the
        # zip. Neither needs room for both when they are on different disks.
        total = sum(backupset._database_size(d.path) for d in dbs)
        largest = max(backupset._database_size(d.path) for d in dbs)
        monkeypatch.setattr(backupset, "_same_volume", lambda a, b: False)

        def usage(free_staging: float, free_backup: float):
            def _usage(path):
                p = Path(path)
                free = free_staging if p.is_relative_to(staging) else free_backup
                return SimpleNamespace(free=int(free))
            return _usage

        # Enough on each disk for its own part, not for both together.
        monkeypatch.setattr(backupset.shutil, "disk_usage",
                            usage(largest * 1.2, total * 0.7))
        write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW, compress=True)

        # Too little room to stage the largest database.
        monkeypatch.setattr(backupset.shutil, "disk_usage",
                            usage(largest * 0.5, total * 10))
        with pytest.raises(NotEnoughSpaceError, match=STAGING_DIRNAME):
            write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW, compress=True)

        # Too little room for the zip.
        monkeypatch.setattr(backupset.shutil, "disk_usage",
                            usage(total * 10, total * 0.1))
        with pytest.raises(NotEnoughSpaceError, match="backups"):
            write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW, compress=True)

    def test_same_volume_needs_room_for_both(self, dbs, backups, monkeypatch):
        total = sum(backupset._database_size(d.path) for d in dbs)
        largest = max(backupset._database_size(d.path) for d in dbs)
        monkeypatch.setattr(backupset, "_same_volume", lambda a, b: True)
        # Room for either part alone, not both.
        monkeypatch.setattr(backupset.shutil, "disk_usage",
                            lambda p: SimpleNamespace(free=int(largest * 1.2)))
        assert largest * 1.2 < largest * 1.10 + total * 0.6
        with pytest.raises(NotEnoughSpaceError):
            write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW, compress=True)

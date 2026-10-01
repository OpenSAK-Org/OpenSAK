"""
tests/unit-tests/test_backup_set_952.py — backup core: write, list and
rotate backup sets (issue #952, part of #942).

The root conftest isolates the install folder (and Path.home()) per test,
so settings and the default backup folder all live under tmp_path.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from opensak.backup import backupset
from opensak.backup.backupset import (
    BACKUP_DIR_KEY,
    KEEP_AUTO_KEY,
    KIND_AUTO,
    KIND_MANUAL,
    BackupError,
    cleanup_partials,
    default_backup_dir,
    get_backup_dir,
    get_keep_auto,
    list_backup_sets,
    read_backup_set,
    rotate,
    set_backup_dir,
    validate_backup_dir,
    write_backup_set,
)
from opensak.db.db_settings import legacy_db_key, read_file
from opensak.settings_store import get_install_dir, get_store

NOW = datetime(2026, 10, 1, 16, 30, 12, tzinfo=timezone.utc)


def _set_name(now: datetime, kind: str) -> str:
    return f"OpenSAK-backup-{now.astimezone():%Y-%m-%d_%H%M}-{kind}"


def _make_db(path: Path, rows: int = 50, version: int = 24) -> SimpleNamespace:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE caches (code TEXT PRIMARY KEY)")
    conn.executemany("INSERT INTO caches VALUES (?)",
                     [(f"GC{i:05d}",) for i in range(rows)])
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    conn.close()
    return SimpleNamespace(name=path.stem, path=path)


def _rows(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT count(*) FROM caches").fetchone()[0]
    finally:
        conn.close()


def _names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


@pytest.fixture
def backups(tmp_path: Path) -> Path:
    return tmp_path / "backups"


@pytest.fixture
def dbs(tmp_path: Path) -> list[SimpleNamespace]:
    return [_make_db(tmp_path / "dbs" / "Default.db"),
            _make_db(tmp_path / "dbs" / "Denmark.db", rows=20)]


@pytest.fixture
def install() -> Path:
    """The (isolated) install folder with settings, credentials and a log."""
    d = get_install_dir()
    get_store().set("display.font_size", 11)          # writes opensak.json
    (d / "filters").mkdir(exist_ok=True)
    (d / "filters" / "Unfound.json").write_text("{}", encoding="utf-8")
    (d / "column_views").mkdir(exist_ok=True)
    (d / "column_views" / "Compact.json").write_text("{}", encoding="utf-8")
    (d / "icons").mkdir(exist_ok=True)
    (d / "icons" / "traditional.svg").write_text("<svg/>", encoding="utf-8")
    (d / "gc_token.json").write_text('{"token": "secret"}', encoding="utf-8")
    (d / "opensak.log").write_text("log", encoding="utf-8")
    (d / "bootstrap.json").write_text("{}", encoding="utf-8")
    return d


# ── Writing a set ─────────────────────────────────────────────────────────────

class TestWriteSet:
    def test_layout_and_manifest(self, backups, dbs, install):
        result = write_backup_set(dbs, KIND_AUTO, folder=backups, now=NOW)

        s = result.backup_set
        assert s.path == backups / _set_name(NOW, KIND_AUTO)
        assert _names(backups) == [s.path.name]
        assert _names(s.path) == ["databases", "manifest.json", "settings"]
        assert s.kind == KIND_AUTO
        assert s.created == NOW.replace(microsecond=0)
        assert s.settings is True
        assert result.skipped == ()

        assert [e.name for e in s.databases] == ["Default", "Denmark"]
        for entry in s.databases:
            copy = s.path / entry.file
            assert copy.is_file()
            assert entry.size_bytes == copy.stat().st_size
            assert entry.schema_version == 24
            assert entry.db_uuid == read_file(copy)["db_uuid"]

    def test_manifest_on_disk_is_plain_json(self, backups, dbs, install):
        s = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW).backup_set
        data = json.loads((s.path / "manifest.json").read_text(encoding="utf-8"))
        assert data["format"] == 1
        assert data["kind"] == "manual"
        assert data["created"] == "2026-10-01T16:30:12Z"
        assert data["databases"][0]["file"] == "databases/Default.db"

    def test_includes_changes_still_in_the_wal(self, backups, tmp_path, install):
        path = tmp_path / "dbs" / "Open.db"
        path.parent.mkdir(parents=True)
        writer = sqlite3.connect(str(path))
        try:
            writer.execute("PRAGMA journal_mode = WAL")
            writer.execute("PRAGMA wal_autocheckpoint = 0")
            writer.execute("CREATE TABLE caches (code TEXT PRIMARY KEY)")
            writer.executemany("INSERT INTO caches VALUES (?)",
                               [(f"GC{i:05d}",) for i in range(500)])
            writer.commit()
            assert Path(str(path) + "-wal").stat().st_size > 0
            s = write_backup_set([SimpleNamespace(name="Open", path=path)],
                                 KIND_AUTO, folder=backups, now=NOW).backup_set
        finally:
            writer.close()
        assert _rows(s.path / "databases" / "Open.db") == 500

    def test_every_copy_passes_integrity_check(self, backups, dbs, install):
        s = write_backup_set(dbs, KIND_AUTO, folder=backups, now=NOW).backup_set
        for entry in s.databases:
            conn = sqlite3.connect(str(s.path / entry.file))
            try:
                assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            finally:
                conn.close()

    def test_database_settings_travel_with_the_copy(self, backups, dbs, install):
        # #659: a database not opened since the upgrade still has its home
        # in opensak.json — the backup imports it into the file first.
        get_store().set(legacy_db_key(dbs[0].path, "home_lat"), 55.7)
        s = write_backup_set(dbs, KIND_AUTO, folder=backups, now=NOW).backup_set
        assert read_file(s.path / "databases" / "Default.db")["home_lat"] == 55.7

    def test_settings_are_copied_but_never_credentials_or_logs(
        self, backups, dbs, install
    ):
        s = write_backup_set(dbs, KIND_AUTO, folder=backups, now=NOW).backup_set
        settings = s.path / "settings"
        assert _names(settings) == ["column_views", "filters", "icons", "opensak.json"]
        assert (settings / "filters" / "Unfound.json").is_file()
        assert (settings / "column_views" / "Compact.json").is_file()
        assert (settings / "icons" / "traditional.svg").is_file()
        assert json.loads((settings / "opensak.json").read_text(
            encoding="utf-8"))["display.font_size"] == 11
        everything = {p.name for p in s.path.rglob("*")}
        assert not everything & {"gc_token.json", "opensak.log", "bootstrap.json"}

    def test_missing_database_is_skipped_and_the_rest_completes(
        self, backups, dbs, tmp_path, install
    ):
        ghost = SimpleNamespace(name="Ghost", path=tmp_path / "gone" / "Ghost.db")
        result = write_backup_set([dbs[0], ghost, dbs[1]], KIND_AUTO,
                                  folder=backups, now=NOW)
        assert result.skipped == ("Ghost",)
        assert [e.name for e in result.backup_set.databases] == ["Default", "Denmark"]

    def test_same_file_name_from_two_folders(self, backups, tmp_path, install):
        a = _make_db(tmp_path / "a" / "Default.db")
        b = _make_db(tmp_path / "b" / "Default.db", rows=7)
        s = write_backup_set([a, b], KIND_AUTO, folder=backups, now=NOW).backup_set
        assert [e.file for e in s.databases] == ["databases/Default.db",
                                                 "databases/Default-2.db"]
        assert _rows(s.path / "databases" / "Default-2.db") == 7

    def test_name_clash_in_the_same_minute(self, backups, dbs, install):
        first = write_backup_set(dbs, KIND_AUTO, folder=backups, now=NOW).backup_set
        second = write_backup_set(dbs, KIND_AUTO, folder=backups,
                                  now=NOW + timedelta(seconds=20)).backup_set
        assert first.path.name == _set_name(NOW, KIND_AUTO)
        assert second.path.name == _set_name(NOW, KIND_AUTO) + "-2"

    def test_settings_without_any_settings_files(self, backups, dbs):
        # A fresh install with nothing saved yet still gets a settings folder.
        s = write_backup_set(dbs, KIND_AUTO, folder=backups, now=NOW).backup_set
        assert (s.path / "settings").is_dir()

    def test_invalid_arguments(self, backups, dbs):
        with pytest.raises(ValueError):
            write_backup_set(dbs, "weekly", folder=backups, now=NOW)
        with pytest.raises(ValueError):
            write_backup_set(dbs, KIND_AUTO, folder=backups,
                             now=datetime(2026, 10, 1, 16, 30))  # naive
        assert not backups.exists()

    def test_uses_the_configured_backup_folder(self, tmp_path, dbs, install):
        folder = tmp_path / "chosen"
        set_backup_dir(folder)
        s = write_backup_set(dbs, KIND_MANUAL, now=NOW).backup_set
        assert s.path.parent == folder


# ── Never half a backup ───────────────────────────────────────────────────────

class TestNothingLeftBehind:
    def test_cancel_from_progress_leaves_nothing(self, backups, dbs, install):
        earlier = write_backup_set(dbs, KIND_AUTO, folder=backups,
                                   now=NOW - timedelta(days=1)).backup_set
        before = _names(backups)

        def cancel(name: str, db_fraction: float, overall: float) -> None:
            if name == "Denmark":
                raise RuntimeError("cancelled")

        with pytest.raises(RuntimeError, match="cancelled"):
            write_backup_set(dbs, KIND_AUTO, cancel, folder=backups, now=NOW)
        assert _names(backups) == before
        assert read_backup_set(earlier.path) == earlier  # untouched

    def test_snapshot_failure_raises_backup_error_and_leaves_nothing(
        self, backups, dbs, install
    ):
        from opensak.backup.snapshot import SnapshotError
        with patch.object(backupset, "snapshot_database",
                          side_effect=SnapshotError("disk on fire")):
            with pytest.raises(BackupError, match="disk on fire"):
                write_backup_set(dbs, KIND_AUTO, folder=backups, now=NOW)
        assert _names(backups) == []

    def test_not_enough_free_space(self, backups, dbs, install):
        with patch.object(backupset.shutil, "disk_usage",
                          return_value=MagicMock(free=10)):
            with pytest.raises(BackupError, match="Not enough free space"):
                write_backup_set(dbs, KIND_AUTO, folder=backups, now=NOW)
        assert _names(backups) == []

    def test_unsuitable_folder_is_refused(self, dbs, install):
        with pytest.raises(BackupError, match="install folder"):
            write_backup_set(dbs, KIND_AUTO, folder=install / "backups", now=NOW)
        assert not (install / "backups").exists()


# ── Progress ──────────────────────────────────────────────────────────────────

class TestProgress:
    def test_reports_per_database_and_overall(self, backups, dbs, install):
        calls: list[tuple[str, float, float]] = []
        write_backup_set(dbs, KIND_AUTO, lambda *a: calls.append(a),
                         folder=backups, now=NOW)
        assert {name for name, _, _ in calls} == {"Default", "Denmark"}
        overall = [o for _, _, o in calls]
        assert overall == sorted(overall)
        assert overall[-1] == pytest.approx(1.0)
        assert calls[-1][1] == pytest.approx(1.0)
        for name in ("Default", "Denmark"):
            assert max(f for n, f, _ in calls if n == name) == pytest.approx(1.0)


# ── Listing and reading ───────────────────────────────────────────────────────

class TestListAndRead:
    def test_newest_first(self, backups, dbs, install):
        old = write_backup_set(dbs, KIND_AUTO, folder=backups,
                               now=NOW - timedelta(days=2)).backup_set
        new = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW).backup_set
        assert [s.path for s in list_backup_sets(backups)] == [new.path, old.path]

    def test_ignores_everything_that_is_not_a_complete_set(
        self, backups, dbs, install
    ):
        good = write_backup_set(dbs, KIND_AUTO, folder=backups, now=NOW).backup_set
        (backups / (_set_name(NOW, KIND_MANUAL) + ".partial")).mkdir()
        (backups / _set_name(NOW - timedelta(days=1), KIND_AUTO)).mkdir()  # no manifest
        (backups / "pre-migration").mkdir()
        (backups / "My own notes").mkdir()
        (backups / "notes.txt").write_text("x", encoding="utf-8")
        broken = backups / _set_name(NOW - timedelta(days=3), KIND_AUTO)
        broken.mkdir()
        (broken / "manifest.json").write_text("{not json", encoding="utf-8")
        assert [s.path for s in list_backup_sets(backups)] == [good.path]

    def test_set_from_a_newer_version_is_refused(self, backups, dbs, install):
        s = write_backup_set(dbs, KIND_AUTO, folder=backups, now=NOW).backup_set
        manifest = s.path / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["format"] = 99
        manifest.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(BackupError, match="newer version"):
            read_backup_set(s.path)
        assert list_backup_sets(backups) == []

    def test_missing_folder_lists_nothing(self, tmp_path):
        assert list_backup_sets(tmp_path / "nowhere") == []


# ── Rotation ──────────────────────────────────────────────────────────────────

class TestRotation:
    def _sets(self, backups, dbs, kinds):
        """One set per kind, a day apart, oldest first."""
        return [
            write_backup_set(dbs, kind, folder=backups,
                             now=NOW - timedelta(days=len(kinds) - i)).backup_set
            for i, kind in enumerate(kinds)
        ]

    def test_keeps_the_newest_auto_sets_and_every_manual_set(
        self, backups, dbs, install
    ):
        made = self._sets(backups, dbs, [KIND_AUTO] * 3 + [KIND_MANUAL]
                          + [KIND_AUTO] * 4 + [KIND_MANUAL])
        autos = [s for s in made if s.kind == KIND_AUTO]       # 7, oldest first
        manuals = [s for s in made if s.kind == KIND_MANUAL]

        deleted = rotate(backups, keep=5)

        assert sorted(deleted) == sorted(s.path for s in autos[:2])
        remaining = {s.path for s in list_backup_sets(backups)}
        assert remaining == {s.path for s in autos[2:]} | {s.path for s in manuals}

    def test_never_touches_other_folders(self, backups, dbs, install):
        self._sets(backups, dbs, [KIND_AUTO] * 3)
        for name in ("pre-migration", "My own notes"):
            (backups / name).mkdir()
        rotate(backups, keep=1)
        assert {"pre-migration", "My own notes"} <= set(_names(backups))

    def test_cleans_up_leftover_partial_folders(self, backups, dbs, install):
        self._sets(backups, dbs, [KIND_AUTO])
        leftover = backups / (_set_name(NOW, KIND_AUTO) + ".partial")
        leftover.mkdir()
        rotate(backups, keep=5)
        assert not leftover.exists()

    def test_default_keep_comes_from_the_setting(self, backups, dbs, install):
        self._sets(backups, dbs, [KIND_AUTO] * 4)
        get_store().set(KEEP_AUTO_KEY, 2)
        rotate(backups)
        assert len(list_backup_sets(backups)) == 2

    def test_keep_is_at_least_one(self, backups, dbs, install):
        self._sets(backups, dbs, [KIND_AUTO] * 2)
        rotate(backups, keep=0)
        assert len(list_backup_sets(backups)) == 1
        get_store().set(KEEP_AUTO_KEY, -3)
        assert get_keep_auto() == 1
        get_store().set(KEEP_AUTO_KEY, "garbage")
        assert get_keep_auto() == 5


def test_cleanup_partials_only_removes_our_own(tmp_path):
    ours = tmp_path / (_set_name(NOW, KIND_AUTO) + ".partial")
    theirs = tmp_path / "something.partial"
    ours.mkdir()
    theirs.mkdir()
    assert cleanup_partials(tmp_path) == [ours]
    assert theirs.exists()


# ── Backup folder ─────────────────────────────────────────────────────────────

class TestBackupFolder:
    def test_default_is_documents(self, monkeypatch):
        monkeypatch.setattr(backupset.sys, "platform", "win32")
        assert default_backup_dir() == Path.home() / "Documents" / "OpenSAK Backups"

    def test_linux_honours_localized_documents_folder(self, monkeypatch, tmp_path):
        monkeypatch.setattr(backupset.sys, "platform", "linux")
        config = Path(tmp_path / "home" / ".config")
        config.mkdir(parents=True, exist_ok=True)
        (config / "user-dirs.dirs").write_text(
            '# written by xdg-user-dirs-update\nXDG_DOCUMENTS_DIR="$HOME/Dokumenter"\n',
            encoding="utf-8",
        )
        assert default_backup_dir() == Path.home() / "Dokumenter" / "OpenSAK Backups"

    def test_linux_without_user_dirs_falls_back(self, monkeypatch):
        monkeypatch.setattr(backupset.sys, "platform", "linux")
        assert default_backup_dir() == Path.home() / "Documents" / "OpenSAK Backups"

    def test_linux_documents_disabled_falls_back(self, monkeypatch, tmp_path):
        monkeypatch.setattr(backupset.sys, "platform", "linux")
        config = Path(tmp_path / "home" / ".config")
        config.mkdir(parents=True, exist_ok=True)
        (config / "user-dirs.dirs").write_text('XDG_DOCUMENTS_DIR="$HOME/"\n',
                                               encoding="utf-8")
        assert default_backup_dir() == Path.home() / "Documents" / "OpenSAK Backups"

    def test_get_backup_dir_uses_setting_else_default(self, tmp_path):
        assert get_backup_dir() == default_backup_dir()
        set_backup_dir(tmp_path / "usb" / "OpenSAK")
        assert get_store().get(BACKUP_DIR_KEY) == str(tmp_path / "usb" / "OpenSAK")
        assert get_backup_dir() == tmp_path / "usb" / "OpenSAK"

    def test_inside_install_folder_is_refused(self):
        with pytest.raises(BackupError, match="install folder"):
            validate_backup_dir(get_install_dir() / "backups")
        with pytest.raises(BackupError):
            validate_backup_dir(get_install_dir())

    def test_inside_database_folder_is_refused(self, tmp_path):
        db_dir = tmp_path / "my databases"
        get_store().set("databases.dir", str(db_dir))
        with pytest.raises(BackupError, match="database folder"):
            set_backup_dir(db_dir / "backups")
        assert get_store().get(BACKUP_DIR_KEY) is None

    def test_folder_elsewhere_is_fine(self, tmp_path):
        validate_backup_dir(tmp_path / "external" / "OpenSAK Backups")

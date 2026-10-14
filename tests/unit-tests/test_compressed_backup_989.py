"""
tests/unit-tests/test_compressed_backup_989.py — optional compressed backups
(issue #989, part of #942). Off by default; when on, a backup set is one zip
file with the same layout inside as a folder set.
"""

from __future__ import annotations

import sqlite3
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from opensak.backup import backupset
from opensak.backup.backupset import (
    COMPRESS_KEY,
    KIND_AUTO,
    KIND_MANUAL,
    MANIFEST_NAME,
    BackupError,
    NotEnoughSpaceError,
    cleanup_partials,
    get_compress,
    is_compressed_set_file,
    list_backup_sets,
    read_backup_set,
    rotate,
    write_backup_set,
)
from opensak.backup.restore import (
    RestoreError,
    restore_database,
    restore_missing_filter_profiles,
)
from opensak.config import get_app_data_dir
from opensak.db.database import dispose_engine
from opensak.lang import load_language
from opensak.settings_store import get_db_dir, get_store

NOW = datetime(2026, 10, 6, 18, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 6)


@pytest.fixture(autouse=True)
def english():
    load_language("en")


def _make_db(path: Path, rows: int = 2000) -> SimpleNamespace:
    """A database with repetitive text, which compresses well."""
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


def _rows(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT count(*) FROM caches").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def backups(tmp_path: Path) -> Path:
    return tmp_path / "backups"


@pytest.fixture
def dbs(tmp_path: Path) -> list[SimpleNamespace]:
    return [_make_db(tmp_path / "dbs" / "Default.db"),
            _make_db(tmp_path / "dbs" / "Denmark.db", rows=500)]


@pytest.fixture
def profiles() -> Path:
    d = get_app_data_dir() / "filters"
    d.mkdir(parents=True, exist_ok=True)
    (d / "Unfound.json").write_text('{"v": 1}', encoding="utf-8")
    return d


# ── Setting ───────────────────────────────────────────────────────────────────

class TestSetting:
    def test_off_by_default(self):
        assert get_compress() is False

    def test_default_backup_is_a_folder(self, dbs, backups):
        result = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW)
        assert result.backup_set.path.is_dir()
        assert result.backup_set.compressed is False

    def test_turning_it_on_compresses_new_backups(self, dbs, backups):
        get_store().set(COMPRESS_KEY, True)
        result = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW)
        assert result.backup_set.path.is_file()
        assert result.backup_set.compressed is True


# ── Writing ───────────────────────────────────────────────────────────────────

class TestWrite:
    def test_one_zip_with_the_same_layout(self, dbs, backups, profiles):
        result = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW,
                                  compress=True)
        path = result.backup_set.path
        assert path.parent == backups
        assert path.name.endswith("-manual.zip")
        assert is_compressed_set_file(path)
        assert sorted(p.name for p in backups.iterdir()) == [path.name]

        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            assert zf.testzip() is None
        assert {MANIFEST_NAME, "databases/Default.db", "databases/Denmark.db",
                "settings/filters/Unfound.json"} <= names
        assert [d.name for d in result.backup_set.databases] == ["Default", "Denmark"]

    def test_it_is_smaller(self, dbs, backups):
        plain = write_backup_set(dbs, KIND_MANUAL, folder=backups / "a", now=NOW,
                                 compress=False).backup_set
        packed = write_backup_set(dbs, KIND_MANUAL, folder=backups / "b", now=NOW,
                                  compress=True).backup_set
        plain_size = sum(p.stat().st_size for p in plain.path.rglob("*") if p.is_file())
        assert packed.path.stat().st_size < plain_size / 2

    def test_manifest_is_the_same_as_for_a_folder_set(self, dbs, backups):
        plain = write_backup_set(dbs, KIND_AUTO, folder=backups / "a", now=NOW,
                                 compress=False).backup_set
        packed = write_backup_set(dbs, KIND_AUTO, folder=backups / "b", now=NOW,
                                  compress=True).backup_set
        assert packed.databases == plain.databases
        assert (packed.kind, packed.created, packed.settings) == (
            plain.kind, plain.created, plain.settings)

    def test_progress_covers_both_steps_and_ends_at_one(self, dbs, backups):
        seen: list[tuple[str, float, float]] = []
        write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW, compress=True,
                         progress=lambda n, f, o: seen.append((n, f, o)))
        overall = [o for _n, _f, o in seen]
        assert overall == sorted(overall)
        assert overall[-1] == pytest.approx(1.0)
        # Each database reports both the snapshot half and the zip half.
        for name in ("Default", "Denmark"):
            fractions = [f for n, f, _o in seen if n == name]
            assert any(f <= 0.5 for f in fractions) and fractions[-1] == 1.0

    def test_cancel_leaves_nothing_behind(self, dbs, backups):
        class Stop(Exception):
            pass

        def cancel(name, fraction, overall):
            if name == "Denmark":
                raise Stop()

        with pytest.raises(Stop):
            write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW,
                             compress=True, progress=cancel)
        assert list(backups.iterdir()) == []

    def test_staging_holds_one_database_at_a_time(self, dbs, backups, monkeypatch):
        staged: list[list[str]] = []
        real = backupset._zip_file

        def spy(zf, path, arcname, report):
            staged.append(sorted(p.name for p in path.parent.iterdir()))
            real(zf, path, arcname, report)

        monkeypatch.setattr(backupset, "_zip_file", spy)
        write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW, compress=True)
        assert staged == [["Default.db"], ["Denmark.db"]]

    def test_free_space_needed_while_writing(self, tmp_path, backups, monkeypatch):
        # A compressed set needs room for the zip plus one database staged
        # uncompressed at a time. With several databases that is less than a
        # folder set needs; with one big database it is more.
        many = [_make_db(tmp_path / "dbs" / f"Db{i}.db", rows=500) for i in range(4)]
        total = sum(backupset._database_size(d.path) for d in many)
        monkeypatch.setattr(backupset.shutil, "disk_usage",
                            lambda p: SimpleNamespace(free=int(total * 0.95)))
        with pytest.raises(NotEnoughSpaceError):
            write_backup_set(many, KIND_MANUAL, folder=backups, now=NOW, compress=False)
        write_backup_set(many, KIND_MANUAL, folder=backups, now=NOW, compress=True)

        one = [_make_db(tmp_path / "big" / "Big.db", rows=2000)]
        size = backupset._database_size(one[0].path)
        monkeypatch.setattr(backupset.shutil, "disk_usage",
                            lambda p: SimpleNamespace(free=int(size * 1.2)))
        write_backup_set(one, KIND_MANUAL, folder=backups, now=NOW, compress=False)
        with pytest.raises(NotEnoughSpaceError):
            write_backup_set(one, KIND_MANUAL, folder=backups, now=NOW, compress=True)

    def test_a_failure_while_zipping_leaves_nothing(self, dbs, backups, monkeypatch):
        def broken(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(backupset, "_zip_file", broken)
        with pytest.raises(BackupError, match="disk full"):
            write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW, compress=True)
        assert list(backups.iterdir()) == []

    def test_two_backups_in_the_same_minute_get_unique_names(self, dbs, backups):
        a = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW, compress=True)
        b = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW, compress=False)
        c = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW, compress=True)
        names = {a.backup_set.path.name, b.backup_set.path.name, c.backup_set.path.name}
        assert len(names) == 3


# ── Listing, rotation, cleanup ────────────────────────────────────────────────

class TestListAndRotate:
    def test_folders_and_zips_are_listed_together(self, dbs, backups):
        write_backup_set(dbs, KIND_AUTO, folder=backups, now=NOW, compress=False)
        write_backup_set(dbs, KIND_AUTO, folder=backups,
                         now=NOW + timedelta(hours=1), compress=True)
        sets = list_backup_sets(backups)
        assert [s.compressed for s in sets] == [True, False]   # newest first

    def test_rotation_counts_zips_and_folders_alike(self, dbs, backups):
        for i, compress in enumerate([True, False, True, False]):
            write_backup_set(dbs, KIND_AUTO, folder=backups,
                             now=NOW + timedelta(hours=i), compress=compress)
        manual = write_backup_set(dbs, KIND_MANUAL, folder=backups,
                                  now=NOW, compress=True).backup_set
        deleted = rotate(backups, keep=2)
        assert len(deleted) == 2
        left = list_backup_sets(backups)
        assert [s.kind for s in left].count(KIND_AUTO) == 2
        assert manual.path.exists()

    def test_leftover_zip_partial_is_cleaned_up(self, backups):
        backups.mkdir()
        leftover = backups / "OpenSAK-backup-2026-10-06_1800-auto.zip.partial"
        leftover.write_bytes(b"half")
        stranger = backups / "holiday.zip.partial"
        stranger.write_bytes(b"not ours")
        assert cleanup_partials(backups) == [leftover]
        assert stranger.exists()

    def test_a_damaged_zip_is_ignored(self, backups):
        backups.mkdir()
        (backups / "OpenSAK-backup-2026-10-06_1800-auto.zip").write_bytes(b"junk")
        assert list_backup_sets(backups) == []
        with pytest.raises(BackupError):
            read_backup_set(backups / "OpenSAK-backup-2026-10-06_1800-auto.zip")

    def test_other_zips_are_not_sets(self, backups):
        backups.mkdir()
        with zipfile.ZipFile(backups / "holiday.zip", "w") as zf:
            zf.writestr(MANIFEST_NAME, "{}")
        assert list_backup_sets(backups) == []


# ── Restore ───────────────────────────────────────────────────────────────────

@pytest.fixture
def manager(qapp, tmp_path):
    from opensak.db.manager import get_db_manager
    m = get_db_manager()
    m.new_database("Trip", tmp_path / "dbs" / "Trip.db")
    dispose_engine(tmp_path / "dbs" / "Trip.db")
    yield m
    dispose_engine()


@pytest.fixture
def packed(manager, tmp_path, profiles):
    trip = next(db for db in manager.databases if db.name == "Trip")
    return write_backup_set([trip], KIND_MANUAL, folder=tmp_path / "backups",
                            compress=True).backup_set


def _left_in_db_folder() -> list[str]:
    return sorted(p.name for p in get_db_dir().iterdir()
                  if "restored" in p.name or p.name.endswith(".unpacking"))


class TestRestore:
    def test_restores_from_a_zip(self, manager, packed):
        info = restore_database(packed, packed.databases[0], today=TODAY)
        assert info.name == "Trip (restored 2026-10-06)"
        assert info.path.is_file()
        assert not list(get_db_dir().glob("*.unpacking"))

    def test_progress_runs_through_unpack_and_copy(self, manager, packed):
        seen: list[float] = []
        restore_database(packed, packed.databases[0],
                         lambda d, t: seen.append(d / t if t else 1.0), today=TODAY)
        assert seen and seen == sorted(seen)
        assert any(f <= 0.5 for f in seen) and seen[-1] == pytest.approx(1.0)

    def test_cancel_while_unpacking_leaves_nothing(self, manager, packed):
        class Stop(Exception):
            pass

        def cancel(done, total):
            raise Stop()

        with pytest.raises(Stop):
            restore_database(packed, packed.databases[0], cancel, today=TODAY)
        assert _left_in_db_folder() == []
        assert all("restored" not in db.name for db in manager.databases)

    def test_not_enough_space_to_unpack(self, manager, packed, monkeypatch):
        from opensak.backup import restore as restore_mod
        monkeypatch.setattr(restore_mod.shutil, "disk_usage",
                            lambda p: SimpleNamespace(free=1024))
        with pytest.raises(RestoreError, match="Not enough free space"):
            restore_database(packed, packed.databases[0], today=TODAY)
        assert _left_in_db_folder() == []

    def test_member_outside_the_set_is_refused(self, manager, packed):
        from dataclasses import replace
        bad = replace(packed.databases[0], file="../evil.db")
        with pytest.raises(RestoreError, match="Invalid file name"):
            restore_database(packed, bad, today=TODAY)

    def test_missing_member(self, manager, packed):
        from dataclasses import replace
        gone = replace(packed.databases[0], file="databases/Nope.db")
        with pytest.raises(RestoreError, match="missing"):
            restore_database(packed, gone, today=TODAY)

    def test_missing_filter_profiles_come_back(self, packed, profiles):
        (profiles / "Unfound.json").unlink()
        assert restore_missing_filter_profiles(packed) == ["Unfound"]
        assert (profiles / "Unfound.json").read_text(encoding="utf-8") == '{"v": 1}'

    def test_existing_filter_profiles_are_left_alone(self, packed, profiles):
        (profiles / "Unfound.json").write_text("changed", encoding="utf-8")
        assert restore_missing_filter_profiles(packed) == []
        assert (profiles / "Unfound.json").read_text(encoding="utf-8") == "changed"


# ── Dialogs ───────────────────────────────────────────────────────────────────

class TestDialogs:
    def test_restore_dialog_opens_a_zip_directly(self, qtbot, manager, packed):
        from opensak.gui.dialogs.restore_dialog import RestoreDialog
        d = RestoreDialog()
        qtbot.addWidget(d)
        d.load(packed.path)
        assert [s.path for s in d._sets] == [packed.path]

    def test_restore_dialog_lists_zips_in_a_folder(self, qtbot, manager, packed):
        from opensak.gui.dialogs.restore_dialog import RestoreDialog
        d = RestoreDialog()
        qtbot.addWidget(d)
        d.load(packed.path.parent)
        assert d._set_list.count() == 1

    def test_settings_checkbox_off_by_default_and_saved(self, qtbot, monkeypatch):
        from opensak.gui.dialogs import settings_dialog as sd
        from opensak.gui.settings import AppSettings
        s = AppSettings()
        monkeypatch.setattr(sd, "get_settings", lambda: s)
        monkeypatch.setattr("opensak.gui.settings.get_settings", lambda: s)
        monkeypatch.setattr("opensak.api.geocaching.is_logged_in", lambda: False)
        d = sd.SettingsDialog()
        qtbot.addWidget(d)
        assert not d._backup_compress_cb.isChecked()
        d._save()
        assert get_store().get(COMPRESS_KEY) is None   # unchanged: not written
        d2 = sd.SettingsDialog()
        qtbot.addWidget(d2)
        d2._backup_compress_cb.setChecked(True)
        d2._save()
        assert get_compress() is True

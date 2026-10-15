"""tests/unit-tests/test_vacuum_1033.py — VACUUM after migration 26 (#1033, #1048).

The migration-26 rebuild leaves the old pages on the free list. maintenance.vacuum()
gives them back, but only when it pays off and the disk has room, and it never
turns a successful migration into a failed one.
"""

import sqlite3
from collections import namedtuple
from functools import partial
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import MetaData, text

from opensak.db import database, maintenance
from opensak.db.database import SCHEMA_VERSION, _make_engine, _run_migrations
from opensak.db.models import Base

_Usage = namedtuple("_Usage", "total used free")
_MB = 1024 * 1024


def _bloated_db(path: Path, rows: int = 4000) -> None:
    """A WAL database with most of its pages on the free list."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, blob TEXT)")
    conn.executemany("INSERT INTO t (blob) VALUES (?)", [("x" * 2000,)] * rows)
    conn.commit()
    conn.execute("DELETE FROM t WHERE id > 100")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


def _size(path: Path) -> int:
    wal = Path(f"{path}-wal")
    return path.stat().st_size + (wal.stat().st_size if wal.exists() else 0)


# ── When it pays off ─────────────────────────────────────────────────────────

def test_is_worthwhile_needs_both_thresholds():
    page = 4096
    big_share_small_size = maintenance.PageStats(page, 1000, 500)        # 2 MB free, 50 %
    big_size_small_share = maintenance.PageStats(page, 1_000_000, 20_000)  # 78 MB, 2 %
    both = maintenance.PageStats(page, 100_000, 50_000)                  # 195 MB, 50 %
    assert not maintenance.is_worthwhile(big_share_small_size)
    assert not maintenance.is_worthwhile(big_size_small_share)
    assert maintenance.is_worthwhile(both)
    assert not maintenance.is_worthwhile(maintenance.PageStats(page, 0, 0))


def test_fresh_database_is_skipped(tmp_path):
    db = tmp_path / "fresh.db"
    engine = _make_engine(db)
    Base.metadata.create_all(engine)
    engine.dispose()
    result = maintenance.vacuum_file(db)
    assert result.status == maintenance.NOT_WORTHWHILE
    assert not result.ran


# ── Running it ───────────────────────────────────────────────────────────────

def test_vacuum_shrinks_and_truncates_wal(tmp_path):
    db = tmp_path / "bloated.db"
    _bloated_db(db)
    before = _size(db)
    called = []
    result = maintenance.vacuum_file(
        db, min_bytes=0, min_fraction=0.5, progress=lambda: called.append(1)
    )
    assert result.status == maintenance.DONE, result
    assert called == [1]
    assert result.size_before == before
    assert result.size_after == _size(db) < before / 2
    assert result.reclaimable > 0
    assert not Path(f"{db}-wal").exists() or Path(f"{db}-wal").stat().st_size == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 100
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_force_skips_the_pays_off_check(tmp_path):
    db = tmp_path / "bloated.db"
    _bloated_db(db)
    assert maintenance.vacuum_file(db).status == maintenance.NOT_WORTHWHILE
    assert maintenance.vacuum_file(db, force=True).status == maintenance.DONE


def test_open_transaction_is_skipped(tmp_path):
    db = tmp_path / "bloated.db"
    _bloated_db(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO t (blob) VALUES ('y')")
    assert conn.in_transaction
    result = maintenance.vacuum(conn, db, force=True)
    conn.rollback()
    conn.close()
    assert result.status == maintenance.IN_TRANSACTION


def test_failure_is_reported_not_raised(tmp_path):
    db = tmp_path / "bloated.db"
    _bloated_db(db)
    real = sqlite3.connect(db)

    class _Failing:
        in_transaction = False

        def execute(self, sql, *args):
            if sql == "VACUUM":
                raise sqlite3.OperationalError("disk I/O error")
            return real.execute(sql, *args)

    result = maintenance.vacuum(_Failing(), db, force=True)
    real.close()
    assert result.status == maintenance.FAILED
    assert "disk I/O error" in result.error


# ── Free disk space ──────────────────────────────────────────────────────────

def test_low_disk_space_is_skipped(tmp_path):
    db = tmp_path / "bloated.db"
    _bloated_db(db)
    with patch.object(maintenance.shutil, "disk_usage", return_value=_Usage(0, 0, 1)):
        result = maintenance.vacuum_file(db, force=True)
    assert result.status == maintenance.LOW_DISK_SPACE
    assert result.size_after == result.size_before


def test_space_check_on_one_disk_needs_twice(tmp_path, monkeypatch):
    stats = maintenance.PageStats(4096, 100 * 256, 0)  # 100 MB used
    monkeypatch.setattr(maintenance, "sqlite_temp_dir", lambda: tmp_path)
    free = 200 * _MB  # room for one copy plus margin, not for two
    with patch.object(maintenance.shutil, "disk_usage", return_value=_Usage(0, 0, free)):
        check = maintenance.check_free_space(tmp_path / "x.db", stats)
        assert not check.ok
        assert check.needed == 2 * 100 * _MB + maintenance._FREE_SPACE_MARGIN
        # With temp_store=MEMORY only the WAL copy goes to disk.
        assert maintenance.check_free_space(tmp_path / "x.db", stats, temp_in_memory=True).ok


def test_space_check_checks_temp_folder_on_another_disk(tmp_path, monkeypatch):
    stats = maintenance.PageStats(4096, 100 * 256, 0)
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(maintenance, "sqlite_temp_dir", lambda: tmp)
    monkeypatch.setattr(maintenance, "_same_device", lambda a, b: False)

    def usage(folder):
        return _Usage(0, 0, 10 * _MB if Path(folder) == tmp else 10_000 * _MB)

    monkeypatch.setattr(maintenance.shutil, "disk_usage", usage)
    check = maintenance.check_free_space(tmp_path / "x.db", stats)
    assert [s[0] for s in check.shortages] == [tmp]


@pytest.mark.skipif(maintenance.sys.platform == "win32", reason="SQLite uses TMP/TEMP on Windows")
def test_sqlite_temp_dir_honours_sqlite_tmpdir(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_TMPDIR", str(tmp_path))
    assert maintenance.sqlite_temp_dir() == tmp_path


@pytest.mark.skipif(maintenance.sys.platform != "win32", reason="Windows search order")
def test_sqlite_temp_dir_uses_tmp_on_windows(tmp_path, monkeypatch):
    monkeypatch.setenv("TMP", str(tmp_path))
    assert maintenance.sqlite_temp_dir() == tmp_path


# ── After migration 26 ───────────────────────────────────────────────────────

def _old_db_with_free_pages(path: Path):
    """A schema-25 database (no #1033 defaults yet) with free pages, so
    migration 26 really rebuilds its tables."""
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    for table, defaults in database._COLUMN_DEFAULTS_26.items():
        for col in defaults:
            metadata.tables[table].c[col].server_default = None
    engine = _make_engine(path)
    metadata.create_all(engine)
    with engine.connect() as c:
        c.execute(text("CREATE TABLE filler (blob TEXT)"))
        for _ in range(2000):
            c.execute(text("INSERT INTO filler VALUES (:b)"), {"b": "x" * 2000})
        c.execute(text("DROP TABLE filler"))
        c.execute(text("PRAGMA user_version = 25"))
        c.commit()
    return engine


def test_migration_26_vacuums_after_a_rebuild(tmp_path):
    db = tmp_path / "old.db"
    engine = _old_db_with_free_pages(db)
    steps = []
    database.set_progress_callback(steps.append)
    try:
        with patch.object(database.maintenance, "vacuum",
                          partial(maintenance.vacuum, min_bytes=0, min_fraction=0.1)):
            _run_migrations(engine)
    finally:
        database.set_progress_callback(None)
    with engine.connect() as c:
        assert c.execute(text("PRAGMA freelist_count")).scalar() == 0
        assert c.execute(text("PRAGMA user_version")).scalar() == SCHEMA_VERSION
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
    engine.dispose()
    assert steps == [database.PROGRESS_MIGRATING, database.PROGRESS_COMPACTING]


def test_migration_26_without_rebuild_does_not_vacuum(tmp_path):
    engine = _old_db_with_free_pages(tmp_path / "old.db")
    with patch.object(database, "_add_column_defaults", return_value=[]), \
         patch.object(database.maintenance, "vacuum") as vac:
        _run_migrations(engine)
    engine.dispose()
    vac.assert_not_called()


def test_failed_vacuum_does_not_fail_the_migration(tmp_path):
    db = tmp_path / "old.db"
    engine = _old_db_with_free_pages(db)
    with patch.object(maintenance, "page_stats", side_effect=sqlite3.OperationalError("boom")):
        _run_migrations(engine)
    with engine.connect() as c:
        assert c.execute(text("PRAGMA user_version")).scalar() == SCHEMA_VERSION
    engine.dispose()

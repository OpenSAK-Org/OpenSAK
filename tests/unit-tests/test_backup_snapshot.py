"""
tests/unit-tests/test_backup_snapshot.py — WAL-safe database snapshot (#943).

These tests use plain sqlite3 connections, not OpenSAK's engine, so they
exercise the snapshot core on its own. A "writer" connection with automatic
checkpoints switched off stands in for the active OpenSAK database: it keeps
committed rows in the -wal file, which is exactly the situation a plain
file copy gets wrong.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest

from opensak.backup import SnapshotError, snapshot_database
from opensak.backup import snapshot as snapshot_mod

ROWS = 2000


def _open_wal_writer(path: Path, rows: int = ROWS) -> sqlite3.Connection:
    """
    Create a WAL database at *path* holding *rows* committed rows that are
    still in the -wal file, and return the open connection. The caller must
    keep it open — closing the last connection checkpoints the WAL.
    """
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA wal_autocheckpoint = 0")
    conn.execute("CREATE TABLE caches (code TEXT PRIMARY KEY, name TEXT)")
    conn.executemany(
        "INSERT INTO caches VALUES (?, ?)",
        [(f"GC{i:05d}", f"Cache {i} " + "x" * 50) for i in range(rows)],
    )
    conn.commit()
    return conn


@pytest.fixture
def wal_db(tmp_path: Path) -> Iterator[tuple[Path, sqlite3.Connection]]:
    path = tmp_path / "source.db"
    conn = _open_wal_writer(path)
    try:
        yield path, conn
    finally:
        conn.close()


def _row_count(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT count(*) FROM caches").fetchone()[0]
    finally:
        conn.close()


def _leftovers(folder: Path, keep: set[str]) -> list[str]:
    """Names in *folder* other than *keep* — temp files, sidecars, etc."""
    return sorted(p.name for p in folder.iterdir() if p.name not in keep)


class TestSnapshotContent:
    def test_precondition_rows_are_still_in_the_wal(self, wal_db):
        # Guards the other tests: if SQLite ever checkpointed here, they
        # would no longer prove anything about WAL content.
        path, _conn = wal_db
        wal = Path(str(path) + "-wal")
        assert wal.exists() and wal.stat().st_size > 0

    def test_includes_committed_rows_still_in_the_wal(self, wal_db, tmp_path):
        path, _conn = wal_db
        target = tmp_path / "out" / "copy.db"
        snapshot_database(path, target)
        assert _row_count(target) == ROWS

    def test_includes_rows_committed_after_earlier_reads(self, wal_db, tmp_path):
        path, conn = wal_db
        conn.execute("INSERT INTO caches VALUES ('GCLAST', 'Latest change')")
        conn.commit()
        target = tmp_path / "copy.db"
        snapshot_database(path, target)
        check = sqlite3.connect(str(target))
        try:
            row = check.execute(
                "SELECT name FROM caches WHERE code = 'GCLAST'"
            ).fetchone()
        finally:
            check.close()
        assert row == ("Latest change",)

    def test_copy_passes_integrity_check(self, wal_db, tmp_path):
        path, _conn = wal_db
        target = tmp_path / "copy.db"
        snapshot_database(path, target)
        check = sqlite3.connect(str(target))
        try:
            assert check.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        finally:
            check.close()

    def test_source_is_unchanged(self, wal_db, tmp_path):
        path, _conn = wal_db
        snapshot_database(path, tmp_path / "copy.db")
        assert _row_count(path) == ROWS

    def test_non_ascii_paths(self, tmp_path):
        folder = tmp_path / "Geocaching æøå"
        folder.mkdir()
        conn = _open_wal_writer(folder / "Område.db", rows=10)
        try:
            snapshot_database(folder / "Område.db", folder / "Kopi ø.db")
        finally:
            conn.close()
        assert _row_count(folder / "Kopi ø.db") == 10


class TestSelfContained:
    def test_no_sidecar_files_next_to_the_copy(self, wal_db, tmp_path):
        path, _conn = wal_db
        out = tmp_path / "out"
        snapshot_database(path, out / "copy.db")
        assert _leftovers(out, {"copy.db"}) == []

    def test_copy_is_not_in_wal_mode(self, wal_db, tmp_path):
        # Header bytes 18/19 are 2 for WAL and 1 for rollback-journal mode.
        path, _conn = wal_db
        target = tmp_path / "copy.db"
        snapshot_database(path, target)
        header = target.read_bytes()[:20]
        assert (header[18], header[19]) == (1, 1)

    def test_copy_works_when_moved_on_its_own(self, wal_db, tmp_path):
        path, _conn = wal_db
        target = tmp_path / "copy.db"
        snapshot_database(path, target)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        moved = elsewhere / "moved.db"
        moved.write_bytes(target.read_bytes())  # main file only
        assert _row_count(moved) == ROWS


class TestFailures:
    def test_failure_mid_snapshot_leaves_no_target(self, wal_db, tmp_path):
        path, _conn = wal_db
        out = tmp_path / "out"
        calls = []

        def fail_on_second_step(done: int, total: int) -> None:
            calls.append(done)
            if len(calls) == 2:
                raise RuntimeError("cancelled")

        with pytest.raises(RuntimeError, match="cancelled"):
            snapshot_database(
                path, out / "copy.db", fail_on_second_step, step_pages=2
            )
        assert len(calls) == 2  # it really did stop mid-way
        assert _leftovers(out, set()) == []

    def test_failure_keeps_existing_target_untouched(self, wal_db, tmp_path):
        path, _conn = wal_db
        target = tmp_path / "copy.db"
        target.write_bytes(b"previous backup")

        def fail(done: int, total: int) -> None:
            raise RuntimeError("cancelled")

        with pytest.raises(RuntimeError):
            snapshot_database(path, target, fail, step_pages=1)
        assert target.read_bytes() == b"previous backup"
        assert _leftovers(tmp_path, {"copy.db", "source.db",
                                     "source.db-wal", "source.db-shm"}) == []

    def test_failed_integrity_check_leaves_no_target(self, wal_db, tmp_path):
        path, _conn = wal_db
        out = tmp_path / "out"
        with patch.object(
            snapshot_mod, "_check_integrity",
            side_effect=SnapshotError("Integrity check failed on the copy: x"),
        ):
            with pytest.raises(SnapshotError, match="Integrity check failed"):
                snapshot_database(path, out / "copy.db")
        assert _leftovers(out, set()) == []

    def test_verify_false_skips_integrity_check(self, wal_db, tmp_path):
        path, _conn = wal_db
        with patch.object(snapshot_mod, "_check_integrity") as check:
            snapshot_database(path, tmp_path / "copy.db", verify=False)
        check.assert_not_called()

    def test_missing_source(self, tmp_path):
        with pytest.raises(SnapshotError, match="not found"):
            snapshot_database(tmp_path / "missing.db", tmp_path / "copy.db")
        # sqlite3.connect() must not have created an empty source file
        assert _leftovers(tmp_path, set()) == []

    def test_source_that_is_not_a_database(self, tmp_path):
        junk = tmp_path / "junk.db"
        junk.write_bytes(b"this is not a database" * 100)
        with pytest.raises(SnapshotError, match="not a valid SQLite database"):
            snapshot_database(junk, tmp_path / "copy.db")
        assert _leftovers(tmp_path, {"junk.db"}) == []

    def test_target_same_as_source(self, wal_db):
        path, _conn = wal_db
        with pytest.raises(SnapshotError, match="same file"):
            snapshot_database(path, path)
        assert _row_count(path) == ROWS

    def test_invalid_step_pages(self, wal_db, tmp_path):
        path, _conn = wal_db
        with pytest.raises(ValueError):
            snapshot_database(path, tmp_path / "copy.db", step_pages=0)


class TestProgress:
    def test_called_for_multi_page_database(self, wal_db, tmp_path):
        path, _conn = wal_db
        calls: list[tuple[int, int]] = []
        snapshot_database(
            path, tmp_path / "copy.db",
            lambda done, total: calls.append((done, total)),
            step_pages=5,
        )
        assert len(calls) > 1
        total = calls[-1][1]
        assert total > 5
        assert calls[-1] == (total, total)
        dones = [done for done, _ in calls]
        assert dones == sorted(dones)

    def test_no_callback_is_fine(self, wal_db, tmp_path):
        path, _conn = wal_db
        snapshot_database(path, tmp_path / "copy.db", step_pages=5)
        assert _row_count(tmp_path / "copy.db") == ROWS

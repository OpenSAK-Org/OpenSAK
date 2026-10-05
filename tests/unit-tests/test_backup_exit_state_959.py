"""
tests/unit-tests/test_backup_exit_state_959.py — back up on exit, core (#959).

Change detection, the on-exit setting, suppression and the post-close
refresh in opensak.backup.exit_state. The prompt and close flow are tested
separately once they exist.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from opensak.backup import exit_state as es
from opensak.settings_store import get_install_dir, get_store


def _db(path: Path, rows: int = 5) -> SimpleNamespace:
    """A small WAL-mode database, closed (so no -wal is left behind)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS caches (code TEXT)")
    conn.executemany("INSERT INTO caches VALUES (?)",
                     [(f"GC{i}",) for i in range(rows)])
    conn.commit()
    conn.close()
    return SimpleNamespace(name=path.stem, path=path)


def _bump_mtime(path: Path, seconds: int = 10) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 1_000_000_000))


@pytest.fixture
def dbs(tmp_path, monkeypatch):
    """Two databases, used as the database list."""
    listed = [_db(tmp_path / "dbs" / "A.db"), _db(tmp_path / "dbs" / "B.db")]
    monkeypatch.setattr(es, "_listed_databases", lambda: list(listed))
    return listed


# ── On-exit setting ───────────────────────────────────────────────────────────

class TestOnExitSetting:
    def test_default_is_ask(self):
        assert es.get_on_exit() == es.ON_EXIT_ASK

    @pytest.mark.parametrize("value", es.ON_EXIT_CHOICES)
    def test_round_trip(self, value):
        es.set_on_exit(value)
        assert es.get_on_exit() == value
        assert get_store().get(es.ON_EXIT_KEY) == value

    def test_unknown_stored_value_reads_as_ask(self):
        get_store().set(es.ON_EXIT_KEY, "sometimes")
        assert es.get_on_exit() == es.ON_EXIT_ASK

    def test_unknown_value_is_refused(self):
        with pytest.raises(ValueError):
            es.set_on_exit("sometimes")


# ── Suppression ───────────────────────────────────────────────────────────────

class TestSuppression:
    def test_suite_runs_suppressed(self):
        # tests/conftest.py suppresses the prompt for every test.
        assert es.is_exit_backup_suppressed() is True

    def test_suppress(self, monkeypatch):
        monkeypatch.setattr(es, "_suppressed", False)
        assert es.is_exit_backup_suppressed() is False
        es.suppress_exit_backup()
        assert es.is_exit_backup_suppressed() is True


# ── Change detection ──────────────────────────────────────────────────────────

class TestChangeDetection:
    def test_no_recorded_state_counts_as_changed(self, dbs):
        assert es.has_changes_since_backup() is True

    def test_unknown_state_format_counts_as_changed(self, dbs):
        es.record_backup_state()
        state = get_store().get(es.LAST_STATE_KEY)
        state["format"] = 999
        get_store().set(es.LAST_STATE_KEY, state)
        assert es.has_changes_since_backup() is True

    def test_nothing_changed_after_a_backup(self, dbs):
        es.record_backup_state()
        assert es.has_changes_since_backup() is False

    def test_database_content_change(self, dbs):
        es.record_backup_state()
        _db(dbs[0].path, rows=50)
        _bump_mtime(dbs[0].path)
        assert es.has_changes_since_backup() is True

    def test_modification_time_alone_counts(self, dbs):
        es.record_backup_state()
        _bump_mtime(dbs[1].path)
        assert es.has_changes_since_backup() is True

    def test_database_added_to_the_list(self, dbs, tmp_path):
        es.record_backup_state()
        dbs.append(_db(tmp_path / "dbs" / "C.db"))
        assert es.has_changes_since_backup() is True

    def test_database_removed_from_the_list(self, dbs):
        es.record_backup_state()
        dbs.pop()
        assert es.has_changes_since_backup() is True

    def test_missing_database_file_is_stable(self, dbs, tmp_path):
        dbs.append(SimpleNamespace(name="Gone", path=tmp_path / "dbs" / "Gone.db"))
        es.record_backup_state()
        assert es.has_changes_since_backup() is False

    def test_wal_with_content_counts(self, dbs):
        es.record_backup_state()
        conn = sqlite3.connect(str(dbs[0].path))
        conn.execute("INSERT INTO caches VALUES ('GCNEW')")
        conn.commit()
        try:
            wal = dbs[0].path.with_name("A.db-wal")
            assert wal.stat().st_size > 0
            assert es.has_changes_since_backup() is True
        finally:
            conn.close()

    def test_empty_wal_is_ignored(self, dbs):
        """Opening a database creates an empty -wal with a new mtime."""
        es.record_backup_state()
        wal = dbs[0].path.with_name("A.db-wal")
        wal.write_bytes(b"")
        _bump_mtime(wal)
        assert es.has_changes_since_backup() is False

    def test_opensak_json_is_not_watched(self, dbs):
        es.record_backup_state()
        get_store().set("window_geometry", "something new")   # every exit
        assert es.has_changes_since_backup() is False

    @pytest.mark.parametrize("folder", ["filters", "column_views"])
    def test_settings_folder_new_file(self, dbs, folder):
        (get_install_dir() / folder).mkdir(parents=True, exist_ok=True)
        es.record_backup_state()
        new = get_install_dir() / folder / "New.json"
        new.write_text("{}", encoding="utf-8")
        _bump_mtime(new)
        assert es.has_changes_since_backup() is True

    def test_settings_folder_deleted_file(self, dbs):
        folder = get_install_dir() / "filters"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "Old.json").write_text("{}", encoding="utf-8")
        es.record_backup_state()
        (folder / "Old.json").unlink()
        assert es.has_changes_since_backup() is True

    def test_settings_folder_created(self, dbs):
        es.record_backup_state()
        (get_install_dir() / "filters").mkdir(parents=True, exist_ok=True)
        assert es.has_changes_since_backup() is True

    def test_icons_folder_is_not_watched(self, dbs):
        es.record_backup_state()
        (get_install_dir() / "icons").mkdir(parents=True, exist_ok=True)
        (get_install_dir() / "icons" / "x.svg").write_text("<svg/>", encoding="utf-8")
        assert es.has_changes_since_backup() is False


# ── Partial (manual) backups ──────────────────────────────────────────────────

class TestPartialBackup:
    def test_unticked_never_backed_up_database_still_counts(self, dbs):
        es.record_backup_state([dbs[0]])
        assert es.has_changes_since_backup() is True
        assert list(get_store().get(es.LAST_STATE_KEY)["databases"]) == [
            str(dbs[0].path)
        ]

    def test_unticked_database_keeps_its_earlier_state(self, dbs):
        es.record_backup_state()            # full backup: A and B
        _bump_mtime(dbs[0].path)            # A changes
        _bump_mtime(dbs[1].path)            # B changes
        es.record_backup_state([dbs[0]])    # manual backup of A only
        assert es.has_changes_since_backup() is True   # B is still unprotected
        es.record_backup_state([dbs[1]])
        assert es.has_changes_since_backup() is False

    def test_missing_file_never_keeps_asking(self, dbs, tmp_path):
        dbs.append(SimpleNamespace(name="Gone", path=tmp_path / "dbs" / "Gone.db"))
        es.record_backup_state([dbs[0], dbs[1]])
        assert es.has_changes_since_backup() is False

    def test_removed_database_is_dropped_from_the_state(self, dbs):
        es.record_backup_state()
        gone = dbs.pop()
        es.record_backup_state([dbs[0]])
        assert str(gone.path) not in get_store().get(es.LAST_STATE_KEY)["databases"]
        assert es.has_changes_since_backup() is False


# ── Post-close refresh ────────────────────────────────────────────────────────

class TestFinalizeAfterClose:
    def test_does_nothing_unless_marked_clean(self, dbs):
        with patch("opensak.db.database.dispose_engine") as dispose:
            es.finalize_after_close()
        dispose.assert_not_called()
        assert get_store().get(es.LAST_STATE_KEY) is None

    def test_checkpoint_on_close_does_not_count_as_a_change(self, dbs):
        """
        The scenario behind finalize_after_close(): a backup is taken while
        the WAL holds the session's writes; closing the last connection then
        checkpoints it into the .db file (new size and mtime) and the next
        start creates an empty -wal. Without the refresh the next exit would
        ask again although nothing changed.
        """
        conn = sqlite3.connect(str(dbs[0].path))
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("INSERT INTO caches VALUES ('GCNEW')")
        conn.commit()
        assert dbs[0].path.with_name("A.db-wal").stat().st_size > 0

        es.record_backup_state()            # backup at exit, WAL still full
        es.mark_clean_on_close()

        def _dispose(*_a, **_k):
            conn.close()                    # last connection: checkpoint
            _bump_mtime(dbs[0].path)        # make the rewrite visible on coarse clocks

        with patch("opensak.db.database.dispose_engine", side_effect=_dispose):
            es.finalize_after_close()

        # Next session: opening creates an empty -wal.
        dbs[0].path.with_name("A.db-wal").write_bytes(b"")
        assert es.has_changes_since_backup() is False

    def test_without_refresh_the_checkpoint_would_count(self, dbs):
        """Guards the test above: the checkpoint really does change the stats."""
        conn = sqlite3.connect(str(dbs[0].path))
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("INSERT INTO caches VALUES ('GCNEW')")
        conn.commit()
        es.record_backup_state()
        conn.close()
        _bump_mtime(dbs[0].path)
        assert es.has_changes_since_backup() is True

    def test_flag_is_cleared(self, dbs):
        es.mark_clean_on_close()
        with patch("opensak.db.database.dispose_engine"):
            es.finalize_after_close()
        get_store().delete(es.LAST_STATE_KEY)
        with patch("opensak.db.database.dispose_engine") as dispose:
            es.finalize_after_close()
        dispose.assert_not_called()

    def test_never_raises(self, dbs):
        es.mark_clean_on_close()
        with patch("opensak.db.database.dispose_engine",
                   side_effect=RuntimeError("boom")):
            es.finalize_after_close()          # must not raise

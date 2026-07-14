# tests/unit-tests/test_db_manager.py — DatabaseManager unit tests (store mocked).

from datetime import datetime

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

pytest.importorskip("pytestqt")

from opensak.db.manager import DatabaseManager, DatabaseInfo


@pytest.fixture
def manager(tmp_path, qapp, monkeypatch):
    """Isolated DatabaseManager: settings_store mocked, init_db no-op, tmp_path for files."""
    from opensak import settings_store as ss

    # Fresh in-memory store — no databases saved yet
    fresh = ss.SettingsStore()
    fresh._data = {}
    fresh._path = tmp_path / "opensak.json"
    monkeypatch.setattr(ss, "_store", fresh)

    with (
        patch("opensak.db.database.init_db"),
        patch("opensak.config.get_app_data_dir", return_value=tmp_path),
    ):
        yield DatabaseManager()


@pytest.fixture
def real_manager(tmp_path, qapp, monkeypatch):
    """
    Like `manager`, but init_db is only mocked for the DatabaseManager's own
    construction (avoiding a real SQLite file for its default database) — not
    for the whole test body, unlike the `manager` fixture above where the
    patch stays active for the test's entire duration (it's a generator
    fixture that yields *inside* the `with` block).

    Needed for tests that must exercise the real SQLAlchemy engine lifecycle
    (init_db / dispose_engine / get_session), e.g. the #562 regression test
    below — with `manager`, init_db is a no-op MagicMock for the whole test,
    so a "fixed" engine-reopen bug would silently pass for the wrong reason.
    """
    from opensak import settings_store as ss

    fresh = ss.SettingsStore()
    fresh._data = {"databases.dir": str(tmp_path)}
    fresh._path = tmp_path / "opensak.json"
    monkeypatch.setattr(ss, "_store", fresh)

    with (
        patch("opensak.db.database.init_db"),
        patch("opensak.config.get_app_data_dir", return_value=tmp_path),
    ):
        mgr = DatabaseManager()
    return mgr


# ── Initialisation ────────────────────────────────────────────────────────────

class TestDatabaseManagerInit:
    def test_creates_default_database_when_settings_empty(self, manager):
        assert len(manager.databases) == 1

    def test_active_is_set_after_init(self, manager):
        assert manager.active is not None

    def test_active_path_matches_active_info(self, manager):
        assert manager.active_path == manager.active.path

    def test_databases_property_returns_copy(self, manager):
        dbs = manager.databases
        dbs.append(object())  # type: ignore
        assert len(manager.databases) == 1


# ── new_database ──────────────────────────────────────────────────────────────

class TestNewDatabase:
    def test_adds_entry_to_list(self, manager, tmp_path):
        with patch("opensak.db.database.init_db"):
            manager.new_database("Test", tmp_path / "Test.db")
        assert any(db.name == "Test" for db in manager.databases)

    def test_returns_database_info_instance(self, manager, tmp_path):
        with patch("opensak.db.database.init_db"):
            info = manager.new_database("Test2", tmp_path / "Test2.db")
        assert isinstance(info, DatabaseInfo)

    def test_increments_database_count(self, manager, tmp_path):
        before = len(manager.databases)
        with patch("opensak.db.database.init_db"):
            manager.new_database("Extra", tmp_path / "Extra.db")
        assert len(manager.databases) == before + 1

    def test_rejects_duplicate_name(self, manager, tmp_path):
        with patch("opensak.db.database.init_db"):
            manager.new_database("Dup", tmp_path / "Dup.db")
        with pytest.raises(ValueError):
            with patch("opensak.db.database.init_db"):
                manager.new_database("Dup", tmp_path / "Dup2.db")

    def test_two_new_databases_are_distinct(self, manager, tmp_path):
        with patch("opensak.db.database.init_db"):
            a = manager.new_database("A", tmp_path / "A.db")
            b = manager.new_database("B", tmp_path / "B.db")
        assert a.name != b.name

    # ── Issue #539 (follow-up) ────────────────────────────────────────────────
    # GeePa67's beta.8 feedback: after several rename/remove/delete
    # experiments, a "New database" could silently reopen an orphaned file
    # left behind at the derived path — reappearing with old caches and old
    # column settings under a name the user believes is brand new.

    def test_rejects_when_stray_file_already_exists_at_derived_path(
        self, manager, tmp_path
    ):
        # Simulate an orphaned file left behind by e.g. remove_from_list()
        # (which intentionally does not delete the physical file) or a
        # previous rename during testing.
        stray = tmp_path / "test1.db"
        stray.write_text("OLD CACHE DATA FROM A PREVIOUS TEST DATABASE")

        with patch("opensak.settings_store.get_db_dir", return_value=tmp_path):
            with pytest.raises(ValueError):
                manager.new_database("test1", None)

        # The orphaned file must be left completely untouched.
        assert stray.read_text() == "OLD CACHE DATA FROM A PREVIOUS TEST DATABASE"
        assert not any(db.name == "test1" for db in manager.databases)

    def test_rejects_when_explicit_path_already_has_a_file(self, manager, tmp_path):
        stray = tmp_path / "Reused.db"
        stray.write_text("someone else's data")
        with pytest.raises(ValueError):
            manager.new_database("Reused", stray)
        assert stray.read_text() == "someone else's data"

    def test_new_database_still_works_after_removing_one_with_same_name(
        self, manager, tmp_path
    ):
        # remove_from_list() deliberately leaves the file on disk, so the
        # freed-up name must map to a *different* physical file next time.
        first_path = tmp_path / "Recreate.db"
        with patch("opensak.db.database.init_db"):
            first = manager.new_database("Recreate", first_path)
        first_path.touch()
        with patch("opensak.db.database.dispose_engine"):
            manager.remove_from_list(first)

        with pytest.raises(ValueError):
            manager.new_database("Recreate", first_path)


# ── rename ────────────────────────────────────────────────────────────────────

class TestRename:
    def test_renames_database(self, manager, tmp_path):
        db_path = tmp_path / "Original.db"
        with patch("opensak.db.database.init_db"):
            db = manager.new_database("Original", db_path)
        db_path.touch()  # simulate init_db() having created the file
        with patch("opensak.db.database.dispose_engine"), patch("opensak.db.database.init_db"):
            manager.rename(db, "NewName")
        assert db.name == "NewName"

    def test_renamed_entry_visible_in_list(self, manager, tmp_path):
        db_path = tmp_path / "Original.db"
        with patch("opensak.db.database.init_db"):
            db = manager.new_database("Original", db_path)
        db_path.touch()  # simulate init_db() having created the file
        with patch("opensak.db.database.dispose_engine"), patch("opensak.db.database.init_db"):
            manager.rename(db, "Visible")
        assert any(d.name == "Visible" for d in manager.databases)

    def test_rejects_name_already_taken_by_another(self, manager, tmp_path):
        with patch("opensak.db.database.init_db"):
            other = manager.new_database("Other", tmp_path / "Other.db")
        with pytest.raises(ValueError):
            manager.rename(manager.databases[0], "Other")

    def test_rename_to_same_name_is_allowed(self, manager):
        db = manager.databases[0]
        original = db.name
        manager.rename(db, original)
        assert db.name == original

    # ── Issue #539 ──────────────────────────────────────────────────────────

    def test_rename_moves_the_physical_file(self, manager, tmp_path):
        old_path = tmp_path / "Original.db"
        with patch("opensak.db.database.init_db"):
            db = manager.new_database("Original", old_path)
        old_path.write_text("db contents")  # simulate init_db() writing content

        with patch("opensak.db.database.dispose_engine"), patch("opensak.db.database.init_db"):
            manager.rename(db, "Renamed")

        assert db.path != old_path
        assert db.path.exists()
        assert not old_path.exists()
        assert db.path.read_text() == "db contents"
        assert db.path.name == "Renamed.db"

    def test_rename_moves_shm_and_wal_sidecar_files(self, manager, tmp_path):
        old_path = tmp_path / "Original.db"
        with patch("opensak.db.database.init_db"):
            db = manager.new_database("Original", old_path)
        old_path.touch()  # simulate init_db() having created the file

        shm = Path(str(old_path) + "-shm")
        wal = Path(str(old_path) + "-wal")
        shm.write_text("shm")
        wal.write_text("wal")

        with patch("opensak.db.database.dispose_engine"), patch("opensak.db.database.init_db"):
            manager.rename(db, "WithSidecars")

        new_shm = Path(str(db.path) + "-shm")
        new_wal = Path(str(db.path) + "-wal")
        assert new_shm.exists() and new_shm.read_text() == "shm"
        assert new_wal.exists() and new_wal.read_text() == "wal"
        assert not shm.exists()
        assert not wal.exists()

    def test_rename_rejects_when_target_file_already_exists(self, manager, tmp_path):
        old_path = tmp_path / "Original.db"
        with patch("opensak.db.database.init_db"):
            db = manager.new_database("Original", old_path)
        old_path.touch()  # simulate init_db() having created the file

        # A stray file already sitting at the path the new name would map to
        # (e.g. left over from an earlier, unrelated database).
        (tmp_path / "Taken.db").write_text("someone else's data")

        with pytest.raises(ValueError):
            manager.rename(db, "Taken")
        # Original file and name are untouched after the rejected rename.
        assert db.name == "Original"
        assert db.path.exists()

    def test_renamed_database_frees_up_old_name_without_reviving_old_content(
        self, manager, tmp_path
    ):
        # The exact #539 repro: rename a database away, then create a brand
        # new one under the freed-up old name — it must NOT silently reuse
        # the original (now-moved) file and "come back" with old content.
        old_path = tmp_path / "MyCaches.db"
        with patch("opensak.db.database.init_db"):
            db = manager.new_database("MyCaches", old_path)
        old_path.write_text("original caches")  # simulate init_db() writing content

        with patch("opensak.db.database.dispose_engine"), patch("opensak.db.database.init_db"):
            manager.rename(db, "RenamedAway")

        with patch("opensak.db.database.init_db") as mock_init:
            def _fake_init_db(db_path):
                Path(db_path).write_text("")  # simulate a genuinely empty new DB
            mock_init.side_effect = _fake_init_db
            new_db = manager.new_database("MyCaches", tmp_path / "MyCaches.db")

        assert new_db.path != db.path
        assert new_db.path.read_text() == ""

    def test_rename_migrates_column_settings(self, manager, tmp_path):
        from opensak.gui.dialogs import column_dialog as cd

        old_path = tmp_path / "Original.db"
        with patch("opensak.db.database.init_db"):
            db = manager.new_database("Original", old_path)
        old_path.touch()  # simulate init_db() having created the file
        manager._active = db  # gør db aktiv så _col_key() bruger dens navn

        with patch("opensak.db.manager.get_db_manager", return_value=manager):
            cd.set_visible_columns(["gc_code", "name"])
            cd.set_column_widths({"gc_code": 90})

        with patch("opensak.db.database.dispose_engine"), patch("opensak.db.database.init_db"):
            manager.rename(db, "RenamedForColumns")

        with patch("opensak.db.manager.get_db_manager", return_value=manager):
            assert cd.get_visible_columns() == ["gc_code", "name"]
            assert cd.get_column_widths() == {"gc_code": 90}

    def test_rename_logs_warning_when_column_migration_fails(
        self, manager, tmp_path, caplog
    ):
        # #539 follow-up: a failure here must not be swallowed silently —
        # the rename itself should still succeed, but the failure must be
        # discoverable in the logs.
        old_path = tmp_path / "Original.db"
        with patch("opensak.db.database.init_db"):
            db = manager.new_database("Original", old_path)
        old_path.touch()

        with (
            patch("opensak.db.database.dispose_engine"),
            patch("opensak.db.database.init_db"),
            patch(
                "opensak.gui.dialogs.column_dialog.migrate_column_settings_for_rename",
                side_effect=RuntimeError("boom"),
            ),
            caplog.at_level("WARNING", logger="opensak.db.manager"),
        ):
            manager.rename(db, "StillRenamed")

        assert db.name == "StillRenamed"  # rename itself must still succeed
        assert any(
            "kunne ikke migrere" in rec.message.lower() for rec in caplog.records
        )


# ── remove_from_list ──────────────────────────────────────────────────────────

class TestRemoveFromList:
    def test_removes_entry_from_list(self, manager, tmp_path):
        with patch("opensak.db.database.init_db"):
            extra = manager.new_database("Extra", tmp_path / "Extra.db")
        with patch("opensak.db.database.dispose_engine"):
            manager.remove_from_list(extra)
        assert extra not in manager.databases

    def test_file_is_not_deleted(self, manager, tmp_path):
        db_file = tmp_path / "Keep.db"
        with patch("opensak.db.database.init_db"):
            extra = manager.new_database("Keep", db_file)
        db_file.touch()  # simulate init_db() having created the file
        with patch("opensak.db.database.dispose_engine"):
            manager.remove_from_list(extra)
        assert db_file.exists()

    def test_refuses_to_remove_active_database(self, manager):
        with pytest.raises(ValueError):
            with patch("opensak.db.database.dispose_engine"):
                manager.remove_from_list(manager.active)


# ── delete_database ───────────────────────────────────────────────────────────

class TestDeleteDatabase:
    def test_removes_entry_from_list(self, manager, tmp_path):
        db_file = tmp_path / "Del.db"
        with patch("opensak.db.database.init_db"):
            extra = manager.new_database("Del", db_file)
        db_file.touch()  # simulate init_db() having created the file
        with patch("opensak.db.database.dispose_engine"):
            manager.delete_database(extra)
        assert extra not in manager.databases

    def test_deletes_file_from_disk(self, manager, tmp_path):
        db_file = tmp_path / "Gone.db"
        with patch("opensak.db.database.init_db"):
            extra = manager.new_database("Gone", db_file)
        db_file.touch()  # simulate init_db() having created the file
        with patch("opensak.db.database.dispose_engine"):
            manager.delete_database(extra)
        assert not db_file.exists()

    def test_refuses_to_delete_active_database(self, manager):
        with pytest.raises(ValueError):
            with patch("opensak.db.database.dispose_engine"):
                manager.delete_database(manager.active)

    def test_missing_file_does_not_raise(self, manager, tmp_path):
        db_file = tmp_path / "Missing.db"
        with patch("opensak.db.database.init_db"):
            extra = manager.new_database("Missing", db_file)
        with patch("opensak.db.database.dispose_engine"):
            manager.delete_database(extra)  # file never existed


# ── open_database ─────────────────────────────────────────────────────────────

class TestOpenDatabase:
    def test_file_not_found_raises(self, manager, tmp_path):
        with pytest.raises(FileNotFoundError):
            manager.open_database(tmp_path / "nope.db")

    def test_opens_existing_file(self, manager, tmp_path):
        f = tmp_path / "Open.db"
        f.touch()
        info = manager.open_database(f)
        assert info.path == f

    def test_returns_existing_entry_for_same_path(self, manager, tmp_path):
        f = tmp_path / "Same.db"
        f.touch()
        a = manager.open_database(f)
        b = manager.open_database(f)
        assert a is b

    def test_dedupes_name_collision(self, manager, tmp_path):
        f1 = tmp_path / "Clash.db"
        f2 = tmp_path / "sub" / "Clash.db"
        f1.touch()
        f2.parent.mkdir()
        f2.touch()
        a = manager.open_database(f1)
        b = manager.open_database(f2)
        assert a.name != b.name


# ── switch_to ─────────────────────────────────────────────────────────────────

class TestSwitchTo:
    def test_switch_sets_active(self, manager, tmp_path):
        with patch("opensak.db.database.init_db"):
            extra = manager.new_database("Switch", tmp_path / "Switch.db")
        with patch("opensak.db.database.init_db"):
            manager.switch_to(extra)
        assert manager.active is extra


# ── copy_database ─────────────────────────────────────────────────────────────

class TestCopyDatabase:
    def test_copies_file_and_adds_entry(self, manager, tmp_path):
        src = tmp_path / "Src.db"
        with patch("opensak.db.database.init_db"):
            manager.new_database("Src", src)
        src.touch()  # simulate init_db() having created the file
        src_info = manager.databases[-1]
        dst = tmp_path / "Dst.db"
        copy = manager.copy_database(src_info, "Dst", dst)
        assert copy.name == "Dst"
        assert dst.exists()

    def test_default_path_uses_app_data_dir(self, manager, tmp_path):
        src = tmp_path / "Src2.db"
        with patch("opensak.db.database.init_db"):
            manager.new_database("Src2", src)
        src.touch()  # simulate init_db() having created the file
        src_info = manager.databases[-1]
        with patch("opensak.settings_store.get_db_dir", return_value=tmp_path):
            copy = manager.copy_database(src_info, "DstDefault")
        assert copy.path.parent == tmp_path

    def test_rejects_duplicate_name(self, manager, tmp_path):
        src = tmp_path / "CopySrc.db"
        with patch("opensak.db.database.init_db"):
            manager.new_database("CopySrc", src)
        src.touch()  # simulate init_db() having created the file
        src_info = manager.databases[-1]
        with pytest.raises(ValueError):
            manager.copy_database(src_info, "CopySrc")


# ── move_databases_to ──────────────────────────────────────────────────────────

class TestMoveDatabasesTo:
    def test_keep_originals_copies_and_preserves_source(self, manager, tmp_path):
        new_dir = tmp_path / "new_location"
        old_path = manager.active.path
        old_path.write_text("db content")

        with patch("opensak.db.database.dispose_engine"):
            errors = manager.move_databases_to(new_dir, delete_originals=False)

        assert errors == []
        assert (new_dir / old_path.name).exists()
        assert old_path.exists()
        assert manager.active.path == new_dir / old_path.name

    def test_delete_originals_removes_source_file(self, manager, tmp_path):
        new_dir = tmp_path / "new_location"
        old_path = manager.active.path
        old_path.write_text("db content")

        with patch("opensak.db.database.dispose_engine"):
            errors = manager.move_databases_to(new_dir, delete_originals=True)

        assert errors == []
        assert (new_dir / old_path.name).exists()
        assert not old_path.exists()

    def test_moves_sidecar_wal_and_shm_files(self, manager, tmp_path):
        new_dir = tmp_path / "new_location"
        old_path = manager.active.path
        old_path.write_text("db content")
        wal = Path(str(old_path) + "-wal")
        shm = Path(str(old_path) + "-shm")
        wal.write_text("wal")
        shm.write_text("shm")

        with patch("opensak.db.database.dispose_engine"):
            errors = manager.move_databases_to(new_dir, delete_originals=True)

        assert errors == []
        assert (new_dir / wal.name).exists()
        assert (new_dir / shm.name).exists()
        assert not wal.exists()
        assert not shm.exists()

    def test_skips_database_already_in_target_dir(self, manager, tmp_path):
        target = manager.active.path.parent
        with patch("opensak.db.database.dispose_engine"):
            errors = manager.move_databases_to(target, delete_originals=False)
        assert errors == []
        # Path unchanged — no-op for databases already in the destination.
        assert manager.active.path.parent == target

    def test_collision_with_existing_file_reports_error_and_skips(self, manager, tmp_path):
        new_dir = tmp_path / "new_location"
        new_dir.mkdir()
        old_path = manager.active.path
        old_path.write_text("real db")
        (new_dir / old_path.name).write_text("unrelated existing file")

        with patch("opensak.db.database.dispose_engine"):
            errors = manager.move_databases_to(new_dir, delete_originals=True)

        assert len(errors) == 1
        # Original must survive untouched — the move was aborted for this file.
        assert old_path.exists()
        assert old_path.read_text() == "real db"
        assert manager.active.path == old_path
        # The unrelated existing file at the destination must not be overwritten.
        assert (new_dir / old_path.name).read_text() == "unrelated existing file"

    def test_moves_multiple_databases(self, manager, tmp_path):
        new_dir = tmp_path / "new_location"
        with patch("opensak.db.database.init_db"):
            second = manager.new_database("Second", tmp_path / "Second.db")
        manager.active.path.write_text("db1")
        second.path.write_text("db2")

        with patch("opensak.db.database.dispose_engine"):
            errors = manager.move_databases_to(new_dir, delete_originals=False)

        assert errors == []
        assert all((new_dir / db.path.name).exists() for db in manager.databases)

    def test_creates_target_directory_if_missing(self, manager, tmp_path):
        new_dir = tmp_path / "does_not_exist_yet"
        assert not new_dir.exists()
        manager.active.path.write_text("db content")

        with patch("opensak.db.database.dispose_engine"):
            manager.move_databases_to(new_dir, delete_originals=False)

        assert new_dir.exists()

    def test_persists_updated_paths_to_settings(self, manager, tmp_path):
        new_dir = tmp_path / "new_location"
        manager.active.path.write_text("db content")

        with patch("opensak.db.database.dispose_engine"):
            manager.move_databases_to(new_dir, delete_originals=False)

        from opensak.settings_store import get_store
        saved_list = get_store().get("databases.list")
        assert any(d["path"] == str(manager.active.path) for d in saved_list)

    def test_active_database_usable_immediately_after_move_no_restart_needed(
        self, real_manager, tmp_path
    ):
        """
        Regression test: moving the active database used to leave its
        SQLAlchemy engine disposed but never reopened, so any DB access
        before an app restart crashed with "Database not initialised — call
        init_db() first." (hit in practice via mainwindow re-syncing
        distances right after the Settings dialog closes).

        Uses `real_manager` rather than `manager`: the latter keeps
        opensak.db.database.init_db mocked out for the whole test (it's a
        generator fixture that yields *inside* the patch context), so a
        regression in the real reopen logic would go undetected — the mock
        would happily "succeed" without ever touching _engine/_SessionLocal.
        """
        from opensak.db import database as dbmod

        manager = real_manager
        new_dir = tmp_path / "new_location"
        try:
            # Give the active database a real, valid SQLite file/engine
            # first — a plain text stand-in wouldn't exercise the actual
            # re-init path.
            dbmod.init_db(db_path=manager.active.path)
            dbmod.dispose_engine(manager.active.path)

            manager.move_databases_to(new_dir, delete_originals=False)

            assert manager.active.path.parent == new_dir
            with dbmod.get_session():
                pass  # must not raise RuntimeError("Database not initialised...")
        finally:
            # Real engine state is process-global — don't leak it into
            # whichever test happens to run next.
            dbmod.dispose_engine()

    def test_move_failure_to_reopen_active_engine_does_not_raise(
        self, manager, tmp_path
    ):
        """A failure to reopen the engine afterwards must not surface as if
        the (already successful) file move itself had failed."""
        new_dir = tmp_path / "new_location"
        manager.active.path.write_text("db content")
        expected_name = manager.active.path.name

        with (
            patch("opensak.db.database.dispose_engine"),
            patch.object(
                manager, "ensure_active_initialised",
                side_effect=RuntimeError("simulated failure"),
            ),
        ):
            errors = manager.move_databases_to(new_dir, delete_originals=False)

        assert errors == []  # the move itself still succeeded
        assert manager.active.path == new_dir / expected_name


# ── ensure_active_initialised ─────────────────────────────────────────────────

class TestEnsureActiveInitialised:
    def test_initialises_active(self, manager):
        with patch("opensak.db.database.init_db") as mock_init:
            manager.ensure_active_initialised()
        mock_init.assert_called_once_with(db_path=manager.active.path)

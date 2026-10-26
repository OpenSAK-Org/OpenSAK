"""
tests/unit-tests/test_companion_housekeeping_1034.py — the companion database
(#1034), step 2: cache-linked rows follow their caches (delete in the GUI,
in macros and on move, copy on copy), orphan cleanup and the check when a
database is opened, damaged companions, the user notice, and the companion
side of database maintenance (#1048).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from opensak.db import companion, maintenance
from opensak.db.cache_delete import delete_caches
from opensak.db.companion import Status
from opensak.db.database import dispose_engine, get_session, init_db
from opensak.db.db_settings import ensure_seeded
from opensak.db.models import Cache
from opensak.db.transfer import transfer_caches
from opensak.lang import load_language
from opensak.macro import MacroRuntime, WriteApproval, db_access
from opensak.utils import flags


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    load_language("en")
    monkeypatch.setattr(flags, "companion_db", True)
    companion.reset()
    db_access.reset_session()
    yield
    dispose_engine()
    companion.reset()
    db_access.reset_session()


def _database(path: Path, codes=("GC1", "GC2", "GC3"), notes=True) -> Path:
    """A database with caches *codes* and a companion with a cache-linked
    table `notes` (one row per cache) and a free table `settings`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path=path)
    with get_session() as s:
        for code in codes:
            s.add(Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0))
    dispose_engine(path)
    ensure_seeded(path, path.stem)
    if notes:
        _sql(path, "CREATE TABLE notes (gc_code TEXT PRIMARY KEY, note TEXT)",
             "CREATE INDEX ix_notes_note ON notes (note)",
             "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)",
             "INSERT INTO settings VALUES ('gc_code', 'not a cache')",
             *(f"INSERT INTO notes VALUES ('{c}', 'note {c}')" for c in codes))
    return path


def _sql(db_path: Path, *statements: str) -> None:
    companion.create(db_path)
    conn = sqlite3.connect(str(companion.companion_path(db_path)))
    try:
        for statement in statements:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


def _rows(db_path: Path, table: str = "notes") -> list[str]:
    conn = sqlite3.connect(str(companion.companion_path(db_path)))
    try:
        return [r[0] for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
    finally:
        conn.close()


def _register(db_path: Path, name: str, kind: str, code_column=None) -> None:
    _sql(db_path, f"INSERT INTO opensak_tables (name, owner, kind, code_column, created_at) "
                  f"VALUES ('{name}', 'test.lua', '{kind}', "
                  f"{'NULL' if code_column is None else repr(code_column)}, '2026-10-22')")


# ── Which tables are cache-linked ─────────────────────────────────────────────

class TestCacheLinked:
    def _linked(self, db_path):
        conn = sqlite3.connect(str(companion.companion_path(db_path)))
        try:
            return companion.cache_linked_tables(conn)
        finally:
            conn.close()

    def test_a_gc_code_column_makes_a_table_cache_linked(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        assert self._linked(db) == [("notes", "gc_code")]

    def test_registry_free_opts_out(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        _register(db, "notes", companion.KIND_FREE)
        assert self._linked(db) == []

    def test_registry_names_another_code_column(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        _sql(db, "CREATE TABLE finds (code TEXT, found TEXT)")
        _register(db, "finds", companion.KIND_CACHE, "code")
        assert ("finds", "code") in self._linked(db)

    def test_opensak_tables_are_never_linked(self, tmp_path):
        db = _database(tmp_path / "Found.db", notes=False)
        _sql(db, "CREATE TABLE opensak_custom (gc_code TEXT)")
        assert self._linked(db) == []


# ── Deleting caches ───────────────────────────────────────────────────────────

class TestDelete:
    def test_gui_delete_deletes_the_rows(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        init_db(db_path=db)
        with get_session() as s:
            assert delete_caches(s, ["GC1", "GC3"]) == ["GC1", "GC3"]
        assert _rows(db) == ["GC2"]
        assert _rows(db, "settings") == ["gc_code"]

    def test_undone_delete_keeps_the_rows(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        init_db(db_path=db)
        with pytest.raises(RuntimeError):
            with get_session() as s:
                delete_caches(s, ["GC1"])
                raise RuntimeError("undo")
        assert _rows(db) == ["GC1", "GC2", "GC3"]

    def test_delete_without_companion(self, tmp_path):
        db = _database(tmp_path / "Found.db", notes=False)
        init_db(db_path=db)
        with get_session() as s:
            assert delete_caches(s, ["GC1"]) == ["GC1"]

    def test_delete_without_the_flag_leaves_rows_for_the_cleanup(self, tmp_path, monkeypatch):
        db = _database(tmp_path / "Found.db")
        monkeypatch.setattr(flags, "companion_db", False)
        init_db(db_path=db)
        with get_session() as s:
            delete_caches(s, ["GC1"])
        dispose_engine(db)
        assert _rows(db) == ["GC1", "GC2", "GC3"]
        monkeypatch.setattr(flags, "companion_db", True)
        companion.reset()
        init_db(db_path=db)
        assert _rows(db) == ["GC2", "GC3"]


class _Host:
    def database_name(self):
        return "Found"

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def approve_macro_write(self, macro, change=None, editing=False):
        return WriteApproval.SESSION

    def caches_changed(self, codes, added):
        pass

    def caches_removed(self):
        pass

    def end_macro(self):
        pass


def _run(source):
    out: list[str] = []
    MacroRuntime(_Host(), output=out.append).run(source)
    return out


@pytest.mark.skipif(not flags.lua_macro_write, reason="needs the lua-macro-write flag")
class TestMacroDelete:
    @pytest.fixture
    def db(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        init_db(db_path=db)
        return db

    def test_opensak_delete(self, db):
        _run('opensak.delete("GC1")')
        assert _rows(db) == ["GC2", "GC3"]

    def test_sql_write_delete(self, db):
        _run("opensak.sql_write(\"DELETE FROM caches WHERE gc_code IN ('GC1', 'GC2')\")")
        assert _rows(db) == ["GC3"]

    def test_committed_transaction(self, db):
        _run("""
            opensak.transaction(function()
                opensak.delete("GC1")
                opensak.sql_write("DELETE FROM caches WHERE gc_code = 'GC2'")
            end)
        """)
        assert _rows(db) == ["GC3"]

    def test_undone_transaction_keeps_the_rows(self, db):
        _run("""
            pcall(opensak.transaction, function()
                opensak.delete("GC1")
                opensak.sql_write("DELETE FROM caches WHERE gc_code = 'GC2'")
                error("undo")
            end)
        """)
        assert _rows(db) == ["GC1", "GC2", "GC3"]

    def test_undone_inner_transaction_keeps_its_rows_only(self, db):
        _run("""
            opensak.transaction(function()
                opensak.delete("GC1")
                pcall(opensak.transaction, function()
                    opensak.delete("GC2")
                    error("undo")
                end)
            end)
        """)
        assert _rows(db) == ["GC2", "GC3"]


# ── Copying and moving caches ─────────────────────────────────────────────────

class TestTransfer:
    def test_copy_copies_the_rows_and_creates_the_table(self, tmp_path):
        src = _database(tmp_path / "Src.db")
        dst = _database(tmp_path / "Dst.db", codes=("GC9",), notes=False)
        assert not companion.exists(dst)

        assert transfer_caches(["GC1", "GC2"], src, dst, copy_only=True) == 2

        assert _rows(dst) == ["GC1", "GC2"]
        assert _rows(src) == ["GC1", "GC2", "GC3"]
        conn = sqlite3.connect(str(companion.companion_path(dst)))
        try:
            indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        assert "ix_notes_note" in indexes
        assert "settings" not in tables          # free tables stay where they are

    def test_copy_takes_the_registry_row_along(self, tmp_path):
        src = _database(tmp_path / "Src.db")
        _register(src, "notes", companion.KIND_CACHE)
        dst = _database(tmp_path / "Dst.db", codes=(), notes=False)
        transfer_caches(["GC1"], src, dst, copy_only=True)
        conn = sqlite3.connect(str(companion.companion_path(dst)))
        try:
            owner = conn.execute("SELECT owner FROM opensak_tables WHERE name='notes'").fetchone()
        finally:
            conn.close()
        assert owner == ("test.lua",)

    def test_copy_replaces_the_targets_rows(self, tmp_path):
        src = _database(tmp_path / "Src.db")
        dst = _database(tmp_path / "Dst.db", codes=("GC1",))
        _sql(dst, "UPDATE notes SET note = 'old' WHERE gc_code = 'GC1'")
        transfer_caches(["GC1"], src, dst, copy_only=True)
        conn = sqlite3.connect(str(companion.companion_path(dst)))
        try:
            assert conn.execute("SELECT note FROM notes").fetchall() == [("note GC1",)]
        finally:
            conn.close()

    def test_copy_into_a_table_with_fewer_columns(self, tmp_path):
        src = _database(tmp_path / "Src.db")
        _sql(src, "ALTER TABLE notes ADD COLUMN extra TEXT")
        dst = _database(tmp_path / "Dst.db", codes=(), notes=False)
        _sql(dst, "CREATE TABLE notes (gc_code TEXT PRIMARY KEY, note TEXT)")
        transfer_caches(["GC2"], src, dst, copy_only=True)
        assert _rows(dst) == ["GC2"]

    def test_move_moves_the_rows(self, tmp_path):
        src = _database(tmp_path / "Src.db")
        dst = _database(tmp_path / "Dst.db", codes=(), notes=False)
        transfer_caches(["GC1"], src, dst)
        assert _rows(dst) == ["GC1"]
        assert _rows(src) == ["GC2", "GC3"]

    def test_skipped_cache_keeps_its_rows(self, tmp_path):
        src = _database(tmp_path / "Src.db")
        dst = _database(tmp_path / "Dst.db", codes=("GC1",))
        _sql(dst, "UPDATE notes SET note = 'mine' WHERE gc_code = 'GC1'")
        assert transfer_caches(["GC1"], src, dst, if_exists="skip") == 0
        conn = sqlite3.connect(str(companion.companion_path(dst)))
        try:
            assert conn.execute("SELECT note FROM notes").fetchall() == [("mine",)]
        finally:
            conn.close()
        assert _rows(src) == ["GC1", "GC2", "GC3"]

    def test_no_rows_no_companion_for_the_target(self, tmp_path):
        src = _database(tmp_path / "Src.db", notes=False)
        dst = _database(tmp_path / "Dst.db", codes=(), notes=False)
        transfer_caches(["GC1"], src, dst, copy_only=True)
        assert not companion.exists(dst)

    def test_unusable_target_companion_never_fails_the_transfer(self, tmp_path):
        src = _database(tmp_path / "Src.db")
        dst = _database(tmp_path / "Dst.db", codes=(), notes=False)
        other = _database(tmp_path / "Other.db", codes=())
        companion.companion_path(other).rename(companion.companion_path(dst))
        assert transfer_caches(["GC1"], src, dst, copy_only=True) == 1


# ── Opening: orphans, check, damaged companions ───────────────────────────────

class TestOpen:
    def test_orphans_are_removed_when_opened(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        _sql(db, "INSERT INTO notes VALUES ('GCGONE', 'x')", "INSERT INTO notes VALUES (NULL, 'y')")
        companion.reset()
        init_db(db_path=db)
        assert _rows(db) == [None, "GC1", "GC2", "GC3"]
        assert _rows(db, "settings") == ["gc_code"]

    def test_checked_once_per_session(self, tmp_path, monkeypatch):
        db = _database(tmp_path / "Found.db")
        companion.reset()
        calls = []
        real = companion._check_rows
        monkeypatch.setattr(companion, "_check_rows",
                            lambda conn, pragma: calls.append(pragma) or real(conn, pragma))
        for _ in range(3):
            assert companion.prepare(db) is Status.OK
        assert calls == ["quick_check"]

    def test_damaged_companion_is_moved_aside_and_reported(self, tmp_path, monkeypatch):
        db = _database(tmp_path / "Found.db")
        companion.reset()
        monkeypatch.setattr(companion, "_check_rows", lambda conn, pragma: ["page 3 is never used"])

        assert companion.prepare(db) is Status.BROKEN
        assert not companion.exists(db)
        assert companion.status(db) is Status.MISSING
        moved = list(tmp_path.glob("Found.companion.corrupt-*.db3"))
        assert len(moved) == 1
        notices = companion.take_notices()
        assert [(n.kind, n.path) for n in notices] == [(companion.NOTICE_CORRUPT, moved[0])]
        assert companion.take_notices() == []

    def test_not_a_database_is_moved_aside(self, tmp_path):
        db = _database(tmp_path / "Found.db", notes=False)
        companion.companion_path(db).write_bytes(b"x" * 4096)
        init_db(db_path=db)
        with get_session() as s:
            assert s.query(Cache).count() == 3
        assert len(list(tmp_path.glob("Found.companion.corrupt-*.db3"))) == 1
        # The next write starts a new one.
        companion.create(db)
        assert companion.status(db) is Status.OK

    def test_a_locked_companion_is_left_in_place(self, tmp_path, monkeypatch):
        db = _database(tmp_path / "Found.db")
        companion.reset()

        def locked(path):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(companion, "_connect", locked)
        assert companion.prepare(db) is Status.BROKEN
        assert companion.exists(db)
        assert companion.take_notices() == []

    @pytest.mark.parametrize("kind", ["foreign", "newer"])
    def test_foreign_and_newer_are_reported_once(self, tmp_path, kind):
        db = _database(tmp_path / "Found.db")
        if kind == "foreign":
            companion.set_main_uuid(db, "someone else")
        else:
            conn = sqlite3.connect(str(companion.companion_path(db)))
            conn.execute(f"PRAGMA user_version = {companion.SCHEMA_VERSION + 1}")
            conn.close()
        companion.reset()
        companion.prepare(db)
        companion.prepare(db)
        assert [n.kind for n in companion.take_notices()] == [kind]
        assert companion.exists(db)


class TestNoticeDialog:
    def test_shows_one_warning_for_all_notices(self, tmp_path, monkeypatch):
        from opensak.gui import mainwindow

        db = _database(tmp_path / "Found.db")
        companion.set_main_uuid(db, "someone else")
        companion.reset()
        companion.prepare(db)
        with patch.object(mainwindow.QMessageBox, "warning") as warning:
            mainwindow.MainWindow.show_companion_notices(MagicMock())
            mainwindow.MainWindow.show_companion_notices(MagicMock())
        assert warning.call_count == 1
        assert "Found.db" in warning.call_args.args[2]

    def test_premigration_notices_show_them_too(self, monkeypatch):
        from opensak.gui import mainwindow

        window = MagicMock()
        with patch("opensak.backup.premigration.take_notices", return_value=[]):
            mainwindow.MainWindow.show_premigration_backup_notices(window)
        window.show_companion_notices.assert_called_once()


# ── Maintenance (#1048) ───────────────────────────────────────────────────────

class TestMaintenance:
    def test_info(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        _register(db, "settings", companion.KIND_FREE)
        result = companion.info(db)
        assert result is not None
        assert result.schema_version == companion.SCHEMA_VERSION
        assert result.file_bytes > 0
        tables = {t.name: t for t in result.tables}
        assert set(tables) == {"notes", "settings"}
        assert tables["notes"].kind == companion.KIND_CACHE and tables["notes"].rows == 3
        assert tables["notes"].owner is None
        assert tables["settings"].owner == "test.lua"

    def test_info_and_check_without_companion(self, tmp_path):
        db = _database(tmp_path / "Found.db", notes=False)
        assert companion.info(db) is None
        assert companion.check(db) is None

    def test_check_ok(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        result = companion.check(db, thorough=True)
        assert result is not None and result.ok
        assert result.unregistered == ("notes", "settings")

    def test_check_finds_orphans_missing_tables_and_uuid(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        _register(db, "gone", companion.KIND_FREE)
        _sql(db, "INSERT INTO notes VALUES ('GCGONE', 'x')")
        companion.set_main_uuid(db, "someone else")
        result = companion.check(db)
        assert result is not None and not result.ok
        assert result.orphans == {"notes": 1}
        assert result.missing == ("gone",)
        assert result.uuid_mismatch
        assert _rows(db) == ["GC1", "GC2", "GC3", "GCGONE"]      # changes nothing

    def test_optimize_and_checkpoint(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        assert companion.optimize(db)
        assert companion.checkpoint(db)
        assert not companion.optimize(tmp_path / "None.db")

    def test_compact_both_files(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        _sql(db, "CREATE TABLE big (gc_code TEXT, blob TEXT)",
             "INSERT INTO big SELECT 'GC1', hex(randomblob(2000)) FROM "
             "(WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i < 500) SELECT i FROM n)",
             "DROP TABLE big")
        before = companion.info(db)
        assert before is not None and before.free_bytes > 0

        main, comp = maintenance.vacuum_with_companion(db, force=True)
        assert main.ran and comp is not None and comp.ran
        after = companion.info(db)
        assert after is not None and after.free_bytes == 0
        assert _rows(db) == ["GC1", "GC2", "GC3"]

    def test_compact_without_companion(self, tmp_path):
        db = _database(tmp_path / "Found.db", notes=False)
        main, comp = maintenance.vacuum_with_companion(db, force=True)
        assert main.ran and comp is None

    def test_small_companion_is_not_worth_compacting(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        _main, comp = maintenance.vacuum_with_companion(db)
        assert comp is not None and comp.status == maintenance.NOT_WORTHWHILE

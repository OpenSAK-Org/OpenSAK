"""
tests/unit-tests/test_db_maintenance_1048.py — database maintenance (#1048)
and the "Companion data" part of it (#1149 phase 5): Info, Check, Optimize,
Checkpoint and Compact for a database and its companion, the automatic
Optimize, the companion table actions, and the Maintenance dialog opened
from the Database Manager.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("pytestqt")

from opensak.db import companion, maintenance
from opensak.db.database import (
    dispose_engine, get_engine, get_session, init_db, optimize_database, release_connections,
)
from opensak.db.db_settings import ensure_seeded
from opensak.db.models import Cache
from opensak.gui.dialogs import database_maintenance_dialog as dmd
from opensak.lang import load_language, tr
from opensak.utils import flags


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    load_language("en")
    monkeypatch.setattr(flags, "companion_db", True)
    companion.reset()
    yield
    dispose_engine()
    companion.reset()


def _database(path: Path, codes=("GC1", "GC2", "GC3"), with_companion=True) -> Path:
    """A database with caches *codes*; with a companion holding a
    cache-linked table `notes`, a free table `settings`, a view and
    OpenSAK's own table `custom_values`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path=path)
    with get_session() as s:
        for code in codes:
            s.add(Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0))
    dispose_engine(path)
    ensure_seeded(path, path.stem)
    if with_companion:
        _sql(path,
             "CREATE TABLE notes (gc_code TEXT PRIMARY KEY, note TEXT)",
             "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)",
             "CREATE VIEW note_view AS SELECT * FROM notes",
             "CREATE TABLE IF NOT EXISTS custom_values (gc_code TEXT PRIMARY KEY)",
             *(f"INSERT INTO notes VALUES ('{c}', 'note {c}')" for c in codes))
        _register(path, "settings", "free", owner="C:/gone/away/settings_macro.lua",
                  owner_name="Settings macro")
        _register(path, "custom_values", "cache", owner=companion.OWNER_OPENSAK)
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


def _register(db_path: Path, name: str, kind: str, owner: str, owner_name=None) -> None:
    conn = sqlite3.connect(str(companion.companion_path(db_path)))
    try:
        with conn:
            companion.register(conn, name, owner, owner_name, kind, schema="main")
    finally:
        conn.close()


def _companion_tables(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(companion.companion_path(db_path)))
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def _registry(db_path: Path) -> dict:
    conn = sqlite3.connect(str(companion.companion_path(db_path)))
    try:
        return companion.registry(conn, "main")
    finally:
        conn.close()


def _has_stats(path: Path) -> bool:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'sqlite_stat1'").fetchone() is not None
    finally:
        conn.close()


def _drop_stats(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DROP TABLE IF EXISTS sqlite_stat1")
        conn.commit()
    finally:
        conn.close()


def _make_free_pages(db_path: Path, rows: int = 3000) -> None:
    """Fill and empty a scratch table, so the file has free pages."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE scratch (blob TEXT)")
        conn.execute(
            "INSERT INTO scratch SELECT hex(randomblob(2000)) FROM (WITH RECURSIVE n(i) AS "
            f"(SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i < {rows}) SELECT i FROM n)")
        conn.commit()
        conn.execute("DROP TABLE scratch")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


# ── Info, Check, Optimize, Checkpoint of the main database ────────────────────

class TestMainDatabase:
    def test_file_info(self, tmp_path):
        db = _database(tmp_path / "Found.db", with_companion=False)
        info = maintenance.file_info(db)
        assert info.rows["caches"] == 3 and info.rows["logs"] == 0
        assert info.schema_version > 0
        assert info.sqlite_version == sqlite3.sqlite_version
        assert info.file_bytes == db.stat().st_size

    def test_file_info_shows_free_space(self, tmp_path):
        db = _database(tmp_path / "Found.db", with_companion=False)
        _make_free_pages(db, 500)
        info = maintenance.file_info(db)
        assert info.stats.free_bytes > 500_000
        assert 0 < info.free_fraction < 1

    def test_missing_file_is_an_error_not_a_new_database(self, tmp_path):
        with pytest.raises(sqlite3.Error):
            maintenance.file_info(tmp_path / "None.db")
        assert not (tmp_path / "None.db").exists()

    def test_check_ok(self, tmp_path):
        db = _database(tmp_path / "Found.db", with_companion=False)
        for thorough in (False, True):
            report = maintenance.check_file(db, thorough)
            assert report.ok and report.thorough is thorough

    def test_check_finds_foreign_key_problems(self, tmp_path):
        db = _database(tmp_path / "Found.db", with_companion=False)
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("INSERT INTO logs (cache_id, log_type) VALUES (9999, 'Found it')")
        conn.commit()
        conn.close()
        report = maintenance.check_file(db)
        assert not report.ok and report.foreign_key_problems == 1

    def test_optimize_creates_statistics(self, tmp_path):
        db = _database(tmp_path / "Found.db", with_companion=False)
        _drop_stats(db)
        assert maintenance.optimize_file(db)
        assert _has_stats(db)
        assert not maintenance.optimize_file(tmp_path / "None.db")

    def test_optimize_before_3_46_limits_the_first_analyze(self, tmp_path, monkeypatch):
        # The first full ANALYZE of a large database froze the GUI.
        monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 45, 3))
        db = _database(tmp_path / "Found.db", with_companion=False)
        _drop_stats(db)
        conn = sqlite3.connect(str(db))
        try:
            statements: list[str] = []
            conn.set_trace_callback(statements.append)
            maintenance.optimize(conn)
            assert conn.execute("PRAGMA analysis_limit").fetchone()[0] == 1000
        finally:
            conn.close()
        assert statements.index("PRAGMA analysis_limit=1000") < statements.index("ANALYZE")
        assert _has_stats(db)

    def test_checkpoint_empties_the_wal(self, tmp_path):
        db = _database(tmp_path / "Found.db", with_companion=False)
        init_db(db_path=db)
        with get_session() as s:
            s.add(Cache(gc_code="GC9", name="GC9", cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0))
        assert Path(f"{db}-wal").stat().st_size > 0
        # The engine's idle connection stays open (closing the last one
        # would checkpoint and remove the -wal file by itself).
        assert maintenance.checkpoint_file(db)
        assert Path(f"{db}-wal").stat().st_size == 0


# ── Optimize on its own (#1048) ───────────────────────────────────────────────

class TestAutomaticOptimize:
    def test_opening_creates_statistics(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        _drop_stats(db)
        _drop_stats(companion.companion_path(db))
        init_db(db_path=db)
        assert _has_stats(db)
        assert _has_stats(companion.companion_path(db))

    def test_closing_optimizes(self, tmp_path):
        db = _database(tmp_path / "Found.db", with_companion=False)
        init_db(db_path=db)
        _drop_stats(db)
        dispose_engine(db)
        assert _has_stats(db)

    def test_switching_optimizes_the_old_database(self, tmp_path):
        first = _database(tmp_path / "A.db", with_companion=False)
        second = _database(tmp_path / "B.db", with_companion=False)
        init_db(db_path=first)
        _drop_stats(first)
        init_db(db_path=second)
        assert _has_stats(first)

    def test_never_raises(self, tmp_path):
        optimize_database(tmp_path / "None.db")
        (tmp_path / "Junk.db").write_bytes(b"not a database" * 100)
        optimize_database(tmp_path / "Junk.db")
        dispose_engine()
        optimize_database()      # no active database

    def test_after_moving_caches(self, tmp_path):
        from opensak.db.transfer import transfer_caches

        source = _database(tmp_path / "A.db", with_companion=False)
        target = _database(tmp_path / "B.db", codes=(), with_companion=False)
        _drop_stats(source)
        _drop_stats(target)
        assert transfer_caches(["GC1"], source, target) == 1
        assert _has_stats(source) and _has_stats(target)

    @pytest.mark.skipif(not flags.lua_macro_write, reason="needs the lua-macro-write flag")
    def test_after_deleting_caches_in_a_macro(self, tmp_path):
        from opensak.macro import MacroRuntime, WriteApproval, db_access

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

        db = _database(tmp_path / "Found.db", with_companion=False)
        init_db(db_path=db)
        _drop_stats(db)
        db_access.reset_session()
        MacroRuntime(_Host(), output=lambda _line: None).run('opensak.delete("GC1")')
        db_access.reset_session()
        assert _has_stats(db)


# ── release_connections ───────────────────────────────────────────────────────

class TestReleaseConnections:
    def test_only_the_active_database(self, tmp_path):
        db = _database(tmp_path / "Found.db", with_companion=False)
        other = _database(tmp_path / "Other.db", with_companion=False)
        init_db(db_path=db)
        assert not release_connections(other)
        assert release_connections(db)
        # The engine stays: the next session reconnects.
        with get_session() as s:
            assert s.query(Cache).count() == 3
        assert get_engine() is not None


# ── Companion data (#1149 phase 5) ────────────────────────────────────────────

class TestCompanionData:
    def test_info_lists_tables_views_owners_and_sizes(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        info = companion.info(db)
        assert info is not None
        tables = {t.name: t for t in info.tables}
        assert set(tables) == {"notes", "settings", "note_view", "custom_values"}
        assert tables["notes"].kind == companion.KIND_CACHE
        assert tables["notes"].rows == 3 and tables["notes"].est_bytes > 0
        assert tables["note_view"].type == "view" and tables["note_view"].rows == 0
        assert tables["settings"].owner_missing and tables["settings"].owner_name == "Settings macro"
        assert tables["custom_values"].owned_by_opensak and not tables["custom_values"].owner_missing
        assert tables["notes"].owner is None and not tables["notes"].owner_missing

    def test_size_estimate_grows_with_the_data(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        _sql(db, "CREATE TABLE big (gc_code TEXT, blob TEXT)",
             "INSERT INTO big SELECT 'GC1', hex(randomblob(500)) FROM (WITH RECURSIVE n(i) AS "
             "(SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i < 200) SELECT i FROM n)")
        conn = sqlite3.connect(str(companion.companion_path(db)))
        try:
            estimate = companion.size_estimate(conn, "big")
        finally:
            conn.close()
        assert 200 * 1000 <= estimate <= 200 * 1200

    def test_owner_missing(self, tmp_path):
        macro = tmp_path / "here.lua"
        macro.write_text("-- Name: Here\n", encoding="utf-8")
        assert not companion.owner_missing(str(macro))
        assert companion.owner_missing(str(tmp_path / "gone.lua"))
        assert not companion.owner_missing(None)
        assert not companion.owner_missing(companion.OWNER_OPENSAK)

    def test_clear_table(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        assert companion.clear_table(db, "NOTES") == 3
        info = companion.info(db)
        assert info is not None
        assert {t.name: t.rows for t in info.tables}["notes"] == 0

    def test_drop_table_and_view_unregister_them(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        companion.drop_table(db, "note_view")
        companion.drop_table(db, "settings")
        assert "settings" not in _companion_tables(db)
        assert "settings" not in _registry(db)

    def test_opensak_tables_are_refused(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        for action in (lambda: companion.clear_table(db, "custom_values"),
                       lambda: companion.drop_table(db, "custom_values"),
                       lambda: companion.reassign_owner(db, "custom_values", "x.lua", "X"),
                       lambda: companion.drop_table(db, "opensak_tables"),
                       lambda: companion.clear_table(db, "no_such_table")):
            with pytest.raises(companion.CompanionError):
                action()
        assert {"custom_values", "opensak_tables"} <= _companion_tables(db)

    def test_reassign_a_missing_owner(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        companion.reassign_owner(db, "settings", "c:\\macros\\new.lua", "New macro")
        row = _registry(db)["settings"]
        assert (row["owner"], row["owner_name"], row["kind"]) == (
            "c:\\macros\\new.lua", "New macro", "free")

    def test_reassign_registers_an_unregistered_table(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        companion.reassign_owner(db, "notes", "c:\\macros\\new.lua", "New macro")
        row = _registry(db)["notes"]
        assert (row["kind"], row["code_column"]) == ("cache", "gc_code")

    def test_reassign_needs_a_macro(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        with pytest.raises(companion.CompanionError):
            companion.reassign_owner(db, "settings", companion.OWNER_OPENSAK, None)

    def test_cleanup_removes_orphans_and_stale_registry_rows(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        _sql(db, "INSERT INTO notes VALUES ('GCGONE', 'x')")
        _register(db, "gone", "free", owner="c:\\macros\\x.lua")
        result = companion.cleanup(db)
        assert result.orphans == {"notes": 1} and result.registry_rows == ("gone",)
        assert result.total == 2
        check = companion.check(db)
        assert check is not None and not check.orphans and not check.missing
        assert companion.cleanup(db).total == 0

    def test_actions_without_companion(self, tmp_path):
        db = _database(tmp_path / "Found.db", with_companion=False)
        with pytest.raises(companion.CompanionError):
            companion.clear_table(db, "notes")
        assert not companion.move_aside(db, "test")

    def test_move_aside_keeps_the_main_database(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        size = db.stat().st_size
        assert companion.move_aside(db, "test")
        assert not companion.exists(db) and db.stat().st_size == size
        assert list(tmp_path.glob("Found.companion.corrupt-*.db3"))

    def test_move_aside_while_open(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        init_db(db_path=db)
        with get_session() as s:
            s.query(Cache).count()       # a pooled connection with the companion attached
        release_connections(db)
        assert companion.move_aside(db, "test")


# ── The jobs and texts of the dialog ──────────────────────────────────────────

class TestJobs:
    def test_info_and_check(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        steps: list[str] = []
        info = dmd.run_info(db, steps.append)
        assert info.main is not None and info.companion is not None
        assert len(steps) == 2
        outcome = dmd.run_check(db, False, steps.append)
        lines = dmd.check_lines(outcome)
        assert lines[0] == tr("maint_check_ok", file=tr("maint_db_group"))
        assert any("notes" in line for line in lines)     # unregistered, reported but allowed

    def test_check_without_companion_flag(self, tmp_path, monkeypatch):
        db = _database(tmp_path / "Found.db")
        monkeypatch.setattr(flags, "companion_db", False)
        assert dmd.run_info(db, lambda _s: None).companion is None
        assert dmd.run_check(db, False, lambda _s: None).companion is None

    def test_check_lines_for_problems(self):
        outcome = dmd.CheckOutcome(
            main=maintenance.CheckReport(Path("x.db"), False, problems=tuple(f"p{i}" for i in range(30))),
            companion=companion.CheckResult(Path("x.companion.db3"), orphans={"notes": 2},
                                            missing=("gone",), uuid_mismatch=True),
        )
        lines = dmd.check_lines(outcome)
        assert lines[0] == tr("maint_check_problems", file=tr("maint_db_group"), count=30)
        assert "  …" in lines
        assert tr("maint_check_cleanup_hint") == lines[-1]
        assert any("notes" in line and "2" in line for line in lines)

    def test_optimize_and_checkpoint(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        assert dmd.run_optimize(db, lambda _s: None) == [
            tr("maint_optimize_done", file=tr("maint_db_group")),
            tr("maint_optimize_done", file=tr("companion_notice_title")),
        ]
        assert len(dmd.run_checkpoint(db, lambda _s: None)) == 2

    def test_compact_only_where_it_pays_off(self, tmp_path):
        db = _database(tmp_path / "Found.db")
        assert dmd.compact_targets(dmd.run_info(db, lambda _s: None)) == []
        _make_free_pages(db)
        targets = dmd.compact_targets(dmd.run_info(db, lambda _s: None))
        assert [t[1] for t in targets] == [db]
        entry = SimpleNamespace(name="Found", path=db)
        outcome = dmd.run_compact(entry, targets, False, lambda _s: None)
        assert outcome.results[0].ran
        assert maintenance.file_info(db).stats.free_bytes == 0
        lines = dmd.compact_lines(outcome)
        assert lines[0].startswith(tr("maint_db_group") + ":")

    def test_compact_with_backup(self, tmp_path, monkeypatch):
        db = _database(tmp_path / "Found.db")
        _make_free_pages(db)
        targets = dmd.compact_targets(dmd.run_info(db, lambda _s: None))
        made = SimpleNamespace(backup_set=SimpleNamespace(path=tmp_path / "set"))
        with patch("opensak.backup.backupset.write_backup_set", return_value=made) as write:
            outcome = dmd.run_compact(SimpleNamespace(name="Found", path=db), targets, True,
                                      lambda _s: None)
        write.assert_called_once()
        assert outcome.backup_path == tmp_path / "set"
        assert dmd.compact_lines(outcome)[0] == tr("maint_backup_done", path=str(tmp_path / "set"))

    def test_owner_and_kind_text(self):
        def table(**kw):
            values = dict(name="t", owner=None, owner_name=None, kind=companion.KIND_FREE, rows=0)
            values.update(kw)
            return companion.TableInfo(**values)
        assert dmd.owner_text(table()) == tr("maint_owner_unregistered")
        assert dmd.owner_text(table(owner=companion.OWNER_OPENSAK)) == "OpenSAK"
        assert dmd.owner_text(table(owner="c:/gone/x.lua")) == tr("maint_owner_missing", name="x")
        assert dmd.kind_text(table(type="view")) == tr("maint_kind_view")
        assert dmd.kind_text(table(kind=companion.KIND_CACHE)) == tr("maint_kind_cache")


# ── The dialog ────────────────────────────────────────────────────────────────

def _dialog(qtbot, db: Path) -> dmd.DatabaseMaintenanceDialog:
    dlg = dmd.DatabaseMaintenanceDialog(SimpleNamespace(name=db.stem, path=db))
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: not dlg.is_running() and dlg._info.main is not None, timeout=10000)
    return dlg


def _wait(qtbot, dlg) -> None:
    qtbot.waitUntil(lambda: not dlg.is_running() and dlg._btn_check.isEnabled(), timeout=20000)


def _select(dlg, name: str) -> None:
    for i in range(dlg._tables.topLevelItemCount()):
        item = dlg._tables.topLevelItem(i)
        if item is not None and item.text(0) == name:
            dlg._tables.setCurrentItem(item)
            return
    raise AssertionError(f"{name} not listed")


class TestDialog:
    def test_shows_info_and_companion_tables(self, qtbot, tmp_path):
        db = _database(tmp_path / "Found.db")
        dlg = _dialog(qtbot, db)
        assert dlg._rows.text().startswith("3 caches")
        assert dlg._tables.topLevelItemCount() == 4
        assert not dlg._companion_warning.isVisible()

    def test_without_companion_flag_there_is_no_companion_section(self, qtbot, tmp_path,
                                                                 monkeypatch):
        db = _database(tmp_path / "Found.db")
        monkeypatch.setattr(flags, "companion_db", False)
        dlg = _dialog(qtbot, db)
        assert dlg._companion_group is None and dlg.selected_table() is None

    def test_without_companion(self, qtbot, tmp_path):
        db = _database(tmp_path / "Found.db", with_companion=False)
        dlg = _dialog(qtbot, db)
        assert dlg._companion_summary.text() == tr("maint_companion_none")
        assert not dlg._btn_cleanup.isEnabled()

    def test_buttons_follow_the_selected_table(self, qtbot, tmp_path):
        dlg = _dialog(qtbot, _database(tmp_path / "Found.db"))
        _select(dlg, "custom_values")
        assert not (dlg._btn_clear.isEnabled() or dlg._btn_drop.isEnabled()
                    or dlg._btn_reassign.isEnabled())
        _select(dlg, "settings")                 # owner missing
        assert dlg._btn_clear.isEnabled() and dlg._btn_drop.isEnabled()
        assert dlg._btn_reassign.isEnabled()
        _select(dlg, "note_view")                # a view has no rows of its own
        assert not dlg._btn_clear.isEnabled() and dlg._btn_drop.isEnabled()

    def test_check(self, qtbot, tmp_path):
        dlg = _dialog(qtbot, _database(tmp_path / "Found.db"))
        dlg._check()
        _wait(qtbot, dlg)
        assert tr("maint_check_ok", file=tr("maint_db_group")) in dlg._results.toPlainText()

    def test_drop_table(self, qtbot, tmp_path):
        db = _database(tmp_path / "Found.db")
        dlg = _dialog(qtbot, db)
        _select(dlg, "settings")
        with patch.object(dmd.QMessageBox, "warning", return_value=dmd.QMessageBox.StandardButton.Yes):
            dlg._drop_table()
        _wait(qtbot, dlg)
        qtbot.waitUntil(lambda: dlg._tables.topLevelItemCount() == 3, timeout=10000)
        assert "settings" not in _companion_tables(db)
        assert tr("maint_dropped", table="settings") in dlg._results.toPlainText()

    def test_drop_cancelled(self, qtbot, tmp_path):
        db = _database(tmp_path / "Found.db")
        dlg = _dialog(qtbot, db)
        _select(dlg, "settings")
        with patch.object(dmd.QMessageBox, "warning", return_value=dmd.QMessageBox.StandardButton.No):
            dlg._drop_table()
        assert not dlg.is_running() and "settings" in _companion_tables(db)

    def test_reassign_owner(self, qtbot, tmp_path):
        db = _database(tmp_path / "Found.db")
        macro = tmp_path / "macros" / "renamed.lua"
        macro.parent.mkdir()
        macro.write_text("-- Name: Renamed macro\nprint(1)\n", encoding="utf-8")
        dlg = _dialog(qtbot, db)
        _select(dlg, "settings")
        with patch.object(dmd, "ReassignOwnerDialog") as chooser:
            chooser.return_value.exec.return_value = dmd.QDialog.DialogCode.Accepted
            chooser.return_value.macro_path = macro
            dlg._reassign_owner()
        _wait(qtbot, dlg)
        from opensak.macro.db_access import macro_key
        row = _registry(db)["settings"]
        assert (row["owner"], row["owner_name"]) == (macro_key(macro), "Renamed macro")

    def test_reassign_dialog_lists_the_macros(self, qtbot, tmp_path, monkeypatch):
        (tmp_path / "a.lua").write_text("-- Name: Alpha\n", encoding="utf-8")
        monkeypatch.setattr("opensak.config.get_macros_dir", lambda: tmp_path)
        dlg = dmd.ReassignOwnerDialog("settings")
        qtbot.addWidget(dlg)
        assert dlg.macro_path == tmp_path / "a.lua"

    def test_cleanup(self, qtbot, tmp_path):
        db = _database(tmp_path / "Found.db")
        _sql(db, "INSERT INTO notes VALUES ('GCGONE', 'x')")
        dlg = _dialog(qtbot, db)
        dlg._cleanup()
        _wait(qtbot, dlg)
        assert tr("maint_cleanup_done", file=tr("companion_notice_title"), orphans=1,
                  registry=0) in dlg._results.toPlainText()

    def test_compact_nothing_to_do(self, qtbot, tmp_path):
        dlg = _dialog(qtbot, _database(tmp_path / "Found.db"))
        with patch.object(dmd.QMessageBox, "information") as info:
            dlg._compact()
        info.assert_called_once()
        assert not dlg.is_running()

    def test_compact_the_active_database(self, qtbot, tmp_path):
        db = _database(tmp_path / "Found.db")
        _make_free_pages(db)
        init_db(db_path=db)
        dlg = _dialog(qtbot, db)
        with patch.object(dlg, "_confirm_compact", return_value=False) as confirm:
            dlg._compact()
        confirm.assert_called_once()
        _wait(qtbot, dlg)
        assert "→" in dlg._results.toPlainText()
        assert maintenance.file_info(db).stats.free_bytes == 0
        with get_session() as s:            # the active database is still open
            assert s.query(Cache).count() == 3

    def test_compact_cancelled(self, qtbot, tmp_path):
        db = _database(tmp_path / "Found.db")
        _make_free_pages(db)
        dlg = _dialog(qtbot, db)
        with patch.object(dlg, "_confirm_compact", return_value=None):
            dlg._compact()
        assert not dlg.is_running()
        assert maintenance.file_info(db).stats.free_bytes > 0

    def test_backup_is_offered_for_large_databases(self, qtbot, tmp_path):
        db = _database(tmp_path / "Found.db")
        dlg = _dialog(qtbot, db)
        seen = []

        def fake_exec(box):
            seen.append(box.checkBox().isChecked())
            return 0
        with patch.object(dmd.QMessageBox, "exec", fake_exec):
            assert dlg._confirm_compact(1, 2) is None
            dlg._info.main = maintenance.FileInfo(
                db, dmd.BACKUP_DEFAULT_BYTES, 0, maintenance.PageStats(1, 1, 0), 1, "3", {})
            dlg._confirm_compact(1, 2)
        assert seen == [False, True]

    def test_close_waits_for_a_running_step(self, qtbot, tmp_path):
        dlg = _dialog(qtbot, _database(tmp_path / "Found.db"))
        with patch.object(dmd.QDialog, "reject") as base_reject:
            with patch.object(dlg, "is_running", return_value=True):
                dlg.reject()
            base_reject.assert_not_called()
            dlg.reject()
            base_reject.assert_called_once()


class TestManagerButton:
    def test_maintenance_button(self, qtbot, tmp_path, monkeypatch):
        from opensak.gui.dialogs import database_dialog as dd

        db = _database(tmp_path / "Found.db")
        entry = SimpleNamespace(name="Found", path=db, exists=True, size_mb=1.0, modified=None)
        missing = SimpleNamespace(name="Gone", path=tmp_path / "Gone.db", exists=False,
                                  size_mb=0.0, modified=None)

        class _Manager:
            active = entry
            databases = [entry, missing]

        monkeypatch.setattr(dd, "get_db_manager", lambda: _Manager())
        dlg = dd.DatabaseManagerDialog()
        qtbot.addWidget(dlg)
        dlg._list.setCurrentRow(1)
        assert not dlg._btn_maintenance.isEnabled()
        dlg._list.setCurrentRow(0)
        assert dlg._btn_maintenance.isEnabled()
        with patch("opensak.gui.dialogs.database_maintenance_dialog.DatabaseMaintenanceDialog") as m:
            dlg._open_maintenance()
        m.assert_called_once_with(entry, dlg)

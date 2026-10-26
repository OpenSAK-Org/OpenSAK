"""
tests/unit-tests/test_companion_macros_1034.py — the companion database
(#1034), step 3: macros. The companion is attached to the macros' SQL
connection as `companion`; every macro reads every table, a macro creates,
changes and drops only its own (owner recorded in opensak_tables), triggers
are refused; opensak.companion(), companion_table{}, companion_tables();
the companion of a database attached with opensak.attach().
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from opensak.db import companion
from opensak.db.database import get_engine, session_for
from opensak.db.manager import get_db_manager
from opensak.db.models import Cache, Log
from opensak.export.file_export import active_database_name
from opensak.macro import FolderPermission, MacroError, MacroRuntime, WriteApproval, db_access
from opensak.utils import flags


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(flags, "companion_db", True)
    companion.reset()
    db_access.reset_session()
    yield
    companion.reset()
    db_access.reset_session()


class Host:
    """A MacroHost on the real DatabaseManager and the active database."""

    def __init__(self):
        self.asked = 0
        self.filtered: list = []

    def database_name(self):
        return active_database_name()

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def approve_macro_write(self, macro, change=None, editing=False):
        self.asked += 1
        return WriteApproval.SESSION

    def caches_changed(self, codes, added):
        pass

    def caches_removed(self):
        pass

    def apply_filter(self, filterset, label):
        self.filtered.append(filterset)
        return 0

    def end_macro(self):
        pass


def _add(path, *codes):
    with session_for(path) as s:
        for code in codes:
            s.add(Cache(gc_code=code, name=f"{code} name", cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0))


@pytest.fixture
def active():
    """The active database "Active" with GC1..GC3, no companion yet."""
    manager = get_db_manager()
    db = manager.new_database("Active")
    manager.switch_to(db)
    _add(db.path, "GC1", "GC2", "GC3")
    return Path(db.path)


def _run(source, folder, name="ftf.lua", host=None):
    """Run *source* as the macro file *folder*/*name* (None: an unsaved macro)."""
    out: list[str] = []
    host = host or Host()
    MacroRuntime(
        host, output=out.append,
        folder_permissions=[FolderPermission(str(folder), read=True, write=True)],
    ).run(source, chunk_name=name or "macro", base_dir=folder if name else None)
    return host, out


def _fails(source, folder, name="other.lua") -> str:
    with pytest.raises(MacroError) as info:
        _run(source, folder, name)
    return str(info.value)


def _companion_sql(db_path: Path, query: str) -> list[tuple]:
    conn = sqlite3.connect(str(companion.companion_path(db_path)))
    try:
        rows = conn.execute(query).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def _registry(db_path: Path) -> dict[str, tuple]:
    return {name: tuple(rest) for name, *rest in _companion_sql(
        db_path, "SELECT name, owner, owner_name, kind, code_column, description "
                 "FROM opensak_tables ORDER BY name")}


_FTF = """
    opensak.companion()
    opensak.sql("CREATE TABLE IF NOT EXISTS companion.ftf_info "
        .. "(gc_code TEXT PRIMARY KEY, ftf_date TEXT, ftf_by TEXT)")
    opensak.sql("INSERT INTO companion.ftf_info VALUES ('GC1', '2024-05-01', 'someone')")
    opensak.sql("INSERT INTO companion.ftf_info VALUES ('GC2', '2024-06-01', 'me')")
"""


@pytest.fixture
def ftf(active, tmp_path):
    """The companion of "Active", with ftf.lua's table ftf_info."""
    _run(_FTF, tmp_path)
    return active


# ── opensak.companion() and owners ────────────────────────────────────────────

class TestCompanion:
    def test_creates_the_file_and_records_the_owner(self, active, tmp_path):
        (tmp_path / "ftf.lua").write_text("-- Name: FTF Tracker\n", encoding="utf-8")
        host, out = _run(_FTF + "print(opensak.companion())", tmp_path)
        path = companion.companion_path(active)
        assert out == [f"opensak.companion: created {path}", str(path)]
        assert host.asked == 0
        owner = db_access.macro_key(tmp_path / "ftf.lua")
        assert _registry(active) == {
            "ftf_info": (owner, "FTF Tracker", "cache", "gc_code", None),
        }

    def test_attached_in_later_runs_without_asking(self, ftf, tmp_path):
        _, out = _run("""
            for _, r in ipairs(opensak.sql("SELECT c.gc_code, f.ftf_by FROM caches c "
                .. "JOIN companion.ftf_info f USING (gc_code) ORDER BY 1")) do
                print(r.gc_code, r.ftf_by)
            end
            print(opensak.companion() ~= nil)
        """, tmp_path, name="reader.lua")
        assert out == ["GC1\tsomeone", "GC2\tme", "true"]

    def test_tables_lists_the_companion(self, ftf, tmp_path):
        _, out = _run("""
            local t = {}
            for _, name in ipairs(opensak.tables()) do t[name] = true end
            print(t["companion.ftf_info"], t["companion.opensak_tables"])
            print(#opensak.columns("companion.ftf_info"))
        """, tmp_path, name="reader.lua")
        assert out == ["true\ttrue", "3"]

    def test_unsaved_macro_reads_but_cannot_create(self, ftf, tmp_path):
        _, out = _run('print(#opensak.sql("SELECT * FROM companion.ftf_info"))', tmp_path, name=None)
        assert out == ["2"]
        with pytest.raises(MacroError, match="without a file can only read"):
            _run('opensak.sql("CREATE TABLE companion.mine (x)")', tmp_path, name=None)

    def test_unsaved_macro_cannot_create_the_file(self, active, tmp_path):
        with pytest.raises(MacroError, match="save the macro first"):
            _run("opensak.companion()", tmp_path, name=None)
        assert not companion.exists(active)

    def test_not_inside_a_transaction(self, active, tmp_path):
        with pytest.raises(MacroError, match="inside opensak.transaction"):
            _run("opensak.transaction(function() opensak.companion() end)", tmp_path)

    def test_without_the_flag(self, ftf, tmp_path, monkeypatch):
        monkeypatch.setattr(flags, "companion_db", False)
        _, out = _run("print(opensak.companion, opensak.companion_tables)", tmp_path)
        assert out == ["nil\tnil"]
        with pytest.raises(MacroError, match="no such table: companion.ftf_info"):
            _run('opensak.sql("SELECT * FROM companion.ftf_info")', tmp_path)


# ── What a macro may do there ─────────────────────────────────────────────────

class TestAccess:
    def test_owner_changes_alters_and_drops(self, ftf, tmp_path):
        host, _ = _run("""
            opensak.sql("UPDATE companion.ftf_info SET ftf_by = 'x' WHERE gc_code = 'GC1'")
            opensak.sql("DELETE FROM companion.ftf_info WHERE gc_code = 'GC2'")
            opensak.sql("ALTER TABLE companion.ftf_info ADD COLUMN note TEXT")
            opensak.sql("CREATE INDEX companion.ix_ftf_by ON ftf_info (ftf_by)")
            opensak.sql("DROP INDEX companion.ix_ftf_by")
            opensak.sql("CREATE TABLE companion.scratch (id INTEGER PRIMARY KEY AUTOINCREMENT, v)")
            opensak.sql("INSERT INTO companion.scratch (v) VALUES (1)")
            opensak.sql("DROP TABLE companion.scratch")
        """, tmp_path)
        assert host.asked == 0
        assert _companion_sql(ftf, "SELECT gc_code, ftf_by, note FROM ftf_info") == [("GC1", "x", None)]
        assert set(_registry(ftf)) == {"ftf_info"}

    def test_rename_follows_the_registry(self, ftf, tmp_path):
        _run('opensak.sql("ALTER TABLE companion.ftf_info RENAME TO first_finds")', tmp_path)
        registry = _registry(ftf)
        assert set(registry) == {"first_finds"}
        assert registry["first_finds"][0] == db_access.macro_key(tmp_path / "ftf.lua")

    @pytest.mark.parametrize("statement", [
        "INSERT INTO companion.ftf_info VALUES ('GC3', 'd', 'b')",
        "UPDATE companion.ftf_info SET ftf_by = 'x'",
        "DELETE FROM companion.ftf_info",
        "DROP TABLE companion.ftf_info",
        "ALTER TABLE companion.ftf_info ADD COLUMN x",
        "ALTER TABLE companion.ftf_info RENAME TO mine",
        "CREATE INDEX companion.ix ON ftf_info (ftf_by)",
        "CREATE TABLE companion.ftf_info (gc_code TEXT)",
    ])
    def test_other_macros_only_read(self, ftf, tmp_path, statement):
        message = _fails(f'opensak.sql("{statement}")', tmp_path)
        assert "companion.ftf_info belongs to ftf.lua; only that macro can change it" in message
        assert len(_companion_sql(ftf, "SELECT * FROM ftf_info")) == 2

    def test_sql_write_follows_the_same_rules(self, ftf, tmp_path):
        message = _fails('opensak.sql_write("DELETE FROM companion.ftf_info")', tmp_path)
        assert "belongs to ftf.lua" in message
        db_access.reset_session()
        host, _ = _run('opensak.sql_write("DELETE FROM companion.ftf_info WHERE gc_code = \'GC2\'")',
                       tmp_path)
        assert host.asked == 1          # sql_write asks, as it may change caches
        assert len(_companion_sql(ftf, "SELECT * FROM ftf_info")) == 1

    def test_opensak_tables_are_read_only(self, ftf, tmp_path):
        _, out = _run('print(#opensak.sql("SELECT * FROM companion.opensak_tables"))', tmp_path)
        assert out == ["1"]
        for statement in ("DELETE FROM companion.opensak_tables",
                          "UPDATE companion.opensak_meta SET value = 'x'",
                          "CREATE TABLE companion.opensak_mine (x)",
                          "DROP TABLE companion.opensak_tables"):
            message = _fails(f'opensak.sql("{statement}")', tmp_path, name="ftf.lua")
            assert "belongs to OpenSAK" in message
        assert set(_registry(ftf)) == {"ftf_info"}

    def test_tables_without_owner_are_read_only(self, ftf, tmp_path):
        _companion_sql(ftf, "CREATE TABLE by_hand (gc_code TEXT, x)")
        _, out = _run('print(#opensak.sql("SELECT * FROM companion.by_hand"))', tmp_path)
        assert out == ["0"]
        message = _fails("opensak.sql(\"INSERT INTO companion.by_hand VALUES ('GC1', 1)\")", tmp_path)
        assert "companion.by_hand has no owner" in message
        # CREATE ... IF NOT EXISTS does nothing and doesn't take it over
        _run('opensak.sql("CREATE TABLE IF NOT EXISTS companion.by_hand (x)")', tmp_path)
        assert "by_hand" not in _registry(ftf)

    def test_views_belong_to_their_creator(self, ftf, tmp_path):
        _run('opensak.sql("CREATE VIEW companion.ftf_by_me AS '
             "SELECT * FROM ftf_info WHERE ftf_by = 'me'\")", tmp_path)
        assert _registry(ftf)["ftf_by_me"][2] == "free"
        _, out = _run('print(#opensak.sql("SELECT * FROM companion.ftf_by_me"))', tmp_path, "x.lua")
        assert out == ["1"]
        assert "belongs to ftf.lua" in _fails('opensak.sql("DROP VIEW companion.ftf_by_me")', tmp_path)
        _run('opensak.sql("DROP VIEW companion.ftf_by_me")', tmp_path)
        assert set(_registry(ftf)) == {"ftf_info"}
        assert companion.check(ftf).ok

    def test_triggers_are_refused(self, ftf, tmp_path):
        message = _fails('opensak.sql("CREATE TRIGGER companion.t AFTER INSERT ON ftf_info '
                         'BEGIN DELETE FROM ftf_info; END")', tmp_path, name="ftf.lua")
        assert "triggers can't be created in the companion database" in message

    def test_each_macro_is_its_own_owner_in_run_macro(self, ftf, tmp_path):
        (tmp_path / "called.lua").write_text(
            'opensak.sql("CREATE TABLE companion.called_t (x)")\n'
            'opensak.sql("DELETE FROM companion.ftf_info")\n', encoding="utf-8")
        message = _fails('opensak.run_macro("called.lua")', tmp_path, name="ftf.lua")
        assert "companion.ftf_info belongs to ftf.lua" in message
        assert _registry(ftf)["called_t"][0] == db_access.macro_key(tmp_path / "called.lua")
        # ... and the caller is again the owner of its own afterwards
        _run('opensak.run_macro("noop.lua")\n'
             'opensak.sql("DELETE FROM companion.ftf_info WHERE gc_code = \'GC2\'")',
             _with_file(tmp_path, "noop.lua", "-- nothing"), name="ftf.lua")
        assert len(_companion_sql(ftf, "SELECT * FROM ftf_info")) == 1

    def test_undone_transaction_undoes_the_table_and_its_owner(self, ftf, tmp_path):
        _, out = _run("""
            pcall(opensak.transaction, function()
                opensak.sql("CREATE TABLE companion.temp_t (gc_code TEXT)")
                opensak.sql("DELETE FROM companion.ftf_info")
                error("undo")
            end)
            opensak.sql("CREATE TABLE companion.kept (x)")
            print(#opensak.sql("SELECT * FROM companion.ftf_info"))
        """, tmp_path)
        assert out == ["2"]
        assert set(_registry(ftf)) == {"ftf_info", "kept"}

    def test_transaction_spans_both_files(self, ftf, tmp_path):
        _run("""
            opensak.transaction(function()
                opensak.sql("CREATE TABLE companion.counts (gc_code TEXT PRIMARY KEY, n)")
                opensak.sql("INSERT INTO companion.counts SELECT gc_code, 1 FROM caches")
                opensak.update("GC3", { name = "changed" })
            end)
        """, tmp_path)
        assert len(_companion_sql(ftf, "SELECT * FROM counts")) == 3
        assert _registry(ftf)["counts"][2] == "cache"


def _with_file(folder: Path, name: str, text: str) -> Path:
    (folder / name).write_text(text, encoding="utf-8")
    return folder


# ── Housekeeping through the macro connection ────────────────────────────────

@pytest.mark.skipif(not flags.lua_macro_write, reason="needs the lua-macro-write flag")
class TestDelete:
    def test_sql_write_delete_deletes_the_rows(self, ftf, tmp_path):
        _run("opensak.sql_write(\"DELETE FROM caches WHERE gc_code = 'GC1'\")", tmp_path, "x.lua")
        assert _companion_sql(ftf, "SELECT gc_code FROM ftf_info") == [("GC2",)]

    def test_undone_transaction_keeps_them(self, ftf, tmp_path):
        _run("""
            pcall(opensak.transaction, function()
                opensak.sql_write("DELETE FROM caches WHERE gc_code = 'GC1'")
                opensak.delete("GC2")
                error("undo")
            end)
        """, tmp_path, "x.lua")
        assert len(_companion_sql(ftf, "SELECT * FROM ftf_info")) == 2

    def test_committed_transaction(self, ftf, tmp_path):
        _run("""
            opensak.transaction(function()
                opensak.sql_write("DELETE FROM caches WHERE gc_code = 'GC1'")
                opensak.delete("GC2")
            end)
        """, tmp_path, "x.lua")
        assert _companion_sql(ftf, "SELECT * FROM ftf_info") == []


# ── companion_table{} and companion_tables() ─────────────────────────────────

class TestRegistryFunctions:
    def test_companion_tables(self, ftf, tmp_path):
        _companion_sql(ftf, "CREATE TABLE by_hand (x)")
        _, out = _run("""
            for _, t in ipairs(opensak.companion_tables()) do
                print(t.name, t.type, t.kind, t.code_column, t.rows, t.mine, t.owner_name)
            end
        """, tmp_path)
        assert out == ["by_hand\ttable\tfree\tnil\t0\tfalse\tnil",
                       "ftf_info\ttable\tcache\tgc_code\t2\ttrue\tftf"]

    def test_empty_without_companion(self, active, tmp_path):
        _, out = _run("print(#opensak.companion_tables())", tmp_path)
        assert out == ["0"]
        assert not companion.exists(active)

    def test_linked_false_keeps_the_rows(self, ftf, tmp_path):
        _, out = _run("""
            local t = opensak.companion_table{ name = "companion.ftf_info", linked = false,
                                               description = "FTF data" }
            print(t.kind, t.description)
        """, tmp_path)
        assert out == ["free\tFTF data"]
        assert _registry(ftf)["ftf_info"][2:] == ("free", None, "FTF data")
        with session_for(ftf) as s:
            s.query(Cache).filter_by(gc_code="GC1").delete()
        conn = sqlite3.connect(str(companion.companion_path(ftf)))
        try:
            assert companion.cache_linked_tables(conn) == []
        finally:
            conn.close()

    def test_code_column(self, ftf, tmp_path):
        _run("""
            opensak.sql("CREATE TABLE companion.logs (code TEXT, text TEXT)")
            opensak.companion_table{ name = "logs", code_column = "code" }
        """, tmp_path)
        assert _registry(ftf)["logs"][2:4] == ("cache", "code")

    @pytest.mark.parametrize("spec, message", [
        ('{ name = "missing" }', "companion.missing doesn't exist"),
        ('{ name = "ftf_info", code_column = "nope" }', "has no column 'nope'"),
        ('{ name = "ftf_info", linked = "yes" }', "`linked` must be true or false"),
        ('{ name = "ftf_info", colour = 1 }', "unknown key(s) colour"),
        ('{ }', "`name` must be"),
        ('{ name = "opensak_tables" }', "belongs to OpenSAK"),
    ])
    def test_errors(self, ftf, tmp_path, spec, message):
        assert message in _fails(f"opensak.companion_table{spec}", tmp_path, name="ftf.lua")

    def test_only_the_owner(self, ftf, tmp_path):
        assert "belongs to ftf.lua" in _fails(
            'opensak.companion_table{ name = "ftf_info", linked = false }', tmp_path)

    def test_views_cannot_be_linked(self, ftf, tmp_path):
        _run('opensak.sql("CREATE VIEW companion.v AS SELECT * FROM ftf_info")', tmp_path)
        assert "only tables can be cache-linked" in _fails(
            'opensak.companion_table{ name = "v", linked = true }', tmp_path, name="ftf.lua")


# ── opensak.attach() ──────────────────────────────────────────────────────────

class TestAttach:
    @pytest.fixture
    def other(self, ftf, tmp_path):
        """A second database "Found" whose companion holds ftf_info with
        GC9; "Active" (with its own companion) stays active."""
        manager = get_db_manager()
        found = manager.new_database("Found")
        _add(found.path, "GC9")
        path = Path(found.path)
        companion.create(path)
        _companion_sql(path, "CREATE TABLE ftf_info (gc_code TEXT, ftf_by TEXT)")
        _companion_sql(path, "INSERT INTO ftf_info VALUES ('GC9', 'them')")
        return path

    def test_attaches_and_detaches_the_companion_too(self, other, tmp_path):
        _, out = _run("""
            opensak.attach("Found", "found")
            for _, a in ipairs(opensak.attached()) do print(a.schema, a.companion) end
            print(opensak.sql("SELECT ftf_by FROM found_companion.ftf_info")[1].ftf_by)
            print(#opensak.sql("SELECT * FROM companion.ftf_info"))
            opensak.detach("found")
            print(#opensak.attached())
        """, tmp_path)
        assert out == [
            "opensak.attach: attached 'Found' read-only as found",
            "opensak.attach: attached its companion database read-only as found_companion",
            "found\tfalse", "found_companion\ttrue", "them", "2", "0",
        ]

    def test_attached_companion_is_read_only(self, other, tmp_path):
        message = _fails('opensak.attach("Found", "found")\n'
                         'opensak.sql("DELETE FROM found_companion.ftf_info")', tmp_path)
        assert "read-only" in message
        assert _companion_sql(other, "SELECT * FROM ftf_info") == [("GC9", "them")]

    def test_newer_companion_is_not_attached(self, other, tmp_path):
        _companion_sql(other, f"PRAGMA user_version = {companion.SCHEMA_VERSION + 1}")
        _, out = _run('opensak.attach("Found", "found")\nprint(#opensak.attached())', tmp_path)
        assert out == ["opensak.attach: attached 'Found' read-only as found", "1"]

    def test_without_the_flag(self, other, tmp_path, monkeypatch):
        monkeypatch.setattr(flags, "companion_db", False)
        _, out = _run('opensak.attach("Found", "found")\nprint(#opensak.attached())', tmp_path)
        assert out[-1] == "1"


# ── The example macros ────────────────────────────────────────────────────────

EXAMPLES = Path(__file__).resolve().parents[2] / "macros" / "examples"


def test_example_macros_dnf_stats_and_dnf_alert(active, tmp_path):
    from datetime import datetime

    with session_for(active) as s:
        ids = {c.gc_code: c.id for c in s.query(Cache)}
        for code, log_type, day in (
            ("GC1", "Found it", 1), ("GC1", "Didn't find it", 2), ("GC1", "Didn't find it", 3),
            ("GC2", "Didn't find it", 1), ("GC2", "Found it", 2),
            ("GC3", "Found it", 1),
        ):
            s.add(Log(cache_id=ids[code], log_type=log_type, log_date=datetime(2024, 5, day)))
    for name in ("dnf_stats.lua", "dnf_alert.lua"):
        (tmp_path / name).write_text((EXAMPLES / name).read_text(encoding="utf-8"), encoding="utf-8")

    _, out = _run((tmp_path / "dnf_stats.lua").read_text(encoding="utf-8"), tmp_path, "dnf_stats.lua")
    assert out[-2:] == ["2 caches with 3 DNF logs; 1 with DNFs since their last find.",
                        "Stored in companion.dnf_stats of database Active."]
    assert _registry(active)["dnf_stats"][1:3] == ("DNF statistics", "cache")

    host, out = _run((tmp_path / "dnf_alert.lua").read_text(encoding="utf-8"), tmp_path, "dnf_alert.lua")
    assert out[0] == "Using companion.dnf_stats (2 caches) by DNF statistics."
    assert out[1].startswith("GC1        2 DNFs, last 2024-05-03, last found 2024-05-01")
    assert out[-1] == "1 cache filtered."
    assert len(host.filtered) == 1
    # the reader can't change the writer's table
    assert "belongs to dnf_stats.lua" in _fails(
        'opensak.sql("UPDATE companion.dnf_stats SET dnfs = 0")', tmp_path, name="dnf_alert.lua")


def test_active_engine_path_is_the_active_database(active):
    # The tests above rely on the runtime finding "Active" through get_engine().
    assert Path(get_engine().url.database or "").resolve() == active.resolve()

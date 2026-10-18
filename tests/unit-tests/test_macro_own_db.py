"""tests/unit-tests/test_macro_own_db.py — opensak.macro_db(): the macro's
own .db3 next to the macro file, attached as `macro` with full access
there, used together with the active database, and no way past it."""

import os
import sqlite3

import pytest

from opensak.db.database import get_engine, get_session
from opensak.db.models import Cache
from opensak.macro import FolderApproval, FolderPermission, MacroError, MacroRuntime, WriteApproval


class Host:
    """A MacroHost with what the SQL functions use."""

    def __init__(self, answer=WriteApproval.SESSION, folder_answer=FolderApproval.DENY):
        self.answer = answer
        self.asked = 0
        self.folder_answer = folder_answer
        self.folders_asked: list = []

    def database_name(self):
        return "TestDB"

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def approve_macro_write(self, macro):
        self.asked += 1
        return self.answer

    def caches_changed(self, codes, added):
        pass

    def approve_folder(self, target, folder, write):
        self.folders_asked.append((target, folder, write))
        return self.folder_answer

    def end_macro(self):
        pass


def _run(source, folder, name="my_macro.lua", host=None, permitted=True):
    """Run *source* as the macro *name* in *folder* — a folder macros may
    write to, unless *permitted* is False."""
    out: list[str] = []
    host = host or Host()
    permissions = [FolderPermission(str(folder), read=True, write=True)] if permitted else []
    MacroRuntime(host, output=out.append, folder_permissions=permissions).run(
        source, chunk_name=name, base_dir=folder)
    return host, out


@pytest.fixture(scope="module", autouse=True)
def seed(tmp_db):
    with get_session() as s:
        for code, found in (("GCOWN1", True), ("GCOWN2", False), ("GCOWN3", True)):
            s.add(Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0, found=found))


def test_full_access_persists_between_runs_and_joins_the_active_db(tmp_path):
    host, out = _run("""
        print(opensak.macro_db())
        opensak.sql("CREATE TABLE macro.seen (code TEXT PRIMARY KEY, note TEXT)")
        opensak.sql("CREATE INDEX macro.seen_note ON seen (note)")
        opensak.sql("CREATE VIEW macro.v AS SELECT COUNT(*) AS n FROM seen")
        opensak.sql("INSERT INTO macro.seen SELECT gc_code, 'x' FROM caches "
            .. "WHERE gc_code LIKE 'GCOWN%' AND found = 1")
        opensak.sql("UPDATE macro.seen SET note = 'y' WHERE code = 'GCOWN3'")
        opensak.sql("PRAGMA macro.user_version = 7")
        print(opensak.sql("SELECT n FROM macro.v")[1].n)
        local t = {}
        for _, name in ipairs(opensak.tables()) do t[name] = true end
        print(t["macro.seen"], t["macro.v"], t.caches)
        print(#opensak.columns("macro.seen"))
    """, tmp_path)
    db_file = tmp_path / "my_macro.db3"
    created = f"opensak.macro_db: created {db_file.resolve()}"
    assert out == [created, str(db_file.resolve()), "2", "true\ttrue\ttrue", "2"]
    assert host.asked == 0

    _, out = _run("""
        opensak.macro_db()
        print(opensak.sql("PRAGMA macro.user_version")[1].user_version)
        opensak.sql("DELETE FROM macro.seen WHERE code = 'GCOWN1'")
        for _, r in ipairs(opensak.sql("SELECT c.gc_code, s.note FROM caches c "
            .. "JOIN macro.seen s ON s.code = c.gc_code")) do print(r.gc_code, r.note) end
        opensak.sql("DROP VIEW macro.v")
    """, tmp_path)
    assert out == ["7", "GCOWN3\ty"]       # no "created" line: the file exists

    conn = sqlite3.connect(db_file)
    assert conn.execute("SELECT code FROM seen").fetchall() == [("GCOWN3",)]
    conn.close()


def test_sql_write_uses_the_macro_db_and_transactions_span_both(tmp_path):
    _run("""
        opensak.macro_db()
        opensak.sql("CREATE TABLE macro.names (code TEXT, name TEXT)")
        opensak.sql("INSERT INTO macro.names VALUES ('GCOWN2', 'from macro db')")
        opensak.sql_write("UPDATE caches SET name = (SELECT name FROM macro.names "
            .. "WHERE code = gc_code) WHERE gc_code IN (SELECT code FROM macro.names)")
        pcall(opensak.transaction, function()
            opensak.sql("DELETE FROM macro.names")
            opensak.update("GCOWN2", { name = "undone" })
            error("undo")
        end)
        print(#opensak.sql("SELECT * FROM macro.names"))
    """, tmp_path, name="writer.lua")
    with get_session() as s:
        assert s.query(Cache.name).filter_by(gc_code="GCOWN2").scalar() == "from macro db"
    conn = sqlite3.connect(tmp_path / "writer.db3")
    assert conn.execute("SELECT COUNT(*) FROM names").fetchone() == (1,)
    conn.close()


def test_without_macro_db_the_schema_does_not_exist(tmp_path):
    with pytest.raises(MacroError, match="unknown database macro"):
        _run('opensak.sql("CREATE TABLE macro.t (x)")', tmp_path, name="none.lua")
    assert not (tmp_path / "none.db3").exists()


@pytest.mark.parametrize("query", [
    "ATTACH DATABASE 'other.db3' AS other",
    "DETACH DATABASE macro",
    "CREATE TABLE plain (x)",
    "DELETE FROM caches",
    "DROP TABLE caches",
    "PRAGMA macro.foreign_keys = OFF",
    "PRAGMA foreign_keys = OFF",
    "CREATE TRIGGER macro.t AFTER INSERT ON macro.log BEGIN DELETE FROM main.caches; END",
    "CREATE TABLE macro.opensak_macro_x (x)",
])
def test_no_way_past_the_macro_db(tmp_path, query):
    with pytest.raises(MacroError):
        _run(f"""
            opensak.macro_db()
            opensak.sql("CREATE TABLE IF NOT EXISTS macro.log (x)")
            opensak.sql("{query}")
        """, tmp_path, name="guard.lua")
    with get_session() as s:
        assert s.query(Cache).filter(Cache.gc_code.like("GCOWN%")).count() == 3


def test_macro_trigger_cannot_change_the_active_db(tmp_path):
    with pytest.raises(MacroError):
        _run("""
            opensak.macro_db()
            opensak.sql("CREATE TABLE macro.log (x)")
            opensak.sql("CREATE TRIGGER macro.t AFTER INSERT ON log BEGIN "
                .. "UPDATE caches SET name = 'hacked'; END")
            opensak.sql("INSERT INTO macro.log VALUES (1)")
        """, tmp_path, name="trigger.lua")
    with get_session() as s:
        assert s.query(Cache).filter_by(name="hacked").count() == 0


def test_unsaved_macro_and_opensak_database_are_refused(tmp_path):
    with pytest.raises(MacroError, match="save the macro first"):
        MacroRuntime(Host(), output=lambda _: None).run("opensak.macro_db()")
    active = get_engine().url.database
    from pathlib import Path
    folder, stem = Path(active).parent, Path(active).stem
    if Path(active).suffix == ".db3":
        with pytest.raises(MacroError, match="is an OpenSAK database"):
            _run("opensak.macro_db()", folder, name=f"{stem}.lua")


def test_macro_db_is_refused_inside_a_transaction(tmp_path):
    with pytest.raises(MacroError, match="inside opensak.transaction"):
        _run("opensak.transaction(function() opensak.macro_db() end)", tmp_path, name="tx.lua")


# ── folder permissions (#978) and read-only files ────────────────────────────

def test_unlisted_folder_is_asked_and_a_denial_creates_nothing(tmp_path):
    host = Host(folder_answer=FolderApproval.DENY)
    with pytest.raises(MacroError, match="opensak.macro_db: .*denied by the user"):
        _run("opensak.macro_db()", tmp_path, name="ask.lua", host=host, permitted=False)
    assert host.folders_asked == [((tmp_path / "ask.db3").resolve(), tmp_path.resolve(), True)]
    assert not (tmp_path / "ask.db3").exists()


def test_unlisted_folder_approved_once_works(tmp_path):
    host = Host(folder_answer=FolderApproval.ONCE)
    _, out = _run("opensak.macro_db() opensak.macro_db() "
                  "opensak.sql('CREATE TABLE macro.t (x)')",
                  tmp_path, name="once.lua", host=host, permitted=False)
    assert len(host.folders_asked) == 1      # a second call does not ask again
    assert out == [f"opensak.macro_db: created {(tmp_path / 'once.db3').resolve()}"]


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root ignores file permission bits")
def test_read_only_macro_db_can_be_read_but_not_written(tmp_path):
    import stat
    db_file = tmp_path / "ro.db3"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    os.chmod(db_file, stat.S_IREAD)
    try:
        _, out = _run("opensak.macro_db() print(#opensak.sql('SELECT * FROM macro.t'))",
                      tmp_path, name="ro.lua")
        assert out == ["1"]
        for write in ("opensak.sql('INSERT INTO macro.t VALUES (2)')",
                      "opensak.sql_write('INSERT INTO macro.t VALUES (2)')"):
            with pytest.raises(MacroError, match="macro database .*ro.db3 is read-only"):
                _run(f"opensak.macro_db() {write}", tmp_path, name="ro.lua")
    finally:
        os.chmod(db_file, stat.S_IREAD | stat.S_IWRITE)


def test_database_exemption_is_only_for_the_macro_db(tmp_path):
    from opensak.macro.permissions import FolderAccessDenied, check_access
    perms = [FolderPermission(str(tmp_path), read=True, write=True)]
    target = tmp_path / "x.db3"
    with pytest.raises(FolderAccessDenied, match="database file"):
        check_access(target, write=True, permissions=perms)
    assert check_access(target, write=True, permissions=perms, allow_database=True) == target.resolve()


# ── opensak.run_macro(): the called macro's own file (#1039) ─────────────────

def test_called_macro_gets_its_own_db_and_the_callers_comes_back(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "helper.lua").write_text("""
        local t = {}
        for _, name in ipairs(opensak.tables()) do t[name] = true end
        print("before", t["macro.mine"])
        print(opensak.macro_db())
        opensak.sql("CREATE TABLE IF NOT EXISTS macro.theirs (x)")
        t = {}
        for _, name in ipairs(opensak.tables()) do t[name] = true end
        print("after", t["macro.mine"], t["macro.theirs"])
        if ... then error("helper failed") end
    """, encoding="utf-8")
    _, out = _run("""
        opensak.macro_db()
        opensak.sql("CREATE TABLE macro.mine (x)")
        opensak.run_macro("lib/helper.lua")
        print(#opensak.sql("SELECT * FROM macro.mine"))
        print(pcall(opensak.run_macro, "lib/helper.lua", true) == false)
        opensak.sql("INSERT INTO macro.mine VALUES (1)")
        print(#opensak.sql("SELECT * FROM macro.mine"))
    """, tmp_path, name="caller.lua")
    helper_db = (lib / "helper.db3").resolve()
    assert out == [
        f"opensak.macro_db: created {(tmp_path / 'caller.db3').resolve()}",
        "before\ttrue",                     # until it calls macro_db()
        f"opensak.macro_db: created {helper_db}", str(helper_db),
        "after\tnil\ttrue",
        "0",
        "before\ttrue", str(helper_db), "after\tnil\ttrue",
        "true",
        "1",
    ]
    assert not (lib / "caller.db3").exists()
    conn = sqlite3.connect(helper_db)
    assert conn.execute("SELECT name FROM sqlite_master").fetchall() == [("theirs",)]
    conn.close()


def test_macro_db_is_recorded_for_backups(tmp_path):
    from opensak.backup.backupset import known_macro_databases
    _run("opensak.macro_db() opensak.macro_db()", tmp_path, name="backed_up.lua")
    _run("opensak.macro_db()", tmp_path, name="backed_up.lua")
    recorded = (tmp_path / "backed_up.db3").resolve()
    assert [p for p in known_macro_databases() if p == recorded] == [recorded]

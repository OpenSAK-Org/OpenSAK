"""tests/unit-tests/test_macro_own_db.py — opensak.macro_db(): the macro's
own .db3 next to the macro file, attached as `macro` with full access
there, used together with the active database, and no way past it."""

import sqlite3

import pytest

from opensak.db.database import get_engine, get_session
from opensak.db.models import Cache
from opensak.macro import MacroError, MacroRuntime, WriteApproval


class Host:
    """A MacroHost with what the SQL functions use."""

    def __init__(self, answer=WriteApproval.SESSION):
        self.answer = answer
        self.asked = 0

    def database_name(self):
        return "TestDB"

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def approve_database_write(self, name, path):
        self.asked += 1
        return self.answer

    def caches_changed(self, codes, added):
        pass

    def end_macro(self):
        pass


def _run(source, folder, name="my_macro.lua", host=None):
    out: list[str] = []
    host = host or Host()
    MacroRuntime(host, output=out.append).run(source, chunk_name=name, base_dir=folder)
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
    assert out == [str(db_file.resolve()), "2", "true\ttrue\ttrue", "2"]
    assert host.asked == 0

    _, out = _run("""
        opensak.macro_db()
        print(opensak.sql("PRAGMA macro.user_version")[1].user_version)
        opensak.sql("DELETE FROM macro.seen WHERE code = 'GCOWN1'")
        for _, r in ipairs(opensak.sql("SELECT c.gc_code, s.note FROM caches c "
            .. "JOIN macro.seen s ON s.code = c.gc_code")) do print(r.gc_code, r.note) end
        opensak.sql("DROP VIEW macro.v")
    """, tmp_path)
    assert out == ["7", "GCOWN3\ty"]

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

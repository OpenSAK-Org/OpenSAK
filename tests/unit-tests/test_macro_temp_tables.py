"""tests/unit-tests/test_macro_temp_tables.py — TEMP tables in macro SQL:
created and changed with opensak.sql(), usable by opensak.sql_write() and
inside opensak.transaction(), gone after the run, never in the file, and no
way around the read-only rules for the database's own tables."""

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

    def approve_macro_write(self, macro):
        self.asked += 1
        return self.answer

    def caches_changed(self, codes, added):
        pass

    def end_macro(self):
        pass


def _run(source, host=None):
    out: list[str] = []
    host = host or Host()
    MacroRuntime(host, output=out.append).run(source)
    return host, out


@pytest.fixture(scope="module", autouse=True)
def seed(tmp_db):
    with get_session() as s:
        for code in ("GCTMP1", "GCTMP2", "GCTMP3"):
            s.add(Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0))


def _file_objects():
    conn = sqlite3.connect(get_engine().url.database)
    try:
        return {n for (n,) in conn.execute("SELECT name FROM sqlite_master")}
    finally:
        conn.close()


def test_temp_table_full_sql_without_write_permission():
    host, out = _run("""
        opensak.sql("CREATE TEMP TABLE todo (code TEXT PRIMARY KEY, note TEXT)")
        opensak.sql("CREATE INDEX todo_note ON todo (note)")
        opensak.sql("INSERT INTO todo SELECT gc_code, 'x' FROM caches WHERE gc_code LIKE 'GCTMP%'")
        opensak.sql("UPDATE todo SET note = 'y' WHERE code = 'GCTMP2'")
        opensak.sql("DELETE FROM todo WHERE code = 'GCTMP3'")
        for _, r in ipairs(opensak.sql(
            "SELECT t.code, t.note, c.name FROM todo t JOIN caches c ON c.gc_code = t.code ORDER BY t.code")) do
            print(r.code, r.note, r.name)
        end
        print(opensak.sql("CREATE TEMP VIEW v AS SELECT COUNT(*) AS n FROM todo") and
              opensak.sql("SELECT n FROM v")[1].n)
        local t = {}
        for _, name in ipairs(opensak.tables()) do t[name] = true end
        print(t.todo, t.v, t.caches, t.opensak_macro_touched)
        print(#opensak.columns("todo"))
        opensak.sql("DROP TABLE todo")
    """)
    assert out == ["GCTMP1\tx\tGCTMP1", "GCTMP2\ty\tGCTMP2", "2",
                   "true\ttrue\ttrue\tnil", "2"]
    assert host.asked == 0
    assert not {"todo", "todo_note", "v"} & _file_objects()


def test_temp_tables_last_for_one_run():
    _run('opensak.sql("CREATE TEMP TABLE once (x)")')
    with pytest.raises(MacroError, match="no such table"):
        _run('opensak.sql("SELECT * FROM once")')


def test_sql_write_and_transaction_see_temp_tables():
    _, out = _run("""
        opensak.sql("CREATE TEMP TABLE names (code TEXT, name TEXT)")
        opensak.sql("INSERT INTO names VALUES ('GCTMP1', 'renamed')")
        print(opensak.sql_write(
            "UPDATE caches SET name = (SELECT name FROM names WHERE code = gc_code) "
            .. "WHERE gc_code IN (SELECT code FROM names)"))
        opensak.transaction(function()
            opensak.sql("INSERT INTO names VALUES ('GCTMP2', 'two')")
            print(#opensak.sql("SELECT * FROM names"))
        end)
        pcall(opensak.transaction, function()
            opensak.sql("DELETE FROM names")
            error("undo")
        end)
        print(#opensak.sql("SELECT * FROM names"))
    """)
    assert out == ["1", "2", "2"]
    with get_session() as s:
        assert s.query(Cache.name).filter_by(gc_code="GCTMP1").scalar() == "renamed"


@pytest.mark.parametrize("query", [
    "CREATE TEMP TABLE caches (x)",                 # would shadow main.caches
    "CREATE TEMP VIEW logs AS SELECT 1",
    "CREATE TEMP TABLE Caches (x)",
    "CREATE TABLE plain (x)",                       # main, not temp
    "CREATE TEMP TRIGGER t AFTER UPDATE ON caches BEGIN SELECT 1; END",
    "CREATE TEMP TRIGGER opensak_macro_t AFTER UPDATE ON caches BEGIN SELECT 1; END",
    "SELECT * FROM opensak_macro_touched",
    "DELETE FROM temp.opensak_macro_touched",
    "DROP TABLE opensak_macro_touched",
    "CREATE TEMP TABLE opensak_macro_x (x)",
    "DELETE FROM db_settings",
    "CREATE TEMP TABLE t AS SELECT 1; DELETE FROM caches",
])
def test_temp_rules_are_enforced(query):
    with pytest.raises(MacroError):
        _run(f'opensak.sql("{query}")')
    with pytest.raises(MacroError):
        _run(f'opensak.sql_write("{query}")')
    with get_session() as s:
        assert s.query(Cache).filter(Cache.gc_code.like("GCTMP%")).count() == 3


def test_other_database_gets_its_own_temp_tables(tmp_path):
    from opensak.macro.sql import ReadOnlyDatabase, SqlError

    path = tmp_path / "other.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE caches (gc_code TEXT)")
    conn.commit()
    conn.close()
    db = ReadOnlyDatabase(path)
    try:
        db.query("CREATE TEMP TABLE t (x)")
        db.query("INSERT INTO t VALUES (1)")
        assert db.query("SELECT x FROM t") == [{"x": 1}]
        with pytest.raises(SqlError, match="read-only"):
            db.query("INSERT INTO caches VALUES ('GC1')")
    finally:
        db.close()
    conn = sqlite3.connect(path)
    assert [n for (n,) in conn.execute("SELECT name FROM sqlite_master")] == ["caches"]
    conn.close()

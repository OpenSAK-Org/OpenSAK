"""tests/unit-tests/test_macro_attach_1074.py — opensak.attach(), detach()
and attached() (#1074): other OpenSAK databases attached read-only to the
active database's SQL connection, joined in one statement with it and the
macro database, and no way to write to them."""

import sqlite3

import pytest

from opensak.db.database import get_session, session_for
from opensak.db.manager import get_db_manager
from opensak.db.models import Cache
from opensak.export.file_export import active_database_name
from opensak.macro import FolderPermission, MacroError, MacroRuntime, WriteApproval
from opensak.macro.sql import WritableDatabase, default_schema_name


class Host:
    """A MacroHost on the real DatabaseManager and the active database."""

    def __init__(self):
        self.changed: list[list[str]] = []

    def database_name(self):
        return active_database_name()

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def approve_macro_write(self, macro, change=None, editing=False):
        return WriteApproval.SESSION

    def caches_changed(self, codes, added):
        self.changed.append(list(codes))

    def caches_removed(self):
        pass

    def switch_database(self, name):
        manager = get_db_manager()
        manager.switch_to(next(d for d in manager.databases if d.name == name))

    def end_macro(self):
        pass


def _add(path, *codes, found=False):
    with session_for(path) as s:
        for code in codes:
            s.add(Cache(gc_code=code, name=f"{code} name", cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0, found=found))


def _codes(path) -> set[str]:
    with session_for(path) as s:
        return {c.gc_code for c in s.query(Cache)}


@pytest.fixture
def dbs():
    """Active database "Active" with GCA1..4, "Found 2020" with GCA1, GCA2
    and GCF9, "Placed" with GCA3."""
    manager = get_db_manager()
    active = manager.new_database("Active")
    manager.switch_to(active)
    found = manager.new_database("Found 2020")
    placed = manager.new_database("Placed")
    _add(active.path, "GCA1", "GCA2", "GCA3", "GCA4")
    _add(found.path, "GCA1", "GCA2", "GCF9", found=True)
    _add(placed.path, "GCA3")
    return active, found, placed


def _run(source, host=None, folder=None, name="macro.lua"):
    out: list[str] = []
    host = host or Host()
    permissions = [FolderPermission(str(folder), read=True, write=True)] if folder else None
    MacroRuntime(host, output=out.append, folder_permissions=permissions).run(
        source, chunk_name=name, base_dir=folder)
    return host, out


# ── Joining ──────────────────────────────────────────────────────────────────

def test_join_active_two_attached_and_the_macro_db(dbs, tmp_path):
    _, out = _run("""
        print(opensak.attach("Found 2020"), opensak.attach("Placed", "placed"))
        opensak.macro_db()
        opensak.sql("CREATE TABLE IF NOT EXISTS macro.notes (code TEXT, note TEXT)")
        opensak.sql("INSERT INTO macro.notes VALUES ('GCA3', 'mine')")
        for _, r in ipairs(opensak.sql([[
            SELECT c.gc_code, f.found AS f, p.gc_code AS p, n.note FROM caches c
            LEFT JOIN found_2020.caches f ON f.gc_code = c.gc_code
            LEFT JOIN placed.caches p ON p.gc_code = c.gc_code
            LEFT JOIN macro.notes n ON n.code = c.gc_code
            WHERE f.gc_code IS NOT NULL OR p.gc_code IS NOT NULL
            ORDER BY c.gc_code]])) do
            print(r.gc_code, r.f, r.p, r.note)
        end
    """, folder=tmp_path)
    assert out == [
        "opensak.attach: attached 'Found 2020' read-only as found_2020",
        "opensak.attach: attached 'Placed' read-only as placed",
        "found_2020\tplaced",
        f"opensak.macro_db: created {(tmp_path / 'macro.db3').resolve()}",
        "GCA1\t1\tnil\tnil",
        "GCA2\t1\tnil\tnil",
        "GCA3\tnil\tGCA3\tmine",
    ]


def test_sql_write_updates_the_active_db_from_attached_ones(dbs):
    active, found, _ = dbs
    host, out = _run("""
        opensak.attach("Found 2020", "found")
        print(opensak.sql_write("UPDATE caches SET found = 1, user_flag = 1 "
            .. "WHERE gc_code IN (SELECT gc_code FROM found.caches)"))
        opensak.transaction(function()
            opensak.sql_write("UPDATE caches SET name = 'placed' "
                .. "WHERE gc_code IN (SELECT gc_code FROM found.caches WHERE gc_code = 'GCA2')")
        end)
    """)
    assert out[-1] == "2"
    assert host.changed[0] == ["GCA1", "GCA2"]
    with session_for(active.path) as s:
        rows = {c.gc_code: (c.found, c.user_flag, c.name) for c in s.query(Cache)}
    assert rows["GCA1"] == (True, True, "GCA1 name")
    assert rows["GCA2"] == (True, True, "placed")
    assert rows["GCA3"][:2] == (False, False)
    assert _codes(found.path) == {"GCA1", "GCA2", "GCF9"}


def test_tables_columns_and_attached(dbs):
    _, out = _run("""
        opensak.attach("Found 2020", "found")
        local t = {}
        for _, name in ipairs(opensak.tables()) do t[name] = true end
        print(t.caches, t["found.caches"], t["found.logs"], t["found.sqlite_sequence"])
        print(#opensak.columns("found.caches") == #opensak.columns("caches"))
        print(opensak.sql("PRAGMA found.user_version")[1].user_version > 0)
        local a = opensak.attached()
        print(#a, a[1].schema, a[1].name, a[1].opensak, a[1].path:find("Found") ~= nil)
    """)
    assert out[1:] == ["true\ttrue\ttrue\tnil", "true", "true", "1\tfound\tFound 2020\ttrue\ttrue"]


def test_default_schema_names():
    assert default_schema_name("Found 2020") == "found_2020"
    assert default_schema_name("Placed") == "placed"
    assert default_schema_name("2020 – Finds!") == "db_2020_finds"
    assert default_schema_name("Main") == "db_main"
    assert default_schema_name("OpenSAK backup") == "db_opensak_backup"
    assert default_schema_name("äöü") == "db"


# ── Read-only ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("func, query", [
    ("sql", "INSERT INTO found.caches (gc_code, name, cache_type, latitude, longitude) "
            "VALUES ('GCX', 'x', 'Traditional Cache', 1, 1)"),
    ("sql", "UPDATE found.caches SET name = 'x'"),
    ("sql", "DELETE FROM found.caches"),
    ("sql", "CREATE TABLE found.t (x)"),
    ("sql", "DROP TABLE found.logs"),
    ("sql", "PRAGMA found.user_version = 1"),
    ("sql_write", "INSERT INTO found.caches (gc_code, name, cache_type, latitude, longitude) "
                  "VALUES ('GCX', 'x', 'Traditional Cache', 1, 1)"),
    ("sql_write", "UPDATE found.caches SET name = 'x'"),
    ("sql_write", "DELETE FROM found.caches"),
    ("sql_write", "INSERT INTO found.user_notes (cache_id) SELECT id FROM found.caches"),
    ("sql_write", "CREATE TABLE found.t (x)"),
])
def test_no_write_reaches_an_attached_database(dbs, func, query):
    found = dbs[1]
    with pytest.raises(MacroError, match="not authorized"):
        _run(f'opensak.attach("Found 2020", "found") opensak.{func}("{query}")')
    assert _codes(found.path) == {"GCA1", "GCA2", "GCF9"}
    with session_for(found.path) as s:
        assert {c.name for c in s.query(Cache)} == {"GCA1 name", "GCA2 name", "GCF9 name"}


def test_mode_ro_refuses_writes_without_the_authorizer(dbs):
    """The second line of defence: the file is attached read-only."""
    active, found, _ = dbs
    db = WritableDatabase(active.path)
    try:
        db.attach_read_only(found.path, "found")
        conn = db._connection()
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE found.caches SET name = 'x'")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE TABLE found.t (x)")
    finally:
        db.close()
    assert _codes(found.path) == {"GCA1", "GCA2", "GCF9"}


@pytest.mark.parametrize("query", [
    "ATTACH DATABASE 'other.db3' AS other",
    "DETACH DATABASE found",
])
@pytest.mark.parametrize("func", ["sql", "sql_write"])
def test_raw_attach_and_detach_stay_refused(dbs, func, query):
    with pytest.raises(MacroError, match="not authorized"):
        _run(f'opensak.attach("Found 2020", "found") opensak.{func}("{query}")')


# ── Refusals ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("call, msg", [
    ('opensak.attach("Nope")', "no database named 'Nope'"),
    ('opensak.attach(1)', "expects a database name"),
    ('opensak.attach("Placed", 5)', "schema name must be a string"),
    ('opensak.attach("Active")', "'Active' is the active database"),
    *[(f'opensak.attach("Placed", "{s}")', "is reserved")
      for s in ("main", "TEMP", "macro", "companion", "opensak_x")],
    *[(f'opensak.attach("Placed", "{s}")', "must be a name of letters")
      for s in ("1x", "a-b", "a b", "", "x" * 65)],
    ('opensak.attach("Found 2020", "x") opensak.attach("Placed", "X")', "'X' is in use already"),
    ('opensak.attach("Found 2020", "x") opensak.attach("Found 2020", "y")', "attached already, as 'x'"),
    ('opensak.detach("found")', "no database is attached as 'found'"),
    ('opensak.detach("main")', "no database is attached as 'main'"),
])
def test_refusals(dbs, call, msg):
    with pytest.raises(MacroError, match=msg):
        _run(call)


def test_attaching_the_same_database_and_schema_again_does_nothing(dbs):
    _, out = _run('print(opensak.attach("Placed"), opensak.attach("Placed", "placed"), #opensak.attached())')
    assert out == ["opensak.attach: attached 'Placed' read-only as placed", "placed\tplaced\t1"]


def test_missing_and_non_sqlite_files_are_refused(dbs):
    _, found, placed = dbs
    placed.path.unlink()
    with pytest.raises(MacroError, match="file of database 'Placed' is missing"):
        _run('opensak.attach("Placed")')
    found.path.write_bytes(b"not a database, just text" * 10)
    with pytest.raises(MacroError, match="is not an SQLite database"):
        _run('opensak.attach("Found 2020")')


def test_the_macro_db_file_is_refused(dbs, tmp_path):
    active, found, _ = dbs
    db = WritableDatabase(active.path)
    try:
        db.attach_macro_db(found.path)
        with pytest.raises(Exception, match="is the macro database"):
            db.attach_read_only(found.path, "found")
    finally:
        db.close()


@pytest.mark.parametrize("call", ['opensak.attach("Placed")', 'opensak.detach("found")'])
def test_attach_and_detach_inside_a_transaction_are_refused(dbs, call):
    with pytest.raises(MacroError, match="cannot be used inside opensak.transaction"):
        _run(f'opensak.attach("Found 2020", "found") opensak.transaction(function() {call} end)')


def test_the_attach_limit(dbs, tmp_path):
    manager = get_db_manager()
    for i in range(10):
        manager.new_database(f"Extra {i}")
    source = "for i = 0, 8 do opensak.attach('Extra ' .. i) end "
    # 9 + the macro database = SQLite's 10
    with pytest.raises(MacroError, match="at most 10 databases can be attached at once"):
        _run(source + "opensak.macro_db() opensak.attach('Extra 9')", folder=tmp_path)
    _, out = _run(source + "opensak.attach('Extra 9') print(#opensak.attached())")
    assert out[-1] == "10"


def test_the_flag(dbs, monkeypatch):
    from opensak.utils import flags
    monkeypatch.setattr(flags, "lua_macro_write", False)
    _, out = _run("print(opensak.attach, opensak.detach, opensak.attached)")
    assert out == ["nil\tnil\tnil"]


# ── Lifetime ─────────────────────────────────────────────────────────────────

def test_detach_and_the_end_of_the_run(dbs):
    _, out = _run("""
        opensak.attach("Found 2020", "found")
        opensak.attach("Placed")
        opensak.detach("found")
        print(#opensak.attached(), opensak.attached()[1].schema)
        print(pcall(opensak.sql, "SELECT * FROM found.caches"))
    """)
    assert out[-2] == "1\tplaced"
    assert out[-1].startswith("false") and "no such table: found.caches" in out[-1]
    # Everything is detached when a run ends, also after an error.
    with pytest.raises(MacroError, match="boom"):
        _run('opensak.attach("Found 2020", "found") error("boom")')
    _, out = _run('print(#opensak.attached(), pcall(opensak.sql, "SELECT * FROM found.caches"))')
    assert out[0].startswith("0\tfalse")


def test_called_macros_see_the_callers_and_their_own_are_undone(dbs, tmp_path):
    (tmp_path / "helper.lua").write_text("""
        local s = {}
        for _, a in ipairs(opensak.attached()) do s[#s + 1] = a.schema end
        print("helper sees", table.concat(s, ","))
        print(#opensak.sql("SELECT * FROM found.caches"))
        opensak.attach("Found 2020", "found")      -- already there: nothing
        opensak.attach("Placed")
        opensak.detach("found")
        if ... then error("helper failed") end
    """, encoding="utf-8")
    _, out = _run("""
        opensak.attach("Found 2020", "found")
        opensak.run_macro("helper.lua")
        local function schemas()
            local s = {}
            for _, a in ipairs(opensak.attached()) do s[#s + 1] = a.schema end
            return table.concat(s, ",")
        end
        print("back", schemas(), #opensak.sql("SELECT * FROM found.caches"))
        print(pcall(opensak.run_macro, "helper.lua", true) == false)
        print("back", schemas(), pcall(opensak.sql, "SELECT * FROM placed.caches"))
    """, folder=tmp_path)
    lines = [line for line in out if not line.startswith("opensak.attach:")]
    assert lines[:-1] == [
        "helper sees\tfound", "3", "back\tfound\t3",
        "helper sees\tfound", "3", "true",
    ]
    # placed (the helper's own) is gone, found (detached by it) is back
    assert lines[-1].startswith("back\tfound\tfalse") and "no such table" in lines[-1]


def test_switch_database_keeps_attachments_but_not_the_new_active_one(dbs):
    _, out = _run("""
        opensak.attach("Found 2020", "found")
        opensak.attach("Placed", "placed")
        opensak.switch_database("Placed")
        print(#opensak.sql("SELECT * FROM found.caches"), #opensak.sql("SELECT * FROM caches"))
        local a = opensak.attached()
        print(#a, a[1].schema)
    """)
    assert out[-3:] == [
        "opensak.attach: detached 'Placed' (placed): it is the active database now",
        "3\t1",
        "1\tfound",
    ]


# ── Schema versions and locking ──────────────────────────────────────────────

def test_an_older_schema_is_attached_as_it_is(dbs):
    found = dbs[1].path
    conn = sqlite3.connect(found)
    conn.execute("PRAGMA user_version = 1")
    conn.close()
    _, out = _run("""
        opensak.attach("Found 2020", "found")
        print(opensak.sql("PRAGMA found.user_version")[1].user_version)
        print(#opensak.sql("SELECT gc_code FROM found.caches"))
    """)
    assert out[-2:] == ["1", "3"]
    conn = sqlite3.connect(found)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()


def test_a_database_in_use_by_another_connection(dbs):
    """Another OpenSAK holding a write transaction on the attached file:
    reads see what it committed, and writes to the active one still work."""
    active, found, _ = dbs
    other = sqlite3.connect(found.path, isolation_level=None)
    try:
        assert other.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        other.execute("BEGIN IMMEDIATE")
        other.execute("UPDATE caches SET name = 'uncommitted'")
        _, out = _run("""
            opensak.attach("Found 2020", "found")
            print(#opensak.sql("SELECT * FROM found.caches WHERE name = 'uncommitted'"))
            print(opensak.sql_write("UPDATE caches SET user_flag = 1 "
                .. "WHERE gc_code IN (SELECT gc_code FROM found.caches)"))
            opensak.transaction(function()
                opensak.sql_write("UPDATE caches SET user_sort = 7 "
                    .. "WHERE gc_code IN (SELECT gc_code FROM found.caches)")
            end)
        """)
        assert out[-2:] == ["0", "2"]
        other.execute("ROLLBACK")
    finally:
        other.close()
    with session_for(active.path) as s:
        assert {c.gc_code for c in s.query(Cache).filter_by(user_sort=7)} == {"GCA1", "GCA2"}


def test_a_closed_wal_database_without_shm_file(dbs):
    found = dbs[1].path
    for suffix in ("-wal", "-shm"):
        assert not found.with_name(found.name + suffix).exists()
    _, out = _run('opensak.attach("Found 2020", "found") print(#opensak.sql("SELECT * FROM found.caches"))')
    assert out[-1] == "3"
    with get_session() as s:
        assert s.query(Cache).count() == 4

"""tests/unit-tests/test_macro_databases.py — Lua macro database functions
(opensak.databases, database, database_exists, create_database,
switch_database, move_caches, copy_caches) against real database files in
an isolated DatabaseManager (conftest's _isolated_app_paths)."""

from datetime import datetime

import pytest

from opensak.db.database import get_session, session_for
from opensak.db.manager import get_db_manager
from opensak.db.models import Cache, Log, UserNote
from opensak.export.file_export import active_database_name
from opensak.filters.engine import FilterSet, apply_filters_auto
from opensak.macro import MacroError, MacroRuntime

OLD, NEW = datetime(2024, 1, 1), datetime(2025, 1, 1)


class Host:
    """MacroHost on the real DatabaseManager and the active database."""

    def __init__(self):
        self.filterset, self.label = FilterSet(), ""
        self.switched: list[str] = []
        self.list_changed = 0
        self.removed = 0

    def apply_filter(self, filterset, label):
        with get_session() as s:
            n = len(apply_filters_auto(s, filterset))
        if n:
            self.filterset, self.label = filterset, label
        return n

    def clear_filter(self):
        self.filterset, self.label = FilterSet(), ""

    def filtered_caches(self):
        with get_session() as s:
            return apply_filters_auto(s, self.filterset)

    def cache_count(self):
        return len(self.filtered_caches())

    def database_name(self):
        return active_database_name()

    def switch_database(self, name):
        manager = get_db_manager()
        manager.switch_to(next(d for d in manager.databases if d.name == name))
        self.switched.append(name)
        self.clear_filter()

    def database_list_changed(self):
        self.list_changed += 1

    def caches_removed(self):
        self.removed += 1

    def end_macro(self):
        pass


def _add(path, code, imported=OLD, name=None):
    with session_for(path) as s:
        cache = Cache(gc_code=code, name=name or code, cache_type="Traditional Cache",
                      latitude=47.0, longitude=8.0, last_gpx_update=imported)
        s.add(cache)
        s.flush()
        s.add(Log(cache_id=cache.id, log_type="Found it", finder="me", text="TFTC"))
        s.add(UserNote(cache_id=cache.id, note=f"note {code}"))


def _codes(path) -> dict[str, str]:
    with session_for(path) as s:
        return {c.gc_code: c.name for c in s.query(Cache)}


@pytest.fixture
def dbs():
    """Active database "Source" with GCDB1..3, empty database "Target"."""
    manager = get_db_manager()
    source = manager.new_database("Source")
    manager.switch_to(source)
    target = manager.new_database("Target")
    for code in ("GCDB1", "GCDB2", "GCDB3"):
        _add(source.path, code, imported=NEW)
    return source, target


def _run(source, host=None):
    out: list[str] = []
    host = host or Host()
    MacroRuntime(host, output=out.append).run(source)
    return host, out


def test_databases_lists_all_with_active_flag(dbs):
    _, out = _run("""
        for _, db in ipairs(opensak.databases()) do
            print(db.name, db.active, type(db.size_mb), db.path:sub(-3))
        end
        print(opensak.database(), opensak.database_exists("Target"),
              opensak.database_exists("target"), opensak.database_exists("Nope"))
    """)
    assert out == [
        "Default\tfalse\tnumber\t.db",
        "Source\ttrue\tnumber\t.db",
        "Target\tfalse\tnumber\t.db",
        "Source\ttrue\tfalse\tfalse",
    ]


def test_create_database(dbs):
    host, out = _run("""
        print(opensak.create_database("  CH_Zurich "))
        print(opensak.database_exists("CH_Zurich"), opensak.database())
    """)
    assert out == ["CH_Zurich", "true\tSource"]
    assert host.list_changed == 1
    db = next(d for d in get_db_manager().databases if d.name == "CH_Zurich")
    assert db.path.exists()


def test_create_database_rejects_existing_name(dbs):
    with pytest.raises(MacroError, match="a database named .Target. exists already"):
        _run('opensak.create_database("Target")')


def test_switch_database_clears_filter_and_reopens_sql(dbs):
    _, target = dbs
    _add(target.path, "GCT1")
    host, out = _run("""
        opensak.filter{ code = "GCDB1" }
        print(opensak.count(), opensak.sql("SELECT COUNT(*) AS n FROM caches")[1].n)
        opensak.switch_database("Target")
        print(opensak.database(), opensak.count(),
              opensak.sql("SELECT gc_code FROM caches")[1].gc_code)
    """)
    assert out == ["1\t3", "Target\t1\tGCT1"]
    assert host.switched == ["Target"] and host.label == ""


def test_switch_database_unknown_name(dbs):
    with pytest.raises(MacroError, match="no database named 'Nope'"):
        _run('opensak.switch_database("Nope")')


def test_copy_caches_of_active_filter(dbs):
    source, target = dbs
    host, out = _run("""
        opensak.filter{ code = "GCDB" }
        print(opensak.copy_caches("Target"))
    """)
    assert out == ["3"]
    assert set(_codes(target.path)) == {"GCDB1", "GCDB2", "GCDB3"}
    assert set(_codes(source.path)) == {"GCDB1", "GCDB2", "GCDB3"}
    assert host.removed == 0
    with session_for(target.path) as s:
        cache = s.query(Cache).filter_by(gc_code="GCDB2").one()
        assert [log.text for log in cache.logs] == ["TFTC"]
        assert cache.user_note.note == "note GCDB2"


def test_move_caches_by_code(dbs):
    source, target = dbs
    host, out = _run('print(opensak.move_caches("Target", { codes = {"gcdb1", "GCDB3"} }))')
    assert out == ["2"]
    assert set(_codes(target.path)) == {"GCDB1", "GCDB3"}
    assert set(_codes(source.path)) == {"GCDB2"}
    assert host.removed == 1


@pytest.mark.parametrize("if_exists, target_imported, written", [
    (None, OLD, True),        # default "newer": source copy is newer
    (None, NEW, False),       # same import time → not newer
    ("newer", OLD, True),
    ("replace", NEW, True),
    ("skip", OLD, False),
])
def test_if_exists(dbs, if_exists, target_imported, written):
    source, target = dbs
    _add(target.path, "GCDB1", imported=target_imported, name="target copy")
    option = f', if_exists = "{if_exists}"' if if_exists else ""
    host, out = _run(f'print(opensak.move_caches("Target", {{ codes = {{"GCDB1"}}{option} }}))')

    assert out == [str(int(written))]
    assert _codes(target.path)["GCDB1"] == ("GCDB1" if written else "target copy")
    # A cache that was not written to the target stays in the source.
    assert ("GCDB1" in _codes(source.path)) is not written
    assert host.removed == int(written)


@pytest.mark.parametrize("call, msg", [
    ('opensak.move_caches("Source")', "'Source' is the active database"),
    ('opensak.copy_caches("Nope")', "no database named 'Nope'"),
    ("opensak.copy_caches()", "opensak.copy_caches expects a database name"),
    ('opensak.copy_caches("Target", "GC1")', "options must be a table"),
    ('opensak.copy_caches("Target", { code = "GC1" })', r"unknown option\(s\) \['code'\]"),
    ('opensak.copy_caches("Target", { if_exists = "always" })', "if_exists must be one of"),
    ('opensak.copy_caches("Target", { codes = { 1 } })', "expects a GC code"),
])
def test_transfer_rejects_bad_input(dbs, call, msg):
    with pytest.raises(MacroError, match=msg):
        _run(call)


def test_transfer_refuses_missing_target_file(dbs):
    _, target = dbs
    get_db_manager().new_database("Gone").path.unlink()
    with pytest.raises(MacroError, match="file of database 'Gone' is missing"):
        _run('opensak.copy_caches("Gone")')


# ── Reading another database (the `database` option) ────────────────────────

@pytest.fixture
def two_dbs(dbs):
    """dbs, plus GCDB2 (renamed, with a description) and GCDB9 in "Target"."""
    source, target = dbs
    _add(target.path, "GCDB9", name="Zulu")
    _add(target.path, "GCDB2", name="Alpha")
    with session_for(target.path) as s:
        s.query(Cache).filter_by(gc_code="GCDB2").update({"long_description": "in target"})
    return source, target


def test_cache_and_description_read_another_database(two_dbs):
    host = Host()
    host.apply_filter(FilterSet(), "all")
    _, out = _run("""
        print(opensak.cache("GCDB2").name, opensak.cache("GCDB2", { database = "Target" }).name)
        print(opensak.cache("GCDB1", { database = "Target" }), opensak.cache("GCDB9"))
        print(opensak.cache("GCDB2", { database = "Source" }).name)
        print(opensak.description("GCDB2", { database = "Target" }).long)
        print(opensak.description("GCDB2").long)
    """, host)
    assert out == ["GCDB2\tAlpha", "nil\tnil", "GCDB2", "in target", "nil"]
    assert host.switched == []
    assert host.label == "all"


def test_caches_of_another_database(two_dbs):
    _, out = _run("""
        for c in opensak.caches{ database = "Target", fields = {"name"} } do
            print(c.code, c.name, c.note)
        end
        for c in opensak.caches{ database = "Target", name = "Zu" } do print(c.code, c.note) end
        local n = 0
        for _ in opensak.caches() do n = n + 1 end
        print(n)
    """)
    assert out == ["GCDB2\tAlpha\tnil", "GCDB9\tZulu\tnil", "GCDB9\tnote GCDB9", "3"]


def test_sql_functions_read_another_database(two_dbs):
    _, out = _run("""
        local opts = { database = "Target" }
        print(#opensak.sql("SELECT gc_code FROM caches", nil, opts), #opensak.sql("SELECT gc_code FROM caches"))
        for r in opensak.sql_each("SELECT name FROM caches WHERE gc_code = ?", { "GCDB9" }, opts) do
            print(r.name)
        end
        local has = {}
        for _, t in ipairs(opensak.tables(opts)) do has[t] = true end
        print(has.caches, #opensak.columns("caches", opts) > 10)
    """)
    assert out == ["2\t3", "Zulu", "true\ttrue"]


def test_another_database_is_read_only(two_dbs):
    with pytest.raises(MacroError, match="read-only"):
        _run('opensak.sql("DELETE FROM caches", {}, { database = "Target" })')
    assert set(_codes(two_dbs[1].path)) == {"GCDB2", "GCDB9"}


@pytest.mark.parametrize("call, msg", [
    ('opensak.cache("GCDB1", { database = "Nope" })', "no database named 'Nope'"),
    ('opensak.cache("GCDB1", "Target")', "options must be a table"),
    ('opensak.cache("GCDB1", { db = "Target" })', r"unknown option\(s\) \['db'\]"),
    ('opensak.caches{ database = "Nope" }', "opensak.caches: no database named"),
    ('opensak.tables({ database = 1 })', "expects a database name"),
])
def test_database_option_errors(two_dbs, call, msg):
    with pytest.raises(MacroError, match=msg):
        _run(call)


def test_older_schema_is_refused_for_cache_reads(two_dbs):
    import sqlite3

    target = two_dbs[1].path
    conn = sqlite3.connect(target)
    conn.execute("PRAGMA user_version = 1")
    conn.close()
    with pytest.raises(MacroError, match="older OpenSAK version"):
        _run('opensak.cache("GCDB9", { database = "Target" })')
    # Raw SQL does not depend on the schema; the file is left as it was.
    _, out = _run('print(#opensak.sql("SELECT 1 AS x", nil, { database = "Target" }))')
    assert out == ["1"]
    conn = sqlite3.connect(target)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()

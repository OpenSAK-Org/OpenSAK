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

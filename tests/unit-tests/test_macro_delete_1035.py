"""tests/unit-tests/test_macro_delete_1035.py — deleting from Lua macros
(#1035): opensak.delete(), delete_log/waypoint/attribute/trackable() and
DELETE in opensak.sql_write(): the cascade to a cache's rows, the derived
columns, transactions, the host notification, the report at the end of the
run and the refusal paths."""

import pytest

from opensak.db.database import get_session
from opensak.db.models import Attribute, Cache, Log, Trackable, UserNote, Waypoint
from opensak.macro import MacroError, MacroRuntime, WriteApproval
from opensak.macro import db_access

_CHILDREN = (Log, Waypoint, Attribute, Trackable, UserNote)


class Host:
    """A MacroHost with what the write and delete functions use."""

    def __init__(self, answer=WriteApproval.SESSION):
        self.answer = answer
        self.asked = 0
        self.changed: list[tuple[list[str], bool]] = []
        self.removed = 0

    def database_name(self):
        return "TestDB"

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def approve_macro_write(self, macro, change=None, editing=False):
        self.asked += 1
        return self.answer

    def caches_changed(self, codes, added):
        self.changed.append((list(codes), added))

    def caches_removed(self):
        self.removed += 1

    def end_macro(self):
        pass


def _run(source, host=None):
    out: list[str] = []
    host = host or Host()
    MacroRuntime(host, output=out.append).run(source)
    return host, out


def _add(code, logs=2, waypoints=1, note=True):
    """A cache with logs, waypoints, an attribute, a trackable and a note."""
    with get_session() as s:
        cache = Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                      latitude=47.0, longitude=8.0, archived=code.endswith("A"))
        s.add(cache)
        s.flush()
        for i in range(logs):
            s.add(Log(cache_id=cache.id, log_type="Found it", finder=f"f{i}"))
        for i in range(waypoints):
            s.add(Waypoint(cache_id=cache.id, prefix=f"P{i}", wp_type="Parking Area"))
        s.add(Attribute(cache_id=cache.id, attribute_id=1, name="Dogs", is_on=True))
        s.add(Trackable(cache_id=cache.id, ref="TB1", name="Bug"))
        if note:
            s.add(UserNote(cache_id=cache.id, note="n"))


def _exists(code) -> bool:
    with get_session() as s:
        return s.query(Cache.id).filter_by(gc_code=code).first() is not None


def _cache(code) -> Cache:
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code=code).one()
        s.expunge(cache)
        return cache


def _orphans() -> int:
    """Rows whose cache no longer exists."""
    with get_session() as s:
        ids = s.query(Cache.id)
        return sum(s.query(m).filter(~m.cache_id.in_(ids)).count() for m in _CHILDREN)


def _child_ids(model, code) -> list[int]:
    with get_session() as s:
        cache_id = s.query(Cache.id).filter_by(gc_code=code).scalar()
        return [r.id for r in s.query(model).filter_by(cache_id=cache_id).order_by(model.id)]


@pytest.fixture(scope="module", autouse=True)
def database(tmp_db):
    yield


@pytest.fixture(autouse=True)
def approved():
    db_access.reset_session()
    yield
    db_access.reset_session()


# ── opensak.delete() ─────────────────────────────────────────────────────────

def test_delete_removes_the_cache_and_all_its_rows():
    _add("GCDEL1")
    host, out = _run("""
        print(opensak.delete("gcdel1"))
        print(opensak.delete("GCDEL1"))
    """)
    assert out == ["true", "false", "opensak: 1 cache deleted from database 'TestDB'"]
    assert not _exists("GCDEL1")
    assert _orphans() == 0
    assert host.removed == 1 and host.asked == 1


def test_delete_takes_a_list_and_returns_the_count():
    for code in ("GCDL1", "GCDL2", "GCDL3"):
        _add(code)
    host, out = _run('print(opensak.delete({ "GCDL1", "GCDL3", "GCNONE", "GCDL1" }))')
    assert out == ["2", "opensak: 2 caches deleted from database 'TestDB'"]
    assert not _exists("GCDL1") and _exists("GCDL2") and not _exists("GCDL3")
    assert _orphans() == 0


def test_delete_of_nothing_asks_and_reports_nothing():
    host, out = _run("""
        print(opensak.delete({}))
        print(opensak.delete("GCNONE"))
    """)
    assert out == ["0", "false"]
    assert host.removed == 0


@pytest.mark.parametrize("arg", ["nil", "42", "''", "{ 42 }"])
def test_delete_rejects_bad_arguments(arg):
    with pytest.raises(MacroError, match="opensak.delete"):
        _run(f"opensak.delete({arg})")


def test_delete_needs_write_permission():
    _add("GCDENY")
    host = Host(WriteApproval.DENY)
    with pytest.raises(MacroError, match="did not allow"):
        _run('opensak.delete("GCDENY")', host)
    with pytest.raises(MacroError, match="did not allow"):
        _run(f"opensak.delete_log({_child_ids(Log, 'GCDENY')[0]})", host)
    with pytest.raises(MacroError, match="did not allow"):
        _run('opensak.sql_write("DELETE FROM caches WHERE gc_code = \'GCDENY\'")', host)
    assert _exists("GCDENY") and len(_child_ids(Log, "GCDENY")) == 2


# ── delete_log / waypoint / attribute / trackable ────────────────────────────

def test_delete_log_updates_the_counts():
    _add("GCDLOG", logs=3)
    assert _cache("GCDLOG").log_count in (0, None)       # not derived by _add()
    ids = _child_ids(Log, "GCDLOG")
    host, out = _run(f"""
        print(opensak.delete_log({ids[0]}))
        print(opensak.delete_log({ids[0]}))
    """)
    assert out == ["true", "false"]
    assert _child_ids(Log, "GCDLOG") == ids[1:]
    assert _cache("GCDLOG").log_count == 2
    assert host.changed == [(["GCDLOG"], False)] and host.removed == 0


@pytest.mark.parametrize("func, model, column, left", [
    ("delete_waypoint", Waypoint, "waypoint_count", 1),
    ("delete_trackable", Trackable, "trackable_count", 0),
    ("delete_attribute", Attribute, None, 0),
])
def test_delete_child_rows(func, model, column, left):
    code = f"GCD{func[7:10].upper()}"
    _add(code, waypoints=2)
    row_id = _child_ids(model, code)[0]
    _, out = _run(f"print(opensak.{func}({row_id}))")
    assert out == ["true"]
    assert len(_child_ids(model, code)) == left
    if column:
        assert getattr(_cache(code), column) == left


def test_delete_child_rejects_a_bad_id():
    with pytest.raises(MacroError, match="expects the log's id"):
        _run('opensak.delete_log("GC12345")')


# ── DELETE in opensak.sql_write() ────────────────────────────────────────────

def test_sql_delete_of_caches_cascades():
    for code in ("GCSQLDA", "GCSQLDB", "GCSQLDC"):
        _add(code)
    host, out = _run("""
        print(opensak.sql_write("DELETE FROM caches WHERE gc_code LIKE 'GCSQLD%' AND archived = 1"))
    """)
    assert out == ["1", "opensak: 1 cache deleted from database 'TestDB'"]
    assert not _exists("GCSQLDA") and _exists("GCSQLDB") and _exists("GCSQLDC")
    assert _orphans() == 0
    assert host.removed == 1


def test_sql_delete_of_logs_recalculates_the_caches():
    _add("GCSQLLOG", logs=7)
    host, out = _run("""
        print(opensak.sql_write([[DELETE FROM logs WHERE cache_id =
            (SELECT id FROM caches WHERE gc_code = 'GCSQLLOG') AND id NOT IN (
            SELECT id FROM logs l2 WHERE l2.cache_id = logs.cache_id ORDER BY id DESC LIMIT 5)]]))
    """)
    assert out == ["2"]
    assert _cache("GCSQLLOG").log_count == 5
    assert host.changed == [(["GCSQLLOG"], False)] and host.removed == 0


@pytest.mark.parametrize("query", [
    "INSERT OR REPLACE INTO caches (gc_code, name, cache_type, latitude, longitude) "
    "VALUES ('GCREPL', 'Replaced', 'Traditional Cache', 1, 1)",
    "replace into caches (gc_code, name, cache_type, latitude, longitude) "
    "VALUES ('GCREPL', 'Replaced', 'Traditional Cache', 1, 1)",
    "WITH x AS (SELECT 1) REPLACE INTO user_notes (cache_id, note) "
    "SELECT id, 'n' FROM caches WHERE gc_code = 'GCREPL'",
    "UPDATE OR /* x */ REPLACE caches SET name = 'Replaced' WHERE gc_code = 'GCREPL'",
    "CREATE TEMP TABLE t (a UNIQUE ON CONFLICT REPLACE)",
])
def test_sql_replace_is_refused(query):
    _add("GCREPL") if not _exists("GCREPL") else None
    with pytest.raises(MacroError, match="ON CONFLICT \\(gc_code\\) DO UPDATE"):
        _run(f"opensak.sql_write([[{query}]])")
    assert _cache("GCREPL").name == "GCREPL"
    assert len(_child_ids(Log, "GCREPL")) == 2 and len(_child_ids(UserNote, "GCREPL")) == 1


def test_sql_upsert_keeps_the_cache_rows():
    _add("GCUPS")
    host, out = _run("""
        print(opensak.sql_write([[INSERT INTO caches
            (gc_code, name, cache_type, latitude, longitude) VALUES
            ('GCUPS', 'Upserted', 'Traditional Cache', 47, 8)
            ON CONFLICT (gc_code) DO UPDATE SET name = excluded.name]]))
        -- replace() the function, and REPLACE in strings, comments and names
        print(opensak.sql_write([[UPDATE caches SET name = replace(name, 'Up', 'Re') -- or replace
            WHERE gc_code = 'GCUPS' AND 'or replace' <> "name"]]))
    """)
    assert out == ["1", "1"]
    assert _cache("GCUPS").name == "Reserted"
    assert len(_child_ids(Log, "GCUPS")) == 2 and len(_child_ids(UserNote, "GCUPS")) == 1
    assert host.removed == 0


@pytest.mark.parametrize("query", [
    "DELETE FROM db_settings",
    "DELETE FROM sqlite_master",
    "DELETE FROM temp.opensak_macro_removed",
    "DELETE FROM temp.opensak_macro_touched",
])
def test_sql_delete_refused_outside_the_cache_tables(query):
    _add("GCKEEP") if not _exists("GCKEEP") else None
    with pytest.raises(MacroError):
        _run(f'opensak.sql_write("{query}")')
    assert _exists("GCKEEP")


def test_sql_read_stays_read_only():
    _add("GCRO")
    with pytest.raises(MacroError, match="read-only"):
        _run("opensak.sql(\"DELETE FROM caches WHERE gc_code = 'GCRO'\")")
    assert _exists("GCRO")


# ── Transactions ─────────────────────────────────────────────────────────────

def test_deletes_are_undone_with_the_transaction():
    _add("GCTXD1", logs=3)
    _add("GCTXD2")
    log_id = _child_ids(Log, "GCTXD1")[0]
    host, out = _run(f"""
        print(pcall(opensak.transaction, function()
            opensak.delete_log({log_id})
            opensak.delete("GCTXD2")
            opensak.sql_write("DELETE FROM caches WHERE gc_code = 'GCTXD1'")
            print(opensak.cache("GCTXD1"), opensak.cache("GCTXD2"))
            error("undo")
        end))
    """)
    assert out[0] == "nil\tnil"
    assert out[1].startswith("false")
    assert len(out) == 2                                 # no deleted-caches report
    assert _exists("GCTXD1") and _exists("GCTXD2")
    assert len(_child_ids(Log, "GCTXD1")) == 3


def test_deletes_in_a_transaction_are_committed_and_recalculated():
    _add("GCTXC1", logs=3)
    _add("GCTXC2")
    ids = _child_ids(Log, "GCTXC1")
    _, out = _run(f"""
        opensak.transaction(function()
            opensak.delete_log({ids[0]})
            opensak.sql_write("DELETE FROM logs WHERE id = {ids[1]}")
            opensak.delete("GCTXC2")
            print(opensak.cache("GCTXC1").log_count)     -- recalculated at the commit
            print(opensak.resync())
            print(opensak.cache("GCTXC1").log_count)
        end)
    """)
    assert out[2] == "1"
    assert out[-1] == "opensak: 1 cache deleted from database 'TestDB'"
    assert not _exists("GCTXC2") and _cache("GCTXC1").log_count == 1
    assert _orphans() == 0


def test_inner_transaction_undone_keeps_the_outer_deletes():
    _add("GCTXO1")
    _add("GCTXO2")
    _, out = _run("""
        opensak.transaction(function()
            opensak.delete("GCTXO1")
            pcall(opensak.transaction, function()
                opensak.delete("GCTXO2")
                error("undo inner")
            end)
        end)
    """)
    assert out == ["opensak: 1 cache deleted from database 'TestDB'"]
    assert not _exists("GCTXO1") and _exists("GCTXO2")

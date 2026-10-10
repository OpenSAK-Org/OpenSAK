"""tests/unit-tests/test_macro_transaction.py — opensak.transaction(): all
writes of the function kept together or undone together, reads inside it
seeing its own changes, nesting, and what is refused inside."""

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache, UserNote
from opensak.macro import MacroError, MacroRuntime, WriteApproval


class Host:
    """A MacroHost with what the write functions use."""

    def __init__(self, answer=WriteApproval.SESSION):
        self.answer = answer
        self.asked = 0
        self.changed: list[tuple[list[str], bool]] = []

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
        self.changed.append((list(codes), added))

    def set_corrected_coords(self, gc_code, lat, lon):
        raise AssertionError("inside a transaction the runtime writes itself")

    def end_macro(self):
        pass


def _run(source, host=None, **kwargs):
    out: list[str] = []
    host = host or Host()
    MacroRuntime(host, output=out.append, **kwargs).run(source)
    return host, out


def _cache(code):
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code=code).first()
        if cache is not None:
            s.expunge(cache)
        return cache


def _note(code):
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code=code).one()
        note = s.query(UserNote).filter_by(cache_id=cache.id).first()
        return None if note is None else (note.corrected_lat, note.corrected_lon, note.is_corrected)


@pytest.fixture(scope="module", autouse=True)
def seed(tmp_db):
    with get_session() as s:
        for code in ("GCT1", "GCT2", "GCT3", "GCT4", "GCT5", "GCT6", "GCT7"):
            s.add(Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0))


def test_transaction_keeps_all_changes_and_returns_the_results():
    host, out = _run("""
        local a, b = opensak.transaction(function()
            print(opensak.in_transaction())
            opensak.update("GCT1", { name = "one" })
            opensak.sql_write("UPDATE caches SET name = 'two' WHERE gc_code = 'GCT2'")
            opensak.insert{ code = "GCTNEW", name = "new", type = "Traditional Cache",
                            lat = 47.1, lon = 8.1 }
            opensak.set_corrected("GCT3", 47.5, 8.5)
            return "done", 2
        end)
        print(a, b, opensak.in_transaction())
    """)
    assert out == ["true", "done\t2\tfalse"]
    assert (_cache("GCT1").name, _cache("GCT2").name, _cache("GCTNEW").name) == ("one", "two", "new")
    assert _note("GCT3") == (47.5, 8.5, True)
    assert host.asked == 1
    assert (["GCTNEW"], True) in host.changed and (["GCT3"], False) in host.changed


def test_error_undoes_everything_and_is_raised_unchanged():
    _, out = _run("""
        local ok, err = pcall(opensak.transaction, function()
            opensak.update("GCT4", { name = "changed" })
            opensak.sql_write("UPDATE caches SET name = 'changed' WHERE gc_code = 'GCT5'")
            opensak.insert{ code = "GCTGONE", name = "x", type = "Traditional Cache",
                            lat = 47, lon = 8 }
            error({ code = 7 })
        end)
        print(ok, type(err), err.code, opensak.in_transaction())
    """)
    assert out == ["false\ttable\t7\tfalse"]
    assert (_cache("GCT4").name, _cache("GCT5").name, _cache("GCTGONE")) == ("GCT4", "GCT5", None)


def test_uncaught_error_fails_the_macro_and_keeps_nothing():
    with pytest.raises(MacroError, match="stop here"):
        _run("""
            opensak.transaction(function()
                opensak.update("GCT4", { name = "changed" })
                error("stop here")
            end)
        """)
    assert _cache("GCT4").name == "GCT4"


def test_reads_inside_see_the_changes_made_so_far():
    _, out = _run("""
        pcall(opensak.transaction, function()
            opensak.update("GCT6", { name = "inside" })
            opensak.sql_write("UPDATE caches SET user_data_1 = 'x' WHERE gc_code = 'GCT6'")
            local c = opensak.cache("GCT6")
            print(c.name, c.user_data[1])
            print(opensak.sql("SELECT name FROM caches WHERE gc_code = 'GCT6'")[1].name)
            for row in opensak.sql_each("SELECT user_data_1 FROM caches WHERE gc_code = 'GCT6'") do
                print(row.user_data_1)
            end
            for c in opensak.caches{ code = "GCT6", fields = {"name"} } do print(c.name) end
            error("undo")
        end)
    """)
    assert out == ["inside\tx", "inside", "x", "inside"]
    assert _cache("GCT6").name == "GCT6"


def test_nested_transaction_is_undone_on_its_own():
    _, out = _run("""
        opensak.transaction(function()
            opensak.update("GCT7", { name = "outer" })
            print(pcall(opensak.transaction, function()
                opensak.update("GCT7", { user_data = { [1] = "inner" } })
                error("inner failed", 0)
            end))
            opensak.transaction(function()
                opensak.update("GCT7", { user_data = { [2] = "kept" } })
            end)
        end)
    """)
    assert out == ["false\tinner failed"]
    c = _cache("GCT7")
    assert (c.name, c.user_data_1, c.user_data_2) == ("outer", None, "kept")


def test_failed_write_inside_undoes_only_itself():
    _, out = _run("""
        opensak.transaction(function()
            opensak.update("GCT1", { name = "kept" })
            print(pcall(opensak.sql_write, "UPDATE caches SET gc_code = 'X' WHERE gc_code = 'GCT1'"))
            print(pcall(opensak.insert, { code = "GCT1", name = "dup", type = "Traditional Cache",
                                          lat = 47, lon = 8 }))
        end)
    """)
    assert out[0].startswith("false\t") and out[1].startswith("false\t")
    assert _cache("GCT1").name == "kept"


def test_instruction_limit_inside_rolls_back():
    with pytest.raises(MacroError, match="instruction limit"):
        _run("""
            opensak.transaction(function()
                opensak.update("GCT5", { name = "endless" })
                while true do end
            end)
        """, instruction_limit=200_000)
    assert _cache("GCT5").name == "GCT5"


def test_denied_write_runs_nothing():
    host = Host(answer=WriteApproval.DENY)
    from opensak.macro import db_access
    db_access.reset_session()
    with pytest.raises(MacroError, match="did not allow"):
        _run('opensak.transaction(function() print("ran") end)', host)
    assert host.asked == 1


@pytest.mark.parametrize("call", [
    'opensak.switch_database("Other")',
    'opensak.copy_caches("Other")',
    'opensak.move_caches("Other")',
])
def test_refused_inside(call):
    with pytest.raises(MacroError, match="cannot be used inside opensak.transaction"):
        _run(f"opensak.transaction(function() {call} end)")


@pytest.mark.parametrize("query", ["BEGIN", "COMMIT", "SAVEPOINT x", "ROLLBACK"])
def test_sql_transaction_control_is_refused(query):
    with pytest.raises(MacroError):
        _run(f'opensak.transaction(function() opensak.sql_write("{query}") end)')


def test_needs_a_function():
    with pytest.raises(MacroError, match="expects a function"):
        _run("opensak.transaction(42)")

"""tests/unit-tests/test_macro_resync.py — inside opensak.transaction() the
derived cache columns are recalculated once before the commit (or by
opensak.resync()), for ORM writes and opensak.sql_write() alike; undone
writes leave nothing pending."""

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.macro import MacroError, MacroRuntime, WriteApproval


class Host:
    """A MacroHost with what the write functions use."""

    def database_name(self):
        return "TestDB"

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def approve_macro_write(self, macro):
        return WriteApproval.SESSION

    def caches_changed(self, codes, added):
        pass

    def end_macro(self):
        pass


def _run(source):
    out: list[str] = []
    MacroRuntime(Host(), output=out.append).run(source)
    return out


def _cache(code):
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code=code).one()
        s.expunge(cache)
        return cache


@pytest.fixture(scope="module", autouse=True)
def seed(tmp_db):
    with get_session() as s:
        for code in ("GCRS1", "GCRS2", "GCRS3", "GCRS4", "GCRS5", "GCRS6"):
            s.add(Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0))


def test_recalculated_at_commit_and_on_resync():
    out = _run("""
        opensak.transaction(function()
            for i = 1, 3 do opensak.insert_log("GCRS1", { type = "Write note", date = "2026-01-0" .. i }) end
            print(opensak.cache("GCRS1").log_count or 0)
            print(opensak.resync())
            print(opensak.cache("GCRS1").log_count, opensak.cache("GCRS1").last_log_date)
            print(opensak.resync())
            opensak.insert_log("GCRS1", { type = "Write note", date = "2026-02-01" })
            print(opensak.cache("GCRS1").log_count)
        end)
        print(opensak.resync())
    """)
    assert out == ["0", "1", "3\t2026-01-03", "0", "3", "0"]
    cache = _cache("GCRS1")
    assert cache.log_count == 4
    assert cache.last_log_date.date().isoformat() == "2026-02-01"


def test_orm_and_sql_writes_are_collected_together():
    _run("""
        opensak.transaction(function()
            opensak.insert_log("GCRS2", { type = "Found it", date = "2026-03-01" })
            opensak.sql_write("INSERT INTO logs (cache_id, log_type, log_date, text_encoded, "
                .. "logged_by_owner) SELECT id, 'Write note', '2026-04-01 00:00:00.000000', 0, 0 "
                .. "FROM caches WHERE gc_code = 'GCRS2'")
            opensak.insert_waypoint("GCRS2", { prefix = "PK", type = "Parking Area" })
            opensak.sql_write("UPDATE caches SET name = 'renamed' WHERE gc_code = 'GCRS2'")
        end)
    """)
    cache = _cache("GCRS2")
    assert (cache.log_count, cache.waypoint_count, cache.name) == (2, 1, "renamed")
    assert cache.last_found_date.date().isoformat() == "2026-03-01"


def test_sql_insert_is_found_by_code_inside_the_transaction():
    out = _run("""
        local given = { gc_code = " gcrsnew ", name = "new", cache_type = "Traditional Cache",
                        latitude = 47.0, longitude = 8.0, imported_at = "2020-01-01" }
        local names, values = {}, {}
        for _, c in ipairs(opensak.columns("caches")) do
            if c.required or given[c.name] then
                names[#names + 1] = c.name
                values[#values + 1] = given[c.name] or 0
            end
        end
        opensak.transaction(function()
            opensak.sql_write("INSERT INTO caches (" .. table.concat(names, ", ") .. ") VALUES ("
                .. string.rep("?", #names, ", ") .. ")", values)
            print(opensak.cache("GCRSNEW").name)
        end)
    """)
    assert out == ["new"]
    assert _cache("GCRSNEW").imported_at is not None


def test_failed_statement_keeps_earlier_pending_caches():
    _run("""
        opensak.transaction(function()
            opensak.insert_log("GCRS3", { type = "Write note" })
            print(pcall(opensak.sql_write, "UPDATE logs SET cache_id = 1"))
        end)
    """)
    assert _cache("GCRS3").log_count == 1


def test_undone_writes_leave_nothing_behind():
    _run("""
        opensak.transaction(function()
            opensak.insert_log("GCRS4", { type = "Write note" })
            pcall(opensak.transaction, function()
                opensak.insert_log("GCRS4", { type = "Write note" })
                opensak.insert_log("GCRS5", { type = "Write note" })
                error("undo inner")
            end)
        end)
        pcall(opensak.transaction, function()
            opensak.insert_log("GCRS5", { type = "Write note" })
            error("undo all")
        end)
    """)
    assert _cache("GCRS4").log_count == 1
    assert _cache("GCRS5").log_count == 0
    out = _run("""
        opensak.transaction(function() print(opensak.resync()) end)
    """)
    assert out == ["0"]


def test_failed_recalculation_undoes_the_transaction(monkeypatch):
    from opensak.macro import cache_write

    def broken(*args, **kwargs):
        raise RuntimeError("refresh failed")

    monkeypatch.setattr(cache_write, "refresh_derived", broken)
    with pytest.raises(MacroError, match="refresh failed"):
        _run("""
            opensak.transaction(function()
                opensak.insert_log("GCRS6", { type = "Write note" })
            end)
        """)
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCRS6").one()
        assert len(cache.logs) == 0

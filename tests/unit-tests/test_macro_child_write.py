"""tests/unit-tests/test_macro_child_write.py — opensak.logs/waypoints/
attributes/trackables() and their insert_/update_ functions: field
parsing, derived cache columns, permission, transactions, constraints."""

import pytest

from opensak.db.database import get_session
from opensak.db.models import Attribute, Cache, Log, Trackable, Waypoint
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

    def approve_macro_write(self, macro):
        self.asked += 1
        return self.answer

    def caches_changed(self, codes, added):
        self.changed.append((list(codes), added))

    def end_macro(self):
        pass


def _run(source, host=None):
    out: list[str] = []
    host = host or Host()
    MacroRuntime(host, output=out.append).run(source)
    return host, out


def _cache(code):
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code=code).one()
        s.expunge(cache)
        return cache


@pytest.fixture(scope="module", autouse=True)
def seed(tmp_db):
    with get_session() as s:
        for code in ("GCCH1", "GCCH2", "GCCH3", "GCCH4", "GCCH5"):
            s.add(Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0))


def test_logs_insert_list_update_and_refresh_the_cache():
    host, out = _run("""
        local a = opensak.insert_log("gcch1", { type = "Found it", date = "2026-01-02",
                                                finder = "Someone", text = "TFTC" })
        local b = opensak.insert_log("GCCH1", { type = "Write note", date = "2026-03-04T10:20:30" })
        print(type(a), a ~= b)
        for _, l in ipairs(opensak.logs("GCCH1")) do print(l.id == b, l.type, l.date, l.finder) end
        print(opensak.update_log(a, { text = false, date = "2026-05-06" }))
        print(opensak.logs("GCCH1")[1].text, opensak.logs("GCCH1")[1].date)
        print(opensak.logs("GCNONE"), opensak.insert_log("GCNONE", { type = "Note" }),
              opensak.update_log(999999, { type = "Note" }))
    """)
    assert out == [
        "number\ttrue",
        "true\tWrite note\t2026-03-04T10:20:30\tnil",
        "false\tFound it\t2026-01-02T00:00:00\tSomeone",
        "true",
        "nil\t2026-05-06T00:00:00",
        "nil\tnil\tfalse",
    ]
    cache = _cache("GCCH1")
    assert cache.log_count == 2
    assert cache.last_log_date.date().isoformat() == "2026-05-06"
    assert cache.last_found_date.date().isoformat() == "2026-05-06"
    assert host.asked == 1
    assert (["GCCH1"], False) in host.changed


def test_waypoints_set_counts_parent_and_user_flag():
    _, out = _run("""
        local id = opensak.insert_waypoint("GCCH2", { prefix = "FN", type = "Final Location",
                                                      lat = 47.1, lon = 8.1, code = "FNCH2" })
        opensak.insert_waypoint("GCCH2", { prefix = "PK", type = "Parking Area" })
        opensak.update_waypoint(id, { lat = false, lon = false, flag = true, name = "Final" })
        for _, w in ipairs(opensak.waypoints("GCCH2")) do print(w.prefix, w.name, w.lat, w.flag) end
    """)
    assert out == ["FN\tFinal\tnil\ttrue", "PK\tnil\tnil\tfalse"]
    assert _cache("GCCH2").waypoint_count == 2
    with get_session() as s:
        wps = s.query(Waypoint).join(Cache).filter(Cache.gc_code == "GCCH2").all()
        assert {w.parent_gc_code for w in wps} == {"GCCH2"}
        assert all(w.created_by_user for w in wps)


def test_attributes_and_duplicates():
    _, out = _run("""
        local id = opensak.insert_attribute("GCCH3", { attribute_id = 1, name = "Dogs" })
        opensak.update_attribute(id, { on = false })
        local a = opensak.attributes("GCCH3")[1]
        print(a.attribute_id, a.name, a.on)
        print(pcall(opensak.insert_attribute, "GCCH3", { attribute_id = 1 }))
    """)
    assert out[0] == "1\tDogs\tfalse"
    assert out[1].startswith("false\t") and "UNIQUE constraint failed" in out[1]
    with get_session() as s:
        assert s.query(Attribute).join(Cache).filter(Cache.gc_code == "GCCH3").count() == 1


def test_trackables_count():
    _run("""
        local id = opensak.insert_trackable("GCCH4", { ref = "TB1", name = "Bug" })
        opensak.insert_trackable("GCCH4", { ref = "TB2" })
        opensak.update_trackable(id, { name = "Renamed" })
    """)
    assert _cache("GCCH4").trackable_count == 2
    with get_session() as s:
        names = {t.ref: t.name for t in s.query(Trackable).join(Cache).filter(Cache.gc_code == "GCCH4")}
    assert names == {"TB1": "Renamed", "TB2": None}


def test_transaction_undoes_child_rows():
    _run("""
        pcall(opensak.transaction, function()
            opensak.insert_log("GCCH5", { type = "Found it" })
            opensak.insert_waypoint("GCCH5", { prefix = "S1", type = "Stage" })
            error("undo")
        end)
    """)
    with get_session() as s:
        assert s.query(Log).join(Cache).filter(Cache.gc_code == "GCCH5").count() == 0
    assert _cache("GCCH5").log_count == 0
    _, out = _run("""
        opensak.transaction(function()
            opensak.insert_log("GCCH5", { type = "Found it", date = "2026-02-02" })
            print(#opensak.logs("GCCH5"))
        end)
    """)
    assert out == ["1"]
    assert _cache("GCCH5").log_count == 1


@pytest.mark.parametrize("call,msg", [
    ('opensak.insert_log("GCCH5", { date = "2026-01-01" })', "missing field(s) type"),
    ('opensak.insert_log("GCCH5", { type = "" })', "type must be a non-empty string"),
    ('opensak.insert_log("GCCH5", { type = "x", log_id = "abc" })', "unknown field 'log_id'"),
    ('opensak.insert_log("GCCH5")', "expects a table of fields"),
    ('opensak.update_log(1, { id = 2 })', "id cannot be changed"),
    ('opensak.update_log("1", { type = "x" })', "expects the log's id"),
    ('opensak.insert_waypoint("GCCH5", { prefix = "S1" })', "missing field(s) type"),
    ('opensak.insert_waypoint("GCCH5", { prefix = "S1", type = "x", lat = 91 })', "between -90 and 90"),
    ('opensak.update_waypoint(1, { parent_gc_code = "GC1" })', "unknown field"),
    ('opensak.insert_attribute("GCCH5", { name = "x" })', "missing field(s) attribute_id"),
    ('opensak.update_attribute(1, { attribute_id = false })', "attribute_id must be a whole number"),
    ('opensak.insert_trackable("GCCH5", { ref = 5 })', "ref must be a string"),
])
def test_invalid_fields(call, msg):
    host = Host()
    with pytest.raises(MacroError, match=msg.replace("(", r"\(").replace(")", r"\)")):
        _run(call, host)
    assert host.asked == 0


def test_denied_permission_writes_nothing():
    host = Host(WriteApproval.DENY)
    with pytest.raises(MacroError, match="did not allow"):
        _run('opensak.insert_trackable("GCCH5", { ref = "TBX" })', host)
    with get_session() as s:
        assert s.query(Trackable).filter_by(ref="TBX").count() == 0

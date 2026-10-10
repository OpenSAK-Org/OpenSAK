"""tests/unit-tests/test_macro_cache_write.py — changing caches from Lua
macros: opensak.update(), opensak.insert{}, opensak.sql_write(), the
protected fields and columns, the recalculated derived columns, and the
per-database write approval (Deny / until OpenSAK closes / always)."""

from datetime import datetime
from pathlib import Path

import pytest

from opensak.db.database import get_engine, get_session
from opensak.db.models import Cache, Log, UserNote, Waypoint
from opensak.filters.engine import distance_km_batch
from opensak.gui.settings import get_settings
from opensak.macro import MacroError, MacroRuntime, WriteApproval
from opensak.macro import db_access


class Host:
    """A MacroHost with what the write functions use."""

    def __init__(self, answer=WriteApproval.SESSION):
        self.answer = answer
        self.asked: list[tuple[str, Path]] = []
        self.changed: list[tuple[list[str], bool]] = []
        self.corrected: list[tuple] = []

    def database_name(self):
        return "TestDB"

    # Bound when the `opensak` table is built.
    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def approve_database_write(self, name, path):
        self.asked.append((name, path))
        return self.answer

    def caches_changed(self, codes, added):
        self.changed.append((list(codes), added))

    def set_corrected_coords(self, gc_code, lat, lon):
        self.corrected.append((gc_code, lat, lon))
        return True

    def end_macro(self):
        pass


def _run(source, host=None):
    out: list[str] = []
    host = host or Host()
    MacroRuntime(host, output=out.append).run(source)
    return host, out


def _db_path() -> Path:
    return Path(get_engine().url.database or "")


def _cache(code) -> Cache:
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code=code).one()
        s.expunge(cache)
        return cache


@pytest.fixture(scope="module", autouse=True)
def seed(tmp_db):
    with get_session() as s:
        for code in ("GCW1", "GCW2", "GCW3", "GCW4", "GCSQL1", "GCSQL2", "GCSQL3"):
            s.add(Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0, country="Switzerland"))


# ── opensak.update() ─────────────────────────────────────────────────────────

def test_update_changes_only_the_given_fields():
    host, out = _run("""
        print(opensak.update("gcw1", {
            name = "Renamed", difficulty = 2.5, found = true, found_date = "2025-06-01",
            user_data = { [2] = "solved" }, color = "#00aa00", note = "my note",
            corrected = "N47 30.000 E008 15.000",
        }))
        print(opensak.update("GCNONE", { user_flag = true }))
    """)
    assert out == ["true", "false"]
    c = _cache("GCW1")
    assert (c.name, c.difficulty, c.found, c.found_date) == ("Renamed", 2.5, True, datetime(2025, 6, 1))
    assert (c.user_data_1, c.user_data_2, c.color, c.country) == (None, "solved", "#00AA00", "Switzerland")
    with get_session() as s:
        note = s.query(UserNote).filter_by(cache_id=c.id).one()
        assert (note.note, note.is_corrected, note.corrected_lat, note.corrected_lon) == (
            "my note", True, 47.5, 8.25)
    assert host.changed == [(["GCW1"], False)]


def test_update_false_clears_fields():
    _run("""
        opensak.update("GCW2", { container = "Small", corrected = { lat = 47.1, lon = 8.1 } })
        opensak.update("GCW2", { container = false, country = "", corrected = false })
    """)
    c = _cache("GCW2")
    assert (c.container, c.country) == (None, None)
    with get_session() as s:
        assert s.query(UserNote).filter_by(cache_id=c.id).one().is_corrected is False


def test_update_of_coordinates_recalculates_distance():
    _run('opensak.update("GCW3", { lat = 46.5, lon = 7.5 })')
    settings = get_settings()
    expected = float(distance_km_batch(settings.home_lat, settings.home_lon, [46.5], [7.5])[0])
    c = _cache("GCW3")
    assert c.distance == pytest.approx(expected) and c.bearing is not None


@pytest.mark.parametrize("fields, msg", [
    ("{ code = 'GC9' }", "code cannot be changed"),
    ("{ distance = 1 }", "distance cannot be changed"),
    ("{ log_count = 1 }", "log_count cannot be changed"),
    ("{ last_gpx_update = '2025-01-01' }", "last_gpx_update cannot be changed"),
    ("{ nonsense = 1 }", "unknown field 'nonsense'"),
    ("{ difficulty = 6 }", "between 1 and 5"),
    ("{ difficulty = 2.3 }", "steps of 0.5"),
    ("{ found = 1 }", "true or false"),
    ("{ name = '' }", "non-empty string"),
    ("{ type = 'Banana' }", "unknown cache type"),
    ("{ color = 'red' }", "#RRGGBB"),
    ("{ hidden = 'yesterday' }", "YYYY-MM-DD"),
    ("{ user_data = { [5] = 'x' } }", "slots 1 to 4"),
    ("{}", "expects a table of fields"),
])
def test_update_rejects_bad_fields_without_asking(fields, msg):
    host = Host()
    with pytest.raises(MacroError, match=msg):
        _run(f'opensak.update("GCW4", {fields})', host)
    assert host.asked == []          # nothing to approve for a call that fails anyway


# ── opensak.insert{} ─────────────────────────────────────────────────────────

def test_insert_adds_a_cache_with_derived_columns():
    host, out = _run("""
        print(opensak.insert{ code = "gcnew1", name = "New", type = "Unknown",
                              lat = 46.0, lon = 7.0, user_flag = true, note = "bonus" })
    """)
    assert out == ["GCNEW1"]
    c = _cache("GCNEW1")
    assert (c.name, c.cache_type, c.user_flag, c.last_gpx_update) == (
        "New", "Unknown Cache", True, None)
    assert c.distance is not None and c.imported_at is not None
    assert host.changed == [(["GCNEW1"], True)]


@pytest.mark.parametrize("source, msg", [
    ('opensak.insert{ code = "GCW1", name = "x", type = "Traditional", lat = 1, lon = 1 }',
     "already in the database"),
    ('opensak.insert{ code = "GCNEW2", name = "x" }', "missing field"),
    ('opensak.insert{ code = "GCNEW2", name = "x", type = "Traditional", lat = 1, lon = 1, '
     'log_count = 3 }', "log_count cannot be changed"),
])
def test_insert_rejects(source, msg):
    with pytest.raises(MacroError, match=msg):
        _run(source)


# ── Approval ─────────────────────────────────────────────────────────────────

def test_denied_write_fails_and_is_not_asked_again_in_the_run():
    host = Host(WriteApproval.DENY)
    _, out = _run("""
        print(pcall(opensak.update, "GCW4", { user_flag = true }))
        print(pcall(opensak.set_corrected, "GCW4", 47, 8))
    """, host)
    assert len(host.asked) == 1 and host.asked[0] == ("TestDB", _db_path())
    assert all(line.startswith("false") and "did not allow" in line for line in out)
    assert host.corrected == [] and _cache("GCW4").user_flag is False
    # A new run asks again.
    _run('pcall(opensak.clear_corrected, "GCW4")', host)
    assert len(host.asked) == 2


def test_session_approval_lasts_until_reset():
    host = Host(WriteApproval.SESSION)
    _run('opensak.update("GCW4", { user_sort = 1 })', host)
    _run('opensak.update("GCW4", { user_sort = 2 })', host)
    assert len(host.asked) == 1 and _cache("GCW4").user_sort == 2
    assert db_access.always_approved() == []
    db_access.reset_session()                        # OpenSAK restarted
    _run('opensak.update("GCW4", { user_sort = 3 })', host)
    assert len(host.asked) == 2


def test_always_approval_is_stored_and_removable():
    host = Host(WriteApproval.ALWAYS)
    _run('opensak.update("GCW4", { user_sort = 4 })', host)
    assert db_access.always_approved() == [str(_db_path().resolve())]
    db_access.reset_session()
    _run('opensak.update("GCW4", { user_sort = 5 })', host)
    assert len(host.asked) == 1
    # Removed in Settings: asked again, even before a restart.
    db_access.save_always_approved([])
    host.answer = WriteApproval.DENY
    with pytest.raises(MacroError, match="did not allow"):
        _run('opensak.update("GCW4", { user_sort = 6 })', host)
    assert len(host.asked) == 2


def test_set_corrected_needs_approval():
    host = Host(WriteApproval.SESSION)
    _run('opensak.set_corrected("GCW4", 47, 8) opensak.clear_corrected("GCW4")', host)
    assert len(host.asked) == 1 and len(host.corrected) == 2


# ── opensak.sql_write() ──────────────────────────────────────────────────────

# The NOT NULL columns of `caches` without a database default (the model's
# defaults are Python-side only), which a raw INSERT must therefore give.
_FLAGS = ("available", "archived", "premium_only", "short_desc_html", "long_desc_html",
          "found", "dnf", "first_to_find", "user_flag", "watch", "locked",
          "log_count", "trackable_count", "found_log_count", "waypoint_count")


def _insert_cache_sql(verb, code, **columns):
    """An INSERT of a cache with all required columns; *columns* (SQL
    literals) add or override columns."""
    values = {"gc_code": f"'{code}'", "name": "'x'", "cache_type": "'Traditional Cache'",
              "latitude": "1", "longitude": "1", "imported_at": "'2020-01-01'",
              **{flag: "0" for flag in _FLAGS}, **columns}
    return f"{verb} INTO caches ({', '.join(values)}) VALUES ({', '.join(values.values())})"


def test_columns_tells_which_columns_an_insert_needs():
    _, out = _run("""
        local need = {}
        for _, c in ipairs(opensak.columns("caches")) do
            if c.required then need[#need + 1] = c.name end
        end
        print(table.concat(need, ","))
    """)
    required = set(out[0].split(","))
    assert {"gc_code", "name", "cache_type", "latitude", "longitude"} <= required
    assert "id" not in required and "country" not in required
    assert required <= {"gc_code", "name", "cache_type", "latitude", "longitude",
                        "imported_at", *_FLAGS}

def test_sql_write_updates_and_reports_changed_caches():
    host, out = _run("""
        print(opensak.sql_write(
            "UPDATE caches SET user_data_1 = ? WHERE gc_code IN ('GCSQL1', 'GCSQL2')", { "todo" }))
    """)
    assert out == ["2"]
    assert _cache("GCSQL1").user_data_1 == "todo" and _cache("GCSQL2").user_data_1 == "todo"
    assert host.changed == [(["GCSQL1", "GCSQL2"], False)]


@pytest.mark.parametrize("query", [
    "UPDATE caches SET gc_code = 'GCX' WHERE gc_code = 'GCSQL3'",
    "UPDATE caches SET id = 999 WHERE gc_code = 'GCSQL3'",
    "UPDATE caches SET distance = 1 WHERE gc_code = 'GCSQL3'",
    "UPDATE caches SET log_count = 5 WHERE gc_code = 'GCSQL3'",
    "UPDATE user_notes SET cache_id = 1",
    "UPDATE logs SET cache_id = 1",
    "DELETE FROM caches WHERE gc_code = 'GCSQL3'",
    "DROP TABLE caches",
    "CREATE TABLE x (a)",
    "ATTACH DATABASE 'other.db' AS other",
    "PRAGMA foreign_keys = OFF",
    "BEGIN",
    "DELETE FROM temp.opensak_macro_touched",
    "INSERT INTO settings (key, value) VALUES ('a', 'b')",
])
def test_sql_write_refuses(query):
    before = _cache("GCSQL3")
    with pytest.raises(MacroError):
        _run(f'opensak.sql_write([[{query}]])')
    after = _cache("GCSQL3")
    assert (after.id, after.gc_code, after.distance, after.log_count) == (
        before.id, before.gc_code, before.distance, before.log_count)


def test_sql_write_insert_or_replace_cannot_delete():
    with pytest.raises(MacroError, match="may not delete"):
        _run(f"opensak.sql_write([[{_insert_cache_sql('INSERT OR REPLACE', 'GCSQL3')}]])")
    assert _cache("GCSQL3").name == "GCSQL3"


def test_sql_write_insert_gets_derived_columns_recalculated():
    sql = _insert_cache_sql("INSERT", "gcsqlnew", name="'New'", latitude="46.0",
                            longitude="7.0", distance="0.5", log_count="99",
                            last_gpx_update="'2020-01-01'")
    host, _ = _run(f"opensak.sql_write([[{sql}]])")
    c = _cache("GCSQLNEW")
    assert c.log_count == 0 and c.last_gpx_update is None and c.imported_at is not None
    assert c.distance != 0.5 and c.distance is not None
    assert host.changed == [(["GCSQLNEW"], True)]


def test_sql_write_logs_and_waypoints_update_counts():
    cache_id = _cache("GCSQL2").id
    _run(f"""
        opensak.sql_write([[INSERT INTO logs (cache_id, log_type, log_date, finder, text_encoded, logged_by_owner)
                            VALUES ({cache_id}, 'Found it', '2025-05-05 10:00:00.000000', 'someone', 0, 0),
                                   ({cache_id}, 'Write note', '2025-06-06 10:00:00.000000', 'other', 0, 0)]])
        opensak.sql_write([[INSERT INTO waypoints (cache_id, prefix, wp_type, latitude, longitude, created_by_user, wp_flag)
                            VALUES ({cache_id}, 'P1', 'Parking Area', 47, 8, 1, 0)]])
    """)
    c = _cache("GCSQL2")
    assert (c.log_count, c.waypoint_count) == (2, 1)
    assert c.last_log_date == datetime(2025, 6, 6, 10, 0)
    assert c.last_found_date == datetime(2025, 5, 5, 10, 0)
    assert c.last_four_logs.splitlines()[0].startswith("2025-06-06T10:00:00\tWrite note")
    with get_session() as s:
        assert s.query(Waypoint).filter_by(cache_id=cache_id).one().parent_gc_code == "GCSQL2"
        assert s.query(Log).filter_by(cache_id=cache_id).count() == 2


def test_sql_write_coordinates_recalculate_distance():
    _run("opensak.sql_write([[UPDATE caches SET latitude = 45.5 WHERE gc_code = 'GCSQL1']])")
    settings = get_settings()
    expected = float(distance_km_batch(settings.home_lat, settings.home_lon, [45.5], [8.0])[0])
    assert _cache("GCSQL1").distance == pytest.approx(expected)


def test_sql_write_failure_rolls_back_the_statement():
    with pytest.raises(MacroError, match="UNIQUE"):
        _run("opensak.sql_write([[UPDATE caches SET name = 'changed' WHERE gc_code = 'GCSQL1']])"
             f" opensak.sql_write([[{_insert_cache_sql('INSERT', 'GCSQL1')}]])")
    assert _cache("GCSQL1").name == "changed"     # the first statement was committed


def test_sql_write_needs_approval():
    host = Host(WriteApproval.DENY)
    with pytest.raises(MacroError, match="did not allow"):
        _run("opensak.sql_write([[UPDATE caches SET name = 'x' WHERE gc_code = 'GCSQL3']])", host)
    assert _cache("GCSQL3").name == "GCSQL3"

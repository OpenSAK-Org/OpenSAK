"""tests/unit-tests/test_macro_cache_write.py — changing caches from Lua
macros: opensak.update(), opensak.insert{}, opensak.sql_write(), the
protected fields and columns, the recalculated derived columns, and the
per-macro write approval (Deny / until OpenSAK closes / always)."""

from datetime import datetime
from pathlib import Path

import pytest

from opensak.db.database import get_engine, get_session
from opensak.db.models import Cache, Log, UserNote, Waypoint
from opensak.filters.engine import distance_km_batch
from opensak.gui.settings import get_settings
from opensak.macro import FolderPermission, MacroError, MacroRuntime, WriteApproval
from opensak.macro import db_access


class Host:
    """A MacroHost with what the write functions use."""

    def __init__(self, answer=WriteApproval.SESSION):
        self.answer = answer
        self.asked: list[Path | None] = []
        self.changed: list[tuple[list[str], bool]] = []
        self.corrected: list[tuple] = []

    def database_name(self):
        return "TestDB"

    # Bound when the `opensak` table is built.
    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def approve_macro_write(self, macro, change=None, editing=False):
        self.asked.append(macro)
        return self.answer

    def caches_changed(self, codes, added):
        self.changed.append((list(codes), added))

    def set_corrected_coords(self, gc_code, lat, lon):
        self.corrected.append((gc_code, lat, lon))
        return True

    def end_macro(self):
        pass


def _run(source, host=None, macro_path=None, permissions=None):
    """Run *source*, as the macro file *macro_path* if given (it need not
    exist), else as an unsaved macro."""
    out: list[str] = []
    host = host or Host()
    MacroRuntime(host, output=out.append, folder_permissions=permissions).run(
        source,
        chunk_name=macro_path.name if macro_path else "macro",
        base_dir=macro_path.parent if macro_path else None,
        macro_path=macro_path,
    )
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
    ("{ owned = true }", "owned cannot be changed"),
    ("{ last_user_update = '2025-01-01' }", "last_user_update cannot be changed"),
    ("{ corrected_source = 'user' }", "corrected_source cannot be changed"),
    ("{ macro_flag = 1 }", "true or false"),
    ("{ macro_sort = 3 }", "must be a string"),
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
    assert host.asked == [None]                      # an unsaved macro
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


def test_approval_belongs_to_the_macro_file(tmp_path):
    host = Host(WriteApproval.SESSION)
    first, second = tmp_path / "first.lua", tmp_path / "second.lua"
    _run('opensak.update("GCW4", { user_sort = 11 })', host, first)
    _run('opensak.update("GCW4", { user_sort = 11 })', host, first)
    assert host.asked == [first]
    host.answer = WriteApproval.DENY
    with pytest.raises(MacroError, match="did not allow macro 'second.lua'"):
        _run('opensak.update("GCW4", { user_sort = 12 })', host, second)
    with pytest.raises(MacroError, match=r"did not allow macro '\(unsaved\)'"):
        _run('opensak.update("GCW4", { user_sort = 12 })', host)
    assert host.asked == [first, second, None] and _cache("GCW4").user_sort == 11


def test_always_approval_is_stored_by_macro_path_and_removable(tmp_path):
    host = Host(WriteApproval.ALWAYS)
    macro = tmp_path / "always.lua"
    source = 'opensak.update("GCW4", { user_sort = opensak.count() + 4 })'
    _run(source, host, macro)
    assert db_access.always_approved() == [str(macro.resolve())]
    db_access.reset_session()
    _run(source, host, macro)
    assert len(host.asked) == 1
    # Removed in Settings: asked again, even before a restart.
    db_access.save_always_approved([])
    host.answer = WriteApproval.DENY
    with pytest.raises(MacroError, match="did not allow"):
        _run(source, host, macro)
    assert len(host.asked) == 2


def test_unsaved_macro_is_never_approved_always():
    host = Host(WriteApproval.ALWAYS)                # the dialog does not offer it
    _run('opensak.update("GCW4", { user_sort = 7 })', host)
    assert db_access.always_approved() == []
    _run('opensak.update("GCW4", { user_sort = 8 })', host)
    assert len(host.asked) == 1                      # until OpenSAK closes
    db_access.reset_session()
    _run('opensak.update("GCW4", { user_sort = 9 })', host)
    assert len(host.asked) == 2


def test_called_macro_needs_its_own_approval(tmp_path):
    (tmp_path / "helper.lua").write_text(
        'opensak.update("GCW4", { user_sort = 20 })', encoding="utf-8")
    host = Host(WriteApproval.SESSION)
    caller = tmp_path / "caller.lua"
    perms = [FolderPermission(str(tmp_path), read=True, write=False)]
    _run('opensak.update("GCW4", { user_sort = 19 }) opensak.run_macro("helper.lua")',
         host, caller, perms)
    assert host.asked == [caller, tmp_path / "helper.lua"]
    # An approved helper does not approve its caller.
    db_access.reset_session()
    db_access.approve(tmp_path / "helper.lua", WriteApproval.SESSION,
                      db_access.source_hash('opensak.update("GCW4", { user_sort = 20 })'))
    host.answer = WriteApproval.DENY
    with pytest.raises(MacroError, match="did not allow macro 'caller.lua'"):
        _run('opensak.run_macro("helper.lua") opensak.update("GCW4", { user_sort = 21 })',
             host, caller, perms)
    assert _cache("GCW4").user_sort == 20


def test_set_corrected_needs_approval():
    host = Host(WriteApproval.SESSION)
    _run('opensak.set_corrected("GCW4", 47, 8) opensak.clear_corrected("GCW4")', host)
    assert len(host.asked) == 1 and len(host.corrected) == 2


# ── lua-macro-write feature flag ─────────────────────────────────────────────

_WRITE_FUNCTIONS = ("update", "insert", "sql_write", "transaction", "in_transaction",
                    "resync", "insert_log", "update_log", "insert_waypoint",
                    "update_waypoint", "insert_attribute", "update_attribute",
                    "insert_trackable", "update_trackable", "delete", "delete_log",
                    "delete_waypoint", "delete_attribute", "delete_trackable")


def test_write_functions_need_the_flag(monkeypatch):
    from opensak.utils import flags
    source = "".join(f'print(opensak.{f} == nil) ' for f in _WRITE_FUNCTIONS)
    monkeypatch.setattr(flags, "lua_macro_write", False)
    _, out = _run(source)
    assert out == ["true"] * len(_WRITE_FUNCTIONS)
    monkeypatch.setattr(flags, "lua_macro_write", True)
    _, out = _run(source)
    assert out == ["false"] * len(_WRITE_FUNCTIONS)


def test_without_the_flag_reading_stays_and_set_corrected_does_not_ask(monkeypatch):
    from opensak.utils import flags
    monkeypatch.setattr(flags, "lua_macro_write", False)
    host = Host(WriteApproval.DENY)
    _, out = _run('print(#opensak.logs("GCW4")) '
                  'opensak.set_corrected("GCW4", 47, 8) opensak.clear_corrected("GCW4")', host)
    assert out == ["0"]
    assert host.asked == [] and len(host.corrected) == 2


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
    "UPDATE caches SET is_owner = 1 WHERE gc_code = 'GCSQL3'",
    "UPDATE caches SET last_user_update = '2020-01-01' WHERE gc_code = 'GCSQL3'",
    "UPDATE user_notes SET cache_id = 1",
    "UPDATE logs SET cache_id = 1",
    "DELETE FROM db_settings",
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


def test_sql_write_insert_gets_derived_columns_recalculated():
    sql = _insert_cache_sql("INSERT", "gcsqlnew", name="'New'", latitude="46.0",
                            longitude="7.0", distance="0.5", log_count="99",
                            last_gpx_update="'2020-01-01'")
    host, _ = _run(f"opensak.sql_write([[{sql}]])")
    c = _cache("GCSQLNEW")
    assert c.log_count == 0 and c.last_gpx_update is None and c.imported_at is not None
    assert c.distance != 0.5 and c.distance is not None
    assert host.changed == [(["GCSQLNEW"], True)]


def test_insert_columns_match_the_database():
    # The documented INSERT columns are what opensak.columns() marks required
    # in a current database (schema 26, #1033).
    from opensak.macro.sql import INSERT_COLUMNS, WRITABLE_TABLES
    assert set(INSERT_COLUMNS) == WRITABLE_TABLES
    for table, expected in INSERT_COLUMNS.items():
        _, out = _run(f"""
            for _, c in ipairs(opensak.columns("{table}")) do
                if c.required then print(c.name) end
            end""")
        assert out == list(expected), table


def test_sql_write_insert_with_only_the_required_columns():
    _run("""
        opensak.sql_write("INSERT INTO caches (gc_code, name, cache_type, latitude, longitude) "
                          .. "VALUES ('GCMIN1', 'Minimal', 'Traditional Cache', 47.1, 8.1)")
        opensak.sql_write("INSERT INTO logs (cache_id, log_type) "
                          .. "SELECT id, 'Write note' FROM caches WHERE gc_code = 'GCMIN1'")
    """)
    c = _cache("GCMIN1")
    assert c.name == "Minimal" and c.log_count == 1 and c.imported_at is not None


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


# ── The #1139 columns (#1028 step 2c) ────────────────────────────────────────

def _set_account(monkeypatch, username: str) -> None:
    """The geocaching.com account in Settings, as _refresh_owned() reads it."""
    import opensak.gui.settings as settings_module
    real = settings_module.get_settings()

    class Account:
        gc_finder_id = ""
        gc_username = username

        def __getattr__(self, name):
            return getattr(real, name)

    monkeypatch.setattr(settings_module, "get_settings", lambda: Account())


@pytest.fixture
def account(monkeypatch):
    _set_account(monkeypatch, "Me")


def _add_cache(code, **columns) -> None:
    with get_session() as s:
        s.add(Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                    latitude=47.0, longitude=8.0, **columns))


def test_update_writes_the_macro_columns_and_reads_them_back():
    _add_cache("GCG1")
    _, out = _run("""
        opensak.update("GCG1", { macro_flag = true, macro_sort = "0042", symbol = "Geocache Found" })
        local c = opensak.cache("GCG1")
        print(c.macro_flag, c.macro_sort, c.symbol, c.owned, c.corrected_source)
        opensak.update("GCG1", { macro_sort = false, symbol = "" })
        c = opensak.cache("GCG1")
        print(c.macro_flag, c.macro_sort, c.symbol)
    """)
    assert out == ["true	0042	Geocache Found	false	nil", "true	nil	nil"]
    c = _cache("GCG1")
    assert (c.macro_flag, c.macro_sort, c.symbol) == (True, None, None)


def test_macro_columns_do_not_count_as_a_user_update():
    _add_cache("GCG2")
    _run('opensak.update("GCG2", { macro_flag = true, macro_sort = "x" })')
    assert _cache("GCG2").last_user_update is None
    _, out = _run("""
        opensak.update("GCG2", { user_flag = true })
        local c = opensak.cache("GCG2")
        print(opensak.date.parse(c.last_user_update) ~= nil)
    """)
    assert out == ["true"]
    assert _cache("GCG2").last_user_update is not None


def test_corrected_source_is_macro_after_a_macro_correction():
    _add_cache("GCG3")
    _, out = _run("""
        opensak.update("GCG3", { corrected = { lat = 47.1, lon = 8.1 } })
        print(opensak.cache("GCG3").corrected_source)
        opensak.update("GCG3", { corrected = false })
        print(opensak.cache("GCG3").corrected_source)
    """)
    assert out == ["macro", "nil"]


def test_owner_change_and_insert_recalculate_owned(account):
    _add_cache("GCG4", owner_name="Someone")
    _, out = _run("""
        opensak.update("GCG4", { owner = "me" })
        print(opensak.cache("GCG4").owned)
        opensak.insert{ code = "GCG5", name = "x", type = "Traditional", lat = 1, lon = 1,
                        owner = "Me" }
        print(opensak.cache("GCG5").owned)
        opensak.transaction(function()
            opensak.update("GCG4", { owner = "Someone" })
        end)
        print(opensak.cache("GCG4").owned)
    """)
    assert out == ["true", "true", "false"]


def test_owned_stays_without_an_account(monkeypatch):
    _set_account(monkeypatch, "")
    _add_cache("GCG6", owner_name="Someone", is_owner=True)    # e.g. from GSAK
    _run('opensak.update("GCG6", { owner = "Someone else" })')
    assert _cache("GCG6").is_owner is True


def test_sql_write_owner_change_recalculates_is_owner(account):
    _add_cache("GCG7", owner_name="Someone")
    _run("opensak.sql_write([[UPDATE caches SET owner_name = 'ME' WHERE gc_code = 'GCG7']])")
    assert _cache("GCG7").is_owner is True
    insert = _insert_cache_sql("INSERT", "GCG8", owner_name="'Me'", is_owner="0")
    _run(f"opensak.sql_write([[{insert}]])")
    assert _cache("GCG8").is_owner is True


def test_sql_write_of_a_user_field_still_stamps_last_user_update():
    _add_cache("GCG9")
    _run("opensak.sql_write([[UPDATE caches SET user_flag = 1, macro_flag = 1, "
         "symbol = 'Geocache' WHERE gc_code = 'GCG9']])")
    c = _cache("GCG9")
    assert (c.user_flag, c.macro_flag, c.symbol) == (True, True, "Geocache")
    assert c.last_user_update is not None


# ── opensak.macro_flag() and sorting by macro_sort ───────────────────────────

class FilterHost(Host):
    """A Host whose active filter shows *codes*."""

    def __init__(self, codes, answer=WriteApproval.SESSION):
        super().__init__(answer)
        self.codes = codes

    def filtered_caches(self):
        return [Cache(gc_code=c) for c in self.codes]


def _macro_flags(*codes) -> list[bool]:
    return [_cache(c).macro_flag for c in codes]


def test_macro_flag_set_clear_and_swap():
    for code in ("GCMF1", "GCMF2", "GCMF3"):
        _add_cache(code)
    host, out = _run("""
        print(opensak.macro_flag("set", {"gcmf1", "GCMF2"}))
        print(opensak.macro_flag("SET", "GCMF1"))         -- already set: no change
        print(opensak.macro_flag("swap", {"GCMF2", "GCMF3"}))
        print(opensak.macro_flag("clear", "GCMF1"), opensak.macro_flag("set", {}))
    """)
    assert out == ["2", "0", "2", "1	0"]
    assert _macro_flags("GCMF1", "GCMF2", "GCMF3") == [False, False, True]
    assert host.changed == [(["GCMF1", "GCMF2"], False), (["GCMF2", "GCMF3"], False),
                            (["GCMF1"], False)]
    assert _cache("GCMF3").last_user_update is None      # not a user field


def test_macro_flag_defaults_to_the_active_filter_and_all_takes_every_cache():
    for code in ("GCMF4", "GCMF5"):
        _add_cache(code)
    _, out = _run('print(opensak.macro_flag("set"))', FilterHost(["GCMF4"]))
    assert out == ["1"] and _macro_flags("GCMF4", "GCMF5") == [True, False]
    _, out = _run('print(opensak.macro_flag("set", { all = true }) > 0)')
    assert out == ["true"]
    with get_session() as s:
        assert s.query(Cache).filter(Cache.macro_flag == False).count() == 0  # noqa: E712
    _run('opensak.macro_flag("clear", { all = true })')
    with get_session() as s:
        assert s.query(Cache).filter(Cache.macro_flag == True).count() == 0  # noqa: E712


def test_macro_flag_inside_a_transaction_is_undone_with_it():
    _add_cache("GCMF6")
    _run("""
        pcall(opensak.transaction, function()
            opensak.macro_flag("set", "GCMF6")
            error("undo")
        end)
    """)
    assert _macro_flags("GCMF6") == [False]


@pytest.mark.parametrize("call, msg", [
    ('opensak.macro_flag("toggle", "GCW4")', 'must be "set", "clear" or "swap"'),
    ("opensak.macro_flag()", 'must be "set", "clear" or "swap"'),
    ('opensak.macro_flag("set", 5)', "expects a GC code, a list of codes"),
    ('opensak.macro_flag("set", { all = 1 })', "all must be true"),
])
def test_macro_flag_rejects_bad_input_without_asking(call, msg):
    host = Host()
    with pytest.raises(MacroError, match=msg):
        _run(call, host)
    assert host.asked == []


def test_macro_flag_needs_approval():
    _add_cache("GCMF7")
    with pytest.raises(MacroError, match="did not allow"):
        _run('opensak.macro_flag("set", "GCMF7")', Host(WriteApproval.DENY))
    assert _macro_flags("GCMF7") == [False]


def test_sort_by_macro_sort_orders_the_caches():
    from opensak.filters.engine import FilterSet, GcCodeFilter, SortSpec, apply_filters
    for code, key in (("GCMS1", "b"), ("GCMS2", None), ("GCMS3", "A")):
        _add_cache(code, macro_sort=key)
    fs = FilterSet().add(GcCodeFilter("GCMS1;GCMS2;GCMS3", op="in_list"))
    with get_session() as s:
        codes = [c.gc_code for c in apply_filters(s, fs, SortSpec("macro_sort"))]
    assert codes == ["GCMS2", "GCMS3", "GCMS1"]          # empty first, then case-insensitive


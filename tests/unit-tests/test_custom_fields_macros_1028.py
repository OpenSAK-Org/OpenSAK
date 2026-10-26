"""
tests/unit-tests/test_custom_fields_macros_1028.py — custom fields for Lua
macros (#1028 step 5, #1149 phase 6): the `custom` table of a cache,
opensak.update/insert{ custom = … } with the write approval and inside
transactions, opensak.custom_fields(), define_custom_field{} and
delete_custom_field().
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opensak.db import companion, custom_fields
from opensak.db.database import session_for
from opensak.db.manager import get_db_manager
from opensak.db.models import Cache
from opensak.export.file_export import active_database_name
from opensak.macro import FolderPermission, MacroError, MacroRuntime, WriteApproval, db_access
from opensak.utils import flags


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(flags, "companion_db", True)
    monkeypatch.setattr(flags, "lua_macro_write", True)
    companion.reset()
    custom_fields.changed()
    db_access.reset_session()
    yield
    companion.reset()
    db_access.reset_session()


class Host:
    def __init__(self, approve: WriteApproval = WriteApproval.SESSION):
        self.asked = 0
        self.changed: list[str] = []
        self._approve = approve

    def database_name(self):
        return active_database_name()

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def apply_filter(self, filterset, label):
        return 0

    def approve_macro_write(self, macro, change=None, editing=False):
        self.asked += 1
        return self._approve

    def caches_changed(self, codes, added):
        self.changed.extend(codes)

    def caches_removed(self):
        pass

    def end_macro(self):
        pass


@pytest.fixture
def active() -> Path:
    """The active database "Macros" with GC1..GC3 and the fields Angel and GCFinds."""
    manager = get_db_manager()
    db = manager.new_database("Macros")
    manager.switch_to(db)
    with session_for(db.path) as s:
        for code in ("GC1", "GC2", "GC3"):
            s.add(Cache(gc_code=code, name=f"Cache {code}", cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0))
    path = Path(db.path)
    custom_fields.define_field(path, "Angel", "boolean", default="0")
    custom_fields.define_field(path, "GCFinds", "integer", default="-1")
    custom_fields.write_values(path, [("GC1", {"Angel": 1, "GCFinds": 12})])
    return path


def _run(source, folder, host=None):
    out: list[str] = []
    host = host or Host()
    MacroRuntime(
        host, output=out.append,
        folder_permissions=[FolderPermission(str(folder), read=True, write=True)],
    ).run(source, chunk_name="custom.lua", base_dir=folder)
    return host, out


def _values(path: Path, code: str) -> dict:
    return custom_fields.read_values(path, [code])[code]


# ── Reading ──────────────────────────────────────────────────────────────────

def test_cache_has_its_custom_fields_with_defaults(active, tmp_path):
    _, out = _run("""
        local a, b = opensak.cache("GC1"), opensak.cache("GC2")
        print(a.custom.Angel, a.custom.GCFinds, b.custom.Angel, b.custom.GCFinds)
        print(math.type(a.custom.GCFinds))
    """, tmp_path)
    assert out == ["true\t12\tfalse\t-1", "integer"]


def test_caches_can_ask_for_custom_only(active, tmp_path):
    _, out = _run("""
        for c in opensak.caches{ all = true, fields = {"custom"} } do
            print(c.code, c.name, c.custom.Angel)
        end
    """, tmp_path)
    assert sorted(out) == ["GC1\tnil\ttrue", "GC2\tnil\tfalse", "GC3\tnil\tfalse"]


def test_custom_fields_lists_the_definitions(active, tmp_path):
    _, out = _run("""
        for _, f in ipairs(opensak.custom_fields()) do
            print(f.name, f.type, tostring(f.default), f.scope, f.origin)
        end
    """, tmp_path)
    assert out == ["Angel\tboolean\tfalse\tlocal\topensak",
                   "GCFinds\tinteger\t-1\tlocal\topensak"]


# ── Writing ──────────────────────────────────────────────────────────────────

def test_update_writes_custom_fields_with_approval(active, tmp_path):
    host, out = _run("""
        print(opensak.update("GC2", { custom = { angel = true, GCFinds = "7" } }))
        print(opensak.update("GC2", { user_flag = true, custom = { GCFinds = false } }))
        print(opensak.update("GC9", { custom = { Angel = true } }))
    """, tmp_path)
    assert out == ["true", "true", "false"]
    assert host.asked == 1
    assert "GC2" in host.changed
    assert _values(active, "GC2") == {"Angel": 1, "GCFinds": -1}    # false: default again
    with session_for(active) as s:
        assert s.query(Cache).filter_by(gc_code="GC2").one().user_flag


@pytest.mark.parametrize("update, message", [
    ('{ custom = { Nope = 1 } }', "no custom field 'Nope'"),
    ('{ custom = { GCFinds = "many" } }', "GCFinds must be a whole number"),
    ('{ custom = { Angel = "maybe" } }', "Angel must be true or false"),
    ('{ custom = 5 }', "custom must be a table"),
])
def test_update_refuses_bad_custom_values(active, tmp_path, update, message):
    with pytest.raises(MacroError) as info:
        _run(f'opensak.update("GC2", {update})', tmp_path)
    assert message in str(info.value)
    assert _values(active, "GC2") == {"Angel": 0, "GCFinds": -1}


def test_refused_approval_writes_nothing(active, tmp_path):
    with pytest.raises(MacroError):
        _run('opensak.update("GC2", { custom = { Angel = true } })', tmp_path,
             Host(WriteApproval.DENY))
    assert _values(active, "GC2")["Angel"] == 0


def test_transaction_writes_custom_fields_on_commit_only(active, tmp_path):
    _run("""
        opensak.transaction(function()
            opensak.update("GC2", { custom = { Angel = true } })
            opensak.transaction(function()
                opensak.update("GC3", { custom = { GCFinds = 5 } })
            end)
        end)
        pcall(opensak.transaction, function()
            opensak.update("GC1", { custom = { Angel = false } })
            error("roll back")
        end)
    """, tmp_path)
    assert _values(active, "GC2")["Angel"] == 1
    assert _values(active, "GC3")["GCFinds"] == 5
    assert _values(active, "GC1")["Angel"] == 1          # rolled back


def test_insert_with_custom_fields(active, tmp_path):
    _run("""
        opensak.insert{ code = "GC77", name = "New", type = "Traditional",
                        lat = 47.1, lon = 8.1, custom = { Angel = true } }
    """, tmp_path)
    assert _values(active, "GC77")["Angel"] == 1


def test_sql_cannot_write_custom_values(active, tmp_path):
    with pytest.raises(MacroError):
        _run('opensak.sql_write("UPDATE companion.custom_values SET Angel = 0")', tmp_path)
    assert _values(active, "GC1")["Angel"] == 1


# ── Defining ─────────────────────────────────────────────────────────────────

def test_define_is_idempotent_and_checks_the_type(active, tmp_path):
    host, out = _run("""
        local f = opensak.define_custom_field{ name = "Parking", type = "string", default = "-" }
        print(f.name, f.type, f.default)
        opensak.define_custom_field{ name = "parking", type = "string" }
        opensak.update("GC1", { custom = { Parking = "Lot A" } })
        print(opensak.cache("GC1").custom.Parking, opensak.cache("GC2").custom.Parking)
        print(pcall(opensak.define_custom_field, { name = "Parking", type = "integer" }))
    """, tmp_path)
    assert out[0] == "opensak.define_custom_field: defined the local custom field Parking (string)"
    assert out[1] == "Parking\tstring\t-"
    assert out[2] == "Lot A\t-"
    assert "exists already as string" in out[3]
    assert [f.name for f in custom_fields.fields(active)] == ["Angel", "GCFinds", "Parking"]


@pytest.mark.parametrize("spec, message", [
    ('{ name = "gc_code", type = "string" }', "reserved"),
    ('{ name = "X", type = "blob" }', "`type` must be one of"),
    ('{ name = "X", type = "string", scope = "all" }', "`scope` must be"),
    ('{ name = "X", type = "integer", default = "ten" }', "X must be a whole number"),
    ('{ name = "X", type = "string", colour = "red" }', "unknown key"),
    ('{ name = "X", type = "integer", expression = "nope(X)" }', "display expression doesn't work"),
])
def test_define_refuses_bad_definitions(active, tmp_path, spec, message):
    with pytest.raises(MacroError) as info:
        _run(f"opensak.define_custom_field{spec}", tmp_path)
    assert message in str(info.value)


def test_delete_keeps_the_values(active, tmp_path):
    _, out = _run("""
        print(opensak.delete_custom_field("angel"), opensak.delete_custom_field("Nope"))
        print(opensak.cache("GC1").custom.Angel)
    """, tmp_path)
    assert out[1] == "true\tfalse"
    assert out[2] == "nil"
    stored = {f.name: f for f in custom_fields.stored_fields(active, include_orphaned=True)}
    assert stored["Angel"].orphaned


# ── Without the feature flag ─────────────────────────────────────────────────

def test_without_the_flag_there_are_no_custom_fields(active, tmp_path, monkeypatch):
    monkeypatch.setattr(flags, "companion_db", False)
    _, out = _run("""
        print(opensak.cache("GC1").custom, opensak.custom_fields, opensak.define_custom_field)
    """, tmp_path)
    assert out == ["nil\tnil\tnil"]
    with pytest.raises(MacroError) as info:
        _run('opensak.update("GC1", { custom = { Angel = false } })', tmp_path)
    assert "companion-db" in str(info.value)


def test_define_with_a_display_expression(active, tmp_path):
    _run("""
        opensak.define_custom_field{ name = "Finds", type = "integer", default = -1,
                                     expression = "nullif(Finds, -1)" }
        opensak.update("GC1", { custom = { Finds = 4 } })
    """, tmp_path)
    values = custom_fields.active_values(["Finds"])
    assert values.display("GC1", "Finds") == 4 and values.display("GC2", "Finds") is None
    assert values.raw("GC2", "Finds") == -1

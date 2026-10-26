"""
tests/unit-tests/test_custom_fields_1028.py — custom fields in the companion
database (#1028 step 3, #1034 phase 4): definitions, local and global fields,
applying definitions when a database is opened, the caches_all TEMP view,
the runtime-built model, rows following their caches, and the GSAK import
of CustomLocal / CustomGlobal / Custom.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text

from opensak.db import companion, custom_fields
from opensak.db.cache_delete import delete_caches
from opensak.db.custom_fields import CustomFieldError, Field
from opensak.db.database import dispose_engine, get_session, init_db
from opensak.db.db_settings import ensure_seeded
from opensak.db.models import Cache
from opensak.db.transfer import transfer_caches
from opensak.importer.gsak_importer import import_gsak_db
from opensak.lang import load_language
from opensak.utils import flags

# Reuse the synthetic GSAK-schema builder of the main importer tests (the
# importlib import mode doesn't let test modules import each other by name).
_spec = importlib.util.spec_from_file_location(
    "_gsak_importer_tests", Path(__file__).with_name("test_gsak_importer.py")
)
assert _spec is not None and _spec.loader is not None
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
_make_gsak_db = _helpers._make_gsak_db


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    load_language("en")
    monkeypatch.setattr(flags, "companion_db", True)
    companion.reset()
    custom_fields.changed()
    yield
    dispose_engine()
    companion.reset()


def _database(path: Path, codes=("GC1", "GC2", "GC3")) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path=path)
    with get_session() as s:
        for code in codes:
            s.add(Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0))
    ensure_seeded(path, path.stem)
    return path


def _view(sql: str = "SELECT * FROM caches_all ORDER BY gc_code") -> list[dict]:
    with get_session() as s:
        return [dict(r._mapping) for r in s.execute(text(sql))]


def _columns(db_path: Path) -> list[str]:
    conn = sqlite3.connect(str(companion.companion_path(db_path)))
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(custom_values)")]
    finally:
        conn.close()


# ── Names and types ──────────────────────────────────────────────────────────

def test_names_that_clash_get_a_suffix():
    assert custom_fields.rename_for_import("Parking") == "Parking"
    assert custom_fields.rename_for_import("Name") == "Name_custom"          # caches.name
    assert custom_fields.rename_for_import("Select") == "Select_custom"      # keyword
    assert custom_fields.rename_for_import("Name", ["name_custom"]) == "Name_custom2"
    assert custom_fields.rename_for_import("My Field!") == "My_Field_"
    with pytest.raises(CustomFieldError):
        custom_fields.check_name("gc_code")
    with pytest.raises(CustomFieldError):
        custom_fields.check_name("1abc")


def test_types_and_conversion():
    assert custom_fields.normalize_type("Decimal") == "real"      # #126
    assert custom_fields.normalize_type("string") == "string"     # GSAK's lower case
    assert custom_fields.normalize_type("Blob") is None
    assert custom_fields.convert("boolean", "True") == (1, True)
    assert custom_fields.convert("integer", "12") == (12, True)
    assert custom_fields.convert("integer", "abc") == ("abc", False)
    assert custom_fields.convert("real", "1.5") == (1.5, True)


# ── Local fields, values, the view ───────────────────────────────────────────

def test_define_local_field_creates_companion_and_column(tmp_path):
    db = _database(tmp_path / "Found.db")
    assert not companion.exists(db)
    field = custom_fields.define_field(db, "Parking", "string", default="")
    assert companion.exists(db)
    assert "Parking" in _columns(db)
    assert [f.name for f in custom_fields.stored_fields(db)] == ["Parking"]
    conn = sqlite3.connect(str(companion.companion_path(db)))
    registry = dict(conn.execute("SELECT name, kind FROM opensak_tables").fetchall())
    conn.close()
    assert registry == {"custom_fields": "free", "custom_values": "cache"}
    with pytest.raises(CustomFieldError):
        custom_fields.define_field(db, "parking", "integer")
    assert field.scope == "local"


def test_view_shows_defaults_for_untouched_caches(tmp_path):
    db = _database(tmp_path / "Found.db")
    custom_fields.define_field(db, "Angel", "boolean", default="0")
    custom_fields.define_field(db, "FindNum", "integer", default="-1")
    custom_fields.write_values(db, [("GC1", {"Angel": 1, "FindNum": 150})])
    rows = {r["gc_code"]: r for r in _view()}
    assert (rows["GC1"]["Angel"], rows["GC1"]["FindNum"]) == (1, 150)
    assert (rows["GC2"]["Angel"], rows["GC2"]["FindNum"]) == (0, -1)
    # A GSAK clause runs unchanged.
    assert [r["gc_code"] for r in _view(
        "SELECT gc_code FROM caches_all WHERE Angel = 0 ORDER BY gc_code")] == ["GC2", "GC3"]
    assert custom_fields.read_values(db, ["GC1", "GC3"]) == {
        "GC1": {"Angel": 1, "FindNum": 150}, "GC3": {"Angel": 0, "FindNum": -1}}


def test_view_follows_definition_changes(tmp_path):
    db = _database(tmp_path / "Found.db")
    custom_fields.define_field(db, "Parking", "string")
    assert "Parking" in _view()[0]
    field = custom_fields.define_field(db, "Place", "integer", default="0")
    custom_fields.write_values(db, [("GC2", {"Place": 7})])
    custom_fields.rename_field(db, field.field_id, "Rank")
    row = _view("SELECT * FROM caches_all WHERE gc_code = 'GC2'")[0]
    assert row["Rank"] == 7 and "Place" not in row


def test_write_values_updates_only_named_fields(tmp_path):
    db = _database(tmp_path / "Found.db")
    custom_fields.define_field(db, "A", "integer")
    custom_fields.define_field(db, "B", "string")
    custom_fields.write_values(db, [("GC1", {"A": 1, "B": "x"})])
    custom_fields.write_values(db, [("GC1", {"A": 2})])
    assert custom_fields.read_values(db, ["GC1"])["GC1"] == {"A": 2, "B": "x"}
    with pytest.raises(CustomFieldError):
        custom_fields.write_values(db, [("GC1", {"Nope": 1})])


def test_delete_keeps_column_drop_removes_it(tmp_path):
    db = _database(tmp_path / "Found.db")
    field = custom_fields.define_field(db, "Old", "string")
    custom_fields.write_values(db, [("GC1", {"Old": "kept"})])
    custom_fields.delete_field(db, field.field_id)
    assert custom_fields.fields(db) == []
    assert "Old" in _columns(db)
    assert "Old" not in _view()[0]
    custom_fields.drop_field(db, field.field_id)
    assert "Old" not in _columns(db)
    assert custom_fields.stored_fields(db, include_orphaned=True) == []


def test_change_type_converts_values(tmp_path):
    db = _database(tmp_path / "Found.db")
    field = custom_fields.define_field(db, "Count", "string")
    custom_fields.write_values(db, [("GC1", {"Count": "12"}), ("GC2", {"Count": "lots"})])
    custom_fields.change_type(db, field.field_id, "integer")
    values = custom_fields.read_values(db, ["GC1", "GC2"])
    assert values["GC1"]["Count"] == 12
    assert values["GC2"]["Count"] == 0      # not convertible → NULL → the default


def test_values_model_reads_and_writes_in_a_session(tmp_path):
    db = _database(tmp_path / "Found.db")
    custom_fields.define_field(db, "Parking", "string")
    custom_fields.define_field(db, "Angel", "boolean")
    model = custom_fields.values_model(db)
    assert model is not None and [f.name for f in model.fields] == ["Parking", "Angel"]
    with get_session() as s:
        s.add(model.cls(gc_code="GC3", Parking="P1", Angel=True))
    with get_session() as s:
        row = s.get(model.cls, "GC3")
        assert (row.Parking, row.Angel) == ("P1", True)
    custom_fields.define_field(db, "Extra", "real")
    assert custom_fields.values_model(db) is not model      # rebuilt


# ── Global fields ────────────────────────────────────────────────────────────

def test_global_field_is_lazy(tmp_path):
    custom_fields.define_global(Field(custom_fields.new_field_id(), "Keep", "boolean",
                                      default="0", scope="global"))
    db = _database(tmp_path / "Found.db")
    assert not companion.exists(db)                         # no companion just for it
    assert [r["Keep"] for r in _view()] == [0, 0, 0]        # default as a literal
    custom_fields.write_values(db, [("GC1", {"Keep": 1})])  # first value stores it
    assert companion.exists(db)
    assert [f.scope for f in custom_fields.stored_fields(db)] == ["global"]
    assert [r["Keep"] for r in _view()] == [1, 0, 0]


def test_global_rename_and_delete_follow_on_open(tmp_path):
    db = _database(tmp_path / "Found.db")
    field = custom_fields.define_field(db, "ToSolve", "boolean", scope="global")
    custom_fields.write_values(db, [("GC1", {"ToSolve": 1})])
    dispose_engine(db)
    # Renamed in the master while the database is closed (another database).
    custom_fields.define_global(Field(field.field_id, "Puzzle", "boolean", scope="global"))
    companion.reset()
    init_db(db_path=db)
    assert "Puzzle" in _columns(db) and "ToSolve" not in _columns(db)
    assert custom_fields.read_values(db, ["GC1"])["GC1"]["Puzzle"] == 1
    dispose_engine(db)
    # Deleted from the master: orphaned here, values kept.
    other = _database(tmp_path / "Other.db")
    custom_fields.delete_field(other, field.field_id)
    dispose_engine(other)
    companion.reset()
    init_db(db_path=db)
    assert custom_fields.fields(db) == []
    assert "Puzzle" in _columns(db)


def test_global_type_change_waits_for_confirmation(tmp_path):
    db = _database(tmp_path / "Found.db")
    field = custom_fields.define_field(db, "Score", "string", scope="global")
    custom_fields.define_global(Field(field.field_id, "Score", "integer", scope="global"))
    result = custom_fields.apply_definitions(db)
    assert [(p.name, p.old_type, p.new_type) for p in result.pending] == [
        ("Score", "string", "integer")]
    assert custom_fields.stored_fields(db)[0].type == "string"


# ── Rows follow their caches ─────────────────────────────────────────────────

def test_deleting_caches_deletes_their_values(tmp_path):
    db = _database(tmp_path / "Found.db")
    custom_fields.define_field(db, "Parking", "string")
    custom_fields.write_values(db, [("GC1", {"Parking": "a"}), ("GC2", {"Parking": "b"})])
    with get_session() as s:
        delete_caches(s, ["GC1"])
    assert set(custom_fields.read_values(db)) == {"GC2"}


def test_copying_caches_creates_missing_fields_in_target(tmp_path):
    source = _database(tmp_path / "Source.db")
    custom_fields.define_field(source, "Parking", "string")
    custom_fields.define_field(source, "FindNum", "integer", default="-1")
    custom_fields.write_values(source, [("GC1", {"Parking": "P", "FindNum": 3})])
    dispose_engine(source)
    target = _database(tmp_path / "Target.db", codes=())
    custom_fields.define_field(target, "Own", "string")
    dispose_engine(target)
    transfer_caches(["GC1", "GC2"], source, target, copy_only=True)
    assert {f.name for f in custom_fields.stored_fields(target)} == {"Own", "Parking", "FindNum"}
    assert custom_fields.read_values(target, ["GC1"])["GC1"] == {
        "Own": "", "Parking": "P", "FindNum": 3}


# ── GSAK import ──────────────────────────────────────────────────────────────

def _make_gsak(path: Path, codes=("GC1", "GC2")) -> Path:
    db = _make_gsak_db(path, caches=[{"Code": c, "Name": c} for c in codes])
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE CustomLocal (fname text primary key, ftype text, fsequence integer,
            fdefault text, fcontrol text, fheight integer, fexpression text);
        INSERT INTO CustomLocal VALUES
            ('GCFinds', 'Integer', 110, '-1', '', 0, 'nullif(GCFinds,-1)'),
            ('Name', 'string', 20, '', 'Standard', 0, '');
        CREATE TABLE Custom (cCode text primary key default '' not null,
            GCFinds Integer default -1 not null, Name text default '' not null,
            Angel integer default 0 not null);
        INSERT INTO Custom VALUES ('GC1', 120, 'Hello', 1), ('GC2', -1, '', 0),
            ('GCNOTHERE', 5, 'x', 1);
    """)
    conn.commit()
    conn.close()
    return db


def _make_settings(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE CustomGlobal (fname text primary key, ftype text, fsequence integer,
            fdefault text, fcontrol text, fheight integer, fexpression text);
        INSERT INTO CustomGlobal VALUES ('Angel', 'Boolean', 150, '0', '', 0, '');
        CREATE TABLE CustomDefs (fname, ftype, fscope, fdb);
        INSERT INTO CustomDefs VALUES ('Angel', 'Boolean', 'Global', '');
    """)
    conn.commit()
    conn.close()
    return path


def test_gsak_import_local_and_global_fields(tmp_path):
    gsak = _make_gsak(tmp_path / "sqlite.db3")
    settings = _make_settings(tmp_path / "gsak.db3")
    db = tmp_path / "Found.db"
    init_db(db_path=db)
    ensure_seeded(db, "Found")
    with get_session() as s:
        result = import_gsak_db(gsak, s, settings_db=settings)
    cf = result.custom_fields
    assert cf is not None
    assert sorted(cf.created) == ["Angel", "GCFinds", "Name_custom"]
    assert cf.global_fields == ["Angel"]
    assert cf.renamed == [("Name", "Name_custom")]
    assert cf.values == 2                                   # GCNOTHERE isn't a cache
    stored = {f.name: f for f in custom_fields.stored_fields(db)}
    assert stored["GCFinds"].expression == "nullif(GCFinds,-1)"
    assert stored["GCFinds"].origin == "gsak"
    assert stored["Angel"].scope == "global"
    assert [g.name for g in custom_fields.global_fields()] == ["Angel"]
    rows = {r["gc_code"]: r for r in _view()}
    assert (rows["GC1"]["GCFinds"], rows["GC1"]["Name_custom"], rows["GC1"]["Angel"]) == (
        120, "Hello", 1)
    # Re-import reuses the fields.
    with get_session() as s:
        again = import_gsak_db(gsak, s, settings_db=settings).custom_fields
    assert again is not None and again.created == [] and len(again.reused) == 3


def test_gsak_import_without_settings_imports_globals_as_local(tmp_path):
    gsak = _make_gsak(tmp_path / "sqlite.db3")
    db = tmp_path / "Found.db"
    init_db(db_path=db)
    ensure_seeded(db, "Found")
    with get_session() as s:
        result = import_gsak_db(gsak, s)
    cf = result.custom_fields
    assert cf is not None and cf.global_fields == []
    angel = next(f for f in custom_fields.stored_fields(db) if f.name == "Angel")
    assert (angel.scope, angel.type, angel.default) == ("local", "integer", "0")
    assert any("gsak.db3 not available" in m for m in result.warnings)


def test_gsak_import_without_flag_leaves_custom_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(flags, "companion_db", False)
    gsak = _make_gsak(tmp_path / "sqlite.db3")
    db = tmp_path / "Found.db"
    init_db(db_path=db)
    with get_session() as s:
        result = import_gsak_db(gsak, s)
    assert result.custom_fields is None
    assert not companion.exists(db)


def test_rebuilding_caches_works_with_the_view(tmp_path):
    """A TEMP view on caches makes SQLite refuse the table rebuild of the
    migrations; the view is dropped first and made again afterwards."""
    from opensak.db.database import _add_column_defaults, get_engine
    db = _database(tmp_path / "Found.db")
    custom_fields.define_field(db, "Parking", "string")
    assert "Parking" in _view()[0]
    with get_engine().connect() as c:
        _add_column_defaults(c, {"caches": {"symbol": "'x'"}})
        c.commit()
    assert "Parking" in _view()[0]

"""
tests/unit-tests/test_custom_fields_ui_1028.py — custom fields in the GUI and
in filters (#1028 steps 4–5, #1149 phase 6): values with their display
expression, the custom-field filter (the Filter dialog's Custom tab), Where
clauses on caches_all, and the GSAK filter importer's Custom tab and
custom-field names.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from opensak.db import companion, custom_fields
from opensak.db.custom_fields import CustomFieldError
from opensak.db.database import dispose_engine, get_session, init_db
from opensak.db.db_settings import ensure_seeded
from opensak.db.models import Cache
from opensak.filters.engine import (
    CustomFieldFilter, FilterSet, NameFilter, WhereClauseFilter,
    apply_filters, apply_filters_lightweight, validate_where_sql,
)
from opensak.importer.gsak_filter_importer import (
    COMMENT, NATIVE, SQL, Options, convert, parse_filter_blob, translate_where,
)
from opensak.lang import load_language
from opensak.utils import flags


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
            s.add(Cache(gc_code=code, name=f"Cache {code}", cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0))
    ensure_seeded(path, path.stem)
    return path


@pytest.fixture
def db(tmp_path) -> Path:
    path = _database(tmp_path / "Found.db")
    custom_fields.define_field(path, "Angel", "boolean", default="0")
    custom_fields.define_field(path, "GCFinds", "integer", default="-1",
                               expression="nullif(GCFinds,-1)")
    custom_fields.define_field(path, "Parking", "string", default="")
    custom_fields.define_field(path, "Visited", "date")
    custom_fields.write_values(path, [
        ("GC1", {"Angel": 1, "GCFinds": 12, "Parking": "Lot A", "Visited": "2024-05-01"}),
        ("GC2", {"GCFinds": 3, "Parking": "Zürich Bahnhof"}),
    ])
    return path


def _codes(fs: FilterSet, lightweight: bool = False) -> list[str]:
    with get_session() as s:
        run = apply_filters_lightweight if lightweight else apply_filters
        return sorted(c.gc_code for c in run(s, fs))


def _fs(*filters, mode="AND") -> FilterSet:
    fs = FilterSet(mode=mode)
    for f in filters:
        fs.add(f)
    return fs


# ── Values for the cache list and the detail panel ───────────────────────────

def test_load_values_has_defaults_and_display_values(db):
    values = custom_fields.active_values()
    assert [f.name for f in values.fields] == ["Angel", "GCFinds", "Parking", "Visited"]
    assert values.raw("GC1", "Angel") == 1
    assert values.raw("GC3", "Angel") == 0                # untouched: the default
    assert values.raw("GC3", "GCFinds") == -1
    assert values.display("GC3", "GCFinds") is None       # nullif(GCFinds,-1)
    assert values.display("GC1", "GCFinds") == 12
    assert values.display("GC2", "parking") == "Zürich Bahnhof"   # names: any case


def test_load_values_for_some_codes_and_fields(db):
    values = custom_fields.active_values(["parking"], ["GC2", "GC9"])
    assert [f.name for f in values.fields] == ["Parking"]
    assert set(values.rows) == {"GC2"}
    assert values.raw("GC2", "GCFinds") is None           # not asked for


def test_gsak_expression_is_translated_for_display(db):
    custom_fields.define_field(db, "Apprec", "real", default="0.0", origin="gsak",
                               expression="CASE Apprec WHEN 0.0 THEN '' "
                                          "ELSE printf('%.1f%%', Apprec) END")
    custom_fields.write_values(db, [("GC1", {"Apprec": 12.5})])
    values = custom_fields.active_values(["Apprec"])
    assert values.display("GC1", "Apprec") == "12.5%"
    assert values.display("GC2", "Apprec") == ""
    assert values.raw("GC2", "Apprec") == 0.0


def test_a_failing_expression_shows_the_stored_value(db):
    custom_fields.define_field(db, "Broken", "integer", default="5",
                               expression="no_such_function(Broken)")
    values = custom_fields.active_values(["Broken"])
    assert values.display("GC1", "Broken") == 5


def test_expressions_cannot_write(db):
    with get_session() as s:
        dbapi = s.connection().connection.dbapi_connection
        with custom_fields.read_only(dbapi):
            with pytest.raises(Exception):
                dbapi.execute("DELETE FROM caches")
        assert dbapi.execute("SELECT count(*) FROM caches").fetchone()[0] == 3


def test_parse_value_checks_the_type(db):
    by_name = {f.name: f for f in custom_fields.fields(db)}
    assert custom_fields.parse_value(by_name["Angel"], True) == 1
    assert custom_fields.parse_value(by_name["GCFinds"], "12") == 12
    assert custom_fields.parse_value(by_name["GCFinds"], "") is None
    assert custom_fields.parse_value(by_name["Visited"], date(2024, 5, 1)) == "2024-05-01"
    assert custom_fields.parse_value(by_name["Parking"], "") == ""
    for name, bad in (("GCFinds", "many"), ("GCFinds", True), ("Visited", "1st May"),
                      ("Angel", "maybe"), ("Parking", 12)):
        with pytest.raises(CustomFieldError):
            custom_fields.parse_value(by_name[name], bad)


# ── The custom-field filter ──────────────────────────────────────────────────

@pytest.mark.parametrize("lightweight", [False, True])
def test_boolean_filter_matches_untouched_caches_by_default(db, lightweight):
    assert _codes(_fs(CustomFieldFilter("Angel", "boolean", "yes")), lightweight) == ["GC1"]
    assert _codes(_fs(CustomFieldFilter("angel", "boolean", "no")), lightweight) == ["GC2", "GC3"]


def test_number_filter(db):
    assert _codes(_fs(CustomFieldFilter("GCFinds", "integer", "at_least", value1=3))) \
        == ["GC1", "GC2"]
    assert _codes(_fs(CustomFieldFilter("GCFinds", "integer", "equal", value1=-1))) == ["GC3"]
    assert _codes(_fs(CustomFieldFilter("GCFinds", "integer", "between",
                                        value1=10, value2=2))) == ["GC2"]


def test_text_filter_with_sql_and_python_operators(db):
    assert _codes(_fs(CustomFieldFilter("Parking", "string", "contains", text="lot"))) == ["GC1"]
    assert _codes(_fs(CustomFieldFilter("Parking", "string", "empty"))) == ["GC3"]
    # Non-ASCII and regex go through Python
    assert _codes(_fs(CustomFieldFilter("Parking", "string", "contains", text="zürich"))) \
        == ["GC2"]
    assert _codes(_fs(CustomFieldFilter("Parking", "string", "regex", text=r"^lot\s"))) == ["GC1"]


def test_date_filter(db):
    assert _codes(_fs(CustomFieldFilter("Visited", "date", "equal", date1=date(2024, 5, 1)))) \
        == ["GC1"]
    assert _codes(_fs(CustomFieldFilter("Visited", "date", "during", amount=1, unit="weeks"))) \
        == []
    assert _codes(_fs(CustomFieldFilter("Visited", "date", "not_during",
                                        amount=1, unit="weeks"))) == ["GC1", "GC2", "GC3"]
    custom_fields.write_values(db, [("GC3", {"Visited": (date.today() - timedelta(days=2))
                                             .isoformat()})])
    assert _codes(_fs(CustomFieldFilter("Visited", "date", "during", amount=1, unit="weeks"))) \
        == ["GC3"]


def test_filter_inside_or_and_negated_sets(db):
    either = _fs(CustomFieldFilter("Angel", "boolean", "yes"),
                 NameFilter("GC2", "contains"), mode="OR")
    assert _codes(either) == ["GC1", "GC2"]
    inverted = _fs(CustomFieldFilter("Angel", "boolean", "yes"))
    inverted.negate = True
    assert _codes(inverted, lightweight=True) == ["GC2", "GC3"]


def test_unknown_field_matches_nothing(db):
    assert _codes(_fs(CustomFieldFilter("Nope", "boolean", "yes"))) == []


def test_filter_round_trips_through_a_profile(db):
    for f in (CustomFieldFilter("Angel", "boolean", "no"),
              CustomFieldFilter("Parking", "string", "starts_with", text="Lot"),
              CustomFieldFilter("GCFinds", "integer", "not_between", value1=1, value2=5),
              CustomFieldFilter("Visited", "date", "between",
                                date1=date(2024, 1, 1), date2=date(2024, 12, 31))):
        again = FilterSet.from_dict(_fs(f).to_dict())
        assert again.to_dict() == _fs(f).to_dict()
        assert _codes(again) == _codes(_fs(f))


def test_bad_operator_for_type_is_refused():
    with pytest.raises(ValueError):
        CustomFieldFilter("Angel", "boolean", "contains")
    with pytest.raises(ValueError):
        CustomFieldFilter("Visited", "date", "equal")       # needs date1


# ── Where clauses on caches_all ──────────────────────────────────────────────

def test_where_clause_uses_custom_fields_by_name(db):
    assert _codes(_fs(WhereClauseFilter("Angel = 1 OR GCFinds = 3"))) == ["GC1", "GC2"]
    # Sub-selects referring to caches.id keep working
    assert _codes(_fs(WhereClauseFilter(
        "NOT EXISTS (SELECT 1 FROM logs WHERE logs.cache_id = caches.id) AND Angel = 0"
    ))) == ["GC2", "GC3"]
    with get_session() as s:
        assert validate_where_sql(s, "Parking <> ''") is None
        assert validate_where_sql(s, "NoSuchField = 1") is not None


def test_where_clause_without_custom_fields_runs_on_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(flags, "companion_db", False)
    _database(tmp_path / "Plain.db")
    assert _codes(_fs(WhereClauseFilter("gc_code = 'GC2'"))) == ["GC2"]


# ── GSAK filter importer ─────────────────────────────────────────────────────

_CUSTOM_OPTS = Options(custom_fields={"Angel": "boolean", "GCFinds": "integer",
                                      "Parking": "string", "LabID": "string",
                                      "Name_custom": "string"})


def _convert(custom: list[str] | None = None, where: str = "",
             opts: Options = _CUSTOM_OPTS):
    lines = []
    if custom:
        lines += ["*custom*", *custom]
    text = "\r\n".join(lines)
    if where:
        text += "\r\n*where*=" + where
    return convert(parse_filter_blob("T", text), opts)


def test_gsak_custom_tab_becomes_native_filters():
    c = _convert([
        "Angel;bool;Ja/Richtig oder Nein/Falsch;1;0",
        "GCFinds;integer;Less than or equal to;10;",
        "Parking;string;Contains;Lot;",
        "Both;bool;;1;1",                          # sets nothing
    ])
    assert [cr.status for cr in c.criteria] == [NATIVE, NATIVE, NATIVE]
    by_field = {f.field: f for f in c.filters}
    assert by_field["Angel"].op == "yes"
    assert (by_field["GCFinds"].op, by_field["GCFinds"].value1) == ("at_most", 10.0)
    assert (by_field["Parking"].op, by_field["Parking"].text) == ("contains", "Lot")


def test_gsak_custom_tab_german_and_unknown_operators():
    c = _convert(["GCFinds;integer;Größer oder gleich;5;",
                  "GCFinds;integer;Ungleich;7;",
                  "GCFinds;integer;Irgendwas;7;"])
    assert [cr.status for cr in c.criteria] == [NATIVE, NATIVE, COMMENT]
    assert c.filters[0].op == "at_least"
    assert (c.filters[1].op, c.filters[1].value1, c.filters[1].value2) \
        == ("not_between", 7.0, 7.0)


def test_gsak_custom_field_missing_in_the_database_is_kept_with_a_note():
    c = _convert(["ToSolve;bool;;1;0"])
    assert c.criteria[0].status == NATIVE
    assert any("ToSolve" in n and "not defined" in n for n in c.notes)


def test_gsak_custom_tab_without_custom_fields_is_documented_only():
    c = _convert(["Angel;bool;;1;0"], opts=Options())
    assert c.criteria[0].status == COMMENT and c.filters == []


def test_where_uses_known_custom_fields_and_tables():
    sql, warnings, _notes, verified = translate_where(
        "code in (select cCode from custom where Angel = 1) and FavPoints > 10",
        known_fields=["Angel"])
    assert verified, warnings
    assert sql == "gc_code in (select gc_code from caches_all where Angel = 1) " \
                  "and favorite_points > 10"
    sql, _w, _n, verified = translate_where(
        "labid not in (select labid from cachesall where found=1)", known_fields=["LabID"])
    assert verified
    assert sql == "LabID not in (select LabID from caches_all where found=1)"


def test_where_custom_field_renamed_on_import():
    sql, _w, notes, verified = translate_where("Name = 'x'", known_fields=["Name_custom"])
    # GSAK's own Name column wins: a GSAK database can't have a custom field
    # called Name, so only the clashing OpenSAK name is renamed.
    assert verified and sql == "name = 'x'"
    # An SQL keyword as GSAK field name got "_custom" on import.
    sql, _w, notes, verified = translate_where("Plan = 1", known_fields=["Plan_custom"])
    assert verified and sql == "Plan_custom = 1"
    assert any("Plan_custom" in n for n in notes)


def test_where_unknown_custom_field_stays_unverified():
    _sql, warnings, _n, verified = translate_where(
        "MyCol = 1", custom_columns=["MyCol"], known_fields=["Angel"])
    assert not verified
    assert any("MyCol" in w and "doesn't have" in w for w in warnings)


def test_imported_where_clause_runs(db):
    c = _convert(where="code in (select cCode from custom where Angel = 1) or GCFinds = 3",
                 opts=Options(custom_fields={f.name: f.type for f in custom_fields.fields(db)}))
    assert [cr.status for cr in c.criteria] == [SQL]
    assert _codes(c.build_filterset()) == ["GC1", "GC2"]

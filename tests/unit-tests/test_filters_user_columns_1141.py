# tests/unit-tests/test_filters_user_columns_1141.py — the #1139 columns in
# filters (#1141, #1028 step 2b-2): the Dates tab "Last user update", the
# Last user update / GPX symbol columns, and the GSAK filter importer.

import time
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from opensak.db.models import Cache
from opensak.filters.engine import (
    DATE_FILTER_FIELDS,
    DATETIME_FILTER_FIELDS,
    UTC_DATE_FILTER_FIELDS,
    DateFilter,
)
from opensak.importer.gsak_filter_importer import (
    NATIVE,
    SQL,
    Options,
    WHERE_UNMAPPABLE,
    convert,
    parse_filter_blob,
    translate_where,
)


@pytest.fixture
def copenhagen(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset() is not available on this platform")
    monkeypatch.setenv("TZ", "Europe/Copenhagen")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _convert(pairs: dict, where: str = ""):
    text = "\r\n".join(f"{k}={v}" for k, v in pairs.items())
    if where:
        text += "\r\n*where*=" + where
    return convert(parse_filter_blob("T", text), Options())


def _statuses(c) -> dict:
    return {crit.label: crit.status for crit in c.criteria}


# ── Dates tab ─────────────────────────────────────────────────────────────────

class TestDateField:
    def test_registered_as_a_utc_timestamp(self):
        assert DATE_FILTER_FIELDS["last_user_update"] == "last_user_update"
        assert "last_user_update" in DATETIME_FILTER_FIELDS
        assert "last_user_update" in UTC_DATE_FILTER_FIELDS

    def test_on_the_dates_tab(self):
        from opensak.gui.dialogs.filter_dialog import _DATE_FIELD_LABELS
        assert ("last_user_update", "col_last_user_update") in _DATE_FIELD_LABELS

    def test_local_day_in_python_and_sql(self, db_session, copenhagen, make_cache):
        # 23:30 UTC on 14 Sep is 01:30 on 15 Sep in Copenhagen.
        inside = make_cache("GC1IN", last_user_update=datetime(2026, 9, 14, 23, 30))
        outside = make_cache("GC1OUT", last_user_update=datetime(2026, 9, 15, 22, 30))
        never = make_cache("GC1NONE")
        db_session.add_all([inside, outside, never])
        db_session.commit()
        f = DateFilter("last_user_update", "equal", date1=date(2026, 9, 15))
        sql = {c.gc_code for c in f.apply_to_query(db_session.query(Cache)).all()}
        assert sql == {"GC1IN"}
        assert {c.gc_code for c in (inside, outside, never) if f.matches(c)} == sql

    def test_not_during_keeps_caches_never_edited(self, db_session, make_cache):
        db_session.add(make_cache("GC1NONE"))
        db_session.commit()
        f = DateFilter("last_user_update", "not_during", amount=30, unit="days")
        assert f.apply_to_query(db_session.query(Cache)).count() == 1

    def test_round_trips_through_a_profile(self):
        f = DateFilter("last_user_update", "on_or_after", date1=datetime(2026, 9, 15, 8, 30))
        back = DateFilter.from_dict(f.to_dict())
        assert (back.field, back.date1) == ("last_user_update", datetime(2026, 9, 15, 8, 30))


# ── Columns ─────────────────────────────────────────────────────────────────────

class TestColumns:
    def test_in_the_column_chooser(self):
        from opensak.gui.dialogs.column_dialog import _ALL_COLUMNS_DEF
        ids = [c[0] for c in _ALL_COLUMNS_DEF]
        assert "last_user_update" in ids and "symbol" in ids

    def test_in_every_language(self):
        from opensak.lang import AVAILABLE_LANGUAGES
        import importlib
        for code in AVAILABLE_LANGUAGES:
            strings = importlib.import_module(f"opensak.lang.{code}").STRINGS
            assert strings.get("col_last_user_update"), code
            assert strings.get("col_symbol"), code

    def test_display_and_sort(self, monkeypatch, copenhagen, qapp):
        from opensak.gui import cache_table as ct
        from opensak.utils.types import DateFormat
        s = SimpleNamespace(date_format=DateFormat.YMD, use_miles=False,
                            home_lat=None, home_lon=None)
        monkeypatch.setattr(ct, "get_settings", lambda: s)
        monkeypatch.setattr(ct, "_get_active_columns",
                            lambda: ["gc_code", "last_user_update", "symbol"])
        model = ct.CacheTableModel()
        a = Cache(gc_code="GC1A", name="A", symbol="Geocache Found",
                  last_user_update=datetime(2026, 9, 14, 23, 30))
        b = Cache(gc_code="GC1B", name="B")
        assert model._display_value(a, "last_user_update") == "2026-09-15"
        assert model._display_value(a, "symbol") == "Geocache Found"
        assert model._display_value(b, "last_user_update") == ""
        assert model._display_value(b, "symbol") == ""
        model._caches = [a, b]
        model.sort(model._columns.index("last_user_update"))
        assert [c.gc_code for c in model._caches] == ["GC1B", "GC1A"]

    def test_carried_by_the_lightweight_rows(self, db_session, make_cache):
        from opensak.filters.engine import FilterSet, apply_filters_lightweight
        db_session.add(make_cache("GC1L", symbol="Geocache", is_owner=True,
                                  last_user_update=datetime(2026, 1, 1)))
        db_session.commit()
        row = apply_filters_lightweight(db_session, FilterSet())[0]
        assert (row.symbol, row.is_owner, row.last_user_update) == (
            "Geocache", True, datetime(2026, 1, 1))


# ── GSAK filter importer ────────────────────────────────────────────────────────

class TestGsakFilterImporter:
    @pytest.mark.parametrize("gsak, osak", [
        ("IsOwner = 1", "is_owner = 1"),
        ("MacroFlag = 1", "macro_flag = 1"),
        ("MacroSort <> ''", "macro_sort <> ''"),
        ("Symbol = 'Geocache Found'", "symbol = 'Geocache Found'"),
        ("LastUserDate > '2025-01-01'", "last_user_update > '2025-01-01'"),
    ])
    def test_where_columns(self, gsak, osak):
        sql, warnings, _notes, verified = translate_where(gsak)
        assert sql.lower() == osak.lower()   # SQLite column names ignore case
        assert warnings == []
        assert verified

    def test_no_longer_unmappable(self):
        assert "isowner" not in WHERE_UNMAPPABLE
        assert "symbol" not in WHERE_UNMAPPABLE

    def test_is_owner_ignores_the_account_name(self):
        sql, _w, notes, verified = translate_where("IsOwner = 0", me="Someone")
        assert sql == "is_owner = 0" and verified
        assert any("is_owner" in n for n in notes)

    def test_last_user_date_on_the_dates_tab(self):
        c = _convert({"cbxLastUser": "4", "edtLastUserDuring": "30",
                      "cbxLastUserDuring": "0"})
        assert _statuses(c) == {"Last user update": NATIVE}
        f = c.filters[0]
        assert (f.field, f.op, f.amount, f.unit) == ("last_user_update", "during", 30, "days")

    def test_symbol_text_box(self):
        c = _convert({"edtSymbol": "Geocache Found"})
        assert _statuses(c) == {"Symbol name": SQL}
        (label, sql), = c.sql_parts
        assert label == "Symbol name" and "symbol" in sql and "geocache found" in sql.lower()

# tests/unit-tests/test_gsak_where_local_dates_1363.py — #1363: a migrated
# GSAK where clause compares LastGPXDate, Created and LastUserDate as local
# dates, as GSAK does. OpenSAK stores them as naive UTC (#1097): a GSAK date
# imported east of UTC is local midnight, i.e. 22:00/23:00 the day before,
# which shifted every comparison and date difference.

import time
from datetime import datetime

import pytest

from opensak.filters.engine import FilterSet, WhereClauseFilter, apply_filters
from opensak.importer.gsak_filter_importer import translate_where
from opensak.importer.gsak_importer import _utc as _gsak_utc

# GSAK's "Ideas" test: favourite points per day since the cache was placed.
IDEAS = "FavPoints / (JulianDay(LastGPXDate) - JulianDay(PlacedDate)) > 0.5"


@pytest.fixture
def zurich(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset() is not available on this platform")
    monkeypatch.setenv("TZ", "Europe/Zurich")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _where(gsak: str) -> str:
    sql, warnings, _notes, verified = translate_where(gsak)
    assert warnings == [] and verified
    return sql


def _codes(session, gsak: str) -> list[str]:
    fs = FilterSet().add(WhereClauseFilter(_where(gsak)))
    return sorted(c.gc_code for c in apply_filters(session, fs))


class TestTranslation:
    @pytest.mark.parametrize("gsak, osak", [
        ("LastGPXDate >= '2026-11-20'", "date(last_gpx_update, 'localtime') >= '2026-11-20'"),
        ("Created = '2026-11-20'", "date(imported_at, 'localtime') = '2026-11-20'"),
        ("LastUserDate < '2026-11-20'", "date(last_user_update, 'localtime') < '2026-11-20'"),
    ])
    def test_utc_columns_compare_their_local_date(self, gsak, osak):
        assert _where(gsak) == osak

    def test_table_qualifier_is_kept(self):
        assert _where("c.LastGPXDate = '2026-11-20'") == (
            "date(c.last_gpx_update, 'localtime') = '2026-11-20'")

    def test_string_literal_is_left_alone(self):
        assert _where("Name = 'LastGPXDate'") == "name = 'LastGPXDate'"

    def test_calendar_dates_stay_bare(self):
        # Placed and found dates are stored as local calendar dates.
        assert _where("PlacedDate = '2026-11-20'") == "hidden_date = '2026-11-20'"

    def test_note_says_how_it_is_compared(self):
        _sql, _w, notes, _v = translate_where("LastGPXDate = '2026-11-20'")
        assert any("compared as its local date" in n for n in notes)


class TestAgainstTheDatabase:
    def _add(self, session, make_cache, last_gpx: datetime) -> None:
        session.add(make_cache(
            "GCBWC87", favorite_points=48, hidden_date=datetime(2026, 8, 16),
            last_gpx_update=last_gpx, imported_at=last_gpx, last_user_update=last_gpx))
        session.commit()

    def test_gsak_date_matches_as_in_gsak(self, db_session, make_cache):
        # GSAK's LastGPXDate 2026-11-20 as the GSAK importer stores it in
        # this computer's time zone: 48 favourite points in 96 days is 0.5.
        self._add(db_session, make_cache, _gsak_utc("2026-11-20"))
        assert _codes(db_session, IDEAS) == []
        for column in ("LastGPXDate", "Created", "LastUserDate"):
            assert _codes(db_session, f"{column} = '2026-11-20'") == ["GCBWC87"], column

    @pytest.mark.parametrize("stored", [
        datetime(2026, 11, 19, 23, 0),   # 2026-11-20 00:00 in winter, UTC+1
        datetime(2026, 11, 20, 12, 0),
    ])
    def test_utc_timestamp_compared_as_local_date(self, zurich, db_session, make_cache, stored):
        self._add(db_session, make_cache, stored)
        assert _codes(db_session, IDEAS) == []
        assert _codes(db_session, "LastGPXDate >= '2026-11-20'") == ["GCBWC87"]
        assert _codes(db_session, "LastGPXDate < '2026-11-20'") == []

    def test_summer_time(self, zurich, db_session, make_cache):
        self._add(db_session, make_cache, datetime(2026, 8, 19, 22, 0))  # UTC+2
        assert _codes(db_session, "LastGPXDate = '2026-08-20'") == ["GCBWC87"]

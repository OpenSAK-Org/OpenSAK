# tests/unit-tests/test_log_dates_875.py — issue #875.
#
# Log dates arrive as UTC timestamps, not as the date the logger picked, so
# an evening find in the Americas came out a day late (C3GPS: found Aug 8,
# OpenSAK showed Aug 9). log_calendar_date() works the picked date back out:
# fixed website times (on a whole hour) keep their calendar date, 19:00
# Pacific is read in Pacific time, and a real app time is read in local time
# for the user's own logs. found_date / dnf_date are stored as that date;
# the log rows keep the original timestamp.

import functools
import textwrap
from datetime import date, datetime, timedelta, timezone

import pytest

import opensak.importer as importer_mod
from opensak.db.database import get_session, init_db
from opensak.db.models import Cache, Log
from opensak.importer import import_gpx
from opensak.utils.log_dates import (
    _pacific, is_fixed_time, is_own_log, log_calendar_date, log_calendar_midnight,
)
from tests.data import write_gpx

UTC = timezone.utc
US_CENTRAL = timezone(timedelta(hours=-5))   # CDT, as in the August report
DENMARK = timezone(timedelta(hours=2))       # CEST
NZ = timezone(timedelta(hours=12))


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0,
        second: int = 0, microsecond: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, microsecond, tzinfo=UTC)


# ── Pacific time without zoneinfo ────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    # 2007+: DST from the 2nd Sunday of March to the 1st Sunday of November.
    (utc(2026, 3, 8, 9, 59), datetime(2026, 3, 8, 1, 59)),    # PST
    (utc(2026, 3, 8, 10, 0), datetime(2026, 3, 8, 3, 0)),     # PDT starts
    (utc(2026, 11, 1, 8, 59), datetime(2026, 11, 1, 1, 59)),  # still PDT
    (utc(2026, 11, 1, 9, 0), datetime(2026, 11, 1, 1, 0)),    # PST again
    (utc(2026, 8, 9, 2, 0), datetime(2026, 8, 8, 19, 0)),
    (utc(2026, 1, 15, 3, 0), datetime(2026, 1, 14, 19, 0)),
    # Before 2007: 1st Sunday of April to the last Sunday of October.
    (utc(2005, 4, 3, 10, 0), datetime(2005, 4, 3, 3, 0)),
    (utc(2005, 3, 20, 10, 0), datetime(2005, 3, 20, 2, 0)),   # PST in March
    (utc(2005, 10, 30, 9, 0), datetime(2005, 10, 30, 1, 0)),
])
def test_pacific_wall_time(value, expected):
    assert _pacific(value) == expected


def test_fixed_time_is_a_whole_hour():
    assert is_fixed_time(utc(2026, 8, 8, 19, 0, 0))
    assert not is_fixed_time(utc(2026, 8, 8, 19, 0, 1))
    assert not is_fixed_time(utc(2026, 8, 8, 19, 30, 0))
    assert not is_fixed_time(utc(2026, 8, 8, 19, 0, 0, 5))


# ── the calendar date of a log ───────────────────────────────────────────────

class TestRealAppTime:
    def test_own_evening_find_in_the_americas(self):
        # The report: found the evening of Aug 8 in the US, after midnight UTC.
        stamp = utc(2026, 8, 9, 1, 23, 45)
        assert log_calendar_date(stamp, own=True, local_tz=US_CENTRAL) == date(2026, 8, 8)

    def test_own_find_in_europe_after_local_midnight(self):
        stamp = utc(2026, 8, 8, 22, 30, 12)
        assert log_calendar_date(stamp, own=True, local_tz=DENMARK) == date(2026, 8, 9)

    def test_other_finders_stay_utc(self):
        stamp = utc(2026, 8, 9, 1, 23, 45)
        assert log_calendar_date(stamp, own=False, local_tz=US_CENTRAL) == date(2026, 8, 9)


class TestFixedTimes:
    @pytest.mark.parametrize("stamp, expected", [
        (utc(2026, 8, 8, 19, 0), date(2026, 8, 8)),    # noon PDT as UTC
        (utc(2026, 1, 8, 20, 0), date(2026, 1, 8)),    # noon PST as UTC
        (utc(2026, 8, 8, 12, 0), date(2026, 8, 8)),    # opted-out users
        (utc(2026, 8, 8, 0, 0), date(2026, 8, 8)),     # date only
        (utc(2006, 6, 1, 14, 0), date(2006, 6, 1)),    # 07:00 PDT, pre-2008
        (utc(2012, 8, 9, 2, 0), date(2012, 8, 8)),     # 19:00 PDT
        (utc(2012, 1, 9, 3, 0), date(2012, 1, 8)),     # 19:00 PST
    ])
    @pytest.mark.parametrize("tz", [US_CENTRAL, DENMARK, NZ])
    def test_fixed_time_ignores_the_local_zone(self, stamp, expected, tz):
        # 19:00 Pacific read in Denmark would be the next morning there.
        assert log_calendar_date(stamp, own=True, local_tz=tz) == expected
        assert log_calendar_date(stamp, own=False, local_tz=tz) == expected

    def test_two_utc_on_a_non_dst_date_is_not_19_pacific(self):
        # 02:00 UTC in January is 18:00 PST, not a fixed Pacific time.
        assert log_calendar_date(utc(2026, 1, 9, 2, 0), own=True) == date(2026, 1, 9)


def test_naive_values_are_utc():
    assert log_calendar_date(datetime(2026, 8, 9, 1, 23, 45), own=True,
                             local_tz=US_CENTRAL) == date(2026, 8, 8)


def test_none():
    assert log_calendar_date(None, own=True) is None
    assert log_calendar_midnight(None, own=True) is None


def test_midnight_form():
    assert log_calendar_midnight(utc(2026, 8, 9, 1, 23, 45), own=True,
                                 local_tz=US_CENTRAL) == datetime(2026, 8, 8)


class TestIsOwnLog:
    def test_finder_id(self):
        assert is_own_log("1768915", "Renamed", "1768915", "AB Green")
        assert is_own_log(1768915, None, "1768915", None)

    def test_username_fallback(self):
        assert is_own_log(None, "ab green", "", "AB Green")

    def test_someone_else(self):
        assert not is_own_log("11905321", "Someone Else", "1768915", "AB Green")

    def test_no_account_set(self):
        assert not is_own_log("1", "AB Green", None, None)


# ── import ───────────────────────────────────────────────────────────────────

_GPX = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <gpx version="1.0" creator="Groundspeak Pocket Query"
     xmlns="http://www.topografix.com/GPX/1/0">
     <wpt lat="40.1" lon="-75.1">
      <time>2016-06-11T08:00:00Z</time>
      <name>GC875AA</name>
      <urlname>Evening find</urlname>
      <sym>Geocache Found</sym>
      <type>Geocache|Traditional Cache</type>
      <groundspeak:cache id="875" available="True" archived="False"
       xmlns:groundspeak="http://www.groundspeak.com/cache/1/0/1">
       <groundspeak:name>Evening find</groundspeak:name>
       <groundspeak:placed_by>Owner</groundspeak:placed_by>
       <groundspeak:owner id="1">Owner</groundspeak:owner>
       <groundspeak:type>Traditional Cache</groundspeak:type>
       <groundspeak:container>Small</groundspeak:container>
       <groundspeak:difficulty>1.5</groundspeak:difficulty>
       <groundspeak:terrain>1.5</groundspeak:terrain>
       <groundspeak:logs>
        <groundspeak:log id="3">
          <groundspeak:date>2026-08-09T01:23:45Z</groundspeak:date>
          <groundspeak:type>Found it</groundspeak:type>
          <groundspeak:finder id="1768915">AB Green</groundspeak:finder>
          <groundspeak:text encoded="False">TFTC</groundspeak:text>
        </groundspeak:log>
        <groundspeak:log id="2">
          <groundspeak:date>2026-08-06T00:45:10Z</groundspeak:date>
          <groundspeak:type>Didn't find it</groundspeak:type>
          <groundspeak:finder id="1768915">AB Green</groundspeak:finder>
          <groundspeak:text encoded="False">No luck</groundspeak:text>
        </groundspeak:log>
        <groundspeak:log id="1">
          <groundspeak:date>2026-08-01T01:10:00Z</groundspeak:date>
          <groundspeak:type>Didn't find it</groundspeak:type>
          <groundspeak:finder id="11905321">Someone Else</groundspeak:finder>
          <groundspeak:text encoded="False">Nope</groundspeak:text>
        </groundspeak:log>
       </groundspeak:logs>
      </groundspeak:cache>
     </wpt>
    </gpx>
    """)


@pytest.fixture()
def account():
    from opensak.gui.settings import get_settings
    get_settings().gc_username = "AB Green"
    get_settings().gc_finder_id = "1768915"


@pytest.fixture()
def in_us_central(monkeypatch):
    monkeypatch.setattr(
        importer_mod, "log_calendar_midnight",
        functools.partial(log_calendar_midnight, local_tz=US_CENTRAL))


def _import(tmp_path, content: str = _GPX):
    init_db(db_path=tmp_path / "finds.db")
    write_gpx(tmp_path, "finds.gpx", content)
    with get_session() as s:
        import_gpx(tmp_path / "finds.gpx", s)


def test_found_date_is_the_local_date_of_the_own_log(tmp_path, account, in_us_central):
    _import(tmp_path)
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC875AA").one()
        assert cache.found is True
        assert cache.found_date == datetime(2026, 8, 8)


def test_log_row_keeps_the_original_timestamp(tmp_path, account, in_us_central):
    _import(tmp_path)
    with get_session() as s:
        log = s.query(Log).filter_by(log_type="Found it").one()
        assert log.log_date.replace(tzinfo=None) == datetime(2026, 8, 9, 1, 23, 45)


def test_own_latest_dnf_is_read_in_local_time(tmp_path, account, in_us_central):
    _import(tmp_path)
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC875AA").one()
        assert cache.dnf_date == datetime(2026, 8, 5)


def test_someone_elses_latest_dnf_stays_utc(tmp_path, account, in_us_central):
    # Drop the user's own DNF: the latest is now the stranger's.
    content = _GPX.replace("2026-08-06T00:45:10Z", "2026-07-01T00:45:10Z")
    _import(tmp_path, content)
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC875AA").one()
        assert cache.dnf_date == datetime(2026, 8, 1)


def test_website_log_keeps_its_date_in_europe(tmp_path, account, monkeypatch):
    # A website find on Aug 8 stamped 19:00 Pacific (02:00 UTC Aug 9) must
    # not become Aug 9 in Denmark either.
    monkeypatch.setattr(
        importer_mod, "log_calendar_midnight",
        functools.partial(log_calendar_midnight, local_tz=DENMARK))
    _import(tmp_path, _GPX.replace("2026-08-09T01:23:45Z", "2026-08-09T02:00:00Z"))
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC875AA").one()
        assert cache.found_date == datetime(2026, 8, 8)

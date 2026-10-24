# tests/unit-tests/test_gpx_user_found_1145.py — issue #1145: the found-by-me
# date must survive a GPX round trip, to GSAK and back into OpenSAK.
#
# GSAK writes it as <gsak:UserFound>YYYY-MM-DD</gsak:UserFound> inside
# <gsak:wptExtension> (confirmed against a real GSAK export, GC6KHCZ below).
# OpenSAK's export never wrote it, so GSAK showed a blank Found date whenever
# the user's own found log wasn't in the file. OpenSAK's importer ignored it
# too, so the date was also lost when the user's log was missing.

import textwrap
from datetime import datetime

import pytest

from opensak.db.database import get_session, init_db, reload_caches_full
from opensak.db.models import Cache
from opensak.gps.garmin import generate_gpx
from opensak.importer import _parse_gsak_date, import_gpx
from tests.data import SAMPLE_GPX, write_gpx


@pytest.fixture()
def account():
    from opensak.gui.settings import get_settings
    get_settings().gc_username = "AB Green"
    get_settings().gc_finder_id = "1768915"


def _gsak_export(own_log: bool) -> str:
    """A trimmed real GSAK export (GC6KHCZ): the extension fields as GSAK
    writes them, a stranger's find, and optionally the user's own find."""
    own = textwrap.dedent("""\
        <groundspeak:log id="1365239305">
          <groundspeak:date>2026-06-04T13:39:08Z</groundspeak:date>
          <groundspeak:type>Found it</groundspeak:type>
          <groundspeak:finder id="1768915">AB Green</groundspeak:finder>
          <groundspeak:text encoded="False">Greetings from Denmark</groundspeak:text>
        </groundspeak:log>""") if own_log else ""
    gpx = textwrap.dedent("""\
        <?xml version="1.0" encoding="utf-8"?>
        <gpx xmlns:xsd="http://www.w3.org/2001/XMLSchema"
        xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
        version="1.0" creator="GSAK"
        xmlns="http://www.topografix.com/GPX/1/0">
         <wpt lat="36.05205" lon="14.25205">
          <time>2016-06-11T08:00:00Z</time>
          <name>GC6KHCZ</name>
          <urlname>In-Numri by holmiium, mowgli142</urlname>
          <sym>Geocache Found</sym>
          <type>Geocache|Unknown Cache|Found</type>
            <gsak:wptExtension xmlns:gsak="http://www.gsak.net/xmlv1/6">
              <gsak:UserFlag>true</gsak:UserFlag>
              <gsak:Lock>false</gsak:Lock>
              <gsak:DNF>false</gsak:DNF>
              <gsak:Watch>false</gsak:Watch>
              <gsak:UserData></gsak:UserData>
              <gsak:UserFound>2026-06-04</gsak:UserFound>
              <gsak:FirstToFind>false</gsak:FirstToFind>
              <gsak:UserSort>0</gsak:UserSort>
              <gsak:LastGpxDate>2026-10-23</gsak:LastGpxDate>
              <gsak:Code>GC6KHCZ</gsak:Code>
            </gsak:wptExtension>
          <groundspeak:cache id="5712774" available="True" archived="False" xmlns:groundspeak="http://www.groundspeak.com/cache/1/0/1">
           <groundspeak:name>In-Numri</groundspeak:name>
           <groundspeak:placed_by>holmiium, mowgli142</groundspeak:placed_by>
           <groundspeak:owner id="14788391">holmiium</groundspeak:owner>
           <groundspeak:type>Unknown Cache</groundspeak:type>
           <groundspeak:container>Micro</groundspeak:container>
           <groundspeak:difficulty>3.5</groundspeak:difficulty>
           <groundspeak:terrain>1.5</groundspeak:terrain>
           <groundspeak:country>Malta</groundspeak:country>
           <groundspeak:logs>
            <groundspeak:log id="1390637314">
              <groundspeak:date>2026-10-21T11:00:00Z</groundspeak:date>
              <groundspeak:type>Found it</groundspeak:type>
              <groundspeak:finder id="11905321">Someone Else</groundspeak:finder>
              <groundspeak:text encoded="False">TFTC</groundspeak:text>
            </groundspeak:log>
        <!-- OWN_LOG -->
           </groundspeak:logs>
          </groundspeak:cache>
         </wpt>
        </gpx>
        """)
    return gpx.replace("<!-- OWN_LOG -->", own)


def _import(tmp_path, name: str, content: str) -> Cache:
    init_db(db_path=tmp_path / f"{name}.db")
    write_gpx(tmp_path, f"{name}.gpx", content)
    with get_session() as s:
        import_gpx(tmp_path / f"{name}.gpx", s)
    with get_session() as s:
        return s.query(Cache).filter_by(gc_code="GC6KHCZ").one()


# ── parsing ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("2026-06-04", datetime(2026, 6, 4)),
    (" 2026-06-04 ", datetime(2026, 6, 4)),
    ("2026-06-04T13:39:08", datetime(2026, 6, 4)),
    ("", None),
    (None, None),
    ("not a date", None),
    ("2026-13-40", None),
])
def test_parse_gsak_date(raw, expected):
    assert _parse_gsak_date(raw) == expected


# ── import of a real GSAK export ─────────────────────────────────────────────

def test_gsak_user_found_used_when_own_log_missing(tmp_path, account):
    cache = _import(tmp_path, "nolog", _gsak_export(own_log=False))
    assert cache.found is True
    # Before #1145 this stayed None: the user's own log isn't in the file,
    # and a stranger's find must never be taken as the user's date.
    assert cache.found_date is not None
    assert cache.found_date.date() == datetime(2026, 6, 4).date()


def test_own_log_on_same_day_keeps_its_time(tmp_path, account):
    cache = _import(tmp_path, "withlog", _gsak_export(own_log=True))
    assert cache.found_date is not None
    assert (cache.found_date.year, cache.found_date.month, cache.found_date.day,
            cache.found_date.hour, cache.found_date.minute) == (2026, 6, 4, 13, 39)


def test_user_found_wins_over_own_log_on_another_day(tmp_path, account):
    # The user changed the found date in GSAK: UserFound is what they set.
    content = _gsak_export(own_log=True).replace(
        "<gsak:UserFound>2026-06-04</gsak:UserFound>",
        "<gsak:UserFound>2026-06-02</gsak:UserFound>",
    )
    cache = _import(tmp_path, "otherday", content)
    assert cache.found_date == datetime(2026, 6, 2)


def test_user_found_alone_does_not_mark_a_cache_found(tmp_path, account):
    # The date only dates a find; found status still comes from sym/type.
    content = (_gsak_export(own_log=False)
               .replace("<sym>Geocache Found</sym>", "<sym>Geocache</sym>")
               .replace("Unknown Cache|Found</type>", "Unknown Cache</type>"))
    cache = _import(tmp_path, "notfound", content)
    assert not cache.found
    assert cache.found_date is None


# ── export and OpenSAK → OpenSAK round trip ──────────────────────────────────

def _seed(tmp_path, *, found: bool, found_date):
    init_db(db_path=tmp_path / "src.db")
    write_gpx(tmp_path, "src.gpx", SAMPLE_GPX)
    with get_session() as s:
        import_gpx(tmp_path / "src.gpx", s)
    with get_session() as s:
        for c in s.query(Cache).all():
            c.found = found
            c.found_date = found_date
            for log in list(c.logs):     # no own log: the date must come from UserFound
                s.delete(log)
    with get_session() as s:
        return reload_caches_full(s.query(Cache).all())


def test_export_writes_user_found(tmp_path):
    caches = _seed(tmp_path, found=True, found_date=datetime(2025, 7, 15, 10, 30))
    gpx = generate_gpx(caches, "found")
    assert gpx.count("<gsak:UserFound>2025-07-15</gsak:UserFound>") == len(caches)


def test_export_skips_user_found_for_unfound_cache(tmp_path):
    # A stale found_date on a cache that isn't found must not be exported.
    caches = _seed(tmp_path, found=False, found_date=datetime(2025, 7, 15))
    assert "UserFound" not in generate_gpx(caches, "unfound")


def test_found_date_survives_opensak_roundtrip(tmp_path, account):
    caches = _seed(tmp_path, found=True, found_date=datetime(2025, 7, 15, 10, 30))
    codes = {c.gc_code for c in caches}
    out = tmp_path / "out.gpx"
    out.write_text(generate_gpx(caches, "roundtrip"), encoding="utf-8")

    init_db(db_path=tmp_path / "dst.db")
    with get_session() as s:
        import_gpx(out, s)
    with get_session() as s:
        rows = {c.gc_code: c for c in s.query(Cache).all()}
    assert set(rows) == codes
    for c in rows.values():
        assert c.found is True
        assert c.found_date is not None and c.found_date.date() == datetime(2025, 7, 15).date()

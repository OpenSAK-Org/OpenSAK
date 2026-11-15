# tests/unit-tests/test_gsak_found_status_import.py — issue #766 (Allyn56
# round-trip testing): found status not carrying over from a genuine GSAK
# GPX/GGZ export.
#
# First diagnosis: GSAK's own GPX/GGZ export doesn't set sym="Geocache Found"
# the way the GC.com Pocket Query convention does. That's still true, and
# the wptExtension-fingerprint fallback below stays as a secondary signal.
#
# BUT: a real before/after GSAK export pair supplied by Allyn56 (GC4RD9A)
# showed the actual, universal signal GSAK uses for its own Mark As Found
# state is a trailing "|Found" segment on <type>, e.g.
#   <type>Geocache|Traditional Cache|Found</type>
# with NO gsak: namespace or wptExtension present at all in that export, and
# critically -- the account owner's own "Found it" log is NOT necessarily
# present anywhere in the file's <groundspeak:logs> (GSAK's Mark As Found is
# a personal flag, not a log entry). That means:
#   1. found_by_me must also be derived directly from the <type> "|Found"
#      segment, independent of both <sym> and the log list.
#   2. cache_type parsing must strip that trailing "|Found" segment first,
#      or it gets misparsed as the cache type itself.
#   3. found_date must NOT be guessed from a stranger's found-type log just
#      because gc_username/gc_finder_id is configured but happens not to
#      match anything in this particular file -- that previously fell
#      through to the "no username configured" oldest-log fallback and
#      would silently attribute someone else's find date as the user's own.

import textwrap
from pathlib import Path

import pytest

from opensak.db.database import get_session, init_db
from opensak.db.models import Cache
from opensak.importer import import_gpx
from tests.data import build_gpx, cache_wpt, write_gpx


@pytest.fixture()
def fresh_db(tmp_path):
    db_path = tmp_path / "gsak_found.db"
    init_db(db_path=db_path)
    return db_path


@pytest.fixture()
def found_username():
    from opensak.gui.settings import get_settings
    get_settings().gc_username = "AB Green"
    get_settings().gc_finder_id = ""


def _write_gpx(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "test.gpx"
    p.write_text(content, encoding="utf-8")
    return p


def _gsak_gpx(*, sym: str = "Geocache", gsak_extension: str, log_date: str = "2024-03-01T00:00:00Z") -> str:
    # Mirrors a real GSAK GPX export: <sym> reflects the cache TYPE icon
    # (never "Geocache Found"), the found-type log is present in the
    # groundspeak:logs block, and the wptExtension carries GSAK-only
    # fields (here: UserFlag) that OpenSAK's own export never writes.
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="utf-8"?>
        <gpx xmlns="http://www.topografix.com/GPX/1/0"
             xmlns:groundspeak="http://www.groundspeak.com/cache/1/0/1"
             xmlns:gsak="http://www.gsak.net/xmlv1/6"
             version="1.0" creator="GSAK">
          <wpt lat="55.0000" lon="10.0000">
            <time>2024-01-01T00:00:00</time>
            <n>GCFOUNDG1</n>
            <urlname>Test Cache</urlname>
            <sym>{sym}</sym>
            <type>Geocache|Traditional Cache</type>
            <groundspeak:cache id="1" archived="False" available="True">
              <groundspeak:name>Test Cache</groundspeak:name>
              <groundspeak:placed_by>Owner</groundspeak:placed_by>
              <groundspeak:type>Traditional Cache</groundspeak:type>
              <groundspeak:container>Small</groundspeak:container>
              <groundspeak:difficulty>2.0</groundspeak:difficulty>
              <groundspeak:terrain>2.0</groundspeak:terrain>
              <groundspeak:logs>
                <groundspeak:log id="9001">
                  <groundspeak:date>{log_date}</groundspeak:date>
                  <groundspeak:type>Found it</groundspeak:type>
                  <groundspeak:finder id="999">AB Green</groundspeak:finder>
                  <groundspeak:text encoded="False">TFTC</groundspeak:text>
                </groundspeak:log>
              </groundspeak:logs>
            </groundspeak:cache>
            {gsak_extension}
          </wpt>
        </gpx>
    """)


def test_gsak_export_found_status_detected_via_userflag(tmp_path, fresh_db, found_username):
    # Genuine GSAK export: sym is present but plain ("Geocache"), never
    # "Geocache Found" -- only the GSAK-only UserFlag field marks this as a
    # real GSAK file, and only the log list identifies the find.
    gpx = _gsak_gpx(gsak_extension="""
            <gsak:wptExtension>
              <gsak:UserFlag>True</gsak:UserFlag>
            </gsak:wptExtension>""")
    f = _write_gpx(tmp_path, gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCFOUNDG1").one()
        assert cache.found is True
        assert cache.found_date is not None
        assert cache.found_date.year == 2024


def test_gsak_export_found_status_detected_via_lat_before_correct(tmp_path, fresh_db, found_username):
    # Same scenario, but the GSAK fingerprint comes from corrected-coords
    # fields (LatBeforeCorrect/LonBeforeCorrect) instead of UserFlag.
    gpx = _gsak_gpx(gsak_extension="""
            <gsak:wptExtension>
              <gsak:LatBeforeCorrect>55.1000</gsak:LatBeforeCorrect>
              <gsak:LonBeforeCorrect>10.1000</gsak:LonBeforeCorrect>
            </gsak:wptExtension>""")
    f = _write_gpx(tmp_path, gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCFOUNDG1").one()
        assert cache.found is True


@pytest.mark.parametrize("creator", ['creator="OpenSAK"', ""])
def test_gsak_export_without_fingerprint_still_uses_old_guard(
        tmp_path, fresh_db, found_username, creator):
    # Regression guard: a plain <sym>Geocache</sym> with NO GSAK-only
    # wptExtension fields at all (e.g. only gsak:UserNote, matching
    # OpenSAK's own re-export) must still be treated as authoritative
    # "not found" -- the fallback must not fire just because a
    # gsak:wptExtension element exists. #1169: a file whose creator is
    # GSAK gets GSAK's rules (test_gpx_gsak_fields_1169.py), so this is
    # OpenSAK's own file, or one without a creator.
    gpx = _gsak_gpx(gsak_extension="""
            <gsak:wptExtension>
              <gsak:UserNote>Nice spot.</gsak:UserNote>
            </gsak:wptExtension>""").replace('creator="GSAK"', creator)
    f = _write_gpx(tmp_path, gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCFOUNDG1").one()
        assert cache.found is False


def test_gsak_export_sym_geocache_found_still_honoured_directly(tmp_path, fresh_db, found_username):
    # If a GSAK export ever DOES say sym="Geocache Found" outright, it's
    # still honoured directly regardless of the GSAK fingerprint.
    gpx = _gsak_gpx(
        sym="Geocache Found",
        gsak_extension="""
            <gsak:wptExtension>
              <gsak:UserFlag>True</gsak:UserFlag>
            </gsak:wptExtension>""",
    )
    f = _write_gpx(tmp_path, gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCFOUNDG1").one()
        assert cache.found is True


# ── Real-world GSAK export pattern: <type>...|Found</type> ─────────────────
# Confirmed against Allyn56's actual GSAK before/after export of GC4RD9A —
# no gsak: namespace at all, sym stays the plain cache-type icon, and the
# only found signal is the trailing "|Found" segment on <type>.

def test_type_found_segment_marks_cache_as_found(tmp_path, fresh_db):
    # No sym="Geocache Found", no gc_username/gc_finder_id configured, no
    # matching "Found it" log in the file at all -- exactly Allyn56's real
    # GSAK export. Only <type>...|Found</type> signals the find.
    gpx = build_gpx(cache_wpt(
        "GCTYPEF1", cache_type="Traditional Cache", gs_id=50001,
        type_found=True,
        logs=[{
            "id": "50001001", "type": "Found it", "date": "2025-09-26T12:24:13Z",
            "finder": "Someone Else Entirely", "finder_id": "1",
        }],
    ))
    f = write_gpx(tmp_path, "type_found.gpx", gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCTYPEF1").one()
        assert cache.found is True


def test_type_found_segment_does_not_misattribute_date_when_username_configured(
    tmp_path, fresh_db, found_username
):
    # Same file, but gc_username IS configured (as a real user's Settings
    # would be) and simply doesn't match any log in the file -- GSAK's Mark
    # As Found doesn't necessarily add a personal log entry at all. Must NOT
    # fall through to "no username configured" and attribute the stranger's
    # log date as the user's own found_date.
    gpx = build_gpx(cache_wpt(
        "GCTYPEF2", cache_type="Traditional Cache", gs_id=50002,
        type_found=True,
        logs=[{
            "id": "50002001", "type": "Found it", "date": "2025-09-26T12:24:13Z",
            "finder": "Someone Else Entirely", "finder_id": "1",
        }],
    ))
    f = write_gpx(tmp_path, "type_found_user.gpx", gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCTYPEF2").one()
        assert cache.found is True
        assert cache.found_date is None


def test_type_found_segment_still_finds_own_date_when_own_log_present(
    tmp_path, fresh_db, found_username
):
    # If the user's own found-type log IS in the file, it's still used for
    # found_date as normal.
    gpx = build_gpx(cache_wpt(
        "GCTYPEF3", cache_type="Traditional Cache", gs_id=50003,
        type_found=True,
        logs=[{
            "id": "50003001", "type": "Found it", "date": "2024-03-01T00:00:00Z",
            "finder": "AB Green", "finder_id": "999",
        }],
    ))
    f = write_gpx(tmp_path, "type_found_owndate.gpx", gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCTYPEF3").one()
        assert cache.found is True
        assert cache.found_date is not None
        assert cache.found_date.year == 2024


def test_type_found_segment_does_not_leak_into_cache_type(tmp_path, fresh_db):
    # The trailing "|Found" segment must not be mistaken for the cache type
    # itself (cache_type is normally taken from <groundspeak:type>, but the
    # <type> element's own derivation must stay correct too, in case
    # <groundspeak:type> is ever absent).
    gpx = build_gpx(cache_wpt(
        "GCTYPEF4", cache_type="Multi-cache", gs_id=50004, type_found=True,
    ))
    f = write_gpx(tmp_path, "type_found_ctype.gpx", gpx)
    with get_session() as s:
        import_gpx(f, s)
        cache = s.query(Cache).filter_by(gc_code="GCTYPEF4").one()
        assert cache.cache_type == "Multi-cache"
        assert cache.cache_type != "Found"

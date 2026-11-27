"""tests/unit-tests/test_import_keeps_user_waypoints_1388.py — issue #1388.

A GPX/PQ import deleted every waypoint of the caches in the file before
adding the file's waypoints, user-created ones too (waypoint dialog, macros,
GSAK's Child_ByGSAK). A parking waypoint added by hand was gone after the
next Pocket Query. Imports now only replace the waypoints that came from a
file and keep the user's, as GSAK does. A file that carries a user waypoint
back (OpenSAK's or GSAK's export, gsak:Child_ByGSAK) updates it instead of
adding a duplicate.
"""

import pytest

from opensak.db.database import get_session, init_db
from opensak.db.models import Cache, Waypoint
from opensak.importer import import_gpx
from tests.data import (
    SAMPLE_GPX, SAMPLE_WPTS_GPX, make_gpx_with_inline_wpt, write_gpx,
)


def _user_wpt(prefix: str, lat: float) -> str:
    """A user waypoint of GC12345 with GSAK's child fields, as OpenSAK's and
    GSAK's exports write it."""
    return (
        f'  <wpt lat="{lat}" lon="12.5700">\n'
        f'    <name>{prefix}12345</name>\n'
        '    <desc>My parking</desc>\n'
        '    <type>Waypoint|Parking Area</type>\n'
        '    <cmt>From OSM</cmt>\n'
        '    <gsak:wptExtension xmlns:gsak="http://www.gsak.net/xmlv1/6">\n'
        '      <gsak:Parent>GC12345</gsak:Parent>\n'
        '      <gsak:Child_ByGSAK>true</gsak:Child_ByGSAK>\n'
        '      <gsak:Child_Flag>false</gsak:Child_Flag>\n'
        '    </gsak:wptExtension>\n'
        '  </wpt>\n'
    )


def _user_wpt_gpx(prefix: str, lat: float) -> str:
    """SAMPLE_GPX with a user waypoint of GC12345 in the same file."""
    return SAMPLE_GPX.replace("</gpx>", _user_wpt(prefix, lat) + "</gpx>")


@pytest.fixture(autouse=True)
def db(tmp_path):
    init_db(db_path=tmp_path / "wpts1388.db")


def _import(tmp_path, content: str, wpts: str | None = None):
    gpx = write_gpx(tmp_path, "pq.gpx", content)
    wpts_path = write_gpx(tmp_path, "pq-wpts.gpx", wpts) if wpts else None
    with get_session() as s:
        return import_gpx(gpx, s, wpts_path=wpts_path)


def _add_user_wpt(prefix: str = "P1", lat: float = 55.0) -> None:
    """A waypoint added in the waypoint dialog or by a macro."""
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GC12345").one()
        s.add(Waypoint(
            cache_id=cache.id, parent_gc_code="GC12345", prefix=prefix,
            wp_type="Parking Area", name="My parking", latitude=lat,
            longitude=12.57, created_by_user=True,
        ))
        s.commit()


def _waypoints() -> list[tuple[str, bool, float | None]]:
    with get_session() as s:
        return sorted(
            (w.prefix, w.created_by_user, w.latitude)
            for w in s.query(Waypoint).join(Cache).filter(Cache.gc_code == "GC12345")
        )


def _waypoint_count() -> int:
    with get_session() as s:
        return s.query(Cache).filter_by(gc_code="GC12345").one().waypoint_count


# ── user waypoints survive a re-import ──────────────────────────────────────

def test_reimport_keeps_user_waypoint_and_replaces_inline_ones(tmp_path):
    # The reproduction from the issue, with the file's own parking waypoint.
    _import(tmp_path, make_gpx_with_inline_wpt())
    _add_user_wpt()
    _import(tmp_path, make_gpx_with_inline_wpt())
    assert _waypoints() == [("P1", True, 55.0), ("PK", False, 55.677)]
    assert _waypoint_count() == 2


def test_reimport_without_waypoints_keeps_user_waypoint(tmp_path):
    # The per-cache reset: the file has no waypoints for the cache at all.
    _import(tmp_path, SAMPLE_GPX)
    _add_user_wpt()
    _import(tmp_path, SAMPLE_GPX)
    assert _waypoints() == [("P1", True, 55.0)]
    assert _waypoint_count() == 1


def test_reimport_with_companion_wpts_keeps_user_waypoint(tmp_path):
    # A Pocket Query's -wpts.gpx.
    _import(tmp_path, SAMPLE_GPX, SAMPLE_WPTS_GPX)
    _add_user_wpt()
    _import(tmp_path, SAMPLE_GPX, SAMPLE_WPTS_GPX)
    assert _waypoints() == [("P1", True, 55.0), ("PK", False, 55.6762)]
    assert _waypoint_count() == 2


def test_file_waypoints_are_still_replaced(tmp_path):
    # Without user waypoints nothing changes: no duplicates of the file's.
    _import(tmp_path, make_gpx_with_inline_wpt())
    _import(tmp_path, make_gpx_with_inline_wpt())
    assert _waypoints() == [("PK", False, 55.677)]


# ── a user waypoint carried back by the file ─────────────────────────────────

def test_user_waypoint_from_file_updates_the_kept_one(tmp_path):
    _import(tmp_path, SAMPLE_GPX)
    _add_user_wpt("P1", lat=55.0)
    _import(tmp_path, _user_wpt_gpx("P1", lat=55.5))
    assert _waypoints() == [("P1", True, 55.5)]
    assert _waypoint_count() == 1


def test_user_waypoint_from_file_matches_prefix_case_insensitively(tmp_path):
    _import(tmp_path, SAMPLE_GPX)
    _add_user_wpt("p1", lat=55.0)
    _import(tmp_path, _user_wpt_gpx("P1", lat=55.5))
    assert [(w[1], w[2]) for w in _waypoints()] == [(True, 55.5)]


def test_new_user_waypoint_from_file_is_added_as_user_waypoint(tmp_path):
    # Imported twice: the second import updates it, no duplicate.
    _import(tmp_path, _user_wpt_gpx("P2", lat=55.5))
    _import(tmp_path, _user_wpt_gpx("P2", lat=55.5))
    assert _waypoints() == [("P2", True, 55.5)]


def test_file_waypoint_with_a_user_waypoints_prefix_is_added(tmp_path):
    # Only a waypoint the file marks as the user's replaces a user waypoint.
    _import(tmp_path, SAMPLE_GPX)
    _add_user_wpt("PK", lat=55.0)
    _import(tmp_path, make_gpx_with_inline_wpt())
    assert _waypoints() == [("PK", False, 55.677), ("PK", True, 55.0)]

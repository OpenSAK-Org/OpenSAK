# tests/unit-tests/test_gsak_importer.py — GSAK direct database importer tests (#469, session 1).
#
# Builds a small synthetic GSAK-schema SQLite file per test (rather than shipping
# a real GSAK backup into the repo) so these tests are self-contained and don't
# depend on any real-world database. Column layout mirrors the real schema
# confirmed against two independent real GSAK databases during the #469
# investigation (Sommerhus.zip: 48 caches; a 12,600-cache/419MB database).
#
# Uses the function-scoped `db_session` fixture (fresh isolated DB per test),
# not the module-scoped `tmp_db` — these tests all reuse the gc_code
# "GC1TEST", which would silently turn into cross-test updates instead of
# creates under a shared module-scoped database.

import sqlite3
from pathlib import Path

import pytest

from opensak.db.models import Attribute, Cache, Log, Trackable, UserNote, Waypoint
from opensak.importer.gsak_importer import (
    GSAK_CACHE_TYPE_MAP,
    GSAK_CONTAINER_MAP,
    _parse_gsak_trackables,
    _replace_embedded_images_with_placeholders,
    import_gsak_db,
    scan_gsak_notes_for_embedded_images,
)


# ── Synthetic GSAK database builder ──────────────────────────────────────────

_SCHEMA = [
    """CREATE TABLE Caches (
        Code TEXT, Name TEXT, CacheType TEXT, Container TEXT,
        Latitude TEXT, Longitude TEXT, Difficulty REAL, Terrain REAL,
        PlacedBy TEXT, OwnerName TEXT, OwnerId TEXT,
        PlacedDate TEXT, Changed TEXT, Status TEXT, Archived INTEGER,
        TempDisabled INTEGER, Country TEXT, State TEXT, County TEXT,
        Found INTEGER, FoundByMeDate TEXT, DNF INTEGER, DNFDate TEXT,
        FTF INTEGER, UserFlag INTEGER, UserSort INTEGER,
        UserData TEXT, User2 TEXT, User3 TEXT, User4 TEXT,
        FavPoints INTEGER, GcNote TEXT, Elevation REAL, Color TEXT,
        Guid TEXT, Watch INTEGER, CacheId TEXT, Lock INTEGER, FoundCount INTEGER,
        IsPremium INTEGER
    )""",
    """CREATE TABLE CacheMemo (
        Code TEXT, LongDescription TEXT, ShortDescription TEXT,
        Url TEXT, Hints TEXT, UserNote TEXT, TravelBugs TEXT
    )""",
    """CREATE TABLE Corrected (
        kCode TEXT, kBeforeLat TEXT, kBeforeLon TEXT,
        kAfterLat TEXT, kAfterLon TEXT, kType TEXT
    )""",
    """CREATE TABLE Waypoints (
        cParent TEXT, cCode TEXT, cPrefix TEXT, cName TEXT, cType TEXT,
        cLat TEXT, cLon TEXT, cByuser INTEGER, cDate TEXT, cFlag INTEGER
    )""",
    """CREATE TABLE WayMemo (
        cParent TEXT, cCode TEXT, cComment TEXT, cUrl TEXT
    )""",
    """CREATE TABLE Attributes (
        aCode TEXT, aId INTEGER, aInc INTEGER
    )""",
    """CREATE TABLE Logs (
        lParent TEXT, lLogId INTEGER, lType TEXT, lBy TEXT, lDate TEXT,
        lTime TEXT, lLat TEXT, lLon TEXT, lEncoded INTEGER,
        lownerid INTEGER, lHasHtml INTEGER, lIsowner INTEGER
    )""",
    """CREATE TABLE LogMemo (
        lParent TEXT, lLogId INTEGER, lText TEXT
    )""",
]

_DEFAULT_CACHE = dict(
    Code="GC1TEST", Name="Test Cache", CacheType="T", Container="Micro",
    Latitude="55.5802", Longitude="11.175917", Difficulty=1.5, Terrain=2.0,
    PlacedBy="AB Green", OwnerName="AB Green", OwnerId="1768915",
    PlacedDate="2023-10-24", Changed="2026-06-23", Status="A", Archived=0,
    TempDisabled=0, Country="Denmark", State="Region Sjælland", County="",
    Found=0, FoundByMeDate="", DNF=0, DNFDate="", FTF=0, UserFlag=0,
    UserSort=0, UserData="", User2="", User3="", User4="",
    FavPoints=3, GcNote="", Elevation=0.0, Color="", Guid="", Watch=0,
    CacheId="9284799", Lock=0, FoundCount=30, IsPremium=0,
)


def _make_gsak_db(
    path: Path,
    caches: list[dict] | None = None,
    memos: list[dict] | None = None,
    corrected: list[dict] | None = None,
    waypoints: list[dict] | None = None,
    waymemos: list[dict] | None = None,
    attributes: list[dict] | None = None,
    logs: list[dict] | None = None,
    logmemos: list[dict] | None = None,
) -> Path:
    conn = sqlite3.connect(path)
    for ddl in _SCHEMA:
        conn.execute(ddl)

    for c in (caches if caches is not None else [_DEFAULT_CACHE]):
        row = {**_DEFAULT_CACHE, **c}
        cols = ", ".join(row.keys())
        qs = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO Caches ({cols}) VALUES ({qs})", list(row.values()))

    for m in (memos if memos is not None else [{"Code": "GC1TEST", "Url": "https://coord.info/GC1TEST"}]):
        cols = ", ".join(m.keys())
        qs = ", ".join("?" for _ in m)
        conn.execute(f"INSERT INTO CacheMemo ({cols}) VALUES ({qs})", list(m.values()))

    for k in (corrected or []):
        cols = ", ".join(k.keys())
        qs = ", ".join("?" for _ in k)
        conn.execute(f"INSERT INTO Corrected ({cols}) VALUES ({qs})", list(k.values()))

    for w in (waypoints or []):
        cols = ", ".join(w.keys())
        qs = ", ".join("?" for _ in w)
        conn.execute(f"INSERT INTO Waypoints ({cols}) VALUES ({qs})", list(w.values()))

    for wm in (waymemos or []):
        cols = ", ".join(wm.keys())
        qs = ", ".join("?" for _ in wm)
        conn.execute(f"INSERT INTO WayMemo ({cols}) VALUES ({qs})", list(wm.values()))

    for a in (attributes or []):
        cols = ", ".join(a.keys())
        qs = ", ".join("?" for _ in a)
        conn.execute(f"INSERT INTO Attributes ({cols}) VALUES ({qs})", list(a.values()))

    for lg in (logs or []):
        cols = ", ".join(lg.keys())
        qs = ", ".join("?" for _ in lg)
        conn.execute(f"INSERT INTO Logs ({cols}) VALUES ({qs})", list(lg.values()))

    for lm in (logmemos or []):
        cols = ", ".join(lm.keys())
        qs = ", ".join("?" for _ in lm)
        conn.execute(f"INSERT INTO LogMemo ({cols}) VALUES ({qs})", list(lm.values()))

    conn.commit()
    conn.close()
    return path


# ── Basic import ──────────────────────────────────────────────────────────────

def test_non_utf8_field_does_not_abort_import(db_session, tmp_path):
    # GSAK databases aren't guaranteed to store text as UTF-8 (issue #529
    # follow-up, reported by Thomas Bang Christensen). `SmartName` isn't a
    # field OpenSAK reads at all, but it's swept in unused by `SELECT c.*`
    # and must not abort the whole import just because it holds
    # Windows-1252 bytes instead.
    db_path = tmp_path / "gsak.db3"
    _make_gsak_db(db_path)  # base schema + default cache row

    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE Caches ADD COLUMN SmartName TEXT")
    # "Værktøj" encoded as Windows-1252 (0x56 0xE6 0x72 0x6B 0x74 0xF8 0x6A) —
    # invalid as UTF-8, the same shape of field that crashed the real import.
    conn.execute(
        "UPDATE Caches SET SmartName = CAST(x'56E6726B74F86A' AS TEXT) "
        "WHERE Code = 'GC1TEST'"
    )
    conn.commit()
    conn.close()

    result = import_gsak_db(db_path, db_session)

    assert result.errors == []
    assert result.created == 1
    assert result.encoding_fallbacks == 1


def test_import_basic_cache_fields(db_session, tmp_path):
    db = _make_gsak_db(tmp_path / "gsak.db3")
    result = import_gsak_db(db, db_session)
    assert result.created == 1
    assert result.updated == 0
    assert result.errors == []

    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    assert cache.name == "Test Cache"
    assert cache.cache_type == "Traditional Cache"
    assert cache.container == "Micro"
    assert cache.latitude == pytest.approx(55.5802)
    assert cache.longitude == pytest.approx(11.175917)
    assert cache.difficulty == 1.5
    assert cache.terrain == 2.0
    assert cache.owner_name == "AB Green"
    assert cache.available is True
    assert cache.archived is False
    assert cache.gc_cache_id == "9284799"
    assert cache.favorite_points == 3
    assert cache.url == "https://coord.info/GC1TEST"
    # find_count (#517 prep) is deliberately left None by the GSAK importer —
    # GSAK's own FoundCount is identical to Found (0/1), not a true find
    # count, so there's no honest source for it here (see module docstring).
    assert cache.find_count is None


def test_elevation_zero_maps_to_none(db_session, tmp_path):
    # GSAK's default/unset elevation (0.0) must not be mistaken for a real
    # sea-level elevation — see #469 schema PR rationale.
    db = _make_gsak_db(tmp_path / "gsak.db3", caches=[{"Elevation": 0.0}])
    import_gsak_db(db, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    assert cache.elevation is None


def test_elevation_real_value_preserved(db_session, tmp_path):
    db = _make_gsak_db(tmp_path / "gsak.db3", caches=[{"Elevation": 216.0}])
    import_gsak_db(db, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    assert cache.elevation == 216.0


@pytest.mark.parametrize("gsak_code,expected", sorted(GSAK_CACHE_TYPE_MAP.items()))
def test_cache_type_mapping(db_session, tmp_path, gsak_code, expected):
    db = _make_gsak_db(tmp_path / "gsak.db3", caches=[{"CacheType": gsak_code}])
    import_gsak_db(db, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    assert cache.cache_type == expected


@pytest.mark.parametrize("gsak_code", ["D", "F", "Y"])
def test_cache_type_intentionally_unmapped_codes_fall_back(db_session, tmp_path, gsak_code):
    # Issue #532: D ("Groundspeak Lost and Found Celebration"), F ("Lost and
    # Found Event") and Y (Waymark) are deliberately left out of
    # GSAK_CACHE_TYPE_MAP — D/F have no unambiguous match to our single
    # "Community Celebration Event" entry, and Y has no OpenSAK equivalent
    # at all. This locks in the safe fallback rather than risking a wrong
    # mapping being silently reinstated later.
    db = _make_gsak_db(tmp_path / "gsak.db3", caches=[{"CacheType": gsak_code}])
    import_gsak_db(db, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    assert cache.cache_type == "Unknown Cache"


@pytest.mark.parametrize("gsak_container,expected", sorted(GSAK_CONTAINER_MAP.items()))
def test_container_mapping(db_session, tmp_path, gsak_container, expected):
    db = _make_gsak_db(tmp_path / "gsak.db3", caches=[{"Container": gsak_container}])
    import_gsak_db(db, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    assert cache.container == expected


@pytest.mark.parametrize("status,available,archived", [
    ("A", True, False),
    ("T", False, False),
    ("X", False, True),
])
def test_status_mapping(db_session, tmp_path, status, available, archived):
    db = _make_gsak_db(tmp_path / "gsak.db3", caches=[{"Status": status}])
    import_gsak_db(db, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    assert cache.available is available
    assert cache.archived is archived


# ── Waypoints ──────────────────────────────────────────────────────────────

def test_waypoint_mapping(db_session, tmp_path):
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        waypoints=[{
            "cParent": "GC1TEST", "cCode": "PK1TEST", "cPrefix": "PK",
            "cName": "Parking", "cType": "Parking Area",
            "cLat": "55.58", "cLon": "11.17",
            "cByuser": 0, "cDate": "2020-01-01", "cFlag": 1,
        }],
        waymemos=[{
            "cParent": "GC1TEST", "cCode": "PK1TEST",
            "cComment": "Park here", "cUrl": "https://x.test/PK1TEST",
        }],
    )
    result = import_gsak_db(db, db_session)
    assert result.waypoints == 1

    wp = db_session.query(Waypoint).one()
    assert wp.prefix == "PK"
    assert wp.name == "Parking"
    assert wp.wp_type == "Parking Area"
    assert wp.wp_code == "PK1TEST"
    assert wp.comment == "Park here"
    assert wp.url == "https://x.test/PK1TEST"
    assert wp.wp_flag is True
    assert wp.created_by_user is False
    assert wp.parent_gc_code == "GC1TEST"


def test_waypoint_same_prefix_name_distinct_wp_code_both_imported(db_session, tmp_path):
    # Issue #536: two waypoints under one cache sharing prefix+name but
    # distinct cCode must BOTH be imported now (they were previously
    # incorrectly treated as duplicates and one silently dropped).
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        waypoints=[
            {"cParent": "GC1TEST", "cCode": "RP1TEST", "cPrefix": "RP",
             "cName": "Right turn", "cType": "Reference Point",
             "cLat": "55.58", "cLon": "11.17", "cByuser": 0, "cDate": "", "cFlag": 0},
            {"cParent": "GC1TEST", "cCode": "RP1TEST-2", "cPrefix": "RP",
             "cName": "Right turn", "cType": "Reference Point",
             "cLat": "55.581", "cLon": "11.171", "cByuser": 0, "cDate": "", "cFlag": 0},
        ],
    )
    result = import_gsak_db(db, db_session)
    assert result.created == 1
    assert result.errors == []
    assert result.waypoints == 2
    assert result.warnings == []

    wps = db_session.query(Waypoint).order_by(Waypoint.wp_code).all()
    assert [w.wp_code for w in wps] == ["RP1TEST", "RP1TEST-2"]
    assert all(w.prefix == "RP" and w.name == "Right turn" for w in wps)


def test_waypoint_duplicate_wp_code_is_dropped_not_fatal(db_session, tmp_path):
    # A genuine repeated wp_code on one cache (shouldn't normally happen in a
    # real GSAK database, but defensively handled) is still dropped rather
    # than crashing the cache's import or violating the DB constraint.
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        waypoints=[
            {"cParent": "GC1TEST", "cCode": "PK1TEST", "cPrefix": "PK",
             "cName": "Parking", "cType": "Parking Area",
             "cLat": "55.58", "cLon": "11.17", "cByuser": 0, "cDate": "", "cFlag": 0},
            {"cParent": "GC1TEST", "cCode": "PK1TEST", "cPrefix": "PK",
             "cName": "Parking (alt)", "cType": "Parking Area",
             "cLat": "55.582", "cLon": "11.172", "cByuser": 0, "cDate": "", "cFlag": 0},
        ],
    )
    result = import_gsak_db(db, db_session)
    assert result.created == 1
    assert result.errors == []
    assert result.waypoints == 1
    assert any("dropped duplicate waypoint" in w for w in result.warnings)

    wps = db_session.query(Waypoint).all()
    assert len(wps) == 1
    assert wps[0].name == "Parking"  # first one (by cCode order) wins


def test_waymemo_missing_row_does_not_drop_waypoint(db_session, tmp_path):
    # Waypoints/WayMemo row counts can drift slightly in real GSAK databases
    # (seen: 3592 vs 3587 on a real 12,600-cache DB) — a LEFT JOIN miss must
    # not silently lose the waypoint itself.
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        waypoints=[{
            "cParent": "GC1TEST", "cCode": "PK1TEST", "cPrefix": "PK",
            "cName": "Parking", "cType": "Parking Area",
            "cLat": "55.58", "cLon": "11.17",
            "cByuser": 0, "cDate": "", "cFlag": 0,
        }],
        waymemos=[],  # deliberately missing
    )
    result = import_gsak_db(db, db_session)
    assert result.waypoints == 1
    wp = db_session.query(Waypoint).one()
    assert wp.comment is None
    assert wp.url is None


# ── Attributes ────────────────────────────────────────────────────────────────

def test_attribute_mapping_resolves_names(db_session, tmp_path):
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        attributes=[
            {"aCode": "GC1TEST", "aId": 1, "aInc": 1},   # Dogs, positive
            {"aCode": "GC1TEST", "aId": 14, "aInc": 0},  # Recommended at night, negative
        ],
    )
    result = import_gsak_db(db, db_session)
    assert result.attributes == 2

    attrs = {a.attribute_id: a for a in db_session.query(Attribute).all()}
    assert attrs[1].is_on is True
    assert attrs[1].name  # resolved to a real name, not just str(id)
    assert attrs[1].name != "1"
    assert attrs[14].is_on is False


# ── Corrected coordinates ─────────────────────────────────────────────────────

def test_corrected_coordinates_populate_user_note(db_session, tmp_path):
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        corrected=[{
            "kCode": "GC1TEST",
            "kBeforeLat": "55.58", "kBeforeLon": "11.17",
            "kAfterLat": "55.6001", "kAfterLon": "11.2002",
        }],
    )
    result = import_gsak_db(db, db_session)
    assert result.corrected == 1

    note = db_session.query(UserNote).one()
    assert note.corrected_lat == pytest.approx(55.6001)
    assert note.corrected_lon == pytest.approx(11.2002)
    assert note.is_corrected is True
    assert note.note is None  # personal note text is session 3 scope

    # Issue #614: the primary cache.latitude/longitude must reflect the
    # true original/posted position (kBeforeLat/kBeforeLon), NOT GSAK's
    # own Caches.Latitude/Longitude — which reflects the *corrected*
    # position once a cache is solved (default fixture Latitude/Longitude
    # is 55.5802/11.175917, deliberately different from kBeforeLat/Lon here
    # to make sure the override is actually happening).
    cache = db_session.query(Cache).one()
    assert cache.latitude == pytest.approx(55.58)
    assert cache.longitude == pytest.approx(11.17)


def test_corrected_coordinates_without_kbefore_keeps_gsak_latitude(db_session, tmp_path):
    """If a Corrected row has no usable kBeforeLat/kBeforeLon (e.g. blank,
    matching a real-world GSAK inconsistency we found in testing), fall back
    to whatever GSAK's own Caches.Latitude/Longitude holds rather than
    dropping the coordinate entirely."""
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        corrected=[{
            "kCode": "GC1TEST",
            "kBeforeLat": "", "kBeforeLon": "",
            "kAfterLat": "55.6001", "kAfterLon": "11.2002",
        }],
    )
    result = import_gsak_db(db, db_session)
    assert result.corrected == 1

    cache = db_session.query(Cache).one()
    assert cache.latitude == pytest.approx(55.5802)   # default fixture Latitude
    assert cache.longitude == pytest.approx(11.175917)  # default fixture Longitude


# ── Idempotency / re-import ───────────────────────────────────────────────────

def test_reimport_updates_not_duplicates(db_session, tmp_path):
    db = _make_gsak_db(tmp_path / "gsak.db3")
    import_gsak_db(db, db_session)
    result = import_gsak_db(db, db_session)
    assert result.created == 0
    assert result.updated == 1
    assert db_session.query(Cache).count() == 1


# ── Issue #538: trackables ────────────────────────────────────────────────────

def test_parse_gsak_trackables_single_line():
    assert _parse_gsak_trackables("Best TB ever (id = 1234567, ref = TBAB12CD)") == [
        {"name": "Best TB ever", "ref": "TBAB12CD", "tracking_code": "1234567"},
    ]


def test_parse_gsak_trackables_multiple_lines():
    raw = (
        "Best TB ever (id = 1234567, ref = TBAB12CD)\n"
        "Another Bug (id = 42, ref = TB999X)\n"
    )
    parsed = _parse_gsak_trackables(raw)
    assert parsed == [
        {"name": "Best TB ever", "ref": "TBAB12CD", "tracking_code": "1234567"},
        {"name": "Another Bug", "ref": "TB999X", "tracking_code": "42"},
    ]


def test_parse_gsak_trackables_empty_or_none():
    assert _parse_gsak_trackables(None) == []
    assert _parse_gsak_trackables("") == []
    assert _parse_gsak_trackables("   \n   ") == []


def test_parse_gsak_trackables_skips_unmatched_lines_without_crashing():
    # A line that doesn't match the expected "(id = ..., ref = ...)" suffix
    # (e.g. free-text GSAK "Custom" trackable entries) is skipped rather than
    # aborting the whole cache's trackable list.
    raw = (
        "Best TB ever (id = 1234567, ref = TBAB12CD)\n"
        "some unrelated free-text line\n"
        "Another Bug (id = 42, ref = TB999X)\n"
    )
    parsed = _parse_gsak_trackables(raw)
    assert [p["ref"] for p in parsed] == ["TBAB12CD", "TB999X"]


def test_trackable_mapping_via_import(db_session, tmp_path):
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        memos=[{
            "Code": "GC1TEST",
            "TravelBugs": "Best TB ever (id = 1234567, ref = TBAB12CD)",
        }],
    )
    import_gsak_db(db, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()

    trackables = db_session.query(Trackable).filter_by(cache_id=cache.id).all()
    assert len(trackables) == 1
    assert trackables[0].name == "Best TB ever"
    assert trackables[0].ref == "TBAB12CD"
    assert trackables[0].tracking_code == "1234567"
    assert cache.trackable_count == 1


def test_no_travelbugs_field_creates_no_trackables(db_session, tmp_path):
    db = _make_gsak_db(tmp_path / "gsak.db3")  # default memo has no TravelBugs
    import_gsak_db(db, db_session)
    assert db_session.query(Trackable).count() == 0


def test_reimport_rebuilds_trackables_not_duplicates(db_session, tmp_path):
    # Same rebuild-on-reimport pattern as Waypoints/Attributes/Logs (#538):
    # importing the same GSAK database twice must not duplicate trackables.
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        memos=[{
            "Code": "GC1TEST",
            "TravelBugs": "Best TB ever (id = 1234567, ref = TBAB12CD)",
        }],
    )
    import_gsak_db(db, db_session)
    import_gsak_db(db, db_session)
    assert db_session.query(Trackable).count() == 1


def test_reimport_overwrites_trackables_from_earlier_gpx_import(db_session, tmp_path):
    # Deliberate #538 behaviour change: unlike the earlier "leave Trackables
    # completely untouched" design, a GSAK re-import now rebuilds Trackables
    # the same way it rebuilds Waypoints/Attributes/Logs — so a trackable
    # that came from an earlier *GPX* import of the same cache is replaced
    # by whatever the GSAK source currently has (here: nothing, since this
    # GSAK memo has no TravelBugs data).
    db = _make_gsak_db(tmp_path / "gsak.db3")
    import_gsak_db(db, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    db_session.add(Trackable(cache=cache, ref="TB123", name="Some Travel Bug"))
    db_session.commit()

    import_gsak_db(db, db_session)
    assert db_session.query(Trackable).count() == 0


def test_premium_flag_imported_from_gsak_db(db_session, tmp_path):
    # Issue #541: GSAK's own $d_IsPremium column ("Geocaching.com member
    # only cache status") was never read by the direct-DB importer, so
    # premium caches always came in as premium_only=False regardless of
    # what the source GSAK database said.
    db = _make_gsak_db(tmp_path / "gsak.db3", caches=[{"IsPremium": 1}])
    import_gsak_db(db, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    assert cache.premium_only is True


def test_non_premium_cache_imported_as_not_premium(db_session, tmp_path):
    db = _make_gsak_db(tmp_path / "gsak.db3", caches=[{"IsPremium": 0}])
    import_gsak_db(db, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    assert cache.premium_only is False


def test_reimport_updates_premium_flag_not_locked(db_session, tmp_path):
    # premium_only is a personal/status field (mirrors the GPX importer's
    # treatment of gsak:IsPremium) — it must update on re-import even
    # though it isn't in the locked-listing-data field group.
    db = _make_gsak_db(tmp_path / "gsak.db3", caches=[{"IsPremium": 0}])
    import_gsak_db(db, db_session)

    db2 = _make_gsak_db(tmp_path / "gsak2.db3", caches=[{"IsPremium": 1}])
    import_gsak_db(db2, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    assert cache.premium_only is True


def test_locked_cache_is_not_overwritten(db_session, tmp_path):
    db = _make_gsak_db(tmp_path / "gsak.db3", caches=[{"Name": "Original Name"}])
    import_gsak_db(db, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    cache.locked = True
    db_session.commit()

    db2 = _make_gsak_db(tmp_path / "gsak2.db3", caches=[{"Name": "Changed Name"}])
    import_gsak_db(db2, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    assert cache.name == "Original Name"


# ── Error handling ─────────────────────────────────────────────────────────────

def test_missing_db_file_reports_error(db_session, tmp_path):
    result = import_gsak_db(tmp_path / "does_not_exist.db3", db_session)
    assert result.errors
    assert result.created == 0


def test_row_with_missing_coordinates_is_skipped(db_session, tmp_path):
    db = _make_gsak_db(tmp_path / "gsak.db3", caches=[{"Latitude": "", "Longitude": ""}])
    result = import_gsak_db(db, db_session)
    assert result.skipped == 1
    assert result.created == 0


# ── Logs (session 2) ──────────────────────────────────────────────────────────

def test_log_mapping(db_session, tmp_path):
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        logs=[{
            "lParent": "GC1TEST", "lLogId": 123456, "lType": "Found it",
            "lBy": "Someone", "lDate": "2024-03-01", "lTime": "14:30:00",
            "lLat": "", "lLon": "", "lEncoded": 0,
            "lownerid": 999, "lHasHtml": 0, "lIsowner": 0,
        }],
        logmemos=[{"lParent": "GC1TEST", "lLogId": 123456, "lText": "Nice find!"}],
    )
    result = import_gsak_db(db, db_session)
    assert result.logs == 1

    log = db_session.query(Log).one()
    assert log.log_id == "GC1TEST_123456"
    assert log.log_type == "Found it"
    assert log.finder == "Someone"
    assert log.finder_id == "999"
    assert log.text == "Nice find!"
    assert log.text_encoded is False
    assert log.logged_by_owner is False
    assert log.log_date.year == 2024 and log.log_date.hour == 14 and log.log_date.minute == 30

    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    assert cache.log_count == 1
    assert cache.last_log_date == log.log_date


def test_log_id_uniqueness_across_caches_with_same_gsak_log_id(db_session, tmp_path):
    # Real-world edge case found during #469 testing: GSAK's own lLogId is
    # NOT globally unique — the same lLogId can appear on many different
    # caches (e.g. a power-trail run logged on one day). Our log_id is built
    # as f"{lParent}_{lLogId}" specifically to stay unique across the whole
    # database despite this.
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        caches=[{"Code": "GC1TEST"}, {"Code": "GC2TEST"}],
        memos=[{"Code": "GC1TEST"}, {"Code": "GC2TEST"}],
        logs=[
            {"lParent": "GC1TEST", "lLogId": 42, "lType": "Found it",
             "lBy": "X", "lDate": "2024-01-01", "lTime": "", "lLat": "", "lLon": "",
             "lEncoded": 0, "lownerid": 1, "lHasHtml": 0, "lIsowner": 0},
            {"lParent": "GC2TEST", "lLogId": 42, "lType": "Found it",
             "lBy": "X", "lDate": "2024-01-01", "lTime": "", "lLat": "", "lLon": "",
             "lEncoded": 0, "lownerid": 1, "lHasHtml": 0, "lIsowner": 0},
        ],
    )
    result = import_gsak_db(db, db_session)
    assert result.errors == []
    assert result.logs == 2
    log_ids = {lg.log_id for lg in db_session.query(Log).all()}
    assert log_ids == {"GC1TEST_42", "GC2TEST_42"}


def test_log_owner_and_coordinates(db_session, tmp_path):
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        logs=[{
            "lParent": "GC1TEST", "lLogId": 1, "lType": "Update Coordinates",
            "lBy": "Owner", "lDate": "2024-01-01", "lTime": "",
            "lLat": "55.6001", "lLon": "11.2002", "lEncoded": 0,
            "lownerid": 1, "lHasHtml": 0, "lIsowner": 1,
        }],
    )
    import_gsak_db(db, db_session)
    log = db_session.query(Log).one()
    assert log.logged_by_owner is True
    assert log.latitude == pytest.approx(55.6001)
    assert log.longitude == pytest.approx(11.2002)


def test_logmemo_missing_row_does_not_drop_log(db_session, tmp_path):
    # Mirrors the Waypoints/WayMemo drift check — Logs/LogMemo can drift by
    # a row or two in real GSAK databases (seen: 1,123,992 vs 1,123,991 on
    # a real 12,600-cache DB).
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        logs=[{
            "lParent": "GC1TEST", "lLogId": 1, "lType": "Write note",
            "lBy": "X", "lDate": "", "lTime": "", "lLat": "", "lLon": "",
            "lEncoded": 0, "lownerid": 1, "lHasHtml": 0, "lIsowner": 0,
        }],
        logmemos=[],  # deliberately missing
    )
    result = import_gsak_db(db, db_session)
    assert result.logs == 1
    log = db_session.query(Log).one()
    assert log.text is None


def test_reimport_rebuilds_logs_not_duplicates(db_session, tmp_path):
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        logs=[{
            "lParent": "GC1TEST", "lLogId": 1, "lType": "Found it",
            "lBy": "X", "lDate": "2024-01-01", "lTime": "", "lLat": "", "lLon": "",
            "lEncoded": 0, "lownerid": 1, "lHasHtml": 0, "lIsowner": 0,
        }],
    )
    import_gsak_db(db, db_session)
    result = import_gsak_db(db, db_session)
    assert result.logs == 1
    assert db_session.query(Log).count() == 1


# ── Personal notes / embedded images (session 3, #472) ───────────────────────

def test_replace_embedded_images_with_placeholders():
    raw = (
        '~~GeocacheImages~~\r\n\r\nnull\r\n'
        '<img src="file:///C:\\Users\\Bob\\AppData\\Roaming\\gsak87\\UserImages\\'
        'GeocacheImages\\GC3KMD2-null.jpg" width=600 alt="null" title="null">\r\n'
        '~~GeocacheImages~~'
    )
    result, count = _replace_embedded_images_with_placeholders(raw)
    assert count == 1
    assert "[image: GC3KMD2-null.jpg]" in result
    assert "file:///" not in result
    assert "C:\\Users\\Bob" not in result  # local path not leaked
    assert "~~GeocacheImages~~" in result  # untouched surrounding text preserved


def test_replace_embedded_images_multiple_in_one_note():
    raw = (
        '<img src="file:///C:\\a\\one.jpg" width=600 alt="" title="">\r\n'
        'some caption text\r\n'
        '<img src="file:///C:\\a\\two.jpg" width=600 alt="" title="">'
    )
    result, count = _replace_embedded_images_with_placeholders(raw)
    assert count == 2
    assert "[image: one.jpg]" in result
    assert "[image: two.jpg]" in result
    assert "some caption text" in result


def test_replace_embedded_images_no_match_returns_unchanged():
    raw = "Just a plain personal note, nothing special."
    result, count = _replace_embedded_images_with_placeholders(raw)
    assert count == 0
    assert result == raw


def test_scan_gsak_notes_for_embedded_images(tmp_path):
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        memos=[{
            "Code": "GC1TEST",
            "UserNote": '<img src="file:///C:\\a\\one.jpg" width=600 alt="" title="">'
                        '<img src="file:///C:\\a\\two.jpg" width=600 alt="" title="">',
        }],
    )
    stats = scan_gsak_notes_for_embedded_images(db)
    assert stats == {"affected_notes": 1, "total_images": 2}


def test_scan_gsak_notes_no_images(tmp_path):
    db = _make_gsak_db(tmp_path / "gsak.db3")
    stats = scan_gsak_notes_for_embedded_images(db)
    assert stats == {"affected_notes": 0, "total_images": 0}


def test_note_text_imported_with_placeholder(db_session, tmp_path):
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        memos=[{
            "Code": "GC1TEST",
            "UserNote": 'Great cache!\r\n'
                        '<img src="file:///C:\\a\\photo.jpg" width=600 alt="" title="">',
        }],
    )
    result = import_gsak_db(db, db_session)
    assert result.notes == 1
    assert result.note_images_replaced == 1

    note = db_session.query(UserNote).one()
    assert "Great cache!" in note.note
    assert "[image: photo.jpg]" in note.note
    assert "file:///" not in note.note


def test_note_created_without_corrected_coords(db_session, tmp_path):
    # Real-world finding: the vast majority of notes (2,348 of 2,446 on a
    # real database) have text but NO corrected coordinates. UserNote must
    # be created for note text alone, not only when Corrected has a row.
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        memos=[{"Code": "GC1TEST", "UserNote": "Just a plain personal note."}],
    )
    result = import_gsak_db(db, db_session)
    assert result.notes == 1

    note = db_session.query(UserNote).one()
    assert note.note == "Just a plain personal note."
    assert note.is_corrected is False
    assert note.corrected_lat is None


def test_note_and_corrected_coords_together(db_session, tmp_path):
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        memos=[{"Code": "GC1TEST", "UserNote": "Solved it after a while."}],
        corrected=[{
            "kCode": "GC1TEST",
            "kBeforeLat": "55.58", "kBeforeLon": "11.17",
            "kAfterLat": "55.6001", "kAfterLon": "11.2002",
        }],
    )
    result = import_gsak_db(db, db_session)
    assert result.notes == 1
    assert result.corrected == 1

    note = db_session.query(UserNote).one()
    assert note.note == "Solved it after a while."
    assert note.is_corrected is True
    assert note.corrected_lat == pytest.approx(55.6001)


def test_empty_user_note_does_not_create_row(db_session, tmp_path):
    db = _make_gsak_db(tmp_path / "gsak.db3")  # default memo has no UserNote
    result = import_gsak_db(db, db_session)
    assert result.notes == 0
    assert db_session.query(UserNote).count() == 0


def test_reimport_overwrites_note_text(db_session, tmp_path):
    # Per Allan's decision: no "protect existing note" merge logic — a
    # GSAK re-import always overwrites note text, same as
    # Waypoints/Attributes/Logs, since this is mostly a one-time migration
    # rather than a repeated two-way sync.
    db1 = _make_gsak_db(
        tmp_path / "gsak1.db3",
        memos=[{"Code": "GC1TEST", "UserNote": "Old note text."}],
    )
    import_gsak_db(db1, db_session)

    db2 = _make_gsak_db(
        tmp_path / "gsak2.db3",
        memos=[{"Code": "GC1TEST", "UserNote": "New note text."}],
    )
    import_gsak_db(db2, db_session)

    note = db_session.query(UserNote).one()
    assert note.note == "New note text."


# ── found_log_count (issue #552) ──────────────────────────────────────────────
# Reproduces Mike Wood's report: GCCF79, a relocatable cache he found 25 times,
# only counted as 1 in the footer's Found total because Cache.found is a
# boolean. GSAK databases hold full log history (unlike a PQ's 5-log window),
# so this is the most reliable source for the "found N times" count.

def test_gsak_import_found_log_count_counts_relocatable_cache(db_session, tmp_path):
    from opensak.gui.settings import get_settings
    get_settings().gc_finder_id = "1768915"  # matches _DEFAULT_CACHE's OwnerId,
    # reused here purely as a realistic-looking numeric id for the finder.

    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        caches=[{"Found": 1}],
        logs=[
            {"lParent": "GC1TEST", "lLogId": 1, "lType": "Found it",
             "lBy": "AB Green", "lDate": "2020-01-01", "lTime": "", "lLat": "", "lLon": "",
             "lEncoded": 0, "lownerid": 1768915, "lHasHtml": 0, "lIsowner": 0},
            {"lParent": "GC1TEST", "lLogId": 2, "lType": "Found it",
             "lBy": "AB Green", "lDate": "2021-01-01", "lTime": "", "lLat": "", "lLon": "",
             "lEncoded": 0, "lownerid": 1768915, "lHasHtml": 0, "lIsowner": 0},
            {"lParent": "GC1TEST", "lLogId": 3, "lType": "Found it",
             "lBy": "AB Green", "lDate": "2022-01-01", "lTime": "", "lLat": "", "lLon": "",
             "lEncoded": 0, "lownerid": 1768915, "lHasHtml": 0, "lIsowner": 0},
        ],
    )
    import_gsak_db(db, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    assert cache.found is True
    assert cache.found_log_count == 3


def test_gsak_import_found_log_count_ignores_other_finders_logs(db_session, tmp_path):
    from opensak.gui.settings import get_settings
    get_settings().gc_finder_id = "1768915"

    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        caches=[{"Found": 1}],
        logs=[
            {"lParent": "GC1TEST", "lLogId": 1, "lType": "Found it",
             "lBy": "AB Green", "lDate": "2020-01-01", "lTime": "", "lLat": "", "lLon": "",
             "lEncoded": 0, "lownerid": 1768915, "lHasHtml": 0, "lIsowner": 0},
            {"lParent": "GC1TEST", "lLogId": 2, "lType": "Found it",
             "lBy": "Some Other Cacher", "lDate": "2020-06-01", "lTime": "", "lLat": "", "lLon": "",
             "lEncoded": 0, "lownerid": 99999, "lHasHtml": 0, "lIsowner": 0},
        ],
    )
    import_gsak_db(db, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    assert cache.found_log_count == 1


def test_gsak_import_found_log_count_matches_by_username_fallback(db_session, tmp_path):
    # No gc_finder_id configured — falls back to a normalized username match
    # (same fallback order as found_date/FTF derivation on the GPX path).
    from opensak.gui.settings import get_settings
    get_settings().gc_username = "AB Green"

    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        caches=[{"Found": 1}],
        logs=[
            {"lParent": "GC1TEST", "lLogId": 1, "lType": "Found it",
             "lBy": "AB Green", "lDate": "2020-01-01", "lTime": "", "lLat": "", "lLon": "",
             "lEncoded": 0, "lownerid": 1768915, "lHasHtml": 0, "lIsowner": 0},
        ],
    )
    import_gsak_db(db, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    assert cache.found_log_count == 1


def test_gsak_import_found_log_count_zero_without_username_configured(db_session, tmp_path):
    # No gc_username/gc_finder_id configured at all — found_log_count stays
    # at its default 0. mainwindow.py's footer count falls back to counting
    # the cache itself (found=True) in this case.
    db = _make_gsak_db(
        tmp_path / "gsak.db3",
        caches=[{"Found": 1}],
        logs=[
            {"lParent": "GC1TEST", "lLogId": 1, "lType": "Found it",
             "lBy": "AB Green", "lDate": "2020-01-01", "lTime": "", "lLat": "", "lLon": "",
             "lEncoded": 0, "lownerid": 1768915, "lHasHtml": 0, "lIsowner": 0},
        ],
    )
    import_gsak_db(db, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    assert cache.found is True
    assert cache.found_log_count == 0


def test_gsak_reimport_updates_found_log_count(db_session, tmp_path):
    # Re-import must recompute found_log_count, not just accumulate/keep it
    # stale (mirrors log_count/trackable_count re-import behaviour).
    from opensak.gui.settings import get_settings
    get_settings().gc_finder_id = "1768915"

    db1 = _make_gsak_db(
        tmp_path / "gsak1.db3",
        caches=[{"Found": 1}],
        logs=[
            {"lParent": "GC1TEST", "lLogId": 1, "lType": "Found it",
             "lBy": "AB Green", "lDate": "2020-01-01", "lTime": "", "lLat": "", "lLon": "",
             "lEncoded": 0, "lownerid": 1768915, "lHasHtml": 0, "lIsowner": 0},
        ],
    )
    import_gsak_db(db1, db_session)
    assert db_session.query(Cache).filter_by(gc_code="GC1TEST").one().found_log_count == 1

    db2 = _make_gsak_db(
        tmp_path / "gsak2.db3",
        caches=[{"Found": 1}],
        logs=[
            {"lParent": "GC1TEST", "lLogId": 1, "lType": "Found it",
             "lBy": "AB Green", "lDate": "2020-01-01", "lTime": "", "lLat": "", "lLon": "",
             "lEncoded": 0, "lownerid": 1768915, "lHasHtml": 0, "lIsowner": 0},
            {"lParent": "GC1TEST", "lLogId": 2, "lType": "Found it",
             "lBy": "AB Green", "lDate": "2022-01-01", "lTime": "", "lLat": "", "lLon": "",
             "lEncoded": 0, "lownerid": 1768915, "lHasHtml": 0, "lIsowner": 0},
        ],
    )
    import_gsak_db(db2, db_session)
    assert db_session.query(Cache).filter_by(gc_code="GC1TEST").one().found_log_count == 2

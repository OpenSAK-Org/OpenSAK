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

from opensak.db.models import Attribute, Cache, UserNote, Waypoint
from opensak.importer.gsak_importer import (
    GSAK_CACHE_TYPE_MAP,
    GSAK_CONTAINER_MAP,
    import_gsak_db,
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
        Guid TEXT, Watch INTEGER, CacheId TEXT, Lock INTEGER, FoundCount INTEGER
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
    CacheId="9284799", Lock=0, FoundCount=30,
)


def _make_gsak_db(
    path: Path,
    caches: list[dict] | None = None,
    memos: list[dict] | None = None,
    corrected: list[dict] | None = None,
    waypoints: list[dict] | None = None,
    waymemos: list[dict] | None = None,
    attributes: list[dict] | None = None,
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

    conn.commit()
    conn.close()
    return path


# ── Basic import ──────────────────────────────────────────────────────────────

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


def test_waypoint_duplicate_prefix_name_is_dropped_not_fatal(db_session, tmp_path):
    # Real-world edge case found during #469 testing: two waypoints under one
    # cache sharing prefix+name but distinct cCode. Must not crash the whole
    # cache's import — the second is dropped with a warning instead.
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
    assert result.waypoints == 1
    assert any("dropped duplicate waypoint" in w for w in result.warnings)

    wps = db_session.query(Waypoint).all()
    assert len(wps) == 1
    assert wps[0].wp_code == "RP1TEST"  # first one (by cCode order) wins


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


# ── Idempotency / re-import ───────────────────────────────────────────────────

def test_reimport_updates_not_duplicates(db_session, tmp_path):
    db = _make_gsak_db(tmp_path / "gsak.db3")
    import_gsak_db(db, db_session)
    result = import_gsak_db(db, db_session)
    assert result.created == 0
    assert result.updated == 1
    assert db_session.query(Cache).count() == 1


def test_reimport_does_not_touch_existing_logs(db_session, tmp_path):
    # GSAK import (session 1 scope) must never wipe Logs/Trackables that a
    # prior GPX import may have populated — those tables are entirely out
    # of scope here and are simply left alone.
    from opensak.db.models import Log

    db = _make_gsak_db(tmp_path / "gsak.db3")
    import_gsak_db(db, db_session)
    cache = db_session.query(Cache).filter_by(gc_code="GC1TEST").one()
    db_session.add(Log(cache=cache, log_type="Found it", finder="Someone"))
    db_session.commit()

    import_gsak_db(db, db_session)
    assert db_session.query(Log).count() == 1


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

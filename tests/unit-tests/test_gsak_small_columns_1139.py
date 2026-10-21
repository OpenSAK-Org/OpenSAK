# tests/unit-tests/test_gsak_small_columns_1139.py — GSAK's small columns and
# dates (#1139, #1028 step 2) and UTC storage of recorded timestamps (#1097).
#
# Time-zone dependent tests pin TZ to Europe/Copenhagen (UTC+2 in summer,
# UTC+1 in winter) so the UTC/local conversions are visible and the results
# don't depend on the machine running the tests.

import sqlite3
import time
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from opensak.db.database import SCHEMA_VERSION, _run_migrations, get_engine, init_db
from opensak.db.models import Cache, UserNote
from opensak.filters.engine import DateFilter
from opensak.importer.gsak_importer import import_gsak_db
from opensak.utils.timestamps import local_date, local_to_utc, utc_now, utc_to_local


@pytest.fixture
def copenhagen(monkeypatch):
    """Run the test with the local time zone set to Europe/Copenhagen."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset() is not available on this platform")
    monkeypatch.setenv("TZ", "Europe/Copenhagen")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


# ── timestamps helpers ──────────────────────────────────────────────────────

class TestTimestamps:
    def test_utc_to_local_summer_and_winter(self, copenhagen):
        assert utc_to_local(datetime(2026, 7, 1, 22, 30)) == datetime(2026, 7, 2, 0, 30)
        assert utc_to_local(datetime(2026, 1, 1, 22, 30)) == datetime(2026, 1, 1, 23, 30)

    def test_local_to_utc_is_the_inverse(self, copenhagen):
        local = datetime(2026, 7, 2, 0, 30)
        assert local_to_utc(local) == datetime(2026, 7, 1, 22, 30)
        assert utc_to_local(local_to_utc(local)) == local

    def test_none_passes_through(self):
        assert utc_to_local(None) is None
        assert local_to_utc(None) is None
        assert local_date(None) is None

    def test_extreme_values_do_not_raise(self, copenhagen):
        for value in (datetime.min, datetime.max):
            assert isinstance(utc_to_local(value), datetime)
            assert isinstance(local_to_utc(value), datetime)

    def test_local_date_crosses_midnight(self, copenhagen):
        # 23:30 UTC on 14 Sep is 01:30 on 15 Sep in Copenhagen.
        assert local_date(datetime(2026, 9, 14, 23, 30)) == date(2026, 9, 15)

    def test_utc_now_is_naive(self):
        assert utc_now().tzinfo is None


# ── migration 27 ──────────────────────────────────────────────────────────────

_NEW_CACHE_COLUMNS = ("last_user_update", "macro_flag", "macro_sort", "is_owner", "symbol")


def _make_v26_db(path: Path) -> None:
    """A current database turned back into a v26 one: the #1139 columns
    removed, one cache with a correction, user_version 26."""
    init_db(db_path=path)
    engine = get_engine()
    with engine.connect() as c:
        c.execute(text(
            "INSERT INTO caches (gc_code, name, cache_type, latitude, longitude) "
            "VALUES ('GC1MIG', 'Migrated', 'Traditional Cache', 55.0, 12.0)"
        ))
        c.execute(text(
            "INSERT INTO user_notes (cache_id, corrected_lat, corrected_lon, is_corrected) "
            "VALUES (1, 55.1, 12.1, 1)"
        ))
        for col in _NEW_CACHE_COLUMNS:
            c.execute(text(f"ALTER TABLE caches DROP COLUMN {col}"))
        c.execute(text("ALTER TABLE user_notes DROP COLUMN corrected_source"))
        c.execute(text("PRAGMA user_version = 26"))
        c.commit()


class TestMigration27:
    def test_schema_version(self):
        assert SCHEMA_VERSION >= 27

    def test_adds_columns_and_keeps_data(self, tmp_path):
        _make_v26_db(tmp_path / "v26.db")
        engine = get_engine()
        _run_migrations(engine)
        with engine.connect() as c:
            cols = {r[1] for r in c.execute(text("PRAGMA table_info(caches)"))}
            assert set(_NEW_CACHE_COLUMNS) <= cols
            note_cols = {r[1] for r in c.execute(text("PRAGMA table_info(user_notes)"))}
            assert "corrected_source" in note_cols
            row = c.execute(text(
                "SELECT name, macro_flag, is_owner, macro_sort, symbol, last_user_update "
                "FROM caches WHERE gc_code = 'GC1MIG'"
            )).one()
            assert row == ("Migrated", 0, 0, None, None, None)
            # Existing corrections stay "unknown".
            assert c.execute(text(
                "SELECT corrected_source FROM user_notes"
            )).scalar() is None
            assert c.execute(text("PRAGMA user_version")).scalar() == SCHEMA_VERSION

    def test_rerun_is_a_no_op(self, tmp_path):
        _make_v26_db(tmp_path / "v26.db")
        engine = get_engine()
        _run_migrations(engine)
        with engine.connect() as c:
            c.execute(text("PRAGMA user_version = 26"))
            c.commit()
        _run_migrations(engine)  # columns already there: must not raise
        with engine.connect() as c:
            assert c.execute(text("PRAGMA user_version")).scalar() == SCHEMA_VERSION

    def test_plain_insert_gets_defaults(self, tmp_path):
        init_db(db_path=tmp_path / "fresh.db")
        with get_engine().connect() as c:
            c.execute(text(
                "INSERT INTO caches (gc_code, name, cache_type, latitude, longitude) "
                "VALUES ('GC1NEW', 'New', 'Traditional Cache', 55.0, 12.0)"
            ))
            assert c.execute(text(
                "SELECT macro_flag, is_owner FROM caches WHERE gc_code = 'GC1NEW'"
            )).one() == (0, 0)


# ── GSAK import ───────────────────────────────────────────────────────────────

_GSAK_SCHEMA = [
    """CREATE TABLE Caches (
        Code TEXT, Name TEXT, CacheType TEXT, Container TEXT,
        Latitude TEXT, Longitude TEXT, Difficulty REAL, Terrain REAL,
        PlacedBy TEXT, OwnerName TEXT, OwnerId TEXT,
        PlacedDate TEXT, Changed TEXT, Status TEXT, Archived INTEGER,
        TempDisabled INTEGER, Country TEXT, State TEXT, County TEXT,
        Found INTEGER, FoundByMeDate TEXT, DNF INTEGER, DNFDate TEXT,
        FTF INTEGER, UserFlag INTEGER, UserSort INTEGER,
        UserData TEXT, User2 TEXT, User3 TEXT, User4 TEXT,
        FavPoints INTEGER, GcNote TEXT, Elevation REAL, Resolution TEXT,
        Color TEXT, Guid TEXT, Watch INTEGER, CacheId TEXT, Lock INTEGER,
        FoundCount INTEGER, IsPremium INTEGER, ShortHtm INTEGER, LongHtm INTEGER,
        LastUserDate TEXT, MacroFlag INTEGER, MacroSort TEXT, IsOwner INTEGER,
        Symbol TEXT, Created TEXT, LastGPXDate TEXT, UserNoteDate TEXT
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
    "CREATE TABLE WayMemo (cParent TEXT, cCode TEXT, cComment TEXT, cUrl TEXT)",
    "CREATE TABLE Attributes (aCode TEXT, aId INTEGER, aInc INTEGER)",
    """CREATE TABLE Logs (
        lParent TEXT, lLogId INTEGER, lType TEXT, lBy TEXT, lDate TEXT,
        lTime TEXT, lLat TEXT, lLon TEXT, lEncoded INTEGER,
        lownerid INTEGER, lHasHtml INTEGER, lIsowner INTEGER
    )""",
    "CREATE TABLE LogMemo (lParent TEXT, lLogId INTEGER, lText TEXT)",
]

_CACHE = dict(
    Code="GC1SMALL", Name="Small columns", CacheType="T", Container="Micro",
    Latitude="55.58", Longitude="11.17", Difficulty=1.5, Terrain=2.0,
    PlacedBy="AB Green", OwnerName="AB Green", OwnerId="1768915",
    PlacedDate="2023-10-24", Changed="2026-06-23", Status="A", Archived=0,
    TempDisabled=0, Country="Denmark", State="", County="",
    Found=0, FoundByMeDate="", DNF=0, DNFDate="", FTF=0, UserFlag=0, UserSort=0,
    UserData="", User2="", User3="", User4="", FavPoints=0, GcNote="",
    Elevation=0.0, Resolution="", Color="", Guid="", Watch=0, CacheId="1",
    Lock=0, FoundCount=0, IsPremium=0, ShortHtm=0, LongHtm=0,
    LastUserDate="2025-03-10", MacroFlag=1, MacroSort="  0042 ", IsOwner=1,
    Symbol="Geocache Found", Created="2019-07-01", LastGPXDate="2026-07-15 23:30:00",
    UserNoteDate="2024-12-24",
)


def _make_gsak_db(path: Path, cache: dict | None = None, corrected: dict | None = None,
                  note: str | None = None) -> Path:
    conn = sqlite3.connect(path)
    for ddl in _GSAK_SCHEMA:
        conn.execute(ddl)
    row = {**_CACHE, **(cache or {})}
    conn.execute(
        f"INSERT INTO Caches ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
        list(row.values()),
    )
    conn.execute(
        "INSERT INTO CacheMemo (Code, UserNote) VALUES (?, ?)", (row["Code"], note)
    )
    if corrected is not None:
        k = {"kCode": row["Code"], "kBeforeLat": "55.58", "kBeforeLon": "11.17",
             "kAfterLat": "55.6", "kAfterLon": "11.2", **corrected}
        conn.execute(
            f"INSERT INTO Corrected ({', '.join(k)}) VALUES ({', '.join('?' for _ in k)})",
            list(k.values()),
        )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def no_account(monkeypatch):
    # The importer reads gc_finder_id/gc_username for found_log_count.
    s = SimpleNamespace(gc_finder_id="", gc_username="")
    monkeypatch.setattr("opensak.gui.settings.get_settings", lambda: s)


class TestGsakImport:
    def test_small_columns_imported(self, db_session, tmp_path, copenhagen, no_account):
        import_gsak_db(_make_gsak_db(tmp_path / "g.db3"), db_session)
        cache = db_session.query(Cache).filter_by(gc_code="GC1SMALL").one()
        assert cache.macro_flag is True
        assert cache.macro_sort == "0042"
        assert cache.is_owner is True
        assert cache.symbol == "Geocache Found"
        # Date-only GSAK value = local midnight, stored as UTC (CET = UTC+1).
        assert cache.last_user_update == datetime(2025, 3, 9, 23, 0)

    def test_gsak_dates_kept_as_utc(self, db_session, tmp_path, copenhagen, no_account):
        import_gsak_db(_make_gsak_db(tmp_path / "g.db3", note="hello"), db_session)
        cache = db_session.query(Cache).filter_by(gc_code="GC1SMALL").one()
        # Created 2019-07-01 local midnight (CEST = UTC+2).
        assert cache.imported_at.replace(tzinfo=None) == datetime(2019, 6, 30, 22, 0)
        # LastGPXDate with a time: 23:30 local is 21:30 UTC.
        assert cache.last_gpx_update.replace(tzinfo=None) == datetime(2026, 7, 15, 21, 30)
        assert cache.user_note.updated_at.replace(tzinfo=None) == datetime(2024, 12, 23, 23, 0)
        # Shown as the GSAK day again.
        assert local_date(cache.last_gpx_update.replace(tzinfo=None)) == date(2026, 7, 15)

    def test_missing_gsak_dates_fall_back_to_import_time(
            self, db_session, tmp_path, no_account):
        before = utc_now()
        db = _make_gsak_db(tmp_path / "g.db3",
                           cache={"Created": "", "LastGPXDate": "", "LastUserDate": ""})
        import_gsak_db(db, db_session)
        cache = db_session.query(Cache).filter_by(gc_code="GC1SMALL").one()
        after = utc_now()
        assert before <= cache.last_gpx_update.replace(tzinfo=None) <= after
        assert before <= cache.imported_at.replace(tzinfo=None) <= after
        assert cache.last_user_update is None

    def test_reimport_keeps_the_earlier_imported_at(self, db_session, tmp_path, no_account):
        import_gsak_db(_make_gsak_db(tmp_path / "a.db3"), db_session)
        first = db_session.query(Cache).filter_by(gc_code="GC1SMALL").one().imported_at
        import_gsak_db(_make_gsak_db(tmp_path / "b.db3", cache={"Created": "2023-01-01"}),
                       db_session)
        cache = db_session.query(Cache).filter_by(gc_code="GC1SMALL").one()
        assert cache.imported_at == first

    @pytest.mark.parametrize("ktype, expected", [
        ("gui", "user"), ("macro", "macro"), ("api", "import"),
        ("GUI", "user"), ("", None), ("something", None),
    ])
    def test_corrected_source_from_ktype(self, db_session, tmp_path, no_account, ktype, expected):
        db = _make_gsak_db(tmp_path / "g.db3", corrected={"kType": ktype})
        import_gsak_db(db, db_session)
        note = db_session.query(UserNote).one()
        assert note.is_corrected is True
        assert note.corrected_source == expected

    def test_database_without_the_new_columns(self, db_session, tmp_path, no_account):
        # Older/partial GSAK databases: the new columns are just absent.
        db = _make_gsak_db(tmp_path / "g.db3", corrected={"kType": "gui"})
        conn = sqlite3.connect(db)
        for col in ("LastUserDate", "MacroFlag", "MacroSort", "IsOwner", "Symbol",
                    "Created", "LastGPXDate", "UserNoteDate"):
            conn.execute(f"ALTER TABLE Caches DROP COLUMN {col}")
        conn.execute("ALTER TABLE Corrected DROP COLUMN kType")
        conn.commit()
        conn.close()
        result = import_gsak_db(db, db_session)
        assert result.errors == []
        cache = db_session.query(Cache).filter_by(gc_code="GC1SMALL").one()
        assert (cache.macro_flag, cache.is_owner, cache.macro_sort, cache.symbol) == (
            False, False, None, None)
        assert cache.last_gpx_update is not None
        assert db_session.query(UserNote).one().corrected_source is None


# ── date filters on recorded timestamps (#1097) ───────────────────────────────

def _cache(**kw):
    return SimpleNamespace(**{"imported_at": None, "last_gpx_update": None,
                              "hidden_date": None, **kw})


class TestUtcDateFilters:
    def test_equal_uses_the_local_day(self, copenhagen):
        f = DateFilter("last_gpx_update", "equal", date1=date(2026, 9, 15))
        # 23:30 UTC on 14 Sep = 01:30 on 15 Sep local.
        assert f.matches(_cache(last_gpx_update=datetime(2026, 9, 14, 23, 30)))
        assert not f.matches(_cache(last_gpx_update=datetime(2026, 9, 15, 22, 30)))

    def test_sql_matches_python(self, db_session, copenhagen, make_cache):
        inside = make_cache("GC1IN", last_gpx_update=datetime(2026, 9, 14, 23, 30))
        outside = make_cache("GC1OUT", last_gpx_update=datetime(2026, 9, 15, 22, 30))
        db_session.add_all([inside, outside])
        db_session.commit()
        f = DateFilter("last_gpx_update", "equal", date1=date(2026, 9, 15))
        sql = {c.gc_code for c in f.apply_to_query(db_session.query(Cache)).all()}
        assert sql == {"GC1IN"}
        assert {c.gc_code for c in (inside, outside) if f.matches(c)} == sql

    def test_compare_uses_the_local_day(self, db_session, copenhagen, make_cache):
        # Imported 01:30 local on 15 Sep, placed 15 Sep: the same day.
        c = make_cache("GC1CMP", imported_at=datetime(2026, 9, 14, 23, 30),
                       hidden_date=datetime(2026, 9, 15))
        db_session.add(c)
        db_session.commit()
        f = DateFilter("creation_date", "compare", other_field="hidden_date",
                       compare_op="equal")
        assert f.matches(c)
        assert f.apply_to_query(db_session.query(Cache)).count() == 1

    def test_listing_dates_are_not_converted(self, copenhagen):
        f = DateFilter("hidden_date", "equal", date1=date(2026, 9, 14))
        assert f.matches(_cache(hidden_date=datetime(2026, 9, 14, 23, 30)))


# ── cache list display (#1097) ──────────────────────────────────────────────────

def test_cache_list_shows_local_day(monkeypatch, copenhagen, qapp):
    from opensak.gui import cache_table as ct
    from opensak.utils.types import DateFormat

    s = SimpleNamespace(date_format=DateFormat.YMD, use_miles=False,
                        home_lat=None, home_lon=None)
    monkeypatch.setattr(ct, "get_settings", lambda: s)
    monkeypatch.setattr(ct, "_get_active_columns", lambda: ["gc_code", "last_gpx_update"])
    model = ct.CacheTableModel()
    cache = Cache(gc_code="GC1DSP", name="Display")
    cache.last_gpx_update = datetime(2026, 9, 14, 23, 30)
    cache.imported_at = datetime(2026, 9, 14, 23, 30)
    expected = ct.format_date(date(2026, 9, 15), s.date_format)
    assert model._display_value(cache, "last_gpx_update") == expected
    assert model._display_value(cache, "creation_date") == expected
    assert expected == "2026-09-15"

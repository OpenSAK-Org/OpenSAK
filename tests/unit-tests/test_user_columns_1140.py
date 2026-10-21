# tests/unit-tests/test_user_columns_1140.py — OpenSAK keeps the #1139
# columns up to date itself (#1140, #1028 step 2b): the last_user_update
# triggers, is_owner, symbol and corrected_source.

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from opensak.db.database import SCHEMA_VERSION, _run_migrations, get_engine, init_db, session_for
from opensak.db.models import Cache, UserNote
from opensak.db.ownership import (
    account_change_needs_recalc,
    account_changed,
    owner_matches,
    recalculate_is_owner,
    recalculate_is_owner_everywhere,
)
from opensak.utils.timestamps import utc_now
from tests.data import SAMPLE_GPX, make_loc, write_gpx

_OLD = datetime(2020, 1, 1, 12, 0)


def _gsak_builder():
    """The synthetic GSAK database builder from the #1139 tests."""
    import importlib
    return importlib.import_module("tests.unit-tests.test_gsak_small_columns_1139")._make_gsak_db


def _add(session, code="GC1USR", **kw) -> Cache:
    cache = Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                  latitude=55.0, longitude=12.0, **kw)
    session.add(cache)
    session.commit()
    return cache


def _stamp(code="GC1USR") -> datetime | None:
    """last_user_update as stored, read straight from the database."""
    with get_engine().connect() as c:
        raw = c.execute(text("SELECT last_user_update FROM caches WHERE gc_code = :c"),
                        {"c": code}).scalar()
    return datetime.fromisoformat(raw) if raw else None


def _set_old(code="GC1USR") -> None:
    with get_engine().begin() as c:
        c.execute(text("UPDATE caches SET last_user_update = :d WHERE gc_code = :c"),
                  {"d": _OLD.isoformat(" "), "c": code})


def _is_recent(value: datetime | None) -> bool:
    return value is not None and abs(utc_now() - value) < timedelta(minutes=1)


# ── Triggers ──────────────────────────────────────────────────────────────────

class TestTriggers:
    def test_schema_version(self):
        assert SCHEMA_VERSION >= 28

    def test_triggers_on_a_new_database(self, db_session):
        with get_engine().connect() as c:
            names = {r[0] for r in c.execute(text(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"))}
        assert {"trg_caches_last_user_update", "trg_user_notes_insert_last_user_update",
                "trg_user_notes_update_last_user_update"} <= names

    @pytest.mark.parametrize("field, value", [
        ("user_flag", True), ("user_sort", 7), ("user_data_1", "x"),
        ("user_data_4", "y"), ("locked", True),
    ])
    def test_orm_edit_stamps_utc_now(self, db_session, field, value):
        cache = _add(db_session)
        _set_old()
        setattr(cache, field, value)
        db_session.commit()
        assert _is_recent(_stamp())

    def test_unchanged_value_does_not_stamp(self, db_session):
        _add(db_session, user_flag=True)
        _set_old()
        with get_engine().begin() as c:
            c.execute(text("UPDATE caches SET user_flag = 1"))
        assert _stamp() == _OLD

    def test_null_and_empty_text_are_the_same(self, db_session):
        _add(db_session)
        _set_old()
        with get_engine().begin() as c:
            c.execute(text("UPDATE caches SET user_data_2 = ''"))
        assert _stamp() == _OLD

    def test_other_fields_do_not_stamp(self, db_session):
        cache = _add(db_session)
        _set_old()
        cache.name = "Renamed"
        cache.found = True
        db_session.commit()
        assert _stamp() == _OLD

    def test_explicit_date_in_the_same_statement_wins(self, db_session):
        _add(db_session)
        with get_engine().begin() as c:
            c.execute(text("UPDATE caches SET user_flag = 1, last_user_update = :d"),
                      {"d": "2019-05-05 10:00:00.000000"})
        assert _stamp() == datetime(2019, 5, 5, 10, 0)

    def test_bulk_update_stamps(self, db_session):
        # The "clear all flags" pattern: a bulk ORM update.
        _add(db_session, "GC1A", user_flag=True)
        _add(db_session, "GC1B", user_flag=False)
        _set_old("GC1A")
        _set_old("GC1B")
        db_session.query(Cache).filter(Cache.user_flag == True).update(  # noqa: E712
            {Cache.user_flag: False}, synchronize_session=False)
        db_session.commit()
        assert _is_recent(_stamp("GC1A"))
        assert _stamp("GC1B") == _OLD

    def test_note_insert_with_text_stamps(self, db_session):
        cache = _add(db_session)
        _set_old()
        db_session.add(UserNote(cache_id=cache.id, note="hello"))
        db_session.commit()
        assert _is_recent(_stamp())

    def test_empty_note_insert_does_not_stamp(self, db_session):
        cache = _add(db_session)
        _set_old()
        db_session.add(UserNote(cache_id=cache.id))
        db_session.commit()
        assert _stamp() == _OLD

    def test_note_update_stamps(self, db_session):
        cache = _add(db_session)
        note = UserNote(cache_id=cache.id, note="a")
        db_session.add(note)
        db_session.commit()
        _set_old()
        note.note = "b"
        db_session.commit()
        assert _is_recent(_stamp())

    def test_deleting_a_cache_with_a_note_works(self, db_session):
        cache = _add(db_session)
        db_session.add(UserNote(cache_id=cache.id, note="a"))
        db_session.commit()
        db_session.delete(cache)
        db_session.commit()
        assert db_session.query(Cache).count() == 0

    def test_migration_26_rebuild_keeps_the_triggers(self, tmp_path):
        # A table rebuild drops and recreates the triggers (_add_column_defaults).
        from opensak.db.database import _add_column_defaults
        init_db(db_path=tmp_path / "r.db")
        engine = get_engine()
        with engine.connect() as c:
            c.execute(text(
                "INSERT INTO caches (gc_code, name, cache_type, latitude, longitude) "
                "VALUES ('GC1R', 'R', 'Traditional Cache', 55.0, 12.0)"))
            c.commit()
            _add_column_defaults(c, {"caches": {"symbol": "'x'"}})
            names = {r[0] for r in c.execute(text(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"))}
        assert "trg_user_notes_insert_last_user_update" in names
        assert "trg_caches_last_user_update" in names


# ── The edit paths ────────────────────────────────────────────────────────────

class TestEditPaths:
    def test_corrected_coords(self, db_session):
        from opensak.db.corrected_coords import set_corrected_coords
        _add(db_session)
        _set_old()
        assert set_corrected_coords("GC1USR", 55.1, 12.1)
        assert _is_recent(_stamp())
        with session_for(Path(get_engine().url.database)) as s:
            note = s.query(UserNote).one()
            assert note.corrected_source == "user"
        _set_old()
        assert set_corrected_coords("GC1USR", None, None)
        assert _is_recent(_stamp())
        with session_for(Path(get_engine().url.database)) as s:
            assert s.query(UserNote).one().corrected_source is None

    def test_corrected_coords_from_a_macro(self, db_session):
        from opensak.db.corrected_coords import set_corrected_coords
        _add(db_session)
        set_corrected_coords("GC1USR", 55.1, 12.1, source="macro")
        with session_for(Path(get_engine().url.database)) as s:
            assert s.query(UserNote).one().corrected_source == "macro"

    def test_macro_update(self, db_session):
        from opensak.macro.cache_write import parse_values, update_cache
        _add(db_session)
        _set_old()
        values = parse_values("update", {"user_flag": True, "corrected": {"lat": 55.2, "lon": 12.2}})
        assert update_cache(db_session, "GC1USR", values)
        db_session.commit()
        assert _is_recent(_stamp())
        assert db_session.query(UserNote).one().corrected_source == "macro"

    def test_macro_sql_write(self, db_session):
        from opensak.macro.sql import WritableDatabase
        _add(db_session)
        _set_old()
        db = WritableDatabase(Path(get_engine().url.database))
        try:
            db.execute("UPDATE caches SET user_data_3 = 'from a macro'")
        finally:
            db.close()
        assert _is_recent(_stamp())

    def test_edit_dialog_keeps_the_source_when_coords_are_untouched(self, db_session):
        from types import SimpleNamespace
        from opensak.gui.dialogs.waypoint_dialog import WaypointDialog
        cache = _add(db_session)
        db_session.add(UserNote(cache_id=cache.id, corrected_lat=55.1, corrected_lon=12.1,
                                is_corrected=True, corrected_source="macro"))
        db_session.commit()
        fake = SimpleNamespace(get_user_note_data=lambda: {
            "note": "text", "corrected_lat": 55.1, "corrected_lon": 12.1})
        WaypointDialog._save_user_note(fake, db_session, cache)
        db_session.commit()
        assert cache.user_note.corrected_source == "macro"
        fake2 = SimpleNamespace(get_user_note_data=lambda: {
            "note": "text", "corrected_lat": 55.3, "corrected_lon": 12.3})
        WaypointDialog._save_user_note(fake2, db_session, cache)
        db_session.commit()
        assert cache.user_note.corrected_source == "user"


# ── Imports and copies ────────────────────────────────────────────────────────

class TestImports:
    def test_gpx_reimport_does_not_stamp(self, db_session, tmp_path):
        from opensak.importer import import_gpx
        gpx = write_gpx(tmp_path, "a.gpx", SAMPLE_GPX)
        import_gpx(gpx, db_session)
        _set_old("GC12345")
        import_gpx(gpx, db_session)
        assert _stamp("GC12345") == _OLD

    def test_gsak_import_keeps_gsak_last_user_date(self, db_session, tmp_path, monkeypatch):
        from types import SimpleNamespace
        from opensak.importer.gsak_importer import import_gsak_db
        _make_gsak_db = _gsak_builder()
        monkeypatch.setattr("opensak.gui.settings.get_settings",
                            lambda: SimpleNamespace(gc_finder_id="", gc_username=""))
        db = _make_gsak_db(tmp_path / "g.db3", cache={"UserFlag": 1, "UserData": "x",
                                                     "LastUserDate": "2025-03-10 12:00:00"},
                           corrected={"kType": "gui"}, note="hello")
        import_gsak_db(db, db_session)
        expected = _stamp("GC1SMALL")
        assert expected is not None and not _is_recent(expected)
        # A second import (fields unchanged or not) still keeps GSAK's date.
        import_gsak_db(db, db_session)
        assert _stamp("GC1SMALL") == expected

    def test_gsak_import_without_last_user_date(self, db_session, tmp_path, monkeypatch):
        from types import SimpleNamespace
        from opensak.importer.gsak_importer import import_gsak_db
        _make_gsak_db = _gsak_builder()
        monkeypatch.setattr("opensak.gui.settings.get_settings",
                            lambda: SimpleNamespace(gc_finder_id="", gc_username=""))
        db = _make_gsak_db(tmp_path / "g.db3", cache={"UserFlag": 1, "LastUserDate": ""},
                           note="hello")
        import_gsak_db(db, db_session)
        assert _stamp("GC1SMALL") is None

    def test_copy_keeps_last_user_update(self, db_session, tmp_path):
        from opensak.db.transfer import transfer_caches
        source = Path(get_engine().url.database)
        cache = _add(db_session)
        db_session.add(UserNote(cache_id=cache.id, note="n"))
        db_session.commit()
        _set_old()
        target = tmp_path / "target.db"
        init_db(db_path=target)
        assert transfer_caches(["GC1USR"], source, target, copy_only=True) == 1
        with session_for(target) as s:
            copied = s.query(Cache).filter_by(gc_code="GC1USR").one()
            assert copied.last_user_update == _OLD


# ── symbol ──────────────────────────────────────────────────────────────────────

class TestSymbol:
    def test_gpx_sym_is_stored(self, db_session, tmp_path):
        from opensak.importer import import_gpx
        import_gpx(write_gpx(tmp_path, "a.gpx", SAMPLE_GPX), db_session)
        assert db_session.query(Cache).filter_by(gc_code="GC12345").one().symbol == "Geocache"

    def test_loc_without_sym_keeps_the_symbol(self, db_session, tmp_path):
        from opensak.importer import import_gpx
        import_gpx(write_gpx(tmp_path, "a.gpx", SAMPLE_GPX), db_session)
        loc = write_gpx(tmp_path, "a.loc",
                        make_loc([{"gc_code": "GC12345", "lat": 55.6761, "lon": 12.5683}]))
        import_gpx(loc, db_session)
        db_session.expire_all()
        assert db_session.query(Cache).filter_by(gc_code="GC12345").one().symbol == "Geocache"


# ── is_owner ────────────────────────────────────────────────────────────────────

class TestIsOwner:
    @pytest.mark.parametrize("owner_id, owner_name, fid, uname, expected", [
        ("12345", "Someone", "12345", "", True),
        ("1", "TestOwner", "", "testowner", True),
        ("1", "  Test   Owner (F=12 H=3)", "", "test owner", True),
        ("1", "Other", "12345", "Me", False),
        (None, None, "", "", False),
        ("12345", "Other", "", "Me", False),
    ])
    def test_owner_matches(self, owner_id, owner_name, fid, uname, expected):
        assert owner_matches(owner_id, owner_name, fid, uname) is expected

    def test_gpx_import_sets_is_owner(self, db_session, tmp_path):
        from opensak.gui.settings import get_settings
        from opensak.importer import import_gpx
        get_settings().gc_username = "TestOwner"
        import_gpx(write_gpx(tmp_path, "a.gpx", SAMPLE_GPX), db_session)
        assert db_session.query(Cache).filter_by(gc_code="GC12345").one().is_owner is True

    def test_gpx_import_without_account_leaves_is_owner(self, db_session, tmp_path):
        from opensak.importer import import_gpx
        _add(db_session, "GC12345", is_owner=True)
        import_gpx(write_gpx(tmp_path, "a.gpx", SAMPLE_GPX), db_session)
        db_session.expire_all()
        assert db_session.query(Cache).filter_by(gc_code="GC12345").one().is_owner is True

    def test_recalculate_in_one_database(self, db_session):
        _add(db_session, "GC1MINE", owner_name="Me", is_owner=False)
        _add(db_session, "GC1GSAK", owner_name="Someone", is_owner=True)
        with get_engine().begin() as c:
            assert recalculate_is_owner(c, "", "me") == 2
        db_session.expire_all()
        assert db_session.query(Cache).filter_by(gc_code="GC1MINE").one().is_owner is True
        assert db_session.query(Cache).filter_by(gc_code="GC1GSAK").one().is_owner is False

    def test_no_account_changes_nothing(self, db_session):
        _add(db_session, "GC1GSAK", owner_name="Someone", is_owner=True)
        with get_engine().begin() as c:
            assert recalculate_is_owner(c, "", "") == 0

    def test_recalculate_everywhere_skips_missing_files(self, tmp_path):
        a, b = tmp_path / "a.db", tmp_path / "b.db"
        for path in (a, b):
            init_db(db_path=path)
            with get_engine().begin() as c:
                c.execute(text(
                    "INSERT INTO caches (gc_code, name, cache_type, latitude, longitude, owner_id) "
                    "VALUES ('GC1X', 'X', 'Traditional Cache', 55.0, 12.0, '42')"))
        result = recalculate_is_owner_everywhere([a, b, tmp_path / "missing.db"], "42", "")
        assert result == {a: 1, b: 1}
        for path in (a, b):
            con = sqlite3.connect(path)
            assert con.execute("SELECT is_owner FROM caches").fetchone()[0] == 1
            con.close()

    def test_account_changed(self, tmp_path, monkeypatch):
        from types import SimpleNamespace
        from opensak.gui.settings import get_settings
        path = tmp_path / "a.db"
        init_db(db_path=path)
        with get_engine().begin() as c:
            c.execute(text(
                "INSERT INTO caches (gc_code, name, cache_type, latitude, longitude, owner_name) "
                "VALUES ('GC1X', 'X', 'Traditional Cache', 55.0, 12.0, 'NewName')"))
        manager = SimpleNamespace(databases=[SimpleNamespace(path=path)])
        monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: manager)
        s = get_settings()
        s.gc_username = "NewName"
        assert account_changed("", "newname ") == {}          # same account
        assert account_changed("", "OldName") == {path: 1}     # changed

    def test_account_change_needs_recalc(self):
        # Decides whether Settings shows the busy box around account_changed().
        from opensak.gui.settings import get_settings
        s = get_settings()
        s.gc_finder_id = ""
        s.gc_username = "NewName"
        assert account_change_needs_recalc("", "OldName")
        assert not account_change_needs_recalc("", "newname ")  # same account
        s.gc_username = ""
        assert not account_change_needs_recalc("", "OldName")   # no account left

    def test_migration_28_backfills(self, tmp_path):
        from opensak.gui.settings import get_settings
        get_settings().gc_username = "Me"
        init_db(db_path=tmp_path / "m.db")
        engine = get_engine()
        with engine.begin() as c:
            c.execute(text(
                "INSERT INTO caches (gc_code, name, cache_type, latitude, longitude, owner_name) "
                "VALUES ('GC1M', 'M', 'Traditional Cache', 55.0, 12.0, 'me')"))
            c.execute(text("PRAGMA user_version = 27"))
        _run_migrations(engine)
        with engine.connect() as c:
            assert c.execute(text("SELECT is_owner FROM caches")).scalar() == 1
            assert c.execute(text("PRAGMA user_version")).scalar() == SCHEMA_VERSION

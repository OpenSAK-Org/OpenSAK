# tests/unit-tests/test_found_updater.py — found-status sync from a reference DB.

from datetime import datetime

import pytest
from pathlib import Path

from opensak.db.database import init_db, get_session
from opensak.db.models import Cache, Log
from opensak.db.found_updater import get_found_gc_codes, update_found_from_reference


# ── Module-level helpers ──────────────────────────────────────────────────────
# make_cache comes from conftest.py

def _setup_ref_db(ref_path: Path, gc_codes: list[str], make_cache) -> None:
    # Initialise a reference DB and populate it with the given GC codes.
    init_db(db_path=ref_path)
    with get_session() as s:
        for gc in gc_codes:
            s.add(make_cache(gc))


def _setup_active_db(active_path: Path, entries: list[dict], make_cache) -> None:
    # Initialise the active (global) DB and populate it.
    init_db(db_path=active_path)
    with get_session() as s:
        for entry in entries:
            s.add(make_cache(**entry))


# ── get_found_gc_codes ────────────────────────────────────────────────────────

class TestGetFoundGcCodes:
    def test_returns_dict_of_gc_codes(self, tmp_path, make_cache):
        ref_path = tmp_path / "ref.db"
        _setup_ref_db(ref_path, ["GC00001", "GC00002", "GC00003"], make_cache)

        codes = get_found_gc_codes(ref_path)
        assert isinstance(codes, dict)
        assert set(codes.keys()) == {"GC00001", "GC00002", "GC00003"}

    def test_empty_database_returns_empty_dict(self, tmp_path):
        ref_path = tmp_path / "empty_ref.db"
        init_db(db_path=ref_path)

        codes = get_found_gc_codes(ref_path)
        assert codes == {}

    def test_single_entry(self, tmp_path, make_cache):
        ref_path = tmp_path / "single.db"
        _setup_ref_db(ref_path, ["GCABCDE"], make_cache)

        codes = get_found_gc_codes(ref_path)
        assert "GCABCDE" in codes
        assert len(codes) == 1

    def test_nonexistent_path_raises_runtime_error(self, tmp_path):
        bad_path = tmp_path / "does_not_exist.db"
        with pytest.raises(RuntimeError, match="Could not read reference database"):
            get_found_gc_codes(bad_path)

    def test_parses_found_it_log_date(self, tmp_path, make_cache):
        ref_path = tmp_path / "ref.db"
        init_db(db_path=ref_path)
        with get_session() as s:
            c = make_cache("GC00001")
            c.logs.append(Log(log_type="Found it", log_date=datetime(2020, 5, 1)))
            s.add(c)
        found = get_found_gc_codes(ref_path)
        assert found["GC00001"] is not None
        assert found["GC00001"].year == 2020

    def test_parses_attended_log_date(self, tmp_path, make_cache):
        # Bug #457: events log a find as "Attended", not "Found it" — the
        # reference-DB query must pick this up too.
        ref_path = tmp_path / "ref.db"
        init_db(db_path=ref_path)
        with get_session() as s:
            c = make_cache("GCEVENT1")
            c.logs.append(Log(log_type="Attended", log_date=datetime(2010, 7, 31)))
            s.add(c)
        found = get_found_gc_codes(ref_path)
        assert found["GCEVENT1"] is not None
        assert found["GCEVENT1"].year == 2010

    def test_parses_webcam_photo_taken_log_date(self, tmp_path, make_cache):
        # Bug #457: webcam caches log a find as "Webcam Photo Taken", not
        # "Found it" — the reference-DB query must pick this up too.
        ref_path = tmp_path / "ref.db"
        init_db(db_path=ref_path)
        with get_session() as s:
            c = make_cache("GCWEBCAM")
            c.logs.append(Log(log_type="Webcam Photo Taken", log_date=datetime(2013, 12, 26)))
            s.add(c)
        found = get_found_gc_codes(ref_path)
        assert found["GCWEBCAM"] is not None
        assert found["GCWEBCAM"].year == 2013

    def test_ignores_non_found_log_types(self, tmp_path, make_cache):
        # A "Write note" log alone must not produce a found_date.
        ref_path = tmp_path / "ref.db"
        init_db(db_path=ref_path)
        with get_session() as s:
            c = make_cache("GC00099")
            c.logs.append(Log(log_type="Write note", log_date=datetime(2020, 1, 1)))
            s.add(c)
        found = get_found_gc_codes(ref_path)
        assert found["GC00099"] is None


# ── update_found_from_reference ───────────────────────────────────────────────

class TestUpdateFoundFromReference:
    def test_marks_matching_caches_as_found(self, tmp_path, make_cache):
        ref_path = tmp_path / "ref.db"
        active_path = tmp_path / "active.db"

        _setup_ref_db(ref_path, ["GC00001", "GC00002"], make_cache)
        _setup_active_db(active_path, [
            {"gc_code": "GC00001", "found": False},
            {"gc_code": "GC00002", "found": False},
            {"gc_code": "GC99999", "found": False},
        ], make_cache)

        result = update_found_from_reference(ref_path)

        assert result.updated == 2
        assert result.already == 0
        assert len(result.errors) == 0

        with get_session() as s:
            c1 = s.query(Cache).filter_by(gc_code="GC00001").first()
            c2 = s.query(Cache).filter_by(gc_code="GC00002").first()
            c3 = s.query(Cache).filter_by(gc_code="GC99999").first()
            assert c1.found is True
            assert c2.found is True
            assert c3.found is False

    def test_already_found_counted_separately(self, tmp_path, make_cache):
        ref_path = tmp_path / "ref.db"
        active_path = tmp_path / "active.db"

        _setup_ref_db(ref_path, ["GC00001", "GC00002"], make_cache)
        _setup_active_db(active_path, [
            {"gc_code": "GC00001", "found": True},
            {"gc_code": "GC00002", "found": False},
        ], make_cache)

        result = update_found_from_reference(ref_path)

        assert result.already == 1
        assert result.updated == 1

    def test_already_found_updates_date_from_reference(self, tmp_path, make_cache):
        ref_path = tmp_path / "ref.db"
        active_path = tmp_path / "active.db"

        init_db(db_path=ref_path)
        with get_session() as s:
            c = make_cache("GC00001")
            c.logs.append(Log(log_type="Found it", log_date=datetime(2018, 3, 4)))
            s.add(c)
        _setup_active_db(active_path, [{"gc_code": "GC00001", "found": True}], make_cache)

        result = update_found_from_reference(ref_path)

        assert result.already == 1
        with get_session() as s:
            cache = s.query(Cache).filter_by(gc_code="GC00001").first()
            assert cache.found_date is not None
            assert cache.found_date.year == 2018

    def test_not_found_counts_ref_codes_missing_from_active(self, tmp_path, make_cache):
        ref_path = tmp_path / "ref.db"
        active_path = tmp_path / "active.db"

        _setup_ref_db(ref_path, ["GC00001", "GC99999"], make_cache)
        _setup_active_db(active_path, [
            {"gc_code": "GC00001", "found": False},
        ], make_cache)

        result = update_found_from_reference(ref_path)

        assert result.not_found == 1  # GC99999 is in ref but not in active

    def test_idempotent_on_second_run(self, tmp_path, make_cache):
        ref_path = tmp_path / "ref.db"
        active_path = tmp_path / "active.db"

        _setup_ref_db(ref_path, ["GC00001"], make_cache)
        _setup_active_db(active_path, [
            {"gc_code": "GC00001", "found": False},
        ], make_cache)

        r1 = update_found_from_reference(ref_path)
        r2 = update_found_from_reference(ref_path)

        assert r1.updated == 1
        assert r2.already == 1
        assert r2.updated == 0

    def test_empty_reference_db_returns_error(self, tmp_path, make_cache):
        ref_path = tmp_path / "empty_ref.db"
        active_path = tmp_path / "active.db"

        init_db(db_path=ref_path)
        _setup_active_db(active_path, [
            {"gc_code": "GC00001", "found": False},
        ], make_cache)

        result = update_found_from_reference(ref_path)

        assert len(result.errors) > 0

    def test_nonexistent_reference_path_returns_error(self, tmp_path, make_cache):
        active_path = tmp_path / "active.db"
        bad_ref_path = tmp_path / "no_such_ref.db"

        _setup_active_db(active_path, [{"gc_code": "GC00001", "found": False}], make_cache)

        result = update_found_from_reference(bad_ref_path)

        assert len(result.errors) > 0

    def test_no_overlap_between_dbs(self, tmp_path, make_cache):
        ref_path = tmp_path / "ref.db"
        active_path = tmp_path / "active.db"

        _setup_ref_db(ref_path, ["GC11111", "GC22222"], make_cache)
        _setup_active_db(active_path, [
            {"gc_code": "GC33333", "found": False},
            {"gc_code": "GC44444", "found": False},
        ], make_cache)

        result = update_found_from_reference(ref_path)

        assert result.updated == 0
        assert result.already == 0
        assert result.not_found == 2


# ── Chunked bulk update (#1095) ───────────────────────────────────────────────
#
# update_found_from_reference() no longer loads every cache. These pin the
# semantics a naive bulk rewrite gets wrong, and the chunk boundaries.

def _setup_ref_db_with_dates(ref_path: Path, dates: dict, make_cache) -> None:
    # dates: {gc_code: datetime or None}; None = no found log in the ref DB.
    init_db(db_path=ref_path)
    with get_session() as s:
        for gc, d in dates.items():
            c = make_cache(gc)
            if d is not None:
                c.logs.append(Log(log_type="Found it", log_date=d))
            s.add(c)


class TestChunkedBulkUpdate:
    def test_already_found_ref_date_overwrites_ref_null_does_not(self, tmp_path, make_cache):
        ref_path = tmp_path / "ref.db"
        active_path = tmp_path / "active.db"
        _setup_ref_db_with_dates(ref_path, {
            "GC00001": datetime(2018, 3, 4, 12, 0),
            "GC00002": None,
        }, make_cache)
        _setup_active_db(active_path, [
            {"gc_code": "GC00001", "found": True, "found_date": datetime(2020, 1, 1)},
            {"gc_code": "GC00002", "found": True, "found_date": datetime(2021, 6, 7)},
        ], make_cache)

        result = update_found_from_reference(ref_path)

        assert (result.updated, result.already, result.not_found) == (0, 2, 0)
        with get_session() as s:
            c1 = s.query(Cache).filter_by(gc_code="GC00001").one()
            c2 = s.query(Cache).filter_by(gc_code="GC00002").one()
            assert c1.found_date == datetime(2018, 3, 4, 12, 0)   # overwritten
            assert c2.found_date == datetime(2021, 6, 7)          # kept

    def test_not_yet_found_with_null_ref_date_is_marked_found_without_date(
            self, tmp_path, make_cache):
        ref_path = tmp_path / "ref.db"
        active_path = tmp_path / "active.db"
        _setup_ref_db_with_dates(ref_path, {"GC00001": None}, make_cache)
        _setup_active_db(active_path, [
            {"gc_code": "GC00001", "found": False, "found_date": datetime(2019, 1, 1)},
        ], make_cache)

        result = update_found_from_reference(ref_path)

        assert result.updated == 1
        with get_session() as s:
            c = s.query(Cache).filter_by(gc_code="GC00001").one()
            assert c.found is True
            assert c.found_date is None   # set to the ref date, which is NULL

    def test_found_date_stored_in_sqlalchemy_datetime_format(self, tmp_path, make_cache):
        # The bulk UPDATE must store dates exactly as the ORM does — no
        # "T" separator and no "+00:00" from the tz-aware ref dates.
        from sqlalchemy import text as sa_text
        ref_path = tmp_path / "ref.db"
        active_path = tmp_path / "active.db"
        _setup_ref_db_with_dates(ref_path, {"GC00001": datetime(2018, 3, 4, 19, 5, 6)},
                                 make_cache)
        _setup_active_db(active_path, [{"gc_code": "GC00001", "found": False}], make_cache)

        update_found_from_reference(ref_path)

        with get_session() as s:
            raw = s.execute(sa_text(
                "SELECT found_date FROM caches WHERE gc_code = 'GC00001'")).scalar()
        assert raw == "2018-03-04 19:05:06.000000"

    def test_counts_and_updates_span_chunk_boundaries(self, tmp_path, make_cache, monkeypatch):
        import opensak.db.found_updater as fu
        monkeypatch.setattr(fu, "_CHUNK_SIZE", 3)

        ref_path = tmp_path / "ref.db"
        active_path = tmp_path / "active.db"
        ref_codes = [f"GC{n:05d}" for n in range(1, 11)]          # 10 codes, 4 chunks
        _setup_ref_db_with_dates(
            ref_path, {gc: datetime(2017, 1, 1) for gc in ref_codes}, make_cache)
        # Active: 1–4 not found, 5–7 already found, 8–10 missing, plus an
        # unrelated cache that must stay untouched.
        _setup_active_db(active_path,
            [{"gc_code": gc, "found": False} for gc in ref_codes[:4]]
            + [{"gc_code": gc, "found": True} for gc in ref_codes[4:7]]
            + [{"gc_code": "GC99999", "found": False}],
            make_cache)

        result = update_found_from_reference(ref_path)

        assert (result.updated, result.already, result.not_found) == (4, 3, 3)
        with get_session() as s:
            found = {c.gc_code: (c.found, c.found_date) for c in s.query(Cache).all()}
        for gc in ref_codes[:7]:
            assert found[gc] == (True, datetime(2017, 1, 1))
        assert found["GC99999"] == (False, None)

    def test_does_not_load_caches_as_orm_objects(self, tmp_path, make_cache, monkeypatch):
        # The old implementation called session.query(Cache).all().
        from sqlalchemy.orm import Session
        ref_path = tmp_path / "ref.db"
        active_path = tmp_path / "active.db"
        _setup_ref_db(ref_path, ["GC00001"], make_cache)
        _setup_active_db(active_path, [{"gc_code": "GC00001", "found": False}], make_cache)

        def _no_query(self, *args, **kwargs):
            raise AssertionError("session.query() must not be used (#1095)")

        monkeypatch.setattr(Session, "query", _no_query)
        result = update_found_from_reference(ref_path)
        monkeypatch.undo()

        assert result.updated == 1


# ── UpdateResult dataclass ────────────────────────────────────────────────────

class TestUpdateResult:
    def test_str_representation(self, tmp_path, make_cache):
        ref_path = tmp_path / "ref.db"
        active_path = tmp_path / "active.db"

        _setup_ref_db(ref_path, ["GC00001"], make_cache)
        _setup_active_db(active_path, [{"gc_code": "GC00001", "found": False}], make_cache)

        result = update_found_from_reference(ref_path)
        text = str(result)

        assert "1" in text   # updated count visible in output
        assert "\n" in text  # multiline format

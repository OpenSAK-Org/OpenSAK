# tests/unit-tests/test_corrected_coords_db.py — shared corrected-coords DB write.

from opensak.db.corrected_coords import set_corrected_coords
from opensak.db.database import get_session
from opensak.db.models import Cache, UserNote


def _note(gc_code: str):
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code=gc_code).one()
        note = cache.user_note
        if note is None:
            return None
        return note.corrected_lat, note.corrected_lon, note.is_corrected


class TestSetCorrectedCoords:
    def test_creates_user_note_when_missing(self, db_session, make_cache):
        db_session.add(make_cache("GCSET1"))
        db_session.commit()

        assert set_corrected_coords("GCSET1", 56.0, 13.0) is True
        assert _note("GCSET1") == (56.0, 13.0, True)

    def test_updates_existing_note_and_keeps_text(self, db_session, make_cache):
        cache = make_cache("GCSET2")
        db_session.add(cache)
        db_session.flush()
        db_session.add(UserNote(cache_id=cache.id, note="keep me"))
        db_session.commit()

        set_corrected_coords("GCSET2", 57.5, 14.25)

        assert _note("GCSET2") == (57.5, 14.25, True)
        with get_session() as s:
            assert s.query(UserNote).one().note == "keep me"

    def test_clear_resets_is_corrected(self, db_session, make_cache):
        db_session.add(make_cache("GCSET3"))
        db_session.commit()
        set_corrected_coords("GCSET3", 56.0, 13.0)

        assert set_corrected_coords("GCSET3", None, None) is True
        assert _note("GCSET3") == (None, None, False)

    def test_partial_coords_are_not_corrected(self, db_session, make_cache):
        db_session.add(make_cache("GCSET4"))
        db_session.commit()

        set_corrected_coords("GCSET4", 56.0, None)

        assert _note("GCSET4") == (56.0, None, False)

    def test_missing_cache_returns_false(self, db_session):
        assert set_corrected_coords("GCNOPE", 1.0, 2.0) is False
        with get_session() as s:
            assert s.query(UserNote).count() == 0

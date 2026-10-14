# tests/unit-tests/test_user_flags.py — bulk set/clear of the user flag (#293).

import pytest

from opensak.db import user_flags
from opensak.db.database import get_session, init_db
from opensak.db.models import Cache
from opensak.db.user_flags import set_user_flag


@pytest.fixture
def db(tmp_path):
    init_db(db_path=tmp_path / "flags.db")
    with get_session() as s:
        for i, flag in enumerate([False, True, False, False]):
            s.add(Cache(gc_code=f"GC{i}", name=f"Cache {i}", user_flag=flag,
                        cache_type="Traditional Cache", latitude=55.0, longitude=12.0))


def _flags():
    with get_session() as s:
        return {c.gc_code: c.user_flag for c in s.query(Cache).all()}


def test_set_flag_changes_only_unflagged(db):
    changed = set_user_flag(["GC0", "GC1", "GC2"], True)
    assert changed == 2  # GC1 was already flagged
    assert _flags() == {"GC0": True, "GC1": True, "GC2": True, "GC3": False}


def test_clear_flag(db):
    changed = set_user_flag(["GC0", "GC1"], False)
    assert changed == 1  # only GC1 was flagged
    assert _flags()["GC1"] is False


def test_unknown_codes_and_duplicates_are_ignored(db):
    assert set_user_flag(["GC0", "GC0", "", "NOPE"], True) == 1


def test_empty_input(db):
    assert set_user_flag([], True) == 0


def test_chunking(db, monkeypatch):
    # Four caches across chunks of one code each — every chunk is applied.
    monkeypatch.setattr(user_flags, "CHUNK_SIZE", 1)
    assert set_user_flag(["GC0", "GC1", "GC2", "GC3"], True) == 3
    assert all(_flags().values())


def test_accepts_a_generator(db):
    assert set_user_flag((c for c in ["GC0", "GC2"]), True) == 2

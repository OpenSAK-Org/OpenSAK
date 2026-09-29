"""tests/unit-tests/test_log_count.py — cache.log_count column behaviour (issue #87).

log_count caches len(logs) because logs isn't loaded for the table view (raiseload, #898), so len(cache.logs) can't be used there.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import raiseload, sessionmaker

from opensak.db.models import Base, Cache, Log


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield s
    finally:
        s.close()


def _cache(session, gc_code, num_logs=0):
    cache = Cache(gc_code=gc_code, name=gc_code, cache_type="Traditional Cache",
                  latitude=55.0, longitude=12.0)
    session.add(cache)
    session.flush()
    base = datetime(2024, 1, 1)
    for i in range(num_logs):
        session.add(Log(cache_id=cache.id, log_id=f"{gc_code}_{i}", log_type="Found it",
                        log_date=base + timedelta(days=i), finder=f"f{i}"))
    session.flush()
    return cache


def test_log_count_defaults_to_zero_not_null(session):
    cache = _cache(session, "GC1")
    session.commit()
    assert cache.log_count == 0 and cache.log_count is not None


def test_log_count_persists_and_updates_on_reimport(session):
    cache = _cache(session, "GC2", num_logs=10)
    cache.log_count = len(cache.logs)
    session.commit()
    assert cache.log_count == 10

    # Re-import: drop old logs, add fewer, refresh the cached count.
    session.query(Log).filter_by(cache_id=cache.id).delete()
    base = datetime(2025, 1, 1)
    for i in range(3):
        session.add(Log(cache_id=cache.id, log_id=f"new_{i}", log_type="Found it",
                        log_date=base + timedelta(days=i), finder=f"f{i}"))
    cache.log_count = 3
    session.commit()
    assert cache.log_count == 3


def test_log_count_readable_without_loading_logs(session):
    cache = _cache(session, "GC3", num_logs=42)
    cache.log_count = 42
    session.commit()
    cid = cache.id
    session.expunge_all()
    fresh = session.query(Cache).options(raiseload(Cache.logs)).filter_by(id=cid).first()
    assert fresh.log_count == 42


def test_migration_update_populates_count_from_logs(session):
    rows = [_cache(session, "M1", 0), _cache(session, "M2", 5), _cache(session, "M3", 20)]
    session.execute(text("UPDATE caches SET log_count = 0"))
    session.execute(text(
        "UPDATE caches SET log_count = "
        "(SELECT COUNT(*) FROM logs WHERE logs.cache_id = caches.id)"
    ))
    session.commit()
    for cache, expected in zip(rows, (0, 5, 20)):
        session.refresh(cache)
        assert cache.log_count == expected

"""tests/unit-tests/test_apply_filters_raiseload_898.py — issue #898.

apply_filters() used the deprecated noload() for relationships no active
filter needs, which made e.g. ``cache.logs`` silently read as an empty list.
It now uses raiseload(): the caches are handed to the GUI after the session
has closed, so any code reading an unloaded relationship there must fail
loudly (and reload the cache first) instead of quietly showing wrong data.
Relationships a filter *does* need are still eager-loaded and readable.
"""

import pytest
from sqlalchemy.exc import InvalidRequestError

from opensak.db.database import get_session
from opensak.db.models import Attribute, Cache, Log, Trackable, Waypoint
from opensak.filters.engine import (
    AttributeFilter, FilterSet, HasTrackableFilter, LogFilter, NameFilter,
    TextSearchFilter, WaypointFilter, apply_filters,
)

RELATIONSHIPS = ("attributes", "trackables", "logs", "waypoints")


@pytest.fixture(scope="module", autouse=True)
def seed(tmp_db):
    with get_session() as s:
        c = Cache(gc_code="GCRL898", name="Raiseload", cache_type="Traditional Cache",
                  latitude=55.0, longitude=12.0)
        s.add(c)
        s.flush()
        s.add(Attribute(cache_id=c.id, attribute_id=1, name="Dogs allowed", is_on=True))
        s.add(Trackable(cache_id=c.id, ref="TB898", name="Bug"))
        s.add(Log(cache_id=c.id, log_type="Found it", finder="Tester", text="needle in log"))
        s.add(Waypoint(cache_id=c.id, prefix="PK", wp_type="Parking Area",
                       name="Parking", latitude=55.0, longitude=12.0))


def _one(filterset):
    with get_session() as s:
        caches = apply_filters(s, filterset)
    # Read after the session closed — exactly how refresh_worker hands them on.
    assert [c.gc_code for c in caches] == ["GCRL898"]
    return caches[0]


@pytest.mark.parametrize("rel", RELATIONSHIPS)
def test_unneeded_relationship_raises_instead_of_reading_empty(rel):
    cache = _one(FilterSet(mode="AND").add(NameFilter("Raiseload")))
    with pytest.raises(InvalidRequestError):
        getattr(cache, rel)


@pytest.mark.parametrize("rel", RELATIONSHIPS)
def test_unneeded_relationship_raises_with_no_filter_at_all(rel):
    with get_session() as s:
        caches = apply_filters(s, None)
    cache = next(c for c in caches if c.gc_code == "GCRL898")
    with pytest.raises(InvalidRequestError):
        getattr(cache, rel)


def test_needed_relationships_are_still_loaded():
    fs = FilterSet(mode="AND")
    fs.add(AttributeFilter(1, is_on=True))
    fs.add(HasTrackableFilter())
    fs.add(TextSearchFilter("needle", search_logs=True))
    cache = _one(fs)
    assert [a.attribute_id for a in cache.attributes] == [1]
    assert [t.ref for t in cache.trackables] == ["TB898"]
    assert [lg.text for lg in cache.logs] == ["needle in log"]


def test_log_and_waypoint_filters_count_without_the_relationship():
    # Log/Waypoint filters count via prepare() in SQL, never via the
    # (raiseload'ed) relationship — they must still match under raiseload,
    # also in OR mode where nothing is pushed down to SQL.
    for mode in ("AND", "OR"):
        fs = FilterSet(mode=mode)
        fs.add(LogFilter())
        fs.add(WaypointFilter())
        _one(fs)

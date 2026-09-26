"""tests/unit-tests/test_corrected_distance_filter.py — distance between a
cache's corrected and posted coordinates: operators, caches without corrected
coordinates, serialisation, and SQL-pushdown / lightweight-path parity with
matches() (same shape as test_userdata_elevation_gcnote_filters).
"""

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache, UserNote
from opensak.filters.engine import (
    CORRECTED_DISTANCE_OPS, FILTER_REGISTRY, CorrectedDistanceFilter, FilterSet,
    HasCorrectedFilter, apply_filters, apply_filters_lightweight,
)

# Posted at 47.0/8.0; 0.001° of latitude ≈ 111 m, 0.045° ≈ 5004 m.
_SAME, _NEAR, _FAR, _NONE, _UNSET = "GCCD0001", "GCCD0002", "GCCD0003", "GCCD0004", "GCCD0005"


@pytest.fixture(scope="module", autouse=True)
def seed(tmp_db):
    def cache(code, corrected=None, is_corrected=True):
        c = Cache(gc_code=code, name=code, cache_type="Unknown Cache",
                  latitude=47.0, longitude=8.0)
        if corrected is not None:
            c.user_note = UserNote(corrected_lat=corrected[0], corrected_lon=corrected[1],
                                   is_corrected=is_corrected)
        return c

    with get_session() as s:
        s.add(cache(_SAME, (47.0, 8.0)))
        s.add(cache(_NEAR, (47.001, 8.0)))
        s.add(cache(_FAR, (47.045, 8.0)))
        s.add(cache(_NONE))
        s.add(cache(_UNSET, (47.2, 8.0), is_corrected=False))


def codes(f):
    """Codes matched by *f*, asserting the ORM, lightweight and pure-Python
    paths all agree."""
    fs = FilterSet().add(f)
    with get_session() as s:
        pushed = {c.gc_code for c in apply_filters(s, fs)}
        light = {c.gc_code for c in apply_filters_lightweight(s, fs)}
        python = {c.gc_code for c in apply_filters(s, None) if fs.matches(c)}
    assert pushed == python == light
    return pushed


class TestOperators:
    def test_equal_zero_is_corrected_on_posted(self):
        assert codes(CorrectedDistanceFilter("equal", 0)) == {_SAME}

    def test_equal_rounds_to_whole_metres(self):
        assert codes(CorrectedDistanceFilter("equal", 111)) == {_NEAR}
        assert codes(CorrectedDistanceFilter("equal", 110)) == set()

    def test_less_than_and_at_most(self):
        assert codes(CorrectedDistanceFilter("less_than", 0)) == set()
        assert codes(CorrectedDistanceFilter("at_most", 0)) == {_SAME}
        assert codes(CorrectedDistanceFilter("less_than", 1000)) == {_SAME, _NEAR}

    def test_more_than_and_at_least(self):
        assert codes(CorrectedDistanceFilter("more_than", 0)) == {_NEAR, _FAR}
        assert codes(CorrectedDistanceFilter("at_least", 0)) == {_SAME, _NEAR, _FAR}
        assert codes(CorrectedDistanceFilter("more_than", 3219)) == {_FAR}

    def test_between_is_inclusive_and_order_free(self):
        assert codes(CorrectedDistanceFilter("between", 100, 6000)) == {_NEAR, _FAR}
        assert codes(CorrectedDistanceFilter("between", 6000, 100)) == {_NEAR, _FAR}
        assert codes(CorrectedDistanceFilter("between", 0, 0)) == {_SAME}

    def test_not_between(self):
        assert codes(CorrectedDistanceFilter("not_between", 100, 1000)) == {_SAME, _FAR}

    def test_uncorrected_caches_never_match(self):
        for op in CORRECTED_DISTANCE_OPS:
            matched = codes(CorrectedDistanceFilter(op, 0, 20_000_000))
            assert not matched & {_NONE, _UNSET}, op

    def test_unknown_operator_rejected(self):
        with pytest.raises(ValueError):
            CorrectedDistanceFilter("roughly", 5)

    def test_or_group(self):
        fs = FilterSet(mode="OR")
        fs.add(CorrectedDistanceFilter("equal", 0))
        fs.add(CorrectedDistanceFilter("more_than", 3219))
        with get_session() as s:
            assert {c.gc_code for c in apply_filters(s, fs)} == {_SAME, _FAR}

    def test_combines_with_has_corrected(self):
        fs = FilterSet().add(HasCorrectedFilter()).add(CorrectedDistanceFilter("less_than", 50))
        with get_session() as s:
            assert {c.gc_code for c in apply_filters(s, fs)} == {_SAME}


class TestSerialisation:
    def test_registered(self):
        assert FILTER_REGISTRY["corrected_distance"] is CorrectedDistanceFilter

    def test_round_trip(self):
        f = CorrectedDistanceFilter("not_between", 10.5, 3219.0)
        restored = FilterSet.from_dict(FilterSet().add(f).to_dict())._filters[0]
        assert isinstance(restored, CorrectedDistanceFilter)
        assert (restored.op, restored.dist1_m, restored.dist2_m) == ("not_between", 10.5, 3219.0)

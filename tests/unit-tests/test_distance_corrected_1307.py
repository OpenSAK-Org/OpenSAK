"""tests/unit-tests/test_distance_corrected_1307.py — #1307.

Distance and bearing are measured to a cache's corrected coordinates when it
has them, as GSAK does: the Distance/Bearing columns, sorting by distance,
the distance and direction filters, and so the export's "nearest N" cut.

Test data as nagisml's DistanceDemo.gpx: centre "Zurich HB"; GCDST01 3 km
north, GCDST02 8 km east, GCDST03 listed in Bern but corrected 70 m from the
centre.
"""

from pathlib import Path

import pytest

from opensak.db.database import (
    distances_up_to_date, get_session, init_db, recalculate_distances,
)
from opensak.db.models import Cache, UserNote
from opensak.filters.engine import (
    DirectionFilter, DistanceFilter, FilterSet, SortSpec, apply_filters, distance_km,
)
from opensak.gui.settings import get_settings

ZURICH = (47.3779, 8.5403)
BERN = (46.9480, 7.4474)
SOLVED = (47.3785, 8.5403)          # ~70 m north of the centre
NORTH_3KM = (47.4049, 8.5403)
EAST_8KM = (47.3779, 8.6463)


@pytest.fixture
def demo(tmp_path, monkeypatch):
    init_db(db_path=tmp_path / "distance_demo.db")
    s = get_settings()
    monkeypatch.setattr(s, "home_lat", ZURICH[0])
    monkeypatch.setattr(s, "home_lon", ZURICH[1])
    with get_session() as session:
        for code, (lat, lon), ctype in (
            ("GCDST01", NORTH_3KM, "Traditional Cache"),
            ("GCDST02", EAST_8KM, "Traditional Cache"),
            ("GCDST03", BERN, "Unknown Cache"),
        ):
            session.add(Cache(gc_code=code, name=code, cache_type=ctype,
                              latitude=lat, longitude=lon))
        session.flush()
        mystery = session.query(Cache).filter_by(gc_code="GCDST03").one()
        session.add(UserNote(cache_id=mystery.id, corrected_lat=SOLVED[0],
                             corrected_lon=SOLVED[1], is_corrected=True))
    recalculate_distances(*ZURICH)
    return tmp_path / "distance_demo.db"


def _distance(code: str) -> float:
    with get_session() as session:
        value = session.query(Cache).filter_by(gc_code=code).one().distance
    assert value is not None
    return value


def _bearing(code: str) -> float:
    with get_session() as session:
        value = session.query(Cache).filter_by(gc_code=code).one().bearing
    assert value is not None
    return value


def _codes(fs: FilterSet, sort: SortSpec | None = None) -> list[str]:
    with get_session() as session:
        return [c.gc_code for c in apply_filters(session, fs, sort)]


# ── Distance column and sorting ──────────────────────────────────────────────

def test_distance_is_measured_to_the_corrected_coordinates(demo):
    assert _distance("GCDST03") == pytest.approx(distance_km(*ZURICH, *SOLVED), abs=1e-6)
    assert _distance("GCDST03") < 0.1
    assert _distance("GCDST01") == pytest.approx(3.0, abs=0.05)
    assert _distance("GCDST02") == pytest.approx(8.0, abs=0.05)


def test_bearing_is_measured_to_the_corrected_coordinates(demo):
    # The solution is due north of the centre; Bern is to the south-west.
    assert _bearing("GCDST03") == pytest.approx(0.0, abs=0.5)


def test_sorting_by_distance_puts_the_solved_mystery_first(demo):
    assert _codes(FilterSet(), SortSpec("distance")) == ["GCDST03", "GCDST01", "GCDST02"]


def test_caches_without_corrections_are_unchanged(demo):
    assert _distance("GCDST01") == pytest.approx(distance_km(*ZURICH, *NORTH_3KM), abs=1e-6)


def test_a_correction_that_is_not_active_is_ignored(demo):
    with get_session() as session:
        session.query(UserNote).update({UserNote.is_corrected: False})
    recalculate_distances(*ZURICH)
    assert _distance("GCDST03") == pytest.approx(distance_km(*ZURICH, *BERN), abs=1e-6)


# ── Filters ──────────────────────────────────────────────────────────────────

def test_less_than_10_km_includes_the_solved_mystery(demo):
    fs = FilterSet()
    fs.add(DistanceFilter(*ZURICH, op="less_than", dist1_km=10.0))
    assert sorted(_codes(fs)) == ["GCDST01", "GCDST02", "GCDST03"]


def test_more_than_50_km_leaves_the_solved_mystery_out(demo):
    fs = FilterSet()
    fs.add(DistanceFilter(*ZURICH, op="more_than", dist1_km=50.0))
    assert _codes(fs) == []


def test_distance_box_lets_a_far_listing_through(demo):
    # The SQL box alone (before matches()) must not drop GCDST03.
    from opensak.filters.engine import DistanceFilter as DF
    f = DF(*ZURICH, op="at_most", dist1_km=1.0)
    with get_session() as session:
        query = f.apply_to_query(session.query(Cache))
        assert query is not None
        assert "GCDST03" in {c.gc_code for c in query}


def test_direction_filter_from_a_centre_uses_the_corrected_coordinates(demo):
    fs = FilterSet()
    fs.add(DirectionFilter(["N"], lat=ZURICH[0], lon=ZURICH[1]))
    assert sorted(_codes(fs)) == ["GCDST01", "GCDST03"]


# ── Setting and clearing corrected coordinates ───────────────────────────────

def test_setting_corrected_coordinates_updates_the_distance(demo):
    from opensak.db.corrected_coords import set_corrected_coords

    assert set_corrected_coords("GCDST02", *SOLVED)
    assert _distance("GCDST02") == pytest.approx(distance_km(*ZURICH, *SOLVED), abs=1e-6)


def test_clearing_corrected_coordinates_restores_the_listed_distance(demo):
    from opensak.db.corrected_coords import set_corrected_coords

    assert set_corrected_coords("GCDST03", None, None)
    assert _distance("GCDST03") == pytest.approx(distance_km(*ZURICH, *BERN), abs=1e-6)


def test_set_corrected_keeps_distances_up_to_date(demo):
    from opensak.db.corrected_coords import set_corrected_coords

    set_corrected_coords("GCDST01", *SOLVED)
    assert distances_up_to_date(*ZURICH)


def test_macro_update_of_corrected_updates_the_distance(demo):
    from opensak.macro.cache_write import update_cache

    with get_session() as session:
        assert update_cache(session, "GCDST02", {"corrected": SOLVED})
    assert _distance("GCDST02") == pytest.approx(distance_km(*ZURICH, *SOLVED), abs=1e-6)
    with get_session() as session:
        assert update_cache(session, "GCDST02", {"corrected": None})
    assert _distance("GCDST02") == pytest.approx(distance_km(*ZURICH, *EAST_8KM), abs=1e-6)


def test_macro_sql_write_to_user_notes_updates_the_distance(demo):
    from opensak.macro import MacroRuntime, WriteApproval

    class Host:
        def database_name(self):
            return "Demo"

        def clear_filter(self):
            pass

        def cache_count(self):
            return 0

        def approve_macro_write(self, macro, change=None, editing=False):
            return WriteApproval.SESSION

        def caches_changed(self, codes, added):
            pass

        def end_macro(self):
            pass

    out: list[str] = []
    MacroRuntime(Host(), output=out.append).run(
        "print(opensak.sql_write(\"UPDATE user_notes SET is_corrected = 0 "
        "WHERE cache_id = (SELECT id FROM caches WHERE gc_code = 'GCDST03')\"))",
        chunk_name="macro",
    )
    assert out == ["1"]
    assert _distance("GCDST03") == pytest.approx(distance_km(*ZURICH, *BERN), abs=1e-6)


# ── Up-to-date check after the update ────────────────────────────────────────

def test_distances_from_before_the_fix_are_recalculated(demo):
    # A database whose distances were calculated to the listed position:
    # the first start after the update must notice and recalculate once.
    with get_session() as session:
        session.query(Cache).filter_by(gc_code="GCDST03").update(
            {Cache.distance: distance_km(*ZURICH, *BERN)})
    assert not distances_up_to_date(*ZURICH)
    recalculate_distances(*ZURICH)
    assert distances_up_to_date(*ZURICH)


def test_up_to_date_for_a_separate_database_file(demo, tmp_path):
    other = Path(demo)
    assert distances_up_to_date(*ZURICH)
    recalculate_distances(*ZURICH, db_path=other)
    assert distances_up_to_date(*ZURICH, db_path=other)

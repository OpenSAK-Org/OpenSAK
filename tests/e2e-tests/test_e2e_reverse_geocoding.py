# tests/e2e-tests/test_e2e_reverse_geocoding.py — offline reverse-geocoding, real dialog to real DB.
#
# Closes a real integration gap: tests/unit-tests/test_geo.py exercises the
# real BoundaryStore/TerritoryResolver in isolation, and
# tests/unit-tests/test_update_location_dialog.py exercises the DB-writing
# worker with BoundaryStore/TerritoryResolver fully mocked. Nothing proved the
# two actually work together end to end — a real BoundaryStore resolving
# against real GeoJSON, through the real dialog, writing to a real DB. Fully
# offline: the synthetic dataset from tests/data.py stands in for the network
# fetch/bundled baseline.

import pytest

pytest.importorskip("pytestqt")

from tests.data import build_boundary_test_data


@pytest.fixture
def geo_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "boundaries"
    build_boundary_test_data(data_dir)
    monkeypatch.setenv("OPENSAK_BOUNDARIES_DIR", str(data_dir))
    return data_dir


def _add_cache(gc_code: str, lat: float, lon: float) -> None:
    from opensak.db.database import get_session
    from opensak.db.models import Cache

    with get_session() as session:
        session.add(Cache(gc_code=gc_code, name=gc_code, cache_type="Traditional Cache",
                           latitude=lat, longitude=lon))


def test_update_location_resolves_through_real_dialog(empty_window, qtbot, geo_data_dir):
    from opensak.db.database import get_session
    from opensak.db.models import Cache
    from opensak.gui.dialogs.update_location_dialog import UpdateLocationDialog

    _add_cache("GCTEST1", 11.0, 11.0)  # far square -> Gamma County, single R-Tree hit
    empty_window._refresh_cache_list()

    dialog = UpdateLocationDialog(parent=empty_window)
    qtbot.addWidget(dialog)
    dialog._rb_all.setChecked(True)

    with qtbot.waitSignal(dialog.location_updated, timeout=5000):
        dialog._start()

    with get_session() as session:
        cache = session.query(Cache).filter_by(gc_code="GCTEST1").first()
        assert cache.country == "Testland"
        assert cache.state == "Teststate"
        assert cache.county == "Gamma County"
        assert cache.location_source == "boundary"
        assert cache.location_basis == "posted"
        assert cache.location_dataset == "1"


def test_update_location_resolves_multiple_regions_in_parallel(empty_window, qtbot, geo_data_dir):
    # Exercises ReverseGeocodeWorker's real parallel-resolve path (Phase Extra:
    # ThreadPoolExecutor, one BoundaryStore per thread) against real geometry —
    # including the two overlapping-bbox triangles that force the Stage-2
    # point-in-polygon check, not just the single-hit fast path.
    from opensak.db.database import get_session
    from opensak.db.models import Cache
    from opensak.gui.dialogs.update_location_dialog import UpdateLocationDialog

    _add_cache("GCALPHA", 0.5, 0.5)      # Alpha/Beta triangles share a bbox
    _add_cache("GCBETA", 1.5, 1.5)
    _add_cache("GCGAMMA", 11.0, 11.0)    # far square, single hit
    _add_cache("GCBORDER", 20.5, 20.5)   # isolated triangle, outside the big square -> no country/state
    _add_cache("GCOUTSIDE", 21.5, 21.5)  # inside bbox, outside triangle (above the hypotenuse) -> no county either
    empty_window._refresh_cache_list()

    dialog = UpdateLocationDialog(parent=empty_window)
    qtbot.addWidget(dialog)
    dialog._rb_all.setChecked(True)

    with qtbot.waitSignal(dialog.location_updated, timeout=5000):
        dialog._start()

    # (country, state, county)
    expected = {
        "GCALPHA": ("Testland", "Teststate", "Alpha County"),
        "GCBETA": ("Testland", "Teststate", "Beta County"),
        "GCGAMMA": ("Testland", "Teststate", "Gamma County"),
        "GCBORDER": (None, None, "Border County"),
        "GCOUTSIDE": (None, None, None),
    }
    with get_session() as session:
        for gc_code, (country, state, county) in expected.items():
            cache = session.query(Cache).filter_by(gc_code=gc_code).first()
            assert (cache.country, cache.state, cache.county) == (country, state, county), gc_code


def test_update_location_no_boundaries_available_degrades_gracefully(empty_window, qtbot, tmp_path, monkeypatch):
    # Real (non-mocked) BoundaryStore pointed at an empty directory — the
    # graceful-degradation path, not the happy path the other tests cover.
    from opensak.gui.dialogs.update_location_dialog import UpdateLocationDialog
    from opensak.lang import tr

    monkeypatch.setenv("OPENSAK_BOUNDARIES_DIR", str(tmp_path / "empty"))
    # Nothing is bundled and there's no boundaries.db here, so ensure_baseline_seeded
    # would otherwise fall through to a real network fetch (geo.packs.fetch_baseline) —
    # not what this test is about; keep it offline like every other test in the suite.
    monkeypatch.setattr("opensak.geo.store.ensure_baseline_seeded", lambda: None)
    _add_cache("GCTEST1", 11.0, 11.0)
    empty_window._refresh_cache_list()

    dialog = UpdateLocationDialog(parent=empty_window)
    qtbot.addWidget(dialog)
    dialog._rb_all.setChecked(True)

    with qtbot.waitSignal(dialog.location_updated, timeout=5000):
        dialog._start()

    assert tr("update_loc_no_boundaries") in dialog._log.toPlainText()

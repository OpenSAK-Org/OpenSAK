# tests/unit-tests/test_nearby_map_selection_718.py — issue #718.
#
# _build_nearby_label() formats the "showing nearest X of Y within R km/mi"
# label mainwindow.py hands to MapWidget.show_nearby_for_selection() — and
# _on_cache_selected() is the actual wiring that replaces the split-screen
# map's marker set with the selected cache's neighbourhood instead of
# reusing the overview map's already-loaded (and possibly unrelated) set.

from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("pytestqt")

from opensak.gui.mainwindow import MainWindow
from opensak.lang import tr


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _quiet_startup(monkeypatch):
    monkeypatch.setattr(MainWindow, "_initial_load", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_update_background", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_setup_complete", lambda self: None)


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    """MainWindow on a throwaway empty DB."""
    import opensak.db.manager as mgr_module
    from opensak.db.database import init_db
    from opensak.lang import load_language
    from tests.data import make_fake_manager

    load_language("en")

    db_path = tmp_path / "test_nearby_718.db"
    init_db(db_path=db_path)
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="NearbyTest"))

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)

    yield win

    win.close()
    mgr_module._manager = None


# ── _build_nearby_label() ──────────────────────────────────────────────────────

class TestBuildNearbyLabel:
    def test_empty_when_cap_not_reached(self, window):
        assert window._build_nearby_label(shown=4, total=4, radius_km=2.0) == ""

    def test_empty_when_shown_exceeds_total_defensively(self, window):
        # Shouldn't happen (total is always >= shown from get_nearby_caches),
        # but total <= shown must never render a label either way.
        assert window._build_nearby_label(shown=5, total=4, radius_km=2.0) == ""

    def test_km_label_when_cap_reached(self, window):
        with patch("opensak.gui.mainwindow.get_settings") as mock_gs:
            mock_gs.return_value = SimpleNamespace(use_miles=False)
            label = window._build_nearby_label(shown=500, total=1240, radius_km=2.0)
        expected = tr("map_nearby_label").format(shown=500, total=1240, radius="2", unit="km")
        assert label == expected
        assert "500" in label and "1240" in label and "km" in label

    def test_miles_label_converts_radius(self, window):
        with patch("opensak.gui.mainwindow.get_settings") as mock_gs:
            mock_gs.return_value = SimpleNamespace(use_miles=True)
            label = window._build_nearby_label(shown=10, total=20, radius_km=2.0)
        assert "mi" in label
        assert "km" not in label
        # 2.0 km ≈ 1.24 mi, formatted via {:g} — just confirm it's not the
        # raw km figure leaking through unconverted.
        assert "2 mi" not in label

    def test_fractional_radius_formats_cleanly(self, window):
        with patch("opensak.gui.mainwindow.get_settings") as mock_gs:
            mock_gs.return_value = SimpleNamespace(use_miles=False)
            label = window._build_nearby_label(shown=1, total=2, radius_km=1.5)
        assert "1.5 km" in label


# ── _on_cache_selected() wiring ────────────────────────────────────────────────

class TestOnCacheSelectedNearbyWiring:
    def _make_cache(self, session, **kw):
        from opensak.db.models import Cache
        defaults = dict(gc_code="GCSEL001", name="Selected", cache_type="Traditional Cache",
                         latitude=51.5074, longitude=-0.1278)
        defaults.update(kw)
        c = Cache(**defaults)
        session.add(c)
        session.flush()
        return c

    def test_selected_cache_with_coords_calls_show_nearby(self, window, monkeypatch):
        from opensak.db.database import get_session
        with get_session() as s:
            cache = self._make_cache(s)
            gc_code = cache.gc_code

        calls = []
        monkeypatch.setattr(
            window._map_widget, "show_nearby_for_selection",
            lambda *a, **k: calls.append((a, k)),
        )
        pan_calls = []
        monkeypatch.setattr(
            window._map_widget, "pan_to_cache",
            lambda *a, **k: pan_calls.append((a, k)),
        )

        with get_session() as s:
            from opensak.db.models import Cache as CacheModel
            full = s.query(CacheModel).filter_by(gc_code=gc_code).first()
            window._on_cache_selected(full)

        assert len(calls) == 1
        assert pan_calls == []  # coords present — must not fall back to plain pan_to_cache

    def test_selected_cache_without_coords_falls_back_to_pan(self, window, monkeypatch):
        # Cache.latitude/longitude are non-nullable on the model in
        # practice, but _on_cache_selected must still degrade gracefully
        # for any duck-typed caller that omits them.
        full = SimpleNamespace(gc_code="GCNOCOORD", latitude=None, longitude=None,
                                name="No Coords")

        calls = []
        monkeypatch.setattr(
            window._map_widget, "show_nearby_for_selection",
            lambda *a, **k: calls.append((a, k)),
        )
        pan_calls = []
        monkeypatch.setattr(
            window._map_widget, "pan_to_cache",
            lambda *a, **k: pan_calls.append((a, k)),
        )
        monkeypatch.setattr(window, "_load_full_cache", lambda gc_code: full)
        # Detail panel rendering is out of scope here — this test is only
        # about which map call the no-coords branch takes.
        monkeypatch.setattr(window._detail_panel, "show_cache", lambda *a, **k: None)

        window._on_cache_selected(full)

        assert calls == []
        assert len(pan_calls) == 1


# ── Issues #748 / #743: get_nearby_caches() call-site wiring ───────────────

class TestOnCacheSelectedNearbyArguments:
    """_on_cache_selected() must call get_nearby_caches() with the
    selected cache's *effective* (corrected-aware) coordinates as the
    centre (#748), and with whatever filter is currently active (#743) —
    rather than always querying distance-from-raw-coordinates alone."""

    def _make_cache(self, session, **kw):
        from opensak.db.models import Cache
        defaults = dict(gc_code="GCARG001", name="Selected", cache_type="Traditional Cache",
                         latitude=51.5074, longitude=-0.1278)
        defaults.update(kw)
        c = Cache(**defaults)
        session.add(c)
        session.flush()
        return c

    def test_uses_raw_coords_when_not_corrected(self, window, monkeypatch):
        from opensak.db.database import get_session
        with get_session() as s:
            cache = self._make_cache(s)
            gc_code = cache.gc_code

        calls = []

        def fake_get_nearby(session, lat, lon, radius_km, max_caches, filterset=None):
            calls.append((lat, lon, filterset))
            return [], 0

        monkeypatch.setattr("opensak.gui.mainwindow.get_nearby_caches", fake_get_nearby)
        monkeypatch.setattr(
            window._map_widget, "show_nearby_for_selection", lambda *a, **k: None,
        )

        with get_session() as s:
            from opensak.db.models import Cache as CacheModel
            full = s.query(CacheModel).filter_by(gc_code=gc_code).first()
            window._on_cache_selected(full)

        assert len(calls) == 1
        lat, lon, _ = calls[0]
        assert lat == pytest.approx(51.5074)
        assert lon == pytest.approx(-0.1278)

    def test_uses_corrected_coords_as_center_when_set(self, window, monkeypatch):
        from opensak.db.database import get_session
        from opensak.db.models import UserNote
        with get_session() as s:
            cache = self._make_cache(s, gc_code="GCARG002")
            s.add(UserNote(
                cache_id=cache.id, is_corrected=True,
                corrected_lat=52.0, corrected_lon=-1.0,
            ))
            gc_code = cache.gc_code

        calls = []

        def fake_get_nearby(session, lat, lon, radius_km, max_caches, filterset=None):
            calls.append((lat, lon, filterset))
            return [], 0

        monkeypatch.setattr("opensak.gui.mainwindow.get_nearby_caches", fake_get_nearby)
        monkeypatch.setattr(
            window._map_widget, "show_nearby_for_selection", lambda *a, **k: None,
        )

        with get_session() as s:
            from opensak.db.models import Cache as CacheModel
            full = s.query(CacheModel).filter_by(gc_code=gc_code).first()
            window._on_cache_selected(full)

        assert len(calls) == 1
        lat, lon, _ = calls[0]
        assert lat == pytest.approx(52.0)
        assert lon == pytest.approx(-1.0)

    def test_passes_active_filterset(self, window, monkeypatch):
        from opensak.db.database import get_session
        from opensak.filters.engine import GcCodeFilter
        with get_session() as s:
            cache = self._make_cache(s, gc_code="GCARG003")
            gc_code = cache.gc_code

        from opensak.filters.engine import FilterSet
        window._current_filterset = FilterSet().add(GcCodeFilter("GCARG"))

        calls = []

        def fake_get_nearby(session, lat, lon, radius_km, max_caches, filterset=None):
            calls.append(filterset)
            return [], 0

        monkeypatch.setattr("opensak.gui.mainwindow.get_nearby_caches", fake_get_nearby)
        monkeypatch.setattr(
            window._map_widget, "show_nearby_for_selection", lambda *a, **k: None,
        )

        with get_session() as s:
            from opensak.db.models import Cache as CacheModel
            full = s.query(CacheModel).filter_by(gc_code=gc_code).first()
            window._on_cache_selected(full)

        assert len(calls) == 1
        passed_fs = calls[0]
        assert passed_fs is not None
        assert len(passed_fs) > 0

    def test_no_active_filter_passes_empty_filterset(self, window, monkeypatch):
        from opensak.db.database import get_session
        with get_session() as s:
            cache = self._make_cache(s, gc_code="GCARG004")
            gc_code = cache.gc_code

        calls = []

        def fake_get_nearby(session, lat, lon, radius_km, max_caches, filterset=None):
            calls.append(filterset)
            return [], 0

        monkeypatch.setattr("opensak.gui.mainwindow.get_nearby_caches", fake_get_nearby)
        monkeypatch.setattr(
            window._map_widget, "show_nearby_for_selection", lambda *a, **k: None,
        )

        with get_session() as s:
            from opensak.db.models import Cache as CacheModel
            full = s.query(CacheModel).filter_by(gc_code=gc_code).first()
            window._on_cache_selected(full)

        assert len(calls) == 1
        passed_fs = calls[0]
        assert passed_fs is None or len(passed_fs) == 0


# ── Issue #1328: only the selected cache, switched with the map button ───────

class TestSingleCacheToggle1328:
    """With "selected cache only" on, selecting a cache shows just that
    cache (and its waypoints) instead of its surroundings; the button
    redraws the current selection either way. Off is the default."""

    @staticmethod
    def _make_cache(gc_code="GCSEL1328"):
        from opensak.db.database import get_session
        from opensak.db.models import Cache
        with get_session() as s:
            s.add(Cache(gc_code=gc_code, name="Selected", cache_type="Traditional Cache",
                        latitude=51.5074, longitude=-0.1278))
        return gc_code

    @staticmethod
    def _record(monkeypatch, window):
        calls: dict[str, list] = {"nearby": [], "single": [], "pan": []}
        mw = window._map_widget
        monkeypatch.setattr(mw, "show_nearby_for_selection",
                            lambda *a, **k: calls["nearby"].append(a))
        monkeypatch.setattr(mw, "show_single_cache", lambda *a, **k: calls["single"].append(a))
        monkeypatch.setattr(mw, "pan_to_cache", lambda *a, **k: calls["pan"].append(a))
        return calls

    def test_main_map_has_the_button(self, window):
        assert window._map_widget._single_cache_button is True

    def test_selection_shows_surroundings_by_default(self, window, monkeypatch):
        gc_code = self._make_cache()
        calls = self._record(monkeypatch, window)
        window._on_cache_selected(window._load_full_cache(gc_code))
        assert len(calls["nearby"]) == 1
        assert calls["single"] == [] and calls["pan"] == []

    def test_selection_shows_only_the_cache_when_on(self, window, monkeypatch):
        from opensak.gui.settings import get_settings
        get_settings().map_single_cache = True
        gc_code = self._make_cache()
        calls = self._record(monkeypatch, window)
        window._on_cache_selected(window._load_full_cache(gc_code))
        assert calls["nearby"] == []
        assert [a[0].gc_code for a in calls["single"]] == [gc_code]

    def test_switching_on_redraws_the_selection(self, window, monkeypatch):
        from opensak.gui.settings import get_settings
        gc_code = self._make_cache()
        window._on_cache_selected(window._load_full_cache(gc_code))
        calls = self._record(monkeypatch, window)
        window._map_widget._bridge.on_single_cache_toggled(True)
        assert get_settings().map_single_cache is True
        assert [a[0].gc_code for a in calls["single"]] == [gc_code]
        assert calls["nearby"] == []

    def test_switching_off_shows_surroundings_again(self, window, monkeypatch):
        from opensak.gui.settings import get_settings
        get_settings().map_single_cache = True
        gc_code = self._make_cache()
        window._on_cache_selected(window._load_full_cache(gc_code))
        calls = self._record(monkeypatch, window)
        window._map_widget._bridge.on_single_cache_toggled(False)
        assert get_settings().map_single_cache is False
        assert len(calls["nearby"]) == 1
        assert calls["nearby"][0][0].gc_code == gc_code
        assert calls["single"] == []

    def test_switching_without_selection(self, window, monkeypatch):
        calls = self._record(monkeypatch, window)
        window._map_widget._bridge.on_single_cache_toggled(True)
        window._map_widget._bridge.on_single_cache_toggled(False)
        assert calls == {"nearby": [], "single": [], "pan": []}

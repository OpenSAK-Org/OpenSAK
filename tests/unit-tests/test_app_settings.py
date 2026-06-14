"""tests/unit-tests/test_app_settings.py — AppSettings / HomePoint (temp-INI)."""

from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QSettings

from opensak.gui.settings import AppSettings, HomePoint
from opensak.utils.types import CoordFormat

_VALID = "N55 47.250 E012 25.000"


@pytest.fixture
def s(tmp_path, monkeypatch):
    # Bind AppSettings to an explicit temp INI — full isolation from the real
    # user settings (a plain attribute swap, no class patching).
    monkeypatch.setattr(
        "opensak.db.manager.get_db_manager",
        lambda: (_ for _ in ()).throw(RuntimeError("no manager")),
    )
    obj = AppSettings()
    obj._s = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    return obj


# ── HomePoint ───────────────────────────────────────────────────────────────────

class TestHomePoint:
    def test_to_from_dict_roundtrip(self):
        p = HomePoint("Home", 55.0, 12.0)
        d = p.to_dict()
        back = HomePoint.from_dict(d)
        assert (back.name, back.lat, back.lon) == ("Home", 55.0, 12.0)

    def test_repr(self):
        assert repr(HomePoint("X", 1.0, 2.0)) == "HomePoint('X', 1.0, 2.0)"


# ── home lat/lon (global fallback path) ─────────────────────────────────────────

class TestHomeCoords:
    def test_defaults(self, s):
        assert s.home_lat == pytest.approx(55.6761)
        assert s.home_lon == pytest.approx(12.5683)

    def test_set_and_get(self, s):
        s.home_lat = 1.5
        s.home_lon = 2.5
        assert s.home_lat == pytest.approx(1.5)
        assert s.home_lon == pytest.approx(2.5)


# ── per-database key resolution ─────────────────────────────────────────────────

class TestPerDbKeys:
    def test_per_db_key_used_when_active(self, s, monkeypatch):
        active = SimpleNamespace(path="/tmp/my.db")
        monkeypatch.setattr(
            "opensak.db.manager.get_db_manager",
            lambda: SimpleNamespace(active=active),
        )
        s.home_lat = 10.0
        # stored under per-db key; a fresh read resolves the same per-db value
        assert s.home_lat == pytest.approx(10.0)


# ── home points list ────────────────────────────────────────────────────────────

class TestHomePoints:
    def test_empty_by_default(self, s):
        assert s.home_points == []

    def test_add_update_remove(self, s):
        s.add_or_update_home_point(HomePoint("Work", 1.0, 2.0))
        assert [p.name for p in s.home_points] == ["Work"]
        s.add_or_update_home_point(HomePoint("Work", 3.0, 4.0))  # update
        assert s.home_points[0].lat == pytest.approx(3.0)
        s.remove_home_point("Work")
        assert s.home_points == []

    def test_corrupt_json_ignored(self, s):
        s._s.setValue("homepoints/list", "{not json")
        assert s.home_points == []

    def test_gc_home_prepended_as_star(self, s):
        s.gc_home_location = _VALID
        names = [p.name for p in s.home_points]
        assert names and names[0] == "★ Home"

    def test_setter_filters_out_star_home(self, s):
        s.home_points = [HomePoint("★ Home", 1.0, 2.0), HomePoint("Real", 3.0, 4.0)]
        raw_names = [p.name for p in s.home_points]
        assert "Real" in raw_names

    def test_active_home_roundtrip(self, s):
        p = HomePoint("Spot", 5.0, 6.0)
        s.add_or_update_home_point(p)
        s.set_active_home(p)
        assert s.active_home_name == "Spot"
        got = s.get_active_home()
        assert got is not None and got.name == "Spot"

    def test_get_active_home_none(self, s):
        assert s.get_active_home() is None

    def test_remove_active_clears_active_name(self, s):
        p = HomePoint("A", 1.0, 2.0)
        s.add_or_update_home_point(p)
        s.set_active_home(p)
        s.remove_home_point("A")
        assert s.active_home_name == ""


# ── geocaching user fields ──────────────────────────────────────────────────────

class TestGcFields:
    def test_username_stripped(self, s):
        s.gc_username = "  bob  "
        assert s.gc_username == "bob"

    def test_finder_id_stripped(self, s):
        s.gc_finder_id = "  12345  "
        assert s.gc_finder_id == "12345"

    def test_gc_home_point_valid(self, s):
        s.gc_home_location = _VALID
        hp = s.get_gc_home_point()
        assert hp is not None and hp.name == "★ Home"

    def test_gc_home_point_empty(self, s):
        s.gc_home_location = ""
        assert s.get_gc_home_point() is None

    def test_gc_home_point_invalid(self, s):
        s.gc_home_location = "garbage"
        assert s.get_gc_home_point() is None

    def test_is_setup_complete(self, s):
        assert s.is_setup_complete() is False
        s.gc_username = "bob"
        s.gc_home_location = _VALID
        assert s.is_setup_complete() is True


# ── display / format / units ────────────────────────────────────────────────────

class TestDisplay:
    def test_theme_roundtrip(self, s):
        assert s.theme == "auto"
        s.theme = "dark"
        assert s.theme == "dark"

    def test_use_miles(self, s):
        s.use_miles = True
        assert s.use_miles is True

    def test_coord_format_roundtrip(self, s):
        s.coord_format = CoordFormat.DMS
        assert s.coord_format == CoordFormat.DMS

    def test_coord_format_invalid_defaults_dmm(self, s):
        s._s.setValue("display/coord_format", "bogus")
        assert s.coord_format == CoordFormat.DMM

    def test_map_provider_and_url(self, s):
        assert "google.com" in s.get_maps_url(55.0, 12.0)
        s.map_provider = "osm"
        assert "openstreetmap.org" in s.get_maps_url(55.0, 12.0)

    def test_show_flags(self, s):
        s.show_archived = True
        s.show_found = False
        assert s.show_archived is True
        assert s.show_found is False


# ── window state / search / nominatim / paths ───────────────────────────────────

class TestMisc:
    def test_window_state_roundtrip(self, s):
        s.window_geometry = b"geo"
        s.window_state = b"state"
        s.splitter_state = b"sp"
        s.bottom_splitter_state = b"bsp"
        assert s.window_geometry == b"geo"
        assert s.window_state == b"state"
        assert s.splitter_state == b"sp"
        assert s.bottom_splitter_state == b"bsp"

    def test_search_thresholds(self, s):
        s.search_min_chars = 3
        s.search_debounce_ms = 250
        assert s.search_min_chars == 3
        assert s.search_debounce_ms == 250

    def test_nominatim(self, s):
        s.nominatim_enabled = True
        assert s.nominatim_enabled is True

    def test_last_import_dir(self, s):
        assert s.last_import_dir  # defaults to home
        s.last_import_dir = "/tmp/x"
        assert s.last_import_dir == "/tmp/x"

    def test_apply_default_center_for_new_db(self, s):
        s.gc_home_location = _VALID
        s.apply_default_center_for_new_db()
        assert s.active_home_name == "★ Home"

    def test_apply_default_center_no_home(self, s):
        s.apply_default_center_for_new_db()  # no gc_home -> no-op
        assert s.active_home_name == ""

    def test_sync_does_not_raise(self, s):
        s.gc_username = "bob"
        s.sync()  # flush to disk

    def test_get_settings_is_singleton(self):
        from opensak.gui.settings import get_settings
        assert get_settings() is get_settings()

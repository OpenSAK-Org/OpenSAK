# tests/unit-tests/test_center_point_picker.py — CenterPointPicker widget (#511).
#
# Covers only the widget in isolation. Its integration into the "Afstand"
# section of FilterDialog is covered separately in test_filter_dialog.py
# (TestCenterPointIntegration).

from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from opensak.gui.widgets.center_point_picker import CenterPointPicker
from opensak.gui.widgets import center_point_picker as cpp


def _settings(home_lat=55.0, home_lon=12.0, home_points=None, coord_format=None):
    from opensak.utils.types import CoordFormat
    return SimpleNamespace(
        home_lat=home_lat,
        home_lon=home_lon,
        home_points=home_points or [],
        coord_format=coord_format or CoordFormat.DD,
    )


@pytest.fixture
def picker(qtbot, monkeypatch):
    monkeypatch.setattr("opensak.gui.settings.get_settings", lambda: _settings())
    p = CenterPointPicker()
    qtbot.addWidget(p)
    return p


class TestDefaultState:
    def test_defaults_to_home(self, picker):
        assert picker.get_center() == (55.0, 12.0)
        assert picker.to_state() == {"kind": "home"}

    def test_no_home_set_returns_none(self, qtbot, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.settings.get_settings",
            lambda: _settings(home_lat=0.0, home_lon=0.0),
        )
        p = CenterPointPicker()
        qtbot.addWidget(p)
        assert p.get_center() is None


class TestSavedHomePoints:
    def test_saved_point_is_selectable(self, qtbot, monkeypatch):
        from opensak.gui.settings import HomePoint
        monkeypatch.setattr(
            "opensak.gui.settings.get_settings",
            lambda: _settings(home_points=[HomePoint("Cabin", 60.0, 10.0)]),
        )
        p = CenterPointPicker()
        qtbot.addWidget(p)
        p.set_state({"kind": "point", "name": "Cabin"})
        assert p.get_center() == (60.0, 10.0)
        assert p.to_state() == {"kind": "point", "name": "Cabin"}

    def test_missing_point_falls_back_to_home(self, picker):
        picker.set_state({"kind": "point", "name": "DoesNotExist"})
        assert picker.get_center() == (55.0, 12.0)


class TestSelectedCache:
    def test_no_cache_returns_none(self, picker):
        picker.set_state({"kind": "cache"})
        # "cache"-valget findes slet ikke i comboen når ingen cache er sat,
        # så set_state falder tilbage til Home.
        assert picker.get_center() == (55.0, 12.0)

    def test_cache_is_selectable_once_set(self, picker):
        cache = SimpleNamespace(gc_code="GC1AB23", name="Troll Bridge",
                                 latitude=56.1, longitude=10.2)
        picker.set_current_cache(cache)
        picker.set_state({"kind": "cache"})
        assert picker.get_center() == (56.1, 10.2)

    def test_clearing_cache_falls_back(self, picker):
        cache = SimpleNamespace(gc_code="GC1AB23", name="Troll Bridge",
                                 latitude=56.1, longitude=10.2)
        picker.set_current_cache(cache)
        picker.set_state({"kind": "cache"})
        picker.set_current_cache(None)
        # "cache"-valget forsvinder fra comboen når ingen cache er sat længere
        # — pickeren falder tilbage til Home, ligesom for et slettet hjemmepunkt.
        assert picker.get_center() == (55.0, 12.0)


class TestCustomCoordinate:
    def test_valid_dd_coordinate(self, picker):
        picker.set_state({"kind": "custom", "text": "56.5, 10.1"})
        assert picker.get_center() == (56.5, 10.1)

    def test_invalid_text_returns_none(self, picker):
        picker.set_state({"kind": "custom", "text": "not a coordinate"})
        assert picker.get_center() is None

    def test_empty_text_returns_none(self, picker):
        picker.set_state({"kind": "custom", "text": ""})
        assert picker.get_center() is None

    def test_roundtrip_state(self, picker):
        picker.set_state({"kind": "custom", "text": "56.5, 10.1"})
        state = picker.to_state()
        assert state == {"kind": "custom", "text": "56.5, 10.1"}


class TestChangedSignal:
    def test_emits_on_selection_change(self, qtbot, picker, monkeypatch):
        from opensak.gui.settings import HomePoint
        monkeypatch.setattr(
            "opensak.gui.settings.get_settings",
            lambda: _settings(home_points=[HomePoint("Cabin", 60.0, 10.0)]),
        )
        picker.refresh()
        with qtbot.waitSignal(picker.changed, timeout=1000):
            picker.set_state({"kind": "point", "name": "Cabin"})

    def test_emits_on_custom_text_edit(self, qtbot, picker):
        picker.set_state({"kind": "custom", "text": ""})
        with qtbot.waitSignal(picker.changed, timeout=1000):
            picker._custom_edit.setText("56.5, 10.1")

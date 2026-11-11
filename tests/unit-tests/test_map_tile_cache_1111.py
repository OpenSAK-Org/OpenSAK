# tests/unit-tests/test_map_tile_cache_1111.py — #1111 (step 3): the map's
# tiles are kept in a persistent disk HTTP cache shared by every map widget
# (gui/map_profile.py), its size is set in Settings → Map, and the map says
# "Offline – map tiles unavailable" when tiles fail to load.

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWebEngineCore import QWebEngineProfile

from opensak.gui import map_profile
from opensak.gui.map_widget import MapWidget, _js_inline_arg
from opensak.gui.settings import get_settings
from opensak.lang import tr


class FakeProfile:
    """Records the setters map_profile calls; deleteLater() into a log."""

    log: list = []

    def __init__(self, name: str = "") -> None:
        self.name = name
        self.calls: dict = {}

    def deleteLater(self) -> None:
        FakeProfile.log.append(("delete", self))

    def __getattr__(self, attr):
        return lambda *a: self.calls.__setitem__(attr, a)


@pytest.fixture(autouse=True)
def _fresh_profile_state(monkeypatch):
    for name, value in (("_profile", None), ("_interceptor", None),
                        ("_users", 0), ("_quitting", False)):
        monkeypatch.setattr(map_profile, name, value)
    FakeProfile.log = []


def _acquire():
    return map_profile.acquire(FakeProfile)  # type: ignore[arg-type]


class TestSharedProfile:
    def test_one_profile_for_all_maps(self):
        a = _acquire()
        b = _acquire()
        assert a is b
        assert a.name == map_profile.STORAGE_NAME
        assert map_profile._users == 2

    def test_disk_cache_with_size_cap(self):
        get_settings().map_tile_cache_mb = 150
        p = _acquire()
        assert p.calls["setHttpCacheType"] == (QWebEngineProfile.HttpCacheType.DiskHttpCache,)
        assert p.calls["setHttpCacheMaximumSize"] == (150 * 1024 * 1024,)
        cache_dir = map_profile.map_cache_dir()
        assert p.calls["setPersistentStoragePath"] == (str(cache_dir),)
        assert p.calls["setCachePath"] == (str(cache_dir / "http"),)
        assert p.calls["setPersistentCookiesPolicy"] == (
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies,)

    def test_zero_turns_disk_cache_off(self):
        get_settings().map_tile_cache_mb = 0
        p = _acquire()
        assert p.calls["setHttpCacheType"] == (QWebEngineProfile.HttpCacheType.MemoryHttpCache,)
        assert "setHttpCacheMaximumSize" not in p.calls

    def test_tile_referer_interceptor_installed(self):
        p = _acquire()
        (interceptor,) = p.calls["setUrlRequestInterceptor"]
        assert isinstance(interceptor, map_profile.TileInterceptor)

    def test_cache_dir_under_app_data(self, monkeypatch, tmp_path):
        monkeypatch.setattr("opensak.config.get_app_data_dir", lambda: tmp_path)
        assert map_profile.map_cache_dir() == tmp_path / "map_cache"


class TestTeardownOrder:
    def test_kept_while_app_runs(self):
        _acquire()
        map_profile.release()
        assert FakeProfile.log == []
        assert map_profile._profile is not None

    def test_deleted_at_quit_when_no_map_left(self):
        p = _acquire()
        map_profile.release()
        map_profile._on_about_to_quit()
        assert FakeProfile.log == [("delete", p)]
        assert map_profile._profile is None

    def test_deleted_after_the_last_page_at_quit(self):
        # aboutToQuit reaches map_profile first (it connected first), then the
        # MapWidgets' cleanups: the profile must go only after the last page.
        p = _acquire()
        _acquire()
        map_profile._on_about_to_quit()
        assert FakeProfile.log == []
        map_profile.release()
        assert FakeProfile.log == []
        map_profile.release()
        assert FakeProfile.log == [("delete", p)]

    def test_widget_cleanup_releases_after_deleting_page(self, qtbot):
        w = MapWidget()
        qtbot.addWidget(w)
        p = _acquire()
        w._profile = p
        w._page = SimpleNamespace(deleteLater=lambda: FakeProfile.log.append("page"))
        w._view = SimpleNamespace(setPage=lambda _p: None)
        map_profile._on_about_to_quit()
        w._cleanup_webengine()
        assert FakeProfile.log == ["page", ("delete", p)]


class TestClearCache:
    @pytest.fixture
    def cache_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(map_profile, "map_cache_dir", lambda: tmp_path)
        http = tmp_path / "http" / "Cache_Data"
        http.mkdir(parents=True)
        (http / "f_000001").write_bytes(b"x" * 1000)
        (http / "f_000002").write_bytes(b"x" * 500)
        return tmp_path

    def test_size(self, cache_dir):
        assert map_profile.cache_size_bytes() == 1500

    def test_no_map_open_removes_the_folder(self, cache_dir):
        assert map_profile.clear_cache() == 1500
        assert not (cache_dir / "http").exists()
        assert map_profile.cache_size_bytes() == 0

    def test_map_open_clears_through_the_profile(self, cache_dir):
        p = _acquire()
        assert map_profile.clear_cache() == 1500
        assert "clearHttpCache" in p.calls
        # Chromium owns the open files; the profile clears them itself.
        assert (cache_dir / "http").exists()

    def test_missing_folder(self, monkeypatch, tmp_path):
        monkeypatch.setattr(map_profile, "map_cache_dir", lambda: tmp_path / "none")
        assert map_profile.clear_cache() == 0

    def test_settings_button(self, qtbot, monkeypatch):
        from opensak.gui.dialogs import settings_dialog as sd
        monkeypatch.setattr(map_profile, "clear_cache", lambda: 3 * 1024 * 1024)
        shown: list = []
        monkeypatch.setattr(sd.QMessageBox, "information", lambda *a, **_k: shown.append(a))
        dlg = sd.SettingsDialog()
        qtbot.addWidget(dlg)
        assert dlg._map_tile_cache_clear_btn.text() == tr("settings_map_tile_cache_clear")
        dlg._map_tile_cache_clear_btn.click()
        assert shown and shown[0][2] == tr("settings_map_tile_cache_cleared", size="3.0")


class TestSetting:
    def test_default_200_mb(self):
        assert get_settings().map_tile_cache_mb == 200

    @pytest.mark.parametrize("stored, expected", [(500, 500), (-5, 0), ("junk", 200)])
    def test_stored_values(self, stored, expected):
        from opensak.settings_store import get_store
        get_store().set("display.map_tile_cache_mb", stored)
        assert get_settings().map_tile_cache_mb == expected

    def test_settings_dialog_round_trip(self, qtbot):
        from opensak.gui.dialogs.settings_dialog import SettingsDialog
        get_settings().map_tile_cache_mb = 300
        dlg = SettingsDialog()
        qtbot.addWidget(dlg)
        assert dlg._map_tile_cache.value() == 300
        assert dlg._map_tile_cache.specialValueText() == tr("settings_map_tile_cache_off")
        dlg._map_tile_cache.setValue(0)
        dlg._save()
        assert get_settings().map_tile_cache_mb == 0


class TestOfflineNotice:
    def test_notice_wired_to_tile_events(self):
        html = MapWidget._render_map_html(55.0, 10.0)
        assert "tileLayer.on('tileerror'" in html
        assert "tileLayer.on('tileload'" in html
        assert "MAP_TILES_OFFLINE" not in html
        assert f"div.textContent = {_js_inline_arg(tr('map_tiles_offline'))};" in html

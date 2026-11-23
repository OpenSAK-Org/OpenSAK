# tests/unit-tests/test_offline_webengine_1361.py — #1361 (part 1b): Offline
# mode in the WebEngine views. Chromium's request interceptor runs before its
# HTTP cache, so blocking a tile there would hide the cached copy too. Every
# map layer therefore comes through the tile proxy (opensak://tiles/), which
# in Offline mode serves from its own disk cache only. The description pane
# gets a profile of its own whose interceptor blocks remote images in Offline
# mode, with a Load images button per cache.

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QByteArray, QObject, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEngineUrlRequestJob

from opensak import net
from opensak.gui import web_assets
from opensak.gui.cache_detail import _DescInterceptor, has_remote_content
from opensak.gui.settings import get_settings
from opensak.gui.tile_disk_cache import TileDiskCache

OSM = "opensak://tiles/osm/3/4/2"


class FakeJob(QObject):
    """Stands in for QWebEngineUrlRequestJob; records reply()/fail()."""

    def __init__(self, url: str):
        super().__init__()
        self._url = QUrl(url)
        self.replied: tuple[bytes, bytes] | None = None
        self.error = None

    def requestUrl(self):
        return self._url

    def requestMethod(self):
        return QByteArray(b"GET")

    def reply(self, content_type, device):
        self.replied = (bytes(content_type), bytes(device.readAll().data()))

    def fail(self, error):
        self.error = error


class FakeReply(QObject):
    finished = Signal()

    def isRunning(self):
        return True

    def abort(self):
        pass


class FakeNam:
    """Records the requests the proxy sends; they never finish."""

    def __init__(self):
        self.requests: list = []

    def get(self, request):
        self.requests.append(request)
        return FakeReply()


@pytest.fixture
def handler(qapp, tmp_path):
    h = web_assets.AssetSchemeHandler()
    h._disk_cache = TileDiskCache(tmp_path / "proxy_tiles", 10 * 1024 * 1024)
    h._nam = FakeNam()  # type: ignore[assignment]
    return h


def _cache_tile(handler, layer_id: str, age: float, header: str) -> None:
    handler._disk_cache.put(layer_id, 3, 4, 2, b"PNG", "image/png", header,
                            now=time.time() - age)


def _request(handler, url: str = OSM) -> FakeJob:
    job = FakeJob(url)
    handler.requestStarted(job)  # type: ignore[arg-type]
    return job


# ── Tile proxy ────────────────────────────────────────────────────────────


class TestTileProxyOnline:
    def test_keyless_layer_is_fetched(self, handler):
        job = _request(handler)
        assert job.error is None and job.replied is None   # waiting for the reply
        (request,) = handler._nam.requests
        assert request.url().toString() == "https://tile.openstreetmap.org/3/4/2.png"

    def test_osm_gets_its_referer(self, handler):
        # Set by the TileInterceptor while Chromium fetched OSM itself.
        _request(handler)
        (request,) = handler._nam.requests
        assert bytes(request.rawHeader("Referer").data()) == b"https://www.openstreetmap.org/"

    def test_other_providers_get_none(self, handler):
        _request(handler, "opensak://tiles/opentopomap/3/4/2")
        (request,) = handler._nam.requests
        assert not request.hasRawHeader("Referer")

    def test_fresh_tile_from_cache(self, handler):
        _cache_tile(handler, "osm", 10, "max-age=100")
        job = _request(handler)
        assert job.replied == (b"image/png", b"PNG")
        assert handler._nam.requests == []

    def test_stale_tile_is_refreshed(self, handler):
        _cache_tile(handler, "osm", 500, "max-age=100, stale-while-revalidate=1000")
        job = _request(handler)
        assert job.replied is None
        assert len(handler._nam.requests) == 1


class TestTileProxyOffline:
    @pytest.fixture(autouse=True)
    def offline(self):
        net.set_offline_mode(True)

    def test_fresh_tile_from_cache(self, handler):
        _cache_tile(handler, "osm", 10, "max-age=100")
        assert _request(handler).replied == (b"image/png", b"PNG")
        assert handler._nam.requests == []

    def test_stale_tile_from_cache(self, handler):
        # For as long as the provider allows a stale copy, as when it can't
        # be reached.
        _cache_tile(handler, "osm", 500, "max-age=100, stale-while-revalidate=1000")
        assert _request(handler).replied == (b"image/png", b"PNG")
        assert handler._nam.requests == []

    def test_expired_tile_fails(self, handler):
        _cache_tile(handler, "osm", 5000, "max-age=100, stale-while-revalidate=1000")
        job = _request(handler)
        assert job.error == QWebEngineUrlRequestJob.Error.RequestFailed
        assert handler._nam.requests == []

    def test_uncached_tile_fails_without_a_request(self, handler):
        job = _request(handler)
        assert job.error == QWebEngineUrlRequestJob.Error.RequestFailed
        assert handler._nam.requests == []

    def test_layer_that_may_not_be_kept_fails(self, handler):
        get_settings().map_api_keys = {"esri": "SECRET"}
        job = _request(handler, "opensak://tiles/esri_aerial/3/4/2")
        assert job.error == QWebEngineUrlRequestJob.Error.RequestFailed
        assert handler._nam.requests == []


# ── Tile cache size ───────────────────────────────────────────────────────


class TestTileCacheSize:
    def test_no_walk_while_under_the_cap(self, tmp_path, monkeypatch):
        cache = TileDiskCache(tmp_path / "c", 10 * 1024 * 1024)
        cache.put("osm", 1, 0, 0, b"x" * 100, "image/png", "max-age=100")
        cache.prune()   # counts the folder once
        assert cache._size == cache.size_bytes()

        def walk():
            raise AssertionError("folder walked under the cap")

        monkeypatch.setattr(cache, "_files", walk)
        for x in range(60):      # past _PRUNE_EVERY
            cache.put("osm", 7, x, 0, b"x" * 100, "image/png", "max-age=100")
        cache.prune()

    def test_count_follows_writes_and_pruning(self, tmp_path):
        cache = TileDiskCache(tmp_path / "c", 2000)
        cache.prune()
        for x in range(10):
            cache.put("osm", 4, x, 0, b"x" * 300, "image/png", "max-age=100")
        cache.put("osm", 4, 0, 0, b"y" * 300, "image/png", "max-age=100")  # replaced
        assert cache._size == sum(f[1] for f in cache._files())
        cache.prune()
        assert cache.size_bytes() <= 2000
        assert cache._size == cache.size_bytes()
        cache.clear()
        assert cache._size == 0


# ── Description pane ──────────────────────────────────────────────────────


@pytest.mark.parametrize("body, remote", [
    ('<img src="https://img.geocaching.com/x.jpg">', True),
    ("<IMG SRC='http://example.com/a.png'>", True),
    ('<img src=//example.com/a.png>', True),
    ('<img srcset="https://example.com/a.png 2x">', True),
    ('<td background="https://example.com/bg.gif">', True),
    ('<div style="background: url(https://example.com/bg.png)">', True),
    ('<link rel="stylesheet" href="https://example.com/s.css">', True),
    ('<a href="https://example.com/">a link</a>', False),
    ('<img src="data:image/png;base64,AAAA">', False),
    ("Plain text, https://example.com mentioned", False),
    ("", False),
])
def test_has_remote_content(body, remote):
    assert has_remote_content(body) is remote


class TestDescInterceptor:
    @staticmethod
    def _blocked(interceptor, url: str) -> bool:
        blocked: list = []
        info = SimpleNamespace(requestUrl=lambda: QUrl(url), block=blocked.append)
        interceptor.interceptRequest(info)
        return blocked == [True]

    def test_nothing_blocked_online(self, qapp):
        assert not self._blocked(_DescInterceptor(), "https://example.com/a.png")

    def test_remote_blocked_in_offline_mode(self, qapp):
        net.set_offline_mode(True)
        interceptor = _DescInterceptor()
        assert self._blocked(interceptor, "https://example.com/a.png")
        assert self._blocked(interceptor, "http://example.com/a.png")
        assert not self._blocked(interceptor, "data:image/png;base64,AAAA")
        interceptor.allow_remote = True     # Load images
        assert not self._blocked(interceptor, "https://example.com/a.png")


class TestLoadImagesBar:
    IMG = '<p>Hi</p><img src="https://img.geocaching.com/x.jpg">'

    @pytest.fixture
    def panel(self, qapp):
        from opensak.gui.cache_detail import CacheDetailPanel
        p = CacheDetailPanel()
        p._current_gc_code = "GC1"
        return p

    def test_hidden_online(self, panel):
        panel._set_description(self.IMG)
        assert panel._images_bar.isHidden()

    def test_shown_in_offline_mode(self, panel):
        net.set_offline_mode(True)
        panel._set_description(self.IMG)
        assert not panel._images_bar.isHidden()

    def test_hidden_without_remote_content(self, panel):
        net.set_offline_mode(True)
        panel._set_description("<p>No images here</p>")
        assert panel._images_bar.isHidden()

    def test_load_images_is_per_cache(self, panel):
        net.set_offline_mode(True)
        panel._set_description(self.IMG)
        panel._load_images()
        assert panel._images_bar.isHidden()
        assert panel._images_allowed_for == "GC1"
        panel._set_description(self.IMG)     # the same cache again
        assert panel._images_bar.isHidden()
        panel._current_gc_code = "GC2"
        panel._set_description(self.IMG)
        assert not panel._images_bar.isHidden()
        assert panel._images_allowed_for is None

    def test_clear_resets(self, panel):
        net.set_offline_mode(True)
        panel._set_description(self.IMG)
        panel._load_images()
        panel.clear()
        assert panel._images_bar.isHidden()
        assert panel._images_allowed_for is None

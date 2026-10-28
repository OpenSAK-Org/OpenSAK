# tests/unit-tests/test_web_assets_1111.py — bundled Leaflet assets served via
# opensak:// instead of unpkg.com (#1111, step 1): the map HTML only refers to
# local assets, every referenced file exists, the scheme handler serves them
# (and nothing outside the asset folder), and opensak.spec bundles them.

import re
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QByteArray, QObject, QUrl
from PySide6.QtWebEngineCore import QWebEngineUrlRequestJob

from opensak.gui import web_assets
from opensak.gui.map_widget import MAP_HTML, MapWidget

ROOT = Path(__file__).resolve().parents[2]

_ASSET_URL = re.compile(r'(?:href|src)="(opensak://[^"]+)"')


def _asset_urls(html: str) -> list[str]:
    return _ASSET_URL.findall(html)


class FakeJob(QObject):
    """Stands in for QWebEngineUrlRequestJob; records reply()/fail()."""

    def __init__(self, url: str, method: bytes = b"GET"):
        super().__init__()
        self._url = QUrl(url)
        self._method = QByteArray(method)
        self.replied: tuple[bytes, bytes] | None = None
        self.error = None

    def requestUrl(self):
        return self._url

    def requestMethod(self):
        return self._method

    def reply(self, content_type, device):
        self.replied = (bytes(content_type), bytes(device.readAll().data()))

    def fail(self, error):
        self.error = error


class TestMapHtml:
    def test_no_cdn(self):
        assert "unpkg.com" not in MAP_HTML
        # No stylesheet or script from the network: only opensak:// and qrc://.
        for tag in re.findall(r"<(?:link|script)\b[^>]*>", MAP_HTML):
            m = re.search(r'(?:href|src)="([^"]+)"', tag)
            if m:
                assert m.group(1).startswith(("opensak://", "qrc:///")), tag

    def test_libraries_referenced(self):
        urls = _asset_urls(MapWidget._render_map_html(55.0, 10.0))
        assert urls == [
            "opensak://assets/leaflet/leaflet.css",
            "opensak://assets/leaflet.markercluster/MarkerCluster.css",
            "opensak://assets/leaflet.markercluster/MarkerCluster.Default.css",
            "opensak://assets/leaflet-geoman/leaflet-geoman.css",
            "opensak://assets/leaflet/leaflet.js",
            "opensak://assets/leaflet.markercluster/leaflet.markercluster.js",
            "opensak://assets/leaflet-geoman/leaflet-geoman.js",
        ]

    def test_every_url_resolves(self):
        for url in _asset_urls(MAP_HTML):
            q = QUrl(url)
            assert web_assets.resolve_asset(q.host(), q.path()) is not None, url

    def test_versions(self):
        leaflet = (web_assets.WEB_DIR / "leaflet" / "leaflet.js").read_text(encoding="utf-8")
        assert "Leaflet 1.9.4" in leaflet
        geoman = (web_assets.WEB_DIR / "leaflet-geoman" / "leaflet-geoman.js").read_text(encoding="utf-8")
        assert "2.19.3" in geoman

    def test_css_images_bundled(self):
        # leaflet.css refers to images/… relative to itself (layer switcher,
        # default marker); those must be served from the same folder.
        css = (web_assets.WEB_DIR / "leaflet" / "leaflet.css").read_text(encoding="utf-8")
        images = set(re.findall(r"url\((images/[^)]+)\)", css))
        assert images
        for rel in images:
            assert web_assets.resolve_asset("assets", f"/leaflet/{rel}") is not None, rel

    def test_licences_bundled(self):
        for lib in ("leaflet", "leaflet.markercluster", "leaflet-geoman"):
            assert (web_assets.WEB_DIR / lib / "LICENSE").is_file(), lib
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        for name in ("Leaflet 1.9.4", "Leaflet.markercluster 1.5.3",
                     "Leaflet-Geoman (free) 2.19.3", "BSD-2-Clause", "MIT"):
            assert name in notices, name


class TestResolveAsset:
    def test_known_file(self):
        path = web_assets.resolve_asset("assets", "/leaflet/leaflet.js")
        assert path == (web_assets.WEB_DIR / "leaflet" / "leaflet.js").resolve()

    @pytest.mark.parametrize("host, path", [
        ("tiles", "/leaflet/leaflet.js"),          # unknown host
        ("assets", "/leaflet/missing.js"),         # no such file
        ("assets", "/leaflet"),                    # a directory
        ("assets", "/leaflet/LICENSE"),            # no served MIME type
        ("assets", "/README.md"),
        ("assets", "/../web_assets.py"),
        ("assets", "/../../gui/web_assets.py"),    # path traversal
    ])
    def test_rejected(self, host, path):
        assert web_assets.resolve_asset(host, path) is None


class TestSchemeHandler:
    @pytest.fixture
    def handler(self, qapp):
        return web_assets.AssetSchemeHandler()

    @pytest.mark.parametrize("url, mime", [
        ("opensak://assets/leaflet/leaflet.js", b"text/javascript"),
        ("opensak://assets/leaflet.markercluster/MarkerCluster.css", b"text/css"),
        ("opensak://assets/leaflet/images/layers.png", b"image/png"),
    ])
    def test_serves_file(self, handler, url, mime):
        job = FakeJob(url)
        handler.requestStarted(job)  # type: ignore[arg-type]
        assert job.error is None
        assert job.replied is not None
        content_type, body = job.replied
        q = QUrl(url)
        target = web_assets.resolve_asset(q.host(), q.path())
        assert target is not None
        assert content_type == mime
        assert body == target.read_bytes()

    def test_missing_file(self, handler):
        job = FakeJob("opensak://assets/leaflet/nope.js")
        handler.requestStarted(job)  # type: ignore[arg-type]
        assert job.replied is None
        assert job.error == QWebEngineUrlRequestJob.Error.UrlNotFound

    def test_only_get(self, handler):
        job = FakeJob("opensak://assets/leaflet/leaflet.js", b"POST")
        handler.requestStarted(job)  # type: ignore[arg-type]
        assert job.replied is None
        assert job.error == QWebEngineUrlRequestJob.Error.RequestDenied


def test_register_scheme_after_app_is_noop(qapp):
    # Too late once the QApplication exists: must not raise or warn-register.
    web_assets.register_scheme()


def test_app_registers_scheme_before_qapplication():
    src = (ROOT / "src" / "opensak" / "app.py").read_text(encoding="utf-8")
    assert src.index("register_scheme()") < src.index("QApplication(sys.argv)")


def test_spec_bundles_web_assets():
    spec = (ROOT / "opensak.spec").read_text(encoding="utf-8")
    assert '("src/opensak/assets/web",               "opensak/assets/web")' in spec

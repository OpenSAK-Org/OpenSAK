"""
web_assets.py — Serve bundled web assets (Leaflet & plugins) via opensak://.

The map page loads Leaflet, Leaflet.markercluster and Leaflet-Geoman from
src/opensak/assets/web/ instead of unpkg.com, so the map (pins, clustering,
map actions) works with no network and has no runtime dependency on a CDN
(#1111, step 1).

The page itself keeps its qrc:///<timestamp> base URL (qwebchannel.js lives
in qrc). A file:// base URL would collide with QtWebEngine's file-access
rules, so the assets come from a custom scheme instead:

    opensak://assets/leaflet/leaflet.js  →  assets/web/leaflet/leaflet.js

The scheme must be registered before the QApplication is created
(register_scheme(), called from app.main()); the handler is installed on the
shared map profile (install_handler(), called from map_profile.py).

#747, #1361: the same scheme proxies the tiles of every map layer:

    opensak://tiles/esri_aerial/12/2145/1434  →  the provider's tile URL

The API key is added here, so it never appears in the page or in Leaflet's
tile URLs. Chromium never writes custom-scheme responses to its HTTP cache,
and the QNetworkAccessManager used here has no cache: the tiles a layer may
keep (MapLayer.offline) are cached by TileDiskCache (tile_disk_cache.py),
filed by layer/z/x/y, and offline=False tiles stay off the disk.

With Offline mode on (#1361) the proxy serves tiles from that cache only
and never goes online; a tile that isn't cached fails, and the map shows
its offline notice.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QBuffer, QCoreApplication, QIODevice, QObject, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWebEngineCore import (
    QWebEngineProfile,
    QWebEngineUrlRequestJob,
    QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler,
)

SCHEME = b"opensak"
ASSETS_HOST = "assets"
# Prefix for asset URLs in the map HTML, e.g. ASSET_BASE + "leaflet/leaflet.js".
ASSET_BASE = "opensak://assets/"
TILES_HOST = "tiles"
# Prefix for proxied tile URLs (#747), e.g. TILE_BASE + "esri_aerial/{z}/{x}/{y}".
TILE_BASE = "opensak://tiles/"

WEB_DIR = Path(__file__).parent.parent / "assets" / "web"

if TYPE_CHECKING:
    from opensak.gui.map_layers import MapLayer
    from opensak.gui.tile_disk_cache import TileDiskCache

# The Referer some tile servers ask for (the OSM tile usage policy). Set by
# the map profile's request interceptor while Chromium fetched OSM itself.
_REFERERS = {
    "tile.openstreetmap.org": b"https://www.openstreetmap.org/",
}

_MIME_TYPES = {
    ".js": b"text/javascript",
    ".css": b"text/css",
    ".png": b"image/png",
    ".svg": b"image/svg+xml",
}


def register_scheme() -> None:
    """Register the opensak:// scheme with QtWebEngine.

    Must run before the QApplication exists; later calls (and repeated ones)
    are no-ops, since Qt only warns and ignores a registration that comes
    too late."""
    if QCoreApplication.instance() is not None:
        return
    if QWebEngineUrlScheme.schemeByName(SCHEME).name() == SCHEME:
        return
    scheme = QWebEngineUrlScheme(SCHEME)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    # Secure: the qrc:// map page may load it without mixed-content blocking.
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.SecureScheme
        | QWebEngineUrlScheme.Flag.CorsEnabled
    )
    QWebEngineUrlScheme.registerScheme(scheme)


def resolve_asset(host: str, path: str) -> Path | None:
    """Map an opensak:// URL's host and path to a bundled file, or None.

    Only files under WEB_DIR with a known MIME type are served — no path
    traversal out of the asset folder."""
    if host != ASSETS_HOST:
        return None
    root = WEB_DIR.resolve()
    target = (root / path.lstrip("/")).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    if target.suffix.lower() not in _MIME_TYPES:
        return None
    return target


def parse_tile_path(path: str) -> tuple[MapLayer, int, int, int] | None:
    """Split an opensak://tiles/ path ("/<layer>/<z>/<x>/<y>") into the
    layer and tile numbers, or None."""
    from opensak.gui.map_layers import get_layer
    parts = path.strip("/").split("/")
    if len(parts) != 4 or not all(p.isdigit() for p in parts[1:]):
        return None
    layer = get_layer(parts[0])
    if layer is None:
        return None
    z, x, y = (int(p) for p in parts[1:])
    if z > 30 or x >= 2 ** z or y >= 2 ** z:
        return None
    return layer, z, x, y


def resolve_tile(path: str) -> str | None:
    """Map an opensak://tiles/ path to the provider's tile URL, with the
    API key added, or None (see parse_tile_path). A keyed layer without a
    key resolves to None."""
    parsed = parse_tile_path(path)
    return None if parsed is None else upstream_tile_url(*parsed)


def upstream_tile_url(layer: MapLayer, z: int, x: int, y: int) -> str | None:
    """The provider's URL of one tile, with the API key added; None for a
    keyed layer without a key."""
    key = ""
    if layer.key_provider:
        from opensak.gui.settings import get_settings
        key = get_settings().map_api_keys.get(layer.key_provider, "")
        if not key:
            return None
    return layer.upstream_url(z, x, y, key)


class AssetSchemeHandler(QWebEngineUrlSchemeHandler):
    """Answers opensak://assets/... requests from WEB_DIR, and proxies
    opensak://tiles/... requests (#747)."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._nam: QNetworkAccessManager | None = None
        self._disk_cache: TileDiskCache | None = None

    def _tile_cache(self) -> TileDiskCache:
        if self._disk_cache is None:
            from opensak.gui.map_profile import proxy_tile_cache
            self._disk_cache = proxy_tile_cache()
        return self._disk_cache

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:
        url = job.requestUrl()
        if job.requestMethod().data() != b"GET":
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
            return
        if url.host() == TILES_HOST:
            self._proxy_tile(job, url.path())
            return
        target = resolve_asset(url.host(), url.path())
        if target is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        try:
            data = target.read_bytes()
        except OSError:
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            return
        _reply_bytes(job, _MIME_TYPES[target.suffix.lower()], data)

    def _proxy_tile(self, job: QWebEngineUrlRequestJob, path: str) -> None:
        parsed = parse_tile_path(path)
        upstream = None if parsed is None else upstream_tile_url(*parsed)
        if parsed is None or upstream is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        layer, z, x, y = parsed
        # Only layers whose tiles may be kept use the disk cache (#1111).
        cache = self._tile_cache() if layer.offline else None
        cached = cache.get(layer.id, z, x, y) if cache is not None else None
        from opensak import net
        if cached is not None and (cached.fresh or net.offline_mode()):
            _reply_bytes(job, cached.mime.encode(), cached.data)
            return
        if net.offline_mode():
            # #1361: cached tiles only — the map shows its offline notice.
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            return
        if self._nam is None:
            # No cache set on the manager: nothing it fetches is stored.
            self._nam = QNetworkAccessManager(self)
        request = QNetworkRequest(QUrl(upstream))
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, _user_agent())
        referer = _REFERERS.get(QUrl(upstream).host())
        if referer:
            request.setRawHeader(b"Referer", referer)
        reply = self._nam.get(request)
        # The page may cancel the tile (panned away) before the reply ends:
        # the job is then deleted, and the reply is aborted with it.
        state = {"job_alive": True}

        def on_job_gone() -> None:
            state["job_alive"] = False
            if reply.isRunning():
                reply.abort()

        job.destroyed.connect(on_job_gone)

        def on_finished() -> None:
            reply.deleteLater()
            if not state["job_alive"]:
                return
            job.destroyed.disconnect(on_job_gone)
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if reply.error() != QNetworkReply.NetworkError.NoError or status != 200:
                unreachable = status is None or int(status) >= 500
                if cached is not None and unreachable:
                    # The stale copy, for as long as the provider's headers allow
                    _reply_bytes(job, cached.mime.encode(), cached.data)
                else:
                    job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
                return
            content_type = reply.header(QNetworkRequest.KnownHeaders.ContentTypeHeader)
            mime = str(content_type or "image/png").split(";")[0].strip()
            data = bytes(reply.readAll().data())
            if cache is not None:
                cache_control = bytes(reply.rawHeader("Cache-Control").data()).decode("latin-1")
                cache.put(layer.id, z, x, y, data, mime, cache_control)
            _reply_bytes(job, mime.encode(), data)

        reply.finished.connect(on_finished)


def _reply_bytes(job: QWebEngineUrlRequestJob, mime: bytes, data: bytes) -> None:
    # The job owns the buffer, so it lives exactly as long as the reply.
    buf = QBuffer(job)
    buf.setData(data)
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    job.reply(mime, buf)


def _user_agent() -> str:
    from opensak import __version__
    return f"OpenSAK/{__version__} (+https://github.com/OpenSAK-Org/OpenSAK)"


def install_handler(profile: QWebEngineProfile, parent: QObject | None = None) -> AssetSchemeHandler:
    """Install an AssetSchemeHandler on *profile* and return it.

    The caller keeps the returned handler alive for the profile's lifetime."""
    handler = AssetSchemeHandler(parent)
    profile.installUrlSchemeHandler(SCHEME, handler)
    return handler

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
(register_scheme(), called from app.main()); the handler is installed per
QWebEngineProfile (install_handler()).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QBuffer, QCoreApplication, QIODevice, QObject
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

WEB_DIR = Path(__file__).parent.parent / "assets" / "web"

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


class AssetSchemeHandler(QWebEngineUrlSchemeHandler):
    """Answers opensak://assets/... requests from WEB_DIR."""

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:
        url = job.requestUrl()
        if job.requestMethod().data() != b"GET":
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
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
        # The job owns the buffer, so it lives exactly as long as the reply.
        buf = QBuffer(job)
        buf.setData(data)
        buf.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(_MIME_TYPES[target.suffix.lower()], buf)


def install_handler(profile: QWebEngineProfile, parent: QObject | None = None) -> AssetSchemeHandler:
    """Install an AssetSchemeHandler on *profile* and return it.

    The caller keeps the returned handler alive for the profile's lifetime."""
    handler = AssetSchemeHandler(parent)
    profile.installUrlSchemeHandler(SCHEME, handler)
    return handler

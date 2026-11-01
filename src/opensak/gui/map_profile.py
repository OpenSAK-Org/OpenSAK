"""
map_profile.py — The one QWebEngineProfile every map widget shares.

#1111 (step 3): the map used an off-the-record profile per MapWidget, so no
tile survived a restart. It now uses a single named, persistent profile with
a disk HTTP cache under <data dir>/map_cache. Chromium follows the cache
headers of tile.openstreetmap.org (max-age ≈ 1 day, stale-while-revalidate
7 days): a tile viewed online shows again offline for about a week after it
was last loaded. Only tiles the user actually viewed are cached — no
prefetching, as the OSM tile usage policy requires.

Why one shared profile: two profiles with the same storage name in one
process hang QtWebEngine (both open the same on-disk cache), and the main
map, the split-screen map, the pop-out and the draw dialog can all be open
at once.

Teardown order matters (see MapWidget._cleanup_webengine): a profile must
outlive its pages. Each MapWidget acquire()s the profile and release()s it
after scheduling its page for deletion; the profile itself is only deleted
at application quit, once the last page is gone.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineUrlRequestInterceptor

from opensak.logger import get_logger

log = get_logger("map_profile")

STORAGE_NAME = "opensak-map"

_profile: QWebEngineProfile | None = None
_interceptor: QWebEngineUrlRequestInterceptor | None = None
_users = 0
_quitting = False


class TileInterceptor(QWebEngineUrlRequestInterceptor):
    """Sets the Referer header the OSM tile servers ask for."""

    def interceptRequest(self, info) -> None:
        url = info.requestUrl().toString()
        if "tile.openstreetmap.org" in url:
            info.setHttpHeader(b"Referer", b"https://www.openstreetmap.org/")


def map_cache_dir() -> Path:
    """Where the map profile keeps its HTTP cache (and other storage)."""
    from opensak.config import get_app_data_dir
    return get_app_data_dir() / "map_cache"


def _http_cache_dir() -> Path:
    return map_cache_dir() / "http"


def cache_size_bytes() -> int:
    """Disk space the tile cache uses right now."""
    total = 0
    for path in _http_cache_dir().rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            pass
    return total


def clear_cache() -> int:
    """Empty the tile cache; returns the bytes it used before.

    With a map open the live profile clears its own cache (Chromium holds
    the files open); otherwise the cache folder is simply removed."""
    size = cache_size_bytes()
    if _profile is not None:
        _profile.clearHttpCache()
    else:
        shutil.rmtree(_http_cache_dir(), ignore_errors=True)
    log.info("map tile cache cleared (%d bytes)", size)
    return size


def _create(profile_cls: type[QWebEngineProfile]) -> QWebEngineProfile:
    from opensak.gui.settings import get_settings
    global _interceptor
    profile = profile_cls(STORAGE_NAME)
    storage = map_cache_dir()
    profile.setPersistentStoragePath(str(storage))
    profile.setCachePath(str(_http_cache_dir()))
    # Only the tile cache is wanted on disk — no cookies from tile servers.
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
    )
    size_mb = get_settings().map_tile_cache_mb
    if size_mb > 0:
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        profile.setHttpCacheMaximumSize(size_mb * 1024 * 1024)
    else:
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
    _interceptor = TileInterceptor()
    profile.setUrlRequestInterceptor(_interceptor)
    log.debug("map profile: %s (tile cache %d MB)", storage, size_mb)
    return profile


def acquire(profile_cls: type[QWebEngineProfile] = QWebEngineProfile) -> QWebEngineProfile:
    """Return the shared map profile, creating it on first use.

    Every call must be paired with release() once the caller's page has been
    scheduled for deletion."""
    global _profile, _users
    if _profile is None:
        _profile = _create(profile_cls)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(_on_about_to_quit)
    _users += 1
    return _profile


def release() -> None:
    """A map widget is done with the profile (its page is already scheduled
    for deletion). At quit, the last release deletes the profile — after the
    pages, since deleteLater() events run in the order they were posted."""
    global _users
    _users = max(0, _users - 1)
    if _users == 0 and _quitting:
        _delete()


def _on_about_to_quit() -> None:
    global _quitting
    _quitting = True
    if _users == 0:
        _delete()


def _delete() -> None:
    global _profile, _interceptor
    if _profile is None:
        return
    try:
        _profile.deleteLater()
    except RuntimeError:
        pass  # already deleted by Qt
    _profile = None
    _interceptor = None


def reset() -> None:
    """Forget the shared profile without deleting it (tests)."""
    global _profile, _interceptor, _users, _quitting
    _profile = None
    _interceptor = None
    _users = 0
    _quitting = False

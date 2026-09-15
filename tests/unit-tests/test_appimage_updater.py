"""tests/unit-tests/test_appimage_updater.py — AppImage self-update (issue
#836, Step B in epic #824), fully offline.

Mirrors test_updater.py's fake-urlopen pattern (see its module docstring
for why: a real urlopen in CI is a flaky, non-deterministic network call).
Two different urlopen calls happen here (release-lookup JSON, then the
binary asset download), so the fake dispatches on the requested URL
instead of returning a single fixed response.

The AppImageUpdateWorker tests are POSIX-only (same `posix_only` marker
convention as test_appimage.py/test_msix.py/test_settings_store.py):
os.chmod()'s executable bits (S_IXUSR etc.) don't map onto Windows'
permission model, so asserting on them fails there even though the
replace itself succeeds. AppImage self-update is a Linux-only feature in
practice anyway. (Discovered: Windows CI run of #836, matching the same
issue already fixed for #835's test_appimage.py.) The fetch_release_by_tag/
find_linux_appimage_asset_url tests above don't touch the filesystem and
run on every platform.
"""

from __future__ import annotations

import json
import os
import stat
from urllib.error import URLError

import pytest

pytest.importorskip("pytestqt")

import opensak.appimage as appimage_mod
import opensak.updater as updater
from opensak.updater import (
    AppImageUpdateWorker,
    fetch_release_by_tag,
    find_linux_appimage_asset_url,
)

posix_only = pytest.mark.skipif(os.name == "nt", reason="AppImage is a POSIX-only concept")

# Note: the worker tests below call worker.run() directly (never .start()),
# so signal connections fire synchronously on the test thread with no event
# loop involved — Qt Signal/slot direct connections work without a live
# QApplication/QCoreApplication instance. No `qapp` fixture is used here on
# purpose (unlike test_updater.py): AppImage self-update is Linux-only
# anyway, and this keeps the tests independent of platform GUI setup.


# ── Fake HTTP layer (no real socket ever) ─────────────────────────────────────

class _FakeJSONResp:
    def __init__(self, payload):
        self._bytes = json.dumps(payload).encode()

    def read(self, *_a):
        return self._bytes

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _FakeBinaryResp:
    """Minimal stream shutil.copyfileobj() can drain via repeated .read(n)."""

    def __init__(self, data: bytes):
        self._data = data

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            out, self._data = self._data, b""
            return out
        out, self._data = self._data[:n], self._data[n:]
        return out

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _dispatching_urlopen(release_payload=None, asset_bytes=b"",
                          release_error=None, download_error=None):
    """
    Fake for urllib.request.urlopen that distinguishes the release-lookup
    call (URL contains '/releases/tags/') from the asset-download call
    (any other URL) — AppImageUpdateWorker makes both in sequence.
    """
    def _urlopen(req, *_a, **_k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "/releases/tags/" in url:
            if release_error is not None:
                raise release_error
            return _FakeJSONResp(release_payload)
        if download_error is not None:
            raise download_error
        return _FakeBinaryResp(asset_bytes)
    return _urlopen


_LINUX_ASSET_NAME = "OpenSAK-v1.19.0-Linux-x86_64.AppImage"
_RELEASE_WITH_LINUX_ASSET = {
    "tag_name": "v1.19.0",
    "assets": [
        {"name": _LINUX_ASSET_NAME,
         "browser_download_url": "https://example.test/dl/" + _LINUX_ASSET_NAME},
        {"name": "OpenSAK-v1.19.0-Windows.zip",
         "browser_download_url": "https://example.test/dl/win.zip"},
    ],
}
_RELEASE_WITHOUT_LINUX_ASSET = {
    "tag_name": "v1.19.0",
    "assets": [
        {"name": "OpenSAK-v1.19.0-Windows.zip",
         "browser_download_url": "https://example.test/dl/win.zip"},
    ],
}


# ── fetch_release_by_tag ───────────────────────────────────────────────────────

def test_fetch_release_by_tag_returns_tag_and_assets(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", _dispatching_urlopen(release_payload=_RELEASE_WITH_LINUX_ASSET)
    )
    result = fetch_release_by_tag("v1.19.0")
    assert result == _RELEASE_WITH_LINUX_ASSET


def test_fetch_release_by_tag_returns_none_on_urlerror(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", _dispatching_urlopen(release_error=URLError("boom"))
    )
    assert fetch_release_by_tag("v1.19.0") is None


def test_fetch_release_by_tag_defaults_empty_assets_list(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _dispatching_urlopen(release_payload={"tag_name": "v1.19.0"}),
    )
    result = fetch_release_by_tag("v1.19.0")
    assert result == {"tag_name": "v1.19.0", "assets": []}


# ── find_linux_appimage_asset_url ───────────────────────────────────────────────

def test_find_linux_appimage_asset_url_matches_expected_name(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", _dispatching_urlopen(release_payload=_RELEASE_WITH_LINUX_ASSET)
    )
    url = find_linux_appimage_asset_url("v1.19.0")
    assert url == "https://example.test/dl/" + _LINUX_ASSET_NAME


def test_find_linux_appimage_asset_url_none_when_no_linux_asset(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _dispatching_urlopen(release_payload=_RELEASE_WITHOUT_LINUX_ASSET),
    )
    assert find_linux_appimage_asset_url("v1.19.0") is None


def test_find_linux_appimage_asset_url_none_when_release_fetch_fails(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", _dispatching_urlopen(release_error=URLError("boom"))
    )
    assert find_linux_appimage_asset_url("v1.19.0") is None


# ── AppImageUpdateWorker ─────────────────────────────────────────────────────
#
# worker.run() is called directly (not .start()) — keeps everything on the
# test thread so signal connections fire synchronously, same pattern
# test_updater.py uses for UpdateCheckWorker.

@posix_only
def test_worker_downloads_validates_and_replaces(monkeypatch, tmp_path):
    target = tmp_path / "OpenSAK.AppImage"
    target.write_bytes(b"old-version-content")
    monkeypatch.setattr(appimage_mod, "get_integrated_appimage_path", lambda: target)

    new_content = b"\x7fELF" + b"new-fake-binary-content"
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _dispatching_urlopen(release_payload=_RELEASE_WITH_LINUX_ASSET, asset_bytes=new_content),
    )

    worker = AppImageUpdateWorker("v1.19.0")
    ok_results: list[str] = []
    err_results: list[str] = []
    worker.finished_ok.connect(ok_results.append)
    worker.finished_error.connect(err_results.append)
    worker.run()

    assert err_results == []
    assert ok_results == [str(target)]
    assert target.read_bytes() == new_content
    assert target.stat().st_mode & stat.S_IXUSR
    # Ingen efterladte midlertidige filer.
    assert list(tmp_path.glob("*.AppImage.part")) == []


@posix_only
def test_worker_reports_asset_not_found_when_no_linux_asset(monkeypatch, tmp_path):
    target = tmp_path / "OpenSAK.AppImage"
    target.write_bytes(b"old-version-content")
    monkeypatch.setattr(appimage_mod, "get_integrated_appimage_path", lambda: target)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _dispatching_urlopen(release_payload=_RELEASE_WITHOUT_LINUX_ASSET),
    )

    worker = AppImageUpdateWorker("v1.19.0")
    ok_results: list[str] = []
    err_results: list[str] = []
    worker.finished_ok.connect(ok_results.append)
    worker.finished_error.connect(err_results.append)
    worker.run()

    assert ok_results == []
    assert err_results == ["asset_not_found"]
    # Den gamle, virkende installation må ikke være rørt.
    assert target.read_bytes() == b"old-version-content"


@posix_only
def test_worker_rejects_download_without_elf_magic(monkeypatch, tmp_path):
    target = tmp_path / "OpenSAK.AppImage"
    target.write_bytes(b"old-version-content")
    monkeypatch.setattr(appimage_mod, "get_integrated_appimage_path", lambda: target)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _dispatching_urlopen(
            release_payload=_RELEASE_WITH_LINUX_ASSET,
            asset_bytes=b"<html>this is not an AppImage, something went wrong</html>",
        ),
    )

    worker = AppImageUpdateWorker("v1.19.0")
    ok_results: list[str] = []
    err_results: list[str] = []
    worker.finished_ok.connect(ok_results.append)
    worker.finished_error.connect(err_results.append)
    worker.run()

    assert ok_results == []
    assert err_results == ["invalid_download"]
    assert target.read_bytes() == b"old-version-content"
    assert list(tmp_path.glob("*.AppImage.part")) == []


@posix_only
def test_worker_reports_oserror_and_cleans_up_on_download_failure(monkeypatch, tmp_path):
    target = tmp_path / "OpenSAK.AppImage"
    target.write_bytes(b"old-version-content")
    monkeypatch.setattr(appimage_mod, "get_integrated_appimage_path", lambda: target)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _dispatching_urlopen(
            release_payload=_RELEASE_WITH_LINUX_ASSET,
            download_error=OSError("disk full"),
        ),
    )

    worker = AppImageUpdateWorker("v1.19.0")
    ok_results: list[str] = []
    err_results: list[str] = []
    worker.finished_ok.connect(ok_results.append)
    worker.finished_error.connect(err_results.append)
    worker.run()

    assert ok_results == []
    assert len(err_results) == 1
    assert "disk full" in err_results[0]
    assert target.read_bytes() == b"old-version-content"
    assert list(tmp_path.glob("*.AppImage.part")) == []

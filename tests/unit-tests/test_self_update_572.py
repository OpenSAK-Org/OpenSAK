# tests/unit-tests/test_self_update_572.py — issue #572.
#
# Mirrors test_appimage_updater.py's fake-urlopen pattern (see its module
# docstring): a real urlopen/subprocess call in CI would be flaky and
# potentially destructive (subprocess.run(["explorer", ...]) has no
# meaning on the Linux/macOS test runners, and even on a real Windows
# runner we don't want tests popping up Explorer windows). Three kinds of
# calls happen here (release-lookup JSON, SHA256SUMS.txt text, and the
# binary asset download) plus one subprocess call (_reveal) — the fake
# urlopen dispatches on the requested URL, and _reveal is monkeypatched
# directly.
#
# worker.run() is called directly (never .start()), same reasoning as
# test_appimage_updater.py: keeps everything on the test thread so signal
# connections fire synchronously, no live QApplication needed.

from __future__ import annotations

import hashlib
import json
import subprocess
from urllib.error import URLError

import pytest

pytest.importorskip("pytestqt")

import opensak.updater as updater
from opensak.updater import (
    SelfUpdateWorker,
    fetch_checksums,
    find_macos_asset_url,
    find_windows_asset_url,
    macos_arch_suffix,
    macos_asset_name,
    windows_asset_name,
)


# ── Fake HTTP layer (no real socket ever) ─────────────────────────────────

class _FakeJSONResp:
    def __init__(self, payload):
        self._bytes = json.dumps(payload).encode()

    def read(self, *_a):
        return self._bytes

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _FakeTextResp:
    def __init__(self, text: str):
        self._bytes = text.encode("utf-8")

    def read(self, *_a):
        return self._bytes

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _FakeBinaryResp:
    """Minimal stream supporting chunked .read(n) AND a .headers lookup,
    matching what SelfUpdateWorker.run() actually calls (unlike
    AppImageUpdateWorker, this one reads Content-Length for progress)."""

    def __init__(self, data: bytes):
        self._data = data
        self.headers = {"Content-Length": str(len(data))}

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


def _dispatching_urlopen(release_payload=None, checksums_text=None, asset_bytes=b"",
                          release_error=None, checksums_error=None, download_error=None):
    """
    Dispatches on URL: '/releases/tags/' -> release JSON, a URL ending in
    'SHA256SUMS.txt' -> the checksums text file, anything else -> the
    binary asset download. SelfUpdateWorker makes all three in sequence
    (fetch_release_by_tag is called twice — once via find_*_asset_url,
    once via fetch_checksums — which is fine, both hit the same fake).
    """
    def _urlopen(req, *_a, **_k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "/releases/tags/" in url:
            if release_error is not None:
                raise release_error
            return _FakeJSONResp(release_payload)
        if url.endswith("SHA256SUMS.txt"):
            if checksums_error is not None:
                raise checksums_error
            return _FakeTextResp(checksums_text or "")
        if download_error is not None:
            raise download_error
        return _FakeBinaryResp(asset_bytes)
    return _urlopen


_WIN_ASSET_NAME = "OpenSAK-v1.20.0-Windows.zip"
_MAC_ARM_ASSET_NAME = "OpenSAK-v1.20.0-macOS-arm64.dmg"
_MAC_X86_ASSET_NAME = "OpenSAK-v1.20.0-macOS-x86_64.dmg"

_RELEASE_ALL_PLATFORMS = {
    "tag_name": "v1.20.0",
    "assets": [
        {"name": _WIN_ASSET_NAME,
         "browser_download_url": "https://example.test/dl/" + _WIN_ASSET_NAME},
        {"name": _MAC_ARM_ASSET_NAME,
         "browser_download_url": "https://example.test/dl/" + _MAC_ARM_ASSET_NAME},
        {"name": _MAC_X86_ASSET_NAME,
         "browser_download_url": "https://example.test/dl/" + _MAC_X86_ASSET_NAME},
        {"name": "OpenSAK-v1.20.0-Linux-x86_64.AppImage",
         "browser_download_url": "https://example.test/dl/linux.AppImage"},
        {"name": "SHA256SUMS.txt",
         "browser_download_url": "https://example.test/dl/SHA256SUMS.txt"},
    ],
}
_RELEASE_WITHOUT_CHECKSUMS = {
    "tag_name": "v1.20.0",
    "assets": [
        {"name": _WIN_ASSET_NAME,
         "browser_download_url": "https://example.test/dl/" + _WIN_ASSET_NAME},
    ],
}


# ── windows_asset_name / macos_arch_suffix / macos_asset_name ─────────────

def test_windows_asset_name_matches_build_yml_pattern():
    assert windows_asset_name("v1.20.0") == "OpenSAK-v1.20.0-Windows.zip"


@pytest.mark.parametrize("machine, expected", [
    ("arm64", "arm64"),
    ("aarch64", "arm64"),
    ("ARM64", "arm64"),
    ("x86_64", "x86_64"),
    ("AMD64", "x86_64"),
    ("", "x86_64"),
])
def test_macos_arch_suffix(monkeypatch, machine, expected):
    monkeypatch.setattr(updater.platform, "machine", lambda: machine)
    assert macos_arch_suffix() == expected


def test_macos_asset_name_uses_detected_arch(monkeypatch):
    monkeypatch.setattr(updater.platform, "machine", lambda: "arm64")
    assert macos_asset_name("v1.20.0") == "OpenSAK-v1.20.0-macOS-arm64.dmg"


# ── find_windows_asset_url / find_macos_asset_url ──────────────────────────

def test_find_windows_asset_url_matches_expected_name(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", _dispatching_urlopen(release_payload=_RELEASE_ALL_PLATFORMS)
    )
    assert find_windows_asset_url("v1.20.0") == "https://example.test/dl/" + _WIN_ASSET_NAME


def test_find_windows_asset_url_none_when_release_fetch_fails(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", _dispatching_urlopen(release_error=URLError("boom"))
    )
    assert find_windows_asset_url("v1.20.0") is None


def test_find_macos_asset_url_picks_arm64(monkeypatch):
    monkeypatch.setattr(updater.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(
        "urllib.request.urlopen", _dispatching_urlopen(release_payload=_RELEASE_ALL_PLATFORMS)
    )
    assert find_macos_asset_url("v1.20.0") == "https://example.test/dl/" + _MAC_ARM_ASSET_NAME


def test_find_macos_asset_url_picks_x86_64(monkeypatch):
    monkeypatch.setattr(updater.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        "urllib.request.urlopen", _dispatching_urlopen(release_payload=_RELEASE_ALL_PLATFORMS)
    )
    assert find_macos_asset_url("v1.20.0") == "https://example.test/dl/" + _MAC_X86_ASSET_NAME


def test_find_macos_asset_url_none_when_arch_missing_from_release(monkeypatch):
    monkeypatch.setattr(updater.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _dispatching_urlopen(release_payload=_RELEASE_WITHOUT_CHECKSUMS),
    )
    assert find_macos_asset_url("v1.20.0") is None


# ── fetch_checksums ─────────────────────────────────────────────────────────

def test_fetch_checksums_parses_standard_sha256sum_format(monkeypatch):
    text = (
        f"aaaa111122223333444455556666777788889999aaaabbbbccccddddeeeeffff  {_WIN_ASSET_NAME}\n"
        f"bbbb222233334444555566667777888899990000aaaabbbbccccddddeeeeffff  {_MAC_ARM_ASSET_NAME}\n"
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _dispatching_urlopen(release_payload=_RELEASE_ALL_PLATFORMS, checksums_text=text),
    )
    result = fetch_checksums("v1.20.0")
    assert result == {
        _WIN_ASSET_NAME: "aaaa111122223333444455556666777788889999aaaabbbbccccddddeeeeffff",
        _MAC_ARM_ASSET_NAME: "bbbb222233334444555566667777888899990000aaaabbbbccccddddeeeeffff",
    }


def test_fetch_checksums_handles_binary_mode_asterisk_prefix(monkeypatch):
    text = f"cccc333344445555666677778888999900001111aaaabbbbccccddddeeeeffff *{_WIN_ASSET_NAME}\n"
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _dispatching_urlopen(release_payload=_RELEASE_ALL_PLATFORMS, checksums_text=text),
    )
    result = fetch_checksums("v1.20.0")
    assert result[_WIN_ASSET_NAME] == "cccc333344445555666677778888999900001111aaaabbbbccccddddeeeeffff"


def test_fetch_checksums_skips_blank_lines_and_comments(monkeypatch):
    text = (
        "# generated by build.yml\n"
        "\n"
        f"dddd444455556666777788889999000011112222aaaabbbbccccddddeeeeffff  {_WIN_ASSET_NAME}\n"
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _dispatching_urlopen(release_payload=_RELEASE_ALL_PLATFORMS, checksums_text=text),
    )
    result = fetch_checksums("v1.20.0")
    assert len(result) == 1
    assert _WIN_ASSET_NAME in result


def test_fetch_checksums_none_when_asset_missing_from_release(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _dispatching_urlopen(release_payload=_RELEASE_WITHOUT_CHECKSUMS),
    )
    assert fetch_checksums("v1.20.0") is None


def test_fetch_checksums_none_when_release_fetch_fails(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", _dispatching_urlopen(release_error=URLError("boom"))
    )
    assert fetch_checksums("v1.20.0") is None


def test_fetch_checksums_none_when_download_of_checksums_file_fails(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _dispatching_urlopen(
            release_payload=_RELEASE_ALL_PLATFORMS, checksums_error=URLError("boom")
        ),
    )
    assert fetch_checksums("v1.20.0") is None


# ── SelfUpdateWorker ─────────────────────────────────────────────────────
#
# sys.platform is monkeypatched directly (not os.name), so pathlib's
# Path() factory is unaffected — it dispatches on os.name at interpreter
# start, so all file I/O below still goes through a real PosixPath on the
# Linux test runner regardless of which sys.platform value the worker's
# own branching sees. See test_settings_store.py's TestPlatformSpecificPaths
# docstring for the same caveat applied the other way around (os.name).

def _content_and_digest(data: bytes) -> tuple[bytes, str]:
    return data, hashlib.sha256(data).hexdigest()


class TestSelfUpdateWorkerWindows:
    def test_success_downloads_verifies_and_reveals(self, monkeypatch, tmp_path):
        monkeypatch.setattr(updater.sys, "platform", "win32")
        content, digest = _content_and_digest(b"fake windows zip contents")
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _dispatching_urlopen(
                release_payload=_RELEASE_ALL_PLATFORMS,
                checksums_text=f"{digest}  {_WIN_ASSET_NAME}\n",
                asset_bytes=content,
            ),
        )
        revealed: list = []
        monkeypatch.setattr(SelfUpdateWorker, "_reveal", lambda self, path: revealed.append(path))

        worker = SelfUpdateWorker("v1.20.0")
        progress_calls: list = []
        ok_results: list = []
        err_results: list = []
        worker.progress.connect(lambda d, t: progress_calls.append((d, t)))
        worker.finished_ok.connect(ok_results.append)
        worker.finished_error.connect(err_results.append)
        worker.run()

        assert err_results == []
        assert len(ok_results) == 1
        assert ok_results[0].endswith(_WIN_ASSET_NAME)
        assert len(revealed) == 1
        # Progress reported the full size at least once, with a known total.
        assert progress_calls[-1] == (len(content), len(content))

    def test_asset_not_found_emits_error(self, monkeypatch):
        monkeypatch.setattr(updater.sys, "platform", "win32")
        empty_release = {"tag_name": "v1.20.0", "assets": []}
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _dispatching_urlopen(release_payload=empty_release),
        )
        worker = SelfUpdateWorker("v1.20.0")
        err_results: list = []
        worker.finished_error.connect(err_results.append)
        worker.run()
        assert err_results == ["asset_not_found"]

    def test_missing_checksums_file_emits_checksum_unavailable(self, monkeypatch):
        monkeypatch.setattr(updater.sys, "platform", "win32")
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _dispatching_urlopen(release_payload=_RELEASE_WITHOUT_CHECKSUMS),
        )
        worker = SelfUpdateWorker("v1.20.0")
        err_results: list = []
        worker.finished_error.connect(err_results.append)
        worker.run()
        assert err_results == ["checksum_unavailable"]

    def test_checksum_mismatch_emits_error_and_does_not_reveal(self, monkeypatch):
        monkeypatch.setattr(updater.sys, "platform", "win32")
        content, _correct_digest = _content_and_digest(b"fake windows zip contents")
        wrong_digest = "0" * 64
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _dispatching_urlopen(
                release_payload=_RELEASE_ALL_PLATFORMS,
                checksums_text=f"{wrong_digest}  {_WIN_ASSET_NAME}\n",
                asset_bytes=content,
            ),
        )
        revealed: list = []
        monkeypatch.setattr(SelfUpdateWorker, "_reveal", lambda self, path: revealed.append(path))

        worker = SelfUpdateWorker("v1.20.0")
        ok_results: list = []
        err_results: list = []
        worker.finished_ok.connect(ok_results.append)
        worker.finished_error.connect(err_results.append)
        worker.run()

        assert ok_results == []
        assert err_results == ["checksum_mismatch"]
        assert revealed == []

    def test_download_network_error_emits_raw_message(self, monkeypatch):
        monkeypatch.setattr(updater.sys, "platform", "win32")
        digest = hashlib.sha256(b"x").hexdigest()
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _dispatching_urlopen(
                release_payload=_RELEASE_ALL_PLATFORMS,
                checksums_text=f"{digest}  {_WIN_ASSET_NAME}\n",
                download_error=URLError("connection reset"),
            ),
        )
        worker = SelfUpdateWorker("v1.20.0")
        err_results: list = []
        worker.finished_error.connect(err_results.append)
        worker.run()
        assert len(err_results) == 1
        assert "connection reset" in err_results[0]

    def test_reveal_calls_explorer_with_select(self, monkeypatch, tmp_path):
        monkeypatch.setattr(updater.sys, "platform", "win32")
        calls: list = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
        target = tmp_path / "OpenSAK-v1.20.0-Windows.zip"
        target.write_bytes(b"x")
        worker = SelfUpdateWorker("v1.20.0")
        worker._reveal(target)
        assert len(calls) == 1
        assert calls[0][0][0] == "explorer"
        assert str(target) in calls[0][0][1]


class TestSelfUpdateWorkerMacos:
    """Issue #893: macOS now installs by itself instead of only opening the
    DMG. install_macos_update() and the Downloads folder are always
    redirected here — no test may run hdiutil/ditto or touch ~/Downloads."""

    @staticmethod
    def _mac_env(monkeypatch, tmp_path, content):
        monkeypatch.setattr(updater.sys, "platform", "darwin")
        monkeypatch.setattr(updater.platform, "machine", lambda: "arm64")
        digest = hashlib.sha256(content).hexdigest()
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _dispatching_urlopen(
                release_payload=_RELEASE_ALL_PLATFORMS,
                checksums_text=f"{digest}  {_MAC_ARM_ASSET_NAME}\n",
                asset_bytes=content,
            ),
        )
        target = tmp_path / "Applications" / "OpenSAK.app"
        monkeypatch.setattr(updater, "macos_install_target", lambda: target)
        downloads = tmp_path / "Downloads"
        monkeypatch.setattr(updater, "_macos_downloads_dir", lambda: downloads)
        revealed: list = []
        monkeypatch.setattr(SelfUpdateWorker, "_reveal", lambda self, path: revealed.append(path))
        return target, downloads, revealed

    @staticmethod
    def _run(worker):
        signals: dict = {"installed": [], "ok": [], "err": []}
        worker.installed.connect(signals["installed"].append)
        worker.finished_ok.connect(signals["ok"].append)
        worker.finished_error.connect(signals["err"].append)
        worker.run()
        return signals

    def test_success_installs_emits_installed_and_removes_download(self, monkeypatch, tmp_path):
        target, downloads, revealed = self._mac_env(monkeypatch, tmp_path, b"fake dmg")
        calls: list = []

        def _fake_install(dmg, tgt):
            assert dmg.read_bytes() == b"fake dmg"
            calls.append((dmg, tgt))
            return tgt
        monkeypatch.setattr(updater, "install_macos_update", _fake_install)

        signals = self._run(SelfUpdateWorker("v1.20.0"))

        assert signals == {"installed": [str(target)], "ok": [], "err": []}
        assert revealed == []
        dmg, tgt = calls[0]
        assert tgt == target
        assert not dmg.exists()            # no orphan in /private/var/folders
        assert not dmg.parent.exists()     # temp dir cleaned up as well
        assert not downloads.exists()

    @pytest.mark.parametrize("exc", [
        updater.MacInstallError("no .app bundle found"),
        PermissionError("/Applications is not writable"),
        subprocess.CalledProcessError(1, ["hdiutil", "attach"]),
        subprocess.TimeoutExpired(["ditto"], 600),
    ])
    def test_install_failure_falls_back_to_downloads(self, monkeypatch, tmp_path, exc):
        target, downloads, revealed = self._mac_env(monkeypatch, tmp_path, b"fake dmg")

        def _failing_install(dmg, tgt):
            raise exc
        monkeypatch.setattr(updater, "install_macos_update", _failing_install)

        signals = self._run(SelfUpdateWorker("v1.20.0"))

        saved = downloads / _MAC_ARM_ASSET_NAME
        assert signals == {"installed": [], "ok": [str(saved)], "err": []}
        assert saved.read_bytes() == b"fake dmg"
        assert revealed == [saved]

    def test_checksum_mismatch_never_installs(self, monkeypatch, tmp_path):
        self._mac_env(monkeypatch, tmp_path, b"fake dmg")
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _dispatching_urlopen(
                release_payload=_RELEASE_ALL_PLATFORMS,
                checksums_text=f"{'0' * 64}  {_MAC_ARM_ASSET_NAME}\n",
                asset_bytes=b"tampered",
            ),
        )

        def _must_not_install(*_a):
            raise AssertionError("must not install an unverified download")
        monkeypatch.setattr(updater, "install_macos_update", _must_not_install)

        signals = self._run(SelfUpdateWorker("v1.20.0"))
        assert signals == {"installed": [], "ok": [], "err": ["checksum_mismatch"]}

    def test_reveal_calls_open(self, monkeypatch, tmp_path):
        monkeypatch.setattr(updater.sys, "platform", "darwin")
        calls: list = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
        target = tmp_path / "OpenSAK-v1.20.0-macOS-arm64.dmg"
        target.write_bytes(b"x")
        worker = SelfUpdateWorker("v1.20.0")
        worker._reveal(target)
        assert len(calls) == 1
        assert calls[0][0] == ["open", str(target)]


class TestSelfUpdateWorkerUnsupportedPlatform:
    def test_unsupported_platform_emits_error_without_any_network_call(self, monkeypatch):
        monkeypatch.setattr(updater.sys, "platform", "linux")

        def _fail_if_called(*_a, **_k):
            raise AssertionError("urlopen should never be called for an unsupported platform")
        monkeypatch.setattr("urllib.request.urlopen", _fail_if_called)

        worker = SelfUpdateWorker("v1.20.0")
        err_results: list = []
        worker.finished_error.connect(err_results.append)
        worker.run()
        assert err_results == ["unsupported_platform"]

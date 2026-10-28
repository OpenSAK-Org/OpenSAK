# tests/unit-tests/test_offline_detected_1111.py — #1111 (step 2): a
# session-wide "offline detected" flag. After a request fails because the
# network is unreachable, automatic requests (background update check,
# boundary data during import) are skipped instead of each waiting for a
# full timeout; requests the user starts still try, and any success clears
# the flag.

from __future__ import annotations

import errno
import socket
import ssl
from email.message import Message
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

import opensak.geo.packs as packs
import opensak.updater as updater
from opensak import net


class _FakeResp(BytesIO):
    """What urllib.request.urlopen returns: a readable context manager."""


def _raise(exc: BaseException):
    def _urlopen(*_a, **_k):
        raise exc
    return _urlopen


def _http_error(code: int) -> HTTPError:
    return HTTPError("https://x.invalid", code, "error", Message(), None)


# ── Which errors mean "no network" ────────────────────────────────────────

@pytest.mark.parametrize("exc", [
    URLError(socket.gaierror(11001, "getaddrinfo failed")),
    URLError(TimeoutError("timed out")),
    URLError(ConnectionRefusedError(10061, "refused")),
    URLError(OSError(errno.ENETUNREACH, "Network is unreachable")),
    URLError(OSError(10051, "WSAENETUNREACH")),
    TimeoutError("read timed out"),
    ConnectionResetError(),
    socket.gaierror(-2, "Name or service not known"),
])
def test_connectivity_errors(exc):
    assert net.is_connectivity_error(exc)


@pytest.mark.parametrize("exc", [
    _http_error(404),
    _http_error(503),
    URLError(ssl.SSLCertVerificationError("certificate verify failed")),
    URLError("unknown url type: foo"),
    OSError(errno.EACCES, "Permission denied"),
    ValueError("bad json"),
])
def test_not_connectivity_errors(exc):
    assert not net.is_connectivity_error(exc)


# ── The flag itself ───────────────────────────────────────────────────────

class TestFlag:
    def test_starts_clear(self):
        assert not net.offline_detected()

    def test_set_by_connectivity_failure_only(self):
        net.note_failure(ValueError("bad json"))
        assert not net.offline_detected()
        net.note_failure(URLError(TimeoutError()))
        assert net.offline_detected()

    def test_cleared_by_success(self):
        net.note_failure(TimeoutError())
        net.note_success()
        assert not net.offline_detected()

    def test_expires_after_retry_window(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(net.time, "monotonic", lambda: now[0])
        net.note_failure(TimeoutError())
        now[0] += net.OFFLINE_RETRY_SECONDS - 1
        assert net.offline_detected()
        now[0] += 2
        assert not net.offline_detected()


# ── Requests record their outcome ─────────────────────────────────────────

class TestRecording:
    def test_manifest_failure_sets_flag(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", _raise(URLError(socket.gaierror())))
        assert packs.fetch_manifest() is None
        assert net.offline_detected()

    def test_manifest_success_clears_flag(self, monkeypatch):
        net.note_failure(TimeoutError())
        monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _FakeResp(b"{}"))
        assert packs.fetch_manifest() == {}
        assert not net.offline_detected()

    def test_pack_404_does_not_set_flag(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", _raise(_http_error(404)))
        assert not packs.fetch_pack("x.geojson", tmp_path)
        assert not net.offline_detected()

    def test_pack_timeout_sets_flag(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", _raise(TimeoutError()))
        assert not packs.fetch_pack("x.geojson", tmp_path)
        assert net.offline_detected()

    @pytest.mark.parametrize("fetch", [updater.fetch_latest_release,
                                       updater.fetch_latest_prerelease])
    def test_update_check_failure_sets_flag(self, fetch, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", _raise(URLError(socket.gaierror())))
        assert fetch() is None
        assert net.offline_detected()


# ── Automatic requests skip while offline ─────────────────────────────────

class TestSkipsWhileOffline:
    def test_fetch_packs_skips_the_probe(self, tmp_path: Path, monkeypatch):
        calls: list = []
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **_k: calls.append(a))
        progress: list = []
        net.note_failure(TimeoutError())
        assert packs.fetch_packs(["a.geojson", "b.geojson"], tmp_path,
                                 progress_cb=lambda d, t: progress.append((d, t))) == 0
        assert calls == []
        assert progress == [(2, 2)]

    def test_baseline_not_downloaded_while_offline(self, tmp_path: Path, monkeypatch):
        from opensak.geo import store
        monkeypatch.setattr(store, "_frozen_bundle_dir", lambda: None)
        calls: list = []
        monkeypatch.setattr(packs, "fetch_baseline", lambda d, *_a, **_k: calls.append(d))
        net.note_failure(TimeoutError())
        store.ensure_baseline_seeded(tmp_path)
        assert calls == []
        net.reset_offline()
        store.ensure_baseline_seeded(tmp_path)
        assert calls == [tmp_path]

    def test_boundary_dialog_check_still_tries(self, tmp_path: Path, monkeypatch):
        # Explicit user actions ignore the flag (and clear it on success).
        net.note_failure(TimeoutError())
        monkeypatch.setattr("urllib.request.urlopen",
                            lambda *_a, **_k: _FakeResp(b'{"dataset_version": "2"}'))
        newer, manifest = packs.check_update(tmp_path, force=True)
        assert newer and manifest == {"dataset_version": "2"}
        assert not net.offline_detected()


# ── Background update check ───────────────────────────────────────────────

class TestBackgroundUpdateCheck:
    def _run(self) -> MagicMock:
        pytest.importorskip("pytestqt")
        from opensak.gui.mainwindow import MainWindow
        win = MagicMock()
        with patch("opensak.msix.is_msix_packaged", return_value=False), \
             patch("opensak.gui.mainwindow.UpdateCheckWorker") as worker_cls, \
             patch("opensak.gui.settings.get_settings") as settings:
            settings.return_value.updates_check_enabled = True
            settings.return_value.notify_about_betas = False
            MainWindow._check_update_background(win)
        return worker_cls

    def test_skipped_while_offline(self):
        net.note_failure(TimeoutError())
        self._run().assert_not_called()

    def test_runs_when_online(self):
        self._run().assert_called_once()

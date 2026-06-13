"""
tests/unit-tests/test_updater.py — Version-check updater (src/opensak/updater.py).

This module was the source of the CI "Aborted (core dumped)" flake: its
``UpdateCheckWorker`` ran a real ``urlopen()`` to GitHub, and on CI those
threads stayed blocked at teardown. These tests pin the behaviour *offline* —
no test here (nor anywhere else, via the autouse stub in tests/conftest.py) is
allowed to touch the network.

Covered:
  * _parse_version          — tag → tuple, malformed → (0,), ordering
  * fetch_latest_release    — happy path + every error path, all with a fake
                              urlopen (never a real socket)
  * UpdateCheckWorker.run   — emits update_available only when strictly newer;
                              always emits check_done
"""

import json

import pytest

pytest.importorskip("pytestqt")

from urllib.error import URLError

import opensak.updater as updater
# Bind the *real* functions at import time. The autouse _no_network_update_check
# fixture (tests/conftest.py) replaces ``updater.fetch_latest_release`` per test;
# these local names keep pointing at the genuine implementations under test.
from opensak.updater import _parse_version, fetch_latest_release, UpdateCheckWorker


# ── Fake HTTP layer (no real socket ever) ─────────────────────────────────────

class _FakeResp:
    """Minimal context-manager response that json.load() can consume."""

    def __init__(self, payload):
        self._bytes = json.dumps(payload).encode()

    def read(self, *_a):
        return self._bytes

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _patch_urlopen(monkeypatch, handler):
    """Replace urllib.request.urlopen with *handler* (called as handler())."""
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *_a, **_k: handler()
    )


# ── _parse_version ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "tag, expected",
    [
        ("v1.11.4", (1, 11, 4)),
        ("1.2.3", (1, 2, 3)),
        ("v2.0 ", (2, 0)),      # trailing whitespace tolerated
        ("v10", (10,)),
        ("", (0,)),
        ("v1.2.x", (0,)),       # non-numeric component → sentinel
        ("garbage", (0,)),
    ],
)
def test_parse_version(tag, expected):
    assert _parse_version(tag) == expected


def test_parse_version_ordering():
    assert _parse_version("v2.0.0") > _parse_version("1.13.11")
    assert _parse_version("1.13.11") > _parse_version("1.13.2")
    assert not _parse_version("1.0.0") > _parse_version("1.0.0")


# ── fetch_latest_release ──────────────────────────────────────────────────────

def test_fetch_returns_release_dict(monkeypatch):
    _patch_urlopen(monkeypatch, lambda: _FakeResp({
        "tag_name": "v1.14.0",
        "html_url": "https://example.test/r/v1.14.0",
        "name": "Release 1.14.0",
    }))
    out = fetch_latest_release()
    assert out == {
        "tag_name": "v1.14.0",
        "html_url": "https://example.test/r/v1.14.0",
        "name": "Release 1.14.0",
    }


def test_fetch_fills_defaults_for_missing_keys(monkeypatch):
    _patch_urlopen(monkeypatch, lambda: _FakeResp({"tag_name": "v9.9.9"}))
    out = fetch_latest_release()
    assert out["tag_name"] == "v9.9.9"
    assert out["html_url"] == updater.RELEASES_PAGE  # default when absent
    assert out["name"] == ""


def test_fetch_returns_none_on_urlerror(monkeypatch):
    def _boom():
        raise URLError("no network")
    _patch_urlopen(monkeypatch, _boom)
    assert fetch_latest_release() is None


def test_fetch_returns_none_on_oserror(monkeypatch):
    def _boom():
        raise OSError("connection reset")
    _patch_urlopen(monkeypatch, _boom)
    assert fetch_latest_release() is None


def test_fetch_returns_none_on_bad_json(monkeypatch):
    class _BadResp(_FakeResp):
        def __init__(self):
            pass

        def read(self, *_a):
            return b"<<not json>>"

    _patch_urlopen(monkeypatch, _BadResp)
    assert fetch_latest_release() is None


# ── UpdateCheckWorker.run (decision logic, run synchronously) ─────────────────

def _run_worker(monkeypatch, qapp, current, release):
    """Run the worker body in-thread and return (update_emits, check_done_count).

    ``release`` is whatever the (stubbed) fetch_latest_release returns.
    Calling run() directly keeps it on the test thread, so the default direct
    signal connections fire synchronously — no event loop, fully deterministic.
    """
    monkeypatch.setattr(updater, "fetch_latest_release", lambda: release)
    worker = UpdateCheckWorker(current)
    updates: list[tuple[str, str]] = []
    done: list[int] = []
    worker.update_available.connect(lambda tag, url: updates.append((tag, url)))
    worker.check_done.connect(lambda: done.append(1))
    worker.run()
    return updates, len(done)


def test_worker_emits_update_when_newer(monkeypatch, qapp):
    updates, done = _run_worker(
        monkeypatch, qapp, "1.0.0",
        {"tag_name": "v2.0.0", "html_url": "https://example.test/u", "name": "n"},
    )
    assert updates == [("v2.0.0", "https://example.test/u")]
    assert done == 1


def test_worker_silent_when_same_version(monkeypatch, qapp):
    updates, done = _run_worker(
        monkeypatch, qapp, "2.0.0",
        {"tag_name": "v2.0.0", "html_url": "https://example.test/u", "name": "n"},
    )
    assert updates == []
    assert done == 1


def test_worker_silent_when_older(monkeypatch, qapp):
    updates, done = _run_worker(
        monkeypatch, qapp, "3.1.0",
        {"tag_name": "v2.0.0", "html_url": "https://example.test/u", "name": "n"},
    )
    assert updates == []
    assert done == 1


def test_worker_done_when_fetch_fails(monkeypatch, qapp):
    updates, done = _run_worker(monkeypatch, qapp, "1.0.0", None)
    assert updates == []
    assert done == 1   # check_done always fires, even with no release

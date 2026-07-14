"""tests/unit-tests/test_updater.py — version-check updater, fully offline.

Was the CI "core dumped" flake (UpdateCheckWorker ran a real urlopen to GitHub); these pin _parse_version, fetch_latest_release and the worker with a fake urlopen.
"""

import json

import pytest

pytest.importorskip("pytestqt")

from urllib.error import URLError

import opensak.updater as updater
# Bind the real functions now; the autouse _no_network_update_check fixture swaps
# updater.fetch_latest_release per test, so these names keep pointing at the real impls.
from opensak.updater import _parse_version, fetch_latest_release, UpdateCheckWorker


# ── Fake HTTP layer (no real socket ever) ─────────────────────────────────────

class _FakeResp:
    # Minimal context-manager response that json.load() can consume.

    def __init__(self, payload):
        self._bytes = json.dumps(payload).encode()

    def read(self, *_a):
        return self._bytes

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _patch_urlopen(monkeypatch, handler):
    # Replace urllib.request.urlopen with *handler* (called as handler()).
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *_a, **_k: handler()
    )


# ── _parse_version ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "tag, expected",
    [
        ("v1.11.4", (1, 11, 4, 9999)),
        ("1.2.3", (1, 2, 3, 9999)),
        ("v2.0.0", (2, 0, 0, 9999)),
        ("v10.0.0", (10, 0, 0, 9999)),
        ("", (0, 0, 0, 0)),
        ("v1.2.x", (0, 0, 0, 0)),          # non-numeric component → sentinel
        ("garbage", (0, 0, 0, 0)),
        ("v10", (0, 0, 0, 0)),             # not full MAJOR.MINOR.PATCH → sentinel
        ("v1.14.0-beta.1", (1, 14, 0, 1)),
        ("v1.14.0-beta.2", (1, 14, 0, 2)),
        ("v1.14.0-beta.10", (1, 14, 0, 10)),
        ("v1.14.0-alpha.3", (1, 14, 0, 3)),
        ("v1.14.0-rc.1", (1, 14, 0, 1)),
    ],
)
def test_parse_version(tag, expected):
    assert _parse_version(tag) == expected


def test_parse_version_ordering():
    assert _parse_version("v2.0.0") > _parse_version("1.13.11")
    assert _parse_version("1.13.11") > _parse_version("1.13.2")
    assert not _parse_version("1.0.0") > _parse_version("1.0.0")


def test_parse_version_prerelease_ordering():
    # beta.2 is newer than beta.1 of the same base version.
    assert _parse_version("v1.14.0-beta.2") > _parse_version("v1.14.0-beta.1")
    # A stable release is always newer than any pre-release of the same base.
    assert _parse_version("v1.14.0") > _parse_version("v1.14.0-beta.99")
    # A beta of a NEW version is still newer than an OLDER stable release —
    # this was the original bug: betas used to parse to (0,) and always
    # lose every comparison.
    assert _parse_version("v1.14.0-beta.1") > _parse_version("v1.13.12")


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

def _run_worker(monkeypatch, qapp, current, stable=None, prerelease=None):
    """Run the worker body in-thread and return (update_emits, check_done_count).

    ``stable`` / ``prerelease`` stub what fetch_latest_release() /
    fetch_latest_prerelease() return respectively (each defaults to None,
    i.e. "nothing found"). Calling run() directly keeps it on the test
    thread, so the default direct signal connections fire synchronously —
    no event loop, fully deterministic.
    """
    monkeypatch.setattr(updater, "fetch_latest_release", lambda: stable)
    monkeypatch.setattr(updater, "fetch_latest_prerelease", lambda: prerelease)
    worker = UpdateCheckWorker(current)
    updates: list[tuple[str, str, bool]] = []
    done: list[int] = []
    worker.update_available.connect(lambda tag, url, is_pre: updates.append((tag, url, is_pre)))
    worker.check_done.connect(lambda: done.append(1))
    worker.run()
    return updates, len(done)


def test_worker_emits_update_when_newer(monkeypatch, qapp):
    updates, done = _run_worker(
        monkeypatch, qapp, "1.0.0",
        stable={"tag_name": "v2.0.0", "html_url": "https://example.test/u", "name": "n"},
    )
    assert updates == [("v2.0.0", "https://example.test/u", False)]
    assert done == 1


def test_worker_silent_when_same_version(monkeypatch, qapp):
    updates, done = _run_worker(
        monkeypatch, qapp, "2.0.0",
        stable={"tag_name": "v2.0.0", "html_url": "https://example.test/u", "name": "n"},
    )
    assert updates == []
    assert done == 1


def test_worker_silent_when_older(monkeypatch, qapp):
    updates, done = _run_worker(
        monkeypatch, qapp, "3.1.0",
        stable={"tag_name": "v2.0.0", "html_url": "https://example.test/u", "name": "n"},
    )
    assert updates == []
    assert done == 1


def test_worker_done_when_fetch_fails(monkeypatch, qapp):
    updates, done = _run_worker(monkeypatch, qapp, "1.0.0")
    assert updates == []
    assert done == 1   # check_done always fires, even with no release


# ── _is_prerelease_tag ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "tag, expected",
    [
        ("v1.14.0", False),
        ("1.13.12", False),
        ("v1.14.0-beta.1", True),
        ("v1.14.0-alpha.2", True),
        ("v1.14.0-rc.1", True),
        ("", False),
    ],
)
def test_is_prerelease_tag(tag, expected):
    from opensak.updater import _is_prerelease_tag
    assert _is_prerelease_tag(tag) is expected


# ── fetch_latest_prerelease ─────────────────────────────────────────────────

class _FakeListResp(_FakeResp):
    def __init__(self, payload_list):
        self._bytes = json.dumps(payload_list).encode()


def test_fetch_prerelease_finds_highest_version_entry(monkeypatch):
    _patch_urlopen(monkeypatch, lambda: _FakeListResp([
        {"tag_name": "v1.14.0", "html_url": "https://x/stable", "prerelease": False},
        {"tag_name": "v1.14.0-beta.2", "html_url": "https://x/beta2", "prerelease": True},
        {"tag_name": "v1.14.0-beta.1", "html_url": "https://x/beta1", "prerelease": True},
    ]))
    from opensak.updater import fetch_latest_prerelease
    out = fetch_latest_prerelease()
    assert out["tag_name"] == "v1.14.0-beta.2"


def test_fetch_prerelease_ignores_github_api_order(monkeypatch):
    """GitHub's /releases list is sorted by the tag's commit date, not by when
    the release was actually created — in practice this can list an older
    beta before a newer one (observed: beta.9 listed before beta.10). The
    function must pick the highest version regardless of array order."""
    _patch_urlopen(monkeypatch, lambda: _FakeListResp([
        {"tag_name": "v1.14.0-beta.9", "html_url": "https://x/beta9", "prerelease": True},
        {"tag_name": "v1.14.0-beta.10", "html_url": "https://x/beta10", "prerelease": True},
    ]))
    from opensak.updater import fetch_latest_prerelease
    out = fetch_latest_prerelease()
    assert out["tag_name"] == "v1.14.0-beta.10"
    assert out["html_url"] == "https://x/beta10"


def test_fetch_prerelease_returns_none_when_no_prerelease_present(monkeypatch):
    _patch_urlopen(monkeypatch, lambda: _FakeListResp([
        {"tag_name": "v1.14.0", "html_url": "https://x/stable", "prerelease": False},
    ]))
    from opensak.updater import fetch_latest_prerelease
    assert fetch_latest_prerelease() is None


def test_fetch_prerelease_returns_none_on_non_list_payload(monkeypatch):
    _patch_urlopen(monkeypatch, lambda: _FakeResp({"not": "a list"}))
    from opensak.updater import fetch_latest_prerelease
    assert fetch_latest_prerelease() is None


def test_fetch_prerelease_returns_none_on_urlerror(monkeypatch):
    def _boom():
        raise URLError("no network")
    _patch_urlopen(monkeypatch, _boom)
    from opensak.updater import fetch_latest_prerelease
    assert fetch_latest_prerelease() is None


# ── Worker routes to the correct fetch function based on running version ────

def test_worker_uses_stable_fetch_when_running_stable(monkeypatch, qapp):
    # A stable (main) user must never hit the prerelease endpoint.
    called = {"prerelease": False}

    def _fail_if_called():
        called["prerelease"] = True
        return None

    monkeypatch.setattr(updater, "fetch_latest_release",
                         lambda: {"tag_name": "v1.14.0", "html_url": "https://example.test/u", "name": "n"})
    monkeypatch.setattr(updater, "fetch_latest_prerelease", _fail_if_called)
    worker = UpdateCheckWorker("1.13.12")
    updates: list[tuple[str, str, bool]] = []
    worker.update_available.connect(lambda tag, url, is_pre: updates.append((tag, url, is_pre)))
    worker.run()

    assert called["prerelease"] is False
    assert updates == [("v1.14.0", "https://example.test/u", False)]


def test_worker_beta_user_checks_both_stable_and_prerelease(monkeypatch, qapp):
    # A beta user must be checked against BOTH feeds, not just the
    # prerelease one — see test_worker_beta_user_sees_newer_stable_release
    # below for why (a stable release is a real update for a beta user too).
    called = {"stable": False, "prerelease": False}
    real_stable = updater.fetch_latest_release
    real_prerelease = updater.fetch_latest_prerelease

    def _track_stable():
        called["stable"] = True
        return None

    def _track_prerelease():
        called["prerelease"] = True
        return {"tag_name": "v1.14.0-beta.2", "html_url": "https://example.test/beta2", "name": "n"}

    monkeypatch.setattr(updater, "fetch_latest_release", _track_stable)
    monkeypatch.setattr(updater, "fetch_latest_prerelease", _track_prerelease)
    worker = UpdateCheckWorker("1.14.0-beta.1")
    updates: list[tuple[str, str, bool]] = []
    worker.update_available.connect(lambda tag, url, is_pre: updates.append((tag, url, is_pre)))
    worker.run()

    assert called == {"stable": True, "prerelease": True}
    assert updates == [("v1.14.0-beta.2", "https://example.test/beta2", True)]


def test_worker_beta_user_sees_no_update_for_older_beta(monkeypatch, qapp):
    updates, done = _run_worker(
        monkeypatch, qapp, "1.14.0-beta.2",
        prerelease={"tag_name": "v1.14.0-beta.1", "html_url": "https://example.test/u", "name": "n"},
    )
    assert updates == []
    assert done == 1


def test_worker_beta_user_sees_newer_stable_release(monkeypatch, qapp):
    # The bug this fixes: a beta-running user (e.g. on v1.15.0-beta.16)
    # never learned that v1.15.0 stable had shipped, because the old code
    # only ever compared against fetch_latest_prerelease() — which by
    # definition never returns a non-prerelease entry. A stable release is
    # a real, higher-priority update for a beta user too.
    updates, done = _run_worker(
        monkeypatch, qapp, "1.15.0-beta.16",
        stable={"tag_name": "v1.15.0", "html_url": "https://example.test/stable", "name": "n"},
        prerelease=None,
    )
    assert updates == [("v1.15.0", "https://example.test/stable", False)]
    assert done == 1


def test_worker_beta_user_prefers_newer_beta_over_older_stable(monkeypatch, qapp):
    # If a newer beta cycle has already started (e.g. v1.16.0-beta.1) while
    # the current stable is still the older v1.15.0, the beta must win —
    # it's the objectively higher version.
    updates, done = _run_worker(
        monkeypatch, qapp, "1.15.0-beta.16",
        stable={"tag_name": "v1.15.0", "html_url": "https://example.test/stable", "name": "n"},
        prerelease={"tag_name": "v1.16.0-beta.1", "html_url": "https://example.test/beta1", "name": "n"},
    )
    assert updates == [("v1.16.0-beta.1", "https://example.test/beta1", True)]


def test_worker_beta_user_silent_when_both_fetches_fail(monkeypatch, qapp):
    updates, done = _run_worker(monkeypatch, qapp, "1.14.0-beta.5", stable=None, prerelease=None)
    assert updates == []
    assert done == 1


def test_worker_beta_user_still_works_when_stable_fetch_fails(monkeypatch, qapp):
    # A network hiccup fetching the stable feed shouldn't prevent a beta
    # user from still being offered a newer beta.
    updates, done = _run_worker(
        monkeypatch, qapp, "1.14.0-beta.1",
        stable=None,
        prerelease={"tag_name": "v1.14.0-beta.2", "html_url": "https://example.test/beta2", "name": "n"},
    )
    assert updates == [("v1.14.0-beta.2", "https://example.test/beta2", True)]

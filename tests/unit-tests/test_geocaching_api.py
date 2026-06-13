"""tests/unit-tests/test_geocaching_api.py — network-mocked API tests (no real HTTP)."""

import base64
import json
import os
import time
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

import opensak.api.geocaching as gc_module
from opensak.api.geocaching import (
    _delete_token,
    _generate_pkce,
    _is_token_valid,
    _load_token,
    _save_token,
    get_cache_details,
    is_logged_in,
    logout,
)


# ── Shared fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_token_cache():
    """Clear the in-memory token cache before and after every test."""
    gc_module._cached_token = None
    yield
    gc_module._cached_token = None


@pytest.fixture
def token_file(tmp_path):
    """Redirect get_token_file() to a path inside tmp_path."""
    token_path = tmp_path / "gc_token.json"
    with patch("opensak.api.geocaching.get_token_file", return_value=token_path):
        yield token_path


def _mock_urlopen(payload: dict):
    """Return a context-manager mock that yields a response with JSON payload."""
    body = json.dumps(payload).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ── _generate_pkce ────────────────────────────────────────────────────────────


class TestGeneratePkce:
    def test_verifier_meets_rfc7636_length(self):
        verifier, _ = _generate_pkce()
        assert 43 <= len(verifier) <= 128

    def test_challenge_is_url_safe_base64_without_padding(self):
        _, challenge = _generate_pkce()
        assert "+" not in challenge
        assert "/" not in challenge
        assert "=" not in challenge

    def test_challenge_decodes_to_sha256_digest(self):
        _, challenge = _generate_pkce()
        padded = challenge + "=" * (-len(challenge) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        assert len(decoded) == 32  # SHA-256 is always 32 bytes

    def test_successive_calls_produce_distinct_verifiers(self):
        v1, _ = _generate_pkce()
        v2, _ = _generate_pkce()
        assert v1 != v2


# ── _is_token_valid ───────────────────────────────────────────────────────────


class TestIsTokenValid:
    def test_valid_token(self):
        assert _is_token_valid({"expires_at": time.time() + 3600}) is True

    def test_expired_token(self):
        assert _is_token_valid({"expires_at": time.time() - 1}) is False

    def test_token_within_60s_buffer_is_invalid(self):
        # Expires in 30 s — inside the 60 s safety buffer → treat as expired
        assert _is_token_valid({"expires_at": time.time() + 30}) is False

    def test_missing_expires_at_defaults_to_zero(self):
        assert _is_token_valid({}) is False


# ── Token persistence ─────────────────────────────────────────────────────────


class TestTokenPersistence:
    def test_save_and_load_roundtrip(self, token_file):
        data = {"access_token": "abc123", "expires_at": time.time() + 3600}
        _save_token(data)
        loaded = _load_token()
        assert loaded == data

    def test_save_creates_file(self, token_file):
        _save_token({"access_token": "x", "expires_at": 9_999_999_999})
        assert token_file.exists()

    def test_save_sets_600_permissions_on_posix(self, token_file):
        _save_token({"access_token": "x", "expires_at": 9_999_999_999})
        if os.name == "posix":
            assert (token_file.stat().st_mode & 0o777) == 0o600

    def test_load_returns_none_when_file_absent(self, token_file):
        assert not token_file.exists()
        assert _load_token() is None

    def test_load_returns_cached_value_without_disk_read(self, token_file):
        data = {"access_token": "cached", "expires_at": 9_999_999_999}
        _save_token(data)
        # Corrupt the file — the in-memory cache should be served instead
        token_file.write_text("not json", encoding="utf-8")
        gc_module._cached_token = data
        assert _load_token() == data

    def test_delete_removes_file(self, token_file):
        _save_token({"access_token": "y", "expires_at": 9_999_999_999})
        _delete_token()
        assert not token_file.exists()

    def test_delete_clears_memory_cache(self, token_file):
        _save_token({"access_token": "y", "expires_at": 9_999_999_999})
        _delete_token()
        assert gc_module._cached_token is None

    def test_delete_when_file_absent_does_not_raise(self, token_file):
        assert not token_file.exists()
        _delete_token()  # must not raise


# ── is_logged_in ──────────────────────────────────────────────────────────────


class TestIsLoggedIn:
    def test_false_with_no_token_file(self, token_file):
        assert is_logged_in() is False

    def test_true_with_valid_token(self, token_file):
        _save_token({"access_token": "tok", "expires_at": time.time() + 3600})
        assert is_logged_in() is True

    def test_true_with_expired_token(self, token_file):
        # is_logged_in only checks presence, not validity
        _save_token({"access_token": "old", "expires_at": time.time() - 1})
        assert is_logged_in() is True


# ── logout ────────────────────────────────────────────────────────────────────


class TestLogout:
    def test_logout_removes_token_file(self, token_file):
        _save_token({"access_token": "tok", "expires_at": time.time() + 3600})
        logout()
        assert not token_file.exists()

    def test_logout_clears_memory_cache(self, token_file):
        _save_token({"access_token": "tok", "expires_at": time.time() + 3600})
        logout()
        assert gc_module._cached_token is None


# ── get_cache_details ─────────────────────────────────────────────────────────


_SAMPLE_CACHE_PAYLOAD = {
    "referenceCode": "GC12345",
    "name": "Test Cache",
    "difficulty": 2.0,
    "terrain": 3.0,
    "favoritePoints": 42,
    "trackables": [],
    "shortDescription": "<p>Short.</p>",
    "longDescription": "<p>Long.</p>",
    "hints": "Under a rock",
    "attributes": [{"id": 1, "name": "Dogs allowed"}],
    "recentActivity": [],
    "geocacheType": {"id": 2, "name": "Traditional Cache"},
    "status": "Active",
}


@pytest.fixture
def logged_in_client(token_file):
    """Provide a valid token and a non-empty GC_CLIENT_ID."""
    _save_token({"access_token": "valid_token", "expires_at": time.time() + 3600})
    with patch.object(gc_module, "GC_CLIENT_ID", "test_client_id"):
        yield


class TestGetCacheDetails:
    def test_returns_none_when_client_id_empty(self, token_file):
        with patch.object(gc_module, "GC_CLIENT_ID", ""):
            result = get_cache_details("GC12345")
        assert result is None

    def test_returns_none_for_invalid_gc_code(self, logged_in_client):
        result = get_cache_details("INVALID")
        assert result is None

    def test_returns_populated_dict_on_success(self, logged_in_client):
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(_SAMPLE_CACHE_PAYLOAD)):
            result = get_cache_details("GC12345")

        assert result is not None
        assert result["referenceCode"] == "GC12345"
        assert result["name"] == "Test Cache"
        assert result["difficulty"] == 2.0
        assert result["terrain"] == 3.0
        assert result["favoritePoints"] == 42
        assert result["hints"] == "Under a rock"
        assert result["attributes"][0]["name"] == "Dogs allowed"
        assert result["geocacheType"]["name"] == "Traditional Cache"

    def test_returns_none_on_http_error(self, logged_in_client):
        err = urllib.error.HTTPError(url=None, code=500, msg="Server Error", hdrs=None, fp=None)
        with patch("urllib.request.urlopen", side_effect=err):
            result = get_cache_details("GC12345")
        assert result is None

    def test_returns_none_when_not_logged_in(self, token_file):
        with patch.object(gc_module, "GC_CLIENT_ID", "test_client_id"):
            result = get_cache_details("GC12345")
        assert result is None

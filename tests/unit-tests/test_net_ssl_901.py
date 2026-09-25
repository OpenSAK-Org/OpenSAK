"""tests/unit-tests/test_net_ssl_901.py — issue #901.

Boundary Data Updates failed on macOS with "no network connection" because
geo/packs.py called urlopen() without the certifi-based SSL context that
updater.py already used: a PyInstaller-bundled Python on macOS can't find
the system CA certificates, so certificate verification failed and the
error was swallowed as "no network".

Every HTTPS request now goes through opensak.net.SSL_CONTEXT. The static
scan below makes sure no urlopen() call in src/ can forget it again.
"""

from __future__ import annotations

import ast
import ssl
from pathlib import Path
from urllib.error import URLError

import pytest

import opensak.geo.packs as packs
import opensak.updater as updater
from opensak.net import SSL_CONTEXT, build_ssl_context

SRC = Path(__file__).resolve().parents[2] / "src" / "opensak"


# ── The shared context itself ─────────────────────────────────────────────

def test_context_verifies_certificates_and_hostnames():
    assert SSL_CONTEXT.verify_mode == ssl.CERT_REQUIRED
    assert SSL_CONTEXT.check_hostname is True


def test_context_includes_every_certifi_root():
    # Works where the system store can't be found (bundled macOS app).
    certifi = pytest.importorskip("certifi")
    certifi_only = ssl.create_default_context(cafile=certifi.where())
    assert SSL_CONTEXT.cert_store_stats()["x509_ca"] >= \
        certifi_only.cert_store_stats()["x509_ca"] > 0


def test_context_keeps_the_system_store_too(monkeypatch):
    # Users behind a TLS-inspecting corporate proxy have its root only in the
    # system store — certifi must be added on top, never replace it.
    calls: list = []
    real = ssl.create_default_context

    def _spy(*a, **k):
        calls.append((a, k))
        return real(*a, **k)
    monkeypatch.setattr(ssl, "create_default_context", _spy)
    build_ssl_context()
    assert calls == [((), {})]  # system defaults, not cafile=certifi-only


def test_falls_back_to_system_context_without_certifi(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def _no_certifi(name, *a, **k):
        if name == "certifi":
            raise ImportError("simulated")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", _no_certifi)

    ctx = build_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED  # still verifying, never disabled


def test_updater_shares_the_same_context():
    assert updater._SSL_CONTEXT is SSL_CONTEXT


# ── No urlopen() in src/ may go without it ────────────────────────────────

def _urlopen_calls_without_context() -> list[str]:
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name != "urlopen":
                continue
            if not any(kw.arg == "context" for kw in node.keywords):
                offenders.append(f"{path.relative_to(SRC.parent)}:{node.lineno}")
    return offenders


def test_every_urlopen_call_passes_an_ssl_context():
    offenders = _urlopen_calls_without_context()
    assert offenders == [], (
        "urlopen() without context= fails HTTPS verification in the bundled "
        "macOS app (issue #901) — pass context=opensak.net.SSL_CONTEXT:\n  "
        + "\n  ".join(offenders)
    )


def test_the_scan_finds_the_known_call_sites():
    # Guard against the scan silently matching nothing (e.g. a moved SRC).
    count = 0
    for path in SRC.rglob("*.py"):
        count += path.read_text(encoding="utf-8").count("urlopen(")
    assert count >= 10


# ── geo/packs.py actually passes it ───────────────────────────────────────

class _Resp:
    def __init__(self, data: bytes):
        self._data = data

    def read(self, *_a):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


@pytest.fixture
def captured(monkeypatch):
    calls: list[dict] = []

    def _fake(url, **kw):
        calls.append(kw)
        return _Resp(b"{}")
    monkeypatch.setattr("urllib.request.urlopen", _fake)
    return calls


def test_fetch_manifest_uses_the_ssl_context(captured):
    assert packs.fetch_manifest() == {}
    assert captured[0]["context"] is SSL_CONTEXT


def test_fetch_pack_uses_the_ssl_context(captured, tmp_path):
    assert packs.fetch_pack("x.db", tmp_path)
    assert captured[0]["context"] is SSL_CONTEXT


def test_fetch_file_atomic_uses_the_ssl_context(captured, tmp_path):
    assert packs._fetch_file_atomic("x.db", tmp_path)
    assert captured[0]["context"] is SSL_CONTEXT


def test_manifest_failure_is_logged_as_warning(monkeypatch):
    # The dialog reports any failure as "no network connection"; the real
    # cause (e.g. CERTIFICATE_VERIFY_FAILED) must at least reach the log.
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(URLError("certificate verify failed")),
    )
    warnings: list = []
    monkeypatch.setattr(packs.log, "warning", lambda *a, **_k: warnings.append(a))
    assert packs.fetch_manifest() is None
    assert warnings and "certificate verify failed" in str(warnings[0])

"""
tests/conftest.py — Shared fixtures for OpenSAK tests.
"""

import os

# QtWebEngine's Chromium stack crashes the headless test process (SIGTRAP); swap
# in native Qt widgets and keep any stray WebEngine view off the GPU. Must run
# before any widget is constructed, hence at conftest import time.
os.environ.setdefault("OPENSAK_DISABLE_WEBENGINE", "1")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --disable-software-rasterizer --disable-gpu-compositing",
)

import pytest
from opensak.db.database import init_db, make_session
from opensak.db.models import Cache


@pytest.fixture(autouse=True)
def _no_network_update_check(monkeypatch):
    """Stub the GitHub update check offline so no test ever opens a socket.

    The real urlopen() in a QThread can't be interrupted by quit(), so on CI it
    blocks in getaddrinfo and aborts the process at teardown (SIGABRT / exit 134).
    """
    monkeypatch.setattr("opensak.updater.fetch_latest_release", lambda: None)


@pytest.fixture(scope="module")
def tmp_db(tmp_path_factory):
    """Create a fresh SQLite DB in a temp directory for a test module."""
    db_path = tmp_path_factory.mktemp("data") / "test.db"
    init_db(db_path=db_path)
    return db_path


@pytest.fixture
def db_session(tmp_path):
    """Fresh isolated DB + bare Session for each test. Caller must commit."""
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    session = make_session()
    yield session
    session.close()


@pytest.fixture
def make_cache():
    """Return a factory that builds Cache instances with sensible defaults."""
    def _factory(gc_code: str = "GC12345", **kwargs) -> Cache:
        defaults = dict(
            name="Test Cache",
            cache_type="Traditional Cache",
            latitude=55.0,
            longitude=12.0,
        )
        defaults.update(kwargs)
        return Cache(gc_code=gc_code, **defaults)
    return _factory

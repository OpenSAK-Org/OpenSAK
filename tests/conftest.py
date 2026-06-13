"""
tests/conftest.py — Shared fixtures for OpenSAK tests.
"""

import os

# QtWebEngine starts a Chromium multi-process stack that is unstable under the
# headless pytest / CI harness: across a long e2e run the render or GPU process
# crashes with SIGTRAP (exit 133), killing the whole test process — regardless
# of GPU flags or per-window cleanup. The map and the cache description panel
# only render simple HTML in tests, so we swap in native Qt widgets and never
# create Chromium at all. This removes the entire class of WebEngine crashes.
# Must be set before any widget is constructed, hence at conftest import time.
os.environ.setdefault("OPENSAK_DISABLE_WEBENGINE", "1")

# Belt-and-suspenders: if any QtWebEngine view is still created (e.g. a future
# code path), keep it off the GPU so it cannot crash the GPU thread either.
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --disable-software-rasterizer --disable-gpu-compositing",
)

import pytest
from opensak.db.database import init_db, make_session
from opensak.db.models import Cache


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

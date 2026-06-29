# tests/screenshots/conftest.py — fixtures for the screenshot suite.
#
# This mirrors tests/e2e-tests/conftest.py's isolation pattern, but seeds the
# richer demo dataset (tests/screenshots/demo_data.py) instead of the minimal
# e2e fixtures, and exposes an output-directory fixture for saving PNGs.
#
# IMPORTANT: the parent tests/conftest.py sets OPENSAK_DISABLE_WEBENGINE=1 for
# every test (Chromium is flaky over a *long* headless e2e run). Screenshots are
# the one case where we explicitly want the real map, not the "Map disabled"
# placeholder — this is a single short-lived widget, not hundreds of tests, so
# the crash risk that justified disabling it elsewhere doesn't really apply
# here. We flip it back on before anything else imports opensak.gui.*.
# If this ever proves flaky in CI, the safe fallback is to run just
# test_main_window locally (real desktop, not xvfb) and skip it in CI.
import os

os.environ["OPENSAK_DISABLE_WEBENGINE"] = "0"
# --no-sandbox: Chromium refuses to start as root without it (common in
# containerized CI runners; harmless when not running as root).
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--no-sandbox --disable-gpu --disable-software-rasterizer --disable-gpu-compositing"
)

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from tests.data import make_fake_manager
from tests.screenshots.demo_data import seed_demo_caches


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    """Isolate SettingsStore so each test gets a fresh in-memory store."""
    from opensak import settings_store as ss
    fresh = ss.SettingsStore()
    fresh._data = {}
    fresh._path = tmp_path / "opensak.json"
    monkeypatch.setattr(ss, "_store", fresh)
    import opensak.gui.settings as smod
    monkeypatch.setattr(smod, "_settings", None)


@pytest.fixture(autouse=True)
def _quiet_startup(monkeypatch):
    """Disable startup callbacks that would otherwise fire mid-screenshot
    (background update check, first-run wizard, delayed re-refresh)."""
    from opensak.gui.mainwindow import MainWindow

    monkeypatch.setattr(MainWindow, "_initial_load", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_update_background", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_setup_complete", lambda self: None)


@pytest.fixture
def screenshot_dir() -> Path:
    """Where PNGs get written. Override with OPENSAK_SCREENSHOT_DIR for CI;
    defaults to site/assets/screenshots/ so local runs land straight in the
    website folder ready to review and commit."""
    repo_root = Path(__file__).resolve().parents[2]
    out = Path(os.environ.get("OPENSAK_SCREENSHOT_DIR", repo_root / "site" / "assets" / "screenshots"))
    out.mkdir(parents=True, exist_ok=True)
    return out


@pytest.fixture
def demo_window(qtbot, tmp_path, monkeypatch):
    """A shown MainWindow on a throwaway DB seeded with the 12-cache demo set."""
    import opensak.db.manager as mgr_module
    from opensak.db.database import init_db
    from opensak.lang import load_language

    load_language("en")

    db_path = tmp_path / "ScreenshotDemo.db"
    init_db(db_path=db_path)
    seed_demo_caches(tmp_path)

    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="ScreenshotDemo"))

    from opensak.gui.mainwindow import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(1440, 900)
    window.show()
    qtbot.waitExposed(window)
    window._refresh_cache_list()

    try:
        yield window
    finally:
        window.close()
        mgr_module._manager = None

"""
_headless.py — Detect when QtWebEngine (Chromium) must not be instantiated.

QtWebEngine starts a Chromium multi-process stack that is unstable under the
headless pytest / CI harness: across a long e2e run the render or GPU process
crashes with SIGTRAP (exit 133), killing the test process. The map widget and
the cache description panel only need to render simple HTML during tests, so
when OPENSAK_DISABLE_WEBENGINE=1 they fall back to native Qt widgets and no
Chromium is ever created — removing the entire class of WebEngine crashes.

The flag is set by tests/conftest.py. Production never sets it and always uses
the real QtWebEngine views.
"""

from __future__ import annotations

import os


def webengine_disabled() -> bool:
    """Return True when QtWebEngine should be replaced by Qt-native widgets."""
    return os.environ.get("OPENSAK_DISABLE_WEBENGINE") == "1"

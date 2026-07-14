#!/usr/bin/env python3
"""
scripts/fetch_boundary_baseline.py

Fetch the reverse-geocoding baseline (boundaries.db + countries/ + states/)
into data/ before a PyInstaller build, so opensak.spec has something to bundle.

Best-effort: prints a warning and exits 0 on failure rather than breaking a
release build over a transient network issue — the app falls back to
fetching this itself at first run (see opensak.geo.store.ensure_baseline_seeded).

Usage:
    python scripts/fetch_boundary_baseline.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from opensak.geo.packs import fetch_baseline

if fetch_baseline(Path("data")):
    print("Boundary baseline fetched into data/")
else:
    print("Warning: could not fetch boundary baseline — build will ship without bundled boundary data")

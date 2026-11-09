#!/usr/bin/env python3
"""
run.py — Start OpenSAK from the repo root.

Usage:
    python run.py                   # the GUI
    python run.py import gpx …      # a headless command, see opensak.cli
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

# Not opensak.app: a headless command must not import the GUI (#937).
from opensak.cli import entry

if __name__ == "__main__":
    entry()
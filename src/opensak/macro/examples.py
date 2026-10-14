"""
src/opensak/macro/examples.py — example macros shipped with OpenSAK.

The examples live in macros/examples (bundled by opensak.spec). They are
never run from there: "Open example" first copies them into the user's
macros folder, which macros may read by default, so relative paths such as
the CSV next to an example resolve without any extra folder permission.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

from opensak.macro.permissions import macros_dir


def bundled_examples_dir() -> Path:
    """Where the shipped examples are: inside the PyInstaller bundle, or
    macros/examples in a source checkout."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", "")) / "macros" / "examples"
    return Path(__file__).resolve().parents[3] / "macros" / "examples"


def list_examples() -> list[str]:
    """File names of the shipped example macros, sorted."""
    folder = bundled_examples_dir()
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.glob("*.lua"))


def _installed_dir() -> Path:
    return macros_dir() / "examples"


def _bundled(name: str) -> Path:
    source = bundled_examples_dir() / name
    if source.suffix != ".lua" or not source.is_file():
        raise FileNotFoundError(f"no example macro named {name!r}")
    return source


def example_differs(name: str) -> bool:
    """True if the user's copy of example *name* exists and is not the
    shipped version — edited by the user, or updated in a later release."""
    installed = _installed_dir() / name
    try:
        return installed.read_bytes() != _bundled(name).read_bytes()
    except FileNotFoundError:
        return False


def install_example(name: str, restore: bool = False) -> Path:
    """Copy example *name* and the data files the examples use (everything
    that is not a .lua) into <macros>/examples and return the copied macro.

    Files already there are left alone, so a user's edits survive opening
    the same example again. With *restore*, a copy of the macro that
    differs from the shipped one is renamed to a timestamped .bak next to
    it and replaced by the shipped version (data files stay untouched).
    """
    source = _bundled(name)
    target_dir = _installed_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    if restore and example_differs(name):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (target_dir / name).replace(target_dir / f"{name}.{stamp}.bak")
    files = [source] + [
        p for p in source.parent.iterdir() if p.is_file() and p.suffix != ".lua"
    ]
    for file in files:
        target = target_dir / file.name
        if not target.exists():
            shutil.copy2(file, target)
    return target_dir / name

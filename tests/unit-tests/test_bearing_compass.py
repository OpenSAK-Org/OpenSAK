"""The distance/bearing dialog's 8-point compass (#1128 follow-up).

#1128 moved the compass points into the "bearing_dirs" translation. Before
a language is loaded tr() returns the key itself, so the split gave one
item and _compass() raised IndexError — seen when an e2e test ran on its
own, without an earlier test having loaded a language.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from opensak.gui.dialogs import distance_bearing_dialog as dbd
from opensak.gui.dialogs.distance_bearing_dialog import DistanceBearingDialog

LANG_DIR = pathlib.Path(__file__).resolve().parents[2] / "src" / "opensak" / "lang"
LANG_FILES = sorted(f for f in LANG_DIR.glob("*.py") if f.stem != "__init__")


def _strings(path: pathlib.Path) -> dict:
    spec = importlib.util.spec_from_file_location(f"lang_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.STRINGS


@pytest.mark.parametrize("lang_file", LANG_FILES, ids=[f.stem for f in LANG_FILES])
def test_every_language_has_eight_compass_points(lang_file):
    points = _strings(lang_file)["bearing_dirs"].split()
    assert len(points) == 8, f"{lang_file.stem}: bearing_dirs has {len(points)} points: {points}"


@pytest.mark.parametrize("deg, expected", [
    (0, "N"), (44, "NE"), (90, "E"), (135, "SE"),
    (180, "S"), (225, "SW"), (270, "W"), (315, "NW"), (359, "N"),
])
def test_compass_falls_back_to_english_when_key_is_unresolved(monkeypatch, deg, expected):
    # No language loaded: tr() hands back the key.
    monkeypatch.setattr(dbd, "tr", lambda key, **kw: key)
    assert DistanceBearingDialog._compass(deg) == expected


def test_compass_falls_back_when_translation_has_wrong_count(monkeypatch):
    monkeypatch.setattr(dbd, "tr", lambda key, **kw: "N E S W")
    assert DistanceBearingDialog._compass(315) == "NW"


def test_compass_uses_the_translation(monkeypatch):
    monkeypatch.setattr(dbd, "tr", lambda key, **kw: "N NØ Ø SØ S SV V NV")
    assert DistanceBearingDialog._compass(45) == "NØ"

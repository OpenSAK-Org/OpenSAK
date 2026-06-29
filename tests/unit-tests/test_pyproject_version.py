# tests/unit-tests/test_pyproject_version.py — guards against a second,
# driftable version number reappearing in pyproject.toml.
#
# Background: pyproject.toml used to hardcode its own `version = "..."`
# under [project], separate from the single source of truth in
# src/opensak/__init__.py (__version__). That field was never read by any
# build step or by the app itself, so it silently went stale (it sat on
# "1.14.0-beta.6" while __init__.py had already moved on to beta.14).
#
# Fix: [project] now declares `dynamic = ["version"]` and
# [tool.setuptools.dynamic] resolves it from opensak.__version__, so
# pyproject.toml's version is *computed*, not duplicated. These tests make
# sure nobody reintroduces a static version literal, and that the dynamic
# resolution still points at __init__.py.

import re
import tomllib
from pathlib import Path

import opensak

PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _load_pyproject() -> dict:
    with open(PYPROJECT_PATH, "rb") as f:
        return tomllib.load(f)


def test_project_table_has_no_static_version():
    """[project] must declare version as dynamic, not as a literal string."""
    data = _load_pyproject()
    project = data.get("project", {})

    assert "version" not in project, (
        "pyproject.toml has a static [project] version again — this drifts "
        "from src/opensak/__init__.py. Use dynamic = [\"version\"] instead."
    )
    assert "version" in project.get("dynamic", []), (
        "[project] is missing dynamic = [\"version\"]; without it, "
        "setuptools expects a static version field."
    )


def test_dynamic_version_points_at_init_module():
    """[tool.setuptools.dynamic].version must resolve from opensak.__version__."""
    data = _load_pyproject()
    dynamic_cfg = data.get("tool", {}).get("setuptools", {}).get("dynamic", {})

    assert dynamic_cfg.get("version", {}).get("attr") == "opensak.__version__", (
        "tool.setuptools.dynamic.version is no longer pointed at "
        "opensak.__version__ — the single source of truth has moved or "
        "this config rotted."
    )


def test_no_raw_version_literal_in_project_table():
    """Belt-and-braces text check: no `version = "..."` line under [project]."""
    text = PYPROJECT_PATH.read_text(encoding="utf-8")
    project_section = text.split("[project]", 1)[1].split("\n[", 1)[0]

    assert not re.search(r'^\s*version\s*=\s*"', project_section, re.MULTILINE), (
        "Found a literal version = \"...\" inside [project] in pyproject.toml."
    )


def test_init_version_is_set():
    """Sanity check the actual source of truth isn't empty/missing."""
    assert isinstance(opensak.__version__, str)
    assert opensak.__version__.strip() != ""

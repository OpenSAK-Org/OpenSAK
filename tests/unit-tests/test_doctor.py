# tests/unit-tests/test_doctor.py — doctor checks (python, deps, config dir, metadata).

import importlib
import importlib.metadata
import subprocess
import sys
import tomllib
from types import SimpleNamespace

import pytest

from opensak.utils import doctor
from opensak.utils.doctor import (
    check_config_dir,
    check_dependencies,
    check_feature_flags,
    check_git,
    check_python,
    check_venv,
    extract_package_name,
    parse_python_requirement,
    run,
    _project_metadata,
)


# ── check_python ──────────────────────────────────────────────────────────────


class TestCheckPython:
    def test_passes_when_version_meets_requirement(self, monkeypatch):
        monkeypatch.setattr(sys, "version_info", (3, 11, 0))
        name, ok, _ = check_python({"requires-python": ">=3.11"})
        assert name == "Python"
        assert ok is True

    def test_fails_when_version_too_old(self, monkeypatch):
        monkeypatch.setattr(sys, "version_info", (3, 9, 0))
        name, ok, msg = check_python({"requires-python": ">=3.11"})
        assert name == "Python"
        assert ok is False
        assert "3.11" in msg

    def test_passes_with_newer_minor(self, monkeypatch):
        monkeypatch.setattr(sys, "version_info", (3, 13, 0))
        _, ok, _ = check_python({"requires-python": ">=3.11"})
        assert ok is True

    def test_passes_with_newer_major(self, monkeypatch):
        monkeypatch.setattr(sys, "version_info", (4, 0, 0))
        _, ok, _ = check_python({"requires-python": ">=3.11"})
        assert ok is True

    def test_passes_when_no_requirement_in_project(self):
        _, ok, _ = check_python({})
        assert ok is True

    def test_message_includes_requirement_on_failure(self, monkeypatch):
        monkeypatch.setattr(sys, "version_info", (3, 8, 0))
        _, _, msg = check_python({"requires-python": ">=3.11"})
        assert ">=3.11" in msg


# ── check_dependencies ────────────────────────────────────────────────────────


class TestCheckDependencies:
    def test_ok_with_no_dependencies(self):
        name, ok, msg = check_dependencies({"dependencies": []})
        assert name == "Dependencies"
        assert ok is True
        assert msg == "OK"

    def test_ok_when_all_packages_importable(self):
        _, ok, _ = check_dependencies({"dependencies": ["sqlalchemy>=2.0"]})
        assert ok is True

    def test_fails_for_nonexistent_package(self):
        _, ok, msg = check_dependencies({"dependencies": ["totally-fake-pkg>=1.0"]})
        assert ok is False
        assert "totally-fake-pkg" in msg

    def test_reports_all_missing_packages(self):
        project = {"dependencies": ["fake-one>=1", "fake-two>=2"]}
        _, ok, msg = check_dependencies(project)
        assert ok is False
        assert "fake-one" in msg
        assert "fake-two" in msg

    def test_simulates_missing_package_via_mock(self, monkeypatch):
        real_import = importlib.import_module

        def fake_import(name):
            if name == "sqlalchemy":
                raise ImportError("mocked missing")
            return real_import(name)

        monkeypatch.setattr(importlib, "import_module", fake_import)
        _, ok, msg = check_dependencies({"dependencies": ["sqlalchemy>=2.0"]})
        assert ok is False
        assert "sqlalchemy" in msg

    def test_ok_with_missing_project_key(self):
        _, ok, _ = check_dependencies({})
        assert ok is True


# ── check_config_dir ──────────────────────────────────────────────────────────


class TestCheckConfigDir:
    def test_returns_ok_and_existing_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr("opensak.config.get_app_data_dir", lambda: tmp_path / "opensak")
        name, ok, path_str = check_config_dir()
        assert name == "Config dir"
        assert ok is True
        assert (tmp_path / "opensak").exists()

    def test_returned_path_matches_app_data_dir(self, monkeypatch, tmp_path):
        expected = tmp_path / "opensak"
        monkeypatch.setattr("opensak.config.get_app_data_dir", lambda: expected)
        _, _, path_str = check_config_dir()
        assert path_str == str(expected)

    def test_idempotent_when_dir_already_exists(self, monkeypatch, tmp_path):
        target = tmp_path / "opensak"
        target.mkdir()
        monkeypatch.setattr("opensak.config.get_app_data_dir", lambda: target)
        _, ok, _ = check_config_dir()
        assert ok is True

    def test_fails_when_dir_not_writable(self, monkeypatch, tmp_path):
        def _raise():
            raise PermissionError("no write")
        monkeypatch.setattr("opensak.config.get_app_data_dir", _raise)
        _, ok, _ = check_config_dir()
        assert ok is False


# ── _project_metadata ─────────────────────────────────────────────────────────


class TestProjectMetadata:
    def test_returns_dict(self):
        data = _project_metadata()
        assert isinstance(data, dict)

    def test_contains_requires_python(self):
        data = _project_metadata()
        assert "requires-python" in data

    def test_contains_dependencies(self):
        data = _project_metadata()
        assert "dependencies" in data

    def test_dependencies_is_list(self):
        data = _project_metadata()
        assert isinstance(data["dependencies"], list)

    def test_dev_dependencies_excluded(self):
        data = _project_metadata()
        for dep in data["dependencies"]:
            assert "extra ==" not in dep

    def test_falls_back_to_pyproject_when_package_missing(self, monkeypatch):
        def boom(_name):
            raise importlib.metadata.PackageNotFoundError("opensak")
        monkeypatch.setattr(importlib.metadata, "metadata", boom)
        data = _project_metadata()
        assert "requires-python" in data
        assert isinstance(data["dependencies"], list)

    def test_returns_empty_when_both_sources_fail(self, monkeypatch):
        def boom_meta(_name):
            raise importlib.metadata.PackageNotFoundError("opensak")
        def boom_load(_f):
            raise ValueError("bad toml")
        monkeypatch.setattr(importlib.metadata, "metadata", boom_meta)
        monkeypatch.setattr(tomllib, "load", boom_load)
        assert _project_metadata() == {}


# ── small helpers ─────────────────────────────────────────────────────────────

class TestHelpers:
    @pytest.mark.parametrize("dep,expected", [
        ("PySide6>=6.5", "PySide6"),
        ("sqlalchemy[asyncio]>=2.0", "sqlalchemy"),
        ("pkg!=1.0", "pkg"),
        ("pkg; python_version>'3.8'", "pkg"),
        ("trailing ", "trailing"),
    ])
    def test_extract_package_name(self, dep, expected):
        assert extract_package_name(dep) == expected

    @pytest.mark.parametrize("spec,expected", [
        (">=3.11", (3, 11)),
        ("3.13", (3, 13)),
        (">=3.10.2", (3, 10, 2)),
    ])
    def test_parse_python_requirement(self, spec, expected):
        assert parse_python_requirement(spec) == expected


# ── check_venv ────────────────────────────────────────────────────────────────

class TestCheckVenv:
    def test_returns_name_and_bool(self):
        name, active, msg = check_venv()
        assert name == "Virtualenv"
        assert isinstance(active, bool)
        assert msg in ("active", "not active")


# ── check_git ─────────────────────────────────────────────────────────────────

class TestCheckGit:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: SimpleNamespace(returncode=0, stdout="git version 2.40.0\n"),
        )
        name, ok, msg = check_git()
        assert name == "Git" and ok is True
        assert "git version" in msg

    def test_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: SimpleNamespace(returncode=1, stdout=""),
        )
        _, ok, msg = check_git()
        assert ok is False
        assert "error" in msg

    def test_git_not_installed(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError
        monkeypatch.setattr(subprocess, "run", boom)
        _, ok, msg = check_git()
        assert ok is False
        assert "not found" in msg


# ── check_feature_flags ───────────────────────────────────────────────────────

class TestCheckFeatureFlags:
    def test_success(self):
        name, ok, _ = check_feature_flags()
        assert name == "Feature flags"
        assert ok is True

    def test_failure_branch(self, monkeypatch):
        # Drop a flag attribute so getattr() inside the check raises -> except branch.
        from opensak.utils import flags
        monkeypatch.delattr(flags, "update_location", raising=False)
        _, ok, _ = check_feature_flags()
        assert ok is False


# ── run ───────────────────────────────────────────────────────────────────────

class TestRun:
    def test_real_run_prints_report(self, capsys):
        run()
        out = capsys.readouterr().out
        assert "OpenSAK Doctor" in out
        assert "Python:" in out

    def test_all_passed_branch(self, monkeypatch, capsys):
        monkeypatch.setattr(doctor, "CHECKS", [lambda: ("X", True, "ok")])
        monkeypatch.setattr(doctor, "_TAKES_PROJECT", set())
        run()
        assert "All checks passed." in capsys.readouterr().out

    def test_failure_branch_suggests_fix(self, monkeypatch, capsys):
        monkeypatch.setattr(doctor, "CHECKS", [lambda: ("X", False, "bad")])
        monkeypatch.setattr(doctor, "_TAKES_PROJECT", set())
        run()
        out = capsys.readouterr().out
        assert "Some checks failed." in out
        assert "pip install" in out

"""
tests/unit-tests/test_config.py — config path and language preference tests.
"""

import json
import os
from pathlib import Path

import pytest

import opensak.config as config_module
from opensak.config import (
    get_db_path,
    get_gc_token_path,
    get_gpx_import_dir,
    get_language,
    get_log_path,
    print_config,
    set_language,
)


@pytest.fixture(autouse=True)
def isolate_prefs(tmp_path):
    """Point the module-level prefs-file cache at a fresh temp path each test."""
    original = config_module._PREFS_FILE
    config_module._PREFS_FILE = tmp_path / "preferences.json"
    yield config_module._PREFS_FILE
    config_module._PREFS_FILE = original


# ── get_language / set_language ───────────────────────────────────────────────


class TestGetLanguage:
    def test_default_is_english_when_file_absent(self):
        assert get_language() == "en"

    def test_returns_saved_value(self, isolate_prefs):
        isolate_prefs.write_text(json.dumps({"language": "pt"}), encoding="utf-8")
        assert get_language() == "pt"

    def test_missing_language_key_falls_back_to_default(self, isolate_prefs):
        isolate_prefs.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
        assert get_language() == "en"

    def test_malformed_json_falls_back_to_default(self, isolate_prefs):
        isolate_prefs.write_text("{ not valid json }", encoding="utf-8")
        assert get_language() == "en"

    def test_empty_file_falls_back_to_default(self, isolate_prefs):
        isolate_prefs.write_text("", encoding="utf-8")
        assert get_language() == "en"


class TestSetLanguage:
    def test_write_read_roundtrip(self):
        set_language("de")
        assert get_language() == "de"

    def test_overwrites_previous_language(self):
        set_language("fr")
        set_language("es")
        assert get_language() == "es"

    def test_preserves_other_prefs_keys(self, isolate_prefs):
        isolate_prefs.write_text(json.dumps({"theme": "dark", "language": "en"}), encoding="utf-8")
        set_language("pt")
        data = json.loads(isolate_prefs.read_text(encoding="utf-8"))
        assert data["theme"] == "dark"
        assert data["language"] == "pt"

    def test_creates_file_if_absent(self, isolate_prefs):
        assert not isolate_prefs.exists()
        set_language("it")
        assert isolate_prefs.exists()

    def test_file_contains_valid_json(self, isolate_prefs):
        set_language("nl")
        data = json.loads(isolate_prefs.read_text(encoding="utf-8"))
        assert data["language"] == "nl"

    def test_corrupt_existing_file_is_replaced(self, isolate_prefs):
        isolate_prefs.write_text("{ broken", encoding="utf-8")
        set_language("pt")
        assert get_language() == "pt"


# ── get_app_data_dir (per-OS branches) ────────────────────────────────────────

class TestAppDataDir:
    def test_posix_with_xdg(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        d = config_module.get_app_data_dir()
        assert d == tmp_path / "opensak"
        assert d.exists()

    def test_posix_without_xdg(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        assert config_module.get_app_data_dir() == tmp_path / ".local" / "share" / "opensak"

    # The Windows branch is not testable here: pathlib.Path keys off os.name at
    # instantiation, so forcing os.name="nt" makes Path() raise on macOS/Linux.

    def test_other_os_uses_home(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os, "name", "java")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        assert config_module.get_app_data_dir() == tmp_path / "opensak"


# ── derived paths ─────────────────────────────────────────────────────────────

class TestDerivedPaths:
    @pytest.fixture(autouse=True)
    def _xdg(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        monkeypatch.setattr(config_module, "_PREFS_FILE", None)
        self.base = tmp_path / "opensak"

    def test_db_path(self):
        assert get_db_path() == self.base / "opensak.db"

    def test_gpx_import_dir_created(self):
        d = get_gpx_import_dir()
        assert d == self.base / "imports"
        assert d.exists()

    def test_log_path(self):
        assert get_log_path() == self.base / "opensak.log"

    def test_gc_token_path(self):
        assert get_gc_token_path() == self.base / "gc_token.json"

    def test_prefs_file_recomputed_when_none(self):
        assert config_module._get_prefs_file() == self.base / "preferences.json"

    def test_print_config(self, capsys):
        print_config()
        out = capsys.readouterr().out
        assert "App data dir" in out
        assert "Language" in out

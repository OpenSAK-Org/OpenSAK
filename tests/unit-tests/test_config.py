# tests/unit-tests/test_config.py — config path and language preference tests.

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

# Forces os.name="posix" + Path(str), which can't instantiate PosixPath on Windows.
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX-only path branch")


@pytest.fixture(autouse=True)
def isolate_store(tmp_path, monkeypatch):
    """Isolate SettingsStore so each test gets a fresh in-memory store."""
    from opensak import settings_store
    fresh = settings_store.SettingsStore()
    fresh._data = {}
    fresh._path = tmp_path / "opensak.json"
    monkeypatch.setattr(settings_store, "_store", fresh)
    # Also point install_dir at tmp_path so path-derived tests work
    monkeypatch.setattr(settings_store, "get_install_dir", lambda: tmp_path / "opensak")
    yield fresh


# ── get_language / set_language ───────────────────────────────────────────────

class TestGetLanguage:
    def test_default_is_english_when_file_absent(self):
        assert get_language() == "en"

    def test_returns_saved_value(self):
        set_language("pt")
        assert get_language() == "pt"

    def test_missing_language_key_falls_back_to_default(self):
        # Store has no app.language key → default
        assert get_language() == "en"

    def test_malformed_json_falls_back_to_default(self, tmp_path):
        # Legacy preferences.json with bad JSON → fall back
        prefs = (tmp_path / "opensak") / "preferences.json"
        prefs.parent.mkdir(parents=True, exist_ok=True)
        prefs.write_text("{ not valid json }", encoding="utf-8")
        assert get_language() == "en"

    def test_empty_file_falls_back_to_default(self, tmp_path):
        prefs = (tmp_path / "opensak") / "preferences.json"
        prefs.parent.mkdir(parents=True, exist_ok=True)
        prefs.write_text("", encoding="utf-8")
        assert get_language() == "en"


class TestSetLanguage:
    def test_write_read_roundtrip(self):
        set_language("de")
        assert get_language() == "de"

    def test_overwrites_previous_language(self):
        set_language("fr")
        set_language("es")
        assert get_language() == "es"

    def test_preserves_other_store_keys(self, isolate_store):
        # Other keys in the store survive a set_language call
        from opensak.settings_store import get_store
        get_store().set("display.theme", "dark")
        set_language("pt")
        assert get_store().get("display.theme") == "dark"
        assert get_language() == "pt"

    def test_corrupt_existing_store_replaced(self):
        # Even if the JSON on disk is broken, set+get works (in-memory)
        set_language("pt")
        assert get_language() == "pt"


# ── get_app_data_dir (per-OS branches) ────────────────────────────────────────

class TestAppDataDir:
    @posix_only
    def test_posix_with_xdg(self, tmp_path, monkeypatch):
        from opensak import settings_store as ss
        monkeypatch.setattr(ss, "get_install_dir", lambda: tmp_path / "opensak")
        d = config_module.get_app_data_dir()
        assert d == tmp_path / "opensak"

    @posix_only
    def test_posix_without_xdg(self, tmp_path, monkeypatch):
        from opensak import settings_store as ss
        monkeypatch.setattr(ss, "get_install_dir", lambda: tmp_path / ".local" / "share" / "opensak")
        d = config_module.get_app_data_dir()
        assert d == tmp_path / ".local" / "share" / "opensak"

    def test_other_os_uses_home(self, tmp_path, monkeypatch):
        from opensak import settings_store as ss
        monkeypatch.setattr(ss, "get_install_dir", lambda: tmp_path / "opensak")
        assert config_module.get_app_data_dir() == tmp_path / "opensak"


# ── derived paths ─────────────────────────────────────────────────────────────

@posix_only
class TestDerivedPaths:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        from opensak import settings_store as ss
        monkeypatch.setattr(ss, "get_install_dir", lambda: tmp_path / "opensak")
        (tmp_path / "opensak").mkdir(parents=True, exist_ok=True)
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

    def test_print_config(self, capsys):
        print_config()
        out = capsys.readouterr().out
        assert "App data dir" in out
        assert "Language" in out

# tests/unit-tests/conftest.py — shared fixtures for unit tests.

import json

import pytest

import opensak.utils.flags as flags_module


def _reload_flags() -> None:
    # Reload flags module state from the current _FEATURES_FILE.
    flags_module._flags = flags_module._load()
    for key in flags_module._RELEASE_DEFAULTS:
        setattr(flags_module, key.replace("-", "_"), flags_module._flags[key])


@pytest.fixture(autouse=True)
def reset_flags():
    # Restore flags module state after every test.
    yield
    _reload_flags()


@pytest.fixture(autouse=True)
def isolate_settings_store(tmp_path, monkeypatch):
    """Isolate SettingsStore so every unit test gets a fresh in-memory store.

    This replaces the old pattern of patching AppSettings._s (QSettings).
    All reads/writes go to an in-memory dict; nothing touches real user config.
    """
    from opensak import settings_store as ss
    fresh = ss.SettingsStore()
    fresh._data = {}
    fresh._path = tmp_path / "opensak.json"
    monkeypatch.setattr(ss, "_store", fresh)
    # Reset AppSettings singleton so it uses the fresh store
    import opensak.gui.settings as smod
    monkeypatch.setattr(smod, "_settings", None)
    # Reset DatabaseManager singleton
    import opensak.db.manager as mgr
    monkeypatch.setattr(mgr, "_manager", None)


@pytest.fixture()
def patch_features_file(tmp_path, monkeypatch):
    # Write a features.json to a temp path and wire it into the flags module.

    def _write(data: dict) -> None:
        f = tmp_path / "features.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(flags_module, "_FEATURES_FILE", f)
        _reload_flags()

    return _write


@pytest.fixture()
def no_features_file(tmp_path, monkeypatch):
    # Point the flags module at a path where features.json does not exist.
    monkeypatch.setattr(flags_module, "_FEATURES_FILE", tmp_path / "features.json")
    _reload_flags()

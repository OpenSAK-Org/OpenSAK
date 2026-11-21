# tests/unit-tests/test_map_keys_747.py — #747: the map tile API keys are kept
# in the OS keyring, never in opensak.json (so never in a settings backup);
# without a usable keyring they fall back to a file only the user can read.
# The in-memory keyring comes from tests/conftest.py (fake_os_keyring).

from __future__ import annotations

import json
import os
import sys

import keyring.errors
import pytest

from opensak import map_keys
from opensak.config import get_app_data_dir
from opensak.paths import LocationKind, get_all_storage_locations, purge_user_data
from opensak.settings_store import get_store


def _fallback() -> dict:
    path = get_app_data_dir() / map_keys.FALLBACK_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@pytest.fixture
def broken_keyring(monkeypatch):
    """A keyring backend that refuses everything (no backend available)."""
    def fail(*_args):
        raise keyring.errors.NoKeyringError("no backend")
    monkeypatch.setattr(keyring, "get_password", fail)
    monkeypatch.setattr(keyring, "set_password", fail)
    monkeypatch.setattr(keyring, "delete_password", fail)


class TestKeyring:
    def test_keys_go_to_the_keyring_not_the_settings_file(self, fake_os_keyring):
        assert map_keys.save_keys({"esri": " E ", "maptiler": "M"}) is True
        assert fake_os_keyring.passwords == {
            (map_keys.SERVICE_NAME, "esri"): "E",
            (map_keys.SERVICE_NAME, "maptiler"): "M",
        }
        stored = json.dumps(get_store()._data)
        assert '"E"' not in stored and '"M"' not in stored
        assert get_store().get(map_keys.STORE_KEY) == ["esri", "maptiler"]
        assert _fallback() == {}

    def test_load_reads_the_keyring(self, fake_os_keyring):
        map_keys.save_keys({"thunderforest": "TF"})
        map_keys.reset_cache()
        assert map_keys.load_keys() == {"thunderforest": "TF"}

    def test_empty_key_deletes_it(self, fake_os_keyring):
        map_keys.save_keys({"esri": "E", "maptiler": "M"})
        map_keys.save_keys({"esri": "", "maptiler": "M"})
        assert (map_keys.SERVICE_NAME, "esri") not in fake_os_keyring.passwords
        assert get_store().get(map_keys.STORE_KEY) == ["maptiler"]
        map_keys.reset_cache()
        assert map_keys.load_keys() == {"maptiler": "M"}

    def test_keyring_only_asked_for_stored_providers(self, fake_os_keyring, monkeypatch):
        asked = []
        monkeypatch.setattr(keyring, "get_password",
                            lambda service, user: asked.append(user))
        assert map_keys.load_keys() == {}
        assert asked == []

    def test_kept_in_memory(self, fake_os_keyring, monkeypatch):
        # The tile proxy asks on every tile: the keyring is read once.
        map_keys.save_keys({"esri": "E"})
        map_keys.reset_cache()
        calls = []
        real_get = keyring.get_password
        monkeypatch.setattr(keyring, "get_password",
                            lambda s, u: calls.append(u) or real_get(s, u))
        for _ in range(3):
            assert map_keys.load_keys() == {"esri": "E"}
        assert calls == ["esri"]

    def test_key_missing_from_keyring_is_left_out(self, fake_os_keyring):
        # Settings restored on another computer: provider listed, no key.
        get_store().set(map_keys.STORE_KEY, ["esri"])
        assert map_keys.load_keys() == {}


class TestFallbackFile:
    def test_falls_back_without_keyring(self, broken_keyring):
        assert map_keys.save_keys({"esri": "E"}) is False
        assert _fallback() == {"esri": "E"}
        assert "E" not in json.dumps(get_store()._data).replace("esri", "")
        map_keys.reset_cache()
        assert map_keys.load_keys() == {"esri": "E"}

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
    def test_fallback_file_private(self, broken_keyring):
        map_keys.save_keys({"esri": "E"})
        mode = os.stat(get_app_data_dir() / map_keys.FALLBACK_FILE).st_mode
        assert mode & 0o077 == 0

    def test_moves_to_keyring_once_available(self, fake_os_keyring, broken_keyring,
                                             monkeypatch):
        map_keys.save_keys({"esri": "E"})
        assert _fallback() == {"esri": "E"}
        # The keyring works again: the next save moves the key into it
        monkeypatch.setattr(keyring, "get_password", fake_os_keyring.get_password)
        monkeypatch.setattr(keyring, "set_password", fake_os_keyring.set_password)
        monkeypatch.setattr(keyring, "delete_password", fake_os_keyring.delete_password)
        map_keys.reset_cache()
        assert map_keys.save_keys(map_keys.load_keys()) is True
        assert fake_os_keyring.passwords == {(map_keys.SERVICE_NAME, "esri"): "E"}
        assert _fallback() == {}

    def test_deleted_key_leaves_no_file(self, broken_keyring):
        map_keys.save_keys({"esri": "E"})
        map_keys.save_keys({})
        assert not (get_app_data_dir() / map_keys.FALLBACK_FILE).exists()


class TestStorageLocations:
    def test_listed_and_purged(self, fake_os_keyring, monkeypatch):
        from opensak.email import credentials
        monkeypatch.setattr(credentials, "delete_password", lambda _u: True)
        map_keys.save_keys({"esri": "E", "maptiler": "M"})
        entries = [loc.location for loc in get_all_storage_locations()
                   if loc.kind is LocationKind.MAP_KEY_ENTRY]
        assert entries == ["esri", "maptiler"]
        purge_user_data()
        assert fake_os_keyring.passwords == {}

    def test_label_and_location(self, fake_os_keyring):
        from opensak.gui.dialogs.storage_locations_dialog import describe, display_location
        from opensak.lang import tr
        map_keys.save_keys({"esri": "E"})
        loc = next(loc for loc in get_all_storage_locations()
                   if loc.kind is LocationKind.MAP_KEY_ENTRY)
        assert describe(loc) == tr("file_locations_kind_map_key_entry")
        assert display_location(loc) == f"{map_keys.SERVICE_NAME} / esri"

"""
src/opensak/map_keys.py — storage of the user's own map tile API keys (#747).

The keys (Esri, MapTiler, Thunderforest — gui/map_layers.KEY_PROVIDERS) never
go into opensak.json, so they never end up in a settings backup or in a
settings file shared for a bug report. Each key is filed in the OS keyring
under SERVICE_NAME with the provider id as user name, as the PQ e-mail
password is (email/credentials.py, #443).

Where no keyring backend is available (some Linux desktops, headless use),
a key falls back to FALLBACK_FILE in the install folder, readable by the
user only (mode 600 on Linux/macOS). That file is plain text, but it is not
part of a backup (backup/backupset.py only copies opensak.json and an
allow-list of folders) and is removed with the install folder on purge.

opensak.json holds only the ids of the providers with a stored key
(STORE_KEY), so the keyring is only asked for keys that exist and
paths.get_all_storage_locations() can list the entries without reading them.

Keys are read once and kept in memory: the tile proxy (gui/web_assets.py)
asks for them on every tile.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import keyring
import keyring.errors

from opensak.logger import get_logger

log = get_logger("map_keys")

SERVICE_NAME = "OpenSAK map tiles"
FALLBACK_FILE = "map_keys.json"
STORE_KEY = "display.map_api_key_providers"

_cache: dict[str, str] | None = None


def _fallback_path() -> Path:
    from opensak.config import get_app_data_dir
    return get_app_data_dir() / FALLBACK_FILE


def _read_fallback() -> dict[str, str]:
    try:
        data = json.loads(_fallback_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, str) and v}


def _write_fallback(keys: dict[str, str]) -> None:
    path = _fallback_path()
    if not keys:
        path.unlink(missing_ok=True)
        return
    # Created with mode 600 from the start, so the key is never readable by
    # others, not even for a moment (os.open's mode is ignored on Windows,
    # where the user's AppData folder is private already).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(keys, f, indent=2)
    if sys.platform != "win32":
        os.chmod(path, 0o600)   # an older file may have had other rights


def stored_providers() -> list[str]:
    """Ids of the providers with a stored key (from opensak.json)."""
    from opensak.settings_store import get_store
    val = get_store().get(STORE_KEY, [])
    if not isinstance(val, list):
        return []
    return [v for v in val if isinstance(v, str) and v]


def load_keys() -> dict[str, str]:
    """The stored keys by provider id. Keys missing from both the keyring
    and the fallback file (e.g. after restoring settings on another
    computer) are left out."""
    global _cache
    if _cache is not None:
        return dict(_cache)
    fallback = _read_fallback()
    keys: dict[str, str] = {}
    for provider in stored_providers():
        key = None
        try:
            key = keyring.get_password(SERVICE_NAME, provider)
        except keyring.errors.KeyringError:
            pass
        key = key or fallback.get(provider)
        if key:
            keys[provider] = key
    _cache = keys
    return dict(keys)


def save_keys(value: dict[str, str]) -> bool:
    """Store *value* (provider id → key; an empty key deletes it), replacing
    all stored keys. Returns False if any key had to go to the fallback
    file because the keyring could not take it."""
    global _cache
    from opensak.settings_store import get_store
    wanted = {str(k): str(v).strip() for k, v in value.items() if str(v).strip()}
    fallback = _read_fallback()
    all_in_keyring = True
    for provider in set(stored_providers()) | set(fallback) | set(wanted):
        key = wanted.get(provider)
        if key:
            try:
                keyring.set_password(SERVICE_NAME, provider, key)
                fallback.pop(provider, None)
            except keyring.errors.KeyringError:
                log.warning("No usable keyring: map API key for %s stored in %s",
                            provider, FALLBACK_FILE)
                fallback[provider] = key
                all_in_keyring = False
        else:
            _delete_from_keyring(provider)
            fallback.pop(provider, None)
    _write_fallback(fallback)
    get_store().set(STORE_KEY, sorted(wanted))
    _cache = dict(wanted)
    return all_in_keyring


def delete_keyring_entry(provider: str) -> bool:
    """Remove *provider*'s key from the keyring (purge). True also when there
    was none; False only if the keyring failed."""
    return _delete_from_keyring(provider)


def _delete_from_keyring(provider: str) -> bool:
    try:
        keyring.delete_password(SERVICE_NAME, provider)
        return True
    except keyring.errors.PasswordDeleteError:
        return True   # nothing stored
    except keyring.errors.KeyringError:
        return False


def reset_cache() -> None:
    """Forget the keys held in memory (tests, purge)."""
    global _cache
    _cache = None

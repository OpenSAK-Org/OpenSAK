# tests/unit-tests/test_paths.py — samlet oversigt over OpenSAKs
# lagringssteder + purge (issue #906).
#
# Isolation: den autouse _isolated_app_paths-fixture i tests/conftest.py
# patcher Path.home(), APPDATA/XDG_* OG paths.qsettings_location() til en
# tmp-mappe pr. test, så intet her kan røre rigtige brugerdata.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from opensak import paths
from opensak.paths import LocationKind, get_all_storage_locations, purge_user_data
from opensak.settings_store import _bootstrap_path, get_install_dir, get_store, set_install_dir


def _by_kind(kind: LocationKind) -> list[paths.StorageLocation]:
    return [loc for loc in get_all_storage_locations() if loc.kind is kind]


@pytest.fixture
def fake_keyring(monkeypatch):
    """Registrér delete_password-kald i stedet for at røre den rigtige keyring."""
    from opensak.email import credentials

    deleted: list[str] = []

    def _fake_delete(username: str) -> bool:
        deleted.append(username)
        return True

    monkeypatch.setattr(credentials, "delete_password", _fake_delete)
    return deleted


# ── get_all_storage_locations() ──────────────────────────────────────────────

def test_lists_install_dir_and_bootstrap_dir():
    set_install_dir(get_install_dir())  # sørg for at bootstrap.json findes
    install_dir = get_install_dir()
    bootstrap_dir = _bootstrap_path().parent

    install = _by_kind(LocationKind.INSTALL_DIR)
    bootstrap = _by_kind(LocationKind.BOOTSTRAP_DIR)

    assert [loc.location for loc in install] == [str(install_dir)]
    assert install[0].removed_on_purge is True

    # Windows (ikke-MSIX) og macOS: samme mappe → kun vist som INSTALL_DIR.
    # Linux: ~/.config/opensak vs. ~/.local/share/opensak → begge vises.
    if bootstrap_dir == install_dir:
        assert bootstrap == []
    else:
        assert [loc.location for loc in bootstrap] == [str(bootstrap_dir)]
        assert bootstrap[0].removed_on_purge is True


def test_same_dir_is_kept_as_install_dir(monkeypatch):
    import opensak.settings_store as ss

    install_dir = get_install_dir()
    monkeypatch.setattr(ss, "_bootstrap_path", lambda: install_dir / "bootstrap.json")

    assert [loc.location for loc in _by_kind(LocationKind.INSTALL_DIR)] == [str(install_dir)]
    assert _by_kind(LocationKind.BOOTSTRAP_DIR) == []


def test_custom_database_dir_outside_install_dir_is_listed(tmp_path):
    custom = tmp_path / "my-caches"
    custom.mkdir()
    get_store().set("databases.dir", str(custom))

    assert [loc.location for loc in _by_kind(LocationKind.DATABASE_DIR)] == [str(custom)]


def test_database_dir_inside_install_dir_not_listed_separately():
    get_store().set("databases.dir", str(get_install_dir() / "databases"))

    assert _by_kind(LocationKind.DATABASE_DIR) == []


def test_listing_does_not_create_database_dir(tmp_path):
    custom = tmp_path / "not-created-yet"
    get_store().set("databases.dir", str(custom))

    get_all_storage_locations()

    assert not custom.exists()


def test_external_databases_listed_but_not_purged(tmp_path):
    external = tmp_path / "usb-drive" / "Challenges.sqlite"
    internal = get_install_dir() / "Default.sqlite"
    get_store().set("databases.list", [
        {"name": "Challenges", "path": str(external)},
        {"name": "Default", "path": str(internal)},
    ])

    ext = _by_kind(LocationKind.EXTERNAL_DATABASE)

    assert [loc.location for loc in ext] == [str(external)]
    assert ext[0].removed_on_purge is False


def test_malformed_database_entries_are_ignored():
    get_store().set("databases.list", [{"name": "no path"}, "garbage", {"path": ""}])

    assert _by_kind(LocationKind.EXTERNAL_DATABASE) == []


def test_qsettings_location_listed_as_path():
    qs = _by_kind(LocationKind.QT_SETTINGS)

    assert len(qs) == 1
    assert qs[0].is_path is True
    assert qs[0].location.endswith("OpenSAK.conf")


def test_qsettings_registry_location_is_not_a_path(monkeypatch):
    monkeypatch.setattr(
        paths, "qsettings_location",
        lambda: "\\HKEY_CURRENT_USER\\Software\\OpenSAK Project\\OpenSAK",
    )

    qs = _by_kind(LocationKind.QT_SETTINGS)

    assert qs[0].is_path is False


def test_keyring_entry_only_listed_when_username_set():
    assert _by_kind(LocationKind.KEYRING_ENTRY) == []

    get_store().set("pq_email.username", "cacher@example.com")
    entries = _by_kind(LocationKind.KEYRING_ENTRY)

    assert [(e.location, e.is_path) for e in entries] == [("cacher@example.com", False)]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="AppImage is Linux-only")
def test_appimage_artifacts_listed_only_when_present():
    from opensak import appimage

    assert _by_kind(LocationKind.APPIMAGE_DESKTOP_FILE) == []

    desktop = appimage.get_desktop_file_path()
    desktop.parent.mkdir(parents=True)
    desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

    entries = _by_kind(LocationKind.APPIMAGE_DESKTOP_FILE)
    assert [e.location for e in entries] == [str(desktop)]
    assert entries[0].removed_on_purge is False


# ── purge_user_data() ────────────────────────────────────────────────────────

def test_purge_removes_bootstrap_dir(fake_keyring):
    # Selve fejlen i #906: bootstrap.json overlevede altid en purge.
    set_install_dir(get_install_dir())
    bootstrap = _bootstrap_path()
    assert bootstrap.exists()

    purge_user_data()

    assert not bootstrap.parent.exists()


def test_purge_removes_install_dir_and_custom_db_dir(tmp_path, fake_keyring):
    install_dir = get_install_dir()
    (install_dir / "Default.sqlite").write_text("data", encoding="utf-8")
    custom = tmp_path / "my-caches"
    custom.mkdir()
    (custom / "Other.sqlite").write_text("data", encoding="utf-8")
    get_store().set("databases.dir", str(custom))

    purge_user_data()

    assert not install_dir.exists()
    assert not custom.exists()


def test_purge_removes_qsettings_file_and_empty_parent(fake_keyring):
    conf = Path(paths.qsettings_location())
    conf.parent.mkdir(parents=True)
    conf.write_text("[shortcuts]\n", encoding="utf-8")

    purge_user_data()

    assert not conf.exists()
    assert not conf.parent.exists()


def test_purge_keeps_non_empty_qsettings_parent(fake_keyring):
    conf = Path(paths.qsettings_location())
    conf.parent.mkdir(parents=True)
    conf.write_text("[shortcuts]\n", encoding="utf-8")
    other = conf.parent / "SomethingElse.conf"
    other.write_text("keep me", encoding="utf-8")

    purge_user_data()

    assert not conf.exists()
    assert other.exists()


def test_purge_deletes_keyring_entry(fake_keyring):
    get_store().set("pq_email.username", "cacher@example.com")

    purge_user_data()

    assert fake_keyring == ["cacher@example.com"]


def test_purge_skips_keyring_when_no_username(fake_keyring):
    purge_user_data()

    assert fake_keyring == []


def test_purge_never_deletes_external_databases(tmp_path, fake_keyring):
    external = tmp_path / "usb-drive" / "Challenges.sqlite"
    external.parent.mkdir()
    external.write_text("precious", encoding="utf-8")
    get_store().set("databases.list", [{"name": "Challenges", "path": str(external)}])

    purge_user_data()

    assert external.read_text(encoding="utf-8") == "precious"


def test_purge_never_deletes_home_or_its_ancestors(monkeypatch, fake_keyring):
    import opensak.settings_store as ss

    sentinel = Path.home() / "do-not-delete-me.txt"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("still here?", encoding="utf-8")

    # Fejlkonfigureret database-mappe der peger på en FORÆLDER til $HOME.
    get_store().set("databases.dir", str(Path.home().parent))
    monkeypatch.setattr(ss, "get_install_dir", lambda: Path.home())

    purge_user_data()

    assert sentinel.exists()

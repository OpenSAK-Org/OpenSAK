# tests/unit-tests/test_appimage_uninstall.py — In-app afinstaller for
# AppImage (issue #837, Step C i epic #824).
#
# POSIX-only, samme begrundelse og markør-mønster som test_appimage.py
# (#835): rører .desktop-filer, chmod og reelle stier under
# ~/.local/share — meningsløst at teste på Windows.
#
# Isolation: den autouse _isolated_app_paths-fixture i tests/conftest.py
# (#829) patcher Path.home() OG XDG_CONFIG_HOME/XDG_DATA_HOME globalt til
# en tmp-mappe pr. test, så settings_store.get_install_dir()/get_db_dir()
# automatisk peger et sikkert sted uden ekstra opsætning her.

from __future__ import annotations

import os
from pathlib import Path

import pytest

from opensak import appimage
from opensak.settings_store import get_db_dir, get_install_dir, get_store

posix_only = pytest.mark.skipif(os.name == "nt", reason="AppImage is a POSIX-only concept")
pytestmark = posix_only


@pytest.fixture(autouse=True)
def _clear_appimage_env(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("APPDIR", raising=False)


def _integrate_fake_appimage(monkeypatch, tmp_path: Path) -> Path:
    """Kør en rigtig integrate_appimage() så der er noget at afinstallere."""
    fake_source = tmp_path / "OpenSAK-v1.19.0-Linux-x86_64.AppImage"
    fake_source.write_bytes(b"fake-appimage-content")
    monkeypatch.setenv("APPIMAGE", str(fake_source))
    result = appimage.integrate_appimage()
    assert result.success
    assert result.installed_path is not None
    return result.installed_path


# ── _remove_desktop_file / _remove_icons (private helpers, direkte testet) ────

def test_remove_desktop_file_removes_existing_file():
    apps_dir = Path.home() / ".local" / "share" / "applications"
    apps_dir.mkdir(parents=True)
    desktop_path = apps_dir / "opensak.desktop"
    desktop_path.write_text("[Desktop Entry]\n", encoding="utf-8")

    appimage._remove_desktop_file()

    assert not desktop_path.exists()


def test_remove_desktop_file_noop_when_missing():
    # Må ikke kaste en exception selvom filen aldrig har eksisteret.
    appimage._remove_desktop_file()


def test_remove_icons_removes_existing_files():
    hicolor_dir = (
        Path.home() / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    )
    hicolor_dir.mkdir(parents=True)
    (hicolor_dir / "opensak.png").write_bytes(b"fake-png")

    pixmaps_dir = Path.home() / ".local" / "share" / "pixmaps"
    pixmaps_dir.mkdir(parents=True)
    (pixmaps_dir / "opensak.png").write_bytes(b"fake-png")

    appimage._remove_icons()

    assert not (hicolor_dir / "opensak.png").exists()
    assert not (pixmaps_dir / "opensak.png").exists()


def test_remove_icons_noop_when_missing():
    appimage._remove_icons()


# ── _reset_integration_flags ─────────────────────────────────────────────────

def test_reset_integration_flags_clears_all_state(monkeypatch, tmp_path):
    _integrate_fake_appimage(monkeypatch, tmp_path)
    assert appimage.is_appimage_integrated() is True
    appimage.decline_integration(remember=True)

    appimage._reset_integration_flags()

    assert appimage.is_appimage_integrated() is False
    assert appimage.get_integration_install_path() is None
    assert get_store().get("appimage.integration_declined", False) is False


# ── uninstall_appimage() ─────────────────────────────────────────────────────

def test_uninstall_program_only_removes_integration_artifacts(monkeypatch, tmp_path):
    installed_path = _integrate_fake_appimage(monkeypatch, tmp_path)
    desktop_path = Path.home() / ".local" / "share" / "applications" / "opensak.desktop"
    assert desktop_path.exists()
    assert installed_path.exists()

    result = appimage.uninstall_appimage(purge_data=False)

    assert result.success is True
    assert not desktop_path.exists()
    assert not installed_path.exists()
    assert appimage.is_appimage_integrated() is False


def test_uninstall_program_only_keeps_user_data(monkeypatch, tmp_path):
    _integrate_fake_appimage(monkeypatch, tmp_path)
    install_dir = get_install_dir()
    marker = install_dir / "my_cache_database.sqlite"
    marker.write_text("precious data", encoding="utf-8")

    result = appimage.uninstall_appimage(purge_data=False)

    assert result.success is True
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") == "precious data"


def test_uninstall_purge_removes_install_dir(monkeypatch, tmp_path):
    _integrate_fake_appimage(monkeypatch, tmp_path)
    install_dir = get_install_dir()
    marker = install_dir / "my_cache_database.sqlite"
    marker.write_text("precious data", encoding="utf-8")

    result = appimage.uninstall_appimage(purge_data=True)

    assert result.success is True
    assert not install_dir.exists()


def test_uninstall_purge_removes_separate_custom_db_dir(monkeypatch, tmp_path):
    _integrate_fake_appimage(monkeypatch, tmp_path)

    custom_db_dir = tmp_path / "my-custom-caches-folder"
    custom_db_dir.mkdir()
    (custom_db_dir / "MyDatabase.sqlite").write_text("data", encoding="utf-8")
    get_store().set("databases.dir", str(custom_db_dir))
    assert get_db_dir() == custom_db_dir

    result = appimage.uninstall_appimage(purge_data=True)

    assert result.success is True
    assert not custom_db_dir.exists()


def test_uninstall_purge_never_deletes_home_directory(monkeypatch, tmp_path):
    _integrate_fake_appimage(monkeypatch, tmp_path)
    sentinel = Path.home() / "do-not-delete-me.txt"
    sentinel.write_text("still here?", encoding="utf-8")

    # Simulér en fejlkonfigureret install_dir der (forkert) peger på selve
    # hjemmemappen — sikkerhedstjekket i _purge_user_data() skal fange det.
    import opensak.settings_store as settings_store_mod
    monkeypatch.setattr(settings_store_mod, "get_install_dir", lambda: Path.home())

    result = appimage.uninstall_appimage(purge_data=True)

    assert result.success is True
    assert Path.home().exists()
    assert sentinel.exists()


def test_uninstall_returns_failure_result_on_error(monkeypatch, tmp_path):
    _integrate_fake_appimage(monkeypatch, tmp_path)

    def _boom():
        raise OSError("permission denied")

    monkeypatch.setattr(appimage, "_remove_desktop_file", _boom)

    result = appimage.uninstall_appimage(purge_data=False)

    assert result.success is False
    assert "permission denied" in (result.error or "")

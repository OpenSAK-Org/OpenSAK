# tests/unit-tests/test_appimage.py — Linux AppImage-detektion og
# selv-integration (issue #835, Step A i epic #824).
#
# POSIX-only (samme mønster som test_msix.py/test_settings_store.py's
# `posix_only`-markør): AppImage-selv-integration er i sin natur en
# Linux/POSIX-only feature. os.chmod()'s eksekverbarhedsbits (S_IXUSR
# m.fl.) har ingen meningsfuld betegnelse på Windows — kaldet fejler ikke,
# men st_mode afspejler dem aldrig som forventet — så at lade disse tests
# køre på en Windows CI-runner giver falske fejl, ikke reel dækning.
# (Opdaget: Windows CI-kørsel af #835, 10. sep 2026 —
# test_copies_file_and_sets_executable_bit fejlede netop på dette.)
#
# Isolation: den autouse _isolated_app_paths-fixture i tests/conftest.py
# (issue #829) patcher Path.home() globalt til en tmp-mappe og nulstiller
# settings_store-singletonen før hver test, så disse tests aldrig rører
# den rigtige bruger-installation. $APPIMAGE/$APPDIR sættes eksplicit pr.
# test via monkeypatch.setenv/delenv.

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from opensak import appimage
from opensak.settings_store import get_store

posix_only = pytest.mark.skipif(os.name == "nt", reason="AppImage is a POSIX-only concept")
pytestmark = posix_only


@pytest.fixture(autouse=True)
def _clear_appimage_env(monkeypatch):
    """Sørg for at ingen test utilsigtet arver $APPIMAGE/$APPDIR fra miljøet."""
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("APPDIR", raising=False)


def _make_fake_appimage(tmp_path: Path) -> Path:
    fake = tmp_path / "OpenSAK-v1.18.0-Linux-x86_64.AppImage"
    fake.write_bytes(b"fake-appimage-content")
    return fake


class TestDetection:
    def test_not_running_as_appimage_when_env_unset(self):
        assert appimage.is_running_as_appimage() is False
        assert appimage.get_appimage_path() is None

    def test_not_running_as_appimage_when_file_missing(self, monkeypatch, tmp_path):
        # $APPIMAGE peger på en fil der ikke (længere) findes — fx afmonteret.
        monkeypatch.setenv("APPIMAGE", str(tmp_path / "gone.AppImage"))
        assert appimage.is_running_as_appimage() is False
        assert appimage.get_appimage_path() is None

    def test_running_as_appimage_when_file_exists(self, monkeypatch, tmp_path):
        fake = _make_fake_appimage(tmp_path)
        monkeypatch.setenv("APPIMAGE", str(fake))
        assert appimage.is_running_as_appimage() is True
        assert appimage.get_appimage_path() == fake

    def test_get_appdir_path_none_when_unset(self):
        assert appimage.get_appdir_path() is None

    def test_get_appdir_path_none_when_not_a_directory(self, monkeypatch, tmp_path):
        monkeypatch.setenv("APPDIR", str(tmp_path / "not-a-dir"))
        assert appimage.get_appdir_path() is None

    def test_get_appdir_path_when_valid(self, monkeypatch, tmp_path):
        appdir = tmp_path / "squashfs-root"
        appdir.mkdir()
        monkeypatch.setenv("APPDIR", str(appdir))
        assert appimage.get_appdir_path() == appdir

    def test_get_integrated_appimage_path_under_fake_home(self, tmp_path):
        # fake_home sættes af den autouse #829-isolationsfixturen i
        # tests/conftest.py (Path.home() er patchet globalt).
        result = appimage.get_integrated_appimage_path()
        assert result == Path.home() / ".local" / "bin" / "OpenSAK.AppImage"


class TestIntegrationStatus:
    def test_not_integrated_by_default(self):
        assert appimage.is_appimage_integrated() is False
        assert appimage.get_integration_install_path() is None

    def test_is_appimage_integrated_reflects_store(self):
        get_store().set("appimage.integrated", True)
        assert appimage.is_appimage_integrated() is True

    def test_get_integration_install_path_reflects_store(self, tmp_path):
        fake_path = tmp_path / "OpenSAK.AppImage"
        get_store().set("appimage.install_path", str(fake_path))
        assert appimage.get_integration_install_path() == fake_path

    def test_decline_integration_without_remember_sets_nothing(self):
        appimage.decline_integration(remember=False)
        assert get_store().get("appimage.integration_declined", False) is False

    def test_decline_integration_with_remember_sets_flag(self):
        appimage.decline_integration(remember=True)
        assert get_store().get("appimage.integration_declined", False) is True


class TestAppImageLauncherAdoption:
    def test_no_desktop_dir_returns_false(self):
        assert appimage._has_appimagelauncher_desktop_entry() is False

    def test_desktop_file_without_identifier_ignored(self):
        apps_dir = Path.home() / ".local" / "share" / "applications"
        apps_dir.mkdir(parents=True)
        (apps_dir / "somethingelse.desktop").write_text(
            "[Desktop Entry]\nName=OpenSAK\nExec=opensak\n", encoding="utf-8"
        )
        assert appimage._has_appimagelauncher_desktop_entry() is False

    def test_desktop_file_with_identifier_but_not_opensak_ignored(self):
        apps_dir = Path.home() / ".local" / "share" / "applications"
        apps_dir.mkdir(parents=True)
        (apps_dir / "other.desktop").write_text(
            "[Desktop Entry]\nName=SomeOtherApp\nX-AppImage-Identifier=abc123\n",
            encoding="utf-8",
        )
        assert appimage._has_appimagelauncher_desktop_entry() is False

    def test_desktop_file_with_identifier_and_opensak_detected(self):
        apps_dir = Path.home() / ".local" / "share" / "applications"
        apps_dir.mkdir(parents=True)
        (apps_dir / "opensak.desktop").write_text(
            "[Desktop Entry]\nName=OpenSAK\nX-AppImage-Identifier=abc123\n",
            encoding="utf-8",
        )
        assert appimage._has_appimagelauncher_desktop_entry() is True


class TestShouldPromptForIntegration:
    def test_false_when_not_running_as_appimage(self):
        assert appimage.should_prompt_for_integration() is False

    def test_false_when_already_integrated(self, monkeypatch, tmp_path):
        fake = _make_fake_appimage(tmp_path)
        monkeypatch.setenv("APPIMAGE", str(fake))
        get_store().set("appimage.integrated", True)
        assert appimage.should_prompt_for_integration() is False

    def test_false_and_silently_adopts_when_appimagelauncher_present(
        self, monkeypatch, tmp_path
    ):
        fake = _make_fake_appimage(tmp_path)
        monkeypatch.setenv("APPIMAGE", str(fake))
        apps_dir = Path.home() / ".local" / "share" / "applications"
        apps_dir.mkdir(parents=True)
        (apps_dir / "opensak.desktop").write_text(
            "[Desktop Entry]\nName=OpenSAK\nX-AppImage-Identifier=abc123\n",
            encoding="utf-8",
        )
        assert appimage.should_prompt_for_integration() is False
        # Skal have sat flaget stille, uden brugerinteraktion.
        assert appimage.is_appimage_integrated() is True

    def test_false_when_declined_permanently(self, monkeypatch, tmp_path):
        fake = _make_fake_appimage(tmp_path)
        monkeypatch.setenv("APPIMAGE", str(fake))
        appimage.decline_integration(remember=True)
        assert appimage.should_prompt_for_integration() is False

    def test_true_on_fresh_appimage_run(self, monkeypatch, tmp_path):
        fake = _make_fake_appimage(tmp_path)
        monkeypatch.setenv("APPIMAGE", str(fake))
        assert appimage.should_prompt_for_integration() is True


class TestIntegrateAppimage:
    def test_fails_when_not_running_as_appimage(self):
        result = appimage.integrate_appimage()
        assert result.success is False
        assert result.error == "not_running_as_appimage"

    def test_copies_file_and_sets_executable_bit(self, monkeypatch, tmp_path):
        fake = _make_fake_appimage(tmp_path)
        monkeypatch.setenv("APPIMAGE", str(fake))

        result = appimage.integrate_appimage()

        assert result.success is True
        target = appimage.get_integrated_appimage_path()
        assert result.installed_path == target
        assert target.exists()
        assert target.read_bytes() == b"fake-appimage-content"
        assert target.stat().st_mode & stat.S_IXUSR

    def test_creates_desktop_file_pointing_at_target(self, monkeypatch, tmp_path):
        fake = _make_fake_appimage(tmp_path)
        monkeypatch.setenv("APPIMAGE", str(fake))

        result = appimage.integrate_appimage()

        desktop_path = Path.home() / ".local" / "share" / "applications" / "opensak.desktop"
        assert desktop_path.exists()
        content = desktop_path.read_text(encoding="utf-8")
        assert "[Desktop Entry]" in content
        assert str(result.installed_path) in content
        assert "Icon=opensak" in content

    def test_persists_status_and_path_in_store(self, monkeypatch, tmp_path):
        fake = _make_fake_appimage(tmp_path)
        monkeypatch.setenv("APPIMAGE", str(fake))

        appimage.integrate_appimage()

        assert appimage.is_appimage_integrated() is True
        assert appimage.get_integration_install_path() == appimage.get_integrated_appimage_path()

    def test_installs_bundled_icon_when_appdir_present(self, monkeypatch, tmp_path):
        fake = _make_fake_appimage(tmp_path)
        monkeypatch.setenv("APPIMAGE", str(fake))

        appdir = tmp_path / "squashfs-root"
        icon_dir = appdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
        icon_dir.mkdir(parents=True)
        (icon_dir / "opensak.png").write_bytes(b"fake-png-bytes")
        monkeypatch.setenv("APPDIR", str(appdir))

        # Ikon-cache-kommandoer findes sandsynligvis ikke i test-miljøet —
        # skal degradere gracefully (best-effort), ikke fejle integrationen.
        result = appimage.integrate_appimage()
        assert result.success is True

        hicolor_target = (
            Path.home() / ".local" / "share" / "icons" / "hicolor"
            / "256x256" / "apps" / "opensak.png"
        )
        pixmap_target = Path.home() / ".local" / "share" / "pixmaps" / "opensak.png"
        assert hicolor_target.read_bytes() == b"fake-png-bytes"
        assert pixmap_target.read_bytes() == b"fake-png-bytes"

    def test_skips_icon_gracefully_when_no_appdir(self, monkeypatch, tmp_path):
        fake = _make_fake_appimage(tmp_path)
        monkeypatch.setenv("APPIMAGE", str(fake))
        # $APPDIR bevidst ikke sat — skal stadig lykkes uden ikon.
        result = appimage.integrate_appimage()
        assert result.success is True

    def test_returns_failure_result_on_copy_error(self, monkeypatch, tmp_path):
        fake = _make_fake_appimage(tmp_path)
        monkeypatch.setenv("APPIMAGE", str(fake))

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(appimage.shutil, "copy2", _boom)

        result = appimage.integrate_appimage()

        assert result.success is False
        assert "disk full" in (result.error or "")
        # Fejlet integration må ikke sætte "integreret"-flaget.
        assert appimage.is_appimage_integrated() is False

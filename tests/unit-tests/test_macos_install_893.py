# tests/unit-tests/test_macos_install_893.py — issue #893.
#
# The macOS install helpers in updater.py shell out to hdiutil and ditto,
# which don't exist on the Linux CI runners (and must never really run from
# a test anyway). updater.subprocess.run is replaced by a fake that records
# every call; ditto is emulated with shutil.copytree so the bundle swap
# logic in _replace_app_bundle() runs against real directories in tmp_path.

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import opensak.updater as updater
from opensak.updater import (
    MacInstallError,
    _find_app_in_volume,
    _hdiutil_attach,
    _hdiutil_detach,
    _move_to_downloads,
    _replace_app_bundle,
    _running_app_bundle,
    install_macos_update,
    macos_install_target,
)


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_bundle(path: Path, marker: str) -> Path:
    """A minimal fake .app bundle whose content identifies its version."""
    (path / "Contents" / "MacOS").mkdir(parents=True)
    (path / "Contents" / "MacOS" / "OpenSAK").write_text(marker)
    return path


def _marker(bundle: Path) -> str:
    return (bundle / "Contents" / "MacOS" / "OpenSAK").read_text()


class _FakeRun:
    """Stands in for subprocess.run. Emulates ditto with copytree unless
    told to fail; hdiutil output/failures are scripted per test."""

    def __init__(self, attach_stdout: bytes = b"", fail: set[str] | None = None):
        self.calls: list[list[str]] = []
        self.attach_stdout = attach_stdout
        self.fail = fail or set()

    def __call__(self, args, **_kw):
        self.calls.append(list(args))
        key = " ".join(args[:2]) if args[0] == "hdiutil" else args[0]
        if "-force" in args:
            key += " -force"
        if key in self.fail:
            raise subprocess.CalledProcessError(1, args)
        if args[0] == "ditto":
            shutil.copytree(args[1], args[2], symlinks=True)
        if args[:2] == ["hdiutil", "attach"]:
            return SimpleNamespace(stdout=self.attach_stdout, returncode=0)
        return SimpleNamespace(stdout=b"", returncode=0)


def _symlink_or_skip(link: Path, target: str) -> None:
    """Windows CI runners may lack the privilege to create symlinks."""
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this runner")


def _attach_plist(mount_point: str | None) -> bytes:
    entities = [{"content-hint": "GUID_partition_scheme", "dev-entry": "/dev/disk4"}]
    if mount_point is not None:
        entities.append({"dev-entry": "/dev/disk4s1", "mount-point": mount_point})
    return plistlib.dumps({"system-entities": entities})


# ── Where to install ──────────────────────────────────────────────────────

class TestInstallTarget:
    def test_not_frozen_has_no_running_bundle(self, monkeypatch):
        monkeypatch.delattr(updater.sys, "frozen", raising=False)
        assert _running_app_bundle() is None

    def test_frozen_bundle_found_from_executable(self, monkeypatch, tmp_path):
        bundle = _make_bundle(tmp_path / "Apps" / "OpenSAK.app", "old")
        monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
        monkeypatch.setattr(updater.sys, "executable",
                            str(bundle / "Contents" / "MacOS" / "OpenSAK"))
        assert _running_app_bundle() == bundle.resolve()

    def test_installs_in_place_where_the_app_runs(self, monkeypatch, tmp_path):
        bundle = tmp_path / "Users" / "me" / "Applications" / "OpenSAK.app"
        monkeypatch.setattr(updater, "_running_app_bundle", lambda: bundle)
        assert macos_install_target() == bundle

    @pytest.mark.parametrize("running_from", [
        None,                                                    # from source
        Path("/Volumes/OpenSAK/OpenSAK.app"),                    # straight from the DMG
        Path("/private/var/folders/x/AppTranslocation/ABC/d/OpenSAK.app"),
    ])
    def test_falls_back_to_applications(self, monkeypatch, running_from):
        monkeypatch.setattr(updater, "_running_app_bundle", lambda: running_from)
        assert macos_install_target() == Path("/Applications/OpenSAK.app")


# ── hdiutil ───────────────────────────────────────────────────────────────

class TestHdiutil:
    def test_attach_is_invisible_and_returns_mount_point(self, monkeypatch):
        fake = _FakeRun(attach_stdout=_attach_plist("/Volumes/OpenSAK 1"))
        monkeypatch.setattr(updater.subprocess, "run", fake)
        assert _hdiutil_attach(Path("/tmp/x.dmg")) == Path("/Volumes/OpenSAK 1")
        args = fake.calls[0]
        assert args[:2] == ["hdiutil", "attach"]
        assert "-nobrowse" in args and "-plist" in args

    def test_attach_tolerates_text_before_the_plist(self, monkeypatch):
        noisy = b"Checksumming whole disk...\n" + _attach_plist("/Volumes/OpenSAK")
        monkeypatch.setattr(updater.subprocess, "run", _FakeRun(attach_stdout=noisy))
        assert _hdiutil_attach(Path("/tmp/x.dmg")) == Path("/Volumes/OpenSAK")

    def test_attach_without_mount_point_raises(self, monkeypatch):
        monkeypatch.setattr(updater.subprocess, "run",
                            _FakeRun(attach_stdout=_attach_plist(None)))
        with pytest.raises(MacInstallError):
            _hdiutil_attach(Path("/tmp/x.dmg"))

    def test_attach_with_unreadable_output_raises(self, monkeypatch):
        monkeypatch.setattr(updater.subprocess, "run",
                            _FakeRun(attach_stdout=b"<?xml garbage"))
        with pytest.raises(MacInstallError):
            _hdiutil_attach(Path("/tmp/x.dmg"))

    def test_detach_retries_with_force(self, monkeypatch):
        fake = _FakeRun(fail={"hdiutil detach"})
        monkeypatch.setattr(updater.subprocess, "run", fake)
        _hdiutil_detach(Path("/Volumes/OpenSAK"))
        mp = str(Path("/Volumes/OpenSAK"))
        assert fake.calls == [
            ["hdiutil", "detach", mp],
            ["hdiutil", "detach", "-force", mp],
        ]

    def test_detach_never_raises(self, monkeypatch):
        fake = _FakeRun(fail={"hdiutil detach", "hdiutil detach -force"})
        monkeypatch.setattr(updater.subprocess, "run", fake)
        _hdiutil_detach(Path("/Volumes/OpenSAK"))  # must not raise
        assert len(fake.calls) == 2


# ── Finding the app on the mounted volume ────────────────────────────────

class TestFindApp:
    def test_prefers_opensak_app_and_ignores_applications_link(self, tmp_path):
        _make_bundle(tmp_path / "Another.app", "x")
        app = _make_bundle(tmp_path / "OpenSAK.app", "new")
        _symlink_or_skip(tmp_path / "Applications", "/Applications")
        assert _find_app_in_volume(tmp_path) == app

    def test_falls_back_to_any_app_bundle(self, tmp_path):
        app = _make_bundle(tmp_path / "OpenSAK Beta.app", "new")
        assert _find_app_in_volume(tmp_path) == app

    def test_no_app_raises(self, tmp_path):
        _symlink_or_skip(tmp_path / "Applications", "/Applications")
        with pytest.raises(MacInstallError):
            _find_app_in_volume(tmp_path)


# ── Swapping the bundle safely ────────────────────────────────────────────

class TestReplaceAppBundle:
    def test_replaces_existing_bundle_and_leaves_no_leftovers(self, monkeypatch, tmp_path):
        monkeypatch.setattr(updater.subprocess, "run", _FakeRun())
        src = _make_bundle(tmp_path / "vol" / "OpenSAK.app", "new")
        target = _make_bundle(tmp_path / "Applications" / "OpenSAK.app", "old")

        _replace_app_bundle(src, target)

        assert _marker(target) == "new"
        assert sorted(p.name for p in target.parent.iterdir()) == ["OpenSAK.app"]

    def test_first_install_without_existing_bundle(self, monkeypatch, tmp_path):
        monkeypatch.setattr(updater.subprocess, "run", _FakeRun())
        src = _make_bundle(tmp_path / "vol" / "OpenSAK.app", "new")
        (tmp_path / "Applications").mkdir()
        target = tmp_path / "Applications" / "OpenSAK.app"

        _replace_app_bundle(src, target)
        assert _marker(target) == "new"

    def test_stale_leftovers_from_a_crashed_attempt_are_cleared(self, monkeypatch, tmp_path):
        monkeypatch.setattr(updater.subprocess, "run", _FakeRun())
        src = _make_bundle(tmp_path / "vol" / "OpenSAK.app", "new")
        target = _make_bundle(tmp_path / "Applications" / "OpenSAK.app", "old")
        _make_bundle(tmp_path / "Applications" / ".OpenSAK.app.new", "stale")
        _make_bundle(tmp_path / "Applications" / ".OpenSAK.app.old", "stale")

        _replace_app_bundle(src, target)
        assert _marker(target) == "new"
        assert sorted(p.name for p in target.parent.iterdir()) == ["OpenSAK.app"]

    def test_ditto_failure_leaves_the_old_app_untouched(self, monkeypatch, tmp_path):
        monkeypatch.setattr(updater.subprocess, "run", _FakeRun(fail={"ditto"}))
        src = _make_bundle(tmp_path / "vol" / "OpenSAK.app", "new")
        target = _make_bundle(tmp_path / "Applications" / "OpenSAK.app", "old")

        with pytest.raises(subprocess.CalledProcessError):
            _replace_app_bundle(src, target)
        assert _marker(target) == "old"
        assert sorted(p.name for p in target.parent.iterdir()) == ["OpenSAK.app"]

    def test_failed_swap_rolls_back_to_the_old_app(self, monkeypatch, tmp_path):
        monkeypatch.setattr(updater.subprocess, "run", _FakeRun())
        src = _make_bundle(tmp_path / "vol" / "OpenSAK.app", "new")
        target = _make_bundle(tmp_path / "Applications" / "OpenSAK.app", "old")
        real_rename = os.rename

        def _rename(a, b):
            if str(a).endswith(".OpenSAK.app.new"):
                raise OSError("simulated rename failure")
            return real_rename(a, b)
        monkeypatch.setattr(updater.os, "rename", _rename)

        with pytest.raises(OSError, match="simulated"):
            _replace_app_bundle(src, target)
        assert _marker(target) == "old"
        assert sorted(p.name for p in target.parent.iterdir()) == ["OpenSAK.app"]

    def test_unwritable_folder_raises_before_copying(self, monkeypatch, tmp_path):
        fake = _FakeRun()
        monkeypatch.setattr(updater.subprocess, "run", fake)
        monkeypatch.setattr(updater.os, "access", lambda *_a: False)
        src = _make_bundle(tmp_path / "vol" / "OpenSAK.app", "new")
        target = _make_bundle(tmp_path / "Applications" / "OpenSAK.app", "old")

        with pytest.raises(PermissionError):
            _replace_app_bundle(src, target)
        assert fake.calls == []
        assert _marker(target) == "old"


# ── The whole install ─────────────────────────────────────────────────────

class TestInstallMacosUpdate:
    def test_mounts_copies_and_always_detaches(self, monkeypatch, tmp_path):
        volume = tmp_path / "Volumes" / "OpenSAK"
        _make_bundle(volume / "OpenSAK.app", "new")
        fake = _FakeRun(attach_stdout=_attach_plist(str(volume)))
        monkeypatch.setattr(updater.subprocess, "run", fake)
        target = _make_bundle(tmp_path / "Applications" / "OpenSAK.app", "old")

        assert install_macos_update(tmp_path / "x.dmg", target) == target
        assert _marker(target) == "new"
        assert fake.calls[-1] == ["hdiutil", "detach", str(volume)]

    def test_detaches_even_when_the_volume_has_no_app(self, monkeypatch, tmp_path):
        volume = tmp_path / "Volumes" / "OpenSAK"
        volume.mkdir(parents=True)
        fake = _FakeRun(attach_stdout=_attach_plist(str(volume)))
        monkeypatch.setattr(updater.subprocess, "run", fake)

        with pytest.raises(MacInstallError):
            install_macos_update(tmp_path / "x.dmg", tmp_path / "OpenSAK.app")
        assert fake.calls[-1] == ["hdiutil", "detach", str(volume)]


# ── Fallback: keep the DMG where the user can find it ─────────────────────

class TestMoveToDownloads:
    def test_moves_into_downloads(self, monkeypatch, tmp_path):
        downloads = tmp_path / "Downloads"
        monkeypatch.setattr(updater, "_macos_downloads_dir", lambda: downloads)
        dmg = tmp_path / "tmp" / "OpenSAK-v1.20.0-macOS-arm64.dmg"
        dmg.parent.mkdir()
        dmg.write_bytes(b"dmg")

        dest = _move_to_downloads(dmg)
        assert dest == downloads / "OpenSAK-v1.20.0-macOS-arm64.dmg"
        assert dest.read_bytes() == b"dmg"
        assert not dmg.exists()

    def test_does_not_overwrite_an_existing_file(self, monkeypatch, tmp_path):
        downloads = tmp_path / "Downloads"
        downloads.mkdir()
        monkeypatch.setattr(updater, "_macos_downloads_dir", lambda: downloads)
        (downloads / "OpenSAK.dmg").write_bytes(b"older")
        (downloads / "OpenSAK (1).dmg").write_bytes(b"older too")
        dmg = tmp_path / "OpenSAK.dmg"
        dmg.write_bytes(b"new")

        dest = _move_to_downloads(dmg)
        assert dest == downloads / "OpenSAK (2).dmg"
        assert (downloads / "OpenSAK.dmg").read_bytes() == b"older"

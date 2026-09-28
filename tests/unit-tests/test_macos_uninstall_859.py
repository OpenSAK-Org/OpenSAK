# tests/unit-tests/test_macos_uninstall_859.py — In-app afinstallation på
# macOS (issue #859, Step 4 i epic #824).
#
# Platform: logikken er macOS-specifik, men testene kører på alle platforme
# ved at pinne sys.platform/sys.frozen/sys.executable eksplicit — samme
# lære som #918: en test må aldrig afhænge af, hvilken maskine den kører på.
# Selve flytningen til papirkurven (_qt_move_to_trash) erstattes altid, så
# ingen test rører udviklerens rigtige papirkurv.

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from opensak import macos_uninstall
from opensak.macos_uninstall import UninstallBlocker

posix_only = pytest.mark.skipif(os.name == "nt", reason="chmod/symlink semantics are POSIX")


def _make_bundle(parent: Path, name: str = "OpenSAK.app") -> Path:
    """Opret en minimal .app-struktur med en eksekverbar fil indeni."""
    exe = parent / name / "Contents" / "MacOS" / "OpenSAK"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"fake-binary")
    return parent / name


def _pin_frozen_mac(monkeypatch, bundle: Path) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(bundle / "Contents" / "MacOS" / "OpenSAK"))


@pytest.fixture
def fake_trash(monkeypatch, tmp_path):
    """Erstat den rigtige papirkurv med en mappe under tmp_path."""
    trash = tmp_path / "Trash"
    trash.mkdir()
    calls: list[Path] = []

    def _move(path: Path) -> bool:
        calls.append(path)
        shutil.move(str(path), str(trash / path.name))
        return True

    monkeypatch.setattr(macos_uninstall, "_qt_move_to_trash", _move)
    return trash, calls


# ── running_bundle / is_supported ─────────────────────────────────────────────

def test_running_bundle_none_on_other_platforms(monkeypatch, tmp_path):
    bundle = _make_bundle(tmp_path)
    _pin_frozen_mac(monkeypatch, bundle)
    monkeypatch.setattr(sys, "platform", "linux")
    assert macos_uninstall.running_bundle() is None
    assert macos_uninstall.is_supported() is False


def test_running_bundle_none_when_running_from_source(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert macos_uninstall.running_bundle() is None
    assert macos_uninstall.is_supported() is False


def test_running_bundle_found_for_frozen_mac_app(monkeypatch, tmp_path):
    bundle = _make_bundle(tmp_path)
    _pin_frozen_mac(monkeypatch, bundle)
    assert macos_uninstall.running_bundle() == bundle.resolve()
    assert macos_uninstall.is_supported() is True


# ── check_can_uninstall ───────────────────────────────────────────────────────

def test_check_ok_for_writable_location(tmp_path):
    bundle = _make_bundle(tmp_path)
    assert macos_uninstall.check_can_uninstall(bundle) is None


@pytest.mark.parametrize("path", [
    "/Volumes/OpenSAK/OpenSAK.app",
    "/private/var/folders/xy/T/AppTranslocation/ABC-123/d/OpenSAK.app",
])
def test_check_blocks_transient_locations(path):
    assert macos_uninstall.check_can_uninstall(Path(path)) is UninstallBlocker.TRANSIENT_LOCATION


def test_check_blocks_when_folder_not_writable(monkeypatch, tmp_path):
    bundle = _make_bundle(tmp_path)
    monkeypatch.setattr(macos_uninstall.os, "access", lambda p, mode: False)
    assert macos_uninstall.check_can_uninstall(bundle) is UninstallBlocker.NOT_WRITABLE


# ── _is_safe_bundle_path ──────────────────────────────────────────────────────

def test_safe_path_accepts_real_bundle(tmp_path):
    assert macos_uninstall._is_safe_bundle_path(_make_bundle(tmp_path)) is True


def test_safe_path_rejects_non_app_directory(tmp_path):
    folder = tmp_path / "Applications"
    folder.mkdir()
    assert macos_uninstall._is_safe_bundle_path(folder) is False


def test_safe_path_rejects_missing_bundle(tmp_path):
    assert macos_uninstall._is_safe_bundle_path(tmp_path / "OpenSAK.app") is False


def test_safe_path_rejects_file_named_app(tmp_path):
    f = tmp_path / "OpenSAK.app"
    f.write_text("not a bundle", encoding="utf-8")
    assert macos_uninstall._is_safe_bundle_path(f) is False


def test_safe_path_rejects_home_even_if_named_app(monkeypatch, tmp_path):
    home = tmp_path / "Weird.app"
    home.mkdir()
    monkeypatch.setattr(macos_uninstall.Path, "home", lambda: home)
    assert macos_uninstall._is_safe_bundle_path(home) is False


@posix_only
def test_safe_path_rejects_symlinked_bundle(tmp_path):
    real = _make_bundle(tmp_path / "real")
    link = tmp_path / "OpenSAK.app"
    link.symlink_to(real, target_is_directory=True)
    assert macos_uninstall._is_safe_bundle_path(link) is False


# ── move_bundle_to_trash ──────────────────────────────────────────────────────

def test_move_to_trash_moves_bundle(tmp_path, fake_trash):
    trash, calls = fake_trash
    bundle = _make_bundle(tmp_path / "Applications")

    macos_uninstall.move_bundle_to_trash(bundle)

    assert not bundle.exists()
    assert (trash / "OpenSAK.app" / "Contents" / "MacOS" / "OpenSAK").exists()
    assert calls == [bundle]


def test_move_to_trash_raises_and_keeps_bundle_when_qt_fails(monkeypatch, tmp_path):
    bundle = _make_bundle(tmp_path)
    monkeypatch.setattr(macos_uninstall, "_qt_move_to_trash", lambda p: False)

    with pytest.raises(OSError):
        macos_uninstall.move_bundle_to_trash(bundle)
    assert bundle.exists()


def test_move_to_trash_raises_if_bundle_still_exists(monkeypatch, tmp_path):
    # Qt påstår succes, men bundlen ligger der stadig — stol på disken.
    bundle = _make_bundle(tmp_path)
    monkeypatch.setattr(macos_uninstall, "_qt_move_to_trash", lambda p: True)

    with pytest.raises(OSError):
        macos_uninstall.move_bundle_to_trash(bundle)


def test_move_to_trash_refuses_unsafe_path_without_touching_it(tmp_path, fake_trash):
    _, calls = fake_trash
    folder = tmp_path / "Applications"
    folder.mkdir()

    with pytest.raises(OSError):
        macos_uninstall.move_bundle_to_trash(folder)
    assert folder.exists()
    assert calls == []


@pytest.mark.parametrize("qt_result, expected", [
    (True, True), (False, False), ((True, "/Users/x/.Trash/OpenSAK.app"), True), ((False, ""), False),
])
def test_qt_move_to_trash_handles_bool_and_tuple(monkeypatch, tmp_path, qt_result, expected):
    # PySide6's stub siger (bool, str), men runtime har returneret en ren bool.
    from PySide6.QtCore import QFile

    monkeypatch.setattr(QFile, "moveToTrash", staticmethod(lambda _p: qt_result))
    assert macos_uninstall._qt_move_to_trash(tmp_path / "OpenSAK.app") is expected


# ── settings_store.disable_writes / purge (regression, gælder også Linux #837) ─

def test_disable_writes_blocks_flush(tmp_path):
    from opensak.settings_store import SettingsStore

    store = SettingsStore()
    store._path = tmp_path / "sub" / "opensak.json"
    store.set("a", 1)
    assert store._path.exists()

    store._path.unlink()
    store._path.parent.rmdir()
    store.disable_writes()
    store.set("window.geometry", "abc")
    store.sync()

    assert not store._path.parent.exists()
    assert store.get("window.geometry") == "abc"  # læsning virker stadig


def test_purge_is_not_undone_by_closing_the_main_window():
    # Bug fundet under #859: efter "slet alle data" lukker appen, og
    # hovedvinduets closeEvent gemmer vinduesgeometri via settings_store.
    # Det genskabte installations-mappen med en opensak.json fuld af de
    # gamle indstillinger (inkl. database-liste og PQ Email-brugernavn).
    from opensak.paths import purge_user_data
    from opensak.settings_store import get_install_dir, get_store

    store = get_store()
    store._path = None  # brug den rigtige sti: <install_dir>/opensak.json
    store.set("pq_email.host", "imap.example.com")
    install_dir = get_install_dir()
    assert (install_dir / "opensak.json").exists()

    purge_user_data()
    assert not install_dir.exists()

    store.set("window.geometry", "abc")  # det closeEvent gør
    store.sync()

    assert not install_dir.exists()


# ── confirm_and_uninstall (dialog-flowet, uden rigtige Qt-dialoger) ───────────

class _FakeMessageBox:
    """Registrerer information/warning-kald i stedet for at vise dialoger."""

    def __init__(self, log: list[str]):
        self._log = log

    def information(self, *args, **kwargs):
        self._log.append("information")

    def warning(self, *args, **kwargs):
        self._log.append("warning")


@pytest.fixture
def dialog_env(monkeypatch, tmp_path):
    """Kørende frossen .app i en skrivbar mappe + registrering af alle skridt."""
    from opensak.gui.dialogs import macos_uninstall_dialog as dlg
    import opensak.paths as paths_mod

    bundle = _make_bundle(tmp_path / "Applications")
    _pin_frozen_mac(monkeypatch, bundle)

    steps: list[str] = []
    monkeypatch.setattr(dlg, "QMessageBox", _FakeMessageBox(steps))
    monkeypatch.setattr(paths_mod, "purge_user_data", lambda: steps.append("purge"))

    def _trash(path: Path) -> None:
        steps.append("trash")

    monkeypatch.setattr(macos_uninstall, "move_bundle_to_trash", _trash)

    def set_choice(choice):
        monkeypatch.setattr(dlg, "ask_uninstall_choice", lambda parent: steps.append("ask") or choice)

    return dlg, steps, set_choice


def test_dialog_noop_when_not_supported(monkeypatch, dialog_env):
    dlg, steps, set_choice = dialog_env
    set_choice(True)
    monkeypatch.setattr(sys, "platform", "linux")

    assert dlg.confirm_and_uninstall(None) is False
    assert steps == []


def test_dialog_blocked_location_never_asks_or_deletes(monkeypatch, dialog_env):
    dlg, steps, set_choice = dialog_env
    set_choice(True)
    monkeypatch.setattr(macos_uninstall, "check_can_uninstall",
                        lambda b: UninstallBlocker.NOT_WRITABLE)

    assert dlg.confirm_and_uninstall(None) is False
    assert steps == ["information"]


def test_dialog_cancel_changes_nothing(dialog_env):
    dlg, steps, set_choice = dialog_env
    set_choice(None)

    assert dlg.confirm_and_uninstall(None) is False
    assert steps == ["ask"]


def test_dialog_program_only_keeps_data(dialog_env):
    dlg, steps, set_choice = dialog_env
    set_choice(False)

    assert dlg.confirm_and_uninstall(None) is True
    assert steps == ["ask", "information", "trash"]


def test_dialog_purge_deletes_data_before_moving_bundle(dialog_env):
    # Rækkefølgen er afgørende: data slettes mens bundlen er intakt, og
    # flytningen er det allersidste — ingen UI efter den.
    dlg, steps, set_choice = dialog_env
    set_choice(True)

    assert dlg.confirm_and_uninstall(None) is True
    assert steps == ["ask", "purge", "information", "trash"]


def test_dialog_trash_failure_program_only_keeps_app_running(monkeypatch, dialog_env):
    dlg, steps, set_choice = dialog_env
    set_choice(False)

    def _fail(path):
        steps.append("trash")
        raise OSError("nope")

    monkeypatch.setattr(macos_uninstall, "move_bundle_to_trash", _fail)

    assert dlg.confirm_and_uninstall(None) is False
    assert steps == ["ask", "information", "trash", "warning"]


def test_dialog_trash_failure_after_purge_still_closes_app(monkeypatch, dialog_env):
    # Data er væk — appen har intet at arbejde med og skal lukke alligevel.
    dlg, steps, set_choice = dialog_env
    set_choice(True)

    def _fail(path):
        steps.append("trash")
        raise OSError("nope")

    monkeypatch.setattr(macos_uninstall, "move_bundle_to_trash", _fail)

    assert dlg.confirm_and_uninstall(None) is True
    assert steps == ["ask", "purge", "information", "trash", "warning"]

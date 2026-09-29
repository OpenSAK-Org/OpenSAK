"""tests/unit-tests/test_app_control_904.py — issue #904.

On Windows 11 with Smart App Control on, the direct download failed at startup
with a blocked, unsigned SQLAlchemy DLL, reported only as "could not open your
database". opensak.app_control recognises that situation so startup can point
the user to the Microsoft Store version instead.

Windows translates its part of the message into the user's language, so the
tests include the exact Danish error from the original report. Everything is
simulated (platform, registry), so these run on every CI platform.
"""

from __future__ import annotations

import sys
import types

import pytest

import opensak.app_control as ac
from opensak.app_control import (
    SAC_EVALUATION, SAC_OFF, SAC_ON, is_app_control_block, smart_app_control_state,
)

# Verbatim from the reporter's opensak.log (Danish Windows 11 Pro).
_DANISH = ("DLL load failed while importing _cache_key_cy: "
           "En politik for programkontrol har blokeret denne fil.")
_ENGLISH = ("DLL load failed while importing _cache_key_cy: "
            "An Application Control policy has blocked this file.")
_MISSING_RUNTIME = ("DLL load failed while importing etree: "
                    "The specified module could not be found.")


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(ac.sys, "platform", "win32")

    def _set_state(state):
        monkeypatch.setattr(ac, "smart_app_control_state", lambda: state)
    _set_state(None)
    return _set_state


class _WinOSError(OSError):
    def __init__(self, winerror):
        super().__init__("blocked")
        self.winerror = winerror


# ── is_app_control_block ──────────────────────────────────────────────────

def test_never_on_other_platforms(monkeypatch):
    monkeypatch.setattr(ac.sys, "platform", "darwin")
    assert is_app_control_block(ImportError(_ENGLISH)) is False


def test_english_message_is_recognised_without_registry(windows):
    assert is_app_control_block(ImportError(_ENGLISH)) is True


@pytest.mark.parametrize("state, expected", [
    (SAC_ON, True),
    (SAC_OFF, False),
    (SAC_EVALUATION, False),   # evaluation mode doesn't block
    (None, False),             # unknown → don't guess
])
def test_translated_message_relies_on_smart_app_control_state(windows, state, expected):
    windows(state)
    assert is_app_control_block(ImportError(_DANISH)) is expected


def test_winerror_4551_is_recognised(windows):
    assert is_app_control_block(_WinOSError(4551)) is True


def test_other_winerrors_are_not(windows):
    windows(SAC_ON)
    assert is_app_control_block(_WinOSError(5)) is False


def test_unrelated_dll_failure_is_not_blamed_on_smart_app_control(windows):
    windows(SAC_OFF)
    assert is_app_control_block(ImportError(_MISSING_RUNTIME)) is False


def test_plain_import_errors_are_ignored(windows):
    windows(SAC_ON)
    assert is_app_control_block(ImportError("No module named 'foo'")) is False
    assert is_app_control_block(RuntimeError("database is locked")) is False


def test_found_anywhere_in_the_exception_chain(windows):
    windows(SAC_ON)
    try:
        try:
            raise ImportError(_DANISH)
        except ImportError as inner:
            raise RuntimeError("init failed") from inner
    except RuntimeError as outer:
        assert is_app_control_block(outer) is True


def test_cyclic_exception_chain_does_not_hang(windows):
    a, b = RuntimeError("a"), RuntimeError("b")
    a.__cause__, b.__cause__ = b, a
    assert is_app_control_block(a) is False


# ── smart_app_control_state ───────────────────────────────────────────────

def test_state_is_none_outside_windows(monkeypatch):
    monkeypatch.setattr(ac.sys, "platform", "linux")
    assert smart_app_control_state() is None


def _fake_winreg(value=None, error=None):
    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    mod = types.ModuleType("winreg")
    mod.HKEY_LOCAL_MACHINE = object()

    def _open(_root, path):
        assert path == r"SYSTEM\CurrentControlSet\Control\CI\Policy"
        if error:
            raise error
        return _Key()

    def _query(_key, name):
        assert name == "VerifiedAndReputablePolicyState"
        return value, 4
    mod.OpenKey = _open
    mod.QueryValueEx = _query
    return mod


@pytest.mark.parametrize("value", [0, 1, 2])
def test_state_reads_the_registry(monkeypatch, value):
    monkeypatch.setattr(ac.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(value=value))
    assert smart_app_control_state() == value


def test_unreadable_registry_gives_none(monkeypatch):
    monkeypatch.setattr(ac.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(error=PermissionError("denied")))
    assert smart_app_control_state() is None


# ── Startup message (app._report_app_control_block) ───────────────────────

@pytest.fixture
def shown(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        "opensak.gui.icon.OpenSAKMessageBox.critical",
        lambda *a, **_k: calls.append(a),
    )
    return calls


def test_startup_shows_store_message_for_a_block(windows, shown, monkeypatch):
    from opensak import app
    from opensak.lang import load_language
    load_language("en")
    windows(SAC_ON)
    monkeypatch.setattr("opensak.msix.is_msix_packaged", lambda: False)

    assert app._report_app_control_block(ImportError(_DANISH)) is True
    _parent, title, msg = shown[0]
    assert "Smart App Control" in title
    assert "Microsoft Store" in msg and _DANISH in msg


def test_startup_falls_through_for_other_errors(windows, shown, monkeypatch):
    from opensak import app
    windows(SAC_OFF)
    monkeypatch.setattr("opensak.msix.is_msix_packaged", lambda: False)
    assert app._report_app_control_block(RuntimeError("database is locked")) is False
    assert shown == []


def test_store_version_never_points_to_itself(windows, shown, monkeypatch):
    from opensak import app
    monkeypatch.setattr("opensak.msix.is_msix_packaged", lambda: True)
    assert app._report_app_control_block(ImportError(_ENGLISH)) is False
    assert shown == []

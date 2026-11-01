"""tests/unit-tests/test_macro_clipboard_1227.py — opensak.set_clipboard() and
opensak.get_clipboard() (#1227).

GSAK's Clip and GetClipText. Writing just replaces the clipboard; reading can
reveal what the user copied (a password, say), so it is put to the user, once
per macro file and run. Without a user interface both fail clearly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from opensak.macro import MacroError, MacroRuntime
from opensak.macro.metadata import PERMISSIONS
from opensak.macro.permissions import FolderPermission
from opensak.macro.runtime import MAX_CLIPBOARD_TEXT


@pytest.fixture
def host():
    """A host with a clipboard (.clipboard); MagicMock answers the rest."""
    mock = MagicMock()
    mock.clipboard = ""
    mock.confirm.return_value = True
    mock.set_clipboard.side_effect = lambda text: setattr(mock, "clipboard", text)
    mock.get_clipboard.side_effect = lambda: mock.clipboard
    return mock


def _runtime(host, folder: Path | None = None):
    out: list[str] = []
    permissions = [FolderPermission(str(folder), read=True, write=False)] if folder else None
    return MacroRuntime(host, output=out.append, folder_permissions=permissions), out


# ── set_clipboard ────────────────────────────────────────────────────────────


def test_set_clipboard_puts_text_without_asking(host):
    runtime, _ = _runtime(host)
    runtime.run('opensak.set_clipboard("Grüezi\\nwelt")')
    assert host.clipboard == "Grüezi\nwelt"
    host.confirm.assert_not_called()


@pytest.mark.parametrize("value, stored", [("12", "12"), ("1.5", "1.5"), ('""', "")])
def test_set_clipboard_accepts_numbers_and_empty_text(host, value, stored):
    runtime, _ = _runtime(host)
    host.clipboard = "before"
    runtime.run(f"opensak.set_clipboard({value})")
    assert host.clipboard == stored


@pytest.mark.parametrize("value", ["nil", "true", "{}"])
def test_set_clipboard_refuses_anything_else(host, value):
    runtime, _ = _runtime(host)
    with pytest.raises(MacroError, match="text must be a string"):
        runtime.run(f"opensak.set_clipboard({value})")
    host.set_clipboard.assert_not_called()


def test_set_clipboard_takes_text_longer_than_a_message(host):
    runtime, _ = _runtime(host)
    runtime.run('opensak.set_clipboard(string.rep("x", 200000))')
    assert len(host.clipboard) == 200_000


def test_set_clipboard_refuses_too_long_text(host):
    runtime, _ = _runtime(host)
    with pytest.raises(MacroError, match="longer than"):
        runtime.run(f'opensak.set_clipboard(string.rep("x", {MAX_CLIPBOARD_TEXT + 1}))')
    host.set_clipboard.assert_not_called()


# ── get_clipboard ────────────────────────────────────────────────────────────


def test_get_clipboard_returns_the_text_after_asking(host):
    runtime, out = _runtime(host)
    host.clipboard = "Grüezi"
    runtime.run("print(opensak.get_clipboard())")
    assert out == ["Grüezi"]
    host.confirm.assert_called_once()
    assert "clipboard" in host.confirm.call_args.args[0]


def test_get_clipboard_without_text_is_empty(host):
    runtime, out = _runtime(host)
    runtime.run('print("[" .. opensak.get_clipboard() .. "]")')
    assert out == ["[]"]


def test_get_clipboard_refused_stops_without_reading(host):
    runtime, _ = _runtime(host)
    host.confirm.return_value = False
    with pytest.raises(MacroError, match="did not allow"):
        runtime.run("opensak.get_clipboard()")
    host.get_clipboard.assert_not_called()


def test_get_clipboard_refuses_too_long_text(host):
    runtime, _ = _runtime(host)
    host.clipboard = "x" * (MAX_CLIPBOARD_TEXT + 1)
    with pytest.raises(MacroError, match="more than"):
        runtime.run("opensak.get_clipboard()")


def test_get_clipboard_over_the_memory_limit_is_an_error(host):
    """Not a crash of the whole process (json_codec.py)."""
    host.clipboard = "x" * 9_000_000
    runtime = MacroRuntime(host, memory_limit=4 * 1024 * 1024)
    with pytest.raises(MacroError, match="not enough memory"):
        runtime.run("opensak.get_clipboard()")


def test_get_clipboard_asks_once_per_run(host):
    runtime, _ = _runtime(host)
    runtime.run("opensak.get_clipboard() opensak.get_clipboard()")
    assert host.confirm.call_count == 1
    runtime.run("opensak.get_clipboard()")
    assert host.confirm.call_count == 2


def test_a_called_macro_is_asked_for_itself(host, tmp_path):
    (tmp_path / "called.lua").write_text("opensak.get_clipboard()", encoding="utf-8")
    main = tmp_path / "main.lua"
    source = 'opensak.get_clipboard()\nopensak.run_macro("called")\nopensak.get_clipboard()'
    main.write_text(source, encoding="utf-8")
    runtime, _ = _runtime(host, tmp_path)
    runtime.run(source, base_dir=tmp_path, macro_path=main)
    assert host.confirm.call_count == 2


# ── Without a user interface ─────────────────────────────────────────────────


@pytest.mark.parametrize("call", ['opensak.set_clipboard("x")', "opensak.get_clipboard()"])
def test_no_user_interface_fails_clearly(call):
    host = MagicMock()
    del host.set_clipboard, host.get_clipboard
    runtime, _ = _runtime(host)
    with pytest.raises(MacroError, match="no user interface"):
        runtime.run(call)
    host.confirm.assert_not_called()


# ── Main window and header ───────────────────────────────────────────────────


def test_main_window_uses_the_system_clipboard(qapp):
    from opensak.gui.mainwindow import MainWindow

    window = MagicMock()
    MainWindow.set_clipboard(window, "from a macro")
    assert qapp.clipboard().text() == "from a macro"
    assert MainWindow.get_clipboard(window) == "from a macro"


def test_read_clipboard_can_be_declared():
    from opensak.gui.dialogs.macro_manager import _PERMISSION_KEYS

    assert "read-clipboard" in PERMISSIONS
    assert set(_PERMISSION_KEYS) == set(PERMISSIONS)

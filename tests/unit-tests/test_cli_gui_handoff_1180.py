"""
tests/unit-tests/test_cli_gui_handoff_1180.py — a bare `opensak` through the
MSIX alias returns at once (#1180): the frozen opensak-cli.exe starts the GUI
in opensak.exe next to it and exits; commands, --help and --run-macro stay in
the console program. sys.frozen, sys.executable and subprocess.Popen are
faked; the real exes are checked in the sideload round
(packaging/msix/README.md).
"""

import subprocess
import sys
import types
from pathlib import Path
from typing import Any, Optional

import pytest

from opensak import cli


class _Popen:
    """Stands in for subprocess.Popen; keeps every call."""

    calls: list[tuple[list[str], dict[str, Any]]] = []

    def __init__(self, args: list[str], **kwargs: Any) -> None:
        _Popen.calls.append((args, kwargs))


@pytest.fixture
def popen(monkeypatch):
    _Popen.calls = []
    monkeypatch.setattr(subprocess, "Popen", _Popen)
    return _Popen.calls


def _freeze(monkeypatch, tmp_path: Path, exe_name: Optional[str] = "opensak-cli.exe",
            gui_exe: bool = True) -> Path:
    """Run as if frozen as *exe_name* in tmp_path (None: a source run)."""
    if gui_exe:
        (tmp_path / "opensak.exe").write_bytes(b"")
    if exe_name is None:
        monkeypatch.delattr(sys, "frozen", raising=False)
        return tmp_path
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / exe_name))
    return tmp_path


@pytest.fixture
def gui(monkeypatch):
    """A fake opensak.app whose main() records that the GUI ran here."""
    calls: list[str] = []
    fake_app = types.ModuleType("opensak.app")
    fake_app.main = lambda: calls.append("gui")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "opensak.app", fake_app)
    return calls


@pytest.mark.parametrize("argv", [[], ["caches.gpx"], ["C:\\pq\\1234.zip"]])
def test_gui_is_handed_off_with_its_arguments(monkeypatch, tmp_path, popen, gui, argv):
    folder = _freeze(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["opensak-cli.exe", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.entry()
    assert exc.value.code == 0
    assert gui == []
    [(args, kwargs)] = popen
    assert args == [str(folder / "opensak.exe"), *argv]
    for stream in ("stdin", "stdout", "stderr"):
        assert kwargs[stream] is subprocess.DEVNULL


def test_hand_off_detaches_on_windows(monkeypatch, tmp_path, popen):
    monkeypatch.setattr(subprocess, "DETACHED_PROCESS", 0x8, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    _freeze(monkeypatch, tmp_path)
    assert cli.hand_off_to_gui_exe([]) is True
    assert popen[0][1]["creationflags"] == 0x8 | 0x200


@pytest.mark.parametrize("argv", [
    ["import", "gpx", "x.gpx", "--db", "A"],
    ["--help"],
    ["-h"],
])
def test_commands_and_help_stay_in_the_console(monkeypatch, tmp_path, popen, gui, argv):
    _freeze(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["opensak-cli.exe", *argv])
    monkeypatch.setattr(cli, "main", lambda a: 3)
    with pytest.raises(SystemExit) as exc:
        cli.entry()
    assert exc.value.code == 3
    assert popen == [] and gui == []


@pytest.mark.parametrize("argv", [
    ["--run-macro", "x.lua"],
    ["--run-macro=x.lua", "--timeout", "5"],
    ["--run-macro"],  # bad arguments: the usage error goes to this shell
])
def test_run_macro_stays_in_the_console(monkeypatch, tmp_path, popen, gui, argv):
    _freeze(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["opensak-cli.exe", *argv])
    cli.entry()
    assert popen == []
    assert gui == ["gui"]  # opensak.app.main() handles --run-macro


@pytest.mark.parametrize("exe_name", [None, "opensak.exe", "python.exe"])
def test_no_hand_off_outside_opensak_cli_exe(monkeypatch, tmp_path, popen, gui, exe_name):
    _freeze(monkeypatch, tmp_path, exe_name)
    monkeypatch.setattr(sys, "argv", ["opensak"])
    cli.entry()
    assert popen == []
    assert gui == ["gui"]


def test_gui_runs_here_without_opensak_exe(monkeypatch, tmp_path, popen, gui):
    _freeze(monkeypatch, tmp_path, gui_exe=False)
    monkeypatch.setattr(sys, "argv", ["opensak-cli.exe"])
    cli.entry()
    assert popen == []
    assert gui == ["gui"]


def test_gui_runs_here_if_opensak_exe_does_not_start(monkeypatch, tmp_path, gui):
    def fail(*args: Any, **kwargs: Any) -> None:
        raise OSError("blocked")
    monkeypatch.setattr(subprocess, "Popen", fail)
    _freeze(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["opensak-cli.exe"])
    cli.entry()
    assert gui == ["gui"]

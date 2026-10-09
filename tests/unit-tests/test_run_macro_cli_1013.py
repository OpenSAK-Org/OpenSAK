"""tests/unit-tests/test_run_macro_cli_1013.py — `opensak --run-macro` (#1013).

Argument parsing, error formatting and the wire protocol, plus the client
(in a subprocess) talking to a MacroServer in this process, whose event
loop qtbot keeps going.
"""

import io
import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from opensak.utils import run_macro
from opensak.utils.run_macro import (
    EXIT_BUSY, EXIT_MACRO_ERROR, EXIT_NOT_RUNNING, EXIT_TIMEOUT, EXIT_USAGE,
    decode_lines, encode, format_error, parse_args,
)

SRC = Path(__file__).resolve().parents[2] / "src"


# ── Arguments ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("argv, expected", [
    ([], None),
    (["--feature", "x=true"], None),
    (["--run-macro", "m.lua"], ("m.lua", None)),
    (["--run-macro=m.lua"], ("m.lua", None)),
    (["--run-macro", "m.lua", "--timeout", "2.5"], ("m.lua", 2.5)),
    (["--timeout=3", "--run-macro=a b.lua"], ("a b.lua", 3.0)),
    (["--timeout", "3"], None),      # --timeout alone does nothing
])
def test_parse_args(argv, expected):
    assert parse_args(argv) == expected


@pytest.mark.parametrize("argv", [
    ["--run-macro"],
    ["--run-macro="],
    ["--run-macro", "m.lua", "--timeout"],
    ["--run-macro", "m.lua", "--timeout", "soon"],
    ["--run-macro", "m.lua", "--timeout", "0"],
])
def test_parse_args_rejects(argv):
    with pytest.raises(ValueError):
        parse_args(argv)


def test_main_without_run_macro_starts_the_gui():
    assert run_macro.main(["--feature", "lua-macros=true"]) is None


def test_main_reports_missing_file(tmp_path, capsys):
    assert run_macro.main(["--run-macro", str(tmp_path / "nope.lua")]) == EXIT_USAGE
    assert "macro not found" in capsys.readouterr().err


def test_main_reports_usage(capsys):
    assert run_macro.main(["--run-macro"]) == EXIT_USAGE
    assert "usage: opensak --run-macro" in capsys.readouterr().err


# ── Error format and protocol ────────────────────────────────────────────────

def test_format_error_puts_full_path_in_place_of_chunk_name():
    path = Path("/home/u/macros/fix.lua")
    assert format_error("fix.lua:12: boom", "fix.lua", path) == f"{path}:12: boom"


def test_format_error_without_line_starts_with_path():
    path = Path("/home/u/macros/fix.lua")
    assert (format_error("macro aborted: memory limit reached", "fix.lua", path)
            == f"{path}: macro aborted: memory limit reached")


def test_format_error_does_not_touch_longer_names():
    path = Path("/m/fix.lua")
    assert format_error("prefix.fix.lua:1: x", "fix.lua", path) == f"{path}: prefix.fix.lua:1: x"


def test_decode_lines_keeps_partial_line_and_skips_garbage():
    data = encode({"op": "out", "text": "a\nb"}) + b"not json\n" + b'{"op": "ex'
    messages, rest = decode_lines(data)
    assert messages == [{"op": "out", "text": "a\nb"}]
    assert rest == b'{"op": "ex'


def test_server_name_is_per_user_and_stable():
    assert run_macro.server_name() == run_macro.server_name()
    assert run_macro.server_name().startswith("opensak-macro-")


# ── Client ↔ server ──────────────────────────────────────────────────────────

@pytest.fixture
def server(qtbot, monkeypatch):
    """A MacroServer on a unique name, with a mock host."""
    from opensak.gui import macro_server
    name = f"opensak-macro-test-{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(run_macro, "server_name", lambda: name)
    monkeypatch.setattr(macro_server, "server_name", lambda: name)
    monkeypatch.setattr(run_macro, "CONNECT_TIMEOUT_MS", 500)
    host = MagicMock()
    srv = macro_server.MacroServer(host)
    assert srv.start()
    yield srv, host
    srv.close()


_CLIENT = """
import sys
from pathlib import Path
from opensak.utils import run_macro
run_macro.server_name = lambda: sys.argv[1]
run_macro.CANCEL_GRACE_S = 10.0
timeout = float(sys.argv[3]) if sys.argv[3] else None
sys.exit(run_macro.run_remote(Path(sys.argv[2]), timeout, sys.stdout, sys.stderr))
"""


def _client(qtbot, path, timeout=None):
    """Run the client in its own process, as from an editor (in a thread its
    blocking waits would hold up the server in this process); returns
    (exit code, stdout, stderr)."""
    env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-c", _CLIENT, run_macro.server_name(), str(path),
         "" if timeout is None else str(timeout)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    try:
        qtbot.waitUntil(lambda: proc.poll() is not None, timeout=20000)
    finally:
        if proc.poll() is None:
            proc.kill()
    out, err = proc.communicate()
    return (proc.returncode, out.decode("utf-8").replace("\r\n", "\n"),
            err.decode("utf-8").replace("\r\n", "\n"))


def _macro(tmp_path, source, name="m.lua"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def test_runs_macro_and_returns_output_and_exit_code(qtbot, server, tmp_path):
    path = _macro(tmp_path, 'print("hello", 1)\nopensak.log("note")\nopensak.exit(5)')
    code, out, err = _client(qtbot, path)
    assert (code, out, err) == (5, "hello\t1\n", "note\n")


def test_lua_error_goes_to_stderr_with_full_path(qtbot, server, tmp_path):
    path = _macro(tmp_path, '\nlocal x = nil + 1')
    code, out, err = _client(qtbot, path)
    assert code == EXIT_MACRO_ERROR
    assert err.startswith(f"{path}:2: attempt to perform arithmetic")


def test_dialogs_use_the_host(qtbot, server, tmp_path):
    _, host = server
    host.confirm.return_value = True
    path = _macro(tmp_path, 'print(opensak.confirm("go?"))')
    code, out, _ = _client(qtbot, path)
    assert (code, out) == (0, "true\n")
    host.confirm.assert_called_once_with("go?")


def test_busy_while_another_macro_runs(qtbot, server, tmp_path, monkeypatch):
    from opensak.gui import macro_server
    monkeypatch.setattr(macro_server, "macro_running", lambda: True)
    code, _, err = _client(qtbot, _macro(tmp_path, 'print("x")'))
    assert code == EXIT_BUSY
    assert "another macro is still running" in err


def test_busy_while_a_dialog_is_open(qtbot, server, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: object()))
    code, _, err = _client(qtbot, _macro(tmp_path, 'print("x")'))
    assert code == EXIT_BUSY
    assert "a dialog is open" in err


def test_timeout_cancels_the_macro_in_its_dialog(qtbot, server, tmp_path):
    """The macro waits in a dialog; the client times out, OpenSAK closes the
    dialog and ends the macro."""
    from PySide6.QtWidgets import QDialog
    _, host = server

    def confirm(message):
        dialog = QDialog()
        dialog.setModal(True)
        dialog.exec()               # rejected by the server on cancel
        return False

    host.confirm.side_effect = confirm
    path = _macro(tmp_path, 'pcall(opensak.confirm, "wait")\nprint("not reached")')
    code, out, err = _client(qtbot, path, timeout=0.5)
    assert code == EXIT_TIMEOUT
    assert out == ""
    assert "timeout" in err


def test_unexpected_server_error_still_ends_the_client(qtbot, server, tmp_path, monkeypatch):
    from opensak.gui import macro_server

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(macro_server, "macro_running", boom)
    code, _, err = _client(qtbot, _macro(tmp_path, 'print("x")'))
    assert code == EXIT_MACRO_ERROR
    assert "internal error" in err


def test_works_after_qapplication_was_stubbed_during_import(qtbot, server, tmp_path,
                                                          monkeypatch):
    """test_app's smoke test replaces QtWidgets.QApplication while main()
    imports this module for the first time."""
    import importlib
    import PySide6.QtWidgets as W
    from opensak.gui import macro_server
    real = W.QApplication
    monkeypatch.setattr(W, "QApplication", lambda *a, **k: None)
    importlib.reload(macro_server)
    monkeypatch.setattr(W, "QApplication", real)
    name = run_macro.server_name()
    monkeypatch.setattr(macro_server, "server_name", lambda: name)
    srv = macro_server.MacroServer(MagicMock())
    server[0].close()
    assert srv.start()
    try:
        code, out, _ = _client(qtbot, _macro(tmp_path, 'print("ok")'))
    finally:
        srv.close()
    assert (code, out) == (0, "ok\n")


def test_not_running(monkeypatch, tmp_path):
    monkeypatch.setattr(run_macro, "server_name",
                        lambda: f"opensak-macro-none-{uuid.uuid4().hex[:12]}")
    monkeypatch.setattr(run_macro, "CONNECT_TIMEOUT_MS", 200)
    err = io.StringIO()
    code = run_macro.run_remote(_macro(tmp_path, ""), None, io.StringIO(), err)
    assert code == EXIT_NOT_RUNNING
    assert "not running" in err.getvalue()

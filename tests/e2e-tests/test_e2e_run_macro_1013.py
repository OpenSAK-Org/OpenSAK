"""tests/e2e-tests/test_e2e_run_macro_1013.py — `opensak --run-macro` drives
the real main window (#1013).

The client runs in its own process, as when an editor starts it; the window
and its MacroServer run here.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from opensak.gui import macro_server
from opensak.utils import run_macro

SRC = Path(__file__).resolve().parents[2] / "src"

_CLIENT = """
import sys
from pathlib import Path
from opensak.utils import run_macro
run_macro.server_name = lambda: sys.argv[1]
sys.exit(run_macro.run_remote(Path(sys.argv[2]), None, sys.stdout, sys.stderr))
"""


@pytest.fixture
def server(seeded_window, monkeypatch):
    name = f"opensak-macro-e2e-{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(macro_server, "server_name", lambda: name)
    srv = macro_server.MacroServer(seeded_window, parent=seeded_window)
    assert srv.start()
    yield name
    srv.close()


def _run(qtbot, server, tmp_path, source):
    path = tmp_path / "cli.lua"
    path.write_text(source, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, "-c", _CLIENT, server, str(path)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    qtbot.waitUntil(lambda: proc.poll() is not None, timeout=20000)
    out, err = proc.communicate()
    return proc.returncode, out.decode("utf-8").splitlines(), err.decode("utf-8")


def test_cli_macro_filters_the_window(qtbot, seeded_window, server, tmp_path):
    code, out, err = _run(qtbot, server, tmp_path, """
        local n = opensak.filter{ code = "GCAAA0", label = "From editor" }
        print("n=" .. n)
        opensak.exit(n == 2 and 0 or 9)
    """)
    assert (code, out, err) == (0, ["n=2"], "")
    assert seeded_window._cache_table.row_count() == 2
    assert seeded_window._active_filter_name == "From editor"


def test_cli_macro_confirm_dialog_in_the_window(qtbot, seeded_window, server, tmp_path):
    def answer_yes():
        box = QApplication.activeModalWidget()
        if isinstance(box, QMessageBox):
            box.button(QMessageBox.StandardButton.Yes).click()
        else:
            QTimer.singleShot(50, answer_yes)

    QTimer.singleShot(50, answer_yes)
    code, out, _ = _run(qtbot, server, tmp_path, 'print(opensak.confirm("Continue?"))')
    assert (code, out) == (0, ["true"])


def test_cli_macro_error_names_file_and_line(qtbot, seeded_window, server, tmp_path):
    code, out, err = _run(qtbot, server, tmp_path, '\nopensak.filter{ nonsense = 1 }')
    assert code == run_macro.EXIT_MACRO_ERROR
    assert err.startswith(f"{tmp_path / 'cli.lua'}:2: ")

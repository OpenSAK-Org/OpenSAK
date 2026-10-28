"""tests/unit-tests/test_macro_shift_key_1038.py — opensak.shift_key() (#1038).

A macro learns whether Shift was held when the user started it, so it can
show its options only then and otherwise run unattended with the saved ones
(GSAK's $_ShiftKey). The GUI reads Shift when the macro is started, before
any question; a macro started by another macro never sees it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from opensak.macro import MacroRuntime
from opensak.macro.permissions import FolderPermission
from opensak.macro.runtime import API, API_VERSION


def _runtime(folder: Path | None = None):
    out: list[str] = []
    permissions = [FolderPermission(str(folder), read=True, write=False)] if folder else None
    return MacroRuntime(MagicMock(), output=out.append, folder_permissions=permissions), out


def _lua(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ── Runtime ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("shift, printed", [(True, "true"), (False, "false")])
def test_shift_key_reports_the_run(shift, printed):
    runtime, out = _runtime()
    runtime.run("print(opensak.shift_key())", shift=shift)
    assert out == [printed]


def test_shift_key_defaults_to_false():
    runtime, out = _runtime()
    runtime.run("print(opensak.shift_key())")
    assert out == ["false"]


def test_shift_is_not_kept_for_the_next_run():
    runtime, out = _runtime()
    runtime.run("print(opensak.shift_key())", shift=True)
    runtime.run("print(opensak.shift_key())")
    assert out == ["true", "false"]


def test_called_macro_runs_without_shift(tmp_path):
    _lua(tmp_path / "called.lua", 'print("called", opensak.shift_key())')
    _lua(tmp_path / "nested.lua", 'print("nested", opensak.shift_key())')
    _lua(tmp_path / "middle.lua", 'opensak.run_macro("nested")\n'
                                  'print("middle", opensak.shift_key())')
    runtime, out = _runtime(tmp_path)
    runtime.run('opensak.run_macro("called")\n'
                'print("caller", opensak.shift_key())\n'
                'opensak.run_macro("middle")\n'
                'pcall(opensak.run_macro, "missing")\n'
                'print("caller", opensak.shift_key())',
                base_dir=tmp_path, shift=True)
    assert out == ["called\tfalse", "caller\ttrue", "nested\tfalse", "middle\tfalse",
                   "caller\ttrue"]


def test_shift_key_is_api_5():
    func = next(f for f in API if f.name == "shift_key")
    assert func.since == 5 == API_VERSION


# ── GUI: Shift is read when the user starts the macro ────────────────────────

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from opensak.gui.dialogs import macro_dialog as md  # noqa: E402
from opensak.gui.dialogs import macro_manager as mm  # noqa: E402

SHIFT_MACRO = "print('shift', opensak.shift_key())\n"


@pytest.fixture
def macros_dir(tmp_path, monkeypatch):
    d = tmp_path / "macros"
    d.mkdir()
    monkeypatch.setattr("opensak.config.get_macros_dir", lambda: d)
    return d.resolve()


@pytest.fixture
def manager(qtbot, macros_dir):
    d = mm.MacroManagerDialog(host=MagicMock())
    qtbot.addWidget(d)
    return d


def _select(d, path: Path) -> None:
    d.reload()
    d.select_path(path)


@pytest.mark.parametrize("held", [True, False])
def test_manager_run_button_passes_shift(manager, macros_dir, monkeypatch, held):
    macro = _lua(macros_dir / "m.lua", SHIFT_MACRO)
    monkeypatch.setattr(mm, "shift_held", lambda: held)
    _select(manager, macro)
    manager._btn_run.click()
    assert f"shift\t{str(held).lower()}" in manager._output.toPlainText()


def test_manager_reads_shift_before_asking(manager, macros_dir, monkeypatch):
    """Double-click asks first; Shift let go while answering still counts."""
    macro = _lua(macros_dir / "m.lua", SHIFT_MACRO)
    held = [True]
    monkeypatch.setattr(mm, "shift_held", lambda: held[0])

    def answer(*args, **kwargs):
        held[0] = False
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(mm.QMessageBox, "question", answer)
    _select(manager, macro)
    manager._run_activated()
    assert "shift\ttrue" in manager._output.toPlainText()


@pytest.fixture
def editor_dialog(qtbot, macros_dir):
    d = md.MacroDialog(host=MagicMock())
    qtbot.addWidget(d)
    return d


@pytest.mark.parametrize("held", [True, False])
def test_editor_run_passes_shift(editor_dialog, monkeypatch, held):
    monkeypatch.setattr(md, "shift_held", lambda: held)
    editor_dialog._editor.setPlainText(SHIFT_MACRO)
    editor_dialog._run()
    assert f"shift\t{str(held).lower()}" in editor_dialog._output.toPlainText()


def test_editor_save_and_run_reads_shift_before_saving(editor_dialog, macros_dir,
                                                       monkeypatch):
    held = [True]
    monkeypatch.setattr(md, "shift_held", lambda: held[0])
    editor_dialog.open_path(_lua(macros_dir / "m.lua", "-- old\n"))
    editor_dialog._editor.setPlainText(SHIFT_MACRO)
    save = editor_dialog._save

    def save_and_let_go():
        held[0] = False
        return save()

    monkeypatch.setattr(editor_dialog, "_save", save_and_let_go)
    editor_dialog._save_and_run()
    assert "shift\ttrue" in editor_dialog._output.toPlainText()


def test_editor_runs_with_shift_f5(editor_dialog):
    keys = [s.toString() for s in editor_dialog._act_run.shortcuts()]
    assert keys == ["F5", "Shift+F5"]
    keys = [s.toString() for s in editor_dialog._act_save_run.shortcuts()]
    assert keys == ["Ctrl+F5", "Ctrl+Shift+F5"]

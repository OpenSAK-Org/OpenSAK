"""tests/unit-tests/test_macro_form_dialog.py — the Qt side of the macro UI
functions (#982): the form dialog built from a spec and the progress window."""

from typing import Any

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from opensak.gui.dialogs.macro_form import FormDialog, MacroProgress  # noqa: E402
from opensak.macro.forms import Form, RawLua, parse_form  # noqa: E402

ALL_FIELDS = [
    {"type": "label", "id": "head", "label": "<b>Head</b>", "align": "center", "color": "red"},
    {"type": "separator"},
    {"type": "text", "id": "text", "label": "Text", "default": "abc", "placeholder": "p",
     "required": True},
    {"type": "number", "id": "num", "label": "Num", "default": 2.5, "decimals": 1,
     "min": 0, "max": 10, "suffix": " km", "bold": True},
    {"type": "memo", "id": "memo", "default": "a\nb", "monospace": True, "wrap": False},
    {"type": "checkbox", "id": "check", "label": "Check", "default": True},
    {"type": "choice", "id": "choice", "options": ["GPX", "GGZ"], "default": "GGZ"},
    {"type": "choice", "id": "edit", "options": ["a"], "editable": True, "default": "free"},
    {"type": "radio", "id": "radio", "options": [1, 2, 3], "columns": 2, "default": 3},
    {"type": "checklist", "id": "list", "options": ["x", "y", "z"], "default": ["z", "x"]},
    {"type": "date", "id": "date", "default": "2026-10-11", "min": "2020-01-01"},
    {"type": "file", "id": "file", "default": "C:/a.csv", "mode": "save"},
    {"type": "folder", "id": "folder"},
    {"type": "group", "id": "grp", "label": "Group", "fields": [
        {"type": "text", "id": "inner", "label": "Inner"}]},
    {"type": "image", "id": "img"},
    {"type": "html", "id": "html", "html": "<h1>Hi</h1><img src='C:/x.png'>"},
    {"type": "button", "id": "apply", "label": "Apply"},
]
EXPECTED = {
    "text": "abc", "num": 2.5, "memo": "a\nb", "check": True, "choice": "GGZ", "edit": "free",
    "radio": 3, "list": ["x", "z"], "date": "2026-10-11", "file": "C:/a.csv", "folder": "",
    "inner": "",
}


def _form(**spec) -> Form:
    return parse_form(spec, is_function=lambda v: isinstance(v, RawLua) or callable(v))


def _dialog(qtbot, form: Form, events=None) -> FormDialog:
    dialog = FormDialog(form, events)
    qtbot.addWidget(dialog)
    return dialog


def _ids(dialog: FormDialog) -> dict[str, Any]:
    return {spec.id: item for spec, item in dialog.view.items.items() if spec.id}


@pytest.mark.parametrize("layout", ["auto", "absolute"])
def test_every_field_type_shows_its_default(qtbot, layout):
    fields = [dict(f, x=8, y=8 + 30 * i) if layout == "absolute" else f
              for i, f in enumerate(ALL_FIELDS)]
    dialog = _dialog(qtbot, _form(layout=layout, fields=fields))
    assert dialog.read_values() == EXPECTED
    items = _ids(dialog)
    assert items["num"].box.suffix() == " km"
    if layout == "absolute":
        assert items["text"].widget.geometry().topLeft() == QPoint(8, 68)
        assert items["head"].caption is None
    else:
        assert items["text"].caption.text() == "Text"


def test_controller_methods_change_the_widgets(qtbot):
    form = _form(fields=ALL_FIELDS)
    dialog = _dialog(qtbot, form)
    by_id = form.by_id()
    dialog.set_value(by_id["text"], "new")
    by_id["choice"].options = ["GGZ", "KML"]
    dialog.set_options(by_id["choice"])
    by_id["radio"].options = [4]
    dialog.set_options(by_id["radio"])
    by_id["list"].options = ["z"]
    dialog.set_options(by_id["list"])
    by_id["text"].label = "Renamed"
    dialog.set_label(by_id["text"])
    by_id["grp"].label = "G2"
    dialog.set_label(by_id["grp"])
    dialog.set_enabled(by_id["num"], False)
    dialog.set_visible(by_id["memo"], False)
    dialog.set_title("New title")
    values = dialog.read_values()
    assert (values["text"], values["choice"], values["radio"], values["list"]) == \
        ("new", "GGZ", None, ["z"])
    items = _ids(dialog)
    assert items["text"].caption.text() == "Renamed"
    assert items["grp"].widget.title() == "G2"
    assert not items["num"].widget.isEnabled() and not items["num"].caption.isEnabled()
    assert items["memo"].widget.isHidden()
    assert dialog.windowTitle() == "New title"


def test_ok_returns_values_cancel_returns_none(qtbot):
    form = _form(title="T", fields=[{"type": "text", "id": "a", "default": "x"}])
    dialog = _dialog(qtbot, form)
    ok = next(b for b in form.buttons if b.id == "ok")
    QTimer.singleShot(0, lambda: _ids(dialog)["ok"].widget.click())
    assert dialog.run() == ({"a": "x"}, "ok")
    assert ok.get("default") is True

    dialog = _dialog(qtbot, form)
    QTimer.singleShot(0, dialog.reject)
    assert dialog.run() is None


def test_required_field_blocks_ok(qtbot, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    form = _form(fields=[{"type": "text", "id": "a", "label": "Name", "required": True}])
    dialog = _dialog(qtbot, form)
    _ids(dialog)["ok"].widget.click()
    assert len(warnings) == 1
    assert dialog.isVisible() is False and dialog.result() == 0


def test_events_reach_the_runtime(qtbot, monkeypatch):
    seen: list[tuple[str, Any]] = []
    refusals = ["not yet"]

    def events(controller, event, field):
        seen.append((event, field.id if field else None))
        if event == "validate" and refusals:
            return refusals.pop()
        return None

    monkeypatch.setattr(QMessageBox, "warning", lambda *a: seen.append(("warning", a[2])))
    form = _form(fields=[
        {"type": "checkbox", "id": "c", "on_change": RawLua("cb")},
        {"type": "button", "id": "b", "label": "B", "on_click": RawLua("cb")},
        {"type": "folder", "id": "f"},
    ])
    dialog = _dialog(qtbot, form, events)
    items = _ids(dialog)
    items["c"].widget.setChecked(True)          # toggled → change
    items["b"].widget.click()                   # on_click → click
    dialog._browse(form.by_id()["f"])           # Browse → browse (value unchanged: no change)
    items["ok"].widget.click()                  # validate refuses
    items["ok"].widget.click()                  # validate accepts
    assert seen == [("change", "c"), ("click", "b"), ("browse", "f"), ("validate", "ok"),
                    ("warning", "not yet"), ("validate", "ok")]
    assert dialog._closer == "ok"


def test_exit_on_change_and_timeout_close_the_form(qtbot):
    form = _form(fields=[{"type": "choice", "id": "dish", "options": ["a", "b"],
                          "exit_on_change": True}])
    dialog = _dialog(qtbot, form)
    combo = _ids(dialog)["dish"].combo
    QTimer.singleShot(0, lambda: (combo.setCurrentIndex(1), combo.activated.emit(1)))
    assert dialog.run() == ({"dish": "b"}, "dish")

    dialog = _dialog(qtbot, _form(timeout=0.01, fields=[]))
    assert dialog.run() == ({}, "timeout")


def test_text_change_fires_on_editing_finished_only_when_changed(qtbot):
    changes = []
    form = _form(fields=[{"type": "text", "id": "t", "on_change": RawLua("cb")}])
    dialog = _dialog(qtbot, form, lambda c, e, f: changes.append(e))
    edit = _ids(dialog)["t"].edit
    edit.editingFinished.emit()
    edit.setText("x")
    edit.editingFinished.emit()
    edit.editingFinished.emit()
    assert changes == ["change"]


def test_html_field_loads_no_resources(qtbot):
    dialog = _dialog(qtbot, _form(fields=[{"type": "html", "html": "<img src='x.png'>"}]))
    view = next(iter(dialog.view.items.values())).widget
    assert view.loadResource(2, "file:///C:/Windows/win.ini") is None


def test_progress_window(qtbot):
    progress = MacroProgress(None, "Work", 3)
    assert progress.update(2, "GC1") is True
    assert progress._dialog.value() == 2
    progress._dialog.cancel()
    assert progress.update(3, "") is False
    progress.close()



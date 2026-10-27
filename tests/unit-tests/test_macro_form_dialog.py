"""tests/unit-tests/test_macro_form_dialog.py — the Qt side of the macro UI
functions (#982): the form dialog built from a spec, the form designer and
its use from the macro editor."""

from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from opensak.gui.dialogs.macro_form import FormDialog, MacroProgress  # noqa: E402
from opensak.macro.form_code import parse_table  # noqa: E402
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


def test_open_reaches_the_runtime_without_on_open_1197(qtbot):
    # The runtime replays on_change of remembered values on "open", so the
    # dialog sends it even when the form has no on_open.
    seen = []
    form = _form(timeout=0.01, fields=[])
    dialog = _dialog(qtbot, form, lambda c, e, f: seen.append(e))
    dialog.run()
    assert seen[0] == "open"


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


# ── Form designer ────────────────────────────────────────────────────────────


@pytest.fixture
def designer(qtbot):
    from opensak.gui.dialogs.form_designer import FormDesigner
    d = FormDesigner()
    qtbot.addWidget(d)
    return d


def _select_tool(designer, kind: str) -> None:
    for i in range(designer._toolbox.count()):
        if designer._toolbox.item(i).data(Qt.ItemDataRole.UserRole) == kind:
            designer._toolbox.setCurrentRow(i)
            return
    raise AssertionError(kind)


def test_designer_adds_moves_edits_and_undoes(designer):
    _select_tool(designer, "text")
    designer._add_field(None, QPoint(16, 24))
    _select_tool(designer, "group")
    designer._add_field(None, QPoint(8, 64))
    model = designer.model()
    assert [(f["type"], f["id"], f["x"], f["y"]) for f in model["fields"]] == [
        ("text", "text1", 16, 24), ("group", "group1", 8, 64)]
    # Edit a property of the selected group, then move the text field into it.
    designer._property_edited("label", "Output")
    designer._move_into(("fields", 0), ("fields", 1), QPoint(9, 17))
    model = designer.model()
    assert model["fields"][0]["label"] == "Output"
    assert model["fields"][0]["fields"][0]["id"] == "text1"
    assert (model["fields"][0]["fields"][0]["x"], model["fields"][0]["fields"][0]["y"]) == (8, 16)
    assert designer._canvas.selection == [("fields", 0, "fields", 0)]
    designer._undo_step()
    assert len(designer.model()["fields"]) == 2
    designer._redo_step()
    assert len(designer.model()["fields"]) == 1
    # The generated Lua is a valid form.
    parse_form(parse_table(designer.lua()), is_function=lambda v: isinstance(v, RawLua))


def test_designer_geometry_align_and_delete(designer):
    _select_tool(designer, "button")
    designer._add_field(None, QPoint(10, 10))
    designer._add_field(None, QPoint(40, 50))
    designer._canvas.set_selection([("fields", 0), ("fields", 1)])
    designer._align("left")
    designer._align("width")
    fields = designer.model()["fields"]
    assert fields[1]["x"] == 10 and fields[1]["w"] == fields[0]["w"]
    designer._apply_geometry({("fields", 0): (24, 32, 80, 30)})
    assert designer.model()["fields"][0] | {} == designer.model()["fields"][0]
    assert {k: designer.model()["fields"][0][k] for k in "xywh"} == {"x": 24, "y": 32, "w": 80, "h": 30}
    designer._canvas.set_selection([("fields", 1)])
    designer._delete()
    assert len(designer.model()["fields"]) == 1


def test_designer_switches_layout_both_ways(designer):
    designer._replace_model({"layout": "auto", "fields": [
        {"type": "text", "id": "a", "label": "Name"},
        {"type": "checkbox", "id": "b", "label": "Yes"}]}, [])
    designer._canvas.set_selection([])
    designer._property_edited("layout", "absolute")
    model = designer.model()
    assert model["layout"] == "absolute"
    kinds = [(f["type"], f.get("label")) for f in model["fields"]]
    assert kinds == [("label", "Name"), ("text", "Name"), ("checkbox", "Yes")]
    assert all({"x", "y", "w", "h"} <= set(f) for f in model["fields"])
    designer._property_edited("layout", "auto")
    model = designer.model()
    assert model["layout"] == "auto" and not any("x" in f for f in model["fields"])


def test_designer_keeps_lua_expressions(designer):
    designer._replace_model(parse_table(
        '{ title = "T", fields = { { type = "choice", id = "c", options = opts,'
        ' on_change = function(f) f:set("c", nil) end } } }'), [])
    lua = designer.lua()
    assert "options = opts" in lua and 'on_change = function(f) f:set("c", nil) end' in lua
    assert designer._canvas.error == ""


def test_designer_loads_gsak_and_lua_text(designer):
    gsak = "<Data> VarName=$form\nName = F\n Type = Form\n Caption = G\n" \
           "Name = e\n Type = Edit\n Left = 4\n Top = 5\n<enddata>\n"
    model, notes = designer.load_text(gsak)
    assert model["title"] == "G" and model["fields"][0]["id"] == "e"
    model, notes = designer.load_text('x = 1\nopensak.form{ fields = { { type = "label" } } }')
    assert model == {"fields": [{"type": "label"}]} and notes == []
    with pytest.raises(ValueError):
        designer.load_text("print(1)")


def test_designer_buttons_menu(designer):
    designer._set_buttons("add")
    assert [b["id"] for b in designer.model()["buttons"]] == ["ok", "cancel", "button1"]
    designer._set_buttons("none")
    assert designer.model()["buttons"] == []
    designer._set_buttons("default")
    assert "buttons" not in designer.model()


def test_designer_shows_spec_errors(designer):
    designer._replace_model({"fields": [{"type": "text", "id": "a", "bad": 1}]}, [])
    assert "unknown key(s) bad" in designer._canvas.error
    assert "unknown key(s) bad" in designer._status.text()


def test_canvas_drag_moves_a_field(designer, qtbot):
    _select_tool(designer, "button")
    designer._add_field(None, QPoint(8, 8))
    canvas = designer._canvas
    designer.show()
    start = canvas.rect_of(("fields", 0)).center()

    def send(kind, pos, buttons):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QMouseEvent
        point = QPointF(pos)
        QApplication.sendEvent(canvas, QMouseEvent(
            kind, point, canvas.mapToGlobal(point), Qt.MouseButton.LeftButton, buttons,
            Qt.KeyboardModifier.NoModifier))

    left = Qt.MouseButton.LeftButton
    send(QEvent.Type.MouseButtonPress, start, left)
    send(QEvent.Type.MouseMove, start + QPoint(10, 3), left)
    send(QEvent.Type.MouseMove, start + QPoint(40, 17), left)
    send(QEvent.Type.MouseButtonRelease, start + QPoint(40, 17), Qt.MouseButton.NoButton)
    field = designer.model()["fields"][0]
    assert (field["x"], field["y"]) == (48, 24)  # snapped to the 8 px grid

    # Double-click on the empty form adds the toolbox's control there ...
    _select_tool(designer, "group")
    origin = canvas.container_origin(None)
    send(QEvent.Type.MouseButtonDblClick, origin + QPoint(100, 80), left)
    group = designer.model()["fields"][1]
    assert (group["type"], group["x"], group["y"]) == ("group", 96, 80)
    # ... and dropping the button on the group moves it inside.
    start = canvas.rect_of(("fields", 0)).center()
    target = canvas.rect_of(("fields", 1)).center()
    send(QEvent.Type.MouseButtonPress, start, left)
    send(QEvent.Type.MouseMove, target, left)
    send(QEvent.Type.MouseButtonRelease, target, Qt.MouseButton.NoButton)
    fields = designer.model()["fields"]
    assert [f["type"] for f in fields] == ["group"]
    assert fields[0]["fields"][0]["id"] == "button1"
    assert designer._canvas.selection == [("fields", 0, "fields", 0)]


# ── Macro editor integration ─────────────────────────────────────────────────


@pytest.fixture
def macro_dialog(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr("opensak.config.get_macros_dir", lambda: tmp_path)
    monkeypatch.setattr("opensak.macro.recent.recent_macros", lambda: [])
    from opensak.gui.dialogs import macro_dialog as md
    # qtbot closes the window after the test; the edited text would then
    # block on the modal "save changes?" question.
    monkeypatch.setattr(md.QMessageBox, "question",
                        lambda *a, **k: md.QMessageBox.StandardButton.Discard)
    dlg = md.MacroDialog(host=MagicMock())
    qtbot.addWidget(dlg)
    return dlg


def _fake_designer(monkeypatch, seen: dict, model: dict):
    from opensak.gui.dialogs import form_designer

    class Fake:
        def __init__(self, given, parent, updating=False):
            seen["model"], seen["updating"] = given, updating

        def exec(self):
            return QDialog.DialogCode.Accepted

        def lua(self, indent=""):
            from opensak.macro.form_code import form_to_lua
            return form_to_lua(model, indent)

    monkeypatch.setattr(form_designer, "FormDesigner", Fake)


def test_form_designer_inserts_a_new_form(macro_dialog, monkeypatch):
    seen: dict = {}
    _fake_designer(monkeypatch, seen, {"fields": [{"type": "text", "id": "a"}]})
    editor = macro_dialog._editor
    editor.setPlainText("print(1)\n  -- here\n")
    cursor = editor.textCursor()
    cursor.setPosition(len("print(1)\n  -- he"))
    editor.setTextCursor(cursor)
    macro_dialog._open_form_designer()
    assert seen == {"model": None, "updating": False}
    assert editor.toPlainText() == (
        "print(1)\n  -- here\n"
        "  local values, button = opensak.form{\n"
        "    fields = {\n"
        '      { type = "text", id = "a" },\n'
        "    },\n"
        "  }\n"
        "  if not values then return end  -- cancelled\n"
    )


def test_form_designer_updates_the_form_at_the_cursor(macro_dialog, monkeypatch):
    seen: dict = {}
    _fake_designer(monkeypatch, seen, {"title": "New", "fields": []})
    editor = macro_dialog._editor
    text = '-- 😀 emoji first\nlocal v = opensak.form{ title = "Old", fields = {} }\nprint(v)\n'
    editor.setPlainText(text)
    cursor = editor.textCursor()
    cursor.setPosition(text.index("Old") + 1 + 1)  # +1: the emoji is two UTF-16 units
    editor.setTextCursor(cursor)
    macro_dialog._open_form_designer()
    assert seen == {"model": {"title": "Old", "fields": []}, "updating": True}
    assert editor.toPlainText() == (
        '-- 😀 emoji first\nlocal v = opensak.form{\n  title = "New",\n  fields = {},\n}\n'
        "print(v)\n"
    )

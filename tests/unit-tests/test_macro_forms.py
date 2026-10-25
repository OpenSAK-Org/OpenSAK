"""tests/unit-tests/test_macro_forms.py — the macro UI functions (#982)
without Qt: the opensak.form{} spec (macro/forms.py), reading and writing
form tables (macro/form_code.py), the GSAK form import (macro/gsak_form.py)
and the runtime side of message/input/choose/choose_folder/form/progress/
select/refresh, driven by a scripted host."""

from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from opensak.macro import MacroError, MacroRuntime, WriteApproval
from opensak.macro.form_code import (
    LuaSyntaxError,
    find_form_table,
    find_form_tables,
    form_to_lua,
    lua_string,
    parse_table,
    tokenize,
)
from opensak.macro.forms import (
    FIELD_KEY_DOCS,
    FIELD_TYPES,
    FORM_KEY_DOCS,
    FORM_KEYS,
    VALUE_KEYS,
    COMMON_KEYS,
    Field,
    Form,
    RawLua,
    apply_remembered,
    check_required,
    coerce_value,
    find_option,
    initial_value,
    parse_form,
    remembered_ids,
)
from opensak.macro.gsak_form import convert, find_gsak_forms
from opensak.macro.runtime import FolderApproval


def _raw(value: Any) -> bool:
    return isinstance(value, RawLua)


def _form(**spec) -> Form:
    return parse_form(spec, is_function=_raw)


# ── Spec validation ──────────────────────────────────────────────────────────


def test_default_buttons_are_ok_and_cancel():
    form = _form(fields=[{"type": "text", "id": "name"}])
    assert [(b.id, b.get("default"), b.get("cancel")) for b in form.buttons] == [
        ("ok", True, None), ("cancel", None, True)]
    assert form.layout == "auto"


def test_empty_buttons_table_means_no_button_row():
    assert _form(fields=[], buttons={}).buttons == []
    assert _form(fields=[], buttons=[]).buttons == []


@pytest.mark.parametrize("spec, message", [
    ({"fields": [{"type": "slider", "id": "a"}]}, "unknown field type 'slider'"),
    ({"fields": [{"type": "text", "id": "a", "colour": "red"}]}, "unknown key(s) colour"),
    ({"fields": [{"type": "text"}]}, 'a "text" field needs an id'),
    ({"fields": [{"type": "text", "id": "a"}, {"type": "number", "id": "a"}]}, "used twice"),
    ({"fields": [{"type": "text", "id": "2x"}]}, "id must be a name"),
    ({"titel": "x"}, "unknown key(s) titel"),
    ({"layout": "grid"}, 'layout must be one of "auto", "absolute"'),
    ({"fields": [{"type": "number", "id": "n", "min": 5, "max": 1}]}, "min (5) is larger than max (1)"),
    ({"fields": [{"type": "choice", "id": "c", "options": ["a"], "default": "b"}]},
     "'b' is not one of the options"),
    ({"fields": [{"type": "text", "id": "t", "on_change": "f"}]}, "on_change must be a function"),
    ({"fields": [{"type": "label", "label": "x", "color": "#12"}]}, "color must be"),
    ({"timeout": 0}, "timeout must be > 0"),
    ({"fields": [{"type": "date", "id": "d", "default": "13/01/2026"}]}, 'must be a date "YYYY-MM-DD"'),
])
def test_invalid_specs_name_the_problem(spec, message):
    with pytest.raises(MacroError) as exc:
        _form(**spec)
    assert message in str(exc.value)
    assert str(exc.value).startswith("opensak.form")


def test_error_names_the_field_position():
    with pytest.raises(MacroError, match=r"fields\[2\]\.fields\[1\] \(x\): unknown key"):
        _form(fields=[{"type": "text", "id": "a"},
                      {"type": "group", "fields": [{"type": "text", "id": "x", "bad": 1}]}])


def test_defaults_are_coerced_to_the_field_type():
    form = _form(fields=[
        {"type": "number", "id": "n", "default": "7.6", "max": 5},
        {"type": "number", "id": "d", "default": 1.234, "decimals": 2},
        {"type": "text", "id": "t", "default": 5.0},
        {"type": "choice", "id": "c", "options": [1, 2, 3], "default": "2"},
        {"type": "checklist", "id": "l", "options": ["a", "b", "c"], "default": ["c", "a"]},
        {"type": "choice", "id": "e", "options": ["x"], "editable": True, "default": "free"},
    ])
    values = {f.id: initial_value(f) for f in form.value_fields()}
    assert values == {"n": 5, "d": 1.23, "t": "5", "c": 2, "l": ["a", "c"], "e": "free"}


def test_initial_values_without_default():
    form = _form(fields=[
        {"type": "text", "id": "t"}, {"type": "checkbox", "id": "c"},
        {"type": "choice", "id": "ch", "options": ["a"]}, {"type": "checklist", "id": "l"},
        {"type": "date", "id": "d"}, {"type": "number", "id": "n", "min": 3},
    ])
    values = {f.id: initial_value(f) for f in form.value_fields()}
    assert values == {"t": "", "c": False, "ch": "a", "l": [], "d": date.today().isoformat(),
                      "n": 3}


def test_groups_nest_and_walk_in_order():
    form = _form(layout="absolute", fields=[
        {"type": "group", "id": "g", "label": "Output", "x": 8, "y": 8, "w": 200, "h": 100,
         "fields": [{"type": "folder", "id": "folder", "x": 8, "y": 20}]},
        {"type": "button", "id": "go", "label": "Go"},
    ])
    assert [f.id for f in form.walk()] == ["g", "folder", "go", "ok", "cancel"]
    assert form.by_id()["folder"].get("x") == 8
    assert [f.id for f in form.value_fields()] == ["folder"]


def test_buttons_take_geometry_inside_the_fields():
    form = _form(layout="absolute", buttons={}, fields=[
        {"type": "button", "id": "b", "label": "B", "x": 10, "y": 20, "w": 75, "h": 25}])
    assert form.fields[0].get("w") == 75


def test_lenient_parse_drops_raw_values_but_keeps_callbacks():
    spec = {"title": RawLua("t"), "fields": [
        {"type": "choice", "id": "c", "options": RawLua("opts"), "on_change": RawLua("cb")},
        RawLua("make_field()"),
    ]}
    with pytest.raises(MacroError):
        parse_form(spec, is_function=_raw)
    form = parse_form(spec, is_function=_raw, lenient=True)
    assert form.title == "" and len(form.fields) == 1
    assert form.fields[0].get("on_change") == RawLua("cb")


def test_find_option_matches_numbers_and_strings():
    assert find_option(["1", "2"], 2) == 1
    assert find_option([1, 2.5], "2.5") == 1
    assert find_option([True, "x"], True) == -1
    assert find_option(["a"], "b") == -1


def test_coerce_value_errors():
    field = _form(fields=[{"type": "checkbox", "id": "c"}]).fields[0]
    with pytest.raises(MacroError, match="true or false"):
        coerce_value(field, "yes", "f:set")


def test_required_and_remember_helpers():
    form = _form(fields=[
        {"type": "text", "id": "a", "required": True},
        {"type": "text", "id": "p", "password": True},
        {"type": "text", "id": "n", "remember": False},
        {"type": "choice", "id": "c", "options": ["x", "y"]},
    ])
    assert check_required(form, {"a": ""}).id == "a"
    assert check_required(form, {"a": "z"}) is None
    assert remembered_ids(form) == {"a", "c"}
    apply_remembered(form, {"a": "old", "c": "gone", "p": "secret"}, "f")
    values = {f.id: initial_value(f) for f in form.value_fields()}
    assert values["a"] == "old" and values["c"] == "x" and values["p"] == ""


def test_every_key_is_documented():
    documented = {d.key for d in FIELD_KEY_DOCS}
    used = set(COMMON_KEYS) | set(VALUE_KEYS) | {k for t in FIELD_TYPES for k in t.keys}
    assert used <= documented, used - documented
    assert {d.key for d in FORM_KEY_DOCS} == set(FORM_KEYS)


# ── Lua table reader / writer ────────────────────────────────────────────────


def test_tokenizer_decodes_strings_and_skips_comments():
    tokens = tokenize('-- c\n--[==[ long ]==] "a\\"b\\n\\65\\u{20AC}" [[\nraw]] 0x10 1e2 x')
    assert [t.value for t in tokens[:-1]] == ['a"b\nA€', "raw", 16, 100.0, "x"]


def test_parse_table_keeps_expressions_as_raw_lua():
    model = parse_table(
        '{ title = "T", width = -5, fields = { { type = "text", id = "a", label = "A" .. x,\n'
        '  on_change = function(f, v) if v then f:set("b", {1, 2}) end end } },\n'
        '  [ "odd key" ] = true, list = { 1, 2; 3 }, nothing = nil, mixed = { 1, k = 2 } }'
    )
    assert model["width"] == -5
    field = model["fields"][0]
    assert field["label"] == RawLua('"A" .. x')
    assert field["on_change"] == RawLua('function(f, v) if v then f:set("b", {1, 2}) end end')
    assert model["odd key"] is True and model["list"] == [1, 2, 3]
    assert model["nothing"] is None and model["mixed"] == {1: 1, "k": 2}


@pytest.mark.parametrize("text", ["{ a = }", "{ a = 1", "x = {}", "{ a = (1 }", '{ "unclosed }'])
def test_parse_table_refuses_broken_lua(text):
    with pytest.raises(LuaSyntaxError):
        parse_table(text)


def test_generated_lua_round_trips():
    model = {
        "title": 'Say "hi"\n', "layout": "absolute", "remember": True,
        "validate": RawLua("check"),
        "fields": [
            {"type": "group", "label": "G", "x": 0, "y": 0, "fields": [
                {"type": "radio", "id": "r", "options": ["a", "b"], "default": "a"}]},
            {"type": "number", "id": "n", "default": 2.5, "on_change": RawLua("function(f) end")},
        ],
        "buttons": [],
    }
    code = form_to_lua(model, "    ")
    assert parse_table(code) == model
    assert code.splitlines()[-1] == "    }"
    from lupa.lua54 import LuaRuntime
    LuaRuntime().compile(f"local check; return {code}")


def test_lua_string_escapes():
    assert lua_string('a"b\\c\n\x01é') == '"a\\"b\\\\c\\n\\001é"'


def test_find_form_table_picks_the_outer_form_around_the_cursor():
    source = (
        "local x = { 1, 2 }\n"
        "local v = opensak.form{\n"
        '  fields = { { type = "group", fields = { { type = "text", id = "t" } } } },\n'
        "}\n"
        "local w = opensak.form({ fields = {} })\n"
    )
    first = (source.index("{\n  fields"), source.index("}\nlocal w") + 1)
    assert find_form_table(source, source.index('id = "t"')) == first
    assert find_form_table(source, source.index("opensak.form{") + 2) == first
    assert find_form_table(source, 3) is None
    second = find_form_table(source, source.rindex("fields"))
    assert source[second[0]:second[1]] == "{ fields = {} }"
    assert find_form_tables(source) == [first, second]
    assert find_form_table('"unclosed', 0) is None


# ── GSAK import ──────────────────────────────────────────────────────────────


GSAK_MACRO = """
$exit = Form($form,"")
<Data> VarName=$form
#********************************************************************
# Form generated by GSAK form designer
#********************************************************************
Name = Form1
  Type = Form
  Caption = Export
  Delay = 30
  Height = 300
  Width = 400

Name = Group1
  Type = Groupbox
  Caption = Format
  Height = 80
  Left = 10
  Top = 10
  Width = 200

Name = rbGpx
  Type = Radiobutton
  Caption = GPX
  Container = Group1
  Left = 10
  Top = 20
  Width = 60
  Height = 17

Name = rbGgz
  Type = Radiobutton
  Caption = GGZ
  Container = Group1
  Left = 10
  Top = 40
  Width = 60
  Height = 17
  ExitOnChange = Yes

Name = Dish
  Type = Combobox
  Values = a*semi*b;c
  DirectInput = Yes
  Left = 220
  Top = 10
  Taborder = 1

Name = Hint
  Type = Label
  Caption = <b>Hello</b>
  FontColor = 255
  FontStyle = bold;italic
  Left = 10
  Top = 100

Name = btnOK
  Type = Button
  Caption = OK
  Enter = Yes
  Spellcheck = Memo1
  Left = 10
  Top = 200
  Taborder = 2

Name = Odd
  Type = Gauge
<enddata>
<data> VarName=$other
Name = F2
  Type = Form
<enddata>
"""


def test_gsak_forms_are_found_by_block():
    forms = find_gsak_forms(GSAK_MACRO)
    assert [(f.var, f.caption) for f in forms] == [("form", "Export"), ("other", "F2")]
    assert find_gsak_forms("Name = X\n Type = Form\n")[0].var == "form"
    assert find_gsak_forms("print('no form')") == []


def test_gsak_form_converts_to_an_absolute_form(monkeypatch):
    import opensak.lang as lang
    from opensak.lang.en import STRINGS
    monkeypatch.setattr(lang, "_translations", STRINGS)
    model, notes = convert(find_gsak_forms(GSAK_MACRO)[0])
    assert model["title"] == "Export" and model["layout"] == "absolute"
    assert (model["width"], model["height"], model["timeout"]) == (384, 261, 30)
    assert model["buttons"] == []
    by_id = {f["id"]: f for f in model["fields"]}
    assert list(by_id) == ["Dish", "btnOK", "Group1", "Hint"]  # tab order, then file order
    assert by_id["Dish"]["options"] == ["a;b", "c"] and by_id["Dish"]["editable"] is True
    assert by_id["btnOK"]["default"] is True
    assert by_id["Hint"] == {"type": "label", "id": "Hint", "label": "<b>Hello</b>", "x": 10,
                             "y": 100, "bold": True, "italic": True, "color": "#ff0000"}
    radio = by_id["Group1"]["fields"][0]
    assert radio == {"type": "radio", "id": "rbGpx", "options": ["GPX", "GGZ"], "x": 10,
                     "y": 20, "w": 60, "h": 37, "exit_on_change": True}
    text = "\n".join(notes)
    assert "Gauge" in text and "Spellcheck" in text and "rbGpx, rbGgz" in text
    assert "timeout" in text
    # The result is a valid form, also after a trip through Lua.
    parse_form(parse_table(form_to_lua(model)), is_function=_raw)


# ── Runtime, with a scripted host ────────────────────────────────────────────


class Scripted:
    """A FormController whose "user" is the test."""

    def __init__(self, form: Form, events):
        self.form = form
        self.events = events
        self.values = {f: initial_value(f) for f in form.value_fields()}
        self.closer: Any = "open"
        self.state: dict[tuple[str, str], Any] = {}
        self.title = form.title
        self.messages: list[str] = []

    def field(self, fid: str) -> Field:
        return self.form.by_id()[fid]

    # what the user does
    def change(self, fid: str, value: Any) -> None:
        field = self.field(fid)
        self.values[field] = value
        self.events(self, "change", field)
        if field.get("exit_on_change") and self.closer == "open":
            self.close_form(fid)

    def click(self, fid: str) -> None:
        field = self.field(fid)
        if field.get("on_click") is not None:
            self.events(self, "click", field)
        elif field.get("cancel"):
            self.close_form(None)
        else:
            message = self.events(self, "validate", field)
            if message:
                self.messages.append(message)
            elif self.closer == "open":
                self.close_form(fid)

    # FormController
    def value(self, field):
        return self.values[field]

    def set_value(self, field, value):
        self.values[field] = value

    def set_options(self, field):
        if self.values[field] not in field.options:
            self.values[field] = None

    def set_enabled(self, field, on):
        self.state[(field.id, "enabled")] = on

    def set_visible(self, field, on):
        self.state[(field.id, "visible")] = on

    def set_label(self, field):
        self.state[(field.id, "label")] = field.label

    def set_title(self, title):
        self.title = title

    def focus(self, field):
        self.state[(field.id, "focus")] = True

    def close_form(self, closer):
        if self.closer == "open":
            self.closer = closer


class BaseHost:
    """The host methods the `opensak` table binds when it is built."""

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def end_macro(self):
        pass

    def database_name(self):
        return "Test"

    def approve_macro_write(self, macro, change=None, editing=False):
        # set_corrected() in an example form asks before its first write
        return WriteApproval.SESSION


class UIHost(BaseHost):
    """A host with a user interface whose answers come from the test."""

    def __init__(self, user: Optional[Callable[[Scripted], None]] = None):
        self.user = user
        self.shown: list[Scripted] = []
        self.messages: list[tuple[str, str]] = []
        self.answers: list[Any] = []
        self.folder: Optional[Path] = None
        self.selected: list[str] = []
        self.refreshed = 0
        self.windows: list["Window"] = []

    def approve_folder(self, target, folder, write, macro=None):
        return FolderApproval.DENY

    def choose_file(self, title, file_filter, save, start_dir):
        return self.answers.pop(0)

    def message(self, text, title):
        self.messages.append((text, title))

    def input(self, prompt, default, title):
        self.messages.append((prompt, default))
        return self.answers.pop(0)

    def choose(self, prompt, options, default, title):
        self.messages.append((prompt, (options, default)))
        return self.answers.pop(0)

    def choose_folder(self, title, start_dir):
        return self.folder

    def show_form(self, form, events):
        controller = Scripted(form, events)
        self.shown.append(controller)
        events(controller, "open", None)
        if self.user is not None and controller.closer == "open":
            self.user(controller)
        if controller.closer == "open":
            controller.click("ok")
        if controller.closer is None:
            return None
        return {f.id: v for f, v in controller.values.items()}, controller.closer

    def progress(self, title, total):
        window = Window(title, total)
        self.windows.append(window)
        return window

    def select_cache(self, code):
        self.selected.append(code)
        return code == "GC1"

    def refresh(self):
        self.refreshed += 1


class Window:
    def __init__(self, title, total):
        self.title, self.total = title, total
        self.steps: list[tuple[Any, str]] = []
        self.closed = 0
        self.cancel_at: Optional[int] = None

    def update(self, value, text):
        self.steps.append((value, text))
        return self.cancel_at is None or value < self.cancel_at

    def close(self):
        self.closed += 1


def _run(host, script: str, **kwargs) -> list[str]:
    out: list[str] = []
    runtime = MacroRuntime(host, output=out.append, folder_permissions=[],
                           settings_dir=kwargs.pop("settings_dir", None))
    runtime.run(script, chunk_name="m.lua", **kwargs)
    return out


def test_ui_functions_fail_clearly_without_a_user_interface():
    class Headless(BaseHost):
        pass

    for call in ('opensak.message("x")', 'opensak.form{ fields = {} }',
                 'opensak.progress("x")', 'opensak.select("GC1")', "opensak.refresh()"):
        with pytest.raises(MacroError, match="no user interface"):
            _run(Headless(), call)


def test_message_input_and_choose():
    host = UIHost()
    host.answers = ["typed", None, 1, None]
    out = _run(host, """
        opensak.message("Hello", "Title")
        print(opensak.input("Name?", "def"))
        print(opensak.input("Again?"))
        print(opensak.choose("Pick", {"a", 2, "c"}, 2))
        print(opensak.choose("Pick", {"a"}))
    """)
    assert out == ["typed", "nil", "2", "nil"]
    assert host.messages[0] == ("Hello", "Title")
    assert host.messages[3] == ("Pick", (["a", "2", "c"], 1))


@pytest.mark.parametrize("script, message", [
    ('opensak.choose("x", {})', "expects options"),
    ('opensak.choose("x", {"a"}, "b")', "default b is not one of the options"),
    ("opensak.message({})", "text must be a string"),
])
def test_simple_dialog_errors(script, message):
    with pytest.raises(MacroError, match=message):
        _run(UIHost(), script)


def test_choose_folder_grants_read_and_write_for_the_run(tmp_path):
    folder = tmp_path / "out"
    folder.mkdir()
    (folder / "a.csv").write_text("code\nGC1\n", encoding="utf-8")
    host = UIHost()
    host.folder = folder
    out: list[str] = []
    runtime = MacroRuntime(host, output=out.append, folder_permissions=[])
    runtime.run('local f = opensak.choose_folder("Where")\n'
                'print(f, #opensak.read_csv(f .. "/a.csv"))')
    assert out == [f"{folder.resolve()}\t1"]
    granted = [p for p in runtime._run_permissions if Path(p.path) == folder.resolve()]
    assert [(p.read, p.write) for p in granted] == [(True, True)]


def test_choose_folder_refuses_a_drive_root(tmp_path):
    host = UIHost()
    host.folder = Path(tmp_path.anchor)
    with pytest.raises(MacroError, match="whole drive"):
        _run(host, "opensak.choose_folder()")


def test_form_returns_values_and_button():
    host = UIHost()
    out = _run(host, """
        local v, button = opensak.form{
          title = "T",
          fields = {
            { type = "text", id = "name", default = "x" },
            { type = "checklist", id = "list", options = {"a", "b"}, default = {"b"} },
            { type = "label", label = "hi" },
          },
        }
        print(button, v.name, #v.list, v.list[1])
    """)
    assert out == ["ok\tx\t1\tb"]


def test_cancel_returns_nil():
    host = UIHost(lambda c: c.click("cancel"))
    assert _run(host, 'print(opensak.form{ fields = { { type = "text", id = "a" } } })') == ["nil"]


def test_callbacks_change_the_open_form():
    def user(c: Scripted) -> None:
        c.change("fmt", "GGZ")
        c.click("apply")

    host = UIHost(user)
    out = _run(host, """
        local v, b = opensak.form{
          on_open = function(f) f:set_title("Opened") end,
          fields = {
            { type = "choice", id = "fmt", options = {"GPX", "GGZ"}, default = "GPX",
              on_change = function(f, value, id)
                f:set_options("kind", value == "GGZ" and {"x", "y"} or {"a"})
                f:set("count", 3)
                f:set_enabled("count", false)
                f:set_visible("note", false)
                f:set_label("note", id .. "=" .. value)
                f:focus("count")
              end },
            { type = "radio", id = "kind", options = {"a"}, default = "a" },
            { type = "number", id = "count" },
            { type = "label", id = "note", label = "" },
            { type = "button", id = "apply", label = "Apply",
              on_click = function(f, id) f:close(id .. "!") end },
          },
        }
        print(b, v.fmt, v.kind, v.count)
    """)
    assert out == ["apply!\tGGZ\tnil\t3"]
    c = host.shown[0]
    assert c.title == "Opened"
    assert c.field("kind").options == ["x", "y"]
    assert c.state == {("count", "enabled"): False, ("note", "visible"): False,
                       ("note", "label"): "fmt=GGZ", ("count", "focus"): True}


def test_exit_on_change_returns_the_field_id():
    host = UIHost(lambda c: c.change("dish", "Lamb"))
    out = _run(host, """
        local v, b = opensak.form{ fields = {
          { type = "choice", id = "dish", options = {"None", "Lamb"}, exit_on_change = true } } }
        print(b, v.dish)
    """)
    assert out == ["dish\tLamb"]


def test_validate_keeps_the_form_open_with_a_message():
    def user(c: Scripted) -> None:
        c.click("ok")
        c.values[c.field("n")] = 5
        c.click("ok")

    host = UIHost(user)
    out = _run(host, """
        local v = opensak.form{
          validate = function(f, button)
            if f:get("n") < 3 then return "n must be at least 3 (" .. button .. ")" end
          end,
          fields = { { type = "number", id = "n" } },
        }
        print(v.n)
    """)
    assert out == ["5"]
    assert host.shown[0].messages == ["n must be at least 3 (ok)"]


def test_an_error_in_a_callback_stops_the_macro_with_its_line():
    host = UIHost(lambda c: c.change("a", "x"))
    with pytest.raises(MacroError, match=r"m\.lua:3: boom"):
        _run(host, """
            opensak.form{ fields = { { type = "text", id = "a",
              on_change = function(f) error("boom") end } } }
            print("not reached")
        """)
    assert host.shown[0].closer is None


@pytest.mark.parametrize("call, message", [
    ('f:get("nope")', "no field with id nope"),
    ('f.get("a")', "with a colon"),
    ('f:set("a", {})', "must be a string"),
    ('f:set_enabled("a", 1)', "expects true or false"),
    ('f:set_options("a", {"x"})', "without options"),
    ('f:get("l")', "without a value"),
])
def test_form_handle_errors(call, message):
    host = UIHost(lambda c: c.change("a", "x"))
    script = (
        "opensak.form{ fields = { { type = \"label\", id = \"l\", label = \"\" },\n"
        f"  {{ type = \"text\", id = \"a\", on_change = function(f) {call} end }} }} }}"
    )
    with pytest.raises(MacroError, match=message):
        _run(host, script)


def test_remember_restores_the_last_values(tmp_path):
    macro = tmp_path / "m.lua"
    script = """
        local v = opensak.form{ id = "main", remember = true, fields = {
          { type = "text", id = "a", default = "start" },
          { type = "text", id = "pw", password = true },
          { type = "text", id = "skip", remember = false },
        } }
        if not v then print("cancelled") return end
        print(v.a, v.pw, v.skip)
    """

    def user(c: Scripted) -> None:
        for fid in ("a", "pw", "skip"):
            c.values[c.field(fid)] = fid + "!"

    first = _run(UIHost(user), script, settings_dir=tmp_path / "s", macro_path=macro)
    assert first == ["a!\tpw!\tskip!"]
    second = _run(UIHost(), script, settings_dir=tmp_path / "s", macro_path=macro)
    assert second == ["a!\t\t"]
    stored = tmp_path / "s" / ".forms" / "m.json"
    assert stored.exists()
    # Cancelling keeps what was stored.
    _run(UIHost(lambda c: c.click("cancel")), script, settings_dir=tmp_path / "s",
         macro_path=macro)
    assert _run(UIHost(), script, settings_dir=tmp_path / "s", macro_path=macro) == ["a!\t\t"]


def test_remember_in_an_unsaved_macro_just_uses_the_defaults():
    out = _run(UIHost(), 'print(opensak.form{ remember = true, fields = {'
                         ' { type = "text", id = "a", default = "d" } } }.a)')
    assert out == ["d"]


def test_image_from_base64_and_bad_data():
    host = UIHost()
    _run(host, 'opensak.form{ fields = { { type = "image", data = "aGVsbG8=" } } }')
    assert host.shown[0].form.fields[0].get("image") == b"hello"
    with pytest.raises(MacroError, match="not valid base64"):
        _run(UIHost(), 'opensak.form{ fields = { { type = "image", data = "a" } } }')


def test_form_file_browse_grants_the_picked_file(tmp_path):
    csv = tmp_path / "in.csv"
    csv.write_text("code\nGC1\nGC2\n", encoding="utf-8")

    def user(c: Scripted) -> None:
        c.events(c, "browse", c.field("file"))

    host = UIHost(user)
    host.answers = [csv]
    out = _run(host, """
        local v = opensak.form{ fields = { { type = "file", id = "file", filter = "*.csv" } } }
        print(#opensak.read_csv(v.file))
    """)
    assert out == ["2"]


def test_progress_updates_cancels_and_closes():
    host = UIHost()
    out = _run(host, """
        local p = opensak.progress("Work", 10)
        for i = 1, 10 do
          if not p:update(i, "step " .. i) then print("cancelled at " .. i) break end
        end
        local q = opensak.progress("Busy")
        q:update()
        p:close()
        p:close()
    """)
    assert out == []
    work, busy = host.windows
    assert (work.total, busy.total) == (10, 0)
    assert work.steps[0] == (1, "step 1") and len(work.steps) == 10
    assert work.closed == 1 and busy.closed == 1  # q closed when the macro ended

    host = UIHost()
    original = host.progress

    def cancelling(title, total):
        window = original(title, total)
        window.cancel_at = 3
        return window

    host.progress = cancelling  # type: ignore[method-assign]
    out = _run(host, """
        local p = opensak.progress("Work", 10)
        for i = 1, 10 do
          if not p:update(i) then print("cancelled at " .. i) break end
        end
    """)
    assert out == ["cancelled at 3"]


def test_progress_errors():
    with pytest.raises(MacroError, match="whole number"):
        _run(UIHost(), 'opensak.progress("x", -1)')
    with pytest.raises(MacroError, match="with a colon"):
        _run(UIHost(), 'local p = opensak.progress("x") p.update(1)')
    with pytest.raises(MacroError, match="closed"):
        _run(UIHost(), 'local p = opensak.progress("x") p:close() p:update(1)')


def test_select_and_refresh():
    host = UIHost()
    out = _run(host, 'print(opensak.select("gc1"), opensak.select("GC2")) opensak.refresh()')
    assert out == ["true\tfalse"]
    assert host.selected == ["GC1", "GC2"] and host.refreshed == 1


# ── The shipped example, against a real database ─────────────────────────────


class DbUIHost(UIHost):
    """Filters the test database like the main window does."""

    def __init__(self, user=None):
        super().__init__(user)
        from opensak.filters.engine import FilterSet
        self.filterset = FilterSet()

    def apply_filter(self, filterset, label):
        from opensak.db.database import get_session
        from opensak.filters.engine import apply_filters_auto
        with get_session() as s:
            count = len(apply_filters_auto(s, filterset))
        if count:
            self.filterset = filterset
        return count

    def filtered_caches(self):
        from opensak.db.database import get_session
        from opensak.filters.engine import apply_filters_auto
        with get_session() as s:
            return apply_filters_auto(s, self.filterset)


def test_find_with_form_example(tmp_db):
    from opensak.db.database import get_session
    from opensak.db.models import Cache

    with get_session() as s:
        for code, ctype, diff, found, favs in [
            ("GCF1", "Traditional Cache", 1.5, False, 3),
            ("GCF2", "Traditional Cache", 2.0, False, 12),
            ("GCF3", "Traditional Cache", 4.0, False, 50),   # too difficult
            ("GCF4", "Traditional Cache", 1.0, True, 99),    # found
            ("GCF5", "Multi-cache", 1.0, False, 70),         # other type
        ]:
            s.add(Cache(gc_code=code, name=f"Cache {code}", cache_type=ctype, difficulty=diff,
                        terrain=1.0, found=found, favorite_points=favs,
                        latitude=47.0, longitude=8.0))
        s.commit()

    source = (Path(__file__).parents[2] / "macros/examples/find_with_form.lua").read_text(
        encoding="utf-8")
    host = DbUIHost()
    _run(host, source)
    form = host.shown[0]
    assert form.state[("km", "enabled")] is False  # on_open: "near" is not ticked
    assert host.selected == ["GCF2"]
    assert host.messages[-1] == ("2 caches match. Most favourite points (12): GCF2 Cache GCF2",
                                 "Find caches")
    assert [step[0] for step in host.windows[0].steps] == [1, 2]
    assert host.windows[0].closed == 1


def test_errors_name_the_line_of_the_macro():
    with pytest.raises(MacroError, match=r"^m\.lua:3: opensak\.form: fields\[1\]: unknown field type"):
        _run(UIHost(), "\n\nopensak.form{ fields = { { type = 'slider' } } }")
    host = UIHost(lambda c: c.change("a", "x"))
    with pytest.raises(MacroError, match=r"^m\.lua:2: opensak\.form: f:get: no field"):
        _run(host, "opensak.form{ fields = { { type = 'text', id = 'a', on_change = function(f)\n"
                   "  f:get('b') end } } }")


def test_export_with_form_example(tmp_db, tmp_path):
    from opensak.db.database import get_session
    from opensak.db.models import Cache

    with get_session() as s:
        for code, diff in (("GCX1", 1.5), ("GCX2", 3.0)):
            s.add(Cache(gc_code=code, name=f"Cache {code}", cache_type="Traditional Cache",
                        difficulty=diff, terrain=2.0, found=False,
                        latitude=47.0, longitude=8.0))
        s.commit()

    class Host(DbUIHost):
        def database_name(self):
            return "Test"

        def filter_name(self):
            return "Mine"

        def center_name(self):
            return ""

        def cache_count(self):
            return len(self.filtered_caches())

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def user(c: Scripted) -> None:
        c.click("export")                    # no folder yet: validate refuses
        c.events(c, "browse", c.field("folder"))
        c.values[c.field("dt")] = True
        c.change("format", "loc")            # disables the POI options
        c.click("recount")                   # only updates the label
        c.click("export")

    host = Host(user)
    host.folder = out_dir
    host.apply_filter(build_test_filter(), "Mine")
    source = (Path(__file__).parents[2] / "macros/examples/export_with_form.lua").read_text(
        encoding="utf-8")
    _run(host, source, settings_dir=tmp_path / "s", macro_path=tmp_path / "m.lua")

    form = host.shown[0]
    assert form.messages == ["Please choose an output folder."]
    assert form.state[("pois", "enabled")] is False
    assert form.state[("count", "label")].startswith("2 caches")
    written = out_dir / "Mine.loc"
    assert host.messages[-1] == (f"2 caches written to\n{written.resolve()}", "Export")
    text = written.read_text(encoding="utf-8")
    assert "1.5/2 Cache GCX1" in text and "3/2 Cache GCX2" in text


def build_test_filter():
    from opensak.filters.engine import FilterSet, GcCodeFilter
    return FilterSet().add(GcCodeFilter("GCX1;GCX2", op="in_list"))


EXAMPLES = Path(__file__).parents[2] / "macros/examples"


def test_corrected_coords_form_is_unchanged_designer_output():
    """The example's form is the designer's own output: reading it and
    writing it back gives the same text, so editing it is lossless."""
    source = (EXAMPLES / "corrected_coords_form.lua").read_text(encoding="utf-8")
    start, end = find_form_table(source, source.index("= opensak.form{") + 4)
    assert form_to_lua(parse_table(source[start:end])) == source[start:end]


def test_corrected_coords_form_example(tmp_db):
    from opensak.db.corrected_coords import set_corrected_coords
    from opensak.db.database import get_session
    from opensak.db.models import Cache
    from opensak.macro.cache_data import load_records

    with get_session() as s:
        s.add(Cache(gc_code="GCCC1", name="Puzzle", cache_type="Unknown Cache",
                    difficulty=3.0, terrain=1.0, latitude=47.0, longitude=8.0))
        s.commit()

    class Host(UIHost):
        def current_code(self):
            return "GCCC1"

        def set_corrected_coords(self, code, lat, lon):
            return set_corrected_coords(code, lat, lon)

    def user(c: Scripted) -> None:
        c.change("coords", "nonsense")
        c.click("ok")                                  # refused: cannot be read
        c.change("coords", "N47 01.000 E008 00.000")
        c.click("ok")

    source = (EXAMPLES / "corrected_coords_form.lua").read_text(encoding="utf-8")
    host = Host(user)
    out = _run(host, source)
    form = host.shown[0]
    assert form.state[("cache", "label")] == "GCCC1 — Puzzle"
    assert form.state[("check", "label")].startswith("✓ N47 01.000")
    assert "1.85 km" in form.state[("check", "label")]
    assert form.messages == ["Please enter coordinates OpenSAK can read, e.g. N47 22.123 E008 32.456."]
    assert out == ["GCCC1: corrected coordinates set to N47 01.000 E008 00.000"]
    with get_session() as s:
        corrected = load_records(s, ["GCCC1"])[0]["corrected"]
    assert corrected == {"lat": pytest.approx(47 + 1 / 60), "lon": pytest.approx(8.0)}

    # Second run: the form starts with the stored coordinates; removing them.
    host = Host(lambda c: (c.change("clear", True), c.click("ok")))
    assert _run(host, source) == ["GCCC1: corrected coordinates removed"]
    assert host.shown[0].values[host.shown[0].field("coords")].startswith("N47 01.000")
    assert host.shown[0].state[("coords", "enabled")] is False


class PuzzleHost(UIHost):
    """The selected cache is *code*; writes are allowed."""

    def __init__(self, code, user):
        super().__init__(user)
        self.code = code

    def current_code(self):
        return self.code

    def caches_changed(self, codes, added):
        pass


def test_puzzle_helper_example(tmp_db):
    """puzzle_helper.lua: one form whose buttons write their results into the
    output box — solving a formula, saving it as a waypoint, the coordinates
    from the listing and the tools."""
    from opensak.db.database import get_session
    from opensak.db.models import Cache

    with get_session() as s:
        s.add(Cache(gc_code="GCPH1", name="Puzzle", cache_type="Unknown Cache",
                    latitude=47.0, longitude=8.0, encoded_hints="haqre gur fgbar",
                    long_description="<p>Start: N47° 00.500' E008° 00.500'</p>"
                                     "<p>Final: N47 0A.BCD O008 00.EFG</p>"
                                     "<p>Year 1987, check with GeoCheck</p>",
                    long_desc_html=True))
        s.commit()

    shown: dict[str, str] = {}

    def user(c: Scripted) -> None:
        def press(button: str, as_: Optional[str] = None) -> None:
            c.click(button)
            shown[as_ or button] = c.values[c.field("output")]

        shown["intro"] = c.values[c.field("output")]
        assert c.values[c.field("letters")] == "A=, B=, C=, D=, E=, F=, G="
        c.change("letters", "A=1, b = ab, C=4, D=5, E=1, F=2")
        press("solve", "missing")
        c.change("letters", "A=1, b = ab, C=4, D=5, E=1, F=2, G=3")
        press("solve")
        c.change("wpname", "Final")
        press("save")
        press("show")
        c.change("input", "1987")
        press("digit")
        c.change("input", "Geo cache")
        press("word")
        press("rot13")
        press("numbers")
        press("hint")
        c.change("result", "nonsense")
        press("save", "bad save")
        c.click("close")

    host = PuzzleHost("GCPH1", user)
    out = _run(host, (EXAMPLES / "puzzle_helper.lua").read_text(encoding="utf-8"))
    assert host.shown[0].closer is None                     # Close cancels the form
    assert out == ["GCPH1: waypoint FN (Final Location) saved at N47 01.345  E008 00.123"]
    assert shown["intro"].startswith("1 coordinate and 1 formula found in the listing.\n"
                                     "The listing mentions GeoCheck: test your result there.")
    assert shown["missing"].endswith("Still missing: G")
    assert shown["solve"].startswith(
        "── Formula ──\n\nN 47 0A.BCD E 008 00.EFG\nA = 1\nB = 3   (word value of ab)\nC = 4\n")
    assert "= N 47 01.345 E 008 00.123\n\nN47 01.345  E008 00.123\n" in shown["solve"]
    assert "2.50 km from the posted coordinates, bearing 4°" in shown["solve"]
    assert shown["save"].endswith("saved at N47 01.345  E008 00.123\n"
                                  "N 47 0A.BCD E 008 00.EFG with A=1, B=3, C=4, D=5, E=1, F=2, G=3")
    assert shown["show"].startswith("── From the listing ──\n\nN 47° 00.500 E 008° 00.500\n\n")
    assert shown["digit"].endswith("Digit sum: 25\nRepeated:  25 → 7")
    assert shown["word"].endswith("Total                  47   (digit sum 11)")
    assert shown["rot13"].endswith("Trb pnpur")
    assert "        1987   digit sum 25" in shown["numbers"]
    assert shown["hint"].endswith("under the stone")
    assert shown["bad save"].endswith("into the Coordinates field first.")

    out = _run(PuzzleHost("GCPH1", None),
               'for _, w in ipairs(opensak.waypoints("GCPH1")) do '
               'print(w.prefix, w.type, w.name, w.comment) end')
    assert out == ["FN\tFinal Location\tFinal\tPuzzle helper: N 47 0A.BCD E 008 00.EFG with "
                   "A=1, B=3, C=4, D=5, E=1, F=2, G=3"]


def test_puzzle_helper_finds_hidden_coordinates(tmp_db):
    """Coordinates in white or CSS-hidden text, HTML comments, attributes
    (URL-encoded too) and <script> blocks are listed once each, with where
    they were hidden; a hidden formula is offered as well."""
    from opensak.db.database import get_session
    from opensak.db.models import Cache

    with get_session() as s:
        s.add(Cache(gc_code="GCPH2", name="Hidden", cache_type="Unknown Cache",
                    latitude=47.0, longitude=8.0, long_desc_html=True, long_description=(
                        '<p style="color:#fff">N47 01.000 E008 01.000</p>'
                        '<span style="display:none">N47 02.000 E008 02.000</span>'
                        '<!-- N47 03.000 E008 03.000 -->'
                        '<img alt="N47 04.000 E008 04.000" src="x.png">'
                        '<a href="https://example.com/?q=N47%2005.000%20E008%2005.000">map</a>'
                        '<script>var f = "N47 0A.BCD E008 0E.FGH";</script>'
                        '<!-- N47 01.000 E008 01.000 -->')))   # listed already
        s.commit()

    def user(c: Scripted) -> None:
        c.change("found", "N 47 03.000 E 008 03.000  (in an HTML comment)")
        c.click("show")
        c.click("close")

    host = PuzzleHost("GCPH2", user)
    _run(host, (EXAMPLES / "puzzle_helper.lua").read_text(encoding="utf-8"))
    form = host.shown[0]
    assert form.field("found").options == [
        "N 47 01.000 E 008 01.000",
        "N 47 02.000 E 008 02.000",
        "N 47 03.000 E 008 03.000  (in an HTML comment)",
        "N 47 04.000 E 008 04.000  (in the alt of a tag)",
        "N 47 05.000 E 008 05.000  (in the href of a tag)",
    ]
    assert form.field("formula").options == ["N 47 0A.BCD E 008 0E.FGH  (in a <script> block)"]
    assert form.values[form.field("result")] == "N47 03.000  E008 03.000"


def test_puzzle_helper_without_coordinates_or_hint(tmp_db):
    """Without coordinates, formulas or hint the choices are greyed out and
    the buttons still answer in the output box."""
    from opensak.db.database import get_session
    from opensak.db.models import Cache

    with get_session() as s:
        s.add(Cache(gc_code="GCPH3", name="Plain", cache_type="Unknown Cache",
                    latitude=47.0, longitude=8.0, long_description="No numbers here."))
        s.commit()

    def user(c: Scripted) -> None:
        assert c.values[c.field("output")].startswith("0 coordinates and 0 formulas")
        c.click("hint")
        assert c.values[c.field("output")].endswith("This cache has no hint.")
        c.click("numbers")
        assert c.values[c.field("output")].endswith("The listing contains no numbers.")
        c.click("close")

    host = PuzzleHost("GCPH3", user)
    _run(host, (EXAMPLES / "puzzle_helper.lua").read_text(encoding="utf-8"))
    form = host.shown[0]
    assert form.field("found").options == ["(none found)"]
    assert form.field("found").get("enabled") is False
    assert form.field("solve").get("enabled") is False


class ReportHost(DbUIHost):
    """Allows the writes to the temp folder that _run's empty folder
    permissions would refuse."""

    def approve_folder(self, target, folder, write, macro=None):
        return FolderApproval.ONCE


def test_sql_report_example(tmp_db, tmp_path, monkeypatch):
    """sql_report.lua: the result in query column order, a typing error shown
    without ending the macro, and one report file per loop value."""
    import tempfile

    from opensak.db.database import get_session
    from opensak.db.models import Cache

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with get_session() as s:
        for code, country, found in [("GCR1", "Denmark", True), ("GCR2", "Denmark", False),
                                     ("GCR3", "Sweden", False), ("GCR4", None, False)]:
            s.add(Cache(gc_code=code, name=f"Cache {code}", cache_type="Traditional Cache",
                        country=country, found=found, latitude=55.0, longitude=12.0))
        s.commit()

    steps = iter([
        lambda c: c.click("run"),                     # the example query
        lambda c: c.click("edit"),
        lambda c: (c.change("query", "SELEKT 1"), c.click("run")),
        lambda c: (c.change("query", "SELECT name, gc_code FROM caches "
                                     "WHERE country = :value ORDER BY gc_code;"),
                   c.change("loop", "SELECT DISTINCT country FROM caches ORDER BY 1"),
                   c.click("run")),
        lambda c: c.click("close"),
    ])
    host = ReportHost(lambda c: next(steps)(c))
    out = _run(host, (EXAMPLES / "sql_report.lua").read_text(encoding="utf-8"),
               settings_dir=tmp_path / "s", macro_path=tmp_path / "m.lua")

    first = host.shown[1].form.fields[0].get("html")
    assert first.index("country") < first.index("caches") < first.index("found")
    assert '<td>Denmark</td><td class="num">2</td><td class="num">1</td>' in first
    assert host.messages == [('SQL error: near "1": syntax error', "SQL report")]
    loop = host.shown[4].form.fields[0].get("html")
    assert loop.index("<h2>Denmark</h2>") < loop.index("GCR2") < loop.index("<h2>Sweden</h2>")
    assert loop.index("name") < loop.index("gc_code")
    assert out[0].startswith("Report: ") and "(empty): skipped" in out   # no country: NULL
    files = {line.split(": ", 1)[0]: Path(line.split(": ", 1)[1]) for line in out}
    assert files["Denmark"].parent == tmp_path / "opensak"
    assert len(set(files.values())) == len(files)                        # each its own file
    assert "GCR3" in files["Sweden"].read_text(encoding="utf-8")
    assert "GCR3" not in files["Denmark"].read_text(encoding="utf-8")
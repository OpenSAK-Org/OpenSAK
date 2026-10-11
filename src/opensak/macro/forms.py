"""
src/opensak/macro/forms.py — the form spec of opensak.form{} (#982).

A macro describes its dialog as a Lua table; this module validates that
table into a Form (fields, buttons, callbacks) and converts values between
Lua and the form. It knows nothing about Qt: the host builds the dialog
(gui/dialogs/macro_form.py) and reports what the user does through a
FormController and an event function, so the runtime can be tested with a
fake host and the form designer can reuse the same validation.

Two layouts:
  * "auto" (default): fields are stacked top to bottom, with their label in
    a column on the left; a group is a framed section with its own fields.
  * "absolute": every field has x, y (and optionally w, h) in pixels, like
    GSAK's form designer; positions inside a group are relative to it.

Events replace GSAK's ExitOnChange round trips: a field's on_change and a
button's on_click run while the dialog stays open and can change the form
through the handle they get (see FORM_METHOD_DOCS). `exit_on_change` still
closes the form like in GSAK, for macros ported one to one.

The documentation registries at the end (FIELD_TYPES, FORM_KEY_DOCS,
FIELD_KEY_DOCS, FORM_METHOD_DOCS) are rendered into docs/macros/api.md and
the Lua Language Server stub by api_docs.py.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterator, Optional, Protocol

from opensak.macro.errors import MacroError

LAYOUTS = ("auto", "absolute")
ALIGNMENTS = ("left", "center", "right")
FILE_MODES = ("open", "save")
# Types whose value is part of the result table.
VALUE_TYPES = frozenset({
    "text", "number", "memo", "checkbox", "choice", "radio", "checklist",
    "date", "file", "folder",
})
OPTION_TYPES = frozenset({"choice", "radio", "checklist"})
# Keys every field takes.
COMMON_KEYS = frozenset({
    "type", "id", "label", "tooltip", "enabled", "visible", "x", "y", "w", "h",
    "bold", "italic", "color", "font_size",
})
# Keys of fields with a value.
VALUE_KEYS = frozenset({"default", "on_change", "exit_on_change", "remember"})
FORM_KEYS = frozenset({
    "id", "title", "layout", "width", "height", "fields", "buttons", "remember",
    "timeout", "on_open", "validate",
})
# x, y, w, h place a button among the fields; the button row ignores them.
BUTTON_KEYS = COMMON_KEYS | {"default", "cancel", "on_click"}
# Closing ids the form itself returns (not a button or field id).
TIMEOUT = "timeout"
_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COLOR = re.compile(r"^(#[0-9a-fA-F]{6}|[A-Za-z]+)$")
# Upper bounds that keep a broken spec from building a giant dialog.
MAX_FIELDS = 500
MAX_OPTIONS = 5000
MAX_SIZE = 4000
MAX_DEPTH = 10


@dataclass(frozen=True)
class RawLua:
    """A Lua expression kept as source text, e.g. a callback or a variable
    in a form the designer read from a macro. It is written back unchanged
    and never evaluated by Python."""

    text: str


@dataclass(eq=False)
class Field:
    """One validated field (or button) of a form."""

    type: str
    id: Optional[str]
    label: str
    # Where the field is in the spec, for error messages: "fields[2]".
    where: str
    # The other validated keys (type-specific ones, callbacks, default, …)
    props: dict[str, Any] = field(default_factory=dict)
    children: list["Field"] = field(default_factory=list)
    options: list[Any] = field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        return self.props.get(key, default)

    @property
    def has_value(self) -> bool:
        return self.type in VALUE_TYPES

    @property
    def name(self) -> str:
        """For messages: `id` if there is one, else the position."""
        return self.id or self.where


@dataclass(eq=False)
class Form:
    """A validated opensak.form{} spec."""

    title: str = ""
    id: Optional[str] = None
    layout: str = "auto"
    width: Optional[int] = None
    height: Optional[int] = None
    fields: list[Field] = field(default_factory=list)
    buttons: list[Field] = field(default_factory=list)
    remember: bool = False
    timeout: Optional[float] = None
    on_open: Any = None
    validate: Any = None

    def walk(self) -> Iterator[Field]:
        """All fields (group contents included), then the buttons."""

        def visit(fields: list[Field]) -> Iterator[Field]:
            for f in fields:
                yield f
                yield from visit(f.children)

        yield from visit(self.fields)
        yield from self.buttons

    def by_id(self) -> dict[str, Field]:
        return {f.id: f for f in self.walk() if f.id}

    def value_fields(self) -> list[Field]:
        return [f for f in self.walk() if f.has_value]


class FormController(Protocol):
    """What the host's dialog lets the runtime do while it is open. The
    runtime validates every value before passing it on."""

    def value(self, field: Field) -> Any:
        """The current value of a value field."""

    def set_value(self, field: Field, value: Any) -> None:
        """Show *value* (already coerced) without firing on_change."""

    def set_options(self, field: Field) -> None:
        """Rebuild the options from field.options (already updated)."""

    def set_enabled(self, field: Field, on: bool) -> None: ...

    def set_visible(self, field: Field, on: bool) -> None: ...

    def set_label(self, field: Field) -> None:
        """Show field.label (already updated)."""

    def set_title(self, title: str) -> None: ...

    def focus(self, field: Field) -> None: ...

    def close_form(self, closer: Optional[str]) -> None:
        """Close the dialog: with a button / field id, or None = cancelled."""


# (controller, event, field) → for "validate" an error message or None.
# Events: "open", "change", "click", "browse", "validate".
FormEvents = Callable[[FormController, str, Optional[Field]], Optional[str]]


# ── Parsing ──────────────────────────────────────────────────────────────────


class _Parser:
    def __init__(
        self,
        func: str,
        is_function: Callable[[Any], bool],
        lenient: bool,
        ok_label: str,
        cancel_label: str,
    ):
        self.func = func
        self.is_function = is_function
        # Designer preview: RawLua values outside callbacks are dropped
        # instead of refused.
        self.lenient = lenient
        self.ok_label = ok_label
        self.cancel_label = cancel_label
        self.ids: dict[str, str] = {}
        self.count = 0

    def error(self, where: str, message: str) -> MacroError:
        place = f" {where}" if where else ""
        return MacroError(f"{self.func}:{place}: {message}" if place else f"{self.func}: {message}")

    # -- scalar checks -----------------------------------------------------

    def text(self, where: str, key: str, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return format_number(value)
        raise self.error(where, f"{key} must be a string, got {_show(value)}")

    def flag(self, where: str, key: str, value: Any) -> bool:
        if not isinstance(value, bool):
            raise self.error(where, f"{key} must be true or false, got {_show(value)}")
        return value

    def size(self, where: str, key: str, value: Any, low: int = 0, high: int = MAX_SIZE) -> int:
        number = _number(value)
        if number is None or number != int(number) or not low <= number <= high:
            raise self.error(where, f"{key} must be a whole number from {low} to {high}, got {_show(value)}")
        return int(number)

    def real(self, where: str, key: str, value: Any) -> float:
        number = _number(value)
        if number is None or not math.isfinite(number):
            raise self.error(where, f"{key} must be a number, got {_show(value)}")
        return number

    def choice(self, where: str, key: str, value: Any, allowed: tuple[str, ...]) -> str:
        if value not in allowed:
            shown = ", ".join(f'"{a}"' for a in allowed)
            raise self.error(where, f"{key} must be one of {shown}, got {_show(value)}")
        return value

    def callback(self, where: str, key: str, value: Any) -> Any:
        if not self.is_function(value):
            raise self.error(where, f"{key} must be a function, got {_show(value)}")
        return value

    # -- spec ----------------------------------------------------------------

    def form(self, spec: Any) -> Form:
        if not isinstance(spec, dict):
            raise self.error("", 'expects a table, e.g. opensak.form{ title = "…", fields = {…} }')
        spec = self.drop_raw(spec, {"on_open", "validate"})
        self.unknown("", spec, FORM_KEYS, "form")
        form = Form()
        if "title" in spec:
            form.title = self.text("", "title", spec["title"])
        if "id" in spec:
            form.id = self.text("", "id", spec["id"])
        if "layout" in spec:
            form.layout = self.choice("", "layout", spec["layout"], LAYOUTS)
        for key in ("width", "height"):
            if key in spec:
                setattr(form, key, self.size("", key, spec[key], 50))
        if "remember" in spec:
            form.remember = self.flag("", "remember", spec["remember"])
        if "timeout" in spec:
            timeout = self.real("", "timeout", spec["timeout"])
            if timeout <= 0:
                raise self.error("", f"timeout must be > 0 seconds, got {_show(spec['timeout'])}")
            form.timeout = timeout
        for key in ("on_open", "validate"):
            if key in spec:
                setattr(form, key, self.callback("", key, spec[key]))
        fields = spec.get("fields", [])
        if self.lenient and isinstance(fields, RawLua):
            fields = []
        form.fields = self.field_list("fields", fields, 0)
        if "buttons" in spec:
            buttons = spec["buttons"]
            if buttons == {}:  # an empty Lua table converts to {}
                buttons = []
            if not isinstance(buttons, list):
                raise self.error("buttons", "must be an array of buttons")
            form.buttons = [self.button(f"buttons[{i}]", b) for i, b in enumerate(buttons, 1)
                            if not (self.lenient and isinstance(b, RawLua))]
        else:
            form.buttons = [
                self.button("buttons[1]", {"id": "ok", "label": self.ok_label, "default": True}),
                self.button("buttons[2]", {"id": "cancel", "label": self.cancel_label,
                                           "cancel": True}),
            ]
        return form

    def unknown(self, where: str, spec: dict, valid: frozenset[str], what: str) -> None:
        unknown = [k for k in spec if k not in valid]
        if unknown:
            shown = ", ".join(sorted(str(k) for k in unknown))
            raise self.error(where, f"unknown key(s) {shown} for {what}; valid: "
                                    + ", ".join(sorted(valid)))

    def drop_raw(self, spec: dict, callbacks: set[str]) -> dict:
        if not self.lenient:
            return spec
        return {k: v for k, v in spec.items()
                if not isinstance(v, RawLua) or k in callbacks}

    def field_list(self, where: str, value: Any, depth: int) -> list[Field]:
        if value == {}:
            return []
        if not isinstance(value, list):
            raise self.error(where, "must be an array of fields, e.g. { { type = \"text\", id = \"name\" } }")
        if depth > MAX_DEPTH:
            raise self.error(where, f"groups nested more than {MAX_DEPTH} levels deep")
        return [self.field(f"{where}[{i}]", item, depth) for i, item in enumerate(value, 1)
                if not (self.lenient and isinstance(item, RawLua))]

    def register(self, where: str, fid: Any, required: bool, what: str) -> Optional[str]:
        self.count += 1
        if self.count > MAX_FIELDS:
            raise self.error(where, f"a form may have at most {MAX_FIELDS} fields")
        if fid is None:
            if required:
                raise self.error(where, f"{what} needs an id, e.g. id = \"name\"")
            return None
        if not isinstance(fid, str) or not _ID.match(fid):
            raise self.error(where, f"id must be a name of letters, digits and _, got {_show(fid)}")
        if fid in self.ids:
            raise self.error(where, f"id {fid!r} is used twice (also at {self.ids[fid]})")
        self.ids[fid] = where
        return fid

    def field(self, where: str, spec: Any, depth: int) -> Field:
        if not isinstance(spec, dict):
            raise self.error(where, f"a field must be a table, got {_show(spec)}")
        ftype = spec.get("type")
        doc = FIELD_TYPE_BY_NAME.get(ftype) if isinstance(ftype, str) else None
        if doc is None:
            raise self.error(where, f"unknown field type {_show(ftype)}; valid: "
                                    + ", ".join(t.name for t in FIELD_TYPES))
        ftype = doc.name
        if ftype == "button":
            return self.button(where, spec)
        valid = COMMON_KEYS | set(doc.keys) | (VALUE_KEYS if ftype in VALUE_TYPES else set())
        spec = self.drop_raw(spec, {"on_change"})
        fid = self.register(where, spec.get("id"), ftype in VALUE_TYPES, f'a "{ftype}" field')
        if fid:
            where = f"{where} ({fid})"
        self.unknown(where, spec, frozenset(valid), f'type "{ftype}"')
        result = Field(ftype, fid, "", where)
        self.common(result, spec)
        for key in doc.keys:
            if key in spec:
                self.type_key(result, key, spec[key], depth)
        for key in ("on_change",):
            if key in spec:
                result.props[key] = self.callback(where, key, spec[key])
        for key in ("exit_on_change", "remember"):
            if key in spec:
                result.props[key] = self.flag(where, key, spec[key])
        if "default" in spec and ftype in VALUE_TYPES:
            result.props["default"] = coerce_value(result, spec["default"], self.func)
        return result

    def button(self, where: str, spec: Any) -> Field:
        if not isinstance(spec, dict):
            raise self.error(where, f"a button must be a table, got {_show(spec)}")
        if spec.get("type", "button") != "button":
            raise self.error(where, f'type must be "button" here, got {_show(spec.get("type"))}')
        spec = self.drop_raw(spec, {"on_click"})
        self.unknown(where, spec, BUTTON_KEYS, "a button")
        fid = self.register(where, spec.get("id"), True, "a button")
        where = f"{where} ({fid})"
        result = Field("button", fid, "", where)
        self.common(result, spec)
        if not result.label:
            result.label = fid or ""
        for key in ("default", "cancel"):
            if key in spec:
                result.props[key] = self.flag(where, key, spec[key])
        if "on_click" in spec:
            result.props["on_click"] = self.callback(where, "on_click", spec["on_click"])
        return result

    def common(self, result: Field, spec: dict) -> None:
        where = result.where
        if "label" in spec:
            result.label = self.text(where, "label", spec["label"])
        for key in ("tooltip", "color"):
            if key in spec:
                result.props[key] = self.text(where, key, spec[key])
        if "color" in result.props and not _COLOR.match(result.props["color"]):
            raise self.error(where, f'color must be "#rrggbb" or a colour name, got {_show(spec["color"])}')
        for key in ("enabled", "visible", "bold", "italic"):
            if key in spec:
                result.props[key] = self.flag(where, key, spec[key])
        for key in ("x", "y", "w", "h"):
            if key in spec:
                result.props[key] = self.size(where, key, spec[key])
        if "font_size" in spec:
            result.props["font_size"] = self.size(where, "font_size", spec["font_size"], 4, 96)

    def type_key(self, result: Field, key: str, value: Any, depth: int) -> None:
        where = result.where
        if key == "fields":
            result.children = self.field_list(f"{where}.fields", value, depth + 1)
        elif key == "options":
            result.options = self.options(where, value)
        elif key in ("placeholder", "suffix", "filter", "path", "data", "html"):
            result.props[key] = self.text(where, key, value)
        elif key in ("password", "readonly", "required", "editable", "wrap", "monospace",
                     "default", "cancel"):
            result.props[key] = self.flag(where, key, value)
        elif key == "scale":
            result.props[key] = self.flag(where, key, value)
        elif key == "columns":
            result.props[key] = self.size(where, key, value, 1, 20)
        elif key == "decimals":
            result.props[key] = self.size(where, key, value, 0, 10)
        elif key in ("min", "max") and result.type == "date":
            result.props[key] = _iso_date(value, f"{self.func}: {where}: {key}")
        elif key in ("min", "max", "step"):
            result.props[key] = self.real(where, key, value)
            if key == "step" and result.props[key] <= 0:
                raise self.error(where, "step must be > 0")
        elif key == "align":
            result.props[key] = self.choice(where, key, value, ALIGNMENTS)
        elif key == "mode":
            result.props[key] = self.choice(where, key, value, FILE_MODES)
        elif key == "on_click":
            result.props[key] = self.callback(where, key, value)
        else:  # pragma: no cover - every key of FIELD_TYPES is handled above
            raise self.error(where, f"unhandled key {key}")
        if result.type == "number":
            lo, hi = result.props.get("min"), result.props.get("max")
            if lo is not None and hi is not None and lo > hi:
                raise self.error(where, f"min ({format_number(lo)}) is larger than max ({format_number(hi)})")

    def options(self, where: str, value: Any) -> list[Any]:
        if value == {}:
            return []
        if not isinstance(value, list):
            raise self.error(where, 'options must be an array, e.g. { "GPX", "GGZ" }')
        if len(value) > MAX_OPTIONS:
            raise self.error(where, f"at most {MAX_OPTIONS} options")
        result = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (str, int, float)):
                raise self.error(where, f"options must be strings or numbers, got {_show(item)}")
            result.append(item)
        return result


def parse_form(
    spec: Any,
    func: str = "opensak.form",
    is_function: Callable[[Any], bool] = callable,
    lenient: bool = False,
    ok_label: str = "OK",
    cancel_label: str = "Cancel",
) -> Form:
    """Validate *spec* — the form table converted to Python (dicts for
    tables with string keys, lists for arrays) — into a Form. Raises
    MacroError naming the field and key at fault.

    *is_function* recognises callbacks (Lua functions in a macro, RawLua in
    the designer). *lenient* drops RawLua values that are not callbacks, so
    the designer can preview a form whose options come from a variable.
    """
    return _Parser(func, is_function, lenient, ok_label, cancel_label).form(spec)


def parse_options(value: Any, func: str) -> list[Any]:
    """New options for a field (the form handle's set_options)."""
    return _Parser(func, callable, False, "", "").options("", value)


# ── Values ───────────────────────────────────────────────────────────────────


def format_number(value: float | int) -> str:
    """5.0 → "5", 2.5 → "2.5" (Lua shows whole floats with .0)."""
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return str(value)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _show(value: Any) -> str:
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return "a table"
    if isinstance(value, RawLua):
        return value.text
    if callable(value):
        return "a function"
    return repr(value)


def _iso_date(value: Any, what: str) -> str:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10]).isoformat()
        except ValueError:
            pass
    raise MacroError(f'{what} must be a date "YYYY-MM-DD", got {_show(value)}')


def find_option(options: list[Any], value: Any) -> int:
    """Index of *value* in *options* (2 matches "2"); -1 if missing."""
    for i, option in enumerate(options):
        if option == value and type(option) is not bool:
            return i
    shown = format_number(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        else value
    for i, option in enumerate(options):
        text = format_number(option) if isinstance(option, (int, float)) else option
        if text == shown:
            return i
    return -1


def coerce_value(field: Field, value: Any, func: str) -> Any:
    """*value* as the field's value type, or MacroError. Used for
    `default`, the handle's set() and remembered values."""
    what = f"{func}: {field.where}"
    ftype = field.type
    if ftype in ("text", "memo", "file", "folder"):
        if value is None:
            return ""
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise MacroError(f"{what}: the value must be a string, got {_show(value)}")
        return value if isinstance(value, str) else format_number(value)
    if ftype == "number":
        number = _number(value) if value is not None else 0.0
        if number is None or not math.isfinite(number):
            raise MacroError(f"{what}: the value must be a number, got {_show(value)}")
        lo, hi = field.get("min"), field.get("max")
        if lo is not None:
            number = max(number, lo)
        if hi is not None:
            number = min(number, hi)
        decimals = field.get("decimals", 0)
        number = round(number, decimals)
        return int(number) if decimals == 0 else number
    if ftype == "checkbox":
        if value is None:
            return False
        if not isinstance(value, bool):
            raise MacroError(f"{what}: the value must be true or false, got {_show(value)}")
        return value
    if ftype in ("choice", "radio"):
        if value is None:
            return None
        index = find_option(field.options, value)
        if index >= 0:
            return field.options[index]
        if ftype == "choice" and field.get("editable") and isinstance(value, (str, int, float)) \
                and not isinstance(value, bool):
            return value if isinstance(value, str) else format_number(value)
        raise MacroError(f"{what}: {_show(value)} is not one of the options")
    if ftype == "checklist":
        if value is None or value == {}:
            return []
        items = value if isinstance(value, list) else [value]
        indexes = set()
        for item in items:
            index = find_option(field.options, item)
            if index < 0:
                raise MacroError(f"{what}: {_show(item)} is not one of the options")
            indexes.add(index)
        return [field.options[i] for i in sorted(indexes)]
    if ftype == "date":
        if value is None:
            return date.today().isoformat()
        return _iso_date(value, what)
    raise MacroError(f"{what}: a {ftype} field has no value")


def initial_value(field: Field) -> Any:
    """The value a field starts with: `default`, else the empty value (a
    choice starts with its first option, like a GSAK combobox)."""
    if "default" in field.props:
        return field.props["default"]
    if field.type == "choice" and field.options:
        return field.options[0]
    return coerce_value(field, None, "")


def check_required(form: Form, values: dict[str, Any]) -> Optional[Field]:
    """The first required field without a value."""
    for f in form.value_fields():
        if f.get("required") and f.id and values.get(f.id) in (None, "", []):
            return f
    return None


def remember_key(form: Form) -> str:
    """Under which name a form's last values are kept."""
    return form.id or form.title or "form"


def remembered_ids(form: Form) -> set[str]:
    """The fields whose values are kept for the next run (remember = true):
    every value field unless it says remember = false. Passwords are never
    kept."""
    return {
        f.id for f in form.value_fields()
        if f.id and f.get("remember", True) and not f.get("password")
    }


def apply_remembered(form: Form, stored: dict[str, Any], func: str) -> None:
    """Use *stored* values (from an earlier run) as defaults. Values that
    no longer fit the field (e.g. a removed option) are ignored."""
    keep = remembered_ids(form)
    for f in form.value_fields():
        if f.id in keep and f.id in stored:
            try:
                f.props["default"] = coerce_value(f, stored[f.id], func)
            except MacroError:
                continue


# ── Documentation ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FieldTypeDoc:
    """One field type of opensak.form{}."""

    name: str
    # The GSAK form control(s) it replaces
    gsak: str
    # Lua type of its value in the result ("" = no value)
    value: str
    # Keys only this type takes (besides COMMON_KEYS / VALUE_KEYS)
    keys: tuple[str, ...]
    description: str


FIELD_TYPES: tuple[FieldTypeDoc, ...] = (
    FieldTypeDoc("label", "Label", "", ("align",),
                 "Text such as a heading or a hint; `label` is the text. Simple HTML "
                 "(`<b>`, `<i>`, `<br>`, `<font color>`) is shown formatted."),
    FieldTypeDoc("separator", "", "", (), "A horizontal line between fields."),
    FieldTypeDoc("text", "Edit", "string", ("placeholder", "password", "readonly", "required"),
                 "Single-line text."),
    FieldTypeDoc("number", "Edit", "number", ("min", "max", "decimals", "step", "suffix"),
                 "Number with up/down arrows; whole numbers unless `decimals` is set."),
    FieldTypeDoc("memo", "Memo", "string",
                 ("placeholder", "readonly", "required", "wrap", "monospace"),
                 "Multi-line text."),
    FieldTypeDoc("checkbox", "CheckBox", "boolean", (),
                 "A yes/no box; `label` is shown next to the box."),
    FieldTypeDoc("choice", "ComboBox", "string|number", ("options", "editable", "required"),
                 "Drop-down list; starts with the first option unless `default` says "
                 "otherwise. With `editable = true` the user may also type a value "
                 "(GSAK DirectInput)."),
    FieldTypeDoc("radio", "RadioButton", "string|number", ("options", "columns", "required"),
                 "One of the options, as radio buttons."),
    FieldTypeDoc("checklist", "CheckListBox, CheckGroup", "string[]", ("options", "columns"),
                 "Several options to tick; the value is an array of the ticked ones."),
    FieldTypeDoc("date", "Date", "string", ("min", "max"),
                 'A date with a pop-up calendar, as "YYYY-MM-DD" (default today).'),
    FieldTypeDoc("file", "File", "string", ("filter", "mode", "placeholder", "required"),
                 "A file path with a Browse button. A file picked with Browse may be "
                 "used for the rest of the run like one from opensak.choose_file() "
                 '(read with `mode = "open"`, the default, or written with "save").'),
    FieldTypeDoc("folder", "Folder", "string", ("placeholder", "required"),
                 "A folder path with a Browse button. A folder picked with Browse may "
                 "be read and written for the rest of the run, like one from "
                 "opensak.choose_folder()."),
    FieldTypeDoc("group", "GroupBox", "", ("fields",),
                 "A frame titled `label` around its own `fields`. In the absolute "
                 "layout their positions are relative to the group."),
    FieldTypeDoc("button", "Button", "", ("on_click", "default", "cancel"),
                 "A button. Without `on_click` it closes the form, which then returns "
                 "the values and the button's id (nil with `cancel = true`). With "
                 "`on_click` it calls the function and the form stays open."),
    FieldTypeDoc("image", "Image", "", ("path", "data", "scale"),
                 "A picture (PNG, JPG, GIF, BMP, ICO) from a file (`path`; read "
                 "permission needed, relative to the macro's folder) or from base64 "
                 "`data`. `scale = true` stretches it to `w` × `h`."),
    FieldTypeDoc("html", "Browser", "", ("html",),
                 "Read-only formatted text from simple HTML (headings, lists, tables). "
                 "Nothing is loaded from the internet and links are not followed."),
)
FIELD_TYPE_BY_NAME = {t.name: t for t in FIELD_TYPES}


@dataclass(frozen=True)
class KeyDoc:
    """One key of the form table or of a field."""

    key: str
    # Lua Language Server type
    type: str
    description: str


FORM_KEY_DOCS: tuple[KeyDoc, ...] = (
    KeyDoc("title", "string", "Window title."),
    KeyDoc("fields", "opensak.FormField[]", "The fields, top to bottom (see Form fields)."),
    KeyDoc("buttons", "opensak.FormButton[]",
           "The buttons at the bottom; default OK and Cancel. `{}` for none, e.g. "
           "when the fields include buttons."),
    KeyDoc("layout", '"auto"|"absolute"',
           '"auto" (default) stacks the fields with their labels on the left; '
           '"absolute" places each field at its `x`, `y` like GSAK.'),
    KeyDoc("width", "integer", "Width of the field area in pixels (absolute layout: "
                               "default fits the fields)."),
    KeyDoc("height", "integer", "Height of the field area in pixels."),
    KeyDoc("remember", "boolean",
           "Start with the values the user entered last time (stored per macro "
           "file, next to opensak.settings; the macro must be saved). Fields "
           "with `remember = false` and passwords are not kept."),
    KeyDoc("id", "string", "Name the remembered values are kept under (default: "
                           "the title), for macros with several forms."),
    KeyDoc("timeout", "number",
           'Close the form after this many seconds; it then returns the values and "timeout".'),
    KeyDoc("on_open", "fun(f: opensak.Form)", "Called once when the form opens."),
    KeyDoc("validate", "fun(f: opensak.Form, button: string): string?",
           "Called before the form closes with a button other than a cancel button; "
           "return a message to show it and keep the form open."),
)

FIELD_KEY_DOCS: tuple[KeyDoc, ...] = (
    KeyDoc("type", "string", "The field type (see Form fields)."),
    KeyDoc("id", "string", "Key of the value in the result; needed for fields with a "
                           "value, buttons, and fields a callback changes."),
    KeyDoc("label", "string", "Caption: on the left in the auto layout, next to a "
                              "checkbox, the title of a group, the text of a label "
                              "or button."),
    KeyDoc("default", "any", "Initial value (for a button: the button Enter presses)."),
    KeyDoc("tooltip", "string", "Shown when the mouse rests on the field."),
    KeyDoc("enabled", "boolean", "false greys the field out (default true)."),
    KeyDoc("visible", "boolean", "false hides the field (default true)."),
    KeyDoc("x", "integer", "Absolute layout: pixels from the left of the form or group."),
    KeyDoc("y", "integer", "Absolute layout: pixels from the top of the form or group."),
    KeyDoc("w", "integer", "Width in pixels (auto layout: minimum width)."),
    KeyDoc("h", "integer", "Height in pixels (auto layout: minimum height)."),
    KeyDoc("bold", "boolean", "Bold caption."),
    KeyDoc("italic", "boolean", "Italic caption."),
    KeyDoc("color", "string", 'Caption colour, "#rrggbb" or a name such as "red".'),
    KeyDoc("font_size", "integer", "Caption font size in points."),
    KeyDoc("on_change", "fun(f: opensak.Form, value: any, id: string)",
           "Called when the user changes the value — a text or number field when it "
           "loses focus or Enter is pressed. The form stays open."),
    KeyDoc("exit_on_change", "boolean",
           "Close the form when the value changes (after on_change); it returns the "
           "values and the field's id. Like GSAK's ExitOnChange."),
    KeyDoc("remember", "boolean", "false: never remember this field's value."),
    KeyDoc("on_click", "fun(f: opensak.Form, id: string)",
           "button: called on a click; the form stays open (f:close() closes it)."),
    KeyDoc("cancel", "boolean", "button: closes the form, which returns nil (also Esc)."),
    KeyDoc("options", "(string|number)[]", "choice, radio, checklist: the options."),
    KeyDoc("editable", "boolean", "choice: also accept typed text."),
    KeyDoc("columns", "integer", "radio, checklist: number of columns (default 1)."),
    KeyDoc("placeholder", "string", "Grey hint shown while the field is empty."),
    KeyDoc("password", "boolean", "text: hide the characters (never remembered)."),
    KeyDoc("readonly", "boolean", "text, memo: can be selected and copied, not changed."),
    KeyDoc("required", "boolean", "The form cannot be confirmed while the field is empty."),
    KeyDoc("min", "number|string", "number: smallest value; date: earliest date."),
    KeyDoc("max", "number|string", "number: largest value; date: latest date."),
    KeyDoc("decimals", "integer", "number: digits after the decimal point (default 0)."),
    KeyDoc("step", "number", "number: change per arrow click (default 1)."),
    KeyDoc("suffix", "string", 'number: unit shown after the value, e.g. " km".'),
    KeyDoc("wrap", "boolean", "memo: wrap long lines (default true)."),
    KeyDoc("monospace", "boolean", "memo: fixed-width font."),
    KeyDoc("filter", "string", 'file: file types, e.g. "CSV files (*.csv)".'),
    KeyDoc("mode", '"open"|"save"', "file: pick a file to read (default) or to write."),
    KeyDoc("align", '"left"|"center"|"right"', "label: text alignment."),
    KeyDoc("fields", "opensak.FormField[]", "group: the fields inside it."),
    KeyDoc("path", "string", "image: the picture file."),
    KeyDoc("data", "string", "image: the picture as base64 text instead of a file."),
    KeyDoc("scale", "boolean", "image: stretch the picture to w × h."),
    KeyDoc("html", "string", "html: the HTML to show."),
)


@dataclass(frozen=True)
class MethodDoc:
    """A method of the form handle passed to callbacks."""

    name: str
    signature: str
    # Lua Language Server type of the method
    type: str
    description: str


FORM_METHOD_DOCS: tuple[MethodDoc, ...] = (
    MethodDoc("get", "f:get(id)", "fun(self: opensak.Form, id: string): any",
              "The current value of a field."),
    MethodDoc("set", "f:set(id, value)", "fun(self: opensak.Form, id: string, value: any)",
              "Change a field's value (on_change is not called)."),
    MethodDoc("values", "f:values()", "fun(self: opensak.Form): table<string, any>",
              "All values, as the form would return them."),
    MethodDoc("set_options", "f:set_options(id, options)",
              "fun(self: opensak.Form, id: string, options: (string|number)[])",
              "Replace the options of a choice, radio or checklist field. The value is "
              "kept if it is still an option."),
    MethodDoc("set_label", "f:set_label(id, text)",
              "fun(self: opensak.Form, id: string, text: string)",
              "Change a caption: the label of a field, a label's text, a button's text, "
              "a group's title."),
    MethodDoc("set_enabled", "f:set_enabled(id, on)",
              "fun(self: opensak.Form, id: string, on: boolean)", "Enable or grey out a field."),
    MethodDoc("set_visible", "f:set_visible(id, on)",
              "fun(self: opensak.Form, id: string, on: boolean)", "Show or hide a field."),
    MethodDoc("set_title", "f:set_title(text)", "fun(self: opensak.Form, text: string)",
              "Change the window title."),
    MethodDoc("focus", "f:focus(id)", "fun(self: opensak.Form, id: string)",
              "Move the keyboard focus to a field."),
    MethodDoc("close", "f:close([id])", "fun(self: opensak.Form, id?: string)",
              'Close the form: opensak.form() returns the values and *id* (default "ok"). '
              "`validate` is not called."),
    MethodDoc("cancel", "f:cancel()", "fun(self: opensak.Form)",
              "Close the form like Cancel: opensak.form() returns nil."),
)

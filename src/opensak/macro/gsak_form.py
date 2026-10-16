"""
src/opensak/macro/gsak_form.py — convert GSAK macro forms to opensak.form{}.

A GSAK form is a block of "Name = …" / "Property = value" lines, usually
between <Data> VarName=$form and <enddata> in a .gsk macro, written by
GSAK's form designer. find_gsak_forms() finds those blocks; convert()
turns one into the form model the designer edits (the same dicts
form_code.parse_table returns), using the absolute layout so the form
keeps its look. Differences that need the macro author's attention are
returned as notes, e.g. radio buttons: GSAK gives each one a boolean
variable, opensak one radio field whose value is the chosen caption.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# GSAK's Width/Height of the form include the window frame; the field
# area of an opensak form is the inside.
FRAME_WIDTH = 16
FRAME_HEIGHT = 39

_BLOCK = re.compile(r"<data>\s*varname\s*=\s*\$?(\w+)[^\n]*\n(.*?)<enddata>",
                    re.IGNORECASE | re.DOTALL)
_PROPERTY = re.compile(r"^\s*([A-Za-z][A-Za-z ]*?)\s*=\s?(.*?)\s*$")

_TYPES = {
    "edit": "text",
    "memo": "memo",
    "checkbox": "checkbox",
    "combobox": "choice",
    "date": "date",
    "file": "file",
    "folder": "folder",
    "label": "label",
    "groupbox": "group",
    "button": "button",
    "checklistbox": "checklist",
    "checkgroup": "checklist",
    "radiobutton": "radio",
    "image": "image",
    "browser": "html",
}
# Properties handled by convert() for every control (lower case).
_HANDLED = {
    "name", "type", "top", "left", "width", "height", "enabled", "visible",
    "container", "taborder", "caption", "exitonchange", "values", "columns",
    "directinput", "readonly", "wrap", "enter", "escape", "file", "resize",
    "font", "fontstyle", "style", "fontsize", "size", "fontcolor", "textcolor",
    "color", "source", "url", "captionposition", "alignment", "scrollbars",
    "display", "silent", "autosize",
}


@dataclass
class GsakForm:
    """One form found in a GSAK macro."""

    # The variable it is stored in ("form" for <Data> VarName=$form)
    var: str
    # The controls in file order, each a dict of lower-cased property
    # names to their text; "name" and "type" always present.
    controls: list[dict[str, str]] = field(default_factory=list)

    @property
    def caption(self) -> str:
        for c in self.controls:
            if c["type"].lower() == "form":
                return c.get("caption", "") or c["name"]
        return self.var


def parse_controls(text: str) -> list[dict[str, str]]:
    """The controls of one form block (the lines between <Data> and
    <enddata>, or a form copied without them)."""
    controls: list[dict[str, str]] = []
    current: Optional[dict[str, str]] = None
    for raw in text.splitlines():
        # Forms pasted from GSAK's help indent with non-breaking spaces.
        line = raw.replace("\xa0", " ").strip(" \t\ufffd")
        if not line or line.startswith("#"):
            continue
        m = _PROPERTY.match(line)
        if not m:
            continue
        key = m.group(1).replace(" ", "").lower()
        value = m.group(2)
        if key == "name":
            current = {"name": value, "type": ""}
            controls.append(current)
        elif current is not None:
            current[key] = value
    return [c for c in controls if c["type"]]


def find_gsak_forms(text: str) -> list[GsakForm]:
    """All forms in a GSAK macro (or in text copied from one)."""
    forms = []
    for m in _BLOCK.finditer(text):
        controls = parse_controls(m.group(2))
        if any(c["type"].lower() == "form" for c in controls):
            forms.append(GsakForm(m.group(1), controls))
    if not forms:
        controls = parse_controls(text)
        if any(c["type"].lower() == "form" for c in controls):
            forms.append(GsakForm("form", controls))
    return forms


def looks_like_gsak_form(text: str) -> bool:
    return bool(find_gsak_forms(text))


def _int(value: Optional[str]) -> Optional[int]:
    try:
        return max(0, int(float(str(value).strip())))
    except (TypeError, ValueError):
        return None


def _yes(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in ("yes", "true", "1")


def _no(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in ("no", "false", "0")


def _values(text: str) -> list[str]:
    return [v.replace("*semi*", ";") for v in text.split(";") if v != ""]


def _color(value: Optional[str]) -> Optional[str]:
    """A Delphi TColor number (0x00BBGGRR) → "#rrggbb"; system colours
    (negative or above 0xFFFFFF) → None."""
    number = _int(value) if value and not value.strip().startswith("-") else None
    if number is None or number > 0xFFFFFF:
        return None
    r, g, b = number & 0xFF, (number >> 8) & 0xFF, (number >> 16) & 0xFF
    return f"#{r:02x}{g:02x}{b:02x}"


def _identifier(name: str, used: set[str]) -> str:
    base = re.sub(r"\W", "_", name.strip()) or "field"
    if base[0].isdigit():
        base = f"f_{base}"
    candidate, n = base, 2
    while candidate in used:
        candidate, n = f"{base}{n}", n + 1
    used.add(candidate)
    return candidate


def convert(form: GsakForm) -> tuple[dict, list[str]]:
    """The opensak form model for *form*, and notes about what changed."""
    notes: list[str] = []
    ignored: dict[str, set[str]] = {}
    used: set[str] = set()
    model: dict[str, Any] = {"layout": "absolute"}

    main = next(c for c in form.controls if c["type"].lower() == "form")
    if main.get("caption"):
        model["title"] = main["caption"]
    width, height = _int(main.get("width")), _int(main.get("height"))
    if width:
        model["width"] = max(50, width - FRAME_WIDTH)
    if height:
        model["height"] = max(50, height - FRAME_HEIGHT)
    delay = _int(main.get("delay"))
    if delay:
        model["timeout"] = delay
        notes.append('Delay became timeout: the form then returns "timeout" instead of "DelayExit".')
    if _yes(main.get("exitonclick")):
        notes.append(f"{main['name']}: ExitOnClick is not supported.")

    # Containers first, in file order, so children can find their group.
    groups: dict[str, dict] = {}
    roots: list[tuple[tuple[int, int], dict]] = []
    children: dict[str, list[tuple[tuple[int, int], dict]]] = {}
    radios: dict[str, list[dict[str, str]]] = {}
    has_buttons = False

    for index, control in enumerate(form.controls):
        kind = control["type"].strip().lower()
        if kind == "form":
            continue
        ftype = _TYPES.get(kind)
        if ftype is None:
            notes.append(f"{control['name']}: type {control['type']} is not supported and was left out.")
            continue
        container = control.get("container", "").strip()
        if ftype == "radio":
            radios.setdefault(container, []).append(control)
            continue
        item = _field(control, ftype, used, notes, ignored)
        has_buttons |= ftype == "button"
        order = ((_int(control.get("taborder")) or 0) if control.get("taborder") else 10_000,
                 index)
        if ftype == "group":
            item["fields"] = []
            groups[control["name"].lower()] = item
        (children.setdefault(container.lower(), []) if container else roots).append((order, item))

    for container, buttons in radios.items():
        item = _radio(buttons, used)
        notes.append(
            f"Radio buttons {', '.join(b['name'] for b in buttons)} became the radio field "
            f"{item['id']!r}: its value is the caption of the chosen button, not one "
            "boolean per button."
        )
        order = min((_int(b.get("taborder")) or 10_000, i) for i, b in enumerate(buttons))
        (children.setdefault(container.lower(), []) if container else roots).append((order, item))

    for name, items in children.items():
        group = groups.get(name)
        if group is None:
            notes.append(f"Container {name!r} does not exist; its fields were put on the form.")
            roots.extend(items)
            continue
        group["fields"] = [item for _, item in sorted(items, key=lambda p: p[0])]

    model["fields"] = [item for _, item in sorted(roots, key=lambda p: p[0])]
    if has_buttons:
        model["buttons"] = []
        notes.append("The form's own buttons close it and return their name, as in GSAK; "
                     "a button with Escape = Yes returns nil (cancel).")
    for prop, names in sorted(ignored.items()):
        notes.append(f"Property {prop} is not supported ({', '.join(sorted(names))}).")
    return model, notes


def _field(control: dict[str, str], ftype: str, used: set[str], notes: list[str],
           ignored: dict[str, set[str]]) -> dict:
    name = control["name"]
    item: dict[str, Any] = {"type": ftype, "id": _identifier(name, used)}
    caption = control.get("caption")
    if caption:
        item["label"] = caption
    # Delphi sizes labels to their text (AutoSize); a fixed size taken over
    # from GSAK's smaller font would cut the text off.
    sizes = [("left", "x"), ("top", "y")]
    if ftype != "label":
        sizes += [("width", "w"), ("height", "h")]
    for prop, key in sizes:
        number = _int(control.get(prop))
        if number is not None:
            item[key] = number
    if _no(control.get("enabled")):
        item["enabled"] = False
    if _no(control.get("visible")):
        item["visible"] = False
    if _yes(control.get("exitonchange")) and ftype in ("checkbox", "choice"):
        item["exit_on_change"] = True
    if "values" in control and ftype in ("choice", "checklist"):
        item["options"] = _values(control["values"])
    if ftype == "checklist" and _int(control.get("columns")):
        item["columns"] = min(20, _int(control.get("columns")) or 1)
    if ftype == "choice" and _yes(control.get("directinput")):
        item["editable"] = True
    if ftype == "memo":
        if _yes(control.get("readonly")):
            item["readonly"] = True
        if _no(control.get("wrap")):
            item["wrap"] = False
    if ftype == "button":
        if _yes(control.get("enter")):
            item["default"] = True
        if _yes(control.get("escape")):
            item["cancel"] = True
        if control.get("spellcheck"):
            notes.append(f"{name}: Spellcheck buttons are not supported.")
    if ftype == "image":
        if control.get("file"):
            item["path"] = control["file"]
        if _yes(control.get("resize")):
            item["scale"] = True
        if control.get("source", "").strip().lower() == "macro":
            notes.append(f"{name}: the picture was embedded in the GSAK macro — pass it "
                         "as base64 `data` or save it as a file for `path`.")
    if ftype == "html":
        notes.append(f"{name}: the Browser control became read-only HTML text; web "
                     "pages (Url) are not loaded.")
    style = (control.get("fontstyle") or control.get("style") or "").lower()
    if "bold" in style:
        item["bold"] = True
    if "italic" in style:
        item["italic"] = True
    size = _int(control.get("fontsize") or control.get("size"))
    if size and 4 <= size <= 96:
        item["font_size"] = size
    color = _color(control.get("fontcolor") or control.get("textcolor"))
    if color:
        item["color"] = color
    for prop in control:
        if prop not in _HANDLED and prop != "spellcheck":
            ignored.setdefault(prop, set()).add(name)
    return item


def _radio(buttons: list[dict[str, str]], used: set[str]) -> dict:
    """One radio field for the GSAK radio buttons of one container."""
    boxes = []
    for b in buttons:
        x, y = _int(b.get("left")) or 0, _int(b.get("top")) or 0
        boxes.append((x, y, x + (_int(b.get("width")) or 100), y + (_int(b.get("height")) or 17)))
    left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
    right, bottom = max(b[2] for b in boxes), max(b[3] for b in boxes)
    ordered = sorted(zip(boxes, buttons), key=lambda p: (p[0][1], p[0][0]))
    columns = len({b[0] for b, _ in ordered})
    item: dict[str, Any] = {
        "type": "radio",
        "id": _identifier(buttons[0]["name"], used),
        "options": [b.get("caption") or b["name"] for _, b in ordered],
        "x": left, "y": top, "w": right - left, "h": bottom - top,
    }
    if columns > 1:
        item["columns"] = columns
    if any(_yes(b.get("exitonchange")) for b in buttons):
        item["exit_on_change"] = True
    return item

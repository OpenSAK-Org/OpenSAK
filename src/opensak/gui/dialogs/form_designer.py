"""
src/opensak/gui/dialogs/form_designer.py — build opensak.form{} tables with
the mouse, like GSAK's form designer (#982).

The designer edits a *model*: the form table as plain Python (dicts and
lists, as macro/form_code.parse_table returns it), so values it does not
understand — callbacks, variables — are kept as RawLua and written back
unchanged. Every edit replaces the model (with an undo step) and the
canvas is rebuilt from it with the same build_form() the running macro
uses, so what the designer shows is what the macro gets.

Absolute layout: double-click on the canvas adds the control chosen in
the toolbox at that point; drag to move (into or out of a group), drag a
handle to resize; Ctrl+arrows move one pixel, Ctrl+Shift+arrows one grid
step, Shift+arrows resize by one pixel. Shift/Ctrl+click and a rubber band
select several controls for the Arrange menu. The auto layout has no
positions: the order of the outline is the order of the form.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFontDatabase, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenuBar, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from opensak.gui.dialogs.macro_form import FieldWidget, FormDialog, build_form
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.lang import tr
from opensak.macro.errors import MacroError
from opensak.macro.form_code import (
    LuaSyntaxError,
    find_form_tables,
    form_to_lua,
    parse_table,
)
from opensak.macro.forms import (
    FIELD_TYPE_BY_NAME,
    FIELD_TYPES,
    VALUE_TYPES,
    Field,
    Form,
    RawLua,
    initial_value,
    parse_form,
)
from opensak.macro.gsak_form import convert, find_gsak_forms

# A path to a field in the model: ("fields", 2) or ("fields", 0, "fields", 1)
# or ("buttons", 0).
Path_ = tuple
CANVAS_MARGIN = 16
HANDLE = 6
# Size of a new control in the absolute layout, and its starting values.
NEW_SIZE = {
    "label": (100, 16), "separator": (200, 8), "text": (150, 24), "number": (90, 24),
    "memo": (220, 80), "checkbox": (140, 20), "choice": (150, 24), "radio": (150, 50),
    "checklist": (160, 80), "date": (110, 24), "file": (260, 24), "folder": (260, 24),
    "group": (240, 140), "button": (75, 25), "image": (64, 64), "html": (260, 120),
}
NEW_PROPS: dict[str, dict[str, Any]] = {
    "label": {"label": "Label"},
    "checkbox": {"label": "Check box"},
    "choice": {"options": ["Option 1", "Option 2"]},
    "radio": {"options": ["Option 1", "Option 2"]},
    "checklist": {"options": ["Option 1", "Option 2"]},
    "group": {"label": "Group", "fields": []},
    "button": {"label": "Button"},
    "html": {"html": "<b>HTML</b>"},
}

# Property editors: key → kind. "lua" keys hold Lua code; in other text
# editors a value starting with "=" is a Lua expression.
_FORM_PROPS = [
    ("title", "str"), ("id", "str"), ("layout", "enum:auto,absolute"), ("width", "int"),
    ("height", "int"), ("remember", "bool"), ("timeout", "num"), ("on_open", "lua"),
    ("validate", "lua"),
]
_COMMON_PROPS = [
    ("id", "str"), ("label", "str"), ("tooltip", "str"), ("enabled", "bool"),
    ("visible", "bool"), ("x", "int"), ("y", "int"), ("w", "int"), ("h", "int"),
    ("bold", "bool"), ("italic", "bool"), ("color", "str"), ("font_size", "int"),
]
_VALUE_PROPS = [("on_change", "lua"), ("exit_on_change", "bool"), ("remember", "bool")]
_KEY_KINDS = {
    "placeholder": "str", "password": "bool", "readonly": "bool", "required": "bool",
    "min": "num", "max": "num", "decimals": "int", "step": "num", "suffix": "str",
    "wrap": "bool", "monospace": "bool", "options": "list", "editable": "bool",
    "columns": "int", "filter": "str", "mode": "enum:open,save",
    "align": "enum:left,center,right", "on_click": "lua", "default": "bool",
    "cancel": "bool", "path": "str", "data": "text", "scale": "bool", "html": "text",
}
_DEFAULT_KINDS = {"number": "num", "memo": "text", "checkbox": "bool", "checklist": "list"}


def new_model() -> dict:
    return {"title": "", "layout": "absolute", "width": 320, "height": 160, "fields": []}


def _get(model: dict, path: Path_) -> Any:
    node: Any = model
    for step in path:
        node = node[step]
    return node


def _path_of(model: dict, wanted: dict) -> Optional[Path_]:
    """The path of the dict *wanted* (by identity) in the model."""
    def visit(items: Any, prefix: Path_, key: str) -> Optional[Path_]:
        for i, item in enumerate(items if isinstance(items, list) else []):
            if item is wanted:
                return prefix + (key, i)
            if isinstance(item, dict):
                found = visit(item.get("fields"), prefix + (key, i), "fields")
                if found is not None:
                    return found
        return None

    return visit(model.get("fields"), (), "fields") or visit(model.get("buttons"), (), "buttons")


def _all_ids(model: dict) -> set[str]:
    ids: set[str] = set()

    def visit(items: Any) -> None:
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                if isinstance(item.get("id"), str):
                    ids.add(item["id"])
                visit(item.get("fields"))

    visit(model.get("fields"))
    visit(model.get("buttons"))
    return ids


def unique_id(model: dict, base: str) -> str:
    used = _all_ids(model)
    n = 1
    while f"{base}{n}" in used:
        n += 1
    return f"{base}{n}"


def _is_designer_callback(value: Any) -> bool:
    return isinstance(value, RawLua)


def preview_form(model: dict) -> Form:
    """The model as a Form, for the canvas and the preview; raises
    MacroError for what the macro would refuse too."""
    return parse_form(model, "form", is_function=_is_designer_callback, lenient=True,
                      ok_label=tr("macro_form_ok"), cancel_label=tr("cancel"))


def _field_paths(model: dict, form: Form) -> dict[Field, Path_]:
    """Which model path each parsed field came from (lenient parsing
    skips RawLua entries, so the lists are walked side by side)."""
    result: dict[Field, Path_] = {}

    def visit(items: Any, fields: list[Field], prefix: Path_, key: str) -> None:
        if not isinstance(items, list):
            return
        parsed = iter(fields)
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            field = next(parsed, None)
            if field is None:
                return
            path = prefix + (key, i)
            result[field] = path
            visit(item.get("fields"), field.children, path, "fields")

    visit(model.get("fields"), form.fields, (), "fields")
    if isinstance(model.get("buttons"), list):
        visit(model["buttons"], form.buttons, (), "buttons")
    return result


def _transparent(widget: QWidget) -> None:
    """Let the canvas get every mouse event."""
    widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    for child in widget.findChildren(QWidget):
        child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        child.setFocusPolicy(Qt.FocusPolicy.NoFocus)


# ── Canvas ───────────────────────────────────────────────────────────────────


class _Overlay(QWidget):
    """Draws the selection and the rubber band above the controls."""

    def __init__(self, canvas: "DesignCanvas"):
        super().__init__(canvas)
        self._canvas = canvas
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        highlight = self.palette().highlight().color()
        canvas = self._canvas
        for i, path in enumerate(canvas.selection):
            rect = canvas.rect_of(path)
            if rect is None:
                continue
            painter.setPen(QPen(highlight, 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect.adjusted(-1, -1, 0, 0))
            if canvas.absolute and len(canvas.selection) == 1:
                painter.setPen(QPen(highlight.darker(150), 1))
                painter.setBrush(highlight if i == 0 else QColor("white"))
                for handle in canvas.handles(rect).values():
                    painter.drawRect(handle)
        if canvas.band is not None:
            painter.setPen(QPen(highlight, 1, Qt.PenStyle.DotLine))
            fill = QColor(highlight)
            fill.setAlpha(40)
            painter.setBrush(fill)
            painter.drawRect(canvas.band.normalized())


class DesignCanvas(QWidget):
    """The form as the macro will show it, with selection, drag and drop."""

    selection_changed = Signal()
    # A finished drag / resize / key move: {path: (x, y, w, h)}
    moved = Signal(object)
    # Move the field at path into the group at path (None = the form).
    reparented = Signal(object, object, QPoint)
    add_at = Signal(object, QPoint)  # (group path or None, position)
    delete_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.selection: list[Path_] = []
        self.band: Optional[QRect] = None
        self.absolute = True
        self.grid = 8
        self.snap = True
        self.show_grid = True
        self.error = ""
        self._model: dict = {}
        self._body: Optional[QWidget] = None
        self._buttons: Optional[QWidget] = None
        self._items: dict[Path_, FieldWidget] = {}
        self._drag: Optional[dict] = None
        self._overlay = _Overlay(self)

    # -- building ----------------------------------------------------------------

    def rebuild(self, model: dict) -> None:
        self._model = model
        for old in (self._body, self._buttons):
            if old is not None:
                old.hide()
                old.deleteLater()
        self._body = self._buttons = None
        self._items = {}
        self.absolute = model.get("layout") == "absolute"
        try:
            form = preview_form(model)
            self.error = ""
        except MacroError as exc:
            self.error = str(exc)
            form = None
        if form is None:
            self.setMinimumSize(320, 160)
            self.resize(320, 160)
            self.update()
            return
        view = build_form(form)
        for spec, item in view.items.items():
            if spec.has_value:
                item.set_value(initial_value(spec))
        paths = _field_paths(model, form)
        self._items = {paths[f]: item for f, item in view.items.items() if f in paths}
        body = view.body
        body.setParent(self)
        _transparent(body)
        if not self.absolute:
            body.setMinimumWidth(max(body.minimumWidth(), form.width or 360))
            body.adjustSize()
        body.move(CANVAS_MARGIN, CANVAS_MARGIN)
        body.show()
        self._body = body
        bottom = body.geometry().bottom() + 1
        right = body.geometry().right() + 1
        if view.button_row is not None:
            row = view.button_row
            row.setParent(self)
            _transparent(row)
            row.adjustSize()
            width = max(row.sizeHint().width(), body.width())
            row.setGeometry(CANVAS_MARGIN, bottom + 8, width, row.sizeHint().height())
            row.show()
            self._buttons = row
            bottom = row.geometry().bottom() + 1
            right = max(right, row.geometry().right() + 1)
        size = QSize(right + CANVAS_MARGIN, bottom + CANVAS_MARGIN)
        self.setMinimumSize(size)
        self.resize(size)
        self.selection = [p for p in self.selection if p in self._items]
        self._overlay.setGeometry(self.rect())
        self._overlay.raise_()
        self.update()
        self._overlay.update()

    # -- geometry ------------------------------------------------------------

    def rect_of(self, path: Path_) -> Optional[QRect]:
        """Where a field is drawn, in canvas coordinates (with its caption)."""
        item = self._items.get(path)
        if item is None or not item.widget.isVisible():
            return None
        rect = QRect(item.widget.mapTo(self, QPoint(0, 0)), item.widget.size())
        if item.caption is not None and item.caption.isVisible():
            rect = rect.united(QRect(item.caption.mapTo(self, QPoint(0, 0)), item.caption.size()))
        return rect

    def geometry_of(self, path: Path_) -> Optional[QRect]:
        """x, y, w, h of a field relative to its container (form or group)."""
        item = self._items.get(path)
        return QRect(item.widget.geometry()) if item is not None else None

    def handles(self, rect: QRect) -> dict[str, QRect]:
        h = HANDLE
        xs = {"l": rect.left() - h // 2, "c": rect.center().x() - h // 2, "r": rect.right() - h // 2}
        ys = {"t": rect.top() - h // 2, "m": rect.center().y() - h // 2, "b": rect.bottom() - h // 2}
        return {
            v + k: QRect(xs[k], ys[v], h, h)
            for v in ("t", "m", "b") for k in ("l", "c", "r") if v + k != "mc"
        }

    def path_at(self, pos: QPoint) -> Optional[Path_]:
        """The innermost field under *pos* (a group's contents before the
        group, later fields before earlier ones)."""
        best: Optional[Path_] = None
        for path in self._items:
            rect = self.rect_of(path)
            if rect is None or not rect.contains(pos):
                continue
            if best is None or len(path) > len(best) or (len(path) == len(best) and path > best):
                best = path
        return best

    def group_at(self, pos: QPoint, exclude: Optional[Path_] = None) -> Optional[Path_]:
        """The innermost group under *pos* that is not *exclude* or inside it."""
        best: Optional[Path_] = None
        for path, item in self._items.items():
            if item.spec.type != "group" or path[0] != "fields":
                continue
            if exclude is not None and path[:len(exclude)] == exclude:
                continue
            rect = self.rect_of(path)
            if rect is not None and rect.contains(pos) and (best is None or len(path) > len(best)):
                best = path
        return best

    def container_origin(self, group: Optional[Path_]) -> QPoint:
        """Canvas position of the (0, 0) of the form body or a group."""
        if group is None:
            return self._body.mapTo(self, QPoint(0, 0)) if self._body else QPoint(CANVAS_MARGIN, CANVAS_MARGIN)
        return self._items[group].widget.mapTo(self, QPoint(0, 0))

    def body_rect(self) -> QRect:
        return self._body.geometry() if self._body is not None else QRect()

    def snapped(self, value: int) -> int:
        return round(value / self.grid) * self.grid if self.snap and self.grid > 1 else value

    def set_selection(self, paths: list[Path_]) -> None:
        self.selection = [p for p in paths if p in self._items or p[0] == "buttons"]
        self._overlay.update()
        self.selection_changed.emit()

    # -- painting --------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().window().color().darker(108))
        if self._body is not None:
            body = self._body.geometry()
            painter.fillRect(body, self.palette().window())
            if self.absolute and self.show_grid and self.grid >= 4:
                painter.setPen(self.palette().mid().color())
                for x in range(body.left(), body.right(), self.grid):
                    for y in range(body.top(), body.bottom(), self.grid):
                        painter.drawPoint(x, y)
        if self.error:
            painter.setPen(QColor("#c0392b"))
            painter.drawText(self.rect().adjusted(8, 8, -8, -8), Qt.TextFlag.TextWordWrap, self.error)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._overlay.setGeometry(self.rect())
        super().resizeEvent(event)

    # -- mouse -------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        pos = event.position().toPoint()
        multi = bool(event.modifiers() & (Qt.KeyboardModifier.ShiftModifier
                                          | Qt.KeyboardModifier.ControlModifier))
        if self.absolute and len(self.selection) == 1:
            rect = self.rect_of(self.selection[0])
            if rect is not None:
                for name, handle in self.handles(rect).items():
                    if handle.adjusted(-2, -2, 2, 2).contains(pos):
                        self._start_drag("resize:" + name, pos)
                        return
        path = self.path_at(pos)
        if path is None:
            if not multi:
                self.set_selection([])
            self.band = QRect(pos, pos)
            self._drag = {"mode": "band", "start": pos, "base": list(self.selection)}
            return
        if multi:
            selection = list(self.selection)
            if path in selection:
                selection.remove(path)
            else:
                selection.append(path)
            self.set_selection(selection)
            return
        if path not in self.selection:
            self.set_selection([path])
        if self.absolute and path[0] == "fields":
            self._start_drag("move", pos)

    def _start_drag(self, mode: str, pos: QPoint) -> None:
        self._drag = {
            "mode": mode, "start": pos,
            "geometry": {p: self.geometry_of(p) for p in self.selection if p[0] == "fields"},
            "moved": False,
        }

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        drag = self._drag
        pos = event.position().toPoint()
        if drag is None:
            self._update_cursor(pos)
            return
        if drag["mode"] == "band":
            self.band = QRect(drag["start"], pos)
            self._overlay.update()
            return
        delta = pos - drag["start"]
        if not drag["moved"] and delta.manhattanLength() < QApplication.startDragDistance():
            return
        drag["moved"] = True
        for path, geo in drag["geometry"].items():
            if geo is None:
                continue
            item = self._items[path]
            new = self._dragged(drag["mode"], geo, delta, path == self.selection[0])
            item.widget.setGeometry(new)
        self._overlay.update()

    def _dragged(self, mode: str, geo: QRect, delta: QPoint, primary: bool) -> QRect:
        if mode == "move":
            x, y = self.snapped(geo.x() + delta.x()), self.snapped(geo.y() + delta.y())
            return QRect(max(0, x), max(0, y), geo.width(), geo.height())
        handle = mode.split(":", 1)[1]
        left, top, right, bottom = geo.left(), geo.top(), geo.right() + 1, geo.bottom() + 1
        if "l" in handle:
            left = min(self.snapped(left + delta.x()), right - 8)
        if "r" in handle:
            right = max(self.snapped(right + delta.x()), left + 8)
        if handle[0] == "t":
            top = min(self.snapped(top + delta.y()), bottom - 8)
        if handle[0] == "b":
            bottom = max(self.snapped(bottom + delta.y()), top + 8)
        return QRect(max(0, left), max(0, top), right - max(0, left), bottom - max(0, top))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt API)
        drag, self._drag = self._drag, None
        if drag is None:
            return
        if drag["mode"] == "band":
            band = self.band.normalized() if self.band else QRect()
            self.band = None
            if band.width() > 2 or band.height() > 2:
                hits = [p for p in self._items if (r := self.rect_of(p)) is not None
                        and band.contains(r)]
                # Keep only the outermost: selecting a group selects its contents too.
                hits = [p for p in hits if not any(q != p and p[:len(q)] == q for q in hits)]
                self.set_selection(drag["base"] + [p for p in hits if p not in drag["base"]])
            self._overlay.update()
            return
        if not drag["moved"]:
            return
        pos = event.position().toPoint()
        if drag["mode"] == "move" and len(drag["geometry"]) == 1:
            path = next(iter(drag["geometry"]))
            target = self.group_at(pos, exclude=path)
            current = path[:-2] or None
            if target != current:
                widget = self._items[path].widget
                corner = widget.mapTo(self, QPoint(0, 0))
                self.reparented.emit(path, target, corner - self.container_origin(target))
                return
        changes = {}
        for path in drag["geometry"]:
            geo = self._items[path].widget.geometry()
            changes[path] = (geo.x(), geo.y(), geo.width(), geo.height())
        self.moved.emit(changes)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt API)
        pos = event.position().toPoint()
        path = self.path_at(pos)
        if path is not None and self._items[path].spec.type != "group":
            return
        group = path
        origin = self.container_origin(group)
        self.add_at.emit(group, QPoint(self.snapped(pos.x() - origin.x()),
                                       self.snapped(pos.y() - origin.y())))

    def _update_cursor(self, pos: QPoint) -> None:
        cursor = Qt.CursorShape.ArrowCursor
        if self.absolute and len(self.selection) == 1:
            rect = self.rect_of(self.selection[0])
            shapes = {"tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
                      "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
                      "tc": Qt.CursorShape.SizeVerCursor, "bc": Qt.CursorShape.SizeVerCursor,
                      "ml": Qt.CursorShape.SizeHorCursor, "mr": Qt.CursorShape.SizeHorCursor}
            if rect is not None:
                for name, handle in self.handles(rect).items():
                    if handle.adjusted(-2, -2, 2, 2).contains(pos):
                        cursor = shapes[name]
        self.setCursor(cursor)

    # -- keyboard ----------------------------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        key = event.key()
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_requested.emit()
            return
        arrows = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                  Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if key not in arrows or not self.absolute or not (ctrl or shift):
            super().keyPressEvent(event)
            return
        dx, dy = arrows[key]
        step = self.grid if ctrl and shift else 1
        changes = {}
        for path in self.selection:
            geo = self.geometry_of(path)
            if geo is None or path[0] != "fields":
                continue
            if ctrl:
                changes[path] = (max(0, geo.x() + dx * step), max(0, geo.y() + dy * step),
                                 geo.width(), geo.height())
            else:
                changes[path] = (geo.x(), geo.y(), max(8, geo.width() + dx),
                                 max(8, geo.height() + dy))
        if changes:
            self.moved.emit(changes)


# ── Property panel ───────────────────────────────────────────────────────────


class _FocusOut(QObject):
    def __init__(self, widget: QWidget, callback: Callable[[], None]):
        super().__init__(widget)
        self._callback = callback
        widget.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.FocusOut:
            self._callback()
        return False


def _shown(value: Any, kind: str) -> str:
    if value is None:
        return ""
    if isinstance(value, RawLua):
        return value.text if kind == "lua" else f"={value.text}"
    if kind == "list":
        if isinstance(value, list):
            return "\n".join(_shown(v, "str") for v in value)
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class _ValueError(ValueError):
    pass


def _parsed(text: str, kind: str) -> Any:
    """Editor text → model value (None removes the key)."""
    if kind == "lua":
        return RawLua(text.strip()) if text.strip() else None
    if kind not in ("text", "list") and text.startswith("="):
        return RawLua(text[1:].strip()) if text[1:].strip() else None
    if kind == "text":
        return text if text else None
    if kind == "list":
        if text.strip().startswith("="):
            return RawLua(text.strip()[1:].strip())
        return [line for line in text.split("\n") if line.strip() != ""] if text.strip() else None
    text = text.strip()
    if not text:
        return None
    if kind == "bool":
        if text not in ("true", "false"):
            raise _ValueError(text)
        return text == "true"
    if kind == "int":
        try:
            return int(text)
        except ValueError:
            raise _ValueError(text) from None
    if kind == "num":
        try:
            number = float(text)
        except ValueError:
            raise _ValueError(text) from None
        return int(number) if number.is_integer() else number
    return text


class PropertyPanel(QScrollArea):
    """Editors for the keys of the selected field (or of the form)."""

    edited = Signal(str, object)  # key, new value (None = remove the key)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setMinimumWidth(250)
        self._title = QLabel()
        self._title.setWordWrap(True)
        self.show_target(None, "")

    def show_target(self, target: Optional[dict], kind: str, count: int = 1) -> None:
        """Show the keys of *target*: a field (kind = its type), the form
        (kind = "form"), or nothing."""
        inner = QWidget()
        outer = QVBoxLayout(inner)
        layout = QFormLayout()
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        outer.addLayout(layout)
        outer.addStretch(1)
        if target is None:
            label = QLabel(tr("designer_nothing_selected"))
            label.setWordWrap(True)
            layout.addRow(label)
        else:
            heading = tr("designer_props_form") if kind == "form" else kind
            if count > 1:
                heading = tr("designer_props_many", n=count, kind=heading)
            title = QLabel(f"<b>{heading}</b>")
            layout.addRow(title)
            for key, key_kind in self._keys(kind):
                layout.addRow(key, self._editor(key, key_kind, target.get(key)))
        self.setWidget(inner)

    @staticmethod
    def _keys(kind: str) -> list[tuple[str, str]]:
        if kind == "form":
            return _FORM_PROPS
        doc = FIELD_TYPE_BY_NAME.get(kind)
        keys = list(_COMMON_PROPS)
        if kind == "label":
            keys[1] = ("label", "text")
        if kind in VALUE_TYPES:
            keys.insert(2, ("default", _DEFAULT_KINDS.get(kind, "str")))
            keys += _VALUE_PROPS
        for key in doc.keys if doc else ():
            if key == "fields":
                continue
            key_kind = _KEY_KINDS[key]
            if kind == "date" and key in ("min", "max"):
                key_kind = "str"
            keys.append((key, key_kind))
        return keys

    def _editor(self, key: str, kind: str, value: Any) -> QWidget:
        def commit(text: str) -> None:
            try:
                parsed = _parsed(text, kind)
            except _ValueError:
                editor.setStyleSheet("background: #f8d7da;")
                return
            editor.setStyleSheet("")
            if parsed != value_holder[0]:
                value_holder[0] = parsed
                self.edited.emit(key, parsed)

        value_holder = [value]
        editor: QWidget
        if kind in ("text", "list", "lua"):
            edit = QPlainTextEdit(_shown(value, kind))
            edit.setTabChangesFocus(True)
            edit.setFixedHeight(70 if kind != "lua" else 90)
            if kind == "lua":
                edit.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
            _FocusOut(edit, lambda: commit(edit.toPlainText()))
            editor = edit
        elif kind == "bool" or kind.startswith("enum:"):
            combo = QComboBox()
            combo.setEditable(True)
            choices = ["", "true", "false"] if kind == "bool" else [""] + kind[5:].split(",")
            combo.addItems(choices)
            combo.setCurrentText(_shown(value, kind))
            combo.activated.connect(lambda _i: commit(combo.currentText()))
            line_edit = combo.lineEdit()
            if line_edit is not None:
                line_edit.editingFinished.connect(lambda: commit(combo.currentText()))
            editor = combo
            if kind.startswith("enum:"):
                kind = "str"
        else:
            line = QLineEdit(_shown(value, kind))
            line.editingFinished.connect(lambda: commit(line.text()))
            editor = line
        editor.setObjectName(f"prop_{key}")
        return editor


# ── Designer window ──────────────────────────────────────────────────────────


class FormDesigner(QDialog):
    """GSAK-like designer for one opensak.form{} table. After exec(),
    model() is the edited form and lua() its Lua code."""

    def __init__(self, model: Optional[dict] = None, parent: Optional[QWidget] = None,
                 updating: bool = False):
        super().__init__(parent)
        self.setWindowTitle(tr("designer_title"))
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint
                            | Qt.WindowType.WindowMinMaxButtonsHint)
        self.resize(1100, 700)
        clamp_dialog_height_to_screen(self, parent)
        self._model = copy.deepcopy(model) if model is not None else new_model()
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self._copied: list[dict] = []
        self._build(updating)
        self._refresh(rebuild_panel=True)

    # -- UI ----------------------------------------------------------------------

    def _action(self, text: str, slot, shortcut=None) -> QAction:
        act = QAction(text, self)
        if shortcut is not None:
            act.setShortcut(QKeySequence(shortcut))
        act.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act.triggered.connect(slot)
        self.addAction(act)
        return act

    def _build(self, updating: bool) -> None:
        key = QKeySequence.StandardKey
        menubar = QMenuBar(self)
        form_menu = menubar.addMenu(tr("designer_menu_form"))
        form_menu.addAction(self._action(tr("designer_new"), self._new_form))
        form_menu.addAction(self._action(tr("designer_paste_form"), self._paste_form))
        form_menu.addAction(self._action(tr("designer_import_gsak"), self._import_gsak_file))
        form_menu.addSeparator()
        form_menu.addAction(self._action(tr("designer_copy_lua"), self._copy_lua))
        form_menu.addAction(self._action(tr("designer_preview"), self._preview, "F5"))
        edit_menu = menubar.addMenu(tr("macro_menu_edit"))
        self._act_undo = self._action(tr("macro_act_undo"), self._undo_step, key.Undo)
        self._act_redo = self._action(tr("macro_act_redo"), self._redo_step, key.Redo)
        edit_menu.addActions([self._act_undo, self._act_redo])
        edit_menu.addSeparator()
        edit_menu.addAction(self._action(tr("designer_copy"), self._copy_fields, key.Copy))
        edit_menu.addAction(self._action(tr("designer_paste"), self._paste_fields, key.Paste))
        edit_menu.addAction(self._action(tr("designer_duplicate"), self._duplicate, "Ctrl+D"))
        edit_menu.addAction(self._action(tr("delete"), self._delete))
        edit_menu.addAction(self._action(tr("designer_select_all"), self._select_all, key.SelectAll))
        arrange = menubar.addMenu(tr("designer_menu_arrange"))
        for name, key_ in (("left", "designer_align_left"), ("right", "designer_align_right"),
                           ("top", "designer_align_top"), ("bottom", "designer_align_bottom"),
                           ("hcenter", "designer_align_hcenter"),
                           ("vcenter", "designer_align_vcenter"),
                           ("width", "designer_align_width"), ("height", "designer_align_height")):
            arrange.addAction(self._action(tr(key_), lambda _c=False, n=name: self._align(n)))
        arrange.addSeparator()
        arrange.addAction(self._action(tr("designer_front"), lambda: self._reorder("front")))
        arrange.addAction(self._action(tr("designer_back"), lambda: self._reorder("back")))
        buttons_menu = menubar.addMenu(tr("designer_menu_buttons"))
        buttons_menu.addAction(self._action(tr("designer_buttons_default"),
                                            lambda: self._set_buttons("default")))
        buttons_menu.addAction(self._action(tr("designer_buttons_none"),
                                            lambda: self._set_buttons("none")))
        buttons_menu.addAction(self._action(tr("designer_buttons_add"),
                                            lambda: self._set_buttons("add")))
        view_menu = menubar.addMenu(tr("designer_menu_view"))
        self._act_grid = self._action(tr("designer_show_grid"), self._toggle_grid)
        self._act_grid.setCheckable(True)
        self._act_grid.setChecked(True)
        self._act_snap = self._action(tr("designer_snap"), self._toggle_snap)
        self._act_snap.setCheckable(True)
        self._act_snap.setChecked(True)
        view_menu.addActions([self._act_grid, self._act_snap])
        view_menu.addAction(self._action(tr("designer_grid_step"), self._grid_step))

        # Left: toolbox and outline
        self._toolbox = QListWidget()
        for doc in FIELD_TYPES:
            item = QListWidgetItem(doc.name)
            item.setToolTip(doc.description)
            item.setData(Qt.ItemDataRole.UserRole, doc.name)
            self._toolbox.addItem(item)
        self._toolbox.setCurrentRow(2)
        self._toolbox.itemDoubleClicked.connect(lambda _i: self._add_field(None, None))
        self._outline = QTreeWidget()
        self._outline.setHeaderHidden(True)
        self._outline.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self._outline.itemSelectionChanged.connect(self._outline_selected)
        order = QHBoxLayout()
        for text, slot in ((tr("designer_up"), lambda: self._reorder("up")),
                           (tr("designer_down"), lambda: self._reorder("down")),
                           (tr("designer_indent"), self._indent),
                           (tr("designer_outdent"), self._outdent)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            order.addWidget(button)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel(tr("designer_toolbox")))
        left_layout.addWidget(self._toolbox, 2)
        add = QPushButton(tr("designer_add"))
        add.clicked.connect(lambda: self._add_field(None, None))
        left_layout.addWidget(add)
        left_layout.addWidget(QLabel(tr("designer_outline")))
        left_layout.addWidget(self._outline, 3)
        left_layout.addLayout(order)

        # Centre: canvas
        self._canvas = DesignCanvas()
        self._canvas.selection_changed.connect(self._canvas_selected)
        self._canvas.moved.connect(self._apply_geometry)
        self._canvas.reparented.connect(self._move_into)
        self._canvas.add_at.connect(lambda group, pos: self._add_field(group, pos))
        self._canvas.delete_requested.connect(self._delete)
        scroll = QScrollArea()
        scroll.setWidget(self._canvas)
        scroll.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self._panel = PropertyPanel()
        self._panel.edited.connect(self._property_edited)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(scroll)
        splitter.addWidget(self._panel)
        splitter.setSizes([220, 600, 280])

        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        buttons = QDialogButtonBox()
        self._btn_apply = buttons.addButton(
            tr("designer_update" if updating else "designer_insert"),
            QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("cancel"))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setMenuBar(menubar)
        layout.addWidget(splitter, 1)
        layout.addWidget(self._status)
        layout.addWidget(buttons)

    # -- model -----------------------------------------------------------------

    def model(self) -> dict:
        return copy.deepcopy(self._model)

    def lua(self, indent: str = "") -> str:
        return form_to_lua(self._model, indent)

    def _change(self, edit: Callable[[dict], Any], selection: Optional[list[Path_]] = None,
                rebuild_panel: bool = True) -> None:
        """Apply *edit* to a copy of the model, with an undo step."""
        before = copy.deepcopy(self._model)
        edit(self._model)
        if self._model == before:
            return
        self._undo.append(before)
        self._redo.clear()
        if selection is not None:
            self._canvas.selection = selection
        self._refresh(rebuild_panel)

    def _refresh(self, rebuild_panel: bool = True) -> None:
        self._canvas.rebuild(self._model)
        self._fill_outline()
        self._act_undo.setEnabled(bool(self._undo))
        self._act_redo.setEnabled(bool(self._redo))
        if self._canvas.error:
            self._status.setStyleSheet("color: #c0392b;")
            self._status.setText(self._canvas.error)
        else:
            self._status.setStyleSheet("")
            self._status.setText(tr("designer_hint_absolute" if self._canvas.absolute
                                    else "designer_hint_auto"))
        if rebuild_panel:
            self._show_properties()

    def _undo_step(self) -> None:
        if self._undo:
            self._redo.append(self._model)
            self._model = self._undo.pop()
            self._refresh()

    def _redo_step(self) -> None:
        if self._redo:
            self._undo.append(self._model)
            self._model = self._redo.pop()
            self._refresh()

    # -- selection ---------------------------------------------------------------

    def _selected(self) -> list[Path_]:
        return [p for p in self._canvas.selection if self._exists(p)]

    def _exists(self, path: Path_) -> bool:
        try:
            return isinstance(_get(self._model, path), dict)
        except (KeyError, IndexError, TypeError):
            return False

    def _canvas_selected(self) -> None:
        self._sync_outline()
        self._show_properties()

    def _outline_selected(self) -> None:
        if getattr(self, "_filling", False):
            return
        paths = []
        for item in self._outline.selectedItems():
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path:
                paths.append(tuple(path))
        self._canvas.selection = [p for p in paths if self._exists(p)]
        self._canvas.update()
        self._canvas._overlay.update()
        self._show_properties()

    def _show_properties(self) -> None:
        selected = self._selected()
        if not selected:
            self._panel.show_target(self._model, "form")
            return
        target = _get(self._model, selected[0])
        self._panel.show_target(target, str(target.get("type", "button")), len(selected))

    def _fill_outline(self) -> None:
        self._filling = True
        self._outline.clear()
        root = QTreeWidgetItem([tr("designer_props_form")])
        self._outline.addTopLevelItem(root)

        def add(parent: QTreeWidgetItem, items: Any, prefix: Path_, key: str) -> None:
            for i, item in enumerate(items if isinstance(items, list) else []):
                path = prefix + (key, i)
                if isinstance(item, dict):
                    kind = item.get("type", "button")
                    name = item.get("id") or item.get("label") or ""
                    text = f"{kind}  {name}" if not isinstance(name, RawLua) else kind
                else:
                    text = tr("designer_lua_item")
                node = QTreeWidgetItem([text])
                node.setData(0, Qt.ItemDataRole.UserRole, list(path) if isinstance(item, dict) else None)
                parent.addChild(node)
                if isinstance(item, dict):
                    add(node, item.get("fields"), path, "fields")

        add(root, self._model.get("fields"), (), "fields")
        buttons = QTreeWidgetItem([tr("designer_buttons_node")
                                   if "buttons" in self._model else tr("designer_buttons_default_node")])
        root.addChild(buttons)
        add(buttons, self._model.get("buttons"), (), "buttons")
        self._outline.expandAll()
        self._filling = False
        self._sync_outline()

    def _sync_outline(self) -> None:
        self._filling = True
        wanted = set(self._selected())
        pending = [self._outline.topLevelItem(i) for i in range(self._outline.topLevelItemCount())]
        while pending:
            item = pending.pop()
            if item is None:
                continue
            path = item.data(0, Qt.ItemDataRole.UserRole)
            item.setSelected(bool(path) and tuple(path) in wanted)
            pending.extend(item.child(i) for i in range(item.childCount()))
        self._filling = False

    # -- editing -----------------------------------------------------------------

    def _property_edited(self, key: str, value: Any) -> None:
        selected = self._selected()
        if key == "layout" and not selected:
            self._switch_layout(value)
            return

        def edit(model: dict) -> None:
            targets = [_get(model, p) for p in selected] if selected else [model]
            for target in targets:
                if value is None:
                    target.pop(key, None)
                else:
                    target[key] = copy.deepcopy(value)

        self._change(edit, rebuild_panel=False)

    def _apply_geometry(self, changes: dict) -> None:
        def edit(model: dict) -> None:
            for path, (x, y, w, h) in changes.items():
                _get(model, path).update({"x": x, "y": y, "w": w, "h": h})

        self._change(edit, rebuild_panel=True)

    def _move_into(self, path: Path_, group: Optional[Path_], pos: QPoint) -> None:
        """Drag and drop of one field into a group (None: onto the form)."""
        moved: list[Path_] = []

        def edit(model: dict) -> None:
            source = _get(model, path[:-1])
            item = source[path[-1]]
            owner = _get(model, group) if group else model
            target = owner.setdefault("fields", [])
            item["x"] = max(0, self._canvas.snapped(pos.x()))
            item["y"] = max(0, self._canvas.snapped(pos.y()))
            # Append first, then remove: removing first could shift the
            # index in *group*'s path.
            target.append(item)
            del source[next(i for i, x in enumerate(source) if x is item)]
            found = _path_of(model, item)
            if found is not None:
                moved.append(found)

        self._change(edit, selection=[])
        self._canvas.set_selection(moved)

    def _add_field(self, group: Optional[Path_], pos: Optional[QPoint]) -> None:
        current = self._toolbox.currentItem()
        if current is None:
            return
        kind = current.data(Qt.ItemDataRole.UserRole)
        absolute = self._model.get("layout") == "absolute"
        selected = self._selected()
        if group is None and pos is None and selected:
            first = _get(self._model, selected[0])
            if first.get("type") == "group" and selected[0][0] == "fields":
                group = selected[0]
            elif selected[0][0] == "fields" and len(selected[0]) > 2:
                group = selected[0][:-2]
        item: dict[str, Any] = {"type": kind, "id": unique_id(self._model, kind)}
        item.update(copy.deepcopy(NEW_PROPS.get(kind, {})))
        if absolute:
            w, h = NEW_SIZE.get(kind, (120, 24))
            if pos is None:
                siblings = _get(self._model, group).get("fields", []) if group else self._model.get("fields", [])
                bottom = 0
                for sibling in siblings if isinstance(siblings, list) else []:
                    if isinstance(sibling, dict):
                        bottom = max(bottom, int(sibling.get("y", 0) or 0) + int(sibling.get("h", 24) or 24))
                pos = QPoint(8, self._canvas.snapped(bottom + 8) if siblings else 8)
            item.update({"x": pos.x(), "y": pos.y(), "w": w, "h": h})
        elif kind not in ("label", "checkbox", "group", "button", "separator", "html", "image"):
            item["label"] = kind.capitalize()
        added: list[Path_] = []

        def edit(model: dict) -> None:
            container = _get(model, group).setdefault("fields", []) if group else model.setdefault("fields", [])
            container.append(item)
            added.append((group or ()) + ("fields", len(container) - 1))

        self._change(edit)
        self._canvas.set_selection(added)
        self._grow_form(added[0] if added else None)

    def _grow_form(self, path: Optional[Path_]) -> None:
        """Make an absolute form with a fixed size big enough for a new field."""
        if path is None or self._model.get("layout") != "absolute" or len(path) != 2:
            return
        item = _get(self._model, path)
        need_w = int(item.get("x", 0)) + int(item.get("w", 0)) + 8
        need_h = int(item.get("y", 0)) + int(item.get("h", 0)) + 8
        width, height = self._model.get("width"), self._model.get("height")
        if (isinstance(width, int) and width < need_w) or (isinstance(height, int) and height < need_h):
            def edit(model: dict) -> None:
                if isinstance(width, int):
                    model["width"] = max(width, need_w)
                if isinstance(height, int):
                    model["height"] = max(height, need_h)
            self._change(edit, selection=[path])

    def _delete(self) -> None:
        selected = self._selected()
        if not selected:
            return

        def edit(model: dict) -> None:
            # Deepest and last first, so the other paths stay valid.
            for path in sorted(selected, key=lambda p: (len(p), p), reverse=True):
                if self._contains_path(model, path):
                    del _get(model, path[:-1])[path[-1]]

        self._change(edit, selection=[])

    @staticmethod
    def _contains_path(model: dict, path: Path_) -> bool:
        try:
            _get(model, path)
            return True
        except (KeyError, IndexError, TypeError):
            return False

    def _select_all(self) -> None:
        fields = self._model.get("fields")
        if isinstance(fields, list):
            self._canvas.set_selection([("fields", i) for i, f in enumerate(fields)
                                        if isinstance(f, dict)])

    def _copy_fields(self) -> None:
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QPlainTextEdit)):
            focus.copy()
            return
        self._copied = [copy.deepcopy(_get(self._model, p)) for p in self._selected()]

    def _paste_fields(self) -> None:
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QPlainTextEdit)):
            focus.paste()
            return
        if self._copied:
            self._insert_copies(self._copied)

    def _duplicate(self) -> None:
        self._insert_copies([copy.deepcopy(_get(self._model, p)) for p in self._selected()])

    def _insert_copies(self, items: list[dict]) -> None:
        if not items:
            return
        added: list[Path_] = []
        step = self._canvas.grid

        def renumber(model: dict, item: dict) -> None:
            if isinstance(item.get("id"), str):
                base = item["id"].rstrip("0123456789") or item.get("type", "field")
                item["id"] = unique_id(model, base)
            for child in item.get("fields", []) if isinstance(item.get("fields"), list) else []:
                if isinstance(child, dict):
                    renumber(model, child)

        def edit(model: dict) -> None:
            for original in items:
                item = copy.deepcopy(original)
                container_key = "buttons" if "type" not in item and isinstance(model.get("buttons"), list) \
                    else "fields"
                container = model.setdefault(container_key, [])
                renumber(model, item)
                if isinstance(item.get("x"), int):
                    item["x"] += step
                if isinstance(item.get("y"), int):
                    item["y"] += step
                container.append(item)
                added.append((container_key, len(container) - 1))

        self._change(edit)
        self._canvas.set_selection(added)

    def _reorder(self, how: str) -> None:
        selected = self._selected()
        if len(selected) != 1:
            return
        path = selected[0]
        index = path[-1]
        new_index: list[int] = []

        def edit(model: dict) -> None:
            container = _get(model, path[:-1])
            item = container.pop(index)
            target = {"up": max(0, index - 1), "down": min(len(container), index + 1),
                      "front": len(container), "back": 0}[how]
            container.insert(target, item)
            new_index.append(target)

        self._change(edit, selection=[])
        if new_index:
            self._canvas.set_selection([path[:-1] + (new_index[0],)])

    def _indent(self) -> None:
        """Move the selected field into the group just before it."""
        selected = self._selected()
        if len(selected) != 1 or selected[0][-1] == 0 or selected[0][-2] != "fields":
            return
        path = selected[0]
        container = _get(self._model, path[:-1])
        before = container[path[-1] - 1]
        if not isinstance(before, dict) or before.get("type") != "group":
            return
        group = path[:-1] + (path[-1] - 1,)
        self._move_into(path, group, QPoint(8, 8 + 24 * len(before.get("fields", []))))

    def _outdent(self) -> None:
        """Move the selected field out of its group, right after it."""
        selected = self._selected()
        if len(selected) != 1 or len(selected[0]) < 4:
            return
        path = selected[0]
        group = path[:-2]
        moved: list[Path_] = []

        def edit(model: dict) -> None:
            item = _get(model, path[:-1]).pop(path[-1])
            group_item = _get(model, group)
            outer = _get(model, group[:-1])
            if isinstance(item.get("x"), int):
                item["x"] += int(group_item.get("x", 0) or 0)
            if isinstance(item.get("y"), int):
                item["y"] += int(group_item.get("y", 0) or 0)
            outer.insert(group[-1] + 1, item)
            moved.append(group[:-1] + (group[-1] + 1,))

        self._change(edit, selection=[])
        self._canvas.set_selection(moved)

    def _align(self, how: str) -> None:
        selected = [p for p in self._selected() if p[0] == "fields"]
        if len(selected) < 2 or self._model.get("layout") != "absolute":
            return
        geometry = {p: g for p in selected if (g := self._canvas.geometry_of(p)) is not None}
        if selected[0] not in geometry:
            return
        ref = geometry[selected[0]]
        changes = {}
        for path in selected[1:]:
            if path not in geometry:
                continue
            geo = geometry[path]
            x, y, w, h = geo.x(), geo.y(), geo.width(), geo.height()
            if how == "left":
                x = ref.x()
            elif how == "right":
                x = ref.x() + ref.width() - w
            elif how == "top":
                y = ref.y()
            elif how == "bottom":
                y = ref.y() + ref.height() - h
            elif how == "hcenter":
                x = ref.x() + (ref.width() - w) // 2
            elif how == "vcenter":
                y = ref.y() + (ref.height() - h) // 2
            elif how == "width":
                w = ref.width()
            elif how == "height":
                h = ref.height()
            changes[path] = (max(0, x), max(0, y), w, h)
        self._apply_geometry(changes)

    def _set_buttons(self, how: str) -> None:
        def edit(model: dict) -> None:
            if how == "default":
                model.pop("buttons", None)
            elif how == "none":
                model["buttons"] = []
            else:
                if not isinstance(model.get("buttons"), list):
                    model["buttons"] = [
                        {"id": "ok", "label": tr("macro_form_ok"), "default": True},
                        {"id": "cancel", "label": tr("cancel"), "cancel": True},
                    ]
                model["buttons"].append({"id": unique_id(model, "button"), "label": "Button"})

        self._change(edit, selection=[])

    def _switch_layout(self, layout: Any) -> None:
        """Change the layout, converting the positions: to absolute from
        where the auto layout put the fields (captions become label
        fields, as in GSAK); to auto in top-to-bottom order."""
        current = self._model.get("layout", "auto")
        if layout not in ("auto", "absolute") or self._canvas.error:
            def set_layout(model: dict) -> None:
                if layout is None:
                    model.pop("layout", None)
                else:
                    model["layout"] = layout
            self._change(set_layout)
            return
        if layout == current:
            return
        if layout == "absolute":
            items = self._canvas._items
            positions = {p: g for p in items
                         if p[0] == "fields" and (g := self._canvas.geometry_of(p)) is not None}
            # A caption sits in the same container as its field (QFormLayout).
            captions = {p: item.caption.geometry() for p, item in items.items()
                        if p[0] == "fields" and item.caption is not None and item.spec.label}

            def to_absolute(model: dict) -> None:
                model["layout"] = "absolute"
                body = self._canvas.body_rect()
                model["width"], model["height"] = body.width(), body.height()
                # Captions become label fields, as in GSAK; deepest first.
                for path in sorted(positions, key=lambda p: (len(p), p), reverse=True):
                    geo = positions[path]
                    item = _get(model, path)
                    item.update({"x": geo.x(), "y": geo.y(), "w": geo.width(), "h": geo.height()})
                    cap = captions.get(path)
                    if cap is not None:
                        label = {"type": "label", "id": unique_id(model, "label"),
                                 "label": item.get("label", ""), "x": cap.x(), "y": cap.y(),
                                 "w": cap.width(), "h": cap.height()}
                        _get(model, path[:-1]).insert(path[-1], label)

            self._change(to_absolute, selection=[])
        else:
            def to_auto(model: dict) -> None:
                model["layout"] = "auto"
                model.pop("height", None)

                def strip(items: Any) -> None:
                    if not isinstance(items, list):
                        return
                    items.sort(key=lambda i: (i.get("y", 0), i.get("x", 0))
                               if isinstance(i, dict) and isinstance(i.get("y", 0), int)
                               and isinstance(i.get("x", 0), int) else (0, 0))
                    for item in items:
                        if isinstance(item, dict):
                            for key in ("x", "y", "w", "h"):
                                item.pop(key, None)
                            strip(item.get("fields"))

                strip(model.get("fields"))

            self._change(to_auto, selection=[])

    # -- view ------------------------------------------------------------------

    def _toggle_grid(self) -> None:
        self._canvas.show_grid = self._act_grid.isChecked()
        self._canvas.update()

    def _toggle_snap(self) -> None:
        self._canvas.snap = self._act_snap.isChecked()

    def _grid_step(self) -> None:
        step, ok = QInputDialog.getInt(self, tr("designer_grid_step"), tr("designer_grid_label"),
                                       self._canvas.grid, 2, 64)
        if ok:
            self._canvas.grid = step
            self._canvas.update()

    # -- import / export --------------------------------------------------------

    def _replace_model(self, model: dict, notes: list[str]) -> None:
        def replace(current: dict) -> None:
            current.clear()
            current.update(copy.deepcopy(model))

        self._change(replace, selection=[])
        if notes:
            QMessageBox.information(self, tr("designer_title"),
                                    tr("designer_import_notes") + "\n\n• " + "\n• ".join(notes))

    def _confirm_replace(self) -> bool:
        if not self._model.get("fields"):
            return True
        answer = QMessageBox.question(self, tr("designer_title"), tr("designer_replace_msg"))
        return answer == QMessageBox.StandardButton.Yes

    def _new_form(self) -> None:
        if self._confirm_replace():
            self._replace_model(new_model(), [])

    def _paste_form(self) -> None:
        text = QApplication.clipboard().text()
        try:
            loaded = self.load_text(text)
        except ValueError as exc:
            QMessageBox.warning(self, tr("designer_title"), str(exc))
            return
        if loaded is not None and self._confirm_replace():
            self._replace_model(*loaded)

    def _import_gsak_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("designer_import_gsak"), "",
                                              tr("designer_gsak_filter"))
        if not path:
            return
        raw = Path(path).read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
        try:
            loaded = self.load_text(text, gsak_only=True)
        except ValueError as exc:
            QMessageBox.warning(self, tr("designer_title"), str(exc))
            return
        if loaded is not None and self._confirm_replace():
            self._replace_model(*loaded)

    def load_text(self, text: str, gsak_only: bool = False) -> Optional[tuple[dict, list[str]]]:
        """A form from text: a GSAK form block, or Lua with a form table.
        Asks which one when there are several; None if cancelled. Raises
        ValueError when there is none."""
        gsak = find_gsak_forms(text)
        if gsak:
            form = gsak[0]
            if len(gsak) > 1:
                names = [f"{f.var} — {f.caption}" for f in gsak]
                name, ok = QInputDialog.getItem(self, tr("designer_title"),
                                                tr("designer_pick_form"), names, 0, False)
                if not ok:
                    return None
                form = gsak[names.index(name)]
            return convert(form)
        if gsak_only:
            raise ValueError(tr("designer_no_form"))
        spans = find_form_tables(text)
        if not spans:
            raise ValueError(tr("designer_no_form"))
        start, end = spans[0]
        try:
            model = parse_table(text[start:end])
        except LuaSyntaxError as exc:
            raise ValueError(tr("designer_parse_error", msg=str(exc))) from None
        if not isinstance(model, dict):
            raise ValueError(tr("designer_no_form"))
        return model, []

    def _copy_lua(self) -> None:
        QApplication.clipboard().setText(self.lua())
        self._status.setText(tr("designer_copied"))

    def _preview(self) -> None:
        try:
            form = preview_form(self._model)
        except MacroError as exc:
            QMessageBox.warning(self, tr("designer_title"), str(exc))
            return
        form.title = tr("designer_preview_title", title=form.title or tr("macro_title"))
        FormDialog(form, None, self).run()

    def _accept(self) -> None:
        # Commit an editor that still has the focus.
        focus = QApplication.focusWidget()
        if focus is not None:
            focus.clearFocus()
        if self._canvas.error:
            answer = QMessageBox.question(self, tr("designer_title"),
                                          tr("designer_error_msg", msg=self._canvas.error))
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.accept()


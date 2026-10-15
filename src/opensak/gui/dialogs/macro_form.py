"""
src/opensak/gui/dialogs/macro_form.py — the dialogs of the macro UI
functions (#982): opensak.form{}, message, input, choose, choose_folder
and progress.

build_form() turns a validated Form (macro/forms.py) into widgets, in the
auto layout (a QFormLayout per level) or the absolute one (fixed
geometry, like GSAK). FormDialog shows them and is the FormController the
runtime drives while callbacks run; the form designer reuses
build_form() for its canvas and preview.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QDate, QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QButtonGroup, QCheckBox, QComboBox, QDateEdit,
    QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QProgressDialog, QPushButton, QRadioButton, QScrollArea, QSizePolicy, QTextBrowser,
    QToolButton, QVBoxLayout, QWidget,
)

from opensak.lang import tr
from opensak.macro.forms import (
    Field,
    Form,
    FormEvents,
    check_required,
    find_option,
    format_number,
    initial_value,
)

# Margin around the fields of an absolute form whose size is not given.
ABSOLUTE_MARGIN = 8
# Widest a number field may go (QDoubleSpinBox needs a range).
NUMBER_LIMIT = 1e12
# progress.update() lets Qt paint at most this often (seconds).
PROGRESS_INTERVAL = 0.05


def _shown(option: Any) -> str:
    return format_number(option) if isinstance(option, (int, float)) else str(option)


# ── Field widgets ────────────────────────────────────────────────────────────


class FieldWidget:
    """The widget(s) of one field and access to its value."""

    def __init__(self, spec: Field, widget: QWidget, caption: Optional[QLabel] = None):
        self.spec = spec
        self.widget = widget
        # The label on the left in the auto layout
        self.caption = caption

    def value(self) -> Any:
        return None

    def set_value(self, value: Any) -> None:
        pass

    def set_options(self) -> None:
        pass

    def set_label(self) -> None:
        if self.caption is not None:
            self.caption.setText(self.spec.label)

    def connect(self, changed: Callable[[], None]) -> None:
        """Call *changed* when the user has changed the value."""

    def focus_widget(self) -> QWidget:
        return self.widget


class _Text(FieldWidget):
    def __init__(self, spec: Field):
        edit = QLineEdit()
        edit.setPlaceholderText(spec.get("placeholder", ""))
        if spec.get("password"):
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setReadOnly(bool(spec.get("readonly")))
        super().__init__(spec, edit)
        self.edit = edit

    def value(self) -> str:
        return self.edit.text()

    def set_value(self, value: Any) -> None:
        self.edit.setText(str(value or ""))

    def connect(self, changed) -> None:
        self.edit.editingFinished.connect(changed)


class _Number(FieldWidget):
    def __init__(self, spec: Field):
        box = QDoubleSpinBox()
        box.setDecimals(spec.get("decimals", 0))
        box.setRange(spec.get("min", -NUMBER_LIMIT), spec.get("max", NUMBER_LIMIT))
        box.setSingleStep(spec.get("step", 1.0))
        box.setSuffix(spec.get("suffix", ""))
        # valueChanged only when the user is done (Enter, focus out, arrows)
        box.setKeyboardTracking(False)
        super().__init__(spec, box)
        self.box = box

    def value(self) -> float | int:
        value = round(self.box.value(), self.box.decimals())
        return int(value) if self.box.decimals() == 0 else value

    def set_value(self, value: Any) -> None:
        self.box.setValue(float(value or 0))

    def connect(self, changed) -> None:
        self.box.valueChanged.connect(lambda _v: changed())


class _FocusOut(QObject):
    """Calls *callback* when the watched widget loses the focus."""

    def __init__(self, widget: QWidget, callback: Callable[[], None]):
        super().__init__(widget)
        self._callback = callback
        widget.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.FocusOut:
            self._callback()
        return False


class _Memo(FieldWidget):
    def __init__(self, spec: Field):
        edit = QPlainTextEdit()
        edit.setPlaceholderText(spec.get("placeholder", ""))
        edit.setReadOnly(bool(spec.get("readonly")))
        if not spec.get("wrap", True):
            edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        if spec.get("monospace"):
            edit.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        edit.setTabChangesFocus(True)
        super().__init__(spec, edit)
        self.edit = edit

    def value(self) -> str:
        return self.edit.toPlainText()

    def set_value(self, value: Any) -> None:
        self.edit.setPlainText(str(value or ""))

    def connect(self, changed) -> None:
        _FocusOut(self.edit, changed)


class _CheckBox(FieldWidget):
    def __init__(self, spec: Field):
        box = QCheckBox(spec.label)
        super().__init__(spec, box)
        self.box = box

    def value(self) -> bool:
        return self.box.isChecked()

    def set_value(self, value: Any) -> None:
        self.box.setChecked(bool(value))

    def set_label(self) -> None:
        self.box.setText(self.spec.label)

    def connect(self, changed) -> None:
        self.box.toggled.connect(lambda _on: changed())


class _Choice(FieldWidget):
    def __init__(self, spec: Field):
        combo = QComboBox()
        combo.setEditable(bool(spec.get("editable")))
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(8)
        super().__init__(spec, combo)
        self.combo = combo
        # The options shown; spec.options changes before set_options().
        self.options: list[Any] = []
        self.set_options()

    def value(self) -> Any:
        options = self.options
        if self.combo.isEditable():
            text = self.combo.currentText()
            index = find_option(options, text)
            return options[index] if index >= 0 else (text or None)
        index = self.combo.currentIndex()
        return options[index] if 0 <= index < len(options) else None

    def set_value(self, value: Any) -> None:
        index = find_option(self.options, value) if value is not None else -1
        self.combo.setCurrentIndex(index)
        if index < 0 and self.combo.isEditable():
            self.combo.setEditText("" if value is None else _shown(value))

    def set_options(self) -> None:
        current = self.value() if self.combo.count() else None
        self.options = list(self.spec.options)
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItems([_shown(o) for o in self.options])
        self.combo.blockSignals(False)
        self.set_value(current)

    def connect(self, changed) -> None:
        self.combo.activated.connect(lambda _i: changed())
        line = self.combo.lineEdit()
        if self.combo.isEditable() and line is not None:
            line.editingFinished.connect(changed)


class _OptionGrid(FieldWidget):
    """Radio buttons or check boxes for the options, in `columns` columns."""

    exclusive = False

    def __init__(self, spec: Field):
        box = QWidget()
        self.grid = QGridLayout(box)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.group = QButtonGroup(box)
        self.group.setExclusive(self.exclusive)
        # The options shown; spec.options changes before set_options().
        self.options: list[Any] = []
        super().__init__(spec, box)
        self.set_options()

    def _make(self, text: str) -> QAbstractButton:
        raise NotImplementedError

    def set_options(self) -> None:
        current = self.value() if self.group.buttons() else None
        for old in self.group.buttons():
            self.group.removeButton(old)
            old.deleteLater()
        self.options = list(self.spec.options)
        columns = self.spec.get("columns", 1)
        rows = max(1, -(-len(self.options) // columns))
        for i, option in enumerate(self.options):
            button = self._make(_shown(option))
            self.group.addButton(button, i)
            # Fill column by column, like GSAK's CheckListBox.
            self.grid.addWidget(button, i % rows, i // rows)
        if current is not None:
            self.set_value(current)


class _Radio(_OptionGrid):
    exclusive = True

    def _make(self, text: str) -> QRadioButton:
        return QRadioButton(text)

    def value(self) -> Any:
        index = self.group.checkedId()
        return self.options[index] if 0 <= index < len(self.options) else None

    def set_value(self, value: Any) -> None:
        index = find_option(self.options, value) if value is not None else -1
        self.group.setExclusive(False)
        for button in self.group.buttons():
            button.setChecked(self.group.id(button) == index)
        self.group.setExclusive(True)

    def connect(self, changed) -> None:
        self.group.idClicked.connect(lambda _i: changed())


class _CheckList(_OptionGrid):
    def __init__(self, spec: Field):
        super().__init__(spec)
        inner = self.widget
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setFrameShape(QFrame.Shape.StyledPanel)
        inner.setContentsMargins(4, 4, 4, 4)
        self.widget = scroll

    def _make(self, text: str) -> QCheckBox:
        return QCheckBox(text)

    def value(self) -> list[Any]:
        return [self.options[self.group.id(b)] for b in
                sorted(self.group.buttons(), key=self.group.id) if b.isChecked()]

    def set_value(self, value: Any) -> None:
        wanted = {find_option(self.options, v) for v in (value or [])}
        for button in self.group.buttons():
            button.setChecked(self.group.id(button) in wanted)

    def connect(self, changed) -> None:
        self.group.idToggled.connect(lambda _i, _on: changed())


class _Date(FieldWidget):
    def __init__(self, spec: Field):
        edit = QDateEdit()
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("yyyy-MM-dd")
        edit.setKeyboardTracking(False)
        if spec.get("min"):
            edit.setMinimumDate(QDate.fromString(spec.get("min"), Qt.DateFormat.ISODate))
        if spec.get("max"):
            edit.setMaximumDate(QDate.fromString(spec.get("max"), Qt.DateFormat.ISODate))
        super().__init__(spec, edit)
        self.edit = edit

    def value(self) -> str:
        return self.edit.date().toString(Qt.DateFormat.ISODate)

    def set_value(self, value: Any) -> None:
        text = value or date.today().isoformat()
        self.edit.setDate(QDate.fromString(text, Qt.DateFormat.ISODate))

    def connect(self, changed) -> None:
        self.edit.dateChanged.connect(lambda _d: changed())


class _Path(FieldWidget):
    def __init__(self, spec: Field, browse: Optional[Callable[[], None]]):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        edit = QLineEdit()
        edit.setPlaceholderText(spec.get("placeholder", ""))
        button = QToolButton()
        button.setText("…")
        button.setToolTip(tr("macro_form_browse"))
        if browse is not None:
            button.clicked.connect(browse)
        row.addWidget(edit, 1)
        row.addWidget(button)
        super().__init__(spec, box)
        self.edit = edit

    def value(self) -> str:
        return self.edit.text()

    def set_value(self, value: Any) -> None:
        self.edit.setText(str(value or ""))

    def connect(self, changed) -> None:
        self.edit.editingFinished.connect(changed)

    def focus_widget(self) -> QWidget:
        return self.edit


class _Label(FieldWidget):
    def __init__(self, spec: Field):
        label = QLabel(spec.label)
        label.setWordWrap(True)
        label.setOpenExternalLinks(False)
        align = {"center": Qt.AlignmentFlag.AlignHCenter, "right": Qt.AlignmentFlag.AlignRight}
        label.setAlignment(align.get(spec.get("align"), Qt.AlignmentFlag.AlignLeft)
                           | Qt.AlignmentFlag.AlignVCenter)
        super().__init__(spec, label)
        self.label = label

    def set_label(self) -> None:
        self.label.setText(self.spec.label)


class _Separator(FieldWidget):
    def __init__(self, spec: Field):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        super().__init__(spec, line)


class _Group(FieldWidget):
    def __init__(self, spec: Field):
        self.box = QGroupBox(spec.label)
        super().__init__(spec, self.box)

    def set_label(self) -> None:
        self.box.setTitle(self.spec.label)


class _Button(FieldWidget):
    def __init__(self, spec: Field):
        self.button = QPushButton(spec.label)
        self.button.setAutoDefault(False)
        self.button.setDefault(bool(spec.get("default")))
        super().__init__(spec, self.button)

    def set_label(self) -> None:
        self.button.setText(self.spec.label)


class _Image(FieldWidget):
    def __init__(self, spec: Field):
        label = QLabel()
        pixmap = QPixmap()
        if spec.get("image"):
            pixmap.loadFromData(spec.get("image"))
        if pixmap.isNull():
            label.setText(tr("macro_form_no_image"))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFrameShape(QFrame.Shape.StyledPanel)
        else:
            label.setPixmap(pixmap)
            label.setScaledContents(bool(spec.get("scale")))
        super().__init__(spec, label)


class _SafeBrowser(QTextBrowser):
    """Shows HTML without loading anything: no images or style sheets from
    disk or the network, no link navigation."""

    def loadResource(self, kind, url):  # noqa: N802 (Qt API)
        return None


class _Html(FieldWidget):
    def __init__(self, spec: Field):
        view = _SafeBrowser()
        view.setOpenLinks(False)
        view.setOpenExternalLinks(False)
        view.setHtml(spec.get("html", ""))
        super().__init__(spec, view)


# Types whose `label` is part of the widget, not a caption on the left.
_INLINE_CAPTION = frozenset({"checkbox", "button", "group", "label", "separator"})


def _make_field(spec: Field, browse: Optional[Callable[[Field], None]]) -> FieldWidget:
    kind = spec.type
    if kind == "text":
        return _Text(spec)
    if kind == "number":
        return _Number(spec)
    if kind == "memo":
        return _Memo(spec)
    if kind == "checkbox":
        return _CheckBox(spec)
    if kind == "choice":
        return _Choice(spec)
    if kind == "radio":
        return _Radio(spec)
    if kind == "checklist":
        return _CheckList(spec)
    if kind == "date":
        return _Date(spec)
    if kind in ("file", "folder"):
        return _Path(spec, (lambda: browse(spec)) if browse else None)
    if kind == "label":
        return _Label(spec)
    if kind == "separator":
        return _Separator(spec)
    if kind == "group":
        return _Group(spec)
    if kind == "button":
        return _Button(spec)
    if kind == "image":
        return _Image(spec)
    if kind == "html":
        return _Html(spec)
    raise ValueError(f"unknown field type {kind}")  # parse_form() refuses it first


def _style(item: FieldWidget) -> None:
    spec, widget = item.spec, item.widget
    if spec.get("tooltip"):
        widget.setToolTip(spec.get("tooltip"))
        if item.caption is not None:
            item.caption.setToolTip(spec.get("tooltip"))
    target = item.caption if item.caption is not None else widget
    if any(spec.get(k) for k in ("bold", "italic", "font_size")):
        font = QFont(target.font())
        font.setBold(bool(spec.get("bold")))
        font.setItalic(bool(spec.get("italic")))
        if spec.get("font_size"):
            font.setPointSize(spec.get("font_size"))
        target.setFont(font)
    if spec.get("color"):
        target.setStyleSheet(f"color: {spec.get('color')};")
    if spec.get("enabled") is False:
        widget.setEnabled(False)
        if item.caption is not None:
            item.caption.setEnabled(False)


class FormView:
    """The widgets built for a form: *body* holds the fields; *items* maps
    every field (and button) to its FieldWidget, in form order."""

    def __init__(self) -> None:
        self.body = QWidget()
        self.items: dict[Field, FieldWidget] = {}
        self.button_row: Optional[QWidget] = None


def build_form(
    form: Form,
    browse: Optional[Callable[[Field], None]] = None,
    with_buttons: bool = True,
) -> FormView:
    """Widgets for *form*. *browse* is called by the Browse buttons of file
    and folder fields. Values are not filled in (see FormDialog)."""
    view = FormView()
    if form.layout == "absolute":
        _build_absolute(form.fields, view.body, view, browse)
        width, height = form.width, form.height
        if width is None or height is None:
            right = bottom = 0
            for child in view.body.children():
                if isinstance(child, QWidget):
                    right = max(right, child.geometry().right() + 1)
                    bottom = max(bottom, child.geometry().bottom() + 1)
            width = width or right + ABSOLUTE_MARGIN
            height = height or bottom + ABSOLUTE_MARGIN
        view.body.setFixedSize(max(width, 50), max(height, 30))
    else:
        layout = QFormLayout(view.body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        _build_auto(form.fields, layout, view, browse)
        if form.width:
            view.body.setMinimumWidth(form.width)
        if form.height:
            view.body.setMinimumHeight(form.height)
    if with_buttons and form.buttons:
        row = QWidget()
        box = QHBoxLayout(row)
        box.setContentsMargins(0, 0, 0, 0)
        box.addStretch(1)
        for spec in form.buttons:
            item = _Button(spec)
            _style(item)
            if spec.get("visible") is False:
                item.widget.hide()
            box.addWidget(item.widget)
            view.items[spec] = item
        view.button_row = row
    return view


def _build_auto(fields: list[Field], layout: QFormLayout, view: FormView, browse) -> None:
    for spec in fields:
        item = _make_field(spec, browse)
        if spec.type not in _INLINE_CAPTION:
            item.caption = QLabel(spec.label)
            item.caption.setBuddy(item.focus_widget())
        _style(item)
        view.items[spec] = item
        if spec.get("w"):
            item.widget.setMinimumWidth(spec.get("w"))
        if spec.get("h"):
            item.widget.setMinimumHeight(spec.get("h"))
        if spec.type == "group":
            inner = QFormLayout(item.widget)
            inner.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            _build_auto(spec.children, inner, view, browse)
        if item.caption is not None:
            layout.addRow(item.caption, item.widget)
        else:
            layout.addRow(item.widget)
        if spec.get("visible") is False:
            layout.setRowVisible(item.widget, False)


def _build_absolute(fields: list[Field], parent: QWidget, view: FormView, browse) -> None:
    for spec in fields:
        item = _make_field(spec, browse)
        _style(item)
        view.items[spec] = item
        widget = item.widget
        widget.setParent(parent)
        if spec.type == "group":
            _build_absolute(spec.children, widget, view, browse)
        hint = widget.sizeHint()
        width = spec.get("w") or max(hint.width(), 40)
        height = spec.get("h") or max(hint.height(), 16)
        if spec.type == "group" and not (spec.get("w") and spec.get("h")):
            right = bottom = 0
            for child in widget.children():
                if isinstance(child, QWidget):
                    right = max(right, child.geometry().right() + 1)
                    bottom = max(bottom, child.geometry().bottom() + 1)
            width = spec.get("w") or right + ABSOLUTE_MARGIN
            height = spec.get("h") or bottom + ABSOLUTE_MARGIN
        widget.setGeometry(spec.get("x", 0), spec.get("y", 0), width, height)
        if spec.get("visible") is False:
            widget.hide()


# ── The form dialog ──────────────────────────────────────────────────────────


class FormDialog(QDialog):
    """A macro's form, and the FormController the runtime uses while it is
    open. run() shows it and returns (values, closer) or None."""

    def __init__(self, form: Form, events: Optional[FormEvents], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._form = form
        self._events = events
        self._closed = False
        self._closer: Optional[str] = None
        self._values: dict[str, Any] = {}
        # The last value seen per field, so on_change fires only on a change.
        self._last: dict[Field, Any] = {}
        self.setWindowTitle(form.title or tr("macro_title"))
        self.view = build_form(form, browse=self._browse)
        layout = QVBoxLayout(self)
        if form.layout == "absolute":
            layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)
            layout.addWidget(self.view.body)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(self.view.body)
            scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            layout.addWidget(scroll, 1)
        if self.view.button_row is not None:
            layout.addWidget(self.view.button_row)
        for spec, item in self.view.items.items():
            if spec.has_value:
                item.set_value(initial_value(spec))
                self._last[spec] = item.value()
                item.connect(self._change_handler(spec))
            elif isinstance(item, _Button):
                item.button.clicked.connect(self._click_handler(spec))
        if form.layout != "absolute":
            self._fit_auto_size(scroll)
        if form.timeout:
            QTimer.singleShot(int(form.timeout * 1000), lambda: self.close_form("timeout"))

    def _fit_auto_size(self, scroll: QScrollArea) -> None:
        """Show the whole form when it fits on the screen, else scroll."""
        hint = self.view.body.sizeHint()
        screen = (self.parentWidget() or self).screen().availableGeometry()
        scroll.setMinimumSize(min(max(hint.width(), 320), int(screen.width() * 0.9)),
                              min(hint.height() + 2, int(screen.height() * 0.8)))

    def _change_handler(self, spec: Field) -> Callable[[], None]:
        return lambda: self._user_changed(spec)

    def _click_handler(self, spec: Field) -> Callable[[], None]:
        return lambda: self._clicked(spec)

    def run(self) -> Optional[tuple[dict[str, Any], str]]:
        if self._events is not None and self._form.on_open is not None:
            QTimer.singleShot(0, lambda: self._emit("open", None))
        self.exec()
        if self._closer is None:
            return None
        return self._values, self._closer

    # -- events ------------------------------------------------------------

    def _emit(self, event: str, spec: Optional[Field]) -> Optional[str]:
        if self._events is None or self._closed:
            return None
        return self._events(self, event, spec)

    def _user_changed(self, spec: Field) -> None:
        if self._closed:
            return
        value = self.view.items[spec].value()
        if value == self._last.get(spec):
            return
        self._last[spec] = value
        self._emit("change", spec)
        if spec.get("exit_on_change") and not self._closed:
            self.close_form(spec.id)

    def _browse(self, spec: Field) -> None:
        if self._events is None:
            return
        self._emit("browse", spec)
        self._user_changed(spec)

    def _clicked(self, spec: Field) -> None:
        if self._closed:
            return
        if spec.get("on_click") is not None:
            self._emit("click", spec)
        elif spec.get("cancel"):
            self.close_form(None)
        else:
            self._confirm(spec)

    def _confirm(self, spec: Field) -> None:
        missing = check_required(self._form, self.read_values())
        if missing is not None:
            QMessageBox.warning(self, self.windowTitle(),
                                tr("macro_form_required", field=missing.label or missing.id or ""))
            self.focus(missing)
            return
        message = self._emit("validate", spec)
        if self._closed:
            return
        if message:
            QMessageBox.warning(self, self.windowTitle(), message)
            return
        self.close_form(spec.id)

    def read_values(self) -> dict[str, Any]:
        return {spec.id: item.value() for spec, item in self.view.items.items()
                if spec.has_value and spec.id}

    def close_form(self, closer: Optional[str]) -> None:
        if self._closed:
            return
        self._closed = True
        if closer is not None:
            self._values = self.read_values()
        self._closer = closer
        if closer is None:
            super().reject()
        else:
            super().accept()

    def reject(self) -> None:  # Esc and the window's close button
        self.close_form(None)

    # -- FormController ----------------------------------------------------------

    def value(self, field: Field) -> Any:
        return self.view.items[field].value()

    def set_value(self, field: Field, value: Any) -> None:
        item = self.view.items[field]
        item.set_value(value)
        self._last[field] = item.value()

    def set_options(self, field: Field) -> None:
        item = self.view.items[field]
        item.set_options()
        self._last[field] = item.value()

    def set_enabled(self, field: Field, on: bool) -> None:
        item = self.view.items[field]
        item.widget.setEnabled(on)
        if item.caption is not None:
            item.caption.setEnabled(on)

    def set_visible(self, field: Field, on: bool) -> None:
        item = self.view.items[field]
        item.widget.setVisible(on)
        if item.caption is not None:
            item.caption.setVisible(on)

    def set_label(self, field: Field) -> None:
        self.view.items[field].set_label()

    def set_title(self, title: str) -> None:
        self.setWindowTitle(title)

    def focus(self, field: Field) -> None:
        self.view.items[field].focus_widget().setFocus()


# ── Simple dialogs ───────────────────────────────────────────────────────────


def show_message(parent: QWidget, text: str, title: str) -> None:
    box = QMessageBox(QMessageBox.Icon.Information, title or tr("macro_title"), text,
                      QMessageBox.StandardButton.Ok, parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.exec()


def ask_text(parent: QWidget, prompt: str, default: str, title: str) -> Optional[str]:
    text, ok = QInputDialog.getText(parent, title or tr("macro_title"), prompt,
                                    QLineEdit.EchoMode.Normal, default)
    return text if ok else None


def ask_choice(parent: QWidget, prompt: str, options: list[str], default: int,
               title: str) -> Optional[int]:
    dialog = QInputDialog(parent)
    dialog.setWindowTitle(title or tr("macro_title"))
    dialog.setLabelText(prompt)
    dialog.setComboBoxItems(options)
    dialog.setComboBoxEditable(False)
    if 0 <= default < len(options):
        dialog.setTextValue(options[default])
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    combo = dialog.findChild(QComboBox)
    if combo is not None and 0 <= combo.currentIndex() < len(options):
        return combo.currentIndex()
    text = dialog.textValue()
    return options.index(text) if text in options else None


def choose_folder_for_macro(parent: QWidget, title: str, start_dir: Path) -> Optional[Path]:
    """OpenSAK's folder dialog for opensak.choose_folder(); the caption
    always says that a macro asks."""
    caption = tr("macro_choose_folder")
    if title.strip():
        caption = tr("macro_choose_caption", caption=caption, title=title.strip())
    path = QFileDialog.getExistingDirectory(parent, caption, str(start_dir))
    return Path(path) if path else None


class MacroProgress:
    """The window of opensak.progress()."""

    def __init__(self, parent: QWidget, title: str, total: int):
        self._title = title
        self._dialog = QProgressDialog(title, tr("cancel"), 0, total, parent)
        self._dialog.setWindowTitle(tr("macro_title"))
        self._dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._dialog.setMinimumDuration(0)
        self._dialog.setAutoClose(False)
        self._dialog.setAutoReset(False)
        self._dialog.setMinimumWidth(360)
        self._dialog.setValue(0)
        self._dialog.show()
        self._painted = 0.0
        QApplication.processEvents()

    def update(self, value: Optional[int], text: str) -> bool:
        if value is not None and self._dialog.maximum() > 0:
            self._dialog.setValue(max(0, min(value, self._dialog.maximum())))
        if text:
            self._dialog.setLabelText(f"{self._title}\n{text}")
        now = time.monotonic()
        if now - self._painted >= PROGRESS_INTERVAL:
            self._painted = now
            QApplication.processEvents()
        return not self._dialog.wasCanceled()

    def close(self) -> None:
        self._dialog.close()
        self._dialog.deleteLater()


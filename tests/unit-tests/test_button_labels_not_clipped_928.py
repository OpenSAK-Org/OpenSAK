# tests/unit-tests/test_button_labels_not_clipped_928.py — no clipped button labels in any language (#928).
#
# #927 fixed text buttons that were capped with setMaximumWidth() /
# setFixedWidth(): the cap fitted the English or Danish label, but longer
# translations (de, de_CH, pl, cs, es, fr, pt, nl) were cut off. Users found
# it; nothing in CI would have. This file guards against it coming back.
#
# Two layers:
#
# - TestDialogsInAllLanguages builds the dialogs #927 touched in every
#   language in AVAILABLE_LANGUAGES and checks that each visible text button
#   is at least as wide as its own sizeHint() — the width Qt says its label
#   needs. Comparing against sizeHint() rather than pixel values keeps this
#   independent of platform, font and display scaling.
#
# - TestNoHardcodedTextButtonWidths scans the GUI source for the cause
#   itself — a width cap on a button created with a translatable label — so
#   a new dialog that isn't listed above is still covered.
#
# Buttons whose label is one or two characters ("ⓘ", "✕", "▲", "🗑", "⇄")
# are icon-like and deliberately narrow; both layers skip them.

import ast
import sys
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QApplication, QPushButton, QTabWidget, QWidget

from opensak.lang import AVAILABLE_LANGUAGES, current_language, load_language

pytestmark = pytest.mark.gui

GUI_DIR = Path(__file__).resolve().parents[2] / "src" / "opensak" / "gui"

# Labels this short are symbols, not words — intentionally narrow buttons.
ICON_MAX_CHARS = 2


def _is_icon_label(text: str) -> bool:
    return len(text.replace("&", "").strip()) <= ICON_MAX_CHARS


# ── Layer 1: build the dialogs in every language ──────────────────────────────

def _new_database():
    from opensak.gui.dialogs.database_dialog import NewDatabaseDialog
    return NewDatabaseDialog()


def _gps_export():
    from opensak.gui.dialogs.gps_dialog import GpsExportDialog
    return GpsExportDialog(caches=[])


def _dir_row():
    from opensak.gui.dialogs.widgets import DirRow
    return DirRow(Path.home())


def _cache_detail():
    from opensak.gui.cache_detail import CacheDetailPanel
    return CacheDetailPanel()


def _filter():
    from opensak.gui.dialogs.filter_dialog import FilterDialog
    return FilterDialog()


def _columns():
    from opensak.gui.dialogs.column_dialog import ColumnChooserDialog
    return ColumnChooserDialog()


def _coord_converter():
    from opensak.gui.dialogs.coord_converter_dialog import CoordConverterDialog
    return CoordConverterDialog(lat=55.0, lon=12.0)


def _projection():
    from opensak.gui.dialogs.projection_dialog import ProjectionDialog
    return ProjectionDialog(lat=55.0, lon=12.0)


def _midpoint():
    from opensak.gui.dialogs.midpoint_dialog import MidpointDialog
    return MidpointDialog(lat=55.0, lon=12.0)


def _corrected_coords():
    from opensak.gui.dialogs.corrected_coords_dialog import CorrectedCoordsDialog
    return CorrectedCoordsDialog("GC928", orig_lat=55.0, orig_lon=12.0)


# The dialogs #927 fixed (see the issue's scope list).
DIALOGS = {
    "new_database": _new_database,
    "gps_export": _gps_export,
    "dir_row": _dir_row,
    "cache_detail": _cache_detail,
    "filter": _filter,
    "columns": _columns,
    "coord_converter": _coord_converter,
    "projection": _projection,
    "midpoint": _midpoint,
    "corrected_coords": _corrected_coords,
}


@pytest.fixture
def language(request):
    previous = current_language()
    load_language(request.param)
    yield request.param
    load_language(previous)


@pytest.fixture(autouse=True)
def _no_saved_filter_profiles(monkeypatch):
    # The filter dialog lists saved profiles from disk; none are needed here.
    from opensak.gui.dialogs import filter_dialog as fd
    monkeypatch.setattr(fd.FilterProfile, "list_profiles", staticmethod(lambda: []))


def _clipped_buttons(root: QWidget) -> list[str]:
    """Visible text buttons under *root* that are narrower than their label needs."""
    QApplication.processEvents()
    problems = []
    for btn in root.findChildren(QPushButton):
        text = btn.text()
        if _is_icon_label(text) or not btn.isVisibleTo(root):
            continue
        need = btn.sizeHint().width()
        if btn.width() < need:
            problems.append(f"{text!r}: {btn.width()}px wide, label needs {need}px")
    return problems


def _check_every_tab(root: QWidget) -> list[str]:
    """
    Run the check once per tab page, so buttons on tabs that aren't shown
    by default are measured with a real layout too.
    """
    problems = _clipped_buttons(root)
    for tabs in root.findChildren(QTabWidget):
        original = tabs.currentIndex()
        for i in range(tabs.count()):
            tabs.setCurrentIndex(i)
            problems += _clipped_buttons(root)
        tabs.setCurrentIndex(original)
    return sorted(set(problems))


# Known exception, under investigation: on the Windows CI runner the
# column dialog's "💾 Save" button gets a few pixels less than its label
# needs in de, de_CH and fr (151 px in all three, so it is being squeezed by
# the layout, not given a hardcoded width). It does not reproduce on Linux
# or macOS, and may be specific to how the headless "offscreen" platform
# measures the emoji on Windows. Not strict: an XPASS there means it has
# gone away and this can be removed.
_KNOWN_WINDOWS_SQUEEZE = {("columns", "de"), ("columns", "de_CH"), ("columns", "fr")}


@pytest.mark.parametrize("language", sorted(AVAILABLE_LANGUAGES), indirect=True)
@pytest.mark.parametrize("name", list(DIALOGS))
class TestDialogsInAllLanguages:
    def test_buttons_not_clipped(self, qtbot, request, language, name):
        if sys.platform == "win32" and (name, language) in _KNOWN_WINDOWS_SQUEEZE:
            request.applymarker(pytest.mark.xfail(
                reason="column dialog Save button squeezed on Windows CI — see comment above",
                strict=False,
            ))
        widget = DIALOGS[name]()
        qtbot.addWidget(widget)
        widget.show()
        qtbot.waitExposed(widget)
        problems = _check_every_tab(widget)
        assert not problems, f"[{language}] {name}:\n  " + "\n  ".join(problems)


# ── Layer 2: no width caps on text buttons anywhere in the GUI source ─────────

_WIDTH_CAPS = {"setFixedWidth", "setMaximumWidth", "setFixedSize", "setMaximumSize"}
_BUTTON_CLASSES = {"QPushButton", "QToolButton"}


def _target_key(node: ast.AST) -> str | None:
    """`btn` or `self._btn` as a string key; None for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        return f"self.{node.attr}"
    return None


def _is_text_button(call: ast.AST) -> bool:
    """A QPushButton/QToolButton(...) call whose label isn't a short symbol literal."""
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    if name not in _BUTTON_CLASSES or not call.args:
        return False
    label = call.args[0]
    if isinstance(label, ast.Constant) and isinstance(label.value, str):
        return not _is_icon_label(label.value)
    return True  # tr(...), f-strings, variables: a real, translatable label


def _hardcoded_text_button_widths(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    found = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        text_buttons: set[str] = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and _is_text_button(node.value):
                text_buttons.update(k for t in node.targets if (k := _target_key(t)))
        for node in ast.walk(func):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _WIDTH_CAPS
                    and _target_key(node.func.value) in text_buttons):
                found.append((node.lineno, f"{_target_key(node.func.value)}.{node.func.attr}()"))
    return found


class TestNoHardcodedTextButtonWidths:
    def test_gui_source_has_no_width_caps_on_text_buttons(self):
        offenders = []
        for path in sorted(GUI_DIR.rglob("*.py")):
            for line, what in _hardcoded_text_button_widths(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.relative_to(GUI_DIR)}:{line}: {what}")
        assert not offenders, (
            "Text buttons must size to their translated label (#927) — use "
            "setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed) "
            "instead of a hardcoded width:\n  " + "\n  ".join(offenders)
        )

    @pytest.mark.parametrize("snippet, expected", [
        # The #927 bug pattern: caught.
        ('def f():\n    b = QPushButton(tr("gps_browse"))\n    b.setMaximumWidth(80)\n', 1),
        ('def f(self):\n    self._b = QPushButton(tr("x"))\n    self._b.setFixedWidth(90)\n', 1),
        ('def f():\n    b = QPushButton("Browse...")\n    b.setFixedWidth(80)\n', 1),
        # Icon-like buttons stay allowed.
        ('def f():\n    b = QPushButton("✕")\n    b.setMaximumWidth(28)\n', 0),
        ('def f():\n    b = QPushButton("🗑")\n    b.setMaximumWidth(40)\n', 0),
        # Size policy and minimum widths are fine.
        ('def f():\n    b = QPushButton(tr("x"))\n    b.setMinimumWidth(100)\n', 0),
        # Unrelated widgets are not buttons.
        ('def f():\n    e = QLineEdit(tr("x"))\n    e.setMaximumWidth(80)\n', 0),
    ])
    def test_scanner(self, snippet, expected):
        assert len(_hardcoded_text_button_widths(snippet)) == expected

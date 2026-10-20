"""
src/opensak/gui/dialogs/map_draw_dialog.py — Draw a line, polygon or point
set on a map for the filter dialog's Line/Polygon tab.

The filter dialog is modal, so the main map can't be used while it is open;
this dialog brings its own MapWidget. It opens with the tab's current points
(so an existing shape can be edited) and the caches shown on the main map as
context, and hands back (mode, points) on OK.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QVBoxLayout,
)

from opensak.filters.line_polygon import LP_MIN_POINTS, LP_MODES, Point
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.gui.map_widget import MapWidget
from opensak.gui.settings import get_settings
from opensak.lang import tr

# (mode, translation key) — same labels as the Line/Polygon tab. The keys are
# literals so test_no_unused_keys can find them.
_MODE_LABELS: tuple[tuple[str, str], ...] = (
    ("line",    "filter_lp_type_line"),
    ("polygon", "filter_lp_type_polygon"),
    ("points",  "filter_lp_type_points"),
)


class MapDrawDialog(QDialog):
    """Map dialog for drawing/editing one Line/Polygon filter shape."""

    def __init__(
        self,
        parent=None,
        mode: str = "line",
        points: Optional[list[Point]] = None,
        caches: Optional[list] = None,
        center: Optional[Point] = None,
    ):
        """*points* is the shape to edit (empty: start drawing one right
        away), *caches* are shown as context, and *center* is where the
        map opens when there is no shape (default: Home)."""
        super().__init__(parent)
        if mode not in LP_MODES:
            mode = "line"
        self.setWindowTitle(tr("map_draw_dialog_title"))
        self.setMinimumSize(600, 450)
        self._mode = mode
        self._points: list[Point] = list(points or [])

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel(tr("filter_lp_type_group")))
        self._mode_group = QButtonGroup(self)
        self._mode_buttons: dict[str, QRadioButton] = {}
        for m, key in _MODE_LABELS:
            button = QRadioButton(tr(key))
            button.setChecked(m == mode)
            self._mode_group.addButton(button)
            self._mode_buttons[m] = button
            top.addWidget(button)
            button.toggled.connect(lambda checked, m=m: self._on_mode_toggled(m, checked))
        top.addStretch()
        self._draw_btn = QPushButton(tr("map_draw_new_btn"))
        self._draw_btn.setAutoDefault(False)
        self._draw_btn.clicked.connect(self._start_draw)
        top.addWidget(self._draw_btn)
        self._clear_btn = QPushButton(tr("map_draw_clear"))
        self._clear_btn.setAutoDefault(False)
        self._clear_btn.clicked.connect(self._clear)
        top.addWidget(self._clear_btn)
        layout.addLayout(top)

        hint = QLabel(tr("map_draw_hint"))
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Zoom to the shape if there is one, else open at *center* (or Home);
        # only fall back to fitting the caches when neither says where.
        self._map: MapWidget = MapWidget(
            map_actions=False,
            auto_fit_caches=not self._points and center is None,
            center=center,
        )
        self._map.shape_changed.connect(self._on_shape_changed)
        layout.addWidget(self._map, 1)

        bottom = QHBoxLayout()
        self._status = QLabel()
        bottom.addWidget(self._status, 1)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        bottom.addWidget(self._buttons)
        layout.addLayout(bottom)

        if caches:
            self._map.load_caches(caches)
        if self._points:
            self._map.load_shape(mode, self._points, fit=True)
        else:
            self._map.start_draw(mode)
        self._restore_geometry()
        # The map takes whatever height is left, so capping clips nothing (#811).
        clamp_dialog_height_to_screen(self, parent)
        self._update_state()

    # ── result ────────────────────────────────────────────────────────────────

    def mode(self) -> str:
        return self._mode

    def points(self) -> list[Point]:
        return list(self._points)

    def is_valid(self) -> bool:
        return len(self._points) >= LP_MIN_POINTS[self._mode]

    # ── events ────────────────────────────────────────────────────────────────

    def _start_draw(self) -> None:
        self._map.start_draw(self._mode)

    def _on_mode_toggled(self, mode: str, checked: bool) -> None:
        if not checked:
            return
        self._mode = mode
        if self._points:
            # Same points, new geometry — a mode switch never loses work.
            self._map.load_shape(mode, self._points, fit=False)
        else:
            self._map.start_draw(mode)
        self._update_state()

    def _on_shape_changed(self, mode: str, points: list) -> None:
        self._points = list(points)
        self._update_state()

    def _clear(self) -> None:
        self._map.clear_shape()
        self._points = []
        self._map.start_draw(self._mode)
        self._update_state()

    def _update_state(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(self.is_valid())
        self._clear_btn.setEnabled(bool(self._points))
        if self.is_valid():
            self._status.setText(tr("map_draw_point_count", count=len(self._points)))
        else:
            self._status.setText(
                tr("filter_lp_too_few_points", count=LP_MIN_POINTS[self._mode])
            )

    # ── geometry / teardown ───────────────────────────────────────────────────

    def _restore_geometry(self) -> None:
        geo = get_settings().map_draw_dialog_geometry
        if geo:
            self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
        else:
            self.resize(900, 700)

    def done(self, result: int) -> None:
        get_settings().map_draw_dialog_geometry = (
            bytes(self.saveGeometry().toBase64().data()).decode("ascii")
        )
        # Page before profile — see MapWidget._cleanup_webengine().
        self._map._cleanup_webengine()
        super().done(result)

    def keyPressEvent(self, event) -> None:
        # Enter would accept the dialog half-way through drawing a shape.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.ignore()
            return
        super().keyPressEvent(event)

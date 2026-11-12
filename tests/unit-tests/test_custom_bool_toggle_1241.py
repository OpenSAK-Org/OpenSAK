"""
tests/unit-tests/test_custom_bool_toggle_1241.py — boolean custom fields
(#1241) are switched where the cache is shown: a check box in the detail
panel's Custom tab, and a click in the cache list column. Both save at once;
a field whose value comes from an expression stays read-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QCheckBox, QLabel, QMessageBox

from opensak.db import companion, custom_fields
from opensak.db.database import get_session, session_for
from opensak.db.manager import get_db_manager
from opensak.db.models import Cache
from opensak.gui.cache_detail import CacheDetailPanel
from opensak.gui.cache_table import CacheTableModel, CacheTableView
from opensak.gui.dialogs import column_dialog
from opensak.lang import load_language
from opensak.utils import flags


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    load_language("en")
    monkeypatch.setattr(flags, "companion_db", True)
    companion.reset()
    custom_fields.changed()
    yield
    companion.reset()


@pytest.fixture
def active() -> Path:
    """The active database with GC1, GC2 and the booleans Angel (stored)
    and Solved (shown through an expression)."""
    manager = get_db_manager()
    db = manager.new_database("Toggle")
    manager.switch_to(db)
    with session_for(db.path) as s:
        for code in ("GC1", "GC2"):
            s.add(Cache(gc_code=code, name=f"Cache {code}", cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0))
    path = Path(db.path)
    custom_fields.define_field(path, "Angel", "boolean", default="0")
    custom_fields.define_field(path, "Solved", "boolean", default="0",
                               expression="CASE Solved WHEN 1 THEN 'yes' ELSE 'no' END")
    custom_fields.write_values(path, [("GC1", {"Angel": 1})])
    return path


def _cache(code: str) -> Cache:
    with get_session() as s:
        from sqlalchemy.orm import selectinload
        cache = s.query(Cache).options(
            selectinload(Cache.logs), selectinload(Cache.waypoints), selectinload(Cache.user_note),
            selectinload(Cache.attributes), selectinload(Cache.trackables),
        ).filter_by(gc_code=code).one()
        s.expunge_all()
    return cache


def _stored(path: Path, code: str, name: str):
    return custom_fields.read_values(path, [code]).get(code, {}).get(name)


def _value_widget(panel: CacheDetailPanel, name: str) -> Any:
    form = panel._custom_form
    for row in range(form.rowCount()):
        label: Any = form.itemAt(row, form.ItemRole.LabelRole)
        if label.widget().text() == f"<b>{name}</b>":
            value: Any = form.itemAt(row, form.ItemRole.FieldRole)
            return value.widget()
    raise AssertionError(f"no row for {name}")


def _panel(qtbot, code: str) -> CacheDetailPanel:
    panel = CacheDetailPanel()
    qtbot.addWidget(panel)
    panel.show_cache(_cache(code))
    return panel


# ── Detail panel ─────────────────────────────────────────────────────────────

def test_boolean_is_a_check_box_that_saves_at_once(active, qtbot):
    panel = _panel(qtbot, "GC2")
    box = _value_widget(panel, "Angel")
    assert isinstance(box, QCheckBox) and not box.isChecked()
    with qtbot.waitSignal(panel.custom_values_changed) as signal:
        box.setChecked(True)
    assert signal.args == ["GC2"]
    assert _stored(active, "GC2", "Angel") == 1


def test_value_is_still_there_after_showing_another_cache(active, qtbot):
    panel = _panel(qtbot, "GC2")
    _value_widget(panel, "Angel").setChecked(True)
    panel.show_cache(_cache("GC1"))
    _value_widget(panel, "Angel").setChecked(False)
    panel.show_cache(_cache("GC2"))
    assert _value_widget(panel, "Angel").isChecked()
    panel.show_cache(_cache("GC1"))
    assert not _value_widget(panel, "Angel").isChecked()
    assert _stored(active, "GC1", "Angel") == 0


def test_boolean_with_an_expression_is_read_only(active, qtbot):
    panel = _panel(qtbot, "GC1")
    shown = _value_widget(panel, "Solved")
    assert isinstance(shown, QLabel) and shown.text() == "no"


def test_failed_save_puts_the_box_back(active, qtbot, monkeypatch):
    def fail(*_a, **_k):
        raise custom_fields.CustomFieldError("disk full")
    monkeypatch.setattr(custom_fields, "write_active_values", fail)
    warnings: list = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    panel = _panel(qtbot, "GC2")
    box = _value_widget(panel, "Angel")
    box.setChecked(True)
    assert not box.isChecked()
    assert "disk full" in warnings[0]


def test_refresh_custom_only_for_the_cache_shown(active, qtbot):
    panel = _panel(qtbot, "GC2")
    custom_fields.write_values(active, [("GC2", {"Angel": 1})])
    panel.refresh_custom("GC1")
    assert not _value_widget(panel, "Angel").isChecked()
    panel.refresh_custom("GC2")
    assert _value_widget(panel, "Angel").isChecked()


# ── Cache list ───────────────────────────────────────────────────────────────

def _caches() -> list[Cache]:
    with get_session() as s:
        caches = s.query(Cache).order_by(Cache.gc_code).all()
        s.expunge_all()
    return caches


def test_model_toggles_a_boolean_column(active, qtbot):
    column_dialog.set_visible_columns(["gc_code", "name", "custom:Angel", "custom:Solved"])
    model = CacheTableModel()
    model.load(_caches())
    angel = model._columns.index("custom:Angel")
    solved = model._columns.index("custom:Solved")
    assert model.toggles("custom:Angel") and not model.toggles("custom:Solved")
    assert not model.toggles("name")
    assert model.flags(model.index(0, angel)) & Qt.ItemFlag.ItemIsEditable
    assert not model.flags(model.index(0, solved)) & Qt.ItemFlag.ItemIsEditable

    with qtbot.waitSignal(model.custom_value_changed) as signal:
        assert model.toggle_custom(model.index(1, angel)) is None
    assert signal.args == ["GC2"]
    # shown without a manual refresh
    assert model.data(model.index(1, angel)) == "✓"
    assert _stored(active, "GC2", "Angel") == 1
    assert model.setData(model.index(1, angel), None)
    assert model.data(model.index(1, angel)) == ""
    assert _stored(active, "GC2", "Angel") == 0
    # an expression's field doesn't change
    assert not model.setData(model.index(0, solved), None)
    assert _stored(active, "GC1", "Solved") == 0         # the default, unchanged


def test_single_click_in_the_list_toggles(active, qtbot):
    column_dialog.set_visible_columns(["gc_code", "name", "custom:Angel"])
    view = CacheTableView()
    qtbot.addWidget(view)
    view.resize(600, 300)
    view.show()
    view._model.load(_caches())
    changed: list = []
    view.custom_value_changed.connect(changed.append)
    index = view._model.index(0, view._model._columns.index("custom:Angel"))
    pos = QPointF(view.visualRect(index).center())
    view.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, pos, pos,
                                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                     Qt.KeyboardModifier.NoModifier))
    assert changed == ["GC1"]
    assert _stored(active, "GC1", "Angel") == 0


def test_list_refreshes_after_a_change_in_the_detail_panel(active, qtbot):
    column_dialog.set_visible_columns(["gc_code", "name", "custom:Angel"])
    view = CacheTableView()
    qtbot.addWidget(view)
    view._model.load(_caches())
    panel = _panel(qtbot, "GC2")
    panel.custom_values_changed.connect(view.refresh_custom)
    _value_widget(panel, "Angel").setChecked(True)
    col = view._model._columns.index("custom:Angel")
    assert view._model.data(view._model.index(1, col)) == "✓"


def test_toggle_keeps_the_other_rows_when_loaded_in_another_order(active, qtbot):
    # The refresh worker gets the visible columns as a set, so its values can
    # list the fields in another order than the columns; merging the one
    # reloaded row used to drop every other cache's values.
    column_dialog.set_visible_columns(["gc_code", "name", "custom:Angel", "custom:Solved"])
    model = CacheTableModel()
    with get_session() as s:
        dbapi = s.connection().connection.dbapi_connection
        worker_values = custom_fields.load_values(dbapi, active, ["Solved", "Angel"])
    model.load(_caches(), worker_values)
    angel = model._columns.index("custom:Angel")
    solved = model._columns.index("custom:Solved")
    assert model.data(model.index(0, angel)) == "✓"
    assert model.toggle_custom(model.index(1, angel)) is None
    assert [model.data(model.index(r, angel)) for r in range(2)] == ["✓", "✓"]
    assert [model.data(model.index(r, solved)) for r in range(2)] == ["no", "no"]


def test_merged_matches_fields_by_name():
    a = custom_fields.Field("a", "A", "integer")
    b = custom_fields.Field("b", "B", "integer")
    old = custom_fields.Values((a, b), {"GC1": (1, 2, 1, 2), "GC2": (3, 4, 3, 4)})
    new = custom_fields.Values((b, a), {"GC2": (40, 30, 40, 30)})
    merged = old.merged(new)
    assert merged.rows == {"GC1": (1, 2, 1, 2), "GC2": (30, 40, 30, 40)}
    only_b = custom_fields.Values((b,), {"GC1": (20, 20)})
    assert old.merged(only_b).rows["GC1"] == (1, 20, 1, 20)

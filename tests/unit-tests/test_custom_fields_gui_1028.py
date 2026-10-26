"""
tests/unit-tests/test_custom_fields_gui_1028.py — custom fields in the GUI
(#1028 step 4, #1149 phase 6): the cache list's columns, the detail panel's
Custom tab, the Edit cache dialog's Custom fields tab, Tools → Custom
fields…, and the Filter dialog's Custom tab.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QCheckBox, QLineEdit

from opensak.db import companion, custom_fields
from opensak.db.database import get_session, session_for
from opensak.db.manager import get_db_manager
from opensak.db.models import Cache
from opensak.filters.engine import CustomFieldFilter, FilterSet
from opensak.gui.cache_detail import CacheDetailPanel
from opensak.gui.cache_table import CacheTableModel, get_column_defs
from opensak.gui.date_format import format_date
from opensak.gui.settings import get_settings
from opensak.gui.dialogs import column_dialog
from opensak.gui.dialogs import filter_dialog as fd
from opensak.gui.dialogs.custom_fields_dialog import CustomFieldsDialog, FieldEditDialog
from opensak.gui.dialogs.waypoint_dialog import CustomFieldsForm, OptionalDateEdit
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
    """The active database "Custom" with GC1..GC3 and four fields."""
    manager = get_db_manager()
    db = manager.new_database("Custom")
    manager.switch_to(db)
    with session_for(db.path) as s:
        for code in ("GC1", "GC2", "GC3"):
            s.add(Cache(gc_code=code, name=f"Cache {code}", cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0))
    path = Path(db.path)
    custom_fields.define_field(path, "Angel", "boolean", default="0")
    custom_fields.define_field(path, "GCFinds", "integer", default="-1",
                               expression="nullif(GCFinds,-1)")
    custom_fields.define_field(path, "Parking", "string", default="")
    custom_fields.define_field(path, "Visited", "date")
    custom_fields.write_values(path, [
        ("GC1", {"Angel": 1, "GCFinds": 12, "Parking": "Lot A", "Visited": "2024-05-01"}),
        ("GC2", {"GCFinds": 3}),
    ])
    return path


def _caches() -> list[Cache]:
    with get_session() as s:
        caches = s.query(Cache).order_by(Cache.gc_code).all()
        s.expunge_all()
    return caches


# ── Cache list ───────────────────────────────────────────────────────────────

def test_column_chooser_and_table_offer_one_column_per_field(active):
    ids = [c[0] for c in column_dialog.get_all_columns()]
    assert ids[-4:] == ["custom:Angel", "custom:GCFinds", "custom:Parking", "custom:Visited"]
    labels = dict((c[0], c[1]) for c in column_dialog.get_all_columns())
    assert labels["custom:Angel"] == "Angel (custom field)"
    assert get_column_defs()["custom:GCFinds"][0] == "GCFinds"     # header: the name


def test_no_custom_columns_without_the_flag(active, monkeypatch):
    monkeypatch.setattr(flags, "companion_db", False)
    assert not any(c[0].startswith("custom:") for c in column_dialog.get_all_columns())


def test_model_shows_and_sorts_custom_values(active, qtbot):
    column_dialog.set_visible_columns(["gc_code", "name", "custom:Angel", "custom:GCFinds",
                                       "custom:Visited"])
    model = CacheTableModel()
    model.load(_caches())
    angel, finds, visited = (model._columns.index(c) for c in
                             ("custom:Angel", "custom:GCFinds", "custom:Visited"))
    text = lambda row, col: model.data(model.index(row, col))   # noqa: E731
    assert [text(r, angel) for r in range(3)] == ["✓", "", ""]
    # GCFinds shows its expression: nullif(GCFinds,-1) hides the -1 default
    assert [text(r, finds) for r in range(3)] == ["12", "3", ""]
    # dates follow the date format in Settings, like the other date columns
    assert text(0, visited) == format_date(datetime(2024, 5, 1), get_settings().date_format)
    model.sort(finds)                        # by the stored value, -1 first
    assert [c.gc_code for c in model._caches] == ["GC3", "GC2", "GC1"]


def test_turning_on_a_custom_column_loads_its_values(active):
    column_dialog.set_visible_columns(["gc_code", "name"])
    model = CacheTableModel()
    model.load(_caches())
    assert model.custom_names() == []
    column_dialog.set_visible_columns(["gc_code", "name", "custom:Parking"])
    model.reload_columns()
    col = model._columns.index("custom:Parking")
    assert model.data(model.index(0, col)) == "Lot A"


# ── Detail panel ─────────────────────────────────────────────────────────────

def test_detail_panel_custom_tab(active, qtbot):
    panel = CacheDetailPanel()
    qtbot.addWidget(panel)
    with get_session() as s:
        from sqlalchemy.orm import selectinload
        cache = s.query(Cache).options(
            selectinload(Cache.logs), selectinload(Cache.waypoints),
            selectinload(Cache.attributes), selectinload(Cache.trackables),
        ).filter_by(gc_code="GC1").one()
        panel.show_cache(cache)
    assert panel._tabs.isTabVisible(panel._custom_tab)
    text = panel._custom_browser.toPlainText()
    assert "Angel" in text and "Yes" in text and "Lot A" in text
    panel.clear()
    assert not panel._tabs.isTabVisible(panel._custom_tab)


# ── Edit cache dialog ────────────────────────────────────────────────────────

def test_edit_form_shows_and_saves_only_changes(active, qtbot):
    form = CustomFieldsForm()
    qtbot.addWidget(form)
    assert [f.name for f in form.fields] == ["Angel", "GCFinds", "Parking", "Visited"]
    form.load("GC2")
    angel = form._inputs["Angel"]
    finds = form._inputs["GCFinds"]
    assert isinstance(angel, QCheckBox) and isinstance(finds, QLineEdit)
    assert not angel.isChecked() and finds.text() == "3"
    assert isinstance(form._inputs["Visited"], OptionalDateEdit)
    angel.setChecked(True)
    assert form.save("GC2") is None
    values = custom_fields.read_values(active, ["GC2"])["GC2"]
    assert values["Angel"] == 1 and values["GCFinds"] == 3


def test_edit_form_refuses_a_value_of_the_wrong_type(active, qtbot):
    form = CustomFieldsForm()
    qtbot.addWidget(form)
    form.load("GC1")
    form._inputs["GCFinds"].setText("many")
    assert "GCFinds" in (form.error() or "")


def test_edit_form_is_empty_without_the_flag(active, qtbot, monkeypatch):
    monkeypatch.setattr(flags, "companion_db", False)
    form = CustomFieldsForm()
    qtbot.addWidget(form)
    assert form.fields == [] and form.save("GC1") is None


# ── Tools → Custom fields… ───────────────────────────────────────────────────

def test_fields_dialog_add_rename_delete_drop(active, qtbot, monkeypatch):
    dlg = CustomFieldsDialog(active, "Custom")
    qtbot.addWidget(dlg)
    assert dlg._table.rowCount() == 4
    renames: list = []
    dlg.fields_changed.connect(renames.append)

    def edit_dialog(name, type_, default, scope="local", expression=""):
        def fake_exec(self):
            self.name.setText(name)
            self.type.setCurrentIndex(self.type.findData(type_))
            self.default.setText(default)
            self.scope.setCurrentIndex(self.scope.findData(scope))
            self.expression.setText(expression)
            return True
        monkeypatch.setattr(FieldEditDialog, "exec", fake_exec)

    edit_dialog("Tour", "boolean", "1")
    dlg._add()
    tour = next(f for f in custom_fields.fields(active) if f.name == "Tour")
    assert (tour.type, tour.default, tour.scope) == ("boolean", "1", "local")

    dlg._table.selectRow(next(i for i, f in enumerate(dlg._fields) if f.name == "Parking"))
    edit_dialog("ParkingSpot", "string", "none", expression="upper(ParkingSpot)")
    dlg._edit()
    assert custom_fields.read_values(active, ["GC1"])["GC1"]["ParkingSpot"] == "Lot A"
    spot = next(f for f in custom_fields.fields(active) if f.name == "ParkingSpot")
    assert spot.expression == "upper(ParkingSpot)"
    assert custom_fields.active_values(["ParkingSpot"]).display("GC1", "ParkingSpot") == "LOT A"

    yes = fd.QMessageBox.StandardButton.Yes
    monkeypatch.setattr("opensak.gui.dialogs.custom_fields_dialog.QMessageBox.question",
                        lambda *a, **k: yes)
    dlg._table.selectRow(next(i for i, f in enumerate(dlg._fields) if f.name == "Tour"))
    dlg._delete()
    assert "Tour" not in [f.name for f in custom_fields.fields(active)]
    orphan = next(i for i, f in enumerate(dlg._fields) if f.name == "Tour" and f.orphaned)
    dlg._table.selectRow(orphan)
    assert dlg._drop_btn.isEnabled()
    dlg._drop()
    assert "Tour" not in [f.name for f in custom_fields.stored_fields(active, True)]
    dlg.accept()
    assert renames == [[("Parking", "ParkingSpot")]]


# ── Filter dialog ────────────────────────────────────────────────────────────

@pytest.fixture
def filter_dlg(active, qtbot, monkeypatch):
    monkeypatch.setattr(fd.FilterProfile, "list_profiles", staticmethod(lambda: []))
    d = fd.FilterDialog()
    qtbot.addWidget(d)
    return d


def _custom(fs: FilterSet) -> list[CustomFieldFilter]:
    return [f for f in fs._filters if isinstance(f, CustomFieldFilter)]


def test_filter_dialog_has_a_row_per_field(filter_dlg):
    assert filter_dlg._custom_tab is not None
    assert list(filter_dlg._custom_rows) == ["angel", "gcfinds", "parking", "visited"]
    assert _custom(filter_dlg._build_filterset()) == []


def test_filter_dialog_builds_and_loads_custom_filters(filter_dlg):
    rows = filter_dlg._custom_rows
    rows["angel"]._no.setChecked(False)                           # only Yes
    rows["gcfinds"]._op.setCurrentIndex(rows["gcfinds"]._op.findData("between"))
    rows["gcfinds"]._value1.setValue(10)
    rows["gcfinds"]._value2.setValue(2)                            # put in order
    rows["parking"]._text.set_op("starts_with")
    rows["parking"]._text.edit.setText("Lot")
    rows["visited"]._date.op_combo.setCurrentIndex(
        rows["visited"]._date.op_combo.findData("during"))
    fs = filter_dlg._build_filterset()
    by_field = {f.field: f for f in _custom(fs)}
    assert by_field["Angel"].op == "yes"
    assert (by_field["GCFinds"].value1, by_field["GCFinds"].value2) == (2.0, 10.0)
    assert (by_field["Parking"].op, by_field["Parking"].text) == ("starts_with", "Lot")
    assert by_field["Visited"].op == "during"

    filter_dlg._reset_all()
    assert _custom(filter_dlg._build_filterset()) == []
    filter_dlg._load_filterset(fs)
    assert {f.field: f.to_dict() for f in _custom(filter_dlg._build_filterset())} \
        == {f.field: f.to_dict() for f in _custom(fs)}


def test_filter_dialog_keeps_filters_on_unknown_fields(filter_dlg):
    fs = FilterSet()
    fs.add(CustomFieldFilter("ToSolve", "boolean", "yes"))
    fs.add(CustomFieldFilter("Visited", "date", "equal", date1=date(2024, 5, 1)))
    filter_dlg._load_filterset(fs)
    assert "ToSolve" in filter_dlg._custom_kept_label.text()
    rebuilt = {f.field for f in _custom(filter_dlg._build_filterset())}
    assert rebuilt == {"ToSolve", "Visited"}


def test_filter_dialog_has_no_custom_tab_without_the_flag(active, qtbot, monkeypatch):
    monkeypatch.setattr(flags, "companion_db", False)
    monkeypatch.setattr(fd.FilterProfile, "list_profiles", staticmethod(lambda: []))
    d = fd.FilterDialog()
    qtbot.addWidget(d)
    assert d._custom_tab is None
    fs = FilterSet()
    fs.add(CustomFieldFilter("Angel", "boolean", "yes"))
    d._load_filterset(fs)
    assert len(_custom(d._build_filterset())) == 1       # kept, not dropped


def test_where_help_lists_the_fields(active):
    help_text = fd._where_custom_help()
    assert "GCFinds" in help_text and "Angel = 1" in help_text


# ── Display expressions in the edit dialog ───────────────────────────────────

def _accepts(monkeypatch, qtbot, field=None, **values) -> tuple[bool, list]:
    """Fill FieldEditDialog with *values*, press OK: (accepted, warnings)."""
    warnings: list = []
    monkeypatch.setattr("opensak.gui.dialogs.custom_fields_dialog.QMessageBox.warning",
                        lambda *a, **k: warnings.append(a[2]))
    dlg = FieldEditDialog(None, field)
    qtbot.addWidget(dlg)
    for name, value in values.items():
        widget = getattr(dlg, name)
        if name == "type":
            widget.setCurrentIndex(widget.findData(value))
        else:
            widget.setText(value)
    dlg._accept()
    return dlg.result() == FieldEditDialog.DialogCode.Accepted, warnings


def test_new_field_with_a_working_expression_is_accepted(active, qtbot, monkeypatch):
    ok, warnings = _accepts(monkeypatch, qtbot, name="Finds", type="integer",
                            expression="nullif(Finds, -1)")
    assert ok and not warnings


def test_a_broken_expression_is_refused(active, qtbot, monkeypatch):
    for expression in ("no_such_function(Finds)", "NoSuchColumn + 1", "1; DROP TABLE caches"):
        ok, warnings = _accepts(monkeypatch, qtbot, name="Finds", type="integer",
                                expression=expression)
        assert not ok and "display expression doesn't work" in warnings[-1]


def test_editing_an_imported_gsak_expression(active, qtbot, monkeypatch):
    custom_fields.define_field(active, "PMFav", "real", default="0.0", origin="gsak",
                               expression="CASE PMFav WHEN 0.0 THEN '-' ELSE PMFav END")
    field = next(f for f in custom_fields.fields(active) if f.name == "PMFav")
    ok, _ = _accepts(monkeypatch, qtbot, field,
                     expression="CASE PMFav WHEN 0.0 THEN '' ELSE printf('%.1f%%', PMFav) END")
    assert ok

"""tests/unit-tests/test_filter_attr_column_width_796.py — issue #796.

The attribute name column in the filter dialog came first and stretched with
the dialog, so on a wide dialog the Yes/No boxes ended up far from their
names (GeePa67). The Yes/No/Any boxes now come first, with the name right
after them, and the rows have alternating colours.
"""

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QCheckBox, QHeaderView  # noqa: E402

from opensak.gui.dialogs.filter_dialog import (  # noqa: E402
    _ATTR_COL_ANY, _ATTR_COL_NAME, _ATTR_COL_NO, _ATTR_COL_YES,
)


@pytest.fixture
def dialog(qtbot, tmp_path):
    from opensak.db.database import init_db
    from opensak.gui.dialogs.filter_dialog import FilterDialog
    from opensak.lang import load_language

    load_language("en")
    init_db(db_path=tmp_path / "attr796.db")
    dlg = FilterDialog()
    qtbot.addWidget(dlg)
    dlg._tabs.setCurrentWidget(dlg._attributes_tab)
    return dlg


def test_boxes_come_first_then_the_name(dialog):
    from opensak.lang import tr

    table = dialog._attr_table
    assert (_ATTR_COL_YES, _ATTR_COL_NO, _ATTR_COL_ANY, _ATTR_COL_NAME) == (0, 1, 2, 3)
    headers = [table.horizontalHeaderItem(c).text() for c in range(4)]
    assert headers == [tr("yes"), tr("no"), tr("filter_none_short"), tr("filter_attr_col_name")]


def test_each_row_has_its_own_boxes_and_name(dialog):
    table = dialog._attr_table
    for attr_id, (ja, nej, ingen) in dialog._attr_boxes.items():
        row, name_item, _haystack = dialog._attr_rows[attr_id]
        assert table.item(row, _ATTR_COL_NAME) is name_item
        for col, box in ((_ATTR_COL_YES, ja), (_ATTR_COL_NO, nej), (_ATTR_COL_ANY, ingen)):
            assert table.cellWidget(row, col).findChild(QCheckBox) is box


def test_boxes_stay_next_to_the_names_on_a_wide_dialog(qtbot, dialog):
    table = dialog._attr_table
    dialog.resize(700, 600)
    dialog.show()
    qtbot.wait(10)
    narrow = table.columnViewportPosition(_ATTR_COL_NAME)
    dialog.resize(1600, 600)
    qtbot.wait(10)
    assert table.columnViewportPosition(_ATTR_COL_NAME) == narrow
    assert narrow < 200


def test_only_the_name_column_stretches(dialog):
    header = dialog._attr_table.horizontalHeader()
    assert header.sectionResizeMode(_ATTR_COL_NAME) == QHeaderView.ResizeMode.Stretch
    for col in (_ATTR_COL_YES, _ATTR_COL_NO, _ATTR_COL_ANY):
        assert header.sectionResizeMode(col) == QHeaderView.ResizeMode.ResizeToContents


def test_rows_alternate_colours(dialog):
    assert dialog._attr_table.alternatingRowColors()


def test_ticking_yes_still_marks_the_name(dialog):
    attr_id = next(iter(dialog._attr_boxes))
    ja, _nej, _ingen = dialog._attr_boxes[attr_id]
    ja.setChecked(True)
    _row, name_item, _haystack = dialog._attr_rows[attr_id]
    assert name_item.font().bold()

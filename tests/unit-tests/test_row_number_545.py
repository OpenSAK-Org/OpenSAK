"""tests/unit-tests/test_row_number_545.py — issue #545.

An optional Row Number column, as in GSAK: 1, 2, 3 … N in the order the
list is filtered and sorted now, so "my 100th find in this filter" is row
100. Off by default, not stored, not sortable.
"""

from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402

from opensak.db.models import Cache  # noqa: E402
from opensak.gui import cache_table as ct  # noqa: E402
from opensak.gui.cache_table import CacheTableModel, CacheTableView  # noqa: E402
from opensak.utils.types import CoordFormat, DateFormat, TextSize  # noqa: E402

COLUMNS = ["row_number", "gc_code", "name", "difficulty"]


def _cache(gc_code: str, name: str, difficulty: float) -> Cache:
    c = Cache(gc_code=gc_code, name=name)
    c.available, c.archived, c.found, c.locked = True, False, False, False
    c.difficulty = difficulty
    return c


CACHES = [
    _cache("GC1", "Charlie", 2.0),
    _cache("GC2", "Alpha", 4.5),
    _cache("GC3", "Bravo", 1.0),
]


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch, qapp):
    import opensak.lang as lang
    from opensak.lang.en import STRINGS
    monkeypatch.setattr(lang, "_translations", STRINGS)
    s = SimpleNamespace(
        home_lat=55.0, home_lon=12.0, use_miles=False,
        coord_format=CoordFormat.DMM, date_format=DateFormat.DMY,
        gc_username="", gc_finder_id="", map_provider="google", text_size=TextSize.MEDIUM,
        distance_method="haversine",
    )
    monkeypatch.setattr(ct, "get_settings", lambda: s)
    monkeypatch.setattr("opensak.gui.settings.get_settings", lambda: s)
    monkeypatch.setattr(ct, "get_cache_type_icon", lambda *a, **k: QPixmap(4, 4))
    monkeypatch.setattr(ct, "_get_active_columns", lambda: list(COLUMNS))
    monkeypatch.setattr(ct, "get_column_widths", lambda: {})
    monkeypatch.setattr(ct, "set_column_widths", lambda w: None)
    return s


def _column(model, col: str) -> list[str]:
    c = model._columns.index(col)
    return [model.data(model.index(r, c)) for r in range(model.rowCount())]


@pytest.fixture
def model():
    m = CacheTableModel()
    m.load(list(CACHES))
    return m


# ── column chooser ───────────────────────────────────────────────────────────

def test_offered_in_the_view_window_and_off_by_default():
    from opensak.gui.dialogs.column_dialog import _ALL_COLUMNS_DEF
    row = [c for c in _ALL_COLUMNS_DEF if c[0] == "row_number"]
    assert len(row) == 1
    assert row[0][1] == "col_row_number"
    assert row[0][3] is False


def test_chosen_column_is_shown(monkeypatch):
    # _get_active_columns() drops ids missing from get_column_defs(), so a
    # column only in the View window would never appear in the list.
    from opensak.gui.dialogs import column_dialog
    monkeypatch.undo()
    monkeypatch.setattr(column_dialog, "get_visible_columns", lambda: ["gc_code", "row_number"])
    assert ct._get_active_columns() == ["gc_code", "row_number"]


def test_every_column_in_the_view_window_can_be_shown():
    from opensak.gui.dialogs.column_dialog import _ALL_COLUMNS_DEF
    defs = ct.get_column_defs()
    assert [c[0] for c in _ALL_COLUMNS_DEF if c[0] not in defs] == []


# ── values ───────────────────────────────────────────────────────────────────

def test_numbers_follow_the_rows(model):
    assert _column(model, "row_number") == ["1", "2", "3"]


def test_numbers_follow_a_new_sort(model):
    model.sort(COLUMNS.index("name"), Qt.SortOrder.AscendingOrder)
    assert _column(model, "name") == ["Alpha", "Bravo", "Charlie"]
    assert _column(model, "row_number") == ["1", "2", "3"]
    model.sort(COLUMNS.index("difficulty"), Qt.SortOrder.DescendingOrder)
    assert _column(model, "gc_code") == ["GC2", "GC1", "GC3"]
    assert _column(model, "row_number") == ["1", "2", "3"]


def test_numbers_follow_a_new_filter(model):
    model.load([CACHES[2], CACHES[0]])
    assert _column(model, "row_number") == ["1", "2"]


def test_centered(model):
    idx = model.index(0, COLUMNS.index("row_number"))
    assert model.data(idx, Qt.ItemDataRole.TextAlignmentRole) == Qt.AlignmentFlag.AlignCenter


def test_short_header_and_full_tooltip(model):
    from opensak.lang import tr
    assert ct.SHORT_HEADER_KEYS["row_number"] == "col_row_number_short"
    model._short_headers = {"row_number"}
    section = COLUMNS.index("row_number")
    assert model.headerData(section, Qt.Orientation.Horizontal) == "#"
    assert model.headerData(section, Qt.Orientation.Horizontal,
                            Qt.ItemDataRole.ToolTipRole) == tr("col_row_number")


# ── not sortable ─────────────────────────────────────────────────────────────

def test_sorting_by_row_number_changes_nothing(model):
    model.sort(COLUMNS.index("name"), Qt.SortOrder.AscendingOrder)
    emitted = []
    model.sort_changed.connect(lambda *a: emitted.append(a))
    model.sort(COLUMNS.index("row_number"), Qt.SortOrder.DescendingOrder)
    assert _column(model, "name") == ["Alpha", "Bravo", "Charlie"]
    assert emitted == []


def test_header_click_keeps_the_current_sort(qtbot):
    view = CacheTableView()
    qtbot.addWidget(view)
    view.load_caches(list(CACHES))
    header = view.horizontalHeader()
    name_col = COLUMNS.index("name")

    view.sortByColumn(name_col, Qt.SortOrder.DescendingOrder)
    assert _column(view._model, "name") == ["Charlie", "Bravo", "Alpha"]
    saved = []
    view.sort_changed.connect(lambda *a: saved.append(a))

    # What a click on the Row Number header does.
    header.setSortIndicator(COLUMNS.index("row_number"), Qt.SortOrder.AscendingOrder)

    assert _column(view._model, "name") == ["Charlie", "Bravo", "Alpha"]
    assert header.sortIndicatorSection() == name_col
    assert header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
    assert saved == []      # nothing new to remember for the database


def test_header_click_without_a_sort_shows_no_indicator(qtbot):
    view = CacheTableView()
    qtbot.addWidget(view)
    view.load_caches(list(CACHES))
    header = view.horizontalHeader()
    header.setSortIndicator(COLUMNS.index("row_number"), Qt.SortOrder.AscendingOrder)
    assert header.sortIndicatorSection() != COLUMNS.index("row_number")
    assert _column(view._model, "gc_code") == ["GC1", "GC2", "GC3"]


# ── where a newly chosen Row number goes ─────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    from opensak import settings_store as ss
    fresh = ss.SettingsStore()
    fresh._data = {}
    fresh._path = tmp_path / "opensak.json"
    monkeypatch.setattr(ss, "_store", fresh)
    return fresh


def _choose(qtbot, *col_ids: str) -> list[str]:
    """Tick *col_ids* in the View window, press OK, return the saved order."""
    from opensak.gui.dialogs.column_dialog import ColumnChooserDialog, get_visible_columns
    dlg = ColumnChooserDialog()
    qtbot.addWidget(dlg)
    for i in range(dlg._list.count()):
        item = dlg._list.item(i)
        if item.data(Qt.ItemDataRole.UserRole) in col_ids:
            item.setCheckState(Qt.CheckState.Checked)
    dlg._save_and_accept()
    return get_visible_columns()


def test_newly_chosen_row_number_goes_first(qtbot, store):
    from opensak.gui.dialogs.column_dialog import set_visible_columns
    set_visible_columns(["user_flag", "gc_code", "name", "difficulty"])
    assert _choose(qtbot, "row_number") == [
        "row_number", "user_flag", "gc_code", "name", "difficulty"]


def test_other_new_columns_still_go_last(qtbot, store):
    from opensak.gui.dialogs.column_dialog import set_visible_columns
    set_visible_columns(["gc_code", "name"])
    assert _choose(qtbot, "row_number", "country") == [
        "row_number", "gc_code", "name", "country"]


def test_row_number_moved_by_the_user_stays_put(qtbot, store):
    from opensak.gui.dialogs.column_dialog import set_visible_columns
    set_visible_columns(["gc_code", "name", "row_number"])
    assert _choose(qtbot, "country") == ["gc_code", "name", "row_number", "country"]

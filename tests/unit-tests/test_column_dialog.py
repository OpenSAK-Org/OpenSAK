# tests/unit-tests/test_column_dialog.py — column chooser dialog + store helpers.

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt

from opensak.gui.dialogs import column_dialog as cd
from opensak.gui.dialogs.column_dialog import (
    ALWAYS_VISIBLE,
    ColumnChooserDialog,
    get_all_columns,
    get_column_widths,
    get_container_display,
    get_type_display,
    get_visible_columns,
    set_column_widths,
    set_container_display,
    set_type_display,
    set_visible_columns,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Isolated SettingsStore — fresh in-memory dict per test."""
    from opensak import settings_store as ss
    fresh = ss.SettingsStore()
    fresh._data = {}
    fresh._path = tmp_path / "opensak.json"
    monkeypatch.setattr(ss, "_store", fresh)
    return fresh


# ── module-level helpers ──────────────────────────────────────────────────────

class TestColumnHelpers:
    def test_get_all_columns_shape(self):
        cols = get_all_columns()
        assert all(len(c) == 4 for c in cols)
        assert {"gc_code", "name"} <= {c[0] for c in cols}

    def test_cache_type_default_width_fits_header(self):
        # Issue #414: 28px truncated the "Type" header; minimum must be >= 40.
        col_map = {fid: w for fid, _, w, _ in get_all_columns()}
        assert col_map["cache_type"] >= 40

    def test_visible_defaults_when_unset(self, store):
        vis = get_visible_columns()
        assert "gc_code" in vis
        assert "country" not in vis  # not a default-visible column

    def test_locked_in_defaults(self, store):
        # Issue #202: locked is a general-purpose data-protection flag, visible
        # by default like user_flag/corrected.
        assert "locked" in get_visible_columns()

    def test_visible_roundtrip(self, store):
        set_visible_columns(["gc_code", "name", "country"])
        assert get_visible_columns() == ["gc_code", "name", "country"]

    def test_widths_default_empty(self, store):
        assert get_column_widths() == {}

    def test_widths_roundtrip(self, store):
        set_column_widths({"gc_code": 80})
        assert get_column_widths() == {"gc_code": 80}

    def test_widths_bad_json_returns_empty(self, store):
        store.set("columns.widths", "{ not json")
        assert get_column_widths() == {}

    def test_container_display_defaults_to_bar(self, store):
        assert get_container_display() == "bar"

    def test_container_display_roundtrip(self, store):
        set_container_display("text")
        assert get_container_display() == "text"
        set_container_display("bar")
        assert get_container_display() == "bar"

    def test_container_display_invalid_falls_back_to_bar(self, store):
        store.set("columns.container_display", "invalid")
        assert get_container_display() == "bar"

    def test_type_display_defaults_to_icon(self, store):
        assert get_type_display() == "icon"

    def test_type_display_roundtrip(self, store):
        for mode in ("icon", "text", "both"):
            set_type_display(mode)
            assert get_type_display() == mode

    def test_type_display_invalid_falls_back_to_icon(self, store):
        store.set("columns.type_display", "invalid")
        assert get_type_display() == "icon"


# ── ColumnChooserDialog ───────────────────────────────────────────────────────

class TestColumnChooserDialog:
    def test_select_all_checks_everything(self, qtbot, store):
        dlg = ColumnChooserDialog()
        qtbot.addWidget(dlg)
        dlg._select_all()
        states = [dlg._list.item(i).checkState() for i in range(dlg._list.count())]
        assert all(s == Qt.CheckState.Checked for s in states)

    def test_select_default_unchecks_non_default(self, qtbot, store):
        dlg = ColumnChooserDialog()
        qtbot.addWidget(dlg)
        dlg._select_all()
        dlg._select_default()
        for i in range(dlg._list.count()):
            item = dlg._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == "country":
                assert item.checkState() == Qt.CheckState.Unchecked

    def test_save_persists_checked_and_always_visible(self, qtbot, store):
        dlg = ColumnChooserDialog()
        qtbot.addWidget(dlg)
        dlg._select_default()
        dlg._save_and_accept()
        saved = get_visible_columns()
        assert "gc_code" in saved
        assert "name" in saved

    def test_always_visible_columns_not_checkable(self, qtbot, store):
        dlg = ColumnChooserDialog()
        qtbot.addWidget(dlg)
        for i in range(dlg._list.count()):
            item = dlg._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) in ALWAYS_VISIBLE:
                assert not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable)

    def test_add_col_appends_without_changing_drag_order(self, qtbot, store):
        # Custom drag order — deliberately not in _ALL_COLUMNS_DEF order
        set_visible_columns(["name", "gc_code", "found", "difficulty"])
        dlg = ColumnChooserDialog()
        qtbot.addWidget(dlg)
        for i in range(dlg._list.count()):
            item = dlg._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == "country":
                item.setCheckState(Qt.CheckState.Checked)
        dlg._save_and_accept()
        saved = get_visible_columns()
        drag_cols = [c for c in saved if c in {"name", "gc_code", "found", "difficulty"}]
        assert drag_cols == ["name", "gc_code", "found", "difficulty"]
        assert "country" in saved
        assert saved.index("difficulty") < saved.index("country")

    def test_container_display_combo_saves_on_accept(self, qtbot, store):
        dlg = ColumnChooserDialog()
        qtbot.addWidget(dlg)
        dlg._container_display_combo.setCurrentIndex(1)  # "text"
        dlg._save_and_accept()
        assert get_container_display() == "text"

    def test_type_display_combo_saves_on_accept(self, qtbot, store):
        dlg = ColumnChooserDialog()
        qtbot.addWidget(dlg)
        dlg._type_display_combo.setCurrentIndex(1)  # "text"
        dlg._save_and_accept()
        assert get_type_display() == "text"
        dlg2 = ColumnChooserDialog()
        qtbot.addWidget(dlg2)
        dlg2._type_display_combo.setCurrentIndex(2)  # "both"
        dlg2._save_and_accept()
        assert get_type_display() == "both"

    def test_remove_col_preserves_order_of_remainder(self, qtbot, store):
        set_visible_columns(["name", "gc_code", "found", "difficulty", "terrain"])
        dlg = ColumnChooserDialog()
        qtbot.addWidget(dlg)
        for i in range(dlg._list.count()):
            item = dlg._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == "found":
                item.setCheckState(Qt.CheckState.Unchecked)
        dlg._save_and_accept()
        saved = get_visible_columns()
        assert "found" not in saved
        remaining = [c for c in saved if c in {"name", "gc_code", "difficulty", "terrain"}]
        assert remaining == ["name", "gc_code", "difficulty", "terrain"]

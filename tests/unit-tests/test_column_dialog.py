# tests/unit-tests/test_column_dialog.py — column chooser dialog + store helpers.

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt

from opensak.gui.dialogs import column_dialog as cd
from opensak.gui.dialogs.column_dialog import (
    ALWAYS_VISIBLE,
    ColumnChooserDialog,
    ColumnView,
    current_column_view_match,
    get_all_columns,
    get_column_widths,
    get_container_display,
    get_default_view,
    get_default_view_name,
    get_type_display,
    get_visible_columns,
    set_column_widths,
    set_container_display,
    set_default_view_name,
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


@pytest.fixture
def views_dir(tmp_path, monkeypatch):
    """Isolated on-disk folder for ColumnView JSON files, per test."""
    d = tmp_path / "column_views"
    monkeypatch.setattr(ColumnView, "_views_dir", staticmethod(lambda: d))
    return d


@pytest.fixture
def fake_active_db(monkeypatch):
    """Let a test control which 'active database name' _col_key() sees,
    without needing a real Database/DatabaseManager instance."""
    class _FakeDb:
        def __init__(self, name):
            self.name = name

    class _FakeManager:
        def __init__(self):
            self.active = None

    manager = _FakeManager()

    def _get_db_manager():
        return manager

    import opensak.db.manager as db_manager_module
    monkeypatch.setattr(db_manager_module, "get_db_manager", _get_db_manager)

    def _set_active(name):
        manager.active = _FakeDb(name) if name is not None else None

    return _set_active


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


# ── Issue #607: new/unconfigured database should inherit the default view ───

class TestDefaultView:
    """A brand-new database (or one that never had its own columns.<name>.*
    key) should fall back to the user-designated default Column View
    (issue #607) — not to whatever database happened to be edited last
    (that implicit #606 behaviour has been replaced by this explicit one),
    and not directly to the hard-coded factory defaults while a default
    view is set."""

    def test_new_database_inherits_visible_columns_from_default_view(
        self, store, views_dir, fake_active_db
    ):
        ColumnView(
            "My Standard",
            visible_columns=["gc_code", "name", "country", "state", "county"],
        ).save()
        set_default_view_name("My Standard")

        fake_active_db("New Trip DB")  # brand-new database, nothing saved yet
        assert get_visible_columns() == ["gc_code", "name", "country", "state", "county"]

    def test_new_database_inherits_column_widths_from_default_view(
        self, store, views_dir, fake_active_db
    ):
        ColumnView(
            "My Standard",
            visible_columns=["gc_code", "name"],
            widths={"gc_code": 90, "name": 300},
        ).save()
        set_default_view_name("My Standard")

        fake_active_db("New Trip DB")
        assert get_column_widths() == {"gc_code": 90, "name": 300}

    def test_new_database_inherits_display_modes_from_default_view(
        self, store, views_dir, fake_active_db
    ):
        ColumnView(
            "My Standard",
            visible_columns=["gc_code", "name"],
            container_display="text",
            type_display="both",
        ).save()
        set_default_view_name("My Standard")

        fake_active_db("New Trip DB")
        assert get_container_display() == "text"
        assert get_type_display() == "both"

    def test_database_with_its_own_saved_settings_is_not_overridden(
        self, store, views_dir, fake_active_db
    ):
        ColumnView("My Standard", visible_columns=["gc_code", "name", "country"]).save()
        set_default_view_name("My Standard")

        fake_active_db("Other DB")
        set_visible_columns(["gc_code", "name", "difficulty"])

        # "Other DB" has its own explicit choice — it must not be replaced
        # by the default view, even though a default view exists.
        assert get_visible_columns() == ["gc_code", "name", "difficulty"]

    def test_setting_default_does_not_retroactively_change_other_databases(
        self, store, views_dir, fake_active_db
    ):
        fake_active_db("Untouched DB")  # never configured, no default set yet
        assert get_visible_columns() == [col[0] for col in get_all_columns() if col[3]]

        ColumnView("My Standard", visible_columns=["gc_code", "name", "country"]).save()
        set_default_view_name("My Standard")

        # Same, still-unconfigured database now picks up the default view.
        assert get_visible_columns() == ["gc_code", "name", "country"]

    def test_no_default_view_set_falls_back_to_hard_coded_defaults(
        self, store, views_dir, fake_active_db
    ):
        fake_active_db("First Ever DB")
        vis = get_visible_columns()
        assert "gc_code" in vis
        assert "country" not in vis

    def test_get_default_view_returns_none_when_unset(self, store, views_dir):
        assert get_default_view_name() is None
        assert get_default_view() is None

    def test_deleting_the_default_view_file_clears_the_reference(
        self, store, views_dir, fake_active_db
    ):
        path = ColumnView("My Standard", visible_columns=["gc_code", "name"]).save()
        set_default_view_name("My Standard")
        path.unlink()

        # get_default_view() should notice the file is gone, clear the
        # dangling reference, and fall back cleanly.
        assert get_default_view() is None
        assert get_default_view_name() is None


# ── Issue #996: preselect the active database's view ─────────────────────────

def _save_current_setup_as(name: str) -> Path:
    """Save a ColumnView that matches the active database's current setup."""
    return ColumnView(
        name,
        visible_columns=get_visible_columns(),
        widths=get_column_widths(),
        container_display=get_container_display(),
        type_display=get_type_display(),
    ).save()


class TestCurrentViewMatch:
    def test_match_returns_view_name(self, store, views_dir):
        ColumnView("Other", visible_columns=["gc_code", "name", "found"]).save()
        _save_current_setup_as("Mine")
        assert current_column_view_match() == "Mine"

    def test_no_match_returns_none(self, store, views_dir):
        ColumnView("Other", visible_columns=["gc_code", "name", "found"]).save()
        assert current_column_view_match() is None

    def test_corrupt_view_file_is_skipped(self, store, views_dir):
        views_dir.mkdir(parents=True, exist_ok=True)
        (views_dir / "broken.json").write_text("{not json", encoding="utf-8")
        _save_current_setup_as("Mine")
        assert current_column_view_match() == "Mine"


class TestDialogPreselectsView:
    def test_preselects_matching_view(self, qtbot, store, views_dir):
        ColumnView("Other", visible_columns=["gc_code", "name", "found"]).save()
        path = _save_current_setup_as("Mine")
        dlg = ColumnChooserDialog()
        qtbot.addWidget(dlg)
        assert dlg._view_combo.currentText() == "Mine"
        assert dlg._view_combo.currentData() == path
        # The view's buttons follow the preselection.
        assert dlg._del_view_btn.isEnabled()
        assert dlg._set_default_btn.isEnabled()

    def test_preselects_matching_default_view(self, qtbot, store, views_dir):
        # The report: even the default (★) view showed "(None)".
        _save_current_setup_as("My Standard")
        set_default_view_name("My Standard")
        dlg = ColumnChooserDialog()
        qtbot.addWidget(dlg)
        assert dlg._view_combo.currentText() == "★ My Standard"

    def test_shows_none_when_nothing_matches(self, qtbot, store, views_dir):
        ColumnView("Other", visible_columns=["gc_code", "name", "found"]).save()
        dlg = ColumnChooserDialog()
        qtbot.addWidget(dlg)
        assert dlg._view_combo.currentIndex() == 0
        assert dlg._view_combo.currentData() is None
        assert not dlg._del_view_btn.isEnabled()

    def test_preselection_does_not_change_the_checkboxes(self, qtbot, store, views_dir):
        # Preselecting must not re-apply the view (signals are blocked);
        # the list shows the active database's columns either way.
        set_visible_columns(["name", "gc_code", "found"])
        _save_current_setup_as("Mine")
        dlg = ColumnChooserDialog()
        qtbot.addWidget(dlg)
        checked = {
            dlg._list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(dlg._list.count())
            if dlg._list.item(i).checkState() == Qt.CheckState.Checked
        }
        assert checked == {"name", "gc_code", "found"} | ALWAYS_VISIBLE


# ── Issue #995: Save asks before overwriting an existing view ────────────────

class TestSaveViewOverwrite:
    @pytest.fixture
    def save_as(self, monkeypatch):
        """Return (dialog_factory, asked) — the dialog's Save prompts are
        stubbed: the name dialog returns *name*, the overwrite question
        records its text and answers *answer*."""
        from PySide6.QtWidgets import QMessageBox as QtMessageBox
        import opensak.lang as lang
        from opensak.lang.en import STRINGS
        monkeypatch.setattr(lang, "_translations", STRINGS)  # real message text
        asked: list[str] = []

        def run(qtbot, name: str, answer=QtMessageBox.StandardButton.No):
            monkeypatch.setattr(cd.QInputDialog, "getText",
                                staticmethod(lambda *a, **k: (name, True)))

            def _question(parent, title, text, *a, **k):
                asked.append(text)
                return answer
            monkeypatch.setattr(cd.QMessageBox, "question", staticmethod(_question))
            monkeypatch.setattr(cd.QMessageBox, "information",
                                staticmethod(lambda *a, **k: None))
            dlg = ColumnChooserDialog()
            qtbot.addWidget(dlg)
            dlg._save_view()
            return dlg

        return run, asked

    def test_new_name_saves_without_asking(self, qtbot, store, views_dir, save_as):
        run, asked = save_as
        run(qtbot, "Fresh")
        assert asked == []
        assert ColumnView.load(ColumnView.path_for("Fresh")).name == "Fresh"

    def test_existing_name_no_keeps_old_view(self, qtbot, store, views_dir, save_as):
        run, asked = save_as
        path = ColumnView("Trip", visible_columns=["gc_code", "name", "found"]).save()
        run(qtbot, "Trip")
        assert len(asked) == 1 and "Trip" in asked[0]
        assert ColumnView.load(path).visible_columns == ["gc_code", "name", "found"]

    def test_existing_name_yes_overwrites(self, qtbot, store, views_dir, save_as):
        from PySide6.QtWidgets import QMessageBox as QtMessageBox
        run, asked = save_as
        path = ColumnView("Trip", visible_columns=["gc_code", "name", "found"]).save()
        run(qtbot, "Trip", QtMessageBox.StandardButton.Yes)
        assert len(asked) == 1
        assert ColumnView.load(path).visible_columns == get_visible_columns()

    def test_same_file_name_counts_as_existing(self, qtbot, store, views_dir, save_as):
        # "My/View" and "My_View" both map to My_View.json.
        from PySide6.QtWidgets import QMessageBox as QtMessageBox
        run, asked = save_as
        path = ColumnView("My_View", visible_columns=["gc_code", "name"]).save()
        run(qtbot, "My/View", QtMessageBox.StandardButton.Yes)
        assert len(asked) == 1 and "My_View" in asked[0]
        assert ColumnView.load(path).name == "My/View"

    def test_overwritten_default_view_stays_default_under_new_name(
        self, qtbot, store, views_dir, save_as,
    ):
        from PySide6.QtWidgets import QMessageBox as QtMessageBox
        run, _ = save_as
        ColumnView("My_View", visible_columns=["gc_code", "name"]).save()
        set_default_view_name("My_View")
        run(qtbot, "My/View", QtMessageBox.StandardButton.Yes)
        assert get_default_view_name() == "My/View"
        assert get_default_view() is not None

    def test_default_untouched_when_overwrite_declined(
        self, qtbot, store, views_dir, save_as,
    ):
        run, _ = save_as
        ColumnView("My_View", visible_columns=["gc_code", "name"]).save()
        set_default_view_name("My_View")
        run(qtbot, "My/View")
        assert get_default_view_name() == "My_View"

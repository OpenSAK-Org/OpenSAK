"""tests/unit-tests/test_filter_profile_name_1308.py — #1308.

The toolbar dropdown showed a saved profile that was not what was applied:
load a profile, change it in "Set filter" (or "Reset all tabs") and Apply,
and the dropdown still showed the old profile, which was also stored as the
database's filter. A status-bar count click or a macro label was stored as a
profile name too.

Now only a saved profile applied unchanged is the active profile; anything
else is "Active (unsaved)", with its label only in the 🔍 label, the info
bar, opensak.filter_name() and export file names.
"""

import pytest

pytest.importorskip("pytestqt")

from opensak.filters.engine import (  # noqa: E402
    DifficultyFilter, FilterProfile, FilterSet, SortSpec, TerrainFilter,
)


@pytest.fixture(autouse=True)
def _english():
    from opensak.lang import load_language
    load_language("en")


def _profile(name: str, *filters) -> FilterSet:
    fs = FilterSet()
    for f in filters:
        fs.add(f)
    FilterProfile(name, fs).save()
    return fs


# ── Set filter dialog ────────────────────────────────────────────────────────

@pytest.fixture
def dialog(qtbot):
    from opensak.gui.dialogs.filter_dialog import FilterDialog

    _profile("Easy", DifficultyFilter(max_difficulty=2.0))
    dlg = FilterDialog(None, None, "Easy")
    qtbot.addWidget(dlg)
    emitted: list[str] = []
    dlg.filter_applied.connect(lambda fs, sort, name: emitted.append(name))
    dlg.emitted = emitted
    assert dlg._profile_combo.currentText() == "Easy"
    return dlg


class TestDialog:
    def test_unchanged_profile_is_reported(self, dialog):
        dialog._apply()
        assert dialog.emitted == ["Easy"]

    def test_changed_criteria_are_not_the_profile(self, dialog):
        dialog._terr_max.setValue(3.0)
        dialog._apply()
        assert dialog.emitted == [""]

    def test_reset_all_sets_the_combo_to_none(self, dialog):
        dialog._on_reset_all_clicked()
        assert dialog._profile_combo.currentIndex() == 0
        assert not dialog._del_btn.isEnabled()
        dialog._diff_min.setValue(4.0)
        dialog._apply()
        assert dialog.emitted == [""]

    def test_changing_back_counts_as_unchanged(self, dialog):
        dialog._terr_max.setValue(3.0)
        dialog._terr_max.setValue(5.0)
        dialog._apply()
        assert dialog.emitted == ["Easy"]

    def test_a_newly_saved_profile_is_reported(self, dialog, monkeypatch):
        from PySide6.QtWidgets import QInputDialog

        from opensak.gui.icon import OpenSAKMessageBox

        dialog._terr_max.setValue(3.0)
        monkeypatch.setattr(QInputDialog, "getText",
                            staticmethod(lambda *a, **k: ("Easy flat", True)))
        monkeypatch.setattr(OpenSAKMessageBox, "information", staticmethod(lambda *a, **k: None))
        dialog._save_profile()
        dialog._apply()
        assert dialog.emitted == ["Easy flat"]


# ── Main window ──────────────────────────────────────────────────────────────

@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    import opensak.db.manager as mgr_module
    from opensak.db.database import get_session, init_db
    from opensak.db.models import Cache
    from opensak.gui.mainwindow import MainWindow
    from tests.data import make_fake_manager

    monkeypatch.setattr(MainWindow, "_check_update_background", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_setup_complete", lambda self: None)
    db_path = tmp_path / "profile_name_1308.db"
    init_db(db_path=db_path)
    with get_session() as s:
        for i in range(4):
            s.add(Cache(gc_code=f"GC13{i}", name=f"Cache {i}", cache_type="Traditional Cache",
                        latitude=55.0 + i / 100, longitude=12.0,
                        difficulty=1.0 + i, terrain=1.0, found=i == 0))
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="Profile1308"))
    win = MainWindow()
    qtbot.addWidget(win)
    win._refresh_cache_list()
    qtbot.waitUntil(lambda: not win._active_refresh_workers)
    yield win
    win.close()
    mgr_module._manager = None


def _stored_profile() -> str:
    from opensak.db import db_settings
    from opensak.db.manager import get_db_manager
    active = get_db_manager().active
    assert active is not None
    path = active.path
    return str(db_settings.get_value("sort.filter_profile",
                                     db_settings.legacy_sort_key(path, "filter_profile"), ""))


def _select_profile(window, name: str) -> None:
    window._populate_filter_profile_combo()
    idx = window._filter_profile_combo.findText(name)
    assert idx > 0
    window._filter_profile_combo.setCurrentIndex(idx)
    window._on_filter_profile_combo_changed(idx)   # what a user's pick runs
    assert window._active_filter_name == name


class TestMainWindow:
    def test_changed_dialog_filter_is_active_unsaved(self, window):
        _profile("Easy", DifficultyFilter(max_difficulty=2.0))
        _select_profile(window, "Easy")
        assert _stored_profile() == "Easy"

        # What the dialog now reports after Reset all + new criteria.
        fs = FilterSet()
        fs.add(DifficultyFilter(min_difficulty=3.0))
        window._on_filter_applied(fs, SortSpec("name"), "")

        assert window._active_filter_name == ""
        assert window._filter_profile_combo.currentIndex() == 0
        assert window._filter_profile_combo.currentText() == "Active (unsaved)"
        assert _stored_profile() == ""
        assert window._cache_table.row_count() == 2

    def test_unchanged_profile_from_dialog_stays_the_profile(self, window):
        fs = _profile("Easy", DifficultyFilter(max_difficulty=2.0))
        window._on_filter_applied(fs, SortSpec("name"), "Easy")
        assert window._active_filter_name == "Easy"
        assert window._filter_profile_combo.currentText() == "Easy"
        assert _stored_profile() == "Easy"

    def test_status_bar_click_is_a_label_not_a_profile(self, window):
        window._filter_by_status("found")
        assert window._active_filter_name == ""
        assert window._filter_profile_combo.currentText() == "Active (unsaved)"
        assert window._filter_lbl.text() == "🔍 Found"
        assert window.filter_name() == "Found"
        assert _stored_profile() == ""

    def test_macro_label_matching_a_profile_name_is_not_that_profile(self, window):
        _profile("Easy", DifficultyFilter(max_difficulty=2.0))
        fs = FilterSet()
        fs.add(TerrainFilter(max_terrain=1.0))
        assert window.apply_filter(fs, "Easy") == 4
        assert window._active_filter_name == ""
        assert window._filter_profile_combo.currentText() == "Active (unsaved)"
        assert window.filter_name() == "Easy"
        assert _stored_profile() == ""

    def test_macro_applying_a_profile_unchanged_selects_it(self, window):
        fs = _profile("Easy", DifficultyFilter(max_difficulty=2.0))
        loaded = FilterProfile.load(FilterProfile.profile_path("Easy")).filterset
        assert window.apply_filter(loaded, "Easy") == 2
        assert window._active_filter_name == "Easy"
        assert window._filter_profile_combo.currentText() == "Easy"
        assert _stored_profile() == "Easy"
        assert fs.to_dict() == loaded.to_dict()

    def test_clear_forgets_the_label(self, window):
        window._filter_by_status("found")
        window._clear_filter()
        assert window.filter_name() == ""
        assert window._filter_lbl.text() == ""

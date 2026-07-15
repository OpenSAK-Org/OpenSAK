# tests/unit-tests/test_filter_dialog.py — complete filter dialog (build/load/profiles).

from datetime import datetime
from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QInputDialog
from PySide6.QtCore import QDate

from opensak.gui.dialogs import filter_dialog as fd
from opensak.gui.dialogs.filter_dialog import FilterDialog, TriStateBox, DTSpinBox
from opensak.filters.engine import (
    FilterSet, NameFilter, GcCodeFilter, PlacedByFilter, OwnerFilter,
    CacheTypeFilter, ContainerFilter, DifficultyFilter, TerrainFilter,
    FoundFilter, NotFoundFilter, AvailabilityFilter, DistanceFilter,
    PremiumFilter, NonPremiumFilter, HasTrackableFilter, HasCorrectedFilter, NoCorrectedFilter,
    CountryFilter, StateFilter, CountyFilter, UserFlagFilter, LockedFilter, DnfFilter,
    FtfFilter, FavoritePointsFilter, AttributeFilter, WhereClauseFilter,
    FoundByMeDateFilter, DnfDateFilter, LastLogDateFilter, TextSearchFilter,
    FilterProfile,
)


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    # No real profiles on disk; deterministic home for DistanceFilter.
    monkeypatch.setattr(fd.FilterProfile, "list_profiles", staticmethod(lambda: []))
    from opensak.utils.types import DateFormat, CoordFormat
    monkeypatch.setattr("opensak.gui.settings.get_settings",
                        lambda: SimpleNamespace(home_lat=55.0, home_lon=12.0, use_miles=False,
                                               date_format=DateFormat.YMD,
                                               coord_format=CoordFormat.DD, home_points=[]))


@pytest.fixture
def dlg(qtbot):
    d = FilterDialog()
    qtbot.addWidget(d)
    return d


# ── multi-monitor positioning (#580) ─────────────────────────────────────────

class TestScreenPositioning:
    def test_uses_parent_screen_not_primary(self, qtbot, monkeypatch):
        # Issue #580: on a multi-monitor setup, the filter dialog always
        # opened on the primary screen instead of whichever screen the main
        # window (its parent) was actually on.
        from PySide6.QtCore import QRect
        from PySide6.QtWidgets import QWidget

        primary_screen = MagicMock()
        primary_screen.availableGeometry.return_value = QRect(0, 0, 1920, 1080)
        secondary_screen = MagicMock()
        secondary_screen.availableGeometry.return_value = QRect(1920, 0, 1920, 1080)

        import PySide6.QtWidgets as _qtw
        monkeypatch.setattr(_qtw.QApplication, "primaryScreen", staticmethod(lambda: primary_screen))

        parent = QWidget()
        qtbot.addWidget(parent)
        parent.screen = lambda: secondary_screen  # PySide6 QWidget instances allow this

        d = FilterDialog(parent=parent)
        qtbot.addWidget(d)

        assert not primary_screen.availableGeometry.called
        assert secondary_screen.availableGeometry.called
        # Centred within the secondary screen's bounds (x >= 1920), not the
        # primary screen's (x in [0, 1920)).
        assert d.pos().x() >= 1920

    def test_falls_back_to_primary_screen_without_a_parent(self, qtbot, monkeypatch):
        from PySide6.QtCore import QRect
        primary_screen = MagicMock()
        primary_screen.availableGeometry.return_value = QRect(0, 0, 1920, 1080)
        import PySide6.QtWidgets as _qtw
        monkeypatch.setattr(_qtw.QApplication, "primaryScreen", staticmethod(lambda: primary_screen))

        d = FilterDialog(parent=None)
        qtbot.addWidget(d)

        assert primary_screen.availableGeometry.called


# ── helper widgets ──────────────────────────────────────────────────────────────

class TestHelperWidgets:
    def test_tristate(self, qtbot):
        box = TriStateBox()
        qtbot.addWidget(box)
        assert box.state is None
        box._ja.setChecked(True)
        assert box.state is True
        box._ja.setChecked(False)
        box._nej.setChecked(True)
        assert box.state is False
        box.reset()
        assert box.state is None

    def test_dtspinbox(self, qtbot):
        sb = DTSpinBox()
        qtbot.addWidget(sb)
        assert sb.minimum() == 1.0 and sb.maximum() == 5.0
        assert sb.singleStep() == 0.5


# ── construction ────────────────────────────────────────────────────────────────

class TestConstruction:
    def test_six_tabs(self, dlg):
        assert dlg._tabs.count() == 6

    def test_init_with_filterset(self, qtbot):
        fs = FilterSet(mode="AND")
        fs.add(NameFilter("hello"))
        d = FilterDialog(current_filterset=fs)
        qtbot.addWidget(d)
        assert d._name_filter.text() == "hello"


# ── build_filterset ─────────────────────────────────────────────────────────────

def _types(fs):
    return [getattr(f, "filter_type", None) for f in fs._filters]


class TestBuildFilterset:
    def test_empty_is_empty(self, dlg):
        dlg._archived_cb.setChecked(True)  # all three statuses -> no availability filter
        fs = dlg._build_filterset()
        assert fs._filters == []

    def test_text_filters(self, dlg):
        dlg._name_filter.setText("n")
        dlg._gc_filter.setText("GC1")
        dlg._placed_filter.setText("p")
        dlg._owner_filter.setText("o")
        types = _types(dlg._build_filterset())
        assert {"name", "gc_code", "placed_by", "owner_name"} <= set(types)

    def test_type_and_container_subset(self, dlg):
        # uncheck one type and one container -> filters added
        first_type = next(iter(dlg._type_checks.values()))
        first_type.setChecked(False)
        first_cont = next(iter(dlg._cont_checks.values()))
        first_cont.setChecked(False)
        types = _types(dlg._build_filterset())
        assert "cache_type" in types and "container" in types

    def test_dt_filters(self, dlg):
        dlg._diff_min.setValue(2.0)
        dlg._terr_max.setValue(4.0)
        types = _types(dlg._build_filterset())
        assert "difficulty" in types and "terrain" in types

    def test_found_only_and_notfound_only(self, dlg):
        dlg._notfound_cb.setChecked(False)
        assert "found" in _types(dlg._build_filterset())
        dlg._notfound_cb.setChecked(True)
        dlg._found_cb.setChecked(False)
        assert "not_found" in _types(dlg._build_filterset())

    def test_availability_filter(self, dlg):
        dlg._unavail_cb.setChecked(False)  # not all three selected
        assert "availability" in _types(dlg._build_filterset())

    def test_distance_filter(self, dlg):
        dlg._dist_enabled.setChecked(True)
        assert "distance" in _types(dlg._build_filterset())

    def test_default_availability_state_does_not_count(self, dlg):
        # Mike's report: setting only a distance filter (leaving Available/
        # Unavailable checked and Archived unchecked, i.e. all defaults)
        # showed "2 active" instead of "1 active". The default availability
        # state still adds a real AvailabilityFilter (archived caches must
        # stay hidden), but it must not count toward the active badge.
        dlg._dist_enabled.setChecked(True)
        fs = dlg._build_filterset()
        assert set(_types(fs)) == {"distance", "availability"}
        assert len(fs) == 2
        assert fs.active_count() == 1

    def test_default_availability_state_alone_counts_zero(self, dlg):
        # Opening the dialog and applying with no changes at all should not
        # register as "1 active" even though an AvailabilityFilter is
        # silently present to keep archived caches hidden.
        fs = dlg._build_filterset()
        assert _types(fs) == ["availability"]
        assert fs.active_count() == 0

    def test_explicit_availability_change_still_counts(self, dlg):
        # A deliberate availability change (e.g. also showing archived
        # caches) is a real, user-chosen filter and must still count.
        dlg._archived_cb.setChecked(True)
        dlg._unavail_cb.setChecked(False)
        fs = dlg._build_filterset()
        assert "availability" in _types(fs)
        assert fs.active_count() == 1

    def test_premium_and_trackable_and_corrected(self, dlg):
        dlg._prem_no.setChecked(False)
        dlg._tb_no.setChecked(False)
        dlg._cc_no.setChecked(False)
        types = _types(dlg._build_filterset())
        assert "premium" in types and "has_trackable" in types and "has_corrected" in types
        dlg._prem_yes.setChecked(False)
        dlg._prem_no.setChecked(True)
        assert "non_premium" in _types(dlg._build_filterset())

    def test_no_corrected_checkbox_alone_builds_filter(self, dlg):
        # Bug #274 — checking only "no corrected" (unchecking "has corrected")
        # produced no filter at all, so the flag was silently ignored.
        dlg._cc_yes.setChecked(False)
        dlg._cc_no.setChecked(True)
        assert "no_corrected" in _types(dlg._build_filterset())

    def test_loads_no_corrected_filter(self, dlg):
        fs = FilterSet(mode="AND")
        fs.add(NoCorrectedFilter())
        dlg._load_filterset(fs)
        assert dlg._cc_no.isChecked() is True
        assert dlg._cc_yes.isChecked() is False

    def test_misc_filters(self, dlg):
        dlg._country_filter.setText("DK")
        dlg._state_filter.setText("Z")
        dlg._county_filter.setText("C")
        dlg._flag_no.setChecked(False)   # flag yes only
        dlg._locked_no.setChecked(False)  # locked yes only (issue #202)
        dlg._dnf_no.setChecked(False)
        dlg._ftf_no.setChecked(False)
        dlg._fav_enabled.setChecked(True)
        types = _types(dlg._build_filterset())
        assert {"country", "state", "county", "user_flag", "locked", "dnf", "ftf",
                "favorite_points"} <= set(types)

    def test_loads_locked_filter(self, dlg):
        # Issue #202: round-trip a saved "Locked = No" profile.
        fs = FilterSet(mode="AND")
        fs.add(LockedFilter(locked=False))
        dlg._load_filterset(fs)
        assert dlg._locked_no.isChecked() is True
        assert dlg._locked_yes.isChecked() is False

    def test_reset_misc_clears_locked(self, dlg):
        dlg._locked_no.setChecked(False)
        dlg._reset_misc()
        assert dlg._locked_yes.isChecked() is True
        assert dlg._locked_no.isChecked() is True

    def test_date_filters(self, dlg):
        dlg._hidden_from_enabled.setChecked(True)
        dlg._found_from_enabled.setChecked(True)
        dlg._dnf_date_from_enabled.setChecked(True)
        dlg._log_from_enabled.setChecked(True)
        types = _types(dlg._build_filterset())
        assert "found_by_me_date" in types
        assert "dnf_date" in types
        assert "last_log_date" in types
        assert "hidden_date_range" in types

    def test_attributes_and_mode(self, dlg):
        attr_id = next(iter(dlg._attr_boxes))
        ja, nej, ingen = dlg._attr_boxes[attr_id]
        ja.setChecked(True)
        fs = dlg._build_filterset()
        assert any(getattr(f, "filter_type", None) == "attribute" for f in fs._filters)

    def test_attributes_or_mode(self, dlg):
        dlg._attr_mode_all.setChecked(False)  # ANY/OR mode
        ids = list(dlg._attr_boxes)[:2]
        for aid in ids:
            dlg._attr_boxes[aid][0].setChecked(True)
        fs = dlg._build_filterset()
        # nested OR FilterSet present
        assert any(isinstance(f, FilterSet) and f.mode == "OR" for f in fs._filters)

    def test_where_clause(self, dlg):
        dlg._where_sql_general.setPlainText("found = 0")
        assert "where_clause" in _types(dlg._build_filterset())

    def test_text_search_builds_filter(self, dlg):
        dlg._text_search_input.setText("waterfall")
        types = _types(dlg._build_filterset())
        assert "text_search" in types

    def test_text_search_empty_text_no_filter(self, dlg):
        dlg._text_search_input.setText("  ")
        assert "text_search" not in _types(dlg._build_filterset())

    def test_text_search_hint_flag_propagates(self, dlg):
        dlg._text_search_input.setText("rock")
        dlg._text_search_hint.setChecked(True)
        fs = dlg._build_filterset()
        f = next(f for f in fs._filters if getattr(f, "filter_type", None) == "text_search")
        assert f.search_hint is True

    def test_text_search_logs_enabled_by_default(self, dlg):
        dlg._text_search_input.setText("TFTC")
        fs = dlg._build_filterset()
        f = next(f for f in fs._filters if getattr(f, "filter_type", None) == "text_search")
        assert f.search_logs is True


# ── load_filterset roundtrip ────────────────────────────────────────────────────

class TestLoadFilterset:
    def test_loads_many_filters(self, dlg):
        fs = FilterSet(mode="AND")
        fs.add(NameFilter("nm"))
        fs.add(GcCodeFilter("GC9"))
        fs.add(PlacedByFilter("pb"))
        fs.add(OwnerFilter("ow"))
        fs.add(DifficultyFilter(2.0, 4.0))
        fs.add(TerrainFilter(1.5, 3.5))
        fs.add(NotFoundFilter())
        fs.add(AvailabilityFilter(show_avail=True, show_unavail=False, show_archived=True))
        fs.add(DistanceFilter(55.0, 12.0, 25.0))
        fs.add(PremiumFilter())
        fs.add(HasTrackableFilter())
        fs.add(HasCorrectedFilter())
        fs.add(CountryFilter("DK"))
        fs.add(StateFilter("Z"))
        fs.add(CountyFilter("Cty"))
        fs.add(UserFlagFilter(flagged=True))
        fs.add(DnfFilter(has_dnf=False))
        fs.add(FtfFilter(has_ftf=True))
        fs.add(FavoritePointsFilter(min_pts=10, max_pts=200))
        fs.add(WhereClauseFilter("found = 0"))
        dlg._load_filterset(fs)
        assert dlg._name_filter.text() == "nm"
        assert dlg._gc_filter.text() == "GC9"
        assert dlg._diff_min.value() == 2.0
        assert dlg._notfound_cb.isChecked() and not dlg._found_cb.isChecked()
        assert dlg._dist_enabled.isChecked()
        assert dlg._country_filter.text() == "DK"
        assert dlg._fav_enabled.isChecked()
        assert dlg._where_sql_general.toPlainText() == "found = 0"

    def test_loads_types_and_container(self, dlg):
        from opensak.utils.constants import CACHE_TYPES, CONTAINER_SIZES
        fs = FilterSet(mode="AND")
        fs.add(CacheTypeFilter([CACHE_TYPES[0]]))
        fs.add(ContainerFilter([CONTAINER_SIZES[0]]))
        dlg._load_filterset(fs)
        assert dlg._type_checks[CACHE_TYPES[0]].isChecked()
        assert not dlg._type_checks[CACHE_TYPES[1]].isChecked()

    def test_loads_date_filters(self, dlg):
        fs = FilterSet(mode="AND")
        fs.add(FoundByMeDateFilter(from_date=datetime(2020, 1, 1),
                                   to_date=datetime(2021, 1, 1)))
        fs.add(DnfDateFilter(from_date=datetime(2020, 2, 2), to_date=None))
        fs.add(LastLogDateFilter(from_date=None, to_date=datetime(2022, 3, 3)))
        dlg._load_filterset(fs)
        assert dlg._found_from_enabled.isChecked()
        assert dlg._dnf_date_from_enabled.isChecked()
        assert dlg._log_to_enabled.isChecked()

    def test_loads_attribute_or_group_sets_any_mode(self, dlg):
        attr_id = next(iter(dlg._attr_boxes))
        inner = FilterSet(mode="OR")
        inner.add(AttributeFilter(attr_id, True))
        fs = FilterSet(mode="AND")
        fs.add(inner)
        dlg._load_filterset(fs)        # exercises the fixed _attr_mode_all toggle
        assert dlg._attr_mode_all.isChecked() is False
        assert dlg._attr_boxes[attr_id][0].isChecked() is True

    def test_loads_text_search_filter(self, dlg):
        fs = FilterSet(mode="AND")
        fs.add(TextSearchFilter("waterfall", search_description=True,
                                search_logs=False, search_notes=False, search_hint=True))
        dlg._load_filterset(fs)
        assert dlg._text_search_input.text() == "waterfall"
        assert dlg._text_search_description.isChecked() is True
        assert dlg._text_search_logs.isChecked() is False
        assert dlg._text_search_notes.isChecked() is False
        assert dlg._text_search_hint.isChecked() is True


# ── reset ───────────────────────────────────────────────────────────────────────

class TestReset:
    def test_reset_all(self, dlg):
        dlg._name_filter.setText("x")
        dlg._country_filter.setText("y")
        dlg._where_sql_general.setPlainText("found = 0")
        dlg._reset_all()
        assert dlg._name_filter.text() == ""
        assert dlg._country_filter.text() == ""
        assert dlg._where_sql_general.toPlainText() == ""

    def test_reset_current_tab_each(self, dlg):
        for i in range(dlg._tabs.count()):
            dlg._tabs.setCurrentIndex(i)
            dlg._reset_current_tab()  # no crash for any tab

    def test_enable_disable_all_types(self, dlg):
        dlg._disable_all_types()
        assert all(not cb.isChecked() for cb in dlg._type_checks.values())
        dlg._enable_all_types()
        assert all(cb.isChecked() for cb in dlg._type_checks.values())

    def test_toggles(self, dlg):
        dlg._on_dist_toggled(True)
        assert dlg._dist_max.isEnabled()
        dlg._on_fav_toggled(True)
        assert dlg._fav_min.isEnabled() and dlg._fav_max.isEnabled()


# ── where SQL validation ────────────────────────────────────────────────────────

class TestWhereSql:
    def test_validate_valid(self, dlg, db_session):
        assert dlg._validate_where_sql("found = 0") is None

    def test_validate_invalid(self, dlg, db_session):
        err = dlg._validate_where_sql("no_such_column = 1")
        assert err is not None

    def test_show_where_info(self, dlg, monkeypatch):
        class _NoExec(fd.QDialog):
            def exec(self):
                return 0
        monkeypatch.setattr(fd, "QDialog", _NoExec)
        dlg._show_where_info()  # builds + (fake) exec, no block


# ── profiles ────────────────────────────────────────────────────────────────────

class TestProfiles:
    @pytest.fixture(autouse=True)
    def _no_modal(self, monkeypatch):
        # Never let a profile-combo signal pop a real (blocking) message box.
        monkeypatch.setattr(fd.QMessageBox, "warning", MagicMock())
        monkeypatch.setattr(fd.QMessageBox, "information", MagicMock())

    def test_save_profile(self, dlg, monkeypatch):
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("MyProfile", True))
        saved = {}
        monkeypatch.setattr(fd.FilterProfile, "save",
                            lambda self, *a, **k: saved.update(name=self.name))
        monkeypatch.setattr(fd.QMessageBox, "information", MagicMock())
        dlg._name_filter.setText("foo")
        dlg._save_profile()
        assert saved.get("name") == "MyProfile"

    def test_save_profile_cancelled(self, dlg, monkeypatch):
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("", False))
        called = []
        monkeypatch.setattr(fd.FilterProfile, "save", lambda self, *a, **k: called.append(True))
        dlg._save_profile()
        assert called == []

    def test_on_profile_selected_none(self, dlg):
        dlg._on_profile_selected(0)  # "none" entry -> del btn disabled
        assert dlg._del_btn.isEnabled() is False

    def test_on_profile_selected_loads(self, dlg, monkeypatch, tmp_path):
        fs = FilterSet(mode="AND")
        fs.add(NameFilter("loaded"))
        prof = SimpleNamespace(filterset=fs)
        # Patch load BEFORE touching the combo, and block the combo signal so
        # setCurrentIndex can't re-enter _on_profile_selected with the real load.
        monkeypatch.setattr(fd.FilterProfile, "load", classmethod(lambda cls, path: prof))
        p = tmp_path / "p.json"
        dlg._profile_combo.blockSignals(True)
        dlg._profile_combo.addItem("P", p)
        dlg._profile_combo.setCurrentIndex(dlg._profile_combo.count() - 1)
        dlg._profile_combo.blockSignals(False)
        dlg._on_profile_selected(dlg._profile_combo.currentIndex())
        assert dlg._name_filter.text() == "loaded"
        assert dlg._del_btn.isEnabled() is True

    def test_delete_profile(self, dlg, monkeypatch, tmp_path):
        p = tmp_path / "del.json"
        p.write_text("{}")
        dlg._profile_combo.blockSignals(True)
        dlg._profile_combo.addItem("Del", p)
        dlg._profile_combo.setCurrentIndex(dlg._profile_combo.count() - 1)
        dlg._profile_combo.blockSignals(False)
        monkeypatch.setattr(fd.QMessageBox, "question",
                            lambda *a, **k: fd.QMessageBox.StandardButton.Yes)
        dlg._delete_profile()
        assert not p.exists()

    def test_delete_profile_emits_profile_deleted_signal(self, dlg, monkeypatch, tmp_path, qtbot):
        # issue #491: deleting a profile must report the name immediately,
        # regardless of whether the dialog is later applied or just closed.
        p = tmp_path / "del.json"
        p.write_text("{}")
        dlg._profile_combo.blockSignals(True)
        dlg._profile_combo.addItem("Del", p)
        dlg._profile_combo.setCurrentIndex(dlg._profile_combo.count() - 1)
        dlg._profile_combo.blockSignals(False)
        monkeypatch.setattr(fd.QMessageBox, "question",
                            lambda *a, **k: fd.QMessageBox.StandardButton.Yes)
        with qtbot.waitSignal(dlg.profile_deleted, timeout=1000) as blocker:
            dlg._delete_profile()
        assert blocker.args == ["Del"]


# ── default button / Enter key (#370) ──────────────────────────────────────────

class TestDefaultButton:
    def test_apply_is_default_and_save_is_not(self, dlg):
        from PySide6.QtWidgets import QPushButton
        buttons = dlg.findChildren(QPushButton)
        default_buttons = [b for b in buttons if b.isDefault()]
        # exactly one default button, and it is not the narrow save button (maxWidth 110)
        assert len(default_buttons) == 1
        assert default_buttons[0].maximumWidth() != 110

    def test_save_btn_not_autodefault(self, dlg):
        from PySide6.QtWidgets import QPushButton
        # the save button is the only one with maxWidth 110
        buttons = dlg.findChildren(QPushButton)
        save_btn = next(b for b in buttons if b.maximumWidth() == 110)
        assert not save_btn.autoDefault()


# ── apply ───────────────────────────────────────────────────────────────────────

class TestApply:
    def test_apply_emits(self, dlg, db_session):
        captured = []
        dlg.filter_applied.connect(lambda fs, sort, name: captured.append((fs, name)))
        dlg._name_filter.setText("hello")
        dlg._apply()
        assert captured and captured[0][1] == ""

    def test_apply_blocks_on_bad_where(self, dlg, db_session):
        captured = []
        dlg.filter_applied.connect(lambda *a: captured.append(a))
        dlg._where_sql_general.setPlainText("no_such_column = 1")
        dlg._apply()
        assert captured == []                       # not emitted
        assert dlg._where_error_label.toPlainText() != ""
        assert not dlg._where_error_label.isHidden()


# ── distance unit preference (#327) ─────────────────────────────────────────────

class TestDistanceUnitPref:
    @pytest.fixture
    def dlg_mi(self, qtbot, monkeypatch):
        from opensak.utils.types import CoordFormat
        monkeypatch.setattr(
            "opensak.gui.settings.get_settings",
            lambda: SimpleNamespace(home_lat=55.0, home_lon=12.0, use_miles=True,
                                     coord_format=CoordFormat.DD, home_points=[]),
        )
        d = FilterDialog()
        qtbot.addWidget(d)
        return d

    def test_suffix_km_by_default(self, dlg):
        assert dlg._dist_max.suffix() == " km"

    def test_suffix_mi_when_use_miles(self, dlg_mi):
        assert dlg_mi._dist_max.suffix() == " mi"

    def test_build_converts_mi_to_km(self, dlg_mi):
        dlg_mi._dist_enabled.setChecked(True)
        dlg_mi._dist_max.setValue(50.0)
        fs = dlg_mi._build_filterset()
        f = next(x for x in fs._filters if getattr(x, "filter_type", None) == "distance")
        assert abs(f.max_km - 50.0 * 1.60934) < 0.01

    def test_build_km_passthrough(self, dlg):
        dlg._dist_enabled.setChecked(True)
        dlg._dist_max.setValue(50.0)
        fs = dlg._build_filterset()
        f = next(x for x in fs._filters if getattr(x, "filter_type", None) == "distance")
        assert abs(f.max_km - 50.0) < 0.01

    def test_load_converts_km_to_mi(self, dlg_mi):
        fs = FilterSet(mode="AND")
        fs.add(DistanceFilter(55.0, 12.0, 80.0))
        dlg_mi._load_filterset(fs)
        assert abs(dlg_mi._dist_max.value() - 80.0 * 0.621371) < 0.01

    def test_load_km_passthrough(self, dlg):
        fs = FilterSet(mode="AND")
        fs.add(DistanceFilter(55.0, 12.0, 25.0))
        dlg._load_filterset(fs)
        assert abs(dlg._dist_max.value() - 25.0) < 0.01

    def test_roundtrip_mi(self, dlg_mi):
        # Enter 50 mi → build → DistanceFilter stores km → load back → should show 50 mi.
        dlg_mi._dist_enabled.setChecked(True)
        dlg_mi._dist_max.setValue(50.0)
        fs = dlg_mi._build_filterset()
        dlg_mi._load_filterset(fs)
        assert abs(dlg_mi._dist_max.value() - 50.0) < 0.1


# ── center point picker integration (#511) ───────────────────────────────────

class TestCenterPointIntegration:
    def test_defaults_to_home(self, dlg):
        dlg._dist_enabled.setChecked(True)
        fs = dlg._build_filterset()
        f = next(x for x in fs._filters if getattr(x, "filter_type", None) == "distance")
        assert (f.lat, f.lon) == (55.0, 12.0)
        assert f.center_state == {"kind": "home"}

    def test_selected_cache_as_center(self, qtbot):
        cache = SimpleNamespace(gc_code="GC1AB23", name="Troll Bridge",
                                 latitude=56.1, longitude=10.2)
        d = FilterDialog(current_cache=cache)
        qtbot.addWidget(d)
        d._dist_enabled.setChecked(True)
        d._center_picker.set_state({"kind": "cache"})
        fs = d._build_filterset()
        f = next(x for x in fs._filters if getattr(x, "filter_type", None) == "distance")
        assert (f.lat, f.lon) == (56.1, 10.2)
        assert f.center_state == {"kind": "cache"}

    def test_custom_coordinate_as_center(self, dlg):
        dlg._dist_enabled.setChecked(True)
        dlg._center_picker.set_state({"kind": "custom", "text": "56.5, 10.1"})
        fs = dlg._build_filterset()
        f = next(x for x in fs._filters if getattr(x, "filter_type", None) == "distance")
        assert (f.lat, f.lon) == (56.5, 10.1)

    def test_invalid_center_skips_distance_filter_with_warning(self, dlg, monkeypatch):
        warned = []
        monkeypatch.setattr(fd.QMessageBox, "warning",
                            staticmethod(lambda *a, **kw: warned.append(a)))
        dlg._dist_enabled.setChecked(True)
        dlg._center_picker.set_state({"kind": "custom", "text": "not a coordinate"})
        fs = dlg._build_filterset()
        assert "distance" not in _types(fs)
        assert warned

    def test_min_distance_included(self, dlg):
        dlg._dist_enabled.setChecked(True)
        dlg._dist_min.setValue(2.0)
        dlg._dist_max.setValue(50.0)
        fs = dlg._build_filterset()
        f = next(x for x in fs._filters if getattr(x, "filter_type", None) == "distance")
        assert abs(f.min_km - 2.0) < 0.01

    def test_load_restores_center_state(self, dlg):
        fs = FilterSet(mode="AND")
        fs.add(DistanceFilter(60.0, 10.0, 30.0, center_state={"kind": "custom", "text": "60.0, 10.0"}))
        dlg._load_filterset(fs)
        assert dlg._center_picker.to_state() == {"kind": "custom", "text": "60.0, 10.0"}

    def test_load_legacy_filter_without_center_state(self, dlg):
        # Pre-#511 saved profile: no center_state at all. Should surface the
        # stored lat/lon as an editable custom point rather than silently
        # assuming Home.
        fs = FilterSet(mode="AND")
        fs.add(DistanceFilter(60.0, 10.0, 30.0))
        dlg._load_filterset(fs)
        assert dlg._center_picker.get_center() == (60.0, 10.0)

    def test_reset_returns_center_to_home(self, dlg):
        dlg._center_picker.set_state({"kind": "custom", "text": "60.0, 10.0"})
        dlg._reset_general()
        assert dlg._center_picker.to_state() == {"kind": "home"}

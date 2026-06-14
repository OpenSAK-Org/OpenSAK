"""tests/unit-tests/test_filter_dialog.py — complete filter dialog (build/load/profiles)."""

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
    PremiumFilter, NonPremiumFilter, HasTrackableFilter, HasCorrectedFilter,
    CountryFilter, StateFilter, CountyFilter, UserFlagFilter, DnfFilter,
    FtfFilter, FavoritePointsFilter, AttributeFilter, WhereClauseFilter,
    FoundByMeDateFilter, DnfDateFilter, LastLogDateFilter, FilterProfile,
)


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    # No real profiles on disk; deterministic home for DistanceFilter.
    monkeypatch.setattr(fd.FilterProfile, "list_profiles", staticmethod(lambda: []))
    monkeypatch.setattr("opensak.gui.settings.get_settings",
                        lambda: SimpleNamespace(home_lat=55.0, home_lon=12.0))


@pytest.fixture
def dlg(qtbot):
    d = FilterDialog()
    qtbot.addWidget(d)
    return d


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
    def test_five_tabs(self, dlg):
        assert dlg._tabs.count() == 5

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

    def test_premium_and_trackable_and_corrected(self, dlg):
        dlg._prem_no.setChecked(False)
        dlg._tb_no.setChecked(False)
        dlg._cc_no.setChecked(False)
        types = _types(dlg._build_filterset())
        assert "premium" in types and "has_trackable" in types and "has_corrected" in types
        dlg._prem_yes.setChecked(False)
        dlg._prem_no.setChecked(True)
        assert "non_premium" in _types(dlg._build_filterset())

    def test_misc_filters(self, dlg):
        dlg._country_filter.setText("DK")
        dlg._state_filter.setText("Z")
        dlg._county_filter.setText("C")
        dlg._flag_no.setChecked(False)   # flag yes only
        dlg._dnf_no.setChecked(False)
        dlg._ftf_no.setChecked(False)
        dlg._fav_enabled.setChecked(True)
        types = _types(dlg._build_filterset())
        assert {"country", "state", "county", "user_flag", "dnf", "ftf",
                "favorite_points"} <= set(types)

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

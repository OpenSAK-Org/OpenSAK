# tests/unit-tests/test_filter_dialog.py — complete filter dialog (build/load/profiles).

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QInputDialog
from PySide6.QtCore import QDate, QTime

from opensak.gui.dialogs import filter_dialog as fd
from opensak.gui.dialogs.filter_dialog import FilterDialog, TriStateBox, DTSpinBox
from opensak.utils.constants import LOG_TYPES
from opensak.filters.engine import (
    FilterSet, NameFilter, GcCodeFilter, PlacedByFilter, OwnerFilter,
    CacheTypeFilter, ContainerFilter, DifficultyFilter, TerrainFilter,
    FoundFilter, NotFoundFilter, AvailabilityFilter, DistanceFilter,
    PremiumFilter, NonPremiumFilter, HasTrackableFilter, HasCorrectedFilter, NoCorrectedFilter,
    CountryFilter, StateFilter, CountyFilter, UserFlagFilter, LockedFilter, DnfFilter,
    FtfFilter, FavoritePointsFilter, AttributeFilter, WhereClauseFilter,
    FoundByMeDateFilter, DnfDateFilter, LastLogDateFilter, HiddenDateFilter,
    DateFilter, DATE_FILTER_FIELDS, DATETIME_FILTER_FIELDS,
    TextSearchFilter, WaypointFilter,
    LogFilter, LOG_SCOPE_CHOICES, LOG_TYPE_OTHER,
    FilterProfile,
)


def _date_filters(fs) -> dict:
    return {f.field: f for f in fs._filters if isinstance(f, DateFilter)}


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    # No real profiles on disk; deterministic home for DistanceFilter.
    monkeypatch.setattr(fd.FilterProfile, "list_profiles", staticmethod(lambda: []))
    from opensak.utils.types import DateFormat, CoordFormat
    monkeypatch.setattr("opensak.gui.settings.get_settings",
                        lambda: SimpleNamespace(home_lat=55.0, home_lon=12.0, use_miles=False,
                                               date_format=DateFormat.YMD,
                                               coord_format=CoordFormat.DD, home_points=[],
                                               theme="light"))


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
    def test_nine_tabs(self, dlg):
        assert dlg._tabs.count() == 9

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
        # Issue #576: Available/Unavailable/Archived are all checked by
        # default (GSAK-style — show everything unless told otherwise), so
        # an untouched dialog already produces no availability filter.
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

    def test_default_availability_state_adds_no_filter(self, dlg):
        # Issue #576: with Available/Unavailable/Archived all checked by
        # default, setting only a distance filter must not silently pull in
        # an AvailabilityFilter too — "2 active" was the old bug.
        dlg._dist_enabled.setChecked(True)
        fs = dlg._build_filterset()
        assert set(_types(fs)) == {"distance"}
        assert len(fs) == 1
        assert fs.active_count() == 1

    def test_default_dialog_state_is_fully_empty(self, dlg):
        # Opening the dialog and applying with no changes at all should
        # produce a completely empty filterset (archived caches included —
        # see test_empty_is_empty).
        fs = dlg._build_filterset()
        assert _types(fs) == []
        assert fs.active_count() == 0

    def test_explicit_availability_change_still_counts(self, dlg):
        # A deliberate availability change (e.g. hiding archived caches) is
        # a real, user-chosen filter and must count toward the badge.
        dlg._archived_cb.setChecked(False)
        fs = dlg._build_filterset()
        assert "availability" in _types(fs)
        assert fs.active_count() == 1

    def test_unchecking_archived_hides_archived(self, dlg):
        # Issue #576 (Mike): unchecking Archived must produce a real
        # AvailabilityFilter with show_archived=False.
        dlg._archived_cb.setChecked(False)
        fs = dlg._build_filterset()
        avail_filters = [f for f in fs._filters if getattr(f, "filter_type", None) == "availability"]
        assert len(avail_filters) == 1
        assert avail_filters[0].show_avail is True
        assert avail_filters[0].show_unavail is True
        assert avail_filters[0].show_archived is False

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

    def test_date_rows_default_to_any(self, dlg):
        assert set(dlg._date_rows) == set(DATE_FILTER_FIELDS)
        assert all(row.op() == "any" for row in dlg._date_rows.values())
        assert _date_filters(dlg._build_filterset()) == {}

    def test_date_filters(self, dlg):
        for row in dlg._date_rows.values():
            row._select(row.op_combo, "on_or_after")
        assert set(_date_filters(dlg._build_filterset())) == set(DATE_FILTER_FIELDS)

    def test_single_day_equal_covers_whole_day(self, dlg):
        # #844: a single date must match the whole day, whatever the time.
        row = dlg._date_rows["found_date"]
        row._select(row.op_combo, "equal")
        row.date1.setDate(QDate(2026, 9, 2))
        f = _date_filters(dlg._build_filterset())["found_date"]
        assert (f.op, f.date1) == ("equal", date(2026, 9, 2))

        class _Cache:
            found = True
            found_date = datetime(2026, 9, 2, 14, 30, 0)
        assert f.matches(_Cache()) is True

    def test_time_checkbox_only_on_datetime_fields(self, dlg):
        for field, row in dlg._date_rows.items():
            row._select(row.op_combo, "equal")
            assert (not row.time_check.isHidden()) is (field in DATETIME_FILTER_FIELDS), field
            row._select(row.op_combo, "during")
            assert row.time_check.isHidden(), field

    def test_time_bounds_build_load_and_reset(self, dlg):
        row = dlg._date_rows["creation_date"]
        row._select(row.op_combo, "between")
        row.date1.setDate(QDate(2026, 9, 2))
        row.date2.setDate(QDate(2026, 9, 3))
        assert _date_filters(dlg._build_filterset())["creation_date"].date1 == date(2026, 9, 2)

        date_width = row.date1.sizeHint().width()
        row.time_check.setChecked(True)
        assert "mm" in row.date1.displayFormat()
        text_width = row.date1.fontMetrics().horizontalAdvance(row.date1.text())
        assert row.date1.minimumWidth() > date_width
        assert row.date1.minimumWidth() > text_width
        row.date1.setTime(QTime(8, 15))
        f = _date_filters(dlg._build_filterset())["creation_date"]
        assert (f.date1, f.date2) == (datetime(2026, 9, 2, 8, 15), datetime(2026, 9, 3, 23, 59))

        dlg._reset_dates()
        assert not row.time_check.isChecked() and "mm" not in row.date1.displayFormat()
        assert row.date1.minimumWidth() == date_width
        row.load(f)
        assert row.time_check.isChecked()
        assert _date_filters(dlg._build_filterset())["creation_date"].to_dict() == f.to_dict()

    def test_between_relative_and_compare_rows(self, dlg):
        hidden = dlg._date_rows["hidden_date"]
        hidden._select(hidden.op_combo, "between")
        hidden.date1.setDate(QDate(2020, 1, 1))
        hidden.date2.setDate(QDate(2020, 12, 31))
        last_found = dlg._date_rows["last_found_date"]
        last_found._select(last_found.op_combo, "not_during")
        last_found.amount.setValue(2)
        last_found._select(last_found.unit_combo, "years")
        log = dlg._date_rows["last_log_date"]
        log._select(log.op_combo, "compare")
        log._select(log.other_combo, "hidden_date")
        log._select(log.compare_combo, "within")
        log.compare_days.setValue(7)

        by_field = _date_filters(dlg._build_filterset())
        assert set(by_field) == {"hidden_date", "last_found_date", "last_log_date"}
        h = by_field["hidden_date"]
        assert (h.op, h.date1, h.date2) == ("between", date(2020, 1, 1), date(2020, 12, 31))
        lf = by_field["last_found_date"]
        assert (lf.op, lf.amount, lf.unit) == ("not_during", 2, "years")
        lg = by_field["last_log_date"]
        assert (lg.op, lg.other_field, lg.compare_op, lg.compare_days) == \
            ("compare", "hidden_date", "within", 7)

    def test_date_row_inputs_follow_operator(self, dlg):
        row = dlg._date_rows["dnf_date"]

        def shown():
            return tuple(not w.isHidden() for w in (row.date1, row.date2, row._relative, row._compare))

        assert shown() == (False, False, False, False)
        assert not row.label.font().bold()
        for op, expected in [
            ("on_or_before", (True, False, False, False)),
            ("equal",        (True, False, False, False)),
            ("between",      (True, True, False, False)),
            ("during",       (False, False, True, False)),
            ("not_during",   (False, False, True, False)),
            ("compare",      (False, False, False, True)),
        ]:
            row._select(row.op_combo, op)
            assert shown() == expected, op
        assert row.label.font().bold()
        assert row.compare_days.isHidden()
        row._select(row.compare_combo, "outside")
        assert not row.compare_days.isHidden()

    def test_compare_offers_every_other_field(self, dlg):
        row = dlg._date_rows["hidden_date"]
        others = [row.other_combo.itemData(i) for i in range(row.other_combo.count())]
        assert set(others) == set(DATE_FILTER_FIELDS) - {"hidden_date"}

    def test_reset_dates(self, dlg):
        row = dlg._date_rows["changed_date"]
        row._select(row.op_combo, "during")
        row.amount.setValue(5)
        dlg._reset_dates()
        assert row.op() == "any"
        assert row.amount.value() == 1
        assert not row.label.font().bold()

    def test_attributes_and_mode(self, dlg):
        attr_id = next(iter(dlg._attr_boxes))
        ja, nej, ingen = dlg._attr_boxes[attr_id]
        ja.setChecked(True)
        fs = dlg._build_filterset()
        assert any(getattr(f, "filter_type", None) == "attribute" for f in fs._filters)

    def test_attributes_or_mode(self, dlg):
        dlg._attr_mode_any.setChecked(True)  # ANY/OR mode
        assert dlg._attr_mode_all.isChecked() is False  # radios are exclusive
        ids = list(dlg._attr_boxes)[:2]
        for aid in ids:
            dlg._attr_boxes[aid][0].setChecked(True)
        fs = dlg._build_filterset()
        # nested OR FilterSet present
        assert any(isinstance(f, FilterSet) and f.mode == "OR" for f in fs._filters)

    def test_attributes_or_mode_matches_cache_with_only_one(self, dlg):
        # ONE-of mode: a cache carrying just one of the selected attributes
        # passes; in ALL mode the same cache is rejected.
        a1, a2 = list(dlg._attr_boxes)[:2]
        dlg._attr_boxes[a1][0].setChecked(True)
        dlg._attr_boxes[a2][0].setChecked(True)
        cache = SimpleNamespace(attributes=[
            SimpleNamespace(attribute_id=a1, is_on=True),
        ])
        assert dlg._build_filterset().matches(cache) is False
        dlg._attr_mode_any.setChecked(True)
        assert dlg._build_filterset().matches(cache) is True

    def test_reset_attributes_restores_all_mode(self, dlg):
        dlg._attr_mode_any.setChecked(True)
        dlg._reset_attributes()
        assert dlg._attr_mode_all.isChecked() is True
        assert dlg._attr_mode_any.isChecked() is False

    @staticmethod
    def _visible_attr_ids(dlg):
        return {a for a, (row, _i, _h) in dlg._attr_rows.items()
                if not dlg._attr_table.isRowHidden(row)}

    def test_attr_search_matches_english_name_case_insensitive(self, dlg):
        dlg._attr_search.setText("WHEELCHAIR")
        assert self._visible_attr_ids(dlg) == {24}

    def test_attr_search_all_terms_must_match(self, dlg):
        dlg._attr_search.setText("hike long")
        assert self._visible_attr_ids(dlg) == {57}

    def test_attr_search_by_id(self, dlg):
        dlg._attr_search.setText("41")
        assert 41 in self._visible_attr_ids(dlg)

    def test_attr_search_ignores_accents(self, dlg):
        from opensak.gui.dialogs.filter_dialog import _fold
        assert _fold("Élévation Ärger") == "elevation arger"

    def test_attr_search_empty_shows_all(self, dlg):
        dlg._attr_search.setText("xyz-no-such-attribute")
        assert self._visible_attr_ids(dlg) == set()
        dlg._attr_search.clear()
        assert self._visible_attr_ids(dlg) == set(dlg._attr_rows)

    def test_attr_only_selected_and_marking(self, dlg):
        dlg._attr_boxes[24][0].setChecked(True)   # Yes
        dlg._attr_boxes[19][1].setChecked(True)   # No
        dlg._attr_only_selected.setChecked(True)
        assert self._visible_attr_ids(dlg) == {24, 19}
        assert dlg._attr_rows[24][1].font().bold() is True
        assert dlg._attr_rows[1][1].font().bold() is False
        dlg._attr_boxes[24][2].setChecked(True)   # back to "none"
        assert dlg._attr_rows[24][1].font().bold() is False

    def test_attr_status_counts(self, dlg):
        total = len(dlg._attr_rows)
        dlg._attr_boxes[24][0].setChecked(True)
        dlg._attr_search.setText("wheelchair")
        assert dlg._attr_status.text() == fd.tr(
            "filter_attr_status", shown=1, total=total, selected=1)

    def test_attr_search_enter_does_not_accept_dialog(self, dlg, qtbot):
        from PySide6.QtCore import Qt
        dlg.show()
        dlg._attr_search.setText("wheelchair")
        qtbot.keyClick(dlg._attr_search, Qt.Key.Key_Return)
        assert dlg.isVisible()

    def test_reset_attributes_clears_search(self, dlg):
        dlg._attr_search.setText("wheelchair")
        dlg._attr_only_selected.setChecked(True)
        dlg._reset_attributes()
        assert dlg._attr_search.text() == ""
        assert dlg._attr_only_selected.isChecked() is False
        assert self._visible_attr_ids(dlg) == set(dlg._attr_rows)

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

    def test_loads_date_filters_round_trip(self, dlg):
        filters = [
            DateFilter("creation_date", "compare", other_field="last_gpx_update",
                       compare_op="older_or_equal"),
            DateFilter("changed_date", "between", date1=date(2020, 1, 1), date2=date(2021, 6, 30)),
            DateFilter("dnf_date", "during", amount=3, unit="months"),
            DateFilter("last_found_date", "compare", other_field="found_date",
                       compare_op="outside", compare_days=30),
        ]
        fs = FilterSet(mode="AND")
        for f in filters:
            fs.add(f)
        dlg._load_filterset(fs)
        rebuilt = {k: f.to_dict() for k, f in _date_filters(dlg._build_filterset()).items()}
        assert rebuilt == {f.field: f.to_dict() for f in filters}

    def test_loads_legacy_date_filters(self, dlg):
        # Profiles saved before the GSAK-style date filter hold from/to
        # range filters; they show as the equivalent operator.
        fs = FilterSet(mode="AND")
        fs.add(FoundByMeDateFilter(from_date=datetime(2020, 1, 1),
                                   to_date=datetime(2021, 1, 1)))
        fs.add(DnfDateFilter(from_date=datetime(2020, 2, 2), to_date=None))
        fs.add(LastLogDateFilter(from_date=None, to_date=datetime(2022, 3, 3)))
        dlg._load_filterset(fs)
        found = dlg._date_rows["found_date"]
        assert found.op() == "between"
        assert (found.date1.date(), found.date2.date()) == (QDate(2020, 1, 1), QDate(2021, 1, 1))
        dnf = dlg._date_rows["dnf_date"]
        assert (dnf.op(), dnf.date1.date()) == ("on_or_after", QDate(2020, 2, 2))
        log = dlg._date_rows["last_log_date"]
        assert (log.op(), log.date1.date()) == ("on_or_before", QDate(2022, 3, 3))
        assert set(_date_filters(dlg._build_filterset())) == {"found_date", "dnf_date", "last_log_date"}

    def test_loads_hidden_date_filter(self, dlg):
        # #857: reopening the Filter dialog after setting a Hidden date
        # range didn't restore it, even though the list was correctly
        # filtered. A legacy hidden_date_range must still restore.
        fs = FilterSet(mode="AND")
        fs.add(HiddenDateFilter(from_date=datetime(2020, 5, 1),
                                 to_date=datetime(2020, 6, 15)))
        dlg._load_filterset(fs)
        row = dlg._date_rows["hidden_date"]
        assert row.op() == "between"
        assert row.date1.date() == QDate(2020, 5, 1)
        assert row.date2.date() == QDate(2020, 6, 15)

    def test_legacy_range_without_dates_leaves_row_any(self, dlg):
        dlg._load_filterset(FilterSet().add(FoundByMeDateFilter()))
        assert dlg._date_rows["found_date"].op() == "any"

    def test_hidden_date_filter_round_trips_via_to_dict(self):
        # #857 (root cause, part 2): the old inline HiddenDateFilter's
        # to_dict() only returned {"filter_type": ...}, silently dropping
        # from_date/to_date — so a *saved* filter profile would lose the
        # dates entirely on reload, independent of the dialog bug above.
        f = HiddenDateFilter(from_date=datetime(2020, 5, 1),
                              to_date=datetime(2020, 6, 15, 23, 59, 59))
        restored = HiddenDateFilter.from_dict(f.to_dict())
        assert restored.from_date == f.from_date
        assert restored.to_date == f.to_date

    def test_hidden_date_filter_matches(self):
        f = HiddenDateFilter(from_date=datetime(2020, 5, 1),
                              to_date=datetime(2020, 6, 15, 23, 59, 59))

        class _Cache:
            hidden_date = datetime(2020, 5, 20)
        assert f.matches(_Cache()) is True

        class _CacheOutside:
            hidden_date = datetime(2020, 7, 1)
        assert f.matches(_CacheOutside()) is False

        class _CacheNoDate:
            hidden_date = None
        assert f.matches(_CacheNoDate()) is False

    def test_loads_attribute_or_group_sets_any_mode(self, dlg):
        attr_id = next(iter(dlg._attr_boxes))
        inner = FilterSet(mode="OR")
        inner.add(AttributeFilter(attr_id, True))
        fs = FilterSet(mode="AND")
        fs.add(inner)
        dlg._load_filterset(fs)
        assert dlg._attr_mode_any.isChecked() is True
        assert dlg._attr_mode_all.isChecked() is False
        assert dlg._attr_boxes[attr_id][0].isChecked() is True

    def test_loads_plain_attribute_sets_all_mode(self, dlg):
        # A previously loaded ONE-of profile must not leak into the next load.
        dlg._attr_mode_any.setChecked(True)
        attr_id = next(iter(dlg._attr_boxes))
        fs = FilterSet(mode="AND")
        fs.add(AttributeFilter(attr_id, True))
        dlg._load_filterset(fs)
        assert dlg._attr_mode_all.isChecked() is True
        assert dlg._attr_mode_any.isChecked() is False

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

    def test_explicit_show_archived_survives_reopen(self, dlg, qtbot):
        # Regression for issue #576 (Mike): checking "show archived" used to
        # be forgotten on reopen, because the all-three-checked state added
        # no AvailabilityFilter to persist in the first place. Build once
        # from a dialog with Archived unchecked (deliberately hiding them)
        # and confirm a freshly reopened dialog restores it exactly.
        dlg._archived_cb.setChecked(False)
        fs_hide = dlg._build_filterset()
        reopened = FilterDialog()
        qtbot.addWidget(reopened)
        reopened._load_filterset(fs_hide)
        assert reopened._archived_cb.isChecked() is False
        assert reopened._avail_cb.isChecked() is True
        assert reopened._unavail_cb.isChecked() is True

    def test_default_dialog_state_survives_reopen(self, qtbot):
        # A brand-new, untouched dialog (Archived checked by default) must
        # still show Archived checked after a build -> load round trip.
        fresh = FilterDialog()
        qtbot.addWidget(fresh)
        fs_default = fresh._build_filterset()
        reopened = FilterDialog()
        qtbot.addWidget(reopened)
        reopened._load_filterset(fs_default)
        assert reopened._archived_cb.isChecked() is True


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

    def test_reset_general_clears_owner_name(self, dlg):
        # "Reset tab" on General cleared name, GC code and placed by, but
        # left the owner name filter in place.
        dlg._owner_filter.setText("me")
        dlg._tabs.setCurrentWidget(dlg._general_tab)
        dlg._reset_current_tab()
        assert dlg._owner_filter.text() == ""

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


# ── waypoints tab ─────────────────────────────────────────────────────────────

def _waypoint_filters(fs) -> list:
    return [f for f in fs._filters if isinstance(f, WaypointFilter)]


class TestWaypointsTab:
    def test_default_builds_nothing(self, dlg):
        assert _waypoint_filters(dlg._build_filterset()) == []
        assert not dlg._wp_count1.isVisibleTo(dlg)

    def test_date_row_has_no_compare(self, dlg):
        row = dlg._wp_date_row
        ops = [row.op_combo.itemData(i) for i in range(row.op_combo.count())]
        assert "compare" not in ops and "between" in ops

    def test_build_all_criteria(self, dlg):
        dlg._wp_text_rows["code"].set_op("starts_with")
        dlg._wp_text_rows["code"].edit.setText("PK")
        dlg._wp_text_rows["comment"].set_op("empty")
        dlg._wp_date_row._select(dlg._wp_date_row.op_combo, "between")
        dlg._wp_date_row.date1.setDate(QDate(2025, 1, 1))
        dlg._wp_date_row.date2.setDate(QDate(2025, 6, 30))
        dlg._wp_by_user_no.setChecked(False)
        dlg._wp_count_op.setCurrentIndex(dlg._wp_count_op.findData("between"))
        dlg._wp_count1.setValue(1)
        dlg._wp_count2.setValue(3)
        [f] = _waypoint_filters(dlg._build_filterset())
        assert {k: (m.text, m.op) for k, m in f.texts.items()} == {
            "code": ("PK", "starts_with"), "comment": ("", "empty")}
        assert (f.date_op, f.date1, f.date2) == ("between", date(2025, 1, 1), date(2025, 6, 30))
        assert f.by_user is True
        assert (f.count_op, f.count1, f.count2) == ("between", 1, 3)

    def test_count_alone_builds_filter(self, dlg):
        dlg._wp_count_op.setCurrentIndex(dlg._wp_count_op.findData("equal"))
        [f] = _waypoint_filters(dlg._build_filterset())
        assert (f.count_op, f.count1) == ("equal", 0)
        assert not f.texts and f.date_op is None and f.by_user is None

    def test_load_roundtrip_and_reset(self, dlg):
        original = WaypointFilter(
            texts={"wp_type": ("Parking", "contains"), "name": ("final", "not_contains")},
            date_op="during", date_amount=2, date_unit="years",
            by_user=False, count_op="at_most", count1=4,
        )
        dlg._load_filterset(FilterSet().add(original))
        [f] = _waypoint_filters(dlg._build_filterset())
        assert f.to_dict() == original.to_dict()
        dlg._tabs.setCurrentWidget(dlg._waypoints_tab)
        dlg._reset_current_tab()
        assert _waypoint_filters(dlg._build_filterset()) == []
        assert dlg._wp_by_user_yes.isChecked() and dlg._wp_by_user_no.isChecked()

    def test_invalid_regex_blocks_apply(self, dlg, monkeypatch):
        warned = MagicMock()
        monkeypatch.setattr(fd.QMessageBox, "warning", warned)
        dlg._wp_text_rows["name"].set_op("regex")
        dlg._wp_text_rows["name"].edit.setText("(")
        assert dlg._validate_text_filters() is False
        warned.assert_called_once()
        assert dlg._tabs.currentWidget() is dlg._waypoints_tab


# ── logs tab ──────────────────────────────────────────────────────────────────

def _log_filters(fs) -> list:
    return [f for f in fs._filters if isinstance(f, LogFilter)]


class TestLogsTab:
    def test_default_builds_nothing(self, dlg):
        assert _log_filters(dlg._build_filterset()) == []
        assert not dlg._log_count1.isVisibleTo(dlg)
        # "All log types" starts ticked, so the type list is inert
        assert not dlg._log_types_box.isEnabled()

    def test_date_row_has_no_compare(self, dlg):
        row = dlg._log_date_row
        ops = [row.op_combo.itemData(i) for i in range(row.op_combo.count())]
        assert "compare" not in ops and "during" in ops

    def test_scope_offers_gsak_choices(self, dlg):
        data = [dlg._log_scope.itemData(i) for i in range(dlg._log_scope.count())]
        assert data == list(LOG_SCOPE_CHOICES)
        assert data[0] == 0  # "All logs"

    def test_unticking_all_enables_the_type_list(self, dlg):
        dlg._log_types_all.setChecked(False)
        assert dlg._log_types_box.isEnabled()
        # but no type ticked still means "every type"
        assert _log_filters(dlg._build_filterset()) == []

    def test_type_list_covers_every_log_type_plus_other(self, dlg):
        assert list(dlg._log_type_checks) == list(LOG_TYPES) + [LOG_TYPE_OTHER]

    def test_build_all_criteria(self, dlg):
        dlg._log_date_row._select(dlg._log_date_row.op_combo, "during")
        dlg._log_date_row.amount.setValue(6)
        dlg._log_date_row._select(dlg._log_date_row.unit_combo, "months")
        dlg._log_scope.setCurrentIndex(dlg._log_scope.findData(5))
        dlg._log_categories["other"].setChecked(False)
        dlg._log_types_all.setChecked(False)
        dlg._log_type_checks["Found it"].setChecked(True)
        dlg._log_finder_enabled.setChecked(True)
        dlg._log_finder_row.set_op("equals")
        dlg._log_finder_row.edit.setText("alice")
        dlg._log_count_op.setCurrentIndex(dlg._log_count_op.findData("at_least"))
        dlg._log_count1.setValue(2)
        dlg._log_exclude.setCurrentIndex(1)
        [f] = _log_filters(dlg._build_filterset())
        assert (f.date_op, f.date_amount, f.date_unit) == ("during", 6, "months")
        assert f.last_n == 5
        assert f.categories == ["found", "not_found"]
        assert f.types == ["Found it"]
        assert (f.finder_text, f.finder_op, f.finder_by_id) == ("alice", "equals", False)
        assert (f.count_op, f.count1) == ("at_least", 2)
        assert f.exclude is True

    def test_enabling_finder_prefills_the_users_own_name(self, qtbot, monkeypatch):
        # An empty "Logged by" matches a log by anyone, which is rarely what
        # ticking the box means — so it starts on the user's own name.
        monkeypatch.setattr(fd, "get_settings",
                            lambda: SimpleNamespace(gc_username="Nagi", text_size=None))
        d = FilterDialog.__new__(FilterDialog)
        d._log_finder_enabled = fd.QCheckBox()
        d._log_finder_row = fd.TextFilterRow("", "")
        d._log_finder_by_id = fd.QCheckBox()
        qtbot.addWidget(d._log_finder_row)
        d._log_finder_enabled.setChecked(True)
        d._update_log_finder_inputs()
        # Exactly, not "contains" — a caching name is an identity, so a
        # substring match would also catch every longer name embedding it.
        assert d._log_finder_row.edit.text() == "Nagi"
        assert d._log_finder_row.op() == "equals"

    def test_enabling_finder_keeps_text_the_user_typed(self, dlg):
        dlg._log_finder_row.set_op("contains")
        dlg._log_finder_row.edit.setText("someone else")
        dlg._log_finder_enabled.setChecked(True)
        assert dlg._log_finder_row.edit.text() == "someone else"
        assert dlg._log_finder_row.op() == "contains"

    def test_enabling_finder_without_a_username_configured(self, qtbot, monkeypatch):
        # Nothing to prefill, so the operator is left alone too.
        monkeypatch.setattr(fd, "get_settings",
                            lambda: SimpleNamespace(gc_username="", text_size=None))
        d = FilterDialog.__new__(FilterDialog)
        d._log_finder_enabled = fd.QCheckBox()
        d._log_finder_row = fd.TextFilterRow("", "")
        d._log_finder_by_id = fd.QCheckBox()
        qtbot.addWidget(d._log_finder_row)
        d._log_finder_enabled.setChecked(True)
        d._update_log_finder_inputs()
        assert d._log_finder_row.edit.text() == ""
        assert d._log_finder_row.op() == "contains"

    def test_finder_row_is_ignored_until_enabled(self, dlg):
        dlg._log_finder_row.edit.setText("alice")
        assert _log_filters(dlg._build_filterset()) == []
        dlg._log_finder_enabled.setChecked(True)
        [f] = _log_filters(dlg._build_filterset())
        assert f.finder_text == "alice"

    def test_finder_by_id(self, dlg):
        dlg._log_finder_enabled.setChecked(True)
        dlg._log_finder_row.edit.setText("U-1")
        dlg._log_finder_by_id.setChecked(True)
        [f] = _log_filters(dlg._build_filterset())
        assert f.finder_by_id is True

    def test_scope_alone_builds_filter(self, dlg):
        dlg._log_scope.setCurrentIndex(dlg._log_scope.findData(1))
        [f] = _log_filters(dlg._build_filterset())
        assert f.last_n == 1 and f.count_op == "any"

    def test_count_alone_builds_filter(self, dlg):
        dlg._log_count_op.setCurrentIndex(dlg._log_count_op.findData("equal"))
        [f] = _log_filters(dlg._build_filterset())
        assert (f.count_op, f.count1) == ("equal", 0)
        assert not f.types and f.date_op is None and f.finder is None

    def test_exclude_alone_builds_nothing(self, dlg):
        # Nothing to exclude on — the filter would match every cache anyway.
        dlg._log_exclude.setCurrentIndex(1)
        assert _log_filters(dlg._build_filterset()) == []

    def test_load_roundtrip_and_reset(self, dlg):
        original = LogFilter(
            date_op="on_or_after", date1=date(2024, 3, 1),
            categories=["found"], last_n=10,
            types=["Needs Maintenance", LOG_TYPE_OTHER],
            finder_text="rev", finder_op="starts_with", finder_by_id=True,
            count_op="at_most", count1=3, exclude=True,
        )
        dlg._load_filterset(FilterSet().add(original))
        [f] = _log_filters(dlg._build_filterset())
        assert f.to_dict() == original.to_dict()
        dlg._tabs.setCurrentWidget(dlg._logs_tab)
        dlg._reset_current_tab()
        assert _log_filters(dlg._build_filterset()) == []
        assert all(cb.isChecked() for cb in dlg._log_categories.values())
        assert dlg._log_types_all.isChecked()
        assert not dlg._log_finder_enabled.isChecked()

    def test_invalid_regex_blocks_apply(self, dlg, monkeypatch):
        warned = MagicMock()
        monkeypatch.setattr(fd.QMessageBox, "warning", warned)
        dlg._log_finder_enabled.setChecked(True)
        dlg._log_finder_row.set_op("regex")
        dlg._log_finder_row.edit.setText("(")
        assert dlg._validate_text_filters() is False
        warned.assert_called_once()
        assert dlg._tabs.currentWidget() is dlg._logs_tab


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


class TestWhereErrorBoxTheme:
    """Issue #613: the Where-filter SQL error box used a hardcoded
    light-theme-only stylesheet (dark-red text on "transparent") which was
    unreadable on Windows dark mode. It must now pick an explicit,
    theme-appropriate style instead."""

    def test_light_theme_error_style(self, qtbot, monkeypatch):
        from types import SimpleNamespace
        from opensak.utils.types import DateFormat, CoordFormat
        monkeypatch.setattr(
            "opensak.gui.settings.get_settings",
            lambda: SimpleNamespace(home_lat=55.0, home_lon=12.0, use_miles=False,
                                     date_format=DateFormat.YMD, coord_format=CoordFormat.DD,
                                     home_points=[], theme="light"),
        )
        d = FilterDialog()
        qtbot.addWidget(d)
        assert "cc0000" in d._where_error_label.styleSheet().lower()

    def test_dark_theme_error_style_differs_from_light(self, qtbot, monkeypatch):
        from types import SimpleNamespace
        from opensak.utils.types import DateFormat, CoordFormat
        monkeypatch.setattr(
            "opensak.gui.settings.get_settings",
            lambda: SimpleNamespace(home_lat=55.0, home_lon=12.0, use_miles=False,
                                     date_format=DateFormat.YMD, coord_format=CoordFormat.DD,
                                     home_points=[], theme="dark"),
        )
        d = FilterDialog()
        qtbot.addWidget(d)
        style = d._where_error_label.styleSheet().lower()
        # Must not just be the light-mode style bleeding through, and must
        # not rely on a transparent background (that was the actual bug).
        assert "transparent" not in style
        assert "background" in style


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

    def test_save_profile_prefills_selected_profile_name(self, dlg, monkeypatch):
        captured = {}
        def fake_get_text(parent, title, label, text=""):
            captured["text"] = text
            return ("", False)  # cancel — we only care about the suggestion
        monkeypatch.setattr(QInputDialog, "getText", fake_get_text)
        dlg._profile_combo.blockSignals(True)
        dlg._profile_combo.addItem("AATestFilter", "/fake/AATestFilter.json")
        dlg._profile_combo.setCurrentIndex(dlg._profile_combo.count() - 1)
        dlg._profile_combo.blockSignals(False)
        dlg._save_profile()
        assert captured["text"] == "AATestFilter"

    def test_save_profile_no_prefill_when_no_profile_selected(self, dlg, monkeypatch):
        captured = {}
        def fake_get_text(parent, title, label, text=""):
            captured["text"] = text
            return ("", False)
        monkeypatch.setattr(QInputDialog, "getText", fake_get_text)
        dlg._profile_combo.blockSignals(True)
        dlg._profile_combo.setCurrentIndex(0)  # "none" entry
        dlg._profile_combo.blockSignals(False)
        dlg._save_profile()
        assert captured["text"] == ""

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

    def test_save_profile_emits_profile_saved_signal(self, dlg, monkeypatch, qtbot):
        # issue #682: saving a profile must report the name immediately,
        # regardless of whether the dialog is later applied or just closed
        # (mirrors test_delete_profile_emits_profile_deleted_signal / #491).
        # Without this, the toolbar's saved-filter dropdown only refreshed
        # when a filter was applied, so a newly saved-but-not-applied
        # profile never appeared until something else happened to trigger
        # a refresh.
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("MyProfile", True))
        monkeypatch.setattr(fd.FilterProfile, "save", lambda self, *a, **k: None)
        with qtbot.waitSignal(dlg.profile_saved, timeout=1000) as blocker:
            dlg._save_profile()
        assert blocker.args == ["MyProfile"]


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
                                     coord_format=CoordFormat.DD, home_points=[],
                                     theme="light"),
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

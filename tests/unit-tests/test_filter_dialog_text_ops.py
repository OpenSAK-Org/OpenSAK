# tests/unit-tests/test_filter_dialog_text_ops.py — operator dropdowns on the
# filter dialog's text filters (name, GC code, placed by, owner, country, state, county,
# user data 1-4, GC.com note).

from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from opensak.gui.dialogs import filter_dialog as fd
from opensak.gui.dialogs.filter_dialog import FilterDialog
from opensak.filters.engine import (
    TEXT_OPS, CountyFilter, FilterSet, GcCodeFilter, NameFilter,
)


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    # No real profiles on disk; deterministic settings.
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


def _text_rows(dlg):
    return dlg._general_text_rows() + dlg._misc_text_rows()


def _by_type(fs, ftype):
    return [f for f in fs._filters if getattr(f, "filter_type", None) == ftype]


class TestOperatorDropdown:
    def test_every_text_row_offers_every_operator(self, dlg):
        rows = _text_rows(dlg)
        assert len(rows) == 12
        for row, _cls in rows:
            assert [row.combo.itemData(i) for i in range(row.combo.count())] == list(TEXT_OPS)
            assert row.op() == "contains"

    def test_default_contains_builds_as_before(self, dlg):
        dlg._name_filter.setText("  Mill ")
        [f] = _by_type(dlg._build_filterset(), "name")
        assert (f.op, f.text) == ("contains", "Mill")

    def test_selected_operator_is_used(self, dlg):
        dlg._name_row.set_op("starts_with")
        dlg._name_filter.setText("Old")
        dlg._gc_row.set_op("in_list")
        dlg._gc_filter.setText("GC1; GC2")
        fs = dlg._build_filterset()
        [name] = _by_type(fs, "name")
        [gc] = _by_type(fs, "gc_code")
        assert (name.op, name.text) == ("starts_with", "Old")
        assert (gc.op, gc.text) == ("in_list", "GC1; GC2")

    def test_valueless_operator_disables_field_and_needs_no_text(self, dlg):
        dlg._owner_filter.setText("ignored")
        dlg._owner_row.set_op("empty")
        assert not dlg._owner_filter.isEnabled()
        [f] = _by_type(dlg._build_filterset(), "owner_name")
        assert (f.op, f.text) == ("empty", "")
        dlg._owner_row.set_op("contains")
        assert dlg._owner_filter.isEnabled()

    def test_text_operator_without_text_adds_no_filter(self, dlg):
        dlg._county_row.set_op("not_contains")
        assert _by_type(dlg._build_filterset(), "county") == []

    def test_placeholder_follows_operator(self, dlg):
        row = dlg._name_row
        contains_placeholder = row.edit.placeholderText()
        row.set_op("in_list")
        assert row.edit.placeholderText() not in ("", contains_placeholder)
        row.set_op("regex")
        assert row.edit.placeholderText() not in ("", contains_placeholder)
        row.set_op("not_empty")
        assert row.edit.placeholderText() == ""


class TestLoadAndReset:
    def test_load_restores_operator_and_text(self, dlg):
        fs = FilterSet()
        fs.add(NameFilter("a; b", "in_list"))
        fs.add(GcCodeFilter("GC1"))
        fs.add(CountyFilter("", "not_empty"))
        dlg._load_filterset(fs)
        assert (dlg._name_row.op(), dlg._name_filter.text()) == ("in_list", "a; b")
        assert (dlg._gc_row.op(), dlg._gc_filter.text()) == ("contains", "GC1")
        assert dlg._county_row.op() == "not_empty"
        assert not dlg._county_filter.isEnabled()

    def test_build_load_roundtrip_every_operator(self, dlg, qtbot):
        reopened = FilterDialog()
        qtbot.addWidget(reopened)
        for op in TEXT_OPS:
            dlg._reset_all()
            dlg._state_row.set_op(op)
            dlg._state_filter.setText("x")
            reopened._load_filterset(dlg._build_filterset())
            assert reopened._state_row.op() == op

    def test_reset_restores_contains_and_clears_text(self, dlg):
        dlg._name_row.set_op("not_equals")
        dlg._name_filter.setText("me")
        dlg._owner_row.set_op("equals")
        dlg._owner_filter.setText("me")
        dlg._country_row.set_op("regex")
        dlg._country_filter.setText("^D")
        dlg._reset_all()
        for row in (dlg._name_row, dlg._owner_row, dlg._country_row):
            assert row.op() == "contains"
            assert row.edit.text() == ""

    def test_reset_general_tab_resets_every_general_text_row(self, dlg):
        # Guards against a row being left out of _reset_general() (#849).
        rows = [row for row, _cls in dlg._general_text_rows()]
        for row in rows:
            row.set_op("not_equals")
            row.edit.setText("x")
        dlg._tabs.setCurrentWidget(dlg._general_tab)
        dlg._reset_current_tab()
        for row in rows:
            assert row.op() == "contains"
            assert row.edit.text() == ""


class TestRegexValidation:
    def test_invalid_regex_blocks_apply(self, dlg, monkeypatch):
        warnings, applied = [], []
        monkeypatch.setattr(fd.QMessageBox, "warning",
                            staticmethod(lambda *a, **k: warnings.append(a)))
        dlg.filter_applied.connect(lambda *a: applied.append(a))
        dlg._county_row.set_op("regex")
        dlg._county_filter.setText("(unclosed")
        dlg._apply()
        assert applied == []
        assert len(warnings) == 1
        assert dlg._tabs.currentWidget() is dlg._misc_tab

    def test_valid_regex_applies(self, dlg, db_session):
        applied = []
        dlg.filter_applied.connect(lambda fs, sort, name: applied.append(fs))
        dlg._name_row.set_op("not_regex")
        dlg._name_filter.setText(r"^GC\d+$")
        dlg._apply()
        [f] = _by_type(applied[0], "name")
        assert (f.op, f.regex_error) == ("not_regex", None)


class TestUserDataGcNoteElevation:
    def test_user_data_and_gc_note_rows_build_their_filters(self, dlg):
        for i, row in enumerate(dlg._ud_rows, start=1):
            row.edit.setText(f"ud{i}")
        dlg._gc_note_row.set_op("not_empty")
        fs = dlg._build_filterset()
        for i in range(1, 5):
            [f] = _by_type(fs, f"user_data_{i}")
            assert (f.op, f.text) == ("contains", f"ud{i}")
        [note] = _by_type(fs, "gc_note")
        assert note.op == "not_empty"

    def test_user_data_and_gc_note_round_trip(self, dlg, qtbot):
        dlg._ud_rows[2].set_op("equals")
        dlg._ud_rows[2].edit.setText("solved")
        dlg._gc_note_row.edit.setText("final")
        reopened = FilterDialog()
        qtbot.addWidget(reopened)
        reopened._load_filterset(dlg._build_filterset())
        assert (reopened._ud_rows[2].op(), reopened._ud_rows[2].edit.text()) == ("equals", "solved")
        assert reopened._gc_note_row.edit.text() == "final"

    def test_elevation_off_by_default(self, dlg):
        assert not dlg._elev_enabled.isChecked()
        assert _by_type(dlg._build_filterset(), "elevation") == []

    def test_elevation_builds_and_round_trips(self, dlg, qtbot):
        dlg._elev_enabled.setChecked(True)
        dlg._elev_min.setValue(400)
        dlg._elev_max.setValue(1200)
        fs = dlg._build_filterset()
        [f] = _by_type(fs, "elevation")
        assert (f.min_m, f.max_m) == (400, 1200)
        reopened = FilterDialog()
        qtbot.addWidget(reopened)
        reopened._load_filterset(fs)
        assert reopened._elev_enabled.isChecked()
        assert (reopened._elev_min.value(), reopened._elev_max.value()) == (400, 1200)

    def test_elevation_in_feet_is_stored_in_metres(self, dlg, monkeypatch):
        from opensak.utils.types import DateFormat, CoordFormat
        monkeypatch.setattr("opensak.gui.settings.get_settings",
                            lambda: SimpleNamespace(home_lat=55.0, home_lon=12.0, use_miles=True,
                                                   date_format=DateFormat.YMD,
                                                   coord_format=CoordFormat.DD, home_points=[],
                                                   theme="light"))
        dlg._elev_enabled.setChecked(True)
        dlg._elev_min.setValue(3281)   # ~1000 m
        dlg._elev_max.setValue(6562)   # ~2000 m
        [f] = _by_type(dlg._build_filterset(), "elevation")
        assert f.min_m == pytest.approx(1000, abs=0.1)
        assert f.max_m == pytest.approx(2000, abs=0.1)

    def test_reset_misc_clears_new_rows(self, dlg):
        dlg._ud_rows[0].edit.setText("x")
        dlg._gc_note_row.edit.setText("y")
        dlg._elev_enabled.setChecked(True)
        dlg._elev_min.setValue(100)
        dlg._tabs.setCurrentWidget(dlg._misc_tab)
        dlg._reset_current_tab()
        assert dlg._ud_rows[0].edit.text() == ""
        assert dlg._gc_note_row.edit.text() == ""
        assert not dlg._elev_enabled.isChecked()
        assert dlg._elev_min.value() == -500

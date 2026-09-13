# tests/unit-tests/test_filter_dialog_text_ops.py — operator dropdowns on the
# filter dialog's text filters (name, GC code, placed by, owner, country, state, county).

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
    return dlg._general_text_rows() + dlg._geo_text_rows()


def _by_type(fs, ftype):
    return [f for f in fs._filters if getattr(f, "filter_type", None) == ftype]


class TestOperatorDropdown:
    def test_every_text_row_offers_every_operator(self, dlg):
        rows = _text_rows(dlg)
        assert len(rows) == 7
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
        dlg._country_row.set_op("regex")
        dlg._country_filter.setText("^D")
        dlg._reset_all()
        for row in (dlg._name_row, dlg._country_row):
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

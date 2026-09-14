# tests/unit-tests/test_filter_dialog_line_polygon.py — the filter dialog's
# Line/Polygon tab (GSAK-style line/polygon filter).

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("pytestqt")

from opensak.gui.dialogs import filter_dialog as fd
from opensak.gui.dialogs.filter_dialog import FilterDialog
from opensak.filters.engine import FilterSet, LinePolygonFilter

# "W,<code>" lines resolve from here instead of a database.
CODES = {"GC12345": (55.5, 10.5)}

_GPX = (
    '<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
    '<trkpt lat="55.1" lon="10.1"/><trkpt lat="55.2" lon="10.2"/>'
    '</trkseg></trk></gpx>'
)


@pytest.fixture
def settings():
    from opensak.utils.types import CoordFormat, DateFormat
    return SimpleNamespace(home_lat=55.0, home_lon=12.0, use_miles=False,
                           date_format=DateFormat.YMD, coord_format=CoordFormat.DD,
                           home_points=[], theme="light")


@pytest.fixture(autouse=True)
def isolate(monkeypatch, settings):
    monkeypatch.setattr(fd.FilterProfile, "list_profiles", staticmethod(lambda: []))
    monkeypatch.setattr("opensak.gui.settings.get_settings", lambda: settings)
    monkeypatch.setattr(fd.FilterDialog, "_resolve_point_code",
                        staticmethod(lambda code: CODES.get(code)))


@pytest.fixture
def dlg(qtbot):
    d = FilterDialog()
    qtbot.addWidget(d)
    return d


def _lp_filters(fs):
    return [f for f in fs._filters if isinstance(f, LinePolygonFilter)]


def _choose_file(monkeypatch, path):
    monkeypatch.setattr(fd, "QFileDialog", SimpleNamespace(
        getOpenFileName=lambda *a, **k: (str(path) if path else "", ""),
    ))


class TestBuild:
    def test_tab_present(self, dlg):
        assert dlg._tabs.indexOf(dlg._line_polygon_tab) >= 0

    def test_unused_tab_adds_no_filter(self, dlg):
        assert _lp_filters(dlg._build_filterset()) == []
        dlg._lp_text.setPlainText("# only a comment\n\n")
        assert _lp_filters(dlg._build_filterset()) == []
        assert dlg._validate_line_polygon() is True

    def test_line_filter(self, dlg):
        dlg._lp_text.setPlainText("55.0, 10.0\nN 55 00.000, E 011 00.000\nW,GC12345")
        dlg._lp_distance.setValue(2.5)
        [f] = _lp_filters(dlg._build_filterset())
        assert f.mode == "line"
        assert f.points == [(55.0, 10.0), (55.0, 11.0), (55.5, 10.5)]
        assert f.distance_km == 2.5
        assert f.exclude is False

    def test_polygon_without_distance(self, dlg):
        dlg._lp_text.setPlainText("55.0, 10.0\n55.0, 11.0\n56.0, 10.5")
        dlg._lp_mode_buttons["polygon"].setChecked(True)
        dlg._lp_distance.setValue(0.0)
        dlg._lp_exclude.setChecked(True)
        [f] = _lp_filters(dlg._build_filterset())
        assert (f.mode, f.distance_km, f.exclude) == ("polygon", 0.0, True)

    def test_miles_are_converted(self, dlg, settings):
        settings.use_miles = True
        dlg._lp_text.setPlainText("55.0, 10.0\n55.0, 11.0")
        dlg._lp_distance.setValue(1.0)
        [f] = _lp_filters(dlg._build_filterset())
        assert f.distance_km == pytest.approx(1.60934)


class TestApply:
    def test_valid_input_applies(self, dlg):
        received = []
        dlg.filter_applied.connect(lambda fs, sort, name: received.append(fs))
        dlg._lp_text.setPlainText("55.0, 10.0\n55.0, 11.0")
        dlg._apply()
        assert len(_lp_filters(received[0])) == 1

    @pytest.mark.parametrize("text, mode, distance, fragment", [
        ("55.0, 10.0\nnonsense", "line", 1.0, "nonsense"),
        ("W,GCNOPE\n55.0, 10.0", "line", 1.0, "W,GCNOPE"),
        ("55.0, 10.0", "line", 1.0, None),                 # too few points
        ("55.0, 10.0\n55.0, 11.0", "polygon", 1.0, None),  # too few points
        ("55.0, 10.0\n55.0, 11.0", "points", 0.0, None),   # distance required
    ])
    def test_invalid_input_blocks_apply(self, dlg, monkeypatch, text, mode, distance, fragment):
        warning = MagicMock()
        monkeypatch.setattr(fd.QMessageBox, "warning", warning)
        monkeypatch.setattr(fd, "tr", lambda key, **kwargs: f"{key} {kwargs}")
        applied = MagicMock()
        dlg.filter_applied.connect(applied)
        dlg._lp_text.setPlainText(text)
        dlg._lp_mode_buttons[mode].setChecked(True)
        dlg._lp_distance.setValue(distance)
        dlg._apply()
        warning.assert_called_once()
        applied.assert_not_called()
        assert dlg._tabs.currentWidget() is dlg._line_polygon_tab
        if fragment:
            assert fragment in warning.call_args.args[2]
        assert _lp_filters(dlg._build_filterset()) == []


class TestLoadAndReset:
    def test_load_restores_tab(self, dlg):
        text = "# route\n55.0, 10.0\nW,GC12345"
        f = LinePolygonFilter([(55.0, 10.0), (55.5, 10.5)], "points", 3.0,
                              exclude=True, text=text)
        dlg._load_filterset(FilterSet().add(f))
        assert dlg._lp_text.toPlainText() == text
        assert dlg._lp_mode_buttons["points"].isChecked()
        assert dlg._lp_distance.value() == 3.0
        assert dlg._lp_exclude.isChecked()
        [rebuilt] = _lp_filters(dlg._build_filterset())
        assert rebuilt.to_dict() == f.to_dict()

    def test_load_without_text_lists_points(self, dlg):
        f = LinePolygonFilter([(55.0, 10.0), (55.0, 11.0)], "line", 1.0)
        dlg._load_filterset(FilterSet().add(f))
        assert dlg._lp_text.toPlainText() == "55.000000, 10.000000\n55.000000, 11.000000"

    def test_reset_current_tab(self, dlg):
        dlg._lp_text.setPlainText("55.0, 10.0")
        dlg._lp_mode_buttons["polygon"].setChecked(True)
        dlg._lp_distance.setValue(7.0)
        dlg._lp_exclude.setChecked(True)
        dlg._tabs.setCurrentWidget(dlg._line_polygon_tab)
        dlg._reset_current_tab()
        assert dlg._lp_text.toPlainText() == ""
        assert dlg._lp_mode_buttons["line"].isChecked()
        assert dlg._lp_distance.value() == 1.0
        assert not dlg._lp_exclude.isChecked()


class TestAddPoints:
    @pytest.fixture
    def fake_db(self, monkeypatch):
        @contextmanager
        def fake_session():
            yield None
        monkeypatch.setattr("opensak.db.database.get_session", fake_session)

    def test_add_flagged(self, dlg, monkeypatch, fake_db):
        monkeypatch.setattr(fd, "user_flagged_codes", lambda session: ["GC1", "GC2"])
        dlg._lp_text.setPlainText("55.0, 10.0\n")
        dlg._add_flagged_points()
        assert dlg._lp_text.toPlainText() == "55.0, 10.0\nW,GC1\nW,GC2"

    def test_add_flagged_none(self, dlg, monkeypatch, fake_db):
        monkeypatch.setattr(fd, "user_flagged_codes", lambda session: [])
        information = MagicMock()
        monkeypatch.setattr(fd.QMessageBox, "information", information)
        dlg._add_flagged_points()
        information.assert_called_once()
        assert dlg._lp_text.toPlainText() == ""

    def test_points_file_replace_and_append(self, dlg, monkeypatch, tmp_path):
        path = tmp_path / "route.gpx"
        path.write_text(_GPX, encoding="utf-8")
        _choose_file(monkeypatch, path)
        dlg._lp_text.setPlainText("55.0, 10.0")
        dlg._load_points_file()  # Replace is the default
        assert dlg._lp_text.toPlainText() == "55.100000, 10.100000\n55.200000, 10.200000"
        dlg._lp_append.setChecked(True)
        dlg._load_points_file()
        assert dlg._lp_text.toPlainText().splitlines() == [
            "55.100000, 10.100000", "55.200000, 10.200000",
            "55.100000, 10.100000", "55.200000, 10.200000",
        ]

    @pytest.mark.parametrize("content", ["<gpx>", '<gpx xmlns="http://www.topografix.com/GPX/1/1"/>'])
    def test_points_file_unreadable_or_empty(self, dlg, monkeypatch, tmp_path, content):
        path = tmp_path / "bad.gpx"
        path.write_text(content, encoding="utf-8")
        _choose_file(monkeypatch, path)
        warning = MagicMock()
        monkeypatch.setattr(fd.QMessageBox, "warning", warning)
        dlg._lp_text.setPlainText("55.0, 10.0")
        dlg._load_points_file()
        warning.assert_called_once()
        assert dlg._lp_text.toPlainText() == "55.0, 10.0"

    def test_cancelled_file_dialog_changes_nothing(self, dlg, monkeypatch):
        _choose_file(monkeypatch, None)
        dlg._lp_text.setPlainText("55.0, 10.0")
        dlg._load_points_file()
        assert dlg._lp_text.toPlainText() == "55.0, 10.0"
